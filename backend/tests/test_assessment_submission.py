from __future__ import annotations

import unittest
from unittest.mock import patch

from app import state
from app.routers import assessments
from app.schemas import AssessmentSubmitRequest


class AssessmentSubmissionTest(unittest.TestCase):
    def test_state_lock_is_reentrant(self) -> None:
        with state.lock:
            with state.lock:
                self.assertTrue(True)

    def test_duplicate_paper_submission_reuses_saved_result(self) -> None:
        existing = {
            "id": "assessment_result_1",
            "userId": "student_1",
            "assessmentPaperId": "paper_1",
            "score": 42,
            "weakness": ["链表"],
            "errorReasons": ["关键步骤缺失"],
            "questionDetails": [],
            "rubricVersion": "rubric_v1",
            "pathAdjustment": {"before": "阶段一", "after": "链表补强"},
            "profileUpdateDrafts": [],
            "mistakesAdded": 1,
        }
        paper = {
            "id": "paper_1",
            "assessmentId": "paper_1",
            "userId": "student_1",
            "questions": [],
        }
        path = {"stages": []}

        with (
            patch.object(assessments, "load_record", return_value=paper),
            patch.object(assessments, "_find_existing_submission", return_value=existing),
            patch.object(state, "load_user_learning_path", return_value=path),
            patch.object(assessments, "evaluate_answers") as evaluate_answers,
        ):
            response = assessments.submit_assessment(
                AssessmentSubmitRequest(assessmentId="paper_1", answers={}),
                authorization="Bearer token-student_1",
            )

        self.assertEqual(response["data"]["assessmentId"], "assessment_result_1")
        self.assertEqual(response["data"]["score"], 42)
        self.assertTrue(response["data"]["idempotent"])
        evaluate_answers.assert_not_called()


if __name__ == "__main__":
    unittest.main()
