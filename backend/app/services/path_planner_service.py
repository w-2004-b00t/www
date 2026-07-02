from __future__ import annotations

from copy import deepcopy
import json
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from ..course_config import COURSE_ID, COURSE_NAME
from ..demo_data import now_text
from ..persistence import list_knowledge_chunks, list_records, load_json
from ..utils import user_scoped_key
from .course_progress_service import list_course_chapters
from .knowledge_service import search_chunks
from .knowledge_point_service import sanitize_knowledge_points
from .llm_service import LLMUnavailable, call_deepseek_json
from .resource_service import build_resources_for_topic
from .stage_progress_service import stage_completion_requirements
from .strict_generation import is_legacy_fallback_resource, raise_blocked


MIN_REAL_CHUNKS = 2
PATH_RESOURCE_TYPES = ["explanation", "mindmap", "reading", "video_script", "exercise", "lab"]
RESOURCE_TYPE_ORDER = {resource_type: index for index, resource_type in enumerate(PATH_RESOURCE_TYPES)}
RESOURCE_TASKS = {
    "explanation": "阅读讲解文档，整理核心概念和典型操作",
    "mindmap": "查看思维导图，补全知识结构和先后关系",
    "reading": "按拓展阅读路线复盘课程引用和例题",
    "video_script": "观看或阅读视频演示脚本，复述关键过程",
    "exercise": "完成题库练习，记录错因和待补强知识点",
    "lab": "完成实操案例，验证输入输出和边界条件",
}


def empty_learning_path(reason: str = "请先上传真实课程资料或生成带引用的学习资源。") -> dict[str, Any]:
    return {
        "id": "path_real_data_required",
        "title": "暂无法生成正式学习路径",
        "summary": "系统未获得足够真实课程依据，因此不会使用静态样例或假数据生成路径。",
        "status": "blocked",
        "generationMode": "strict_real_data",
        "llmStatus": "skipped",
        "blockingReason": reason,
        "generatedAt": None,
        "sourceCitations": [],
        "stages": [],
        "profileBasis": [],
        "adjustmentHistory": [],
        "initialReason": reason,
        "intensity": "60min",
        "resourceCoverage": {
            "approvedTotal": 0,
            "linkedTotal": 0,
            "pendingTotal": 0,
            "unlinkedResourceIds": [],
        },
    }


def generate_learning_path_for_user(user_id: str) -> dict[str, Any]:
    from .. import state

    context = _build_context(user_id)
    if not context["profileItems"]:
        return empty_learning_path("还没有当前用户的学习画像，请先完成画像确认后再生成学习路径。")
    gate = _real_data_gate(context)
    if gate:
        return empty_learning_path(gate)
    resource_gate = ensure_path_resources_for_user(user_id, context["topics"], PATH_RESOURCE_TYPES)
    if resource_gate:
        return empty_learning_path(resource_gate)
    context = _build_context(user_id, topics=context["topics"])
    if not context["resources"]:
        return empty_learning_path("没有已通过审核的学习资源，无法生成正式学习路径。请先生成带引用资源并通过审核。")

    path = _rule_based_path(context)
    if path.get("status") != "ready":
        return path
    try:
        enhanced = _enhance_path_with_llm(path, context)
        path = _merge_llm_enhancement(path, enhanced)
        path["llmStatus"] = "enhanced"
        path["generationMode"] = "deepseek_path_planning"
    except LLMUnavailable as exc:
        raise_blocked(
            status_code=503,
            agent_name="路径规划 Agent",
            message="DeepSeek 路径规划增强不可用，已停止生成正式学习路径；不会使用规则路径兜底。",
            missing_requirements=["DeepSeek 路径规划 JSON", "真实画像", "已审核资源", "课程引用"],
            used_llm=False,
            detail=str(exc),
        )
    return _with_resource_coverage(path, state.load_user_resources(user_id))


def ensure_path_resources_for_user(user_id: str, topics: list[str], resource_types: list[str] | None = None) -> str:
    from .. import state

    existing = [
        item for item in state.load_user_resources(user_id)
        if item.get("auditStatus") == "passed" and not is_legacy_fallback_resource(item)
    ]
    if existing:
        return ""

    topic = next((str(item).strip() for item in topics if str(item or "").strip()), "数据结构")
    chapter = _chapter_for_topic(list_course_chapters(), topic)
    target = _resource_generation_target(topic)
    try:
        generated = build_resources_for_topic(topic, target, resource_types or PATH_RESOURCE_TYPES, chapter)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
        return f"学习路径需要先生成真实学习资源，但资源生成失败：{detail}"
    except Exception as exc:
        return f"学习路径需要先生成真实学习资源，但资源生成异常：{exc.__class__.__name__}: {exc}"

    passed = [item for item in generated if item.get("auditStatus") == "passed"]
    if not passed:
        return "已生成资源均未通过审核，不能进入正式学习路径。请教师复核或重新生成资源。"
    state.save_user_resources(user_id, _merge_resources(state.load_user_resources(user_id), generated))
    state.resources[:] = _merge_resources(state.resources, generated)
    state.persist_state()
    return ""


def attach_passed_resources_to_path(user_id: str, resources: list[dict[str, Any]], task_id: str | None = None) -> dict[str, Any]:
    from .. import state

    passed = [deepcopy(item) for item in resources if item.get("auditStatus") == "passed"]
    learning_path = state.load_user_learning_path(user_id)
    if not passed:
        return sync_learning_path_resources(user_id, learning_path=learning_path)

    state.save_user_resources(user_id, _merge_resources(state.load_user_resources(user_id), passed))
    learning_path = sync_learning_path_resources(
        user_id,
        learning_path=learning_path,
        resources=state.load_user_resources(user_id),
        task_id=task_id,
        trigger="资源生成完成自动同步路径",
        requested_resource_ids=[str(item.get("id") or "") for item in passed],
    )
    state.save_user_learning_path(user_id, state.strip_learning_path_progress_fields(learning_path))
    return learning_path


def sync_learning_path_resources(
    user_id: str,
    *,
    learning_path: dict[str, Any] | None = None,
    resources: list[dict[str, Any]] | None = None,
    requested_resource_ids: list[str] | None = None,
    task_id: str | None = None,
    trigger: str = "学习资源同步路径",
) -> dict[str, Any]:
    """Rebuild the formal path from all approved resources for the current user.

    Course citations remain evidence only; stage.resources is the executable
    learning-resource set. Calling this from every entry point prevents the
    resource center and learning path from drifting apart.
    """
    from .. import state

    current_path = deepcopy(learning_path if learning_path is not None else state.load_user_learning_path(user_id))
    all_resources = deepcopy(resources if resources is not None else state.load_user_resources(user_id))
    passed_resources = [item for item in all_resources if item.get("auditStatus") == "passed"]
    coverage_only_path = _with_resource_coverage(current_path, all_resources)
    if not passed_resources:
        if current_path.get("status") == "ready":
            current_path["stages"] = _remove_non_approved_resources(current_path.get("stages", []), set())
            if not current_path["stages"]:
                blocked = empty_learning_path("没有已通过审核的学习资源，无法生成正式学习路径。请先生成带引用资源并通过审核。")
                blocked["adjustmentHistory"] = current_path.get("adjustmentHistory", [])
                blocked["intensity"] = current_path.get("intensity") or "60min"
                return _with_resource_coverage(blocked, all_resources)
        return _with_resource_coverage(current_path if current_path.get("stages") else coverage_only_path, all_resources)

    before_path = [stage.get("name", "") for stage in current_path.get("stages", [])]
    requested = set(_unique_nonempty(requested_resource_ids or []))
    passed_resources = [item for item in passed_resources if not is_legacy_fallback_resource(item)]
    context = _build_context(user_id, topics=_resource_topics(passed_resources))
    context["resources"] = _merge_resources(context["resources"], passed_resources)
    rebuilt = _rule_based_path(context)
    if rebuilt.get("status") != "ready":
        return _with_resource_coverage(rebuilt, all_resources)
    try:
        enhanced = _enhance_path_with_llm(rebuilt, context)
        rebuilt = _merge_llm_enhancement(rebuilt, enhanced)
        rebuilt["llmStatus"] = "enhanced"
        rebuilt["generationMode"] = "deepseek_path_planning"
    except LLMUnavailable as exc:
        blocked = empty_learning_path("DeepSeek 路径规划增强不可用，已停止同步正式学习路径；不会使用规则路径兜底。")
        blocked["llmStatus"] = "unavailable"
        blocked["llmError"] = str(exc)
        return _with_resource_coverage(blocked, all_resources)

    rebuilt["id"] = current_path.get("id") if current_path.get("status") == "ready" else rebuilt["id"]
    rebuilt["generatedAt"] = current_path.get("generatedAt") or rebuilt.get("generatedAt") or now_text()
    rebuilt["intensity"] = current_path.get("intensity") or rebuilt.get("intensity") or "60min"
    old_history = current_path.get("adjustmentHistory") or []
    resource_titles = [
        item.get("title") or item.get("id", "")
        for item in passed_resources
        if not requested or str(item.get("id") or "") in requested
    ]
    rebuilt["adjustmentHistory"] = [{
        "id": f"resource_sync_{uuid4().hex[:8]}",
        "source": "resource",
        "trigger": trigger,
        "reason": f"已按章节、主题和资源类型重新调度 {len(passed_resources)} 份已审核学习资源；未通过审核资源不会进入正式路径。",
        "before": next((item for item in before_path if item), "未生成正式路径"),
        "after": next((stage.get("name", "") for stage in rebuilt.get("stages", []) if stage.get("status") == "active"), "资源学习任务"),
        "beforePath": before_path,
        "afterPath": [stage.get("name", "") for stage in rebuilt.get("stages", [])],
        "evidence": resource_titles[:8],
        "taskId": task_id,
        "createdAt": now_text(),
    }, *old_history][:12]
    return _with_resource_coverage(rebuilt, all_resources)


def build_remedial_stage(
    *,
    user_id: str,
    weakness: list[str],
    suggestion: str,
    score: int | float,
    error_rate: int | float,
    error_reasons: list[str],
    existing_path: dict[str, Any],
) -> dict[str, Any]:
    context = _build_context(user_id, topics=weakness)
    citations = _citations_for_topics(weakness or ["阶段测评薄弱点"], limit=4)
    resource_ids = _resource_ids_for_topics(context["resources"], weakness)
    stage_id = f"remedial_{uuid4().hex[:8]}"
    path_context = " ".join(
        str(value or "")
        for stage in existing_path.get("stages", [])
        if isinstance(stage, dict)
        for value in [stage.get("chapterName"), stage.get("name"), *(stage.get("knowledgePoints") or [])]
    )
    knowledge_points = sanitize_knowledge_points(weakness, context=path_context)
    if not knowledge_points:
        knowledge_points = sanitize_knowledge_points([], fallback=path_context)
    if not knowledge_points:
        raise ValueError("没有可用于创建补强任务的合法数据结构知识点。")
    stage = {
        "id": stage_id,
        "name": f"{knowledge_points[0]}补强任务",
        "days": 1,
        "status": "active",
        "chapterId": "",
        "chapterName": "",
        "knowledgePoints": knowledge_points,
        "resources": resource_ids,
        "tasks": [
            "复盘本次测评错题和评分 Rubric",
            "阅读对应课程片段并整理关键概念",
            "完成一组针对性练习后重新进入阶段测评",
        ],
        "acceptance": suggestion,
        "aiReason": f"阶段测评分数 {score}，错误率 {error_rate}%，薄弱点集中在：{'、'.join(knowledge_points)}。",
        "source": "assessment",
        "citationChunkIds": [item["chunkId"] for item in citations],
    }
    try:
        enhanced = _enhance_remedial_stage_with_llm(stage, context, error_reasons)
        stage.update({
            key: value
            for key, value in enhanced.items()
            if key in {"tasks", "acceptance", "aiReason"} and value
        })
        if isinstance(stage.get("tasks"), list):
            stage["tasks"] = [str(item) for item in stage["tasks"] if str(item).strip()][:5] or stage["tasks"]
    except LLMUnavailable:
        pass
    return stage


def insert_or_replace_remedial_stage(path: dict[str, Any], stage: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(path)
    stages = updated.setdefault("stages", [])
    for item in stages:
        if item.get("status") == "active":
            item["status"] = "pending"
    existing_index = next((index for index, item in enumerate(stages) if item.get("source") == "assessment"), -1)
    if existing_index >= 0:
        stages[existing_index] = stage
    else:
        active_or_insert = next((index for index, item in enumerate(stages) if item.get("status") == "pending"), len(stages))
        stages.insert(max(active_or_insert, 0), stage)
    updated["status"] = "ready"
    updated["generatedAt"] = updated.get("generatedAt") or now_text()
    return updated


def _build_context(user_id: str, topics: list[str] | None = None) -> dict[str, Any]:
    from .. import state

    resources = [
        item for item in state.load_user_resources(user_id)
        if item.get("auditStatus") == "passed" and not is_legacy_fallback_resource(item)
    ]
    profile_items = load_json(user_scoped_key("profile_items", user_id), [])
    progress = state.load_user_learning_progress(user_id)
    latest_assessment = next((item for item in state.assessment_results if item.get("userId") == user_id), {})
    topic_candidates = _topic_candidates(profile_items, resources, latest_assessment, topics)
    return {
        "userId": user_id,
        "resources": resources,
        "profileItems": profile_items,
        "progress": progress,
        "latestAssessment": latest_assessment,
        "assessments": [
            item
            for item in list_records("assessment_results", 200)
            if item.get("userId") == user_id
        ],
        "chapters": list_course_chapters(),
        "topics": topic_candidates,
        "knowledgeChunkCount": len(list_knowledge_chunks(course_id=COURSE_ID)),
    }


def _real_data_gate(context: dict[str, Any]) -> str:
    if context["knowledgeChunkCount"] < MIN_REAL_CHUNKS:
        return "知识库中的真实课程片段不足，无法生成正式学习路径。请先上传或导入课程资料。"
    citations = _citations_for_topics(context["topics"][:3] or ["数据结构"], limit=5)
    if len(citations) < MIN_REAL_CHUNKS:
        return "当前画像或资源相关主题没有命中足够课程引用，无法生成正式学习路径。请补充资料或先生成带引用资源。"
    return ""


def _rule_based_path(context: dict[str, Any]) -> dict[str, Any]:
    topics = context["topics"][:4] or ["数据结构课程"]
    resources = context["resources"]
    stages = plan_stages_from_resources(context, resources)
    if not stages:
        return empty_learning_path("没有可绑定到路径阶段的已审核学习资源。请先生成并审核通过学习资源。")
    citations = _dedupe_citations(_resource_citations(resources) + _citations_for_topics(topics, limit=8))[:8]
    _activate_first_pending_stage(stages)
    profile_basis = _profile_basis(context["profileItems"])
    path = {
        "id": f"path_{uuid4().hex[:8]}",
        "title": f"{COURSE_NAME}个性化学习路径",
        "summary": "基于真实课程资料、已审核资源、学生画像和学习进度生成；每个阶段都绑定可学习资源。",
        "status": "ready",
        "generationMode": "pending_deepseek_path_planning",
        "llmStatus": "pending",
        "blockingReason": "",
        "generatedAt": now_text(),
        "sourceCitations": citations,
        "profileBasis": profile_basis,
        "initialReason": "严格使用知识库命中的课程资料、已审核学习资源和学生真实学习记录，不使用静态演示路径。",
        "adjustmentHistory": [{
            "id": f"path_generated_{uuid4().hex[:8]}",
            "source": "resource",
            "trigger": "资源驱动正式路径生成",
            "reason": "系统先生成或复用已审核资源，再依据画像、学习进度和课程引用规划学习顺序。",
            "before": "未生成正式路径",
            "after": stages[0]["name"] if stages else "等待路径生成",
            "beforePath": [],
            "afterPath": [stage["name"] for stage in stages],
            "evidence": [item["sourceLocation"] for item in citations[:3]],
            "createdAt": now_text(),
        }],
        "intensity": "60min",
        "stages": stages,
    }
    return _with_resource_coverage(path, resources)


def _with_resource_coverage(path: dict[str, Any], resources: list[dict[str, Any]]) -> dict[str, Any]:
    updated = deepcopy(path)
    approved_ids = {
        str(item.get("id") or "").strip()
        for item in resources
        if item.get("auditStatus") == "passed" and str(item.get("id") or "").strip()
    }
    linked_ids = {
        str(resource_id)
        for stage in updated.get("stages", []) or []
        for resource_id in stage.get("resources", []) or []
        if str(resource_id) in approved_ids
    }
    pending_total = len([
        item for item in resources
        if str(item.get("id") or "").strip() and item.get("auditStatus") != "passed"
    ])
    updated["resourceCoverage"] = {
        "approvedTotal": len(approved_ids),
        "linkedTotal": len(linked_ids),
        "pendingTotal": pending_total,
        "unlinkedResourceIds": sorted(approved_ids - linked_ids),
    }
    return updated


def _remove_non_approved_resources(stages: list[dict[str, Any]], approved_ids: set[str]) -> list[dict[str, Any]]:
    cleaned = []
    for stage in stages:
        item = deepcopy(stage)
        item["resources"] = [
            resource_id for resource_id in item.get("resources", [])
            if str(resource_id) in approved_ids
        ]
        if item.get("source") == "assessment" or item["resources"]:
            cleaned.append(item)
    return cleaned


def plan_stages_from_resources(context: dict[str, Any], resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    topic_order = {topic: index for index, topic in enumerate(context["topics"])}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for resource in _sort_resources_for_path(resources, topic_order):
        key = _resource_group_key(resource)
        grouped.setdefault(key, []).append(resource)

    stages = []
    for index, group in enumerate(grouped.values(), start=1):
        topic = _resource_topic(group[0])
        chapter = _resource_chapter_meta(group[0]) or _chapter_for_topic(context["chapters"], topic)
        resource_ids = _unique_nonempty([item.get("id") for item in group])
        citations = _resource_citations(group) or _citations_for_topics([topic], limit=3)
        knowledge_points = _unique_nonempty([topic, *chapter.get("knowledgePoints", [])[:3]])
        stage = {
            "id": f"path_stage_{index}_{uuid4().hex[:6]}",
            "name": f"{topic}资源学习任务",
            "days": _estimate_stage_days(group),
            "status": "pending",
            "chapterId": chapter.get("chapterId", ""),
            "chapterName": chapter.get("chapterName", topic),
            "knowledgePoints": knowledge_points,
            "resources": resource_ids,
            "tasks": _tasks_for_resources(group),
            "acceptance": f"完成本阶段绑定的 {len(resource_ids)} 份已审核资源，并能结合课程引用说明「{topic}」的核心概念、典型操作和练习结果。",
            "aiReason": _stage_reason(context, topic, group, citations),
            "source": "resource" if index > 1 else "profile",
            "citationChunkIds": _unique_nonempty([item.get("chunkId") for item in citations]),
        }
        requirements = stage_completion_requirements(
            stage,
            resources,
            context["progress"],
            context.get("assessments", []),
        )
        stage["status"] = (
            "mastered"
            if requirements["stageMastered"]
            else "completed"
            if requirements["stageCompleted"]
            else "pending"
        )
        stages.append(stage)
    return stages


def _sort_resources_for_path(resources: list[dict[str, Any]], topic_order: dict[str, int]) -> list[dict[str, Any]]:
    def sort_key(resource: dict[str, Any]) -> tuple[int, int, int, str]:
        metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
        topic = _resource_topic(resource)
        chapter_order = int(metadata.get("chapterOrder") or 999)
        candidate_order = topic_order.get(topic, 999)
        type_order = RESOURCE_TYPE_ORDER.get(str(resource.get("resourceType") or ""), 999)
        return (chapter_order, candidate_order, type_order, str(resource.get("createdAt") or ""))

    return sorted(resources, key=sort_key)


def _resource_group_key(resource: dict[str, Any]) -> str:
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
    return str(metadata.get("chapterId") or metadata.get("chapterName") or metadata.get("topic") or resource.get("title") or resource.get("id"))


def _resource_topic(resource: dict[str, Any]) -> str:
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
    return str(metadata.get("topic") or metadata.get("chapterName") or resource.get("title") or "数据结构核心知识点").strip()


def _resource_topics(resources: list[dict[str, Any]]) -> list[str]:
    return _unique_nonempty([_resource_topic(item) for item in resources])


def _resource_chapter_meta(resource: dict[str, Any]) -> dict[str, Any]:
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
    chapter_id = str(metadata.get("chapterId") or "").strip()
    chapter_name = str(metadata.get("chapterName") or metadata.get("topic") or "").strip()
    if not chapter_id and not chapter_name:
        return {}
    return {
        "chapterId": chapter_id,
        "chapterName": chapter_name,
        "order": metadata.get("chapterOrder") or metadata.get("order") or 999,
        "knowledgePoints": [chapter_name] if chapter_name else [],
    }


def _resource_citations(resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for resource in resources:
        for citation in resource.get("citations", []) or []:
            if isinstance(citation, dict):
                citations.append(deepcopy(citation))
    return _dedupe_citations(citations)


def _tasks_for_resources(resources: list[dict[str, Any]]) -> list[str]:
    tasks = []
    for resource in resources:
        task = RESOURCE_TASKS.get(str(resource.get("resourceType") or ""))
        if task and task not in tasks:
            tasks.append(task)
    tasks.append("完成阶段测评，根据结果动态更新画像和后续路径")
    return tasks[:6]


def _estimate_stage_days(resources: list[dict[str, Any]]) -> int:
    has_lab = any(resource.get("resourceType") == "lab" for resource in resources)
    has_exercise = any(resource.get("resourceType") == "exercise" for resource in resources)
    if has_lab and has_exercise:
        return 2
    return 1


def _stage_reason(context: dict[str, Any], topic: str, resources: list[dict[str, Any]], citations: list[dict[str, Any]]) -> str:
    profile_count = len(context.get("profileItems", []))
    type_labels = _unique_nonempty([resource.get("resourceType") for resource in resources])
    return (
        f"依据 {profile_count} 项学习画像、当前学习进度、{len(citations)} 条课程引用和 "
        f"{len(resources)} 份已审核资源规划；资源类型覆盖：{'、'.join(type_labels)}。"
    )


def _activate_first_pending_stage(stages: list[dict[str, Any]]) -> None:
    first_active = False
    for stage in stages:
        if stage["status"] == "pending" and not first_active:
            stage["status"] = "active"
            first_active = True


def _resource_generation_target(topic: str) -> str:
    return f"围绕{topic}形成讲解文档、视频演示、题库练习和代码实操案例，并服务于个性化学习路径规划。"


def _merge_resources(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for item in [*incoming, *existing]:
        resource_id = str(item.get("id") or "").strip()
        if resource_id and resource_id not in by_id:
            by_id[resource_id] = deepcopy(item)
    return sorted(by_id.values(), key=lambda item: str(item.get("createdAt") or item.get("updatedAt") or ""), reverse=True)


def _attach_resource_to_best_stage(path: dict[str, Any], resource: dict[str, Any]) -> None:
    stages = path.setdefault("stages", [])
    topic = _resource_topic(resource)
    target = next(
        (
            stage for stage in stages
            if topic in json.dumps({
                "name": stage.get("name"),
                "chapterName": stage.get("chapterName"),
                "knowledgePoints": stage.get("knowledgePoints", []),
            }, ensure_ascii=False)
        ),
        None,
    )
    if target is None:
        target = next((stage for stage in stages if stage.get("status") == "active"), None)
    if target is None:
        chapter = _resource_chapter_meta(resource)
        target = {
            "id": chapter.get("chapterId") or f"generated_resources_{uuid4().hex[:8]}",
            "name": f"{topic}资源学习任务",
            "days": 1,
            "status": "active",
            "chapterId": chapter.get("chapterId", ""),
            "chapterName": chapter.get("chapterName", topic),
            "knowledgePoints": [topic],
            "resources": [],
            "tasks": [],
            "acceptance": f"完成已审核资源并能说明「{topic}」的核心概念。",
            "aiReason": "由资源生成任务自动创建学习阶段。",
            "source": "resource",
            "citationChunkIds": [],
        }
        stages.append(target)

    resource_id = str(resource.get("id") or "")
    if resource_id and resource_id not in target.setdefault("resources", []):
        target["resources"].append(resource_id)
    for task in _tasks_for_resources([resource]):
        if task not in target.setdefault("tasks", []):
            target["tasks"].append(task)
    for citation in resource.get("citations", []) or []:
        chunk_id = str(citation.get("chunkId") or "")
        if chunk_id and chunk_id not in target.setdefault("citationChunkIds", []):
            target["citationChunkIds"].append(chunk_id)
    for point in [_resource_topic(resource)]:
        if point and point not in target.setdefault("knowledgePoints", []):
            target["knowledgePoints"].append(point)


def _enhance_path_with_llm(path: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    system_prompt = (
        "你是高校数据结构课程的学习路径规划智能体。只能基于输入中的真实课程引用、画像、资源和进度改写路径。"
        "必须返回 JSON，不要添加输入中不存在的知识点或资源 id。"
    )
    user_prompt = json.dumps({
        "course": COURSE_NAME,
        "path": _compact_path(path),
        "profile": _profile_basis(context["profileItems"]),
        "resources": [_compact_resource(item) for item in context["resources"][:8]],
        "citations": path.get("sourceCitations", [])[:6],
        "progress": context["progress"],
        "expectedSchema": {
            "title": "string",
            "summary": "string",
            "initialReason": "string",
            "stages": [{"id": "same id", "name": "string", "tasks": ["string"], "acceptance": "string", "aiReason": "string"}],
        },
    }, ensure_ascii=False)
    return call_deepseek_json(system_prompt, user_prompt, temperature=0.15, max_tokens=2400)


def _enhance_remedial_stage_with_llm(stage: dict[str, Any], context: dict[str, Any], error_reasons: list[str]) -> dict[str, Any]:
    system_prompt = "你是学习评估 Agent。请基于测评薄弱点和真实课程资料，把补强阶段写得更具体。只返回 JSON。"
    user_prompt = json.dumps({
        "stage": stage,
        "profile": _profile_basis(context["profileItems"]),
        "resources": [_compact_resource(item) for item in context["resources"][:6]],
        "errorReasons": error_reasons,
        "expectedSchema": {"name": "string", "tasks": ["string"], "acceptance": "string", "aiReason": "string"},
    }, ensure_ascii=False)
    return call_deepseek_json(system_prompt, user_prompt, temperature=0.15, max_tokens=1200)


def _merge_llm_enhancement(path: dict[str, Any], enhanced: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(path)
    for key in ["title", "summary", "initialReason"]:
        value = enhanced.get(key)
        if isinstance(value, str) and value.strip():
            merged[key] = value.strip()
    stage_updates = enhanced.get("stages", [])
    if isinstance(stage_updates, list):
        update_by_id = {str(item.get("id")): item for item in stage_updates if isinstance(item, dict)}
        for stage in merged.get("stages", []):
            update = update_by_id.get(str(stage.get("id")))
            if not update:
                continue
            for key in ["name", "acceptance", "aiReason"]:
                value = update.get(key)
                if isinstance(value, str) and value.strip():
                    stage[key] = value.strip()
            tasks = update.get("tasks")
            if isinstance(tasks, list):
                cleaned = [str(item).strip() for item in tasks if str(item).strip()]
                if cleaned:
                    stage["tasks"] = cleaned[:5]
    return merged


def _citations_for_topics(topics: list[str], limit: int) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for topic in _unique_nonempty(topics):
        result = search_chunks(topic, max(3, limit))
        if result.get("coverage") == "none":
            continue
        for item in result.get("items", []):
            citations.append({
                "chunkId": item.get("chunk_id") or item.get("chunkId"),
                "documentId": item.get("document_id") or item.get("documentId") or f"doc_{item.get('chunk_id') or item.get('chunkId')}",
                "documentName": item.get("document_name") or item.get("documentName"),
                "sourceLocation": item.get("source_location") or item.get("sourceLocation") or item.get("section", ""),
                "page": item.get("page", 1),
                "score": item.get("score", 0),
                "similarity": item.get("score", 0),
                "contentPreview": str(item.get("content") or "")[:180],
            })
    return _dedupe_citations(citations)[:limit]


def _topic_candidates(profile_items: list[dict[str, Any]], resources: list[dict[str, Any]], latest: dict[str, Any], topics: list[str] | None) -> list[str]:
    values: list[str] = []
    values.extend(topics or [])
    values.extend(str(item) for item in latest.get("weakness", []) if str(item).strip())
    for item in profile_items:
        dimension = str(item.get("dimension") or "")
        if any(label in dimension for label in ["薄弱", "目标", "易错", "偏好"]):
            values.extend(_split_terms(str(item.get("value") or "")))
    for resource in resources:
        metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
        values.extend([str(metadata.get("topic") or ""), str(metadata.get("chapterName") or ""), str(resource.get("title") or "")])
    if not values:
        values.append("数据结构")
    return _unique_nonempty(values)[:8]


def _resource_ids_for_topics(resources: list[dict[str, Any]], topics: list[str]) -> list[str]:
    ids: list[str] = []
    for resource in resources:
        text = json.dumps({
            "title": resource.get("title"),
            "summary": resource.get("summary"),
            "metadata": resource.get("metadata", {}),
        }, ensure_ascii=False)
        if any(topic and topic in text for topic in topics):
            ids.append(str(resource.get("id")))
    return _unique_nonempty(ids)[:4]


def _chapter_for_topic(chapters: list[dict[str, Any]], topic: str) -> dict[str, Any]:
    for chapter in chapters:
        text = f"{chapter.get('chapterName', '')} {' '.join(chapter.get('knowledgePoints', []))}"
        if topic and topic in text:
            return chapter
    return chapters[0] if chapters else {}


def _profile_basis(profile_items: list[dict[str, Any]]) -> list[str]:
    basis = []
    for item in profile_items:
        dimension = str(item.get("dimension") or "").strip()
        value = str(item.get("value") or "").strip()
        if dimension and value:
            basis.append(f"{dimension}：{value}")
    return basis[:8]


def _split_terms(value: str) -> list[str]:
    for sep in ["、", "；", ",", ";", "，", " "]:
        value = value.replace(sep, "|")
    return [item.strip() for item in value.split("|") if item.strip()]


def _unique_nonempty(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _dedupe_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for citation in citations:
        key = str(citation.get("chunkId") or "")
        if key and key not in seen:
            result.append(citation)
            seen.add(key)
    return result


def _compact_path(path: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": path.get("title"),
        "summary": path.get("summary"),
        "stages": [
            {
                "id": stage.get("id"),
                "name": stage.get("name"),
                "knowledgePoints": stage.get("knowledgePoints", []),
                "resources": stage.get("resources", []),
                "tasks": stage.get("tasks", []),
                "acceptance": stage.get("acceptance"),
            }
            for stage in path.get("stages", [])
        ],
    }


def _compact_resource(resource: dict[str, Any]) -> dict[str, Any]:
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
    return {
        "id": resource.get("id"),
        "title": resource.get("title"),
        "type": resource.get("resourceType"),
        "summary": resource.get("summary"),
        "topic": metadata.get("topic"),
        "chapterName": metadata.get("chapterName"),
    }
