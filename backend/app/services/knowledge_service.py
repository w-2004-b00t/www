from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

from ..persistence import list_knowledge_chunks
from .document_parser import COURSE_ID
from .vector_service import query_knowledge_vectors


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    words = re.findall(r"[a-z0-9_]+", lowered)
    chars = [char for char in lowered if _is_cjk(char)]
    words.extend(_joined_cjk_segments(lowered))
    bigrams = ["".join(chars[index:index + 2]) for index in range(max(len(chars) - 1, 0))]
    trigrams = ["".join(chars[index:index + 3]) for index in range(max(len(chars) - 2, 0))]
    return words + bigrams + trigrams


def search_chunks(query: str, top_k: int = 5) -> dict[str, Any]:
    chunks = list_knowledge_chunks(course_id=COURSE_ID)

    expanded_terms = _expand_query_terms(query)
    expanded_query = " ".join([query, *expanded_terms])
    query_tokens = tokenize(expanded_query)
    vector_enabled = os.getenv("KNOWLEDGE_VECTOR_QUERY_ENABLE", "true").lower() in {"1", "true", "yes", "on"}
    vector_hits = query_knowledge_vectors(query, top_k=max(top_k * 4, 16)) if vector_enabled else []
    vector_score_by_chunk = {
        str((hit.get("metadata") or {}).get("chunkId") or hit.get("id", "").replace("knowledge::", "")): float(hit.get("score", 0.0))
        for hit in vector_hits
    }

    scored: list[dict[str, Any]] = []
    query_counter = Counter(query_tokens)
    for chunk in chunks:
        text = _chunk_search_text(chunk)
        if len(text.strip()) < 18:
            continue
        chunk_tokens = tokenize(text)
        chunk_counter = Counter(chunk_tokens)
        overlap = sum(min(count, chunk_counter[token]) for token, count in query_counter.items())
        phrase_bonus = 3.0 if query and query in text else 0.0
        phrase_bonus += sum(1.2 for term in expanded_terms if term and term in text)
        keyword_bonus = _keyword_bonus(query, chunk) + _data_structure_topic_bonus(query, chunk)
        source_type = chunk.get("sourceType")
        source_bonus = _source_type_bonus(str(source_type or ""))
        chapter_bonus = _chapter_relevance_bonus(query, chunk)
        focus_bonus = _topic_focus_bonus(query, chunk)
        mismatch_penalty = _topic_mismatch_penalty(query, chunk)
        length_bonus = 0.8 if len(str(chunk.get("content") or "")) >= 80 else 0.2
        lexical_raw = (overlap + phrase_bonus + keyword_bonus + source_bonus) / (max(len(set(chunk_tokens)), 1) ** 0.5)
        lexical_score = min(1.0, lexical_raw / 5)
        vector_score = vector_score_by_chunk.get(str(chunk.get("chunkId")), 0.0)
        score = min(
            0.99,
            0.46
            + lexical_score * 0.25
            + vector_score * 0.10
            + chapter_bonus * 0.08
            + focus_bonus * 0.13
            + length_bonus * 0.04,
        )
        if mismatch_penalty:
            score = max(0.0, score - mismatch_penalty)
        if _looks_like_code_chunk(chunk) and not any(term in query for term in ["代码", "实现", "实验", "程序", "C语言", "源码"]):
            score = max(0.0, score - 0.16)
        if source_type == "local_manifest":
            score = max(0.0, score - 0.06)
        if score <= 0.52 and lexical_raw <= 0 and vector_score <= 0 and chapter_bonus <= 0:
            continue
        scored.append(_format_chunk(chunk, score=round(score, 2), rerank_score=round(lexical_raw + vector_score + focus_bonus, 3)))

    scored.sort(key=lambda item: (
        item["score"],
        item["rerank_score"],
        _source_type_rank(str(item.get("sourceType") or item.get("source_type") or "")),
        len(str(item.get("content") or "")),
    ), reverse=True)
    items = _dedupe_chunks(scored, top_k)
    coverage = _coverage_for_items(items)
    missing = [] if coverage == "sufficient" else ["知识库命中不足", "建议教师补充课程原文或重新检索更具体的知识点"]
    return {
        "items": items,
        "coverage": coverage,
        "missing_knowledge": missing,
        "query_tokens": query_tokens,
        "retrieval_pipeline": [
            "SQLite 课程知识片段",
            "BGE-M3/本地向量兜底" if vector_enabled else "向量检索未启用",
            "关键词召回",
            "主题相关性和来源类型混合重排",
            "按文档章节和内容去重",
            "章节页码引用格式化",
        ],
    }


RUBRICS = {
    "q2": {
        "required_keywords": ["定义", "操作", "复杂度", "边界"],
        "error_reason": "概念解释缺少定义、操作过程、复杂度或边界条件中的关键表达。",
    },
    "q3": {
        "required_keywords": ["初始化", "核心操作", "输出"],
        "error_reason": "代码题没有体现初始化、核心操作和输出验证步骤。",
    },
}


def _chunk_search_text(chunk: dict[str, Any]) -> str:
    keywords = chunk.get("keywords") or []
    if isinstance(keywords, str):
        keyword_text = keywords
    else:
        keyword_text = " ".join(str(item) for item in keywords)
    return f"{chunk.get('documentName', '')} {chunk.get('section', '')} {keyword_text} {chunk.get('content', '')}"


def _expand_query_terms(query: str) -> list[str]:
    expansions = {
        "线性表": ["顺序表", "单链表", "链表", "双链表", "循环链表", "插入", "删除", "查找", "表长", "线性结构"],
        "顺序表": ["线性表", "数组", "顺序存储", "插入", "删除", "随机访问", "表长"],
        "链表": ["线性表", "单链表", "双链表", "循环链表", "指针", "头结点", "插入", "删除"],
        "栈": ["顺序栈", "链栈", "入栈", "出栈", "栈顶", "判空"],
        "队列": ["顺序队", "循环队列", "链队", "入队", "出队", "队头", "队尾"],
    }
    result: list[str] = []
    for key, terms in expansions.items():
        if key in query:
            result.extend(terms)
    return list(dict.fromkeys(result))


def _looks_like_code_chunk(chunk: dict[str, Any]) -> bool:
    text = _chunk_search_text(chunk)
    code_tokens = ["#include", "printf(", "return 0", "return OK", "typedef", "malloc(", "void ", "int main", "->", "};"]
    if chunk.get("sourceType") == "code_repository":
        return True
    if re.search(r"\.(c|cpp|h)\b", str(chunk.get("documentName") or ""), re.I):
        return True
    return sum(1 for token in code_tokens if token in text) >= 2


def _keyword_bonus(query: str, chunk: dict[str, Any]) -> float:
    keywords = chunk.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    return float(sum(1 for keyword in keywords if str(keyword) and str(keyword) in query))


def _source_type_bonus(source_type: str) -> float:
    if source_type in {"course_pdf", "teacher_courseware", "teacher_courseware_manifest"}:
        return 1.2
    if source_type in {"uploaded_document", "local_text"}:
        return 0.85
    if source_type == "code_repository":
        return 0.55
    if source_type == "local_manifest":
        return 0.2
    return 0.4


def _source_type_rank(source_type: str) -> int:
    ranks = {
        "course_pdf": 6,
        "teacher_courseware": 6,
        "teacher_courseware_manifest": 5,
        "uploaded_document": 5,
        "local_text": 4,
        "code_repository": 3,
        "local_manifest": 1,
    }
    return ranks.get(source_type, 2)


def _chapter_relevance_bonus(query: str, chunk: dict[str, Any]) -> float:
    haystack = f"{chunk.get('documentName', '')} {chunk.get('section', '')} {chunk.get('keywords', '')}"
    if query and query in haystack:
        return 1.0
    query_terms = [term for term in _data_structure_terms() if term in query]
    if not query_terms:
        return 0.0
    return min(1.0, sum(1 for term in query_terms if term in haystack) / max(len(query_terms), 1))


def _topic_focus_terms(query: str) -> list[str]:
    focus_map = {
        "线性表": ["线性表", "顺序表", "链表", "单链表", "双链表", "循环链表"],
        "顺序表": ["线性表", "顺序表", "顺序存储"],
        "链表": ["线性表", "链表", "单链表", "双链表", "循环链表"],
        "栈": ["栈", "顺序栈", "链栈"],
        "队列": ["队列", "循环队列", "链队", "顺序队"],
    }
    terms: list[str] = []
    for key, values in focus_map.items():
        if key in query:
            terms.extend(values)
    return list(dict.fromkeys(terms))


def _topic_focus_bonus(query: str, chunk: dict[str, Any]) -> float:
    terms = _topic_focus_terms(query)
    if not terms:
        return 0.0
    title_area = f"{chunk.get('documentName', '')} {chunk.get('section', '')} {chunk.get('keywords', '')}"
    full_text = _chunk_search_text(chunk)
    title_hits = sum(1 for term in terms if term in title_area)
    content_hits = sum(1 for term in terms if term in full_text)
    return min(1.0, title_hits * 0.55 + content_hits * 0.18)


def _topic_mismatch_penalty(query: str, chunk: dict[str, Any]) -> float:
    terms = _topic_focus_terms(query)
    if not terms:
        return 0.0
    text = _chunk_search_text(chunk)
    if any(term in text for term in terms):
        return 0.0
    return 0.18


def _data_structure_topic_bonus(query: str, chunk: dict[str, Any]) -> float:
    haystack = _chunk_search_text(chunk)
    score = 0.0
    for term in _data_structure_terms():
        if term.lower() in query.lower() and term.lower() in haystack.lower():
            score += 1.2
    if chunk.get("sourceType") == "code_repository" and any(term in query for term in ["代码", "实现", "实验", "程序"]):
        score += 1.0
    return score


def _data_structure_terms() -> list[str]:
    return [
        "数据结构", "线性表", "顺序表", "链表", "单链表", "双链表", "栈", "队列", "循环队列", "串", "KMP",
        "数组", "广义表", "递归", "树", "二叉树", "遍历", "哈夫曼树", "并查集", "图",
        "邻接矩阵", "邻接表", "最小生成树", "Prim", "Kruskal", "最短路径", "Dijkstra", "Floyd", "拓扑排序",
        "关键路径", "查找", "二叉排序树", "平衡二叉树", "B树", "B+树", "哈希", "排序",
        "插入排序", "冒泡排序", "快速排序", "选择排序", "堆排序", "归并排序", "基数排序",
        "时间复杂度", "空间复杂度", "代码实现",
    ]


def _dedupe_chunks(scored: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_documents: set[tuple[str, str, int]] = set()
    seen_content: set[str] = set()
    doc_counts: Counter[str] = Counter()

    def try_add(item: dict[str, Any], *, enforce_doc_limit: bool) -> bool:
        content = re.sub(r"\s+", "", str(item.get("content") or ""))
        content_key = content[:120]
        doc_name = str(item.get("document_name") or item.get("documentName") or "")
        doc_key = (
            doc_name,
            str(item.get("section") or ""),
            int(item.get("page") or 1),
        )
        if enforce_doc_limit and doc_counts[doc_name] >= 2:
            return False
        if content_key and content_key in seen_content:
            return False
        if doc_key in seen_documents and len(items) >= max(2, top_k // 2):
            return False
        seen_content.add(content_key)
        seen_documents.add(doc_key)
        doc_counts[doc_name] += 1
        items.append(item)
        return True

    for item in scored:
        try_add(item, enforce_doc_limit=True)
        if len(items) >= top_k:
            break
    if len(items) < top_k:
        for item in scored:
            if item in items:
                continue
            try_add(item, enforce_doc_limit=False)
            if len(items) >= top_k:
                break
    return items


def _coverage_for_items(items: list[dict[str, Any]]) -> str:
    if not items:
        return "none"
    strong = [item for item in items if float(item.get("score", 0.0)) >= 0.56]
    if len(strong) >= 2 or float(items[0].get("score", 0.0)) >= 0.62:
        return "sufficient"
    return "low"


def _format_chunk(chunk: dict[str, Any], *, score: float, rerank_score: float) -> dict[str, Any]:
    page = chunk.get("page") or 1
    section = chunk.get("section") or "课程资料"
    chunk_id = chunk.get("chunkId") or chunk.get("chunk_id")
    return {
        "chunk_id": chunk_id,
        "chunkId": chunk_id,
        "content": chunk.get("content", ""),
        "contentPreview": _preview(chunk.get("content", "")),
        "score": score,
        "rerank_score": rerank_score,
        "page": page,
        "source_location": f"{section} · 第 {page} 页",
        "document_name": chunk.get("documentName", "数据结构课程课程资料"),
        "documentName": chunk.get("documentName", "数据结构课程课程资料"),
        "section": section,
        "keywords": chunk.get("keywords") or [],
        "source_type": chunk.get("sourceType", "course_material"),
        "sourceType": chunk.get("sourceType", "course_material"),
    }


def _preview(text: str, limit: int = 96) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact if len(compact) <= limit else f"{compact[:limit]}..."


def _is_cjk(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _joined_cjk_segments(text: str) -> list[str]:
    segments: list[str] = []
    current = ""
    for char in text:
        if _is_cjk(char):
            current += char
        else:
            if len(current) >= 2:
                segments.append(current)
            current = ""
    if len(current) >= 2:
        segments.append(current)
    return segments
