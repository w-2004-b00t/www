from __future__ import annotations

from copy import deepcopy
from typing import Any


STAGE_ASSESSMENT_PASS_SCORE = 80


def stage_completion_requirements(
    stage: dict[str, Any],
    resources: list[dict[str, Any]],
    progress: dict[str, Any],
    assessments: list[dict[str, Any]],
) -> dict[str, Any]:
    resource_by_id = {
        str(item.get("id") or ""): item
        for item in resources
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    required_resource_ids = {
        str(item)
        for item in stage.get("resources", [])
        if str(item or "").strip()
    }
    completed_resource_ids = {
        str(item)
        for item in progress.get("completedResourceIds", [])
        if str(item or "").strip()
    }
    missing_resource_ids = sorted(required_resource_ids - completed_resource_ids)
    missing_resources = [
        {
            "id": resource_id,
            "title": resource_by_id.get(resource_id, {}).get("title") or resource_id,
        }
        for resource_id in missing_resource_ids
    ]

    required_exercise_ids = {
        resource_id
        for resource_id in required_resource_ids
        if resource_by_id.get(resource_id, {}).get("resourceType") == "exercise"
    }
    submitted_exercise_ids = {
        resource_id
        for resource_id in required_exercise_ids
        if isinstance(
            (resource_by_id.get(resource_id, {}).get("metadata") or {}).get("lastPractice"),
            dict,
        )
    }
    missing_exercise_ids = sorted(required_exercise_ids - submitted_exercise_ids)
    missing_exercises = [
        {
            "id": resource_id,
            "title": resource_by_id.get(resource_id, {}).get("title") or resource_id,
        }
        for resource_id in missing_exercise_ids
    ]

    stage_assessments = [
        item
        for item in assessments
        if isinstance(item, dict) and _assessment_matches_stage(item, stage)
    ]
    latest_assessment = max(
        stage_assessments,
        key=lambda item: str(item.get("createdAt") or ""),
        default=None,
    )
    assessment_score = (
        float(latest_assessment.get("score", 0))
        if latest_assessment is not None
        else None
    )

    resources_completed = bool(required_resource_ids) and not missing_resource_ids
    exercises_submitted = not missing_exercise_ids
    assessment_passed = (
        assessment_score is not None
        and assessment_score >= STAGE_ASSESSMENT_PASS_SCORE
    )
    stage_completed = resources_completed and exercises_submitted and assessment_passed

    required_points = {
        str(item)
        for item in stage.get("knowledgePoints", [])
        if str(item or "").strip()
    }
    mastered_points = {
        str(item)
        for item in progress.get("masteredKnowledgePoints", [])
        if str(item or "").strip()
    }
    all_required_points_mastered = bool(required_points) and required_points.issubset(mastered_points)

    return {
        "requiredResourceIds": sorted(required_resource_ids),
        "missingResources": missing_resources,
        "requiredExerciseIds": sorted(required_exercise_ids),
        "missingExercises": missing_exercises,
        "resourcesCompleted": resources_completed,
        "exercisesSubmitted": exercises_submitted,
        "assessmentScore": assessment_score,
        "assessmentPassed": assessment_passed,
        "assessmentPassScore": STAGE_ASSESSMENT_PASS_SCORE,
        "stageCompleted": stage_completed,
        "allRequiredPointsMastered": all_required_points_mastered,
        "stageMastered": stage_completed and all_required_points_mastered,
    }


def reconcile_learning_path_status(
    path: dict[str, Any],
    resources: list[dict[str, Any]],
    progress: dict[str, Any],
    assessments: list[dict[str, Any]],
) -> dict[str, Any]:
    reconciled = deepcopy(path)
    first_incomplete_seen = False
    for stage in reconciled.get("stages", []):
        requirements = stage_completion_requirements(stage, resources, progress, assessments)
        stage["completionRequirements"] = requirements
        stage["isCompleted"] = requirements["stageCompleted"]
        stage["isMastered"] = requirements["stageMastered"]
        if requirements["stageCompleted"]:
            stage["status"] = "mastered" if requirements["stageMastered"] else "completed"
            stage["completedTasks"] = deepcopy(stage.get("tasks", []))
        elif not first_incomplete_seen:
            stage["status"] = (
                "awaiting_assessment"
                if requirements["resourcesCompleted"] and requirements["exercisesSubmitted"]
                else "active"
            )
            stage["completedTasks"] = []
            first_incomplete_seen = True
        else:
            stage["status"] = "pending"
            stage["completedTasks"] = []
    return reconciled


def _assessment_matches_stage(assessment: dict[str, Any], stage: dict[str, Any]) -> bool:
    stage_id = str(stage.get("id") or "")
    assessment_stage_id = str(
        assessment.get("stageId")
        or (assessment.get("stageSnapshot") or {}).get("id")
        or ""
    )
    if stage_id and assessment_stage_id:
        return stage_id == assessment_stage_id

    stage_points = {
        str(item)
        for item in stage.get("knowledgePoints", [])
        if str(item or "").strip()
    }
    assessment_points = {
        str(item)
        for item in (
            assessment.get("stageKnowledgePoints")
            or (assessment.get("stageSnapshot") or {}).get("knowledgePoints")
            or []
        )
        if str(item or "").strip()
    }
    return bool(stage_points and assessment_points and stage_points == assessment_points)
