from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .. import state
from ..demo_data import now_text
from .knowledge_service import search_chunks
from .llm_service import (
    LLMJsonError,
    LLMUnavailable,
    call_deepseek_json,
    llm_enabled,
    llm_model_name,
    stream_deepseek_text,
)
from .profile_update_service import build_profile_context, log_tutor_question


GENERATION_INSUFFICIENT = "insufficient_evidence"
GENERATION_LLM_UNAVAILABLE = "llm_unavailable"
GENERATION_RAG_LLM = "rag_llm"


def citation_from_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "documentId": chunk.get("document_id") or chunk.get("documentId") or chunk.get("chunk_id", "course_doc"),
        "documentName": chunk.get("document_name") or chunk.get("documentName") or "数据结构课程资料",
        "sourceLocation": chunk.get("source_location") or chunk.get("sourceLocation") or "",
        "chunkId": chunk.get("chunk_id") or chunk.get("chunkId") or "",
        "contentPreview": str(chunk.get("content") or "")[:160],
        "page": chunk.get("page"),
        "similarity": chunk.get("score"),
        "fullText": chunk.get("content", ""),
    }


def parse_tutor_message(message: str) -> tuple[str, str]:
    mode = "问知识点"
    text = (message or "").strip()
    if text.startswith("[") and "]" in text:
        raw_mode, raw_text = text[1:].split("]", 1)
        mode = raw_mode.strip() or mode
        text = raw_text.strip()
    return mode, text


def infer_knowledge_point(question: str) -> str:
    if "线性表" in question:
        return "线性表"
    if "栈" in question or "队列" in question:
        return "栈和队列"
    if "树" in question or "二叉树" in question:
        return "树和二叉树"
    if "图" in question:
        return "图"
    if "排序" in question or "查找" in question:
        return "查找与排序"
    if "代码" in question or "实验" in question:
        return "数据结构代码实践"
    return question[:24] or "待通过真实课程资料确认"


def answer_tutor_question(user_id: str, message: str, course_id: str | None = None) -> dict[str, Any]:
    mode, question = parse_tutor_message(message)
    retrieval = search_chunks(question, top_k=4)
    citations = [citation_from_chunk(item) for item in retrieval.get("items", [])]
    profile_update_draft = log_tutor_question(user_id, question, infer_knowledge_point(question))

    if retrieval.get("coverage") == "none" or not citations:
        return _insufficient_evidence_response(mode, question, retrieval, profile_update_draft)

    if not llm_enabled():
        return _llm_unavailable_response(mode, question, citations, retrieval, profile_update_draft, "LLM_ENABLE is not true")

    profile_context = build_profile_context(user_id, current_message=question)
    learning_path = state.load_user_learning_path(user_id)
    prompt_payload = _build_prompt_payload(mode, question, course_id, citations, retrieval, profile_context, learning_path)
    try:
        generated = call_deepseek_json(
            _system_prompt(),
            json.dumps(prompt_payload, ensure_ascii=False),
            temperature=0.18,
            max_tokens=3200,
            timeout=35,
        )
    except LLMUnavailable as exc:
        return _llm_unavailable_response(mode, question, citations, retrieval, profile_update_draft, str(exc))

    return _normalize_llm_response(
        mode=mode,
        question=question,
        citations=citations,
        retrieval=retrieval,
        profile_update_draft=profile_update_draft,
        generated=generated,
    )


def prepare_tutor_stream(
    user_id: str,
    message: str,
    course_id: str | None = None,
) -> dict[str, Any]:
    mode, question = parse_tutor_message(message)
    retrieval = search_chunks(question, top_k=4)
    citations = [citation_from_chunk(item) for item in retrieval.get("items", [])]
    profile_update_draft = log_tutor_question(user_id, question, infer_knowledge_point(question))
    if retrieval.get("coverage") == "none" or not citations:
        raise LLMUnavailable(
            "No relevant course citations were found",
            code="insufficient_evidence",
            retryable=False,
            public_message="课程知识库没有检索到足够相关的内容，请补充题目原文、代码或具体章节。",
        )
    if not llm_enabled():
        raise LLMUnavailable(
            "LLM_ENABLE is not true",
            code="llm_disabled",
            retryable=False,
            public_message="智能辅导模型尚未启用，请联系管理员检查配置。",
        )
    profile_context = build_profile_context(user_id, current_message=question)
    learning_path = state.load_user_learning_path(user_id)
    prompt_payload = _build_text_prompt_payload(
        mode,
        question,
        course_id,
        citations,
        retrieval,
        profile_context,
        learning_path,
    )
    return {
        "mode": mode,
        "question": question,
        "retrieval": retrieval,
        "citations": citations,
        "profileUpdateDraft": profile_update_draft,
        "systemPrompt": _text_answer_system_prompt(),
        "userPrompt": json.dumps(prompt_payload, ensure_ascii=False),
    }


def stream_tutor_answer(context: dict[str, Any]):
    yield from stream_deepseek_text(
        str(context["systemPrompt"]),
        str(context["userPrompt"]),
        temperature=0.18,
        max_tokens=1800,
        timeout=60,
    )


def tutor_stream_result(context: dict[str, Any], answer: str, request_id: str) -> dict[str, Any]:
    retrieval = context["retrieval"]
    citations = context["citations"]
    coverage = retrieval.get("coverage")
    confidence = 0.88 if coverage == "sufficient" else 0.74
    return {
        "answer": answer,
        "citations": citations,
        "allCitations": citations,
        "suggestedActions": [
            {"type": "note", "title": "保存本次讲解", "reason": "保留回答与课程引用，方便后续复习。"},
            {"type": "exercise", "title": "生成相似练习", "reason": "用练习检验是否真正掌握。"},
        ],
        "inferred": coverage != "sufficient",
        "inferredSections": [],
        "confidence": confidence,
        "confidenceReason": "根据课程资料命中范围与回答完整度综合评估。",
        "coverage": coverage,
        "generationMode": GENERATION_RAG_LLM,
        "profileUpdateDraft": context.get("profileUpdateDraft"),
        "llm": {"enabled": True, "model": llm_model_name(), "used": True},
        "requestId": request_id,
        "createdAt": now_text(),
    }


def generate_tutor_extra(
    user_id: str,
    *,
    message: str,
    answer: str,
    extra_type: str,
    course_id: str | None = None,
) -> dict[str, Any]:
    if extra_type not in {"diagram", "video"}:
        raise ValueError("type must be diagram or video")
    mode, question = parse_tutor_message(message)
    retrieval = search_chunks(question, top_k=4)
    citations = [citation_from_chunk(item) for item in retrieval.get("items", [])]
    if retrieval.get("coverage") == "none" or not citations:
        raise LLMUnavailable(
            "No relevant course citations were found for tutor extras",
            code="insufficient_evidence",
            retryable=False,
            public_message="课程资料不足，暂时无法生成该内容。",
        )
    payload = {
        "task": f"generate_tutor_{extra_type}",
        "courseId": course_id,
        "mode": mode,
        "question": question,
        "answerMarkdown": answer[:5000],
        "citations": _compact_citations(citations),
    }
    if extra_type == "diagram":
        payload["expectedSchema"] = {
            "title": "string",
            "diagramMarkdown": "# topic\\n## branch\\n### leaf",
            "diagramMermaid": "string",
            "citationsUsed": ["chunkId"],
        }
        generated = _call_tutor_extra_json(
            _diagram_system_prompt(),
            payload,
            initial_max_tokens=1800,
            retry_max_tokens=2800,
        )
        return {
            "type": "diagram",
            "diagram": {
                "title": str(generated.get("title") or f"{question[:28]}图解"),
                "markdown": _normalize_diagram_markdown(generated.get("diagramMarkdown"), question),
                "mermaid": str(generated.get("diagramMermaid") or "").strip(),
            },
            "citations": citations,
            "generationMode": GENERATION_RAG_LLM,
            "createdAt": now_text(),
        }

    payload["expectedSchema"] = {
        "title": "string",
        "videoScenes": [
            {
                "timeRange": "0:00-0:20",
                "title": "string",
                "screenText": "string",
                "voiceover": "string",
                "keyConcepts": ["string"],
                "citationChunkIds": ["chunkId"],
            }
        ],
    }
    generated = _call_tutor_extra_json(
        _video_system_prompt(),
        payload,
        initial_max_tokens=2600,
        retry_max_tokens=3600,
    )
    allowed_chunk_ids = {str(item.get("chunkId") or "") for item in citations}
    scenes = _normalize_video_scenes(generated.get("videoScenes"), allowed_chunk_ids, question)
    if not scenes:
        raise LLMUnavailable(
            "DeepSeek returned no usable video scenes",
            code="deepseek_invalid_video",
            retryable=True,
            public_message="视频讲解结构生成失败，请重新生成。",
        )
    return {
        "type": "video",
        "videoScript": {
            "title": str(generated.get("title") or f"{question[:28]}短视频讲解"),
            "script": _video_script_markdown(scenes),
            "scenes": scenes,
        },
        "citations": citations,
        "generationMode": GENERATION_RAG_LLM,
        "createdAt": now_text(),
    }


def _call_tutor_extra_json(
    system_prompt: str,
    payload: dict[str, Any],
    *,
    initial_max_tokens: int,
    retry_max_tokens: int,
) -> dict[str, Any]:
    user_prompt = json.dumps(payload, ensure_ascii=False)
    try:
        return call_deepseek_json(
            system_prompt,
            user_prompt,
            temperature=0.15,
            max_tokens=initial_max_tokens,
            timeout=45,
        )
    except LLMJsonError as exc:
        if exc.reason_code not in {"json_truncated", "json_malformed", "json_extra_text"}:
            raise
        return call_deepseek_json(
            system_prompt + "\n上一次输出格式不完整。本次请压缩内容，并确保 JSON 完整闭合。",
            user_prompt,
            temperature=0.1,
            max_tokens=retry_max_tokens,
            timeout=55,
        )


def generate_exercise_from_tutor(
    user_id: str,
    *,
    message: str,
    mode: str | None = None,
    answer: str | None = None,
    course_id: str | None = None,
) -> dict[str, Any]:
    mode = mode or parse_tutor_message(message)[0]
    question = parse_tutor_message(message)[1] if message.startswith("[") else message.strip()
    retrieval = search_chunks(question, top_k=4)
    citations = [citation_from_chunk(item) for item in retrieval.get("items", [])]
    if retrieval.get("coverage") == "none" or not citations:
        raise LLMUnavailable("课程资料命中不足，无法基于真实依据生成练习。")
    if not llm_enabled():
        raise LLMUnavailable("LLM_ENABLE is not true")

    payload = {
        "task": "generate_practice_items",
        "mode": mode,
        "question": question,
        "existingAnswer": (answer or "")[:1200],
        "courseId": course_id,
        "citations": _compact_citations(citations),
        "expectedSchema": {
            "title": "string",
            "items": [
                {
                    "type": "single|short|code|case",
                    "stem": "string",
                    "options": ["string"],
                    "answer": "string",
                    "analysis": "string",
                    "citationChunkIds": ["string"],
                }
            ],
        },
    }
    result = call_deepseek_json(_exercise_system_prompt(), json.dumps(payload, ensure_ascii=False), temperature=0.2, max_tokens=2200)
    items = _normalize_practice_items(result.get("items"), {item["chunkId"] for item in citations})
    if not items:
        raise LLMUnavailable("LLM 未返回可用练习题。")
    return {
        "id": f"tutor_exercise_{_short_id()}",
        "userId": user_id,
        "title": str(result.get("title") or "智能辅导相似练习")[:80],
        "sourceQuestion": question,
        "items": items[:5],
        "citations": citations,
        "generationMode": GENERATION_RAG_LLM,
        "createdAt": now_text(),
    }


def generate_document_from_tutor(
    user_id: str,
    *,
    message: str,
    mode: str | None = None,
    answer: str | None = None,
    course_id: str | None = None,
) -> dict[str, Any]:
    mode = mode or parse_tutor_message(message)[0]
    question = parse_tutor_message(message)[1] if message.startswith("[") else message.strip()
    retrieval = search_chunks(question, top_k=4)
    citations = [citation_from_chunk(item) for item in retrieval.get("items", [])]
    if retrieval.get("coverage") == "none" or not citations:
        raise LLMUnavailable("课程资料命中不足，无法生成高可信讲解文档。")
    if not llm_enabled():
        raise LLMUnavailable("LLM_ENABLE is not true")

    payload = {
        "task": "generate_tutorial_document",
        "mode": mode,
        "question": question,
        "existingAnswer": (answer or "")[:1600],
        "courseId": course_id,
        "citations": _compact_citations(citations),
        "expectedSchema": {
            "title": "string",
            "contentMarkdown": "string",
            "inferredSections": ["string"],
            "citationsUsed": ["chunkId"],
        },
    }
    result = call_deepseek_json(_document_system_prompt(), json.dumps(payload, ensure_ascii=False), temperature=0.18, max_tokens=2800)
    content = str(result.get("contentMarkdown") or "").strip()
    if not content:
        raise LLMUnavailable("LLM 未返回可用讲解文档。")
    return {
        "id": f"tutor_doc_{_short_id()}",
        "userId": user_id,
        "title": str(result.get("title") or "智能辅导讲解文档")[:80],
        "content": content,
        "citations": citations,
        "generationMode": GENERATION_RAG_LLM,
        "inferredSections": [str(item) for item in result.get("inferredSections", []) if str(item).strip()],
        "createdAt": now_text(),
    }


def _system_prompt() -> str:
    return """你是高校《数据结构课程》的智能辅导 Agent。
你必须优先基于输入中的真实课程引用回答，不得编造不存在的课程内容、页码、公式或代码运行结果。
如果需要做教学类推、例子或学习建议，必须放入 inferredSections。
输出必须是 JSON 对象，字段必须包含：
answerMarkdown, diagramMarkdown, diagramMermaid, videoScenes, suggestedActions, citationsUsed, inferredSections, confidence, confidenceReason。
videoScenes 每项包含 timeRange, title, screenText, voiceover, keyConcepts, citationChunkIds。
diagramMarkdown 用 Markdown 层级表达知识结构，适合 markmap 渲染。
citationsUsed 只能使用输入 citations 中存在的 chunkId。"""


def _text_answer_system_prompt() -> str:
    return """你是高校《数据结构课程》的智能辅导老师。
只输出给学生看的 Markdown 正文，不要输出 JSON、代码围栏或内部分析过程。
回答必须优先依据输入中的真实课程引用，不得编造页码、公式、代码运行结果或课程内容。
如果课程资料不足以支持某个结论，要明确标注“补充解释”或“学习建议”，不要伪装成课程原文。
回答结构应简洁清楚，优先包含：直接回答、课程依据、例子或步骤、下一步练习建议。
不要生成思维导图、Mermaid 或视频脚本；这些内容会在用户需要时单独生成。"""


def _diagram_system_prompt() -> str:
    return """你是数据结构课程图解生成 Agent。
根据输入的课程引用和已经生成的辅导回答，输出 JSON 对象：
title, diagramMarkdown, diagramMermaid, citationsUsed。
diagramMarkdown 必须是适合 Markmap 的 Markdown 标题层级，根节点只有一个，最多四层。
不得编造课程事实，citationsUsed 只能引用输入中存在的 chunkId。"""


def _video_system_prompt() -> str:
    return """你是数据结构课程短视频讲解脚本 Agent。
根据输入的课程引用和辅导回答，输出 JSON 对象：title, videoScenes。
videoScenes 每项必须包含 timeRange, title, screenText, voiceover, keyConcepts, citationChunkIds。
总场景数 3 至 6 个，讲解要短、清晰、适合学生复习。
不得编造课程事实，citationChunkIds 只能引用输入中存在的 chunkId。"""


def _exercise_system_prompt() -> str:
    return """你是数据结构课程练习生成 Agent。只基于输入课程引用生成题目。
输出 JSON：title, items。每道题必须有 stem, type, answer, analysis, citationChunkIds；单选题必须有 options。
不要编造引用，citationChunkIds 只能来自输入。"""


def _document_system_prompt() -> str:
    return """你是数据结构课程讲解文档 Agent。只基于输入课程引用和本次辅导回答生成可复习文档。
输出 JSON：title, contentMarkdown, inferredSections, citationsUsed。
课程依据和模型推断必须分开表达；不能编造不存在的引用。"""


def _build_prompt_payload(
    mode: str,
    question: str,
    course_id: str | None,
    citations: list[dict[str, Any]],
    retrieval: dict[str, Any],
    profile_context: dict[str, Any],
    learning_path: dict[str, Any],
) -> dict[str, Any]:
    active_stage = next((item for item in learning_path.get("stages", []) if item.get("status") == "active"), {})
    return {
        "courseId": course_id,
        "mode": mode,
        "question": question,
        "retrieval": {
            "coverage": retrieval.get("coverage"),
            "pipeline": retrieval.get("retrieval_pipeline", []),
            "missingKnowledge": retrieval.get("missing_knowledge", []),
        },
        "citations": _compact_citations(citations),
        "studentContext": {
            "profile": profile_context.get("current_profile", {}),
            "latestGoal": profile_context.get("latest_goal"),
            "mistakeSummary": profile_context.get("mistake_summary", []),
            "resourceFeedback": profile_context.get("resource_feedback", []),
            "tutorQuestionSummary": profile_context.get("tutor_question_summary", []),
            "activeStage": {
                "name": active_stage.get("name"),
                "knowledgePoints": active_stage.get("knowledgePoints", []),
                "tasks": active_stage.get("tasks", []),
            },
        },
        "expectedSchema": {
            "answerMarkdown": "string",
            "diagramMarkdown": "# topic\\n## branch\\n### leaf",
            "diagramMermaid": "mindmap...",
            "videoScenes": [
                {
                    "timeRange": "0:00-0:20",
                    "title": "string",
                    "screenText": "string",
                    "voiceover": "string",
                    "keyConcepts": ["string"],
                    "citationChunkIds": ["chunkId"],
                }
            ],
            "suggestedActions": [{"type": "note|exercise|mistake|path", "title": "string", "reason": "string"}],
            "citationsUsed": ["chunkId"],
            "inferredSections": ["string"],
            "confidence": 0.0,
            "confidenceReason": "string",
        },
    }


def _build_text_prompt_payload(
    mode: str,
    question: str,
    course_id: str | None,
    citations: list[dict[str, Any]],
    retrieval: dict[str, Any],
    profile_context: dict[str, Any],
    learning_path: dict[str, Any],
) -> dict[str, Any]:
    active_stage = next((item for item in learning_path.get("stages", []) if item.get("status") == "active"), {})
    return {
        "courseId": course_id,
        "mode": mode,
        "question": question,
        "retrievalCoverage": retrieval.get("coverage"),
        "citations": _compact_citations(citations),
        "studentContext": {
            "profile": profile_context.get("current_profile", {}),
            "latestGoal": profile_context.get("latest_goal"),
            "mistakeSummary": profile_context.get("mistake_summary", []),
            "resourceFeedback": profile_context.get("resource_feedback", []),
            "activeStage": {
                "name": active_stage.get("name"),
                "knowledgePoints": active_stage.get("knowledgePoints", []),
            },
        },
    }


def _compact_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "chunkId": item.get("chunkId"),
            "documentName": item.get("documentName"),
            "sourceLocation": item.get("sourceLocation"),
            "page": item.get("page"),
            "similarity": item.get("similarity"),
            "content": (item.get("fullText") or item.get("contentPreview") or "")[:900],
        }
        for item in citations
    ]


def _normalize_llm_response(
    *,
    mode: str,
    question: str,
    citations: list[dict[str, Any]],
    retrieval: dict[str, Any],
    profile_update_draft: dict[str, Any] | None,
    generated: dict[str, Any],
) -> dict[str, Any]:
    allowed_chunk_ids = {str(item.get("chunkId") or "") for item in citations if item.get("chunkId")}
    citations_used = [str(item) for item in generated.get("citationsUsed", []) if str(item) in allowed_chunk_ids]
    used_citations = [item for item in citations if item.get("chunkId") in citations_used] or citations
    confidence = _clamp_float(generated.get("confidence"), 0.72)
    if not citations_used:
        confidence = min(confidence, 0.72)
    if retrieval.get("coverage") == "low":
        confidence = min(confidence, 0.78)

    answer_markdown = str(generated.get("answerMarkdown") or "").strip()
    if not answer_markdown:
        answer_markdown = _minimal_grounded_answer(mode, question, used_citations)
        confidence = min(confidence, 0.68)

    inferred_sections = [str(item).strip() for item in generated.get("inferredSections", []) if str(item).strip()]
    video_scenes = _normalize_video_scenes(generated.get("videoScenes"), allowed_chunk_ids, question)
    diagram = {
        "markdown": _normalize_diagram_markdown(generated.get("diagramMarkdown"), question),
        "mermaid": str(generated.get("diagramMermaid") or "").strip(),
        "title": f"{question[:28]}图解",
    }
    return {
        "answer": answer_markdown,
        "diagram": diagram,
        "videoScript": {
            "title": f"{question[:28]}短视频讲解",
            "script": _video_script_markdown(video_scenes),
            "scenes": video_scenes,
        },
        "suggestedActions": _normalize_suggested_actions(generated.get("suggestedActions")),
        "citations": used_citations,
        "allCitations": citations,
        "inferred": bool(inferred_sections) or retrieval.get("coverage") != "sufficient",
        "inferredSections": inferred_sections,
        "confidence": confidence,
        "confidenceReason": str(generated.get("confidenceReason") or "基于课程资料命中、引用使用情况和模型生成完整度综合评估。"),
        "coverage": retrieval.get("coverage"),
        "generationMode": GENERATION_RAG_LLM,
        "profileUpdateDraft": profile_update_draft,
        "llm": {"enabled": True, "model": llm_model_name(), "used": True},
        "createdAt": now_text(),
    }


def _insufficient_evidence_response(
    mode: str,
    question: str,
    retrieval: dict[str, Any],
    profile_update_draft: dict[str, Any] | None,
) -> dict[str, Any]:
    answer = f"""# 当前课程资料依据不足

你问的是：{question}

系统没有在真实课程知识库中检索到足够相关的片段，因此不会生成高可信答案。

## 建议补充

1. 上传或确认包含该知识点的课程讲义、实验指导或题目原文。
2. 把问题缩小到具体章节、定义、操作步骤或代码片段。
3. 让教师先补充课程资料后再重新提问。
"""
    return {
        "answer": answer,
        "diagram": {"markdown": "", "mermaid": "", "title": "资料不足"},
        "videoScript": {"title": "资料不足", "script": "", "scenes": []},
        "suggestedActions": [{"type": "refine_question", "title": "补充课程资料或题目原文", "reason": "当前知识库没有足够依据。"}],
        "citations": [],
        "allCitations": [],
        "inferred": False,
        "inferredSections": [],
        "confidence": 0.0,
        "confidenceReason": "没有真实课程引用，不生成高可信回答。",
        "coverage": retrieval.get("coverage", "none"),
        "generationMode": GENERATION_INSUFFICIENT,
        "profileUpdateDraft": profile_update_draft,
        "llm": {"enabled": llm_enabled(), "model": llm_model_name(), "used": False},
        "createdAt": now_text(),
    }


def _llm_unavailable_response(
    mode: str,
    question: str,
    citations: list[dict[str, Any]],
    retrieval: dict[str, Any],
    profile_update_draft: dict[str, Any] | None,
    error: str,
) -> dict[str, Any]:
    answer = f"""# 大模型服务暂不可用

已检索到 {len(citations)} 条真实课程资料，但当前大模型没有返回可用结果，因此系统不会用模板或假答案伪装成功。

## 本次问题

{question}

## 可继续操作

请检查 `LLM_ENABLE`、`DEEPSEEK_API_KEY` 和网络配置后重试。右侧引用来源仍可用于确认资料是否命中正确章节。
"""
    return {
        "answer": answer,
        "diagram": {"markdown": "", "mermaid": "", "title": "模型不可用"},
        "videoScript": {"title": "模型不可用", "script": "", "scenes": []},
        "suggestedActions": [{"type": "retry", "title": "检查大模型配置后重试", "reason": error[:180]}],
        "citations": citations,
        "allCitations": citations,
        "inferred": False,
        "inferredSections": [],
        "confidence": 0.0,
        "confidenceReason": f"LLM 不可用：{error}",
        "coverage": retrieval.get("coverage"),
        "generationMode": GENERATION_LLM_UNAVAILABLE,
        "profileUpdateDraft": profile_update_draft,
        "llm": {"enabled": llm_enabled(), "model": llm_model_name(), "used": False, "error": error},
        "createdAt": now_text(),
    }


def _normalize_practice_items(items: Any, allowed_chunk_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        stem = str(item.get("stem") or "").strip()
        answer = str(item.get("answer") or "").strip()
        analysis = str(item.get("analysis") or "").strip()
        if not stem or not answer or not analysis:
            continue
        chunk_ids = [str(cid) for cid in item.get("citationChunkIds", []) if str(cid) in allowed_chunk_ids]
        normalized.append({
            "type": str(item.get("type") or "short"),
            "stem": stem,
            "options": [str(option) for option in item.get("options", []) if str(option).strip()],
            "answer": answer,
            "analysis": analysis,
            "citationChunkIds": chunk_ids,
        })
    return normalized


def _normalize_video_scenes(raw_scenes: Any, allowed_chunk_ids: set[str], question: str) -> list[dict[str, Any]]:
    scenes = raw_scenes if isinstance(raw_scenes, list) else []
    normalized = []
    for index, scene in enumerate(scenes[:6]):
        if not isinstance(scene, dict):
            continue
        title = str(scene.get("title") or f"讲解片段 {index + 1}").strip()
        screen_text = str(scene.get("screenText") or scene.get("description") or title).strip()
        voiceover = str(scene.get("voiceover") or scene.get("narration") or "").strip()
        if not voiceover:
            continue
        chunk_ids = [str(cid) for cid in scene.get("citationChunkIds", []) if str(cid) in allowed_chunk_ids]
        normalized.append({
            "id": f"tutor_scene_{index + 1}",
            "time": str(scene.get("time") or scene.get("timeRange") or f"0:{index * 25:02d}-0:{(index + 1) * 25:02d}"),
            "timeRange": str(scene.get("timeRange") or scene.get("time") or f"0:{index * 25:02d}-0:{(index + 1) * 25:02d}"),
            "title": title[:40],
            "screenTitle": title[:40],
            "screenText": screen_text[:140],
            "description": screen_text[:180],
            "visual": screen_text[:140],
            "voiceover": voiceover[:360],
            "narration": voiceover[:360],
            "keyConcepts": [str(item) for item in scene.get("keyConcepts", []) if str(item).strip()][:5],
            "concept": "、".join([str(item) for item in scene.get("keyConcepts", []) if str(item).strip()][:3]) or question[:24],
            "recordingSteps": [str(item) for item in scene.get("recordingSteps", []) if str(item).strip()][:5],
            "kind": str(scene.get("kind") or "structure"),
            "type": str(scene.get("type") or "structure"),
            "citationChunkIds": chunk_ids,
            "agentEvidence": "课程引用：" + "、".join(chunk_ids) if chunk_ids else "模型教学组织，需结合引用复核",
        })
    return normalized


def _normalize_diagram_markdown(value: Any, question: str) -> str:
    text = str(value or "").strip()
    if text:
        return text if text.startswith("#") else f"# {question[:32]}\n{text}"
    return f"# {question[:32] or '智能辅导图解'}\n## 课程依据\n## 核心理解\n## 常见误区\n## 下一步练习"


def _normalize_suggested_actions(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        value = []
    result = []
    for item in value[:6]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if title:
            result.append({"type": str(item.get("type") or "note"), "title": title, "reason": reason})
    return result or [
        {"type": "note", "title": "保存本次讲解", "reason": "保留课程引用和推断标记，便于复习。"},
        {"type": "exercise", "title": "生成相似练习", "reason": "用真实引用检验是否掌握。"},
    ]


def _minimal_grounded_answer(mode: str, question: str, citations: list[dict[str, Any]]) -> str:
    evidence = "\n".join(
        f"- {item.get('documentName')} {item.get('sourceLocation')}：{item.get('contentPreview')}"
        for item in citations[:3]
    )
    return f"""# 基于课程资料的简要回答

你问的是：{question}

## 课程资料命中

{evidence}

## 下一步

当前模型返回内容不完整，建议先核对以上引用，再重新提问或生成练习。
"""


def _video_script_markdown(scenes: list[dict[str, Any]]) -> str:
    if not scenes:
        return ""
    lines = ["# 短视频讲解脚本"]
    for scene in scenes:
        lines.extend([
            f"## {scene.get('timeRange')} {scene.get('title')}",
            f"- 屏幕文字：{scene.get('screenText')}",
            f"- 旁白：{scene.get('voiceover')}",
        ])
    return "\n".join(lines)


def _clamp_float(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = fallback
    return max(0.0, min(1.0, number))


def _short_id() -> str:
    from uuid import uuid4

    return uuid4().hex[:8]
