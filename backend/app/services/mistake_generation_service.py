from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from uuid import uuid4

from .knowledge_service import search_chunks
from .llm_service import LLMUnavailable, call_deepseek_json, llm_enabled
from .tutor_service import citation_from_chunk


def _rubric(record: dict[str, Any]) -> list[str]:
    values = [str(item).strip() for item in record.get("rubric", []) if str(item).strip()]
    if values:
        return values[:4]
    answer = str(record.get("answer") or "").replace("，", " ").replace("、", " ")
    values = [item for item in answer.split() if len(item) >= 2]
    return values[:4] or [str(record.get("knowledge") or "核心概念")]


def _fallback_questions(record: dict[str, Any], reason: str) -> dict[str, Any]:
    knowledge = str(record.get("knowledge") or "课程资料")
    rubric = _rubric(record)
    citations = deepcopy(record.get("citations", []))
    questions = [
        {
            "id": f"verification_{uuid4().hex[:8]}",
            "type": (
                str(record.get("type"))
                if str(record.get("type")) in {"short", "calculation", "code", "case"}
                else "short"
            ),
            "knowledgePoint": knowledge,
            "stem": f"围绕「{knowledge}」重新完成一道同能力题：请说明核心概念和关键步骤。",
            "options": [],
            "answer": " ".join(rubric),
            "analysis": f"回答应覆盖：{'、'.join(rubric)}。",
            "rubric": rubric,
            "citations": citations,
        },
        {
            "id": f"verification_{uuid4().hex[:8]}",
            "type": "case",
            "knowledgePoint": knowledge,
            "stem": f"在一个新的实际情境中，你会如何应用「{knowledge}」？请说明选择依据和处理步骤。",
            "options": [],
            "answer": " ".join(rubric),
            "analysis": f"迁移回答应包含：{'、'.join(rubric)}，并说明如何应用。",
            "rubric": rubric,
            "citations": citations,
        },
    ]
    return {
        "questions": questions,
        "generationMode": "rule_fallback",
        "generationReason": reason,
    }


def _normalize_questions(
    raw: Any,
    record: dict[str, Any],
    citations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) < 2:
        return []
    allowed = {str(item.get("chunkId") or "") for item in citations}
    original_stem = str(record.get("stem") or "").strip()
    seen = {original_stem}
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw[:2]):
        if not isinstance(item, dict):
            return []
        stem = str(item.get("stem") or "").strip()
        answer = str(item.get("answer") or "").strip()
        analysis = str(item.get("analysis") or "").strip()
        rubric = [str(value).strip() for value in item.get("rubric", []) if str(value).strip()]
        if not stem or stem in seen or not answer or not analysis or not rubric:
            return []
        seen.add(stem)
        chunk_ids = [str(value) for value in item.get("citationChunkIds", []) if str(value) in allowed]
        question_citations = [entry for entry in citations if str(entry.get("chunkId") or "") in chunk_ids]
        question_type = str(item.get("type") or (record.get("type") if index == 0 else "case") or "short")
        options = [str(value) for value in item.get("options", []) if str(value).strip()]
        if question_type == "single" and len(options) < 2:
            question_type = "short"
            options = []
        normalized.append({
            "id": f"verification_{uuid4().hex[:8]}",
            "type": question_type,
            "knowledgePoint": str(record.get("knowledge") or "课程资料"),
            "stem": stem,
            "options": options,
            "answer": answer,
            "analysis": analysis,
            "rubric": rubric[:5],
            "citations": question_citations,
        })
    return normalized


def generate_mistake_variants(record: dict[str, Any]) -> dict[str, Any]:
    missing = (record.get("latestCorrection") or {}).get("missingKeywords", [])
    query = " ".join([
        str(record.get("knowledge") or ""),
        str(record.get("stem") or ""),
        " ".join(str(item) for item in missing),
    ]).strip()
    retrieval = search_chunks(query, top_k=5)
    citations = [citation_from_chunk(item) for item in retrieval.get("items", [])]
    if not llm_enabled():
        return _fallback_questions(record, "LLM 未启用，已使用可信规则模板。")
    if retrieval.get("coverage") == "none" or not citations:
        return _fallback_questions(record, "课程检索证据不足，已基于原题快照生成规则题。")
    payload = {
        "task": "generate_mistake_transfer_questions",
        "original": {
            "type": record.get("type"),
            "knowledge": record.get("knowledge"),
            "stem": record.get("stem"),
            "answer": record.get("answer"),
            "analysis": record.get("analysis"),
            "wrongReason": record.get("wrongReason"),
            "rubric": _rubric(record),
            "latestMissingKeywords": missing,
        },
        "citations": [
            {
                "chunkId": item.get("chunkId"),
                "documentName": item.get("documentName"),
                "sourceLocation": item.get("sourceLocation"),
                "contentPreview": item.get("contentPreview"),
            }
            for item in citations
        ],
        "requirements": [
            "严格生成两道题",
            "第一题保持原题核心能力和相近题型，但不得复述原题",
            "第二题改变情境，验证应用与知识迁移",
            "答案、解析和 rubric 必须完整",
            "citationChunkIds 只能使用输入引用",
        ],
        "expectedSchema": {
            "questions": [{
                "type": "single|short|calculation|code|case",
                "stem": "string",
                "options": ["string"],
                "answer": "string",
                "analysis": "string",
                "rubric": ["string"],
                "citationChunkIds": ["string"],
            }],
        },
    }
    system_prompt = (
        "你是高校数据结构课程错题变式生成 Agent。只依据原题快照和提供的真实课程引用出题。"
        "输出 JSON 对象，禁止复制原题，禁止编造引用。"
    )
    last_reason = ""
    for attempt in range(2):
        try:
            result = call_deepseek_json(
                system_prompt + (" 上次结果未通过结构校验，请严格修复。" if attempt else ""),
                json.dumps(payload, ensure_ascii=False),
                temperature=0.15,
                max_tokens=1800,
                timeout=45,
            )
            questions = _normalize_questions(result.get("questions"), record, citations)
            if questions:
                return {
                    "questions": questions,
                    "generationMode": "rag_llm",
                    "generationReason": "基于原错题、订正缺失点和真实课程引用生成。",
                }
            last_reason = "模型结果缺少两道完整且不重复的题目"
        except LLMUnavailable as exc:
            last_reason = exc.public_message
    return _fallback_questions(record, f"AI 生成未通过校验：{last_reason or '未知原因'}，已使用可信规则模板。")
