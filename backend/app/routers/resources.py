from __future__ import annotations

from copy import deepcopy
import json
import threading
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException

from .. import state
from ..demo_data import PROFILE, now_text
from ..persistence import list_records, list_successful_resource_task_outputs, load_json, save_record
from ..schemas import ResourceAuditRequest, ResourceFeedbackRequest, ResourcePracticeSubmitRequest, VideoDemoGenerateRequest
from ..services.resource_service import (
    build_mindmap_payload,
    build_video_demo_payload,
    get_resource_or_404,
    is_practice_answer_correct,
    sanitize_resource_for_display,
)
from ..services.strict_generation import is_legacy_fallback_resource
from ..services.mistake_repository import create_mistake, list_mistakes
from ..services.agnes_video_service import (
    latest_agnes_video_job,
    record_failed_agnes_video_attempt,
    refresh_agnes_video_job,
    retry_agnes_video_job,
    start_agnes_video_job,
    validate_agnes_video_configuration,
    wake_agnes_worker,
)
from ..services.local_open_video_service import (
    latest_local_video_job,
    refresh_local_video_job,
    render_local_video_job,
    retry_local_video_job,
    start_local_video_job,
)
from ..services.profile_update_service import create_profile_update_draft
from ..services.path_planner_service import sync_learning_path_resources
from ..services.vector_service import recommend_resources_by_profile
from ..utils import is_seed_user, ok, user_id_from_authorization, user_scoped_key

router = APIRouter(prefix="/api/resources", tags=["resources"])


@router.get("")
def list_resources(include_completed: bool = False, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    default_profile = deepcopy(PROFILE) if is_seed_user(user_id) else []
    profile_items = load_json(user_scoped_key("profile_items", user_id), default_profile)
    progress = state.load_user_learning_progress(user_id)
    all_resources = _load_current_user_resources(user_id)
    all_resources = _sanitize_resources_and_persist(user_id, all_resources)
    all_resources = state.strip_resources_progress_fields(all_resources)
    all_resources = state.annotate_resources_with_progress(
        all_resources,
        progress,
    )
    raw_resources = all_resources
    mastered_points = [str(item).strip() for item in progress.get("masteredKnowledgePoints", []) if str(item).strip()]
    mastered_chapter_ids = [str(item).strip() for item in progress.get("masteredChapterIds", []) if str(item).strip()]
    completed_ids = set(progress.get("completedResourceIds", []))
    mastered_ids = set(progress.get("masteredResourceIds", []))
    visible_resources = raw_resources if include_completed else [
        resource
        for resource in raw_resources
        if resource.get("id") not in completed_ids
        and resource.get("id") not in mastered_ids
        and not _resource_matches_mastered_chapters(resource, mastered_chapter_ids)
        and not _resource_matches_mastered_points(resource, mastered_points)
    ]
    if not visible_resources:
        return ok([])
    if not profile_items:
        profile_items = deepcopy(PROFILE)
    return ok(recommend_resources_by_profile(profile_items, visible_resources))


def _load_current_user_resources(user_id: str) -> list[dict]:
    return [
        resource
        for resource in state.load_user_resources(user_id)
        if isinstance(resource, dict) and not is_legacy_fallback_resource(resource)
    ]


def _latest_generated_resources_for_user(user_id: str) -> list[dict]:
    for output in list_successful_resource_task_outputs(user_id=user_id):
        resources = output.get("resources")
        if isinstance(resources, list) and resources:
            return deepcopy([item for item in resources if isinstance(item, dict)])
    return []


def _resource_matches_mastered_points(resource: dict, mastered_points: list[str]) -> bool:
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
    text = " ".join([
        str(resource.get("title", "")),
        str(resource.get("summary", "")),
        str(resource.get("fitReason", "")),
        str(metadata.get("topic", "")),
    ])
    return any(point and point in text for point in mastered_points)


def _resource_matches_mastered_chapters(resource: dict, mastered_chapter_ids: list[str]) -> bool:
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
    chapter_id = str(metadata.get("chapterId") or "").strip()
    return bool(chapter_id and chapter_id in mastered_chapter_ids)


@router.get("/{resource_id}")
def get_resource(resource_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    resources = _load_current_user_resources(user_id)
    progress = state.load_user_learning_progress(user_id)
    resource = get_resource_or_404(resource_id, resources)
    resource = _sanitize_resource_and_persist(resource_id, user_id, resources, resource)
    resource = state.strip_resource_progress_fields(resource)
    return ok(state.annotate_resources_with_progress([resource], progress)[0])


def _sanitize_resources_and_persist(user_id: str, resources: list[dict]) -> list[dict]:
    sanitized = [state.strip_resource_progress_fields(sanitize_resource_for_display(resource)) for resource in resources]
    if _resources_changed(resources, sanitized):
        state.save_user_resources(user_id, sanitized)
        if state.resources:
            global_by_id = {str(item.get("id")): deepcopy(item) for item in state.resources}
            changed_global = False
            for item in sanitized:
                resource_id = str(item.get("id") or "")
                if resource_id in global_by_id and _resources_changed([global_by_id[resource_id]], [item]):
                    global_by_id[resource_id] = deepcopy(item)
                    changed_global = True
            if changed_global:
                state.resources[:] = list(global_by_id.values())
                state.persist_state()
    return sanitized


def _sanitize_resource_and_persist(resource_id: str, user_id: str, resources: list[dict], resource: dict) -> dict:
    sanitized = state.strip_resource_progress_fields(sanitize_resource_for_display(resource))
    if not _resources_changed([resource], [sanitized]):
        return sanitized
    repaired_resources = []
    replaced = False
    for item in resources:
        if str(item.get("id") or "") == resource_id:
            repaired_resources.append(deepcopy(sanitized))
            replaced = True
        else:
            repaired_resources.append(deepcopy(item))
    if not replaced:
        repaired_resources.append(deepcopy(sanitized))
    state.save_user_resources(user_id, repaired_resources)
    for index, item in enumerate(state.resources):
        if str(item.get("id") or "") == resource_id:
            state.resources[index] = deepcopy(sanitized)
            state.persist_state()
            break
    return sanitized


def _resources_changed(before: list[dict], after: list[dict]) -> bool:
    return json.dumps(before, ensure_ascii=False, sort_keys=True) != json.dumps(after, ensure_ascii=False, sort_keys=True)


@router.get("/{resource_id}/video-demo")
def get_resource_video_demo(resource_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_id, user_context = _video_user_context(authorization)
    resources = _load_current_user_resources(user_id)
    video_job = _latest_video_demo_job(resource_id, user_id)
    return ok(build_video_demo_payload(
        sanitize_resource_for_display(get_resource_or_404(resource_id, resources)),
        user_context=user_context,
        video_job=video_job,
    ))


@router.post("/{resource_id}/video-demo/generate")
def generate_resource_video_demo(
    resource_id: str,
    payload: VideoDemoGenerateRequest | None = None,
    authorization: str | None = Header(default=None),
) -> dict:
    render_mode = _normalize_video_render_mode(payload.mode if payload else None)
    user_id, user_context = _video_user_context(authorization)
    resources = _load_current_user_resources(user_id)
    resource = sanitize_resource_for_display(get_resource_or_404(resource_id, resources))
    try:
        if render_mode != "animated_lesson":
            validate_agnes_video_configuration()
        existing = _latest_video_demo_job(resource_id, user_id)
        if existing and existing.get("status") in {
            "queued", "submitting", "rendering", "retry_wait", "downloading",
            "validating", "composing", "completed", "orphaned",
        } and existing.get("renderMode") == render_mode and not _is_preview_video_job(existing):
            existing["reuseReason"] = (
                "completed_video" if existing.get("status") == "completed" else "running_job"
            )
            return ok(existing)
        if existing and existing.get("status") == "failed" and existing.get("scenes") and existing.get("renderMode") == render_mode:
            common_job_kwargs = {
                "resource_id": resource_id,
                "user_id": user_id,
                "title": str(existing.get("title") or f"{existing.get('topic') or '数据结构'} 3 分钟教学演示"),
                "topic": str(existing.get("topic") or "数据结构"),
                "scenes": existing.get("scenes") or [],
                "citations": existing.get("citations") or [],
                "personalization": existing.get("personalization") or {},
                "source_type": existing.get("sourceType"),
                "generation_mode": existing.get("generationMode"),
                "llm_status": existing.get("llmStatus"),
                "agent_trace": [*(existing.get("agentTrace") or []), "复用已校验 DeepSeek 教学镜头，仅重建视频任务"],
                "production_notes": existing.get("productionNotes") or [],
                "render_mode": render_mode,
            }
            reused_job = start_local_video_job(**common_job_kwargs) if render_mode == "animated_lesson" else start_agnes_video_job(**common_job_kwargs)
            if render_mode == "animated_lesson":
                _run_local_video_job_async(reused_job["jobId"])
            else:
                wake_agnes_worker()
            return ok(reused_job)
        payload_data = build_video_demo_payload(
            resource,
            user_context=user_context,
            generate_storyboard=True,
        )
        common_job_kwargs = {
            "resource_id": resource_id,
            "user_id": user_id,
            "title": payload_data["title"],
            "topic": payload_data["topic"],
            "scenes": payload_data["scenes"],
            "citations": payload_data.get("citations") or [],
            "personalization": payload_data.get("personalizationEvidence") or {},
            "source_type": payload_data.get("sourceType"),
            "generation_mode": payload_data.get("generationMode"),
            "llm_status": payload_data.get("llmStatus"),
            "agent_trace": payload_data.get("agentTrace"),
            "production_notes": payload_data.get("productionNotes"),
            "render_mode": render_mode,
        }
        job = start_local_video_job(**common_job_kwargs) if render_mode == "animated_lesson" else start_agnes_video_job(**common_job_kwargs)
    except HTTPException as exc:
        _record_video_generation_failure(resource_id, user_id, resource, user_context, exc)
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    except Exception as exc:
        failed_job = _record_video_generation_failure(
            resource_id,
            user_id,
            resource,
            user_context,
            HTTPException(status_code=502, detail=str(exc) or exc.__class__.__name__),
        )
        raise HTTPException(status_code=502, detail=failed_job.get("errorDetail") or str(exc)) from exc
    if render_mode == "animated_lesson":
        _run_local_video_job_async(job["jobId"])
    else:
        wake_agnes_worker()
    return ok(job)


@router.get("/{resource_id}/video-demo/status")
def get_resource_video_demo_status(
    resource_id: str,
    jobId: str,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    job = refresh_local_video_job(jobId, user_id=user_id) if jobId.startswith("hyperframes_") else refresh_agnes_video_job(jobId, user_id=user_id)
    if job.get("resourceId") != resource_id:
        raise HTTPException(status_code=404, detail="该任务不属于当前视频资源。")
    return ok(job)


@router.post("/{resource_id}/video-demo/retry")
def retry_resource_video_demo(
    resource_id: str,
    jobId: str,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    if jobId.startswith("hyperframes_"):
        job = retry_local_video_job(jobId, user_id=user_id)
        _run_local_video_job_async(job["jobId"])
    else:
        job = retry_agnes_video_job(jobId, user_id=user_id)
    if job.get("resourceId") != resource_id:
        raise HTTPException(status_code=404, detail="该任务不属于当前视频资源。")
    return ok(job)


def _normalize_video_render_mode(mode: str | None) -> str:
    value = str(mode or "animated_lesson").strip()
    if value == "fast_preview":
        value = "agnes_clip"
    if value not in {"full_hybrid", "agnes_clip", "animated_lesson"}:
        raise HTTPException(status_code=422, detail="视频生成模式必须是 animated_lesson、full_hybrid 或 agnes_clip。")
    return value


def _latest_video_demo_job(resource_id: str, user_id: str) -> dict | None:
    jobs = [
        job for job in (
            latest_local_video_job(resource_id, user_id),
            latest_agnes_video_job(resource_id, user_id),
        )
        if job
    ]
    if not jobs:
        return None
    return max(jobs, key=_video_job_rank)


def _video_job_rank(job: dict) -> tuple[int, str]:
    status = str(job.get("status") or "")
    timestamp = str(job.get("updatedAt") or job.get("createdAt") or "")
    if status == "completed" and job.get("videoUrl"):
        return (4, timestamp)
    if status in {"queued", "submitting", "rendering", "retry_wait", "downloading", "validating", "composing", "verifying"}:
        return (3, timestamp)
    if status == "orphaned":
        return (2, timestamp)
    return (1, timestamp)


def _is_preview_video_job(job: dict | None) -> bool:
    return bool(
        job
        and job.get("status") == "completed"
        and (
            job.get("compositionWarning")
            or job.get("fallbackVideoUrl")
            or job.get("fallbackReason") == "composition_failed"
            or job.get("compositionStage") == "agnes_clip_fallback"
            or job.get("isPreviewVideo")
        )
    )


def _run_local_video_job_async(job_id: str) -> None:
    thread = threading.Thread(target=render_local_video_job, args=(job_id,), daemon=True, name=f"video-{job_id}")
    thread.start()


def _video_user_context(authorization: str | None) -> tuple[str, dict]:
    user_id = user_id_from_authorization(authorization)
    default_profile = deepcopy(PROFILE) if is_seed_user(user_id) else []
    profile_items = load_json(user_scoped_key("profile_items", user_id), default_profile)
    if not profile_items:
        raise HTTPException(status_code=409, detail="还没有当前用户的学习画像，请先完成对话画像并确认保存。")
    user_context = {
        "userId": user_id,
        "profileItems": profile_items,
        "learningPath": state.load_user_learning_path(user_id),
        "latestAssessment": (list_records("assessment_results", 5) or [None])[0],
        "recentMistakes": list_mistakes(user_id, 5),
    }
    return user_id, user_context


def _record_video_generation_failure(
    resource_id: str,
    user_id: str,
    resource: dict,
    user_context: dict,
    exc: HTTPException,
) -> dict:
    topic = str((resource.get("metadata") or {}).get("topic") or "数据结构课程")
    title = f"{topic} 3 分钟教学演示"
    detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
    citations = resource.get("citations") if isinstance(resource.get("citations"), list) else []
    personalization = {
        "userId": user_context.get("userId") or user_id,
        "profileDimensions": len(user_context.get("profileItems", [])) if isinstance(user_context.get("profileItems"), list) else 0,
    }
    if "AGNES_API_KEY" in detail or "Agnes" in detail:
        error_code = "AGNES_UNAVAILABLE"
    elif "DEEPSEEK_API_KEY" in detail or "DeepSeek" in detail:
        error_code = "DEEPSEEK_UNAVAILABLE"
    elif exc.status_code == 409:
        error_code = "COURSE_CITATIONS_INSUFFICIENT"
    elif "HyperFrames" in detail or "ffprobe" in detail or "FFmpeg" in detail:
        error_code = "HYPERFRAMES_UNAVAILABLE"
    else:
        error_code = exc.__class__.__name__
    return record_failed_agnes_video_attempt(
        resource_id=resource_id,
        user_id=user_id,
        title=title,
        topic=topic,
        error=detail,
        error_code=error_code,
        citations=citations,
        personalization=personalization,
    )


@router.get("/{resource_id}/mindmap")
def get_resource_mindmap(resource_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    resources = _load_current_user_resources(user_id)
    return ok(build_mindmap_payload(sanitize_resource_for_display(get_resource_or_404(resource_id, resources))))


@router.post("/{resource_id}/feedback")
def resource_feedback(
    resource_id: str,
    payload: ResourceFeedbackRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    resources = _load_current_user_resources(user_id)
    resource = get_resource_or_404(resource_id, resources)
    feedback = {
        "id": f"feedback_{uuid4().hex[:8]}",
        "userId": user_id,
        "resourceId": resource_id,
        "type": payload.type,
        "note": payload.note,
        "createdAt": now_text(),
    }
    draft = None
    if payload.type == "too_hard":
        draft = create_profile_update_draft(
            user_id,
            dimension="知识基础",
            value="当前资源难度偏高，需要更基础的分层解释",
            source="behavior",
            trigger="资源反馈：太难",
            evidence=f"学生反馈资源「{resource.get('title', resource_id)}」太难。",
            confidence=0.82,
        )
    elif payload.type == "need_example":
        draft = create_profile_update_draft(
            user_id,
            dimension="资源偏好",
            value="更偏好例题、步骤拆解和对比练习",
            source="behavior",
            trigger="资源反馈：需要例子",
            evidence=f"学生反馈资源「{resource.get('title', resource_id)}」需要更多例子。",
            confidence=0.86,
        )
    elif payload.type == "helpful":
        draft = create_profile_update_draft(
            user_id,
            dimension="认知风格",
            value="图解、例题和代码实践式资源对当前学生有效",
            source="behavior",
            trigger="资源反馈：看懂了",
            evidence=f"学生反馈资源「{resource.get('title', resource_id)}」有帮助。",
            confidence=0.78,
        )
    with state.lock:
        resource.setdefault("feedback", []).insert(0, feedback)
        state.save_user_resources(user_id, resources)
    return ok({"resource": deepcopy(resource), "feedback": feedback, "profileUpdateDraft": draft})


@router.post("/{resource_id}/audit")
def audit_resource(
    resource_id: str,
    payload: ResourceAuditRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    resources = _load_current_user_resources(user_id)
    resource = get_resource_or_404(resource_id, resources)
    if payload.status not in {"pending", "passed", "warning", "rejected"}:
        raise HTTPException(status_code=400, detail="审核状态不合法")
    if payload.status in {"warning", "rejected"} and not payload.reason.strip():
        raise HTTPException(status_code=400, detail="驳回或要求修正必须填写原因")
    record = {
        "id": f"audit_{uuid4().hex[:8]}",
        "resourceId": resource_id,
        "status": payload.status,
        "reason": payload.reason,
        "operator": "课程教师",
        "scope": payload.scope,
        "createdAt": now_text(),
    }
    with state.lock:
        resource["auditStatus"] = payload.status
        resource.setdefault("auditHistory", []).insert(0, record)
        state.audit_history.insert(0, record)
        state.save_user_resources(user_id, resources)
        learning_path = sync_learning_path_resources(
            user_id,
            learning_path=state.load_user_learning_path(user_id),
            resources=resources,
            requested_resource_ids=[resource_id],
            trigger="教师审核状态同步路径",
        )
        state.save_user_learning_path(user_id, state.strip_learning_path_progress_fields(learning_path))
        state.persist_state()
    return ok({"resource": deepcopy(resource), "history": deepcopy(state.audit_history), "learningPath": deepcopy(learning_path)})


@router.post("/{resource_id}/practice/submit")
def submit_resource_practice(
    resource_id: str,
    payload: ResourcePracticeSubmitRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    resources = _load_current_user_resources(user_id)
    learning_path = state.load_user_learning_path(user_id)
    resource = get_resource_or_404(resource_id, resources)
    if resource.get("resourceType") != "exercise":
        raise HTTPException(status_code=400, detail="当前资源不是练习题，无法提交作答")
    try:
        exercises = json.loads(resource.get("content", "[]"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="练习题内容格式异常") from exc

    details = []
    correct_count = 0
    for index, item in enumerate(exercises):
        key = str(index)
        user_answer = payload.answers.get(key, "")
        answer = str(item.get("answer", "")).strip()
        is_correct = is_practice_answer_correct(item, user_answer)
        if is_correct:
            correct_count += 1
        details.append({
            "index": index,
            "stem": item.get("stem", ""),
            "type": item.get("type", "short"),
            "options": deepcopy(item.get("options", [])),
            "userAnswer": user_answer,
            "answer": answer,
            "correct": is_correct,
            "analysis": item.get("analysis", ""),
            "rubric": deepcopy(item.get("rubric", [])),
            "citations": deepcopy(item.get("citations", [])),
            "knowledgePoint": (
                "代码实践"
                if item.get("type") == "code"
                else "待通过真实课程资料确认"
                if "课程资料" in item.get("stem", "")
                else "课程资料基础"
            ),
        })

    total = len(exercises) or 1
    score = round(correct_count / total * 100)
    wrong = [item for item in details if not item["correct"]]
    progress = state.load_user_learning_progress(user_id)
    mastered_points = set(progress.get("masteredKnowledgePoints", []))
    weak_points = [point for point in sorted({item["knowledgePoint"] for item in wrong}) if point not in mastered_points]
    if wrong and not weak_points:
        weak_points = []
    elif not wrong:
        weak_points = ["待通过真实课程资料确认"]
    suggestion = (
        "本资源练习表现稳定，可进入阶段测评。"
        if not wrong
        else "错题涉及已掌握知识点，本次只记录错题，不重复插入补强任务。"
        if not weak_points
        else "请先在错题本订正原题；订正或变式验证失败后，系统再加入补强路径。"
    )
    path_impact = (
        "练习结果良好，学习路径保持当前节奏。"
        if not wrong
        else "错题涉及已掌握知识点，学习路径不重复插入补强任务。"
        if not weak_points
        else f"已记录 {', '.join(weak_points)} 相关错题，当前路径不变；订正失败时再插入补强任务。"
    )
    report_impact = (
        "练习完成记录已写入资源学习效果。"
        if not wrong
        else "错题数量、错因和薄弱知识点会进入学习报告的数据来源。"
    )
    practice_record = {
        "id": f"practice_{uuid4().hex[:8]}",
        "resourceId": resource_id,
        "score": score,
        "correctCount": correct_count,
        "total": len(exercises),
        "createdAt": now_text(),
        "details": details,
        "weakPoints": weak_points,
        "pathImpact": path_impact,
        "reportImpact": report_impact,
    }
    with state.lock:
        resource.setdefault("metadata", {})["lastPractice"] = practice_record
        if score >= 80:
            mastered_points = sorted({
                item["knowledgePoint"]
                for item in details
                if item.get("correct") and item.get("knowledgePoint")
            }) or [str(resource.get("metadata", {}).get("topic") or resource.get("title") or "资源练习").strip()]
            state.record_learning_progress(
                user_id,
                source="resource_practice",
                completed_resource_ids=[resource_id],
                mastered_resource_ids=[resource_id],
                mastered_knowledge_points=mastered_points,
                score=score,
                evidence=[f"资源练习得分：{score} 分", f"正确题数：{correct_count}/{len(exercises)}"],
            )
        created_mistakes = []
        for item in wrong:
            mistake = {
                "id": f"mistake_{uuid4().hex[:8]}",
                "userId": user_id,
                "source": "resource_practice",
                "resourceId": resource_id,
                "knowledge": item["knowledgePoint"],
                "stem": item["stem"],
                "type": item.get("type", "short"),
                "options": deepcopy(item.get("options", [])),
                "userAnswer": item["userAnswer"],
                "answer": item["answer"],
                "analysis": item.get("analysis", ""),
                "rubric": deepcopy(item.get("rubric", [])),
                "citations": deepcopy(item.get("citations", [])),
                "wrongReason": "资源内练习作答错误，已进入错题沉淀。",
                "fixTask": f"复盘「{item['knowledgePoint']}」并重新完成同类练习。",
                "correctionAttempts": [],
                "verificationQuestions": [],
                "verificationAttempts": [],
                "masteryEvidence": [],
                "status": "待订正",
                "createdAt": now_text(),
            }
            created_mistakes.append(mistake)
        state.save_user_resources(user_id, resources)
        state.save_user_learning_path(user_id, learning_path)
    for mistake in created_mistakes:
        create_mistake(mistake)
    return ok({
        "score": score,
        "correctCount": correct_count,
        "total": len(exercises),
        "details": details,
        "suggestion": suggestion,
        "mistakesAdded": len(wrong),
        "studentImpact": "错题已写入错题本，学习报告会统计该资源练习效果。" if wrong else "练习完成记录已保存。",
        "pathImpact": path_impact,
        "reportImpact": report_impact,
    })
