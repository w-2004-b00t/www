from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import mistake_generation_service as service


class MistakeGenerationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.record = {
            "id": "mistake_ai",
            "knowledge": "双链表",
            "stem": "说明双链表插入步骤。",
            "type": "short",
            "answer": "前驱指针 后继指针",
            "analysis": "维护相邻节点指针。",
            "wrongReason": "漏掉后继指针",
            "rubric": ["前驱指针", "后继指针"],
            "citations": [],
            "latestCorrection": {"missingKeywords": ["后继指针"]},
        }
        self.retrieval = {
            "coverage": "high",
            "items": [{
                "chunk_id": "chunk_1",
                "document_name": "课程讲义",
                "source_location": "双链表",
                "content": "双链表插入需要同时维护前驱指针和后继指针。",
                "score": 0.9,
            }],
        }

    def test_valid_ai_questions_are_marked_rag_llm(self) -> None:
        payload = {
            "questions": [
                {
                    "type": "short",
                    "stem": "在表头插入双链表节点时应更新哪些指针？",
                    "answer": "前驱指针 后继指针",
                    "analysis": "维护两侧链接。",
                    "rubric": ["前驱指针", "后继指针"],
                    "citationChunkIds": ["chunk_1"],
                },
                {
                    "type": "case",
                    "stem": "设计撤销功能时如何应用双链表完成节点插入？",
                    "answer": "前驱指针 后继指针",
                    "analysis": "在新情境中维护双向链接。",
                    "rubric": ["前驱指针", "后继指针"],
                    "citationChunkIds": ["chunk_1"],
                },
            ],
        }
        with (
            patch.object(service, "search_chunks", return_value=self.retrieval),
            patch.object(service, "llm_enabled", return_value=True),
            patch.object(service, "call_deepseek_json", return_value=payload),
        ):
            result = service.generate_mistake_variants(self.record)

        self.assertEqual(result["generationMode"], "rag_llm")
        self.assertEqual(len(result["questions"]), 2)
        self.assertTrue(all(item["citations"] for item in result["questions"]))

    def test_invalid_ai_result_retries_then_falls_back(self) -> None:
        with (
            patch.object(service, "search_chunks", return_value=self.retrieval),
            patch.object(service, "llm_enabled", return_value=True),
            patch.object(service, "call_deepseek_json", return_value={"questions": []}) as call,
        ):
            result = service.generate_mistake_variants(self.record)

        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["generationMode"], "rule_fallback")
        self.assertEqual(len(result["questions"]), 2)


if __name__ == "__main__":
    unittest.main()
