from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.routers.learning_paths import _validate_mastery_prerequisites
from app.services import knowledge_graph_service as service


class KnowledgeMasterySyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.node = {
            "id": "kp_linear",
            "chapterId": "chapter_doc_2",
            "name": "线性表",
            "type": "concept",
        }
        self.resource = {
            "id": "resource_linear",
            "title": "线性表讲解",
            "metadata": {
                "topic": "线性表",
                "chapterId": "chapter_doc_2",
                "chapterName": "线性表",
            },
        }
        self.path = {
            "stages": [{
                "id": "stage_linear",
                "name": "线性表资源学习任务",
                "status": "active",
                "chapterId": "chapter_doc_2",
                "chapterName": "线性表",
                "knowledgePoints": ["线性表"],
                "resources": ["resource_linear"],
            }],
        }

    def test_view_progress_and_assessment_are_weighted(self) -> None:
        progress = {
            "viewedResourceIds": ["resource_linear"],
            "completedStageIds": [],
            "completedResourceIds": [],
            "masteredChapterIds": [],
            "masteredKnowledgePoints": [],
            "masteredResourceIds": [],
        }
        assessments = [{
            "userId": "student",
            "createdAt": "2026-06-24",
            "questionDetails": [{"knowledge_point": "线性表", "score": 80, "correct": True}],
        }]
        with (
            patch.object(service.state, "load_user_learning_progress", return_value=progress),
            patch.object(service.state, "load_user_learning_path", return_value=self.path),
            patch.object(service, "list_records", return_value=assessments),
        ):
            result = service._calculate_mastery_from_learning_data(
                "student",
                [self.node],
                [self.resource],
            )[self.node["id"]]

        self.assertEqual(result["pathScore"], 40)
        self.assertEqual(result["assessmentScore"], 80)
        self.assertEqual(result["finalScore"], 64)
        self.assertEqual(result["matchedStageIds"], ["stage_linear"])

    def test_completed_resource_without_assessment_scores_seventy(self) -> None:
        progress = {
            "viewedResourceIds": ["resource_linear"],
            "completedStageIds": [],
            "completedResourceIds": ["resource_linear"],
            "masteredChapterIds": [],
            "masteredKnowledgePoints": [],
            "masteredResourceIds": [],
        }
        with (
            patch.object(service.state, "load_user_learning_progress", return_value=progress),
            patch.object(service.state, "load_user_learning_path", return_value=self.path),
            patch.object(service, "list_records", return_value=[]),
        ):
            result = service._calculate_mastery_from_learning_data(
                "student",
                [self.node],
                [self.resource],
            )[self.node["id"]]

        self.assertEqual(result["pathScore"], 70)
        self.assertIsNone(result["assessmentScore"])
        self.assertEqual(result["finalScore"], 70)

    def test_mastery_confirmation_requires_completed_stage_or_resources(self) -> None:
        empty_progress = {
            "completedStageIds": [],
            "completedResourceIds": [],
        }
        with self.assertRaises(HTTPException) as context:
            _validate_mastery_prerequisites(
                learning_path=self.path,
                progress=empty_progress,
                chapter_ids=[],
                knowledge_points=["线性表"],
                completed_target_resources=[],
            )
        self.assertEqual(context.exception.status_code, 409)

        with self.assertRaises(HTTPException):
            _validate_mastery_prerequisites(
                learning_path=self.path,
                progress={
                    "completedStageIds": [],
                    "completedResourceIds": ["resource_linear"],
                },
                chapter_ids=[],
                knowledge_points=["线性表"],
                completed_target_resources=[],
            )

        _validate_mastery_prerequisites(
            learning_path=self.path,
            progress={
                "completedStageIds": ["stage_linear"],
                "completedResourceIds": ["resource_linear"],
            },
            chapter_ids=[],
            knowledge_points=["线性表"],
            completed_target_resources=[],
        )

    def test_no_real_chapters_produces_no_graph_structure(self) -> None:
        self.assertEqual(service._derive_structure([]), ([], []))

    def test_user_progress_is_isolated(self) -> None:
        progress_by_user = {
            "student_a": {
                "viewedResourceIds": ["resource_linear"],
                "completedStageIds": [],
                "completedResourceIds": [],
                "masteredChapterIds": [],
                "masteredKnowledgePoints": [],
                "masteredResourceIds": [],
            },
            "student_b": {
                "viewedResourceIds": [],
                "completedStageIds": [],
                "completedResourceIds": [],
                "masteredChapterIds": [],
                "masteredKnowledgePoints": [],
                "masteredResourceIds": [],
            },
        }
        with (
            patch.object(
                service.state,
                "load_user_learning_progress",
                side_effect=lambda user_id: progress_by_user[user_id],
            ),
            patch.object(service.state, "load_user_learning_path", return_value=self.path),
            patch.object(service, "list_records", return_value=[]),
        ):
            score_a = service._calculate_mastery_from_learning_data(
                "student_a", [self.node], [self.resource],
            )[self.node["id"]]["finalScore"]
            score_b = service._calculate_mastery_from_learning_data(
                "student_b", [self.node], [self.resource],
            )[self.node["id"]]["finalScore"]

        self.assertEqual(score_a, 40)
        self.assertEqual(score_b, 0)

    def test_chapter_and_course_scores_roll_up_from_children(self) -> None:
        nodes = [
            {
                "id": "course",
                "type": "course",
                "mastery": 0,
                "masteryEvidence": [],
            },
            {
                "id": "chapter_doc_2",
                "chapterId": "chapter_doc_2",
                "type": "chapter",
                "mastery": 0,
                "masteryEvidence": [],
            },
            {
                "id": "point_a",
                "chapterId": "chapter_doc_2",
                "type": "concept",
                "mastery": 40,
                "masteryEvidence": ["已浏览资源"],
            },
            {
                "id": "point_b",
                "chapterId": "chapter_doc_2",
                "type": "concept",
                "mastery": 80,
                "masteryEvidence": ["测评 80 分"],
            },
        ]

        service._roll_up_mastery(nodes)

        self.assertEqual(nodes[1]["mastery"], 60)
        self.assertEqual(nodes[0]["mastery"], 60)

    def test_zero_progress_keeps_complete_graph_structure(self) -> None:
        nodes = [
            {
                "id": "course",
                "name": "数据结构课程",
                "type": "course",
                "chapterId": None,
                "sourceRefs": [],
            },
            {
                "id": "chapter_doc_2",
                "name": "线性表",
                "type": "chapter",
                "chapterId": "chapter_doc_2",
                "sourceRefs": [],
            },
            {
                "id": "kp_linear",
                "name": "顺序表",
                "type": "concept",
                "chapterId": "chapter_doc_2",
                "sourceRefs": [],
            },
        ]
        edges = [
            service._edge("course", "chapter_doc_2", "contains", "course_structure"),
            service._edge("chapter_doc_2", "kp_linear", "contains", "course_structure"),
        ]
        empty_progress = {
            "viewedResourceIds": [],
            "completedStageIds": [],
            "completedResourceIds": [],
            "masteredChapterIds": [],
            "masteredKnowledgePoints": [],
            "masteredResourceIds": [],
        }
        with (
            patch.object(service, "ensure_course_chapters", return_value=[{"id": "chapter_doc_2"}]),
            patch.object(service, "_derive_structure", return_value=(nodes, edges)),
            patch.object(service, "replace_auto_knowledge_graph"),
            patch.object(service, "list_knowledge_graph_nodes", return_value=[dict(item) for item in nodes]),
            patch.object(service, "list_knowledge_graph_edges", return_value=edges),
            patch.object(service.state, "load_user_resources", return_value=[]),
            patch.object(service.state, "load_user_learning_progress", return_value=empty_progress),
            patch.object(service.state, "load_user_learning_path", return_value={"stages": []}),
            patch.object(service, "list_records", return_value=[]),
        ):
            graph = service.build_graph("course_data_structure", "new_student")

        self.assertEqual(len(graph["nodes"]), 3)
        self.assertEqual(len(graph["edges"]), 2)
        self.assertTrue(all(node["mastery"] == 0 for node in graph["nodes"]))
        self.assertTrue(all(node["masteryStatus"] == "unlearned" for node in graph["nodes"]))

    def test_filter_graph_does_not_filter_by_mastery(self) -> None:
        graph = {
            "nodes": [
                {"id": "unlearned", "type": "concept", "masteryStatus": "unlearned", "difficulty": "基础"},
                {"id": "weak", "type": "concept", "masteryStatus": "weak", "difficulty": "基础"},
                {"id": "learning", "type": "concept", "masteryStatus": "learning", "difficulty": "基础"},
                {"id": "mastered", "type": "concept", "masteryStatus": "mastered", "difficulty": "基础"},
            ],
            "edges": [],
            "stats": {},
        }

        filtered = service.filter_graph(graph)

        self.assertEqual(
            {node["id"] for node in filtered["nodes"]},
            {"unlearned", "weak", "learning", "mastered"},
        )


if __name__ == "__main__":
    unittest.main()
