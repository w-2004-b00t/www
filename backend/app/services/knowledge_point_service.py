from __future__ import annotations

import re
from typing import Any, Iterable

from .course_progress_service import CATALOG_TEMPLATE_CHAPTERS


DETAILED_KNOWLEDGE_POINTS = [
    "最小生成树", "二叉排序树", "图的遍历", "快速排序", "归并排序", "循环队列",
    "循环双链表", "循环单链表", "双向链表", "双链表", "单链表", "循环链表",
    "顺序存储结构", "链式存储结构", "顺序表", "链表",
    "二叉树", "广义表", "字符串", "哈希表", "散列表", "最短路径",
    "深度优先搜索", "广度优先搜索", "拓扑排序", "关键路径",
    "插入排序", "选择排序", "冒泡排序", "堆排序",
    "顺序栈", "链栈", "顺序队列", "链队",
]

CHAPTER_KNOWLEDGE_POINTS = [
    str(value)
    for chapter in CATALOG_TEMPLATE_CHAPTERS
    for value in [chapter.get("chapterName"), *chapter.get("knowledgePoints", [])]
    if str(value or "").strip()
]

VALID_KNOWLEDGE_POINTS = list(dict.fromkeys([
    *DETAILED_KNOWLEDGE_POINTS,
    *CHAPTER_KNOWLEDGE_POINTS,
    "线性表", "栈", "队列", "串", "数组", "树", "图", "查找", "排序",
]))

NOISE_WORDS = {
    "ai", "a_i", "style", "visibility", "stylevisibility", "本讲完", "讲完",
    "第2章小结", "章节小结", "小结", "补充", "知识点", "课程资料", "数据结构",
    "课程资料待上传", "待上传", "true", "false", "none", "null",
}

RUBRIC_NOISE_WORDS = NOISE_WORDS | {
    "根据", "依据", "课程片段", "课程引用", "回答", "说明", "要求", "学习",
}


def normalize_knowledge_point(value: Any, *, context: str = "", fallback: str | None = None) -> str:
    candidate = _compact(value)
    if candidate and is_noise_token(candidate):
        return ""
    if candidate in VALID_KNOWLEDGE_POINTS:
        return _canonical_point(candidate)
    combined = f"{candidate} {_compact(context)}"
    matched = _match_known_point(combined)
    if matched:
        return matched
    if fallback:
        fallback_match = _match_known_point(_compact(fallback))
        if fallback_match:
            return fallback_match
    return ""


def sanitize_knowledge_points(
    values: Iterable[Any],
    *,
    context: str = "",
    fallback: str | None = None,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_knowledge_point(value, context=context)
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    if not result and fallback:
        normalized = normalize_knowledge_point(fallback)
        if normalized:
            result.append(normalized)
    return result


def clean_rubric_keywords(values: Iterable[Any], *, context: str = "") -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip(" ，。、;；")
        if not item or is_noise_token(item, rubric=True):
            continue
        if item not in seen:
            result.append(item)
            seen.add(item)
    if not any(point in item for item in result for point in VALID_KNOWLEDGE_POINTS):
        point = normalize_knowledge_point("", context=context)
        if point and point not in seen:
            result.insert(0, point)
    return result[:6]


def is_noise_token(value: Any, *, rubric: bool = False) -> bool:
    text = str(value or "").strip()
    compact = _compact(text).lower()
    if not compact:
        return True
    words = RUBRIC_NOISE_WORDS if rubric else NOISE_WORDS
    if compact in {word.lower() for word in words}:
        return True
    if re.fullmatch(r"(?:第)?\d+(?:/\d+)?(?:页|章|讲)?", compact):
        return True
    if re.fullmatch(r"[a-z_]{1,2}", compact):
        return True
    if compact.startswith("style.") or "stylevisibility" in compact:
        return True
    if re.fullmatch(r"第\d+章小结(?:\(\d+\)|（\d+）)?", compact):
        return True
    return False


def context_from_citation(citation: dict[str, Any] | None) -> str:
    item = citation or {}
    return " ".join(str(item.get(key) or "") for key in [
        "documentName", "sourceLocation", "contentPreview", "fullText",
    ])


def _match_known_point(text: str) -> str:
    if not text:
        return ""
    for point in VALID_KNOWLEDGE_POINTS:
        if point and point in text:
            return _canonical_point(point)
    return ""


def _canonical_point(point: str) -> str:
    aliases = {
        "双向链表": "双链表",
        "循环双链表": "循环链表",
        "循环单链表": "循环链表",
        "字符串": "串",
        "散列表": "哈希表",
    }
    return aliases.get(point, point)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip(" ，。、;；")
