from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any

from .course_config import EMPTY_LEARNING_PATH
from .demo_data import LEARNING_PATH, PROFILE, RESOURCES, USERS
from .persistence import init_db, list_json_keys, load_json, save_json
from .services.knowledge_point_repair import repair_knowledge_point_pollution

lock = threading.RLock()
init_db()
repair_knowledge_point_pollution()

profile_items: list[dict[str, Any]] = load_json("profile_items", deepcopy(PROFILE))
resources: list[dict[str, Any]] = load_json("resources", deepcopy(RESOURCES))
learning_path: dict[str, Any] = load_json("learning_path", deepcopy(LEARNING_PATH))
tasks: dict[str, dict[str, Any]] = {}
audit_history: list[dict[str, Any]] = load_json("audit_history", [])
tutor_notes: list[dict[str, Any]] = load_json("tutor_notes", [])
tutor_mistakes: list[dict[str, Any]] = load_json("tutor_mistakes", [])
assessment_results: list[dict[str, Any]] = load_json("assessment_results", [])
course_chapters: list[dict[str, Any]] = load_json("course_chapters", [])

from .services.mistake_repository import migrate_legacy_mistakes

migrate_legacy_mistakes(tutor_mistakes)


def reset_to_empty_data_structure_course() -> None:
    """Remove old demo course content so it cannot appear as data-structure evidence."""
    resources.clear()
    course_chapters.clear()
    audit_history.clear()
    tutor_notes.clear()
    tutor_mistakes.clear()
    assessment_results.clear()
    learning_path.clear()
    learning_path.update(deepcopy(EMPTY_LEARNING_PATH))


def persist_state() -> None:
    save_json("profile_items", profile_items)
    save_json("resources", resources)
    save_json("learning_path", learning_path)
    save_json("audit_history", audit_history)
    save_json("tutor_notes", tutor_notes)
    save_json("assessment_results", assessment_results)
    save_json("course_chapters", course_chapters)


def _is_seed_user(user_id: str) -> bool:
    return any(user.get("id") == user_id for user in USERS)


def user_key(base_key: str, user_id: str) -> str:
    return f"{base_key}::{user_id or 'anonymous'}"


def load_user_resources(user_id: str) -> list[dict[str, Any]]:
    default_resources = deepcopy(RESOURCES) if _is_seed_user(user_id) else []
    return load_json(user_key("resources", user_id), default_resources)


def save_user_resources(user_id: str, items: list[dict[str, Any]]) -> None:
    sanitized = strip_resources_progress_fields(items)
    save_json(user_key("resources", user_id), sanitized)
    if _is_seed_user(user_id):
        resources[:] = deepcopy(sanitized)


def load_user_learning_path(user_id: str) -> dict[str, Any]:
    default_path = deepcopy(LEARNING_PATH) if _is_seed_user(user_id) else deepcopy(EMPTY_LEARNING_PATH)
    return load_json(user_key("learning_path", user_id), default_path)


def save_user_learning_path(user_id: str, path: dict[str, Any]) -> None:
    save_json(user_key("learning_path", user_id), path)
    if _is_seed_user(user_id):
        learning_path.clear()
        learning_path.update(deepcopy(path))


def _empty_learning_progress() -> dict[str, Any]:
    return {
        "viewedResourceIds": [],
        "completedStageIds": [],
        "completedResourceIds": [],
        "masteredChapterIds": [],
        "masteredKnowledgePoints": [],
        "masteredResourceIds": [],
        "records": [],
    }


def _normalize_learning_progress(progress: dict[str, Any] | None) -> dict[str, Any]:
    normalized = _empty_learning_progress()
    if isinstance(progress, dict):
        for key in ["viewedResourceIds", "completedStageIds", "completedResourceIds", "masteredChapterIds", "masteredKnowledgePoints", "masteredResourceIds"]:
            values = progress.get(key, [])
            if isinstance(values, list):
                normalized[key] = [str(item) for item in values if str(item or "").strip()]
        records = progress.get("records", [])
        if isinstance(records, list):
            normalized_records = []
            for item in records:
                if not isinstance(item, dict):
                    continue
                record = deepcopy(item)
                for key in ["viewedResourceIds", "completedStageIds", "completedResourceIds", "masteredChapterIds", "masteredKnowledgePoints", "masteredResourceIds"]:
                    values = record.get(key, [])
                    record[key] = [str(value) for value in values if str(value or "").strip()] if isinstance(values, list) else []
                normalized_records.append(record)
            normalized["records"] = normalized_records
    return normalized


def load_user_learning_progress(user_id: str) -> dict[str, Any]:
    return _normalize_learning_progress(load_json(user_key("learning_progress", user_id), _empty_learning_progress()))


def save_user_learning_progress(user_id: str, progress: dict[str, Any]) -> None:
    save_json(user_key("learning_progress", user_id), _normalize_learning_progress(progress))


def _append_unique(target: list[str], values: list[str]) -> list[str]:
    existing = set(target)
    added = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in existing:
            target.append(item)
            existing.add(item)
            added.append(item)
    return added


def record_learning_progress(
    user_id: str,
    *,
    source: str,
    viewed_resource_ids: list[str] | None = None,
    completed_stage_ids: list[str] | None = None,
    completed_resource_ids: list[str] | None = None,
    mastered_chapter_ids: list[str] | None = None,
    mastered_knowledge_points: list[str] | None = None,
    mastered_resource_ids: list[str] | None = None,
    score: int | float | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    from .demo_data import now_text

    progress = load_user_learning_progress(user_id)
    added_viewed_resources = _append_unique(progress["viewedResourceIds"], viewed_resource_ids or [])
    added_completed_stages = _append_unique(progress["completedStageIds"], completed_stage_ids or [])
    added_completed_resources = _append_unique(progress["completedResourceIds"], completed_resource_ids or [])
    added_mastered_chapters = _append_unique(progress["masteredChapterIds"], mastered_chapter_ids or [])
    added_mastered_points = _append_unique(progress["masteredKnowledgePoints"], mastered_knowledge_points or [])
    added_mastered_resources = _append_unique(progress["masteredResourceIds"], mastered_resource_ids or [])
    if added_viewed_resources or added_completed_stages or added_completed_resources or added_mastered_chapters or added_mastered_points or added_mastered_resources:
        progress["records"].insert(0, {
            "id": f"progress_{len(progress['records']) + 1}_{now_text().replace(' ', '_').replace(':', '')}",
            "source": source,
            "viewedResourceIds": added_viewed_resources,
            "completedStageIds": added_completed_stages,
            "completedResourceIds": added_completed_resources,
            "masteredChapterIds": added_mastered_chapters,
            "masteredKnowledgePoints": added_mastered_points,
            "masteredResourceIds": added_mastered_resources,
            "score": score,
            "evidence": evidence or [],
            "createdAt": now_text(),
        })
    save_user_learning_progress(user_id, progress)
    return progress


def annotate_learning_path_with_progress(path: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    annotated = deepcopy(path)
    completed_stage_ids = set(progress.get("completedStageIds", []))
    mastered_resource_ids = set(progress.get("masteredResourceIds", []))
    active_index = -1
    for index, stage in enumerate(annotated.get("stages", [])):
        resources = set(stage.get("resources", []) or [])
        points = set(stage.get("knowledgePoints", []) or [])
        stage["isCompleted"] = stage.get("id") in completed_stage_ids or stage.get("status") == "completed"
        stage["isMastered"] = stage.get("status") == "mastered"
        stage["masteryEvidence"] = [
            item for item in list(points) + list(resources)
            if item in mastered_resource_ids
        ]
        if stage.get("status") in {"active", "awaiting_assessment"}:
            active_index = index
    if active_index < 0:
        for stage in annotated.get("stages", []):
            if stage.get("status") == "pending" and not stage.get("isCompleted"):
                stage["status"] = "active"
                break
    return annotated


def annotate_resources_with_progress(resources: list[dict[str, Any]], progress: dict[str, Any]) -> list[dict[str, Any]]:
    viewed_resource_ids = set(progress.get("viewedResourceIds", []))
    completed_resource_ids = set(progress.get("completedResourceIds", []))
    mastered_resource_ids = set(progress.get("masteredResourceIds", []))
    records = progress.get("records", [])
    annotated = []
    for resource in resources:
        item = deepcopy(resource)
        resource_id = str(item.get("id", ""))
        item["isViewed"] = resource_id in viewed_resource_ids
        item["isCompleted"] = resource_id in completed_resource_ids
        item["isMastered"] = resource_id in mastered_resource_ids
        item["masteryEvidence"] = [
            evidence
            for record in records
            if resource_id in record.get("masteredResourceIds", [])
            for evidence in record.get("evidence", [])
        ][:5]
        annotated.append(item)
    return annotated


def strip_resource_progress_fields(resource: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(resource)
    for key in ["isViewed", "isCompleted", "isMastered", "masteryEvidence"]:
        item.pop(key, None)
    return item


def strip_resources_progress_fields(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [strip_resource_progress_fields(item) for item in items]


def strip_learning_path_progress_fields(path: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(path)
    for stage in item.get("stages", []):
        if isinstance(stage, dict):
            for key in ["isCompleted", "isMastered", "masteryEvidence", "completionRequirements"]:
                stage.pop(key, None)
    return item


def _record_is_completion_only(record: dict[str, Any]) -> bool:
    evidence_text = " ".join(str(item) for item in record.get("evidence", []))
    learned_markers = ("已学完", "宸插")
    mastered_markers = ("已掌握", "宸叉")
    return any(marker in evidence_text for marker in learned_markers) and not any(marker in evidence_text for marker in mastered_markers)


def _rebuild_progress_from_records(progress: dict[str, Any]) -> dict[str, Any]:
    rebuilt = _empty_learning_progress()
    for record in progress.get("records", []):
        if not isinstance(record, dict):
            continue
        for key in ["viewedResourceIds", "completedStageIds", "completedResourceIds", "masteredChapterIds", "masteredKnowledgePoints", "masteredResourceIds"]:
            _append_unique(rebuilt[key], record.get(key, []))
        rebuilt["records"].append(record)
    return rebuilt


def repair_completion_only_mastery_progress() -> None:
    for key in list_json_keys("learning_progress::"):
        progress = _normalize_learning_progress(load_json(key, _empty_learning_progress()))
        changed = False
        for record in progress.get("records", []):
            if _record_is_completion_only(record):
                for field in ["masteredChapterIds", "masteredKnowledgePoints", "masteredResourceIds"]:
                    if record.get(field):
                        record[field] = []
                        changed = True
        if not changed:
            continue
        save_json(key, _normalize_learning_progress(_rebuild_progress_from_records(progress)))


def remove_manual_resource_learning_progress(user_id: str) -> dict[str, Any]:
    """Remove old manual resource marks created before view/complete/mastery gates existed."""
    key = user_key("learning_progress", user_id)
    progress = _normalize_learning_progress(load_json(key, _empty_learning_progress()))
    records = []
    changed = False
    for record in progress.get("records", []):
        evidence_text = " ".join(str(item) for item in record.get("evidence", []))
        if record.get("source") == "manual" and "学生手动标记资源" in evidence_text:
            changed = True
            continue
        records.append(record)
    if changed:
        progress["records"] = records
        progress = _normalize_learning_progress(_rebuild_progress_from_records(progress))
        save_json(key, progress)
    return progress


def refresh_vector_index() -> None:
    from .services.vector_service import index_profile, index_resources

    if profile_items:
        index_profile(profile_items)
    if resources:
        index_resources(resources)


repair_completion_only_mastery_progress()
persist_state()
refresh_vector_index()
