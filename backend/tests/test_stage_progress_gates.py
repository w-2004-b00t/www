from __future__ import annotations

import unittest
from copy import deepcopy
from unittest.mock import patch

from fastapi import HTTPException

from app import state
from app.routers import learning_paths
from app.services.path_planner_service import plan_stages_from_resources
from app.services.stage_progress_service import (
    reconcile_learning_path_status,
    stage_completion_requirements,
)


class StageProgressGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.resources = [
            {
                "id": "reading_1",
                "title": "线性表阅读",
                "resourceType": "reading",
                "auditStatus": "passed",
                "metadata": {"topic": "线性表"},
            },
            {
                "id": "exercise_1",
                "title": "线性表练习",
                "resourceType": "exercise",
                "auditStatus": "passed",
                "metadata": {"topic": "线性表"},
            },
        ]
        self.stage = {
            "id": "stage_linear",
            "name": "线性表资源学习任务",
            "status": "active",
            "knowledgePoints": ["线性表", "顺序表"],
            "resources": ["reading_1", "exercise_1"],
            "tasks": ["阅读", "练习", "测评"],
        }
        self.path = {"id": "path_1", "status": "ready", "stages": [self.stage]}

    def test_mastering_one_of_twelve_resources_cannot_complete_stage(self) -> None:
        resources = [
            {
                "id": f"resource_{index}",
                "title": f"资源 {index}",
                "resourceType": "reading",
                "auditStatus": "passed",
                "metadata": {"topic": "线性表"},
            }
            for index in range(12)
        ]
        stage = {**self.stage, "resources": [item["id"] for item in resources]}
        progress = {
            "completedResourceIds": ["resource_1"],
            "masteredResourceIds": ["resource_1"],
            "masteredKnowledgePoints": ["线性表"],
        }

        requirements = stage_completion_requirements(stage, resources, progress, [])

        self.assertFalse(requirements["stageCompleted"])
        self.assertEqual(len(requirements["missingResources"]), 11)

    def test_same_named_mastered_knowledge_point_does_not_complete_stage(self) -> None:
        progress = {
            "completedResourceIds": [],
            "masteredKnowledgePoints": ["线性表"],
        }
        reconciled = reconcile_learning_path_status(self.path, self.resources, progress, [])

        self.assertEqual(reconciled["stages"][0]["status"], "active")
        self.assertFalse(reconciled["stages"][0]["isCompleted"])
        self.assertFalse(reconciled["stages"][0]["isMastered"])

    def test_unviewed_resource_cannot_be_completed(self) -> None:
        progress = {
            "viewedResourceIds": [],
            "completedResourceIds": [],
            "masteredKnowledgePoints": [],
        }
        with (
            patch.object(learning_paths, "_load_resources_with_repair", return_value=self.resources),
            patch.object(state, "load_user_learning_progress", return_value=progress),
        ):
            with self.assertRaises(HTTPException) as context:
                learning_paths.complete_learning_resource(
                    "reading_1",
                    authorization="Bearer token-student",
                )

        self.assertEqual(context.exception.status_code, 409)

    def test_incomplete_resources_cannot_complete_stage(self) -> None:
        progress = {
            "viewedResourceIds": ["reading_1"],
            "completedResourceIds": ["reading_1"],
            "masteredKnowledgePoints": [],
        }
        with (
            patch.object(state, "load_user_learning_path", return_value=self.path),
            patch.object(state, "load_user_learning_progress", return_value=progress),
            patch.object(learning_paths, "_load_resources_with_repair", return_value=self.resources),
            patch.object(learning_paths, "_user_assessments", return_value=[]),
        ):
            with self.assertRaises(HTTPException) as context:
                learning_paths.complete_learning_stage(
                    "stage_linear",
                    authorization="Bearer token-student",
                )

        self.assertEqual(context.exception.status_code, 409)
        self.assertIn("未学完资源", context.exception.detail)
        self.assertIn("阶段测评未达到", context.exception.detail)

    def test_path_regeneration_does_not_expand_existing_progress(self) -> None:
        context = {
            "topics": ["线性表"],
            "chapters": [],
            "resources": self.resources,
            "progress": {
                "completedResourceIds": ["reading_1"],
                "masteredResourceIds": ["reading_1"],
                "masteredKnowledgePoints": ["线性表"],
            },
            "assessments": [],
        }

        stages = plan_stages_from_resources(context, self.resources)

        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]["status"], "pending")

    def test_stage_requires_exercise_submission_and_passing_assessment(self) -> None:
        progress = {
            "completedResourceIds": ["reading_1", "exercise_1"],
            "masteredKnowledgePoints": ["线性表", "顺序表"],
        }
        assessment = {
            "stageId": "stage_linear",
            "score": 80,
            "createdAt": "2026-06-24 22:00",
        }

        before_submit = stage_completion_requirements(
            self.stage,
            self.resources,
            progress,
            [assessment],
        )
        self.assertFalse(before_submit["stageCompleted"])
        self.assertEqual(before_submit["missingExercises"][0]["id"], "exercise_1")

        self.resources[1]["metadata"]["lastPractice"] = {"score": 80}
        completed = stage_completion_requirements(
            self.stage,
            self.resources,
            progress,
            [assessment],
        )
        self.assertTrue(completed["stageCompleted"])
        self.assertTrue(completed["stageMastered"])

    def test_repeated_completion_does_not_add_progress_record(self) -> None:
        progress = {
            "viewedResourceIds": ["reading_1"],
            "completedStageIds": [],
            "completedResourceIds": ["reading_1"],
            "masteredChapterIds": [],
            "masteredKnowledgePoints": [],
            "masteredResourceIds": [],
            "records": [{
                "id": "record_1",
                "source": "manual",
                "completedResourceIds": ["reading_1"],
                "evidence": ["首次标记学完"],
            }],
        }
        saved: list[dict] = []
        with (
            patch.object(state, "load_user_learning_progress", return_value=deepcopy(progress)),
            patch.object(state, "save_user_learning_progress", side_effect=lambda _user_id, value: saved.append(deepcopy(value))),
        ):
            result = state.record_learning_progress(
                "student",
                source="manual",
                completed_resource_ids=["reading_1"],
                evidence=["重复标记学完"],
            )

        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(len(saved[0]["records"]), 1)

    def test_repeated_mastery_does_not_add_progress_record(self) -> None:
        progress = {
            "viewedResourceIds": ["reading_1"],
            "completedStageIds": [],
            "completedResourceIds": ["reading_1"],
            "masteredChapterIds": [],
            "masteredKnowledgePoints": [],
            "masteredResourceIds": ["reading_1"],
            "records": [{
                "id": "record_1",
                "source": "manual",
                "masteredResourceIds": ["reading_1"],
                "evidence": ["首次确认掌握"],
            }],
        }
        saved: list[dict] = []
        with (
            patch.object(state, "load_user_learning_progress", return_value=deepcopy(progress)),
            patch.object(state, "save_user_learning_progress", side_effect=lambda _user_id, value: saved.append(deepcopy(value))),
        ):
            result = state.record_learning_progress(
                "student",
                source="manual",
                mastered_resource_ids=["reading_1"],
                evidence=["重复确认掌握"],
            )

        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(len(saved[0]["records"]), 1)


if __name__ == "__main__":
    unittest.main()
