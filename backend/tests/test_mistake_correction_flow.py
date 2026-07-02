from __future__ import annotations

import unittest
from copy import deepcopy
from unittest.mock import patch

from fastapi import HTTPException

from app import state
from app.routers import tutor
from app.schemas import MistakeCorrectionRequest, MistakeStatusRequest, MistakeVerificationRequest


class MistakeCorrectionFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.mistake = {
            "id": "mistake_flow",
            "userId": "student_flow",
            "knowledge": "双链表",
            "stem": "说明双链表插入节点的关键步骤。",
            "type": "short",
            "options": [],
            "userAnswer": "直接插入",
            "answer": "前驱指针 后继指针",
            "analysis": "需要同时维护新节点与相邻节点的前驱、后继指针。",
            "rubric": ["前驱指针", "后继指针"],
            "citations": [],
            "wrongReason": "没有说明指针更新。",
            "fixTask": "重做原题",
            "status": "待订正",
            "correctionAttempts": [],
            "verificationQuestions": [],
            "verificationAttempts": [],
            "masteryEvidence": [],
            "createdAt": "2026-06-25",
        }
        self.path = {"stages": [{"id": "next", "name": "后续任务", "status": "active"}]}
        self.patches = [
            patch.object(tutor, "user_id_from_authorization", return_value="student_flow"),
            patch.object(tutor, "get_mistake", side_effect=self._get_mistake),
            patch.object(tutor, "update_mistake", side_effect=self._update_mistake),
            patch.object(tutor, "list_mistakes", side_effect=lambda _user_id: [deepcopy(self.mistake)]),
            patch.object(
                tutor,
                "generate_mistake_variants",
                side_effect=self._generate_variants,
            ),
            patch.object(state, "load_user_learning_path", side_effect=lambda _user_id: deepcopy(self.path)),
            patch.object(state, "save_user_learning_path", side_effect=self._save_path),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()

    def _save_path(self, _user_id: str, path: dict) -> None:
        self.path = deepcopy(path)

    def _get_mistake(self, mistake_id: str, user_id: str) -> dict | None:
        if self.mistake["id"] == mistake_id and self.mistake["userId"] == user_id:
            return deepcopy(self.mistake)
        return None

    def _update_mistake(self, record: dict, expected_version: int | None = None) -> dict:
        current_version = int(self.mistake.get("version") or 1)
        if expected_version is not None and expected_version != current_version:
            raise tutor.MistakeVersionConflict(deepcopy(self.mistake))
        self.mistake = deepcopy(record)
        self.mistake["version"] = current_version + 1
        return deepcopy(self.mistake)

    def _generate_variants(self, record: dict) -> dict:
        rubric = record["rubric"]
        return {
            "generationMode": "rule_fallback",
            "generationReason": "测试规则题",
            "questions": [
                {
                    "id": "variant_1",
                    "type": "short",
                    "knowledgePoint": record["knowledge"],
                    "stem": "说明双链表指针更新。",
                    "answer": " ".join(rubric),
                    "analysis": "维护指针。",
                    "rubric": rubric,
                    "citations": [],
                },
                {
                    "id": "variant_2",
                    "type": "case",
                    "knowledgePoint": record["knowledge"],
                    "stem": "在新情境中应用双链表。",
                    "answer": " ".join(rubric),
                    "analysis": "迁移应用。",
                    "rubric": rubric,
                    "citations": [],
                },
            ],
        }

    def test_original_question_must_pass_before_similar_questions(self) -> None:
        with self.assertRaises(HTTPException) as context:
            tutor.generate_similar_mistake(
                "mistake_flow",
                tutor.MistakeSimilarRequest(),
                authorization="Bearer ignored",
            )
        self.assertEqual(context.exception.status_code, 409)

        response = tutor.submit_mistake_correction(
            "mistake_flow",
            MistakeCorrectionRequest(answer="只更新一个指针"),
            authorization="Bearer ignored",
        )

        self.assertEqual(response["data"]["mistake"]["status"], "订正中")
        self.assertLess(response["data"]["result"]["score"], 70)
        self.assertTrue(any(stage.get("mistakeId") == "mistake_flow" for stage in self.path["stages"]))

    def test_passing_correction_and_variants_marks_mastered(self) -> None:
        corrected = tutor.submit_mistake_correction(
            "mistake_flow",
            MistakeCorrectionRequest(answer="双链表插入时同时更新前驱指针和后继指针"),
            authorization="Bearer ignored",
        )
        self.assertEqual(corrected["data"]["mistake"]["status"], "待验证")

        generated = tutor.generate_similar_mistake(
            "mistake_flow",
            tutor.MistakeSimilarRequest(expectedVersion=corrected["data"]["mistake"]["version"]),
            authorization="Bearer ignored",
        )
        questions = generated["data"]["questions"]
        self.assertEqual(len(questions), 2)

        answers = {
            question["id"]: "解决双链表新条件时仍要同时维护前驱指针和后继指针"
            for question in questions
        }
        verified = tutor.submit_mistake_verification(
            "mistake_flow",
            MistakeVerificationRequest(answers=answers),
            authorization="Bearer ignored",
        )

        self.assertTrue(verified["data"]["passed"])
        self.assertEqual(verified["data"]["mistake"]["status"], "已掌握")
        self.assertGreaterEqual(len(verified["data"]["mistake"]["masteryEvidence"]), 2)

    def test_manual_status_change_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as context:
            tutor.update_mistake_status(
                "mistake_flow",
                MistakeStatusRequest(status="已掌握"),
                authorization="Bearer ignored",
            )
        self.assertEqual(context.exception.status_code, 409)

    def test_legacy_mistake_is_normalized_for_correction(self) -> None:
        self.mistake = {
            "id": "legacy",
            "userId": "student_flow",
            "knowledge": "栈",
            "stem": "什么是栈？",
            "wrongReason": "概念不清",
            "fixTask": "复盘",
            "status": "需补强",
        }

        listed = tutor.list_tutor_mistakes(authorization="Bearer ignored")["data"][0]

        self.assertEqual(listed["status"], "待订正")
        self.assertEqual(listed["type"], "short")
        self.assertTrue(listed["rubric"])
        self.assertEqual(listed["verificationQuestions"], [])


if __name__ == "__main__":
    unittest.main()
