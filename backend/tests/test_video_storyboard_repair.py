from __future__ import annotations

import sys
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.llm_service import LLMUnavailable
from app.services.resource_service import (
    _normalize_video_scene,
    _repair_video_scene,
    _validate_llm_video_scene,
    _validate_video_teaching_coverage,
)


class VideoStoryboardRepairTest(unittest.TestCase):
    def setUp(self) -> None:
        self.citations = [
            {
                "chunkId": "chunk_linear_1",
                "documentName": "数据结构讲义",
                "sourceLocation": "线性表",
                "contentPreview": "线性表包含顺序表、链表、插入删除操作和复杂度分析。",
            }
        ]

    def test_repairs_missing_formula_steps_visual_model_and_example_data(self) -> None:
        scene = {
            "title": "跟踪插入操作",
            "screenText": "线性表插入要观察操作前后的状态变化。",
            "sourceChunkIds": ["chunk_linear_1"],
        }

        repaired = _repair_video_scene(scene, 2, self.citations, topic="线性表")
        _validate_llm_video_scene(repaired, 2, self.citations, topic="线性表")
        normalized = _normalize_video_scene(repaired, 2, self.citations)

        self.assertTrue(normalized["formulaOrComplexity"])
        self.assertGreaterEqual(len(normalized["operationSteps"]), 2)
        self.assertEqual(normalized["visualModel"]["type"], "insert_shift")
        self.assertIsInstance(normalized["exampleData"].get("sequence"), list)
        self.assertEqual(normalized["citationChunkIds"], ["chunk_linear_1"])

    def test_repaired_linear_list_five_scene_storyboard_passes_coverage(self) -> None:
        raw_scenes = [
            {"title": "引入线性表", "screenText": "线性表要关注定义、结构和复杂度。", "citationIds": ["chunk_linear_1"]},
            {"title": "比较顺序表和链表", "screenText": "顺序表连续存储，链表用指针连接。", "citationIds": ["chunk_linear_1"]},
            {"title": "跟踪插入", "screenText": "插入 X 时观察元素移动和指针调整。", "citationIds": ["chunk_linear_1"]},
            {"title": "分析复杂度", "screenText": "把访问、查找、插入、删除转换成复杂度。", "citationIds": ["chunk_linear_1"]},
            {"title": "完成练习", "screenText": "用一道插入练习检查结构变化。", "citationIds": ["chunk_linear_1"]},
        ]

        scenes = [
            _normalize_video_scene(_repair_video_scene(raw, index, self.citations, topic="线性表"), index, self.citations)
            for index, raw in enumerate(raw_scenes)
        ]

        _validate_video_teaching_coverage("线性表", scenes)
        self.assertEqual(len(scenes), 5)

    def test_rejects_storyboard_meta_text(self) -> None:
        scene = {
            "title": "展示当前分镜",
            "screenText": "线性表插入操作。",
            "citationChunkIds": ["chunk_linear_1"],
        }

        with self.assertRaises(LLMUnavailable):
            _repair_video_scene(scene, 0, self.citations, topic="线性表")

    def test_rejects_empty_unrepairable_scene(self) -> None:
        with self.assertRaises(LLMUnavailable):
            _repair_video_scene({}, 0, self.citations, topic="线性表")


if __name__ == "__main__":
    unittest.main()
