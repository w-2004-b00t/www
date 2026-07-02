from __future__ import annotations

import re
from typing import Any

from ..persistence import list_knowledge_chunks, list_knowledge_documents
from .knowledge_service import search_chunks

CATALOG_TEMPLATE_CHAPTERS = [
    {"chapterId": "chapter_linear_list", "chapterName": "线性表", "order": 1, "knowledgePoints": ["线性表", "顺序表", "链表"], "source": "catalog_template"},
    {"chapterId": "chapter_stack_queue", "chapterName": "栈和队列", "order": 2, "knowledgePoints": ["栈", "队列", "循环队列"], "source": "catalog_template"},
    {"chapterId": "chapter_string_array", "chapterName": "串、数组和广义表", "order": 3, "knowledgePoints": ["串", "数组", "广义表"], "source": "catalog_template"},
    {"chapterId": "chapter_tree", "chapterName": "树和二叉树", "order": 4, "knowledgePoints": ["树", "二叉树", "遍历"], "source": "catalog_template"},
    {"chapterId": "chapter_graph", "chapterName": "图", "order": 5, "knowledgePoints": ["图", "图的遍历", "最短路径", "最小生成树"], "source": "catalog_template"},
    {"chapterId": "chapter_search", "chapterName": "查找", "order": 6, "knowledgePoints": ["查找", "二叉排序树", "哈希表"], "source": "catalog_template"},
    {"chapterId": "chapter_sort", "chapterName": "排序", "order": 7, "knowledgePoints": ["排序", "快速排序", "堆排序", "归并排序"], "source": "catalog_template"},
]


def list_course_chapters() -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    known_names: set[str] = set()
    parsed: dict[int, str] = {}
    for document in list_knowledge_documents():
        name = str(document.get("name") or "")
        match = re.search(r"第\s*(\d+)\s*章\s*([^第讲\\/\-.（(]+)", name)
        if not match:
            continue
        chapter_order = int(match.group(1))
        chapter_name = _normalize_chapter_name(match.group(2))
        if chapter_name and chapter_name not in known_names:
            parsed[chapter_order] = chapter_name
    for chapter_order, chapter_name in sorted(parsed.items()):
        chapters.append({
            "chapterId": f"chapter_doc_{chapter_order}",
            "chapterName": chapter_name,
            "order": chapter_order,
            "knowledgePoints": [chapter_name],
            "status": "pending",
            "source": "knowledge_document",
        })
    if not chapters and list_knowledge_chunks():
        chapters = [dict(item, status="pending") for item in CATALOG_TEMPLATE_CHAPTERS]
    chapters.sort(key=lambda item: int(item.get("order", 0)))
    return chapters


def chapter_for_topic(topic: str) -> dict[str, Any]:
    text = str(topic or "")
    chapters = list_course_chapters()
    if not chapters:
        return {}
    for chapter in chapters:
        if chapter["chapterName"] in text or any(point and point in text for point in chapter.get("knowledgePoints", [])):
            return chapter
    return chapters[0]


def next_topic_for_progress(progress: dict[str, Any], learning_path: dict[str, Any] | None = None) -> dict[str, Any]:
    chapters = annotate_chapters_with_progress(progress)
    if not chapters:
        return _blocked_payload("知识库中暂无可用于推荐的真实课程章节。请先上传或导入课程资料。")
    active_stage = _active_stage(learning_path or {})
    if active_stage:
        if not _stage_has_evidence(active_stage):
            return _blocked_payload("当前学习阶段缺少课程引用依据，暂不能推荐下一知识点。请先生成带引用的正式学习资料。")
        chapter_id = str(active_stage.get("chapterId") or active_stage.get("metadata", {}).get("chapterId") or "")
        if chapter_id:
            stage_chapter = next((chapter for chapter in chapters if chapter["chapterId"] == chapter_id), None)
            if stage_chapter and stage_chapter.get("status") != "mastered":
                return _topic_payload(stage_chapter, "继续当前未掌握章节。", source="active_stage")
        return _stage_topic_payload(active_stage, "继续当前学习路径中的真实阶段。")
    next_chapter = next((chapter for chapter in chapters if chapter.get("status") != "mastered"), chapters[-1])
    reason = "当前章节已掌握，已推进到下一章节。" if any(chapter.get("status") == "mastered" for chapter in chapters) else "从第一个未掌握章节开始学习。"
    return _topic_payload(next_chapter, reason, source=str(next_chapter.get("source") or "knowledge_document"))


def annotate_chapters_with_progress(progress: dict[str, Any]) -> list[dict[str, Any]]:
    mastered_chapter_ids = set(progress.get("masteredChapterIds", []) or [])
    mastered_points = {str(item) for item in progress.get("masteredKnowledgePoints", []) or []}
    chapters = []
    for chapter in list_course_chapters():
        points = set(chapter.get("knowledgePoints", []))
        mastered = chapter["chapterId"] in mastered_chapter_ids or chapter["chapterName"] in mastered_points or bool(points and points.issubset(mastered_points))
        chapters.append({**chapter, "status": "mastered" if mastered else "pending"})
    first_pending_seen = False
    for chapter in chapters:
        if chapter["status"] == "pending" and not first_pending_seen:
            chapter["status"] = "active"
            first_pending_seen = True
    return chapters


def _topic_payload(chapter: dict[str, Any], reason: str, *, source: str | None = None) -> dict[str, Any]:
    evidence = _evidence_for_topic(chapter["chapterName"])
    if not evidence:
        return _blocked_payload(f"章节「{chapter['chapterName']}」暂未命中真实课程引用，不能作为正式推荐。")
    return {
        "chapterId": chapter["chapterId"],
        "chapterName": chapter["chapterName"],
        "chapterOrder": chapter["order"],
        "topic": chapter["chapterName"],
        "knowledgePoints": chapter.get("knowledgePoints", []),
        "reason": reason,
        "status": chapter.get("status", "active"),
        "source": source or chapter.get("source") or "knowledge_document",
        "evidence": evidence,
        "blocked": False,
    }


def _active_stage(learning_path: dict[str, Any]) -> dict[str, Any] | None:
    return next((stage for stage in learning_path.get("stages", []) if stage.get("status") == "active"), None)


def _stage_topic_payload(stage: dict[str, Any], reason: str) -> dict[str, Any]:
    topic = str(stage.get("chapterName") or stage.get("name") or "").strip()
    points = [str(item) for item in stage.get("knowledgePoints", []) if str(item or "").strip()]
    evidence = _stage_evidence(stage) or _evidence_for_topic(topic or (points[0] if points else ""))
    if not evidence:
        return _blocked_payload("当前阶段未命中真实课程引用，不能作为正式推荐。")
    return {
        "chapterId": str(stage.get("chapterId") or stage.get("id") or ""),
        "chapterName": topic or "当前学习阶段",
        "topic": topic or (points[0] if points else "当前学习阶段"),
        "knowledgePoints": points,
        "reason": reason,
        "status": "active",
        "source": str(stage.get("source") or "learning_path"),
        "evidence": evidence,
        "blocked": False,
    }


def _blocked_payload(reason: str) -> dict[str, Any]:
    return {
        "chapterId": "",
        "chapterName": "",
        "topic": "",
        "knowledgePoints": [],
        "reason": reason,
        "status": "blocked",
        "source": "real_data_gate",
        "evidence": [],
        "blocked": True,
        "blockingReason": reason,
    }


def _stage_has_evidence(stage: dict[str, Any]) -> bool:
    return bool(stage.get("citationChunkIds") or stage.get("resources") or _stage_evidence(stage))


def _stage_evidence(stage: dict[str, Any]) -> list[dict[str, Any]]:
    chunk_ids = {str(item) for item in stage.get("citationChunkIds", []) if str(item or "").strip()}
    if not chunk_ids:
        return []
    evidence = []
    for chunk in list_knowledge_chunks():
        chunk_id = str(chunk.get("chunk_id") or chunk.get("chunkId") or "")
        if chunk_id in chunk_ids:
            evidence.append(_citation_from_chunk(chunk))
    return evidence[:3]


def _evidence_for_topic(topic: str) -> list[dict[str, Any]]:
    text = str(topic or "").strip()
    if not text:
        return []
    result = search_chunks(text, 3)
    if result.get("coverage") == "none":
        return []
    return [_citation_from_chunk(item) for item in result.get("items", [])[:3]]


def _citation_from_chunk(item: dict[str, Any]) -> dict[str, Any]:
    chunk_id = item.get("chunk_id") or item.get("chunkId")
    return {
        "chunkId": chunk_id,
        "documentId": item.get("document_id") or item.get("documentId") or f"doc_{chunk_id}",
        "documentName": item.get("document_name") or item.get("documentName") or item.get("title") or "课程资料",
        "sourceLocation": item.get("source_location") or item.get("sourceLocation") or item.get("section", ""),
        "page": item.get("page", 1),
        "similarity": item.get("score", item.get("similarity", 0)),
        "contentPreview": str(item.get("content") or item.get("contentPreview") or "")[:180],
    }


def _normalize_chapter_name(value: str) -> str:
    text = re.sub(r"\s+", "", value)
    text = re.sub(r"[（(].*$", "", text)
    return text.strip("-_ ")
