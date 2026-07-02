from __future__ import annotations

import unittest

from app.services.assessment_service import _keywords_for_citation
from app.services.knowledge_point_repair import _repair_payload
from app.services.knowledge_point_service import clean_rubric_keywords, sanitize_knowledge_points
from app.services.path_planner_service import build_remedial_stage


class KnowledgePointSanitizationTest(unittest.TestCase):
    def test_ppt_noise_resolves_to_double_linked_list(self) -> None:
        citation = {
            "documentName": "第2章线性表第7讲-双链表.pptx",
            "sourceLocation": "双链表 · 第9页",
            "contentPreview": "∧ L ∧ ai s 插入 9/17 style.visibilitystyle.visibility",
            "keywords": [],
        }
        points = _keywords_for_citation(citation, "线性表")
        self.assertEqual(points[0], "双链表")
        self.assertNotIn("ai", points)

    def test_noise_only_weakness_is_removed(self) -> None:
        self.assertEqual(
            sanitize_knowledge_points(["ai", "本讲完", "第2章小结", "补充", "17", "style"]),
            [],
        )

    def test_mixed_weakness_keeps_valid_unique_points(self) -> None:
        self.assertEqual(
            sanitize_knowledge_points(["ai", "链表", "双链表", "链表", "本讲完"]),
            ["链表", "双链表"],
        )

    def test_rubric_drops_layout_noise(self) -> None:
        rubric = clean_rubric_keywords(
            ["ai", "插入", "17", "style", "双链表"],
            context="第2章线性表第7讲 双链表",
        )
        self.assertEqual(rubric, ["插入", "双链表"])

    def test_valid_weakness_names_remedial_stage(self) -> None:
        stage = build_remedial_stage(
            user_id="test",
            weakness=["ai", "双链表"],
            suggestion="完成双链表练习",
            score=20,
            error_rate=80,
            error_reasons=[],
            existing_path={"stages": [{"name": "线性表资源学习任务", "chapterName": "线性表"}]},
        )
        self.assertEqual(stage["name"], "双链表补强任务")
        self.assertEqual(stage["knowledgePoints"], ["双链表"])

    def test_history_repair_is_idempotent(self) -> None:
        payload = {
            "stages": [{
                "id": "remedial_1",
                "name": "ai补强任务",
                "source": "assessment",
                "chapterName": "线性表",
                "knowledgePoints": ["ai", "线性表"],
            }],
            "adjustmentHistory": [{
                "before": "ai补强任务",
                "after": "ai补强任务",
                "reason": "已插入「ai补强任务」。",
                "beforePath": ["ai补强任务"],
                "afterPath": ["ai补强任务"],
            }],
        }
        repaired = _repair_payload(payload)
        self.assertEqual(repaired["stages"][0]["name"], "线性表补强任务")
        self.assertEqual(_repair_payload(repaired), repaired)

    def test_orphan_history_uses_first_valid_weakness(self) -> None:
        payload = {
            "stages": [{"name": "线性表资源学习任务", "knowledgePoints": ["线性表"]}],
            "adjustmentHistory": [{
                "before": "ai补强任务",
                "after": "ai补强任务",
                "reason": "学习评估 Agent 识别薄弱点：ai, 本讲完, 栈, 线性表，已插入「ai补强任务」。",
                "beforePath": ["ai补强任务"],
                "afterPath": ["ai补强任务"],
            }],
        }
        repaired = _repair_payload(payload)
        history = repaired["adjustmentHistory"][0]
        self.assertEqual(history["after"], "栈补强任务")
        self.assertNotIn("ai", history["reason"].lower())
        self.assertNotIn("本讲完", history["reason"])


if __name__ == "__main__":
    unittest.main()
