from __future__ import annotations

from copy import deepcopy
import re
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException

from .. import state
from ..demo_data import PROFILE, now_text
from ..persistence import list_records, load_json
from ..schemas import LearningIntensityRequest, LearningMasteryRequest, LearningPathAttachResourcesRequest
from ..services.course_progress_service import next_topic_for_progress
from ..services.path_planner_service import empty_learning_path, generate_learning_path_for_user, sync_learning_path_resources
from ..services.stage_progress_service import reconcile_learning_path_status, stage_completion_requirements
from ..services.strict_generation import is_legacy_fallback_resource
from ..utils import is_seed_user, ok, user_id_from_authorization, user_scoped_key

router = APIRouter(prefix="/api/learning-paths", tags=["learning-paths"])


def _has_profile(user_id: str) -> bool:
    default_profile = deepcopy(PROFILE) if is_seed_user(user_id) else []
    return bool(load_json(user_scoped_key("profile_items", user_id), default_profile))


def _empty_path() -> dict:
    return empty_learning_path("请先确认学习画像，并确保课程知识库已有真实资料。")


def _load_resources_with_repair(user_id: str) -> list[dict]:
    resources = [
        item
        for item in state.load_user_resources(user_id)
        if isinstance(item, dict) and not is_legacy_fallback_resource(item)
    ]
    return state.strip_resources_progress_fields(resources)


def _user_assessments(user_id: str) -> list[dict]:
    return [
        item
        for item in list_records("assessment_results", 200)
        if item.get("userId") == user_id
    ]


def _reconcile_path_for_user(user_id: str, learning_path: dict, resources: list[dict] | None = None) -> dict:
    return reconcile_learning_path_status(
        learning_path,
        resources if resources is not None else _load_resources_with_repair(user_id),
        state.load_user_learning_progress(user_id),
        _user_assessments(user_id),
    )


def _clean_ids(values: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    for value in values or []:
        item = str(value or "").strip()
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned


def _normalize_mastery_target(value: object) -> str:
    return re.sub(r"[\s、，,。()（）\-_/]+", "", str(value or "")).lower()


def _stage_matches_mastery_target(stage: dict, target: str, *, chapter: bool = False) -> bool:
    normalized = _normalize_mastery_target(target)
    candidates = (
        [stage.get("chapterId", ""), stage.get("chapterName", "")]
        if chapter
        else [stage.get("name", ""), stage.get("chapterName", ""), *(stage.get("knowledgePoints", []) or [])]
    )
    for candidate in candidates:
        value = _normalize_mastery_target(candidate)
        if value and normalized and (
            value == normalized
            or (min(len(value), len(normalized)) >= 2 and (value in normalized or normalized in value))
        ):
            return True
    return False


def _validate_mastery_prerequisites(
    *,
    learning_path: dict,
    progress: dict,
    chapter_ids: list[str],
    knowledge_points: list[str],
    completed_target_resources: list[dict],
) -> None:
    completed_stage_ids = {str(item) for item in progress.get("completedStageIds", [])}
    stages = learning_path.get("stages", []) if isinstance(learning_path, dict) else []
    missing: list[str] = []
    targets = [(item, True) for item in chapter_ids] + [(item, False) for item in knowledge_points]
    for target, chapter in targets:
        matches = [
            stage for stage in stages
            if _stage_matches_mastery_target(stage, target, chapter=chapter)
        ]
        ready = any(
            str(stage.get("id") or "") in completed_stage_ids
            or stage.get("status") == "completed"
            or stage.get("status") == "mastered"
            for stage in matches
        )
        if not ready:
            missing.append(target)
    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"请先完成对应学习阶段或其全部资源后再确认掌握：{', '.join(missing)}",
        )


def _path_needs_resource_sync(learning_path: dict, resources: list[dict]) -> bool:
    passed_ids = {
        str(item.get("id") or "").strip()
        for item in resources
        if item.get("auditStatus") == "passed" and str(item.get("id") or "").strip()
    }
    if not passed_ids:
        return False
    stages = learning_path.get("stages") or []
    linked_ids = {
        str(resource_id).strip()
        for stage in stages
        for resource_id in (stage.get("resources") or [])
        if str(resource_id or "").strip()
    }
    coverage = learning_path.get("resourceCoverage") if isinstance(learning_path.get("resourceCoverage"), dict) else {}
    return (
        learning_path.get("status") != "ready"
        or not stages
        or not linked_ids
        or bool(passed_ids - linked_ids)
        or int(coverage.get("approvedTotal") or 0) != len(passed_ids)
        or int(coverage.get("linkedTotal") or 0) != len(passed_ids & linked_ids)
    )


@router.get("/me")
def get_learning_path(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    if not _has_profile(user_id):
        return ok(_empty_path())
    learning_path = state.load_user_learning_path(user_id)
    if not learning_path.get("stages") and not learning_path.get("status"):
        learning_path = empty_learning_path("尚未生成正式学习路径。请先点击生成路径。")
    resources = _load_resources_with_repair(user_id)
    if _path_needs_resource_sync(learning_path, resources):
        with state.lock:
            learning_path = sync_learning_path_resources(
                user_id,
                learning_path=learning_path,
                resources=resources,
                trigger="已审核资源自动进入学习路径",
            )
            state.save_user_learning_path(user_id, state.strip_learning_path_progress_fields(learning_path))
    reconciled = _reconcile_path_for_user(user_id, learning_path, resources)
    state.save_user_learning_path(user_id, state.strip_learning_path_progress_fields(reconciled))
    return ok(deepcopy(reconciled))


@router.post("/generate")
def generate_learning_path(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    if not _has_profile(user_id):
        return ok(_empty_path())
    learning_path = generate_learning_path_for_user(user_id)
    state.save_user_learning_path(user_id, state.strip_learning_path_progress_fields(learning_path))
    reconciled = _reconcile_path_for_user(user_id, learning_path)
    state.save_user_learning_path(user_id, state.strip_learning_path_progress_fields(reconciled))
    return ok(deepcopy(reconciled))


@router.get("/me/progress")
def get_learning_progress(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    return ok(deepcopy(state.load_user_learning_progress(user_id)))


@router.get("/me/next-topic")
def get_next_learning_topic(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    progress = state.load_user_learning_progress(user_id)
    learning_path = state.load_user_learning_path(user_id)
    resources = _load_resources_with_repair(user_id)
    if _path_needs_resource_sync(learning_path, resources):
        with state.lock:
            learning_path = sync_learning_path_resources(
                user_id,
                learning_path=learning_path,
                resources=resources,
                trigger="已审核资源自动进入学习路径",
            )
            state.save_user_learning_path(user_id, state.strip_learning_path_progress_fields(learning_path))
    annotated = _reconcile_path_for_user(user_id, learning_path, resources)
    return ok(next_topic_for_progress(progress, annotated))


@router.post("/me/mastery")
def mark_learning_mastery(
    payload: LearningMasteryRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    resource_ids = _clean_ids(payload.resource_ids)
    chapter_ids = _clean_ids(payload.chapter_ids)
    knowledge_points = _clean_ids(payload.knowledge_points)
    if not resource_ids and not chapter_ids and not knowledge_points:
        raise HTTPException(status_code=400, detail="请至少选择一个要标记掌握的资源、章节或知识点")
    progress = state.load_user_learning_progress(user_id)
    learning_path = state.load_user_learning_path(user_id)
    resources = _load_resources_with_repair(user_id)
    learning_path = reconcile_learning_path_status(
        learning_path,
        resources,
        progress,
        _user_assessments(user_id),
    )
    completed_target_resources: list[dict] = []
    if resource_ids:
        existing_resource_ids = {str(item.get("id") or "") for item in resources}
        missing_ids = [resource_id for resource_id in resource_ids if resource_id not in existing_resource_ids]
        if missing_ids:
            raise HTTPException(status_code=404, detail=f"学习资源不存在或不属于当前用户：{', '.join(missing_ids)}")
        completed_resource_ids = {str(item) for item in progress.get("completedResourceIds", [])}
        incomplete_ids = [resource_id for resource_id in resource_ids if resource_id not in completed_resource_ids]
        if incomplete_ids:
            raise HTTPException(status_code=409, detail="请先标记学完后再确认掌握")
        completed_target_resources = [
            item for item in resources if str(item.get("id") or "") in set(resource_ids)
        ]
    _validate_mastery_prerequisites(
        learning_path=learning_path,
        progress=progress,
        chapter_ids=chapter_ids,
        knowledge_points=knowledge_points,
        completed_target_resources=completed_target_resources,
    )
    progress = state.record_learning_progress(
        user_id,
        source="manual",
        mastered_chapter_ids=chapter_ids,
        mastered_knowledge_points=knowledge_points,
        mastered_resource_ids=resource_ids,
        evidence=payload.evidence or ["学生手动标记已掌握"],
    )
    learning_path = _reconcile_path_for_user(user_id, learning_path)
    state.save_user_learning_path(user_id, state.strip_learning_path_progress_fields(learning_path))
    return ok({
        "progress": deepcopy(progress),
        "learningPath": deepcopy(learning_path),
        "nextTopic": deepcopy(next_topic_for_progress(progress, learning_path)),
    })


@router.post("/me/resources/{resource_id}/view")
def view_learning_resource(resource_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    resources = _load_resources_with_repair(user_id)
    resource = next((item for item in resources if item.get("id") == resource_id), None)
    if resource is None:
        raise HTTPException(status_code=404, detail="学习资源不存在")
    if resource.get("auditStatus") != "passed":
        raise HTTPException(status_code=409, detail="该资源尚未通过审核，不能记录为已浏览")
    progress = state.record_learning_progress(
        user_id,
        source="resource_view",
        viewed_resource_ids=[resource_id],
        evidence=[f"学生打开资源详情页开始学习「{resource.get('title', resource_id)}」"],
    )
    learning_path = state.annotate_learning_path_with_progress(state.load_user_learning_path(user_id), progress)
    return ok({
        "progress": deepcopy(progress),
        "nextTopic": deepcopy(next_topic_for_progress(progress, learning_path)),
    })


@router.post("/me/resources/{resource_id}/complete")
def complete_learning_resource(resource_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    resources = _load_resources_with_repair(user_id)
    resource = next((item for item in resources if item.get("id") == resource_id), None)
    if resource is None:
        raise HTTPException(status_code=404, detail="学习资源不存在")
    progress = state.load_user_learning_progress(user_id)
    if resource_id not in {str(item) for item in progress.get("viewedResourceIds", [])}:
        raise HTTPException(status_code=409, detail="请先打开资源详情页开始学习后再标记学完")
    progress = state.record_learning_progress(
        user_id,
        source="manual",
        completed_resource_ids=[resource_id],
        evidence=[f"学生手动标记资源「{resource.get('title', resource_id)}」已学完"],
    )
    learning_path = state.annotate_learning_path_with_progress(state.load_user_learning_path(user_id), progress)
    return ok({
        "progress": deepcopy(progress),
        "nextTopic": deepcopy(next_topic_for_progress(progress, learning_path)),
    })


@router.post("/me/stages/{stage_id}/complete")
def complete_learning_stage(stage_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    learning_path = state.load_user_learning_path(user_id)
    stages = learning_path.get("stages", [])
    stage_index = next((index for index, item in enumerate(stages) if item.get("id") == stage_id), -1)
    if stage_index < 0:
        raise HTTPException(status_code=404, detail="学习阶段不存在")
    resources = _load_resources_with_repair(user_id)
    progress = state.load_user_learning_progress(user_id)
    assessments = _user_assessments(user_id)
    current = stages[stage_index]
    requirements = stage_completion_requirements(current, resources, progress, assessments)
    if not requirements["stageCompleted"]:
        reasons = []
        if requirements["missingResources"]:
            reasons.append(
                "未学完资源：" + "、".join(item["title"] for item in requirements["missingResources"])
            )
        if requirements["missingExercises"]:
            reasons.append(
                "未提交必要练习：" + "、".join(item["title"] for item in requirements["missingExercises"])
            )
        if not requirements["assessmentPassed"]:
            score = requirements["assessmentScore"]
            reasons.append(
                f"阶段测评未达到 {requirements['assessmentPassScore']} 分"
                + (f"（当前 {score:g} 分）" if score is not None else "（尚未提交）")
            )
        raise HTTPException(status_code=409, detail="；".join(reasons))
    with state.lock:
        before_path = [item.get("name", "") for item in stages]
        current["status"] = "mastered" if requirements["stageMastered"] else "completed"
        current["completedTasks"] = deepcopy(current.get("tasks", []))
        progress = state.record_learning_progress(
            user_id,
            source="stage_complete",
            completed_stage_ids=[stage_id],
            evidence=[
                f"完成阶段：{current.get('name', stage_id)}",
                f"全部必修资源已学完，必要练习已提交，阶段测评 {requirements['assessmentScore']:g} 分",
            ],
        )
        if stage_index + 1 < len(stages) and stages[stage_index + 1].get("status") == "pending":
            stages[stage_index + 1]["status"] = "active"
        learning_path = reconcile_learning_path_status(
            learning_path,
            resources,
            progress,
            assessments,
        )
        next_topic = next_topic_for_progress(progress, learning_path)
        active_name = next((item.get("name") for item in learning_path.get("stages", []) if item.get("status") == "active"), "")
        next_label = next_topic.get("topic") or active_name or "暂无下一步推荐"
        next_reason = next_topic.get("blockingReason") or next_topic.get("reason") or "阶段完成后系统重新计算下一步。"
        learning_path.setdefault("adjustmentHistory", []).insert(0, {
            "id": f"path_log_{uuid4().hex[:8]}",
            "source": "behavior",
            "trigger": "阶段完成",
            "reason": next_reason,
            "before": current["name"],
            "after": next_label,
            "beforePath": before_path,
            "afterPath": [item.get("name", "") for item in learning_path.get("stages", [])],
            "evidence": [
                *(item.get("sourceLocation", "") for item in next_topic.get("evidence", []) if item.get("sourceLocation")),
                "阶段完成记录已保存到学习进度",
            ],
            "createdAt": now_text(),
        })
        state.save_user_learning_path(user_id, state.strip_learning_path_progress_fields(learning_path))
    return ok({"learningPath": deepcopy(learning_path), "nextTopic": deepcopy(next_topic)})


@router.post("/me/resources/attach")
def attach_resources_to_learning_path(
    payload: LearningPathAttachResourcesRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    resources = _load_resources_with_repair(user_id)
    learning_path = state.load_user_learning_path(user_id)
    requested_ids = [resource_id for resource_id in payload.resource_ids if resource_id]
    if not requested_ids:
        raise HTTPException(status_code=400, detail="请选择要加入学习路径的资源")

    existing_resources = {
        resource["id"]: resource
        for resource in resources
        if resource.get("id") in requested_ids
    }
    missing_ids = [resource_id for resource_id in requested_ids if resource_id not in existing_resources]
    if missing_ids:
        raise HTTPException(status_code=404, detail=f"资源不存在：{', '.join(missing_ids)}")

    attachable_ids = [
        resource_id
        for resource_id in requested_ids
        if existing_resources[resource_id].get("auditStatus") == "passed"
    ]
    if not attachable_ids:
        raise HTTPException(status_code=409, detail="当前没有已通过审核、可加入学习路径的资源")

    with state.lock:
        learning_path = sync_learning_path_resources(
            user_id,
            learning_path=learning_path,
            resources=resources,
            requested_resource_ids=attachable_ids,
            task_id=payload.task_id,
            trigger="资源中心资料加入路径",
        )
        state.save_user_learning_path(user_id, state.strip_learning_path_progress_fields(learning_path))

    return ok(deepcopy(learning_path))


def _resource_topics(resources: list[dict]) -> list[str]:
    topics: list[str] = []
    for resource in resources:
        metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
        topic = str(metadata.get("topic") or resource.get("title") or "").strip()
        if topic and topic not in topics:
            topics.append(topic)
    return topics[:6]


def _resource_chapter_meta(resources: list[dict]) -> dict:
    for resource in resources:
        metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
        chapter_id = str(metadata.get("chapterId") or "").strip()
        chapter_name = str(metadata.get("chapterName") or metadata.get("topic") or "").strip()
        if chapter_id or chapter_name:
            return {"chapterId": chapter_id, "chapterName": chapter_name}
    return {}


@router.post("/me/intensity")
def update_learning_intensity(
    payload: LearningIntensityRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    learning_path = state.load_user_learning_path(user_id)
    label_map = {"30min": "每天 30 分钟", "60min": "每天 60 分钟", "sprint": "考前冲刺"}
    if payload.intensity not in label_map:
        raise HTTPException(status_code=400, detail="学习强度不合法")
    with state.lock:
        before = label_map.get(learning_path.get("intensity", "60min"), "每天 60 分钟")
        learning_path["intensity"] = payload.intensity
        learning_path.setdefault("adjustmentHistory", []).insert(0, {
            "id": f"intensity_{uuid4().hex[:8]}",
            "source": "manual",
            "trigger": "学习强度调整",
            "reason": f"学生调整学习强度为「{label_map[payload.intensity]}」。",
            "before": before,
            "after": label_map[payload.intensity],
            "beforePath": [item.get("name", "") for item in learning_path["stages"]],
            "afterPath": [item.get("name", "") for item in learning_path["stages"]],
            "evidence": ["学生手动调整每日学习时间", "路径会重新估算今日任务长度"],
            "createdAt": now_text(),
        })
        state.save_user_learning_path(user_id, learning_path)
    return ok(deepcopy(learning_path))
