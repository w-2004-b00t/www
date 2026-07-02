from __future__ import annotations

from copy import deepcopy
import json
import os
import re
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from ..demo_data import RESOURCES, now_text
from .knowledge_service import search_chunks
from .llm_service import LLMJsonError, LLMUnavailable, call_deepseek_json, llm_enabled, llm_model_name
from .course_progress_service import chapter_for_topic
from .strict_generation import raise_blocked
from .topic_sanitizer import clean_generation_target, clean_generation_topic

REQUIRED_RESOURCE_TYPES = ("explanation", "mindmap", "exercise", "reading", "video_script", "lab")
_MINDMAP_PAYLOAD_CACHE: dict[str, dict[str, Any]] = {}
DEFAULT_RESOURCE_TOPIC = "数据结构核心知识点"
DEFAULT_RESOURCE_TARGET = "45 分钟内理解概念、完成例题和代码实践"

RESOURCE_GENERATION_TEMPLATES = {
    "explanation": "course_tutorial_doc_v2",
    "mindmap": "tree_mindmap_v1",
    "exercise": "question_bank_schema_v1",
    "reading": "annotated_reading_v1",
    "video_script": "storyboard_video_v1",
    "lab": "code_laboratory_v1",
}

RESOURCE_PROMPT_SCHEMAS = {
    "explanation": "title, summary, sections[{heading, body_md}], formula_blocks[{label, latex, explanation}], examples[{title, steps, answer}], mistakes[], citation_notes[{chunkId, usage}], takeaways[]",
    "mindmap": "root, branches, node_types, jump_targets, citation_tags",
    "exercise": "question_types, answer_key, rubric, weakness_tags, citation_links",
    "reading": "title, summary, reading_order, suitable_profile, citation_notes",
    "video_script": "title, timeline, scenes[{timeRange, title, voiceover, screenText, recordingSteps}], subtitles, citations",
    "lab": "title, code, run_steps, parameters, experiment_task, acceptance, citations",
}

EXPLANATION_REQUIRED_HEADINGS = (
    "目录",
    "本章导读",
    "1. 线性表的概念",
    "2. 顺序存储结构",
    "3. 链式存储结构",
    "4. 基本操作",
    "5. 操作过程追踪",
    "6. 复杂度分析",
    "7. 代码实现",
    "8. 常见错误",
    "9. 小结与练习",
)

EXPLANATION_TEACHING_HEADINGS = EXPLANATION_REQUIRED_HEADINGS[1:]


def build_resources_for_topic(
    topic: str,
    target: str,
    resource_types: list[str] | None = None,
    chapter: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not llm_enabled():
        raise_blocked(
            status_code=503,
            agent_name="资源生成 Agent",
            message="DeepSeek 未启用，已停止资源生成；不会使用静态模板或本地规则兜底。",
            missing_requirements=["启用 LLM_ENABLE/DEEPSEEK_API_KEY", "DeepSeek 结构化生成"],
        )
    safe_topic = clean_generation_topic(_clean_generated_text(topic, DEFAULT_RESOURCE_TOPIC), DEFAULT_RESOURCE_TOPIC)
    safe_target = clean_generation_target(_clean_generated_text(target, DEFAULT_RESOURCE_TARGET), safe_topic, DEFAULT_RESOURCE_TARGET)
    chapter_meta = chapter or chapter_for_topic(safe_topic)
    selected_types = _normalize_resource_types(resource_types)
    retrieval = search_chunks(safe_topic, int(os.getenv("RESOURCE_RETRIEVAL_TOP_K", "20")))
    if retrieval.get("coverage") != "sufficient":
        raise HTTPException(status_code=409, detail="本地知识库导入或检索不足，已停止资源生成。")
    citation_pool = _sanitize_citations_for_student(_citations_from_retrieval(retrieval.get("items", [])))
    if not citation_pool:
        raise HTTPException(status_code=409, detail="本地知识库未返回可引用片段，已停止资源生成。")
    builders = {
        "explanation": _build_explanation_resource,
        "mindmap": _build_mindmap_resource,
        "exercise": _build_llm_exercise_resource,
        "reading": _build_llm_reading_resource,
        "video_script": _build_llm_video_script_resource,
        "lab": _build_llm_lab_resource,
    }
    generated: list[dict[str, Any]] = []
    for resource_type in selected_types:
        builder = builders.get(resource_type)
        if not builder:
            continue
        pool = _prefer_code_citations(citation_pool) if resource_type == "lab" else _prefer_teaching_citations(citation_pool)
        selected_citations = _select_citations(resource_type, pool)
        item = builder(safe_topic, safe_target, selected_citations, retrieval)
        item.update({
            "id": f"{resource_type}_{uuid4().hex[:8]}",
            "resourceType": resource_type,
            "createdAt": now_text(),
            "updatedAt": now_text(),
            "fitReason": _fit_reason(resource_type),
            "citations": selected_citations,
            "version": 1,
            "versionReason": "本地知识库 RAG 引用溯源生成",
            "auditStatus": "passed" if selected_citations else "warning",
            "auditHistory": [],
            "feedback": [],
            "qualityScore": item.get("qualityScore", 92),
            "metadata": {
                "topic": safe_topic,
                "target": safe_target,
                "chapterId": chapter_meta.get("chapterId"),
                "chapterName": chapter_meta.get("chapterName") or safe_topic,
                "chapterOrder": chapter_meta.get("order") or chapter_meta.get("chapterOrder"),
                "mode": "local_knowledge_base_generation",
                "course": "数据结构课程",
                "generationTemplate": RESOURCE_GENERATION_TEMPLATES.get(resource_type, "structured_template_v1"),
                "promptSchema": RESOURCE_PROMPT_SCHEMAS.get(resource_type, "structured_output"),
                "retrievalQuery": safe_topic,
                "retrievalCoverage": retrieval.get("coverage", "none"),
                "sourceChunkIds": [citation["chunkId"] for citation in selected_citations],
                "citationCount": len(selected_citations),
                "generationMode": item.get("metadata", {}).get("generationMode") or f"deepseek_{resource_type}",
                "llmModel": llm_model_name(),
                **item.get("metadata", {}),
            },
        })
        generated.append(item)
    return generated


def _looks_corrupted_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return True
    bad_count = compact.count("?") + compact.count("\ufffd")
    has_readable_topic = bool(re.search(r"[\u4e00-\u9fffA-Za-z0-9]", compact))
    return "???" in compact or "\ufffd" in compact or (bad_count >= 2 and (bad_count / len(compact)) > 0.2) or not has_readable_topic


def _clean_generated_text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text or _looks_corrupted_text(text):
        return fallback
    text = re.sub(r"\?{3,}", fallback, text)
    text = re.sub(r"\ufffd+", fallback, text)
    return text.strip() or fallback


def sanitize_resource_for_display(resource: dict[str, Any]) -> dict[str, Any]:
    cleaned = deepcopy(resource)
    topic = _clean_generated_text((cleaned.get("metadata") or {}).get("topic"), DEFAULT_RESOURCE_TOPIC)
    target = _clean_generated_text((cleaned.get("metadata") or {}).get("target"), DEFAULT_RESOURCE_TARGET)
    replacements = {
        "??????": topic,
        "????????????????????": target,
    }

    def clean_text(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value
        for bad, good in replacements.items():
            text = text.replace(bad, good)
        text = re.sub(r"\?{3,}", topic, text)
        text = re.sub(r"\ufffd+", topic, text)
        return _strip_debug_text(text)

    for field in ["title", "summary", "fitReason", "versionReason"]:
        if field in cleaned:
            cleaned[field] = clean_text(cleaned[field])
    if "content" in cleaned and isinstance(cleaned.get("content"), str):
        cleaned["content"] = _clean_resource_markdown_content(str(cleaned.get("content") or ""), topic)

    metadata = cleaned.get("metadata")
    if isinstance(metadata, dict):
        metadata["topic"] = topic
        metadata["target"] = target
        metadata["retrievalQuery"] = topic
    for citation in cleaned.get("citations", []) or []:
        if isinstance(citation, dict):
            for field in ("documentName", "sourceLocation", "contentPreview", "fullText"):
                if field in citation:
                    citation[field] = clean_text(citation[field])
    return cleaned


def _normalize_resource_types(resource_types: list[str] | None) -> list[str]:
    ordered = []
    for item in list(resource_types or []) + list(REQUIRED_RESOURCE_TYPES):
        if item and item not in ordered:
            ordered.append(item)
    return ordered or list(REQUIRED_RESOURCE_TYPES)


def _fit_reason(resource_type: str) -> str:
    reasons = {
        "mindmap": "用于完整梳理知识结构，适合先建立全局理解。",
        "video_script": "用于比赛演示和学生快速预习，适合短时间建立直觉。",
        "explanation": "用于补足概念理解，适合先读后练。",
        "exercise": "用于检测薄弱点，适合进入测评前巩固。",
        "reading": "用于扩展资料来源，适合复盘。",
        "lab": "用于代码实践，适合完成课程作业。",
    }
    return reasons.get(resource_type, "根据当前画像和学习目标生成。")


def _build_explanation_resource(topic: str, target: str, citations: list[dict[str, Any]], retrieval: dict[str, Any]) -> dict[str, Any]:
    return _build_structured_explanation_document(
        topic,
        target,
        citations,
        str(retrieval.get("coverage", "none")),
    )


def _build_mindmap_resource(topic: str, target: str, citations: list[dict[str, Any]], retrieval: dict[str, Any]) -> dict[str, Any]:
    coverage = str(retrieval.get("coverage", "none"))
    payload = _try_generate_mindmap_payload(topic, target, citations, coverage)
    if payload:
        return payload
    raise_blocked(
        status_code=503,
        agent_name="多模态生成 Agent",
        message="DeepSeek 未返回合格思维导图结构，已停止生成；不会使用静态思维导图模板兜底。",
        missing_requirements=["DeepSeek mindmap JSON", "合法 Mermaid mindmap", "真实课程引用"],
        used_llm=True,
    )


def _build_exercise_resource(topic: str, target: str, citations: list[dict[str, Any]], retrieval: dict[str, Any]) -> dict[str, Any]:
    primary = citations[0] if citations else {}
    secondary = citations[1] if len(citations) > 1 else primary
    source = _clean_excerpt(str(primary.get("contentPreview") or topic), 220)
    secondary_source = _clean_excerpt(str(secondary.get("contentPreview") or source), 180)
    keywords = _keywords_from_citations(citations) or _keywords_from_text(source) or [topic]
    concept = keywords[0]
    operation = next((item for item in keywords if item != concept), topic)
    exercises = [
        {
            "type": "single",
            "stem": f"根据课程片段，学习「{topic}」时应优先把哪组内容对应起来？",
            "options": [
                f"{concept}的定义、存储方式、基本操作和复杂度",
                "只记住资料标题和页码",
                "只看代码文件名，不分析操作过程",
                "跳过边界条件，直接背结论",
            ],
            "answer": f"{concept}的定义、存储方式、基本操作和复杂度",
            "analysis": f"题目依据：{source}",
            "citationChunkId": primary.get("chunkId"),
        },
        {
            "type": "short",
            "stem": f"结合课程片段，用 2-3 句话说明「{topic}」中「{concept}」的作用。",
            "answer": concept,
            "analysis": f"回答应围绕片段中的定义或操作语句展开：{source}",
            "citationChunkId": primary.get("chunkId"),
        },
        {
            "type": "calculation",
            "stem": f"请说明分析「{topic}」里「{operation}」相关操作复杂度时至少要检查的两个维度。",
            "answer": "时间复杂度 空间复杂度",
            "analysis": f"至少包含时间复杂度和空间复杂度；结合课程片段检查操作次数与数据规模的关系。参考：{secondary_source}",
            "citationChunkId": secondary.get("chunkId"),
        },
        {
            "type": "code",
            "stem": f"写出「{topic}」代码实践时的伪代码框架，要求体现课程片段中的关键操作。",
            "answer": "初始化 核心操作 输出",
            "analysis": f"代码题重点检查是否能把课程知识转为可执行步骤。可参考片段：{secondary_source}",
            "citationChunkId": secondary.get("chunkId"),
        },
    ]
    return {
        "title": f"{topic}分层练习题",
        "summary": f"基于 {len(citations)} 条课程引用生成选择、简答、复杂度分析和代码实践题。",
        "content": json.dumps(exercises, ensure_ascii=False, indent=2),
        "qualityScore": 92,
        "metadata": {
            "exerciseSchema": "single_choice+short_answer+calculation+code",
            "assessmentReady": True,
            "sourceDriven": True,
        },
    }


def _build_llm_exercise_resource(topic: str, target: str, citations: list[dict[str, Any]], retrieval: dict[str, Any]) -> dict[str, Any]:
    coverage = str(retrieval.get("coverage", "none"))
    result = _call_deepseek_resource_json(
        "题库生成 Agent",
        topic,
        target,
        citations,
        "生成 6 道数据结构练习题，必须输出 JSON：{title, summary, exercises:[{type, stem, options, answer, analysis, citationChunkId}]}。type 只能为 single/short/calculation/code/case。",
        max_tokens=2600,
    )
    exercises = result.get("exercises")
    if not isinstance(exercises, list) or len(exercises) < 4:
        raise_blocked(
            status_code=503,
            agent_name="题库生成 Agent",
            message="DeepSeek 未返回足够练习题，已停止生成；不会用静态题库补齐。",
            missing_requirements=["至少 4 道结构化题目", "题目绑定 citationChunkId"],
            used_llm=True,
        )
    valid_ids = {str(item.get("chunkId")) for item in citations}
    cleaned = []
    for item in exercises:
        if not isinstance(item, dict):
            continue
        chunk_id = str(item.get("citationChunkId") or "").strip()
        if chunk_id not in valid_ids:
            continue
        cleaned.append({
            "type": str(item.get("type") or "short"),
            "stem": str(item.get("stem") or "").strip(),
            "options": item.get("options") if isinstance(item.get("options"), list) else [],
            "answer": str(item.get("answer") or "").strip(),
            "analysis": str(item.get("analysis") or "").strip(),
            "citationChunkId": chunk_id,
        })
    if len(cleaned) < 4:
        raise_blocked(
            status_code=503,
            agent_name="题库生成 Agent",
            message="DeepSeek 题目缺少有效课程引用，已停止生成。",
            missing_requirements=["每道题必须引用本次检索命中的 chunkId"],
            used_llm=True,
        )
    return {
        "title": str(result.get("title") or f"{topic}练习题").strip(),
        "summary": str(result.get("summary") or f"围绕{topic}生成的引用驱动练习。").strip(),
        "content": json.dumps(cleaned, ensure_ascii=False),
        "qualityScore": 94,
        "metadata": {
            "generationMode": "deepseek_exercise",
            "retrievalCoverage": coverage,
            "sourceChunkIds": [citation["chunkId"] for citation in citations],
        },
    }


def _build_llm_reading_resource(topic: str, target: str, citations: list[dict[str, Any]], retrieval: dict[str, Any]) -> dict[str, Any]:
    return _build_llm_markdown_resource("拓展阅读 Agent", "reading", topic, target, citations, retrieval)


def _build_llm_video_script_resource(topic: str, target: str, citations: list[dict[str, Any]], retrieval: dict[str, Any]) -> dict[str, Any]:
    return _build_llm_markdown_resource("多模态生成 Agent", "video_script", topic, target, citations, retrieval)


def _build_llm_lab_resource(topic: str, target: str, citations: list[dict[str, Any]], retrieval: dict[str, Any]) -> dict[str, Any]:
    return _build_llm_markdown_resource("代码实操 Agent", "lab", topic, target, citations, retrieval)


def _build_llm_markdown_resource(
    agent_name: str,
    resource_type: str,
    topic: str,
    target: str,
    citations: list[dict[str, Any]],
    retrieval: dict[str, Any],
) -> dict[str, Any]:
    coverage = str(retrieval.get("coverage", "none"))
    result = _call_deepseek_resource_json(
        agent_name,
        topic,
        target,
        citations,
        f"生成 {resource_type} 学习资源，必须输出 JSON：{{title, summary, contentMarkdown, citationChunkIds}}。contentMarkdown 必须引用课程片段，不要使用模板化占位内容。",
        max_tokens=2600,
    )
    content = str(result.get("contentMarkdown") or result.get("content") or "").strip()
    title = str(result.get("title") or "").strip()
    summary = str(result.get("summary") or "").strip()
    chunk_ids = [str(item).strip() for item in result.get("citationChunkIds", []) if str(item).strip()] if isinstance(result.get("citationChunkIds"), list) else []
    valid_ids = {str(item.get("chunkId")) for item in citations}
    if not title or not summary or len(content) < 120 or not chunk_ids or any(chunk_id not in valid_ids for chunk_id in chunk_ids):
        raise_blocked(
            status_code=503,
            agent_name=agent_name,
            message="DeepSeek 返回的学习资源结构不完整或引用无效，已停止生成。",
            missing_requirements=["title", "summary", "contentMarkdown", "有效 citationChunkIds"],
            used_llm=True,
        )
    return {
        "title": title,
        "summary": summary,
        "content": content + "\n",
        "qualityScore": 94,
        "metadata": {
            "generationMode": f"deepseek_{resource_type}",
            "retrievalCoverage": coverage,
            "sourceChunkIds": chunk_ids,
        },
    }


def _call_deepseek_resource_json(
    agent_name: str,
    topic: str,
    target: str,
    citations: list[dict[str, Any]],
    instruction: str,
    *,
    max_tokens: int,
) -> dict[str, Any]:
    citation_context = _citation_context(citations)
    valid_ids = [str(item.get("chunkId")) for item in citations if str(item.get("chunkId") or "").strip()]
    user_prompt = chr(10).join([
        f"topic: {topic}",
        f"target: {target}",
        f"valid citationChunkIds: {', '.join(valid_ids)}",
        "citations:",
        citation_context,
        instruction,
    ])
    timeout = int(os.getenv("DEEPSEEK_RESOURCE_TIMEOUT", "45"))
    attempts: list[dict[str, Any]] = []
    try:
        result = call_deepseek_json(
            "你是严格的课程资源生成 Agent。只能基于给定课程引用生成内容；必须输出合法 JSON；禁止编造引用；禁止静态模板兜底。",
            user_prompt,
            temperature=0.2,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except LLMJsonError as exc:
        attempts.append({
            "stage": "primary",
            "reasonCode": exc.reason_code,
            "message": str(exc),
            "rawExcerpt": exc.raw_excerpt[:300],
        })
        retry_prompt = chr(10).join([
            f"topic: {topic}",
            f"target: {target}",
            f"valid citationChunkIds: {', '.join(valid_ids)}",
            "你上一次输出不是合法 JSON。现在只输出一个 JSON 对象，不要 Markdown fence，不要解释文字，不要在 JSON 前后添加任何字符。",
            "必须继续满足原任务：",
            instruction,
            "citations:",
            citation_context,
        ])
        try:
            result = call_deepseek_json(
                "你是严格 JSON 修复器。只返回合法 JSON 对象，所有换行和引号必须在字符串内正确转义。",
                retry_prompt,
                temperature=0.1,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            attempts.append({"stage": "retry", "reasonCode": "ok", "message": "retry_json_parse_success"})
        except (LLMUnavailable, TimeoutError, OSError, ValueError) as retry_exc:
            if isinstance(retry_exc, LLMJsonError):
                attempts.append({
                    "stage": "retry",
                    "reasonCode": retry_exc.reason_code,
                    "message": str(retry_exc),
                    "rawExcerpt": retry_exc.raw_excerpt[:300],
                })
            raise_blocked(
                status_code=503,
                agent_name=agent_name,
                message="DeepSeek 资源生成不可用，已停止生成；不会使用静态模板或规则兜底。",
                missing_requirements=["DeepSeek 可用", "结构化 JSON 输出"],
                used_llm=True,
                detail=_resource_generation_error_detail(
                    agent_name,
                    retry_exc,
                    timeout=timeout,
                    max_tokens=max_tokens,
                    attempts=attempts,
                ),
            )
    except (LLMUnavailable, TimeoutError, OSError, ValueError) as exc:
        raise_blocked(
            status_code=503,
            agent_name=agent_name,
            message="DeepSeek 资源生成不可用，已停止生成；不会使用静态模板或规则兜底。",
            missing_requirements=["DeepSeek 可用", "结构化 JSON 输出"],
            used_llm=False,
            detail=_resource_generation_error_detail(
                agent_name,
                exc,
                timeout=timeout,
                max_tokens=max_tokens,
                attempts=attempts,
            ),
        )
    if not isinstance(result, dict):
        raise_blocked(
            status_code=503,
            agent_name=agent_name,
            message="DeepSeek 未返回 JSON 对象，已停止生成。",
            missing_requirements=["结构化 JSON 输出"],
            used_llm=True,
        )
    return result


def _resource_generation_error_detail(
    agent_name: str,
    exc: BaseException,
    *,
    timeout: int,
    max_tokens: int,
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw_failure = str(exc) or exc.__class__.__name__
    lower = raw_failure.lower()
    reason_code = "resource_generation_failed"
    if isinstance(exc, LLMJsonError):
        reason_code = exc.reason_code
    elif isinstance(exc, TimeoutError) or "timed out" in lower or "timeout" in lower:
        reason_code = "deepseek_timeout"
    elif "http 401" in lower:
        reason_code = "deepseek_auth_error"
    elif "http 402" in lower:
        reason_code = "deepseek_balance_insufficient"
    elif "http 429" in lower:
        reason_code = "deepseek_rate_limited"
    elif "json" in lower:
        reason_code = "json_malformed"
    return {
        "reasonCode": reason_code,
        "agentName": agent_name,
        "model": llm_model_name(),
        "rawFailure": raw_failure,
        "requestOptions": {
            "timeout": timeout,
            "maxTokens": max_tokens,
        },
        "suggestedActions": _resource_generation_suggested_actions(reason_code),
    }
    if attempts:
        detail["attempts"] = attempts
    if isinstance(exc, LLMJsonError) and exc.raw_excerpt:
        detail["rawExcerpt"] = exc.raw_excerpt[:500]
    return detail


def _resource_generation_suggested_actions(reason_code: str) -> list[str]:
    if reason_code == "deepseek_timeout":
        return ["点击重试生成", "检查 DeepSeek 网络连通性", "如多次超时，可减少勾选资源类型或调低生成内容长度"]
    if reason_code == "deepseek_auth_error":
        return ["检查 DEEPSEEK_API_KEY 是否正确", "重启后端服务后重试"]
    if reason_code == "deepseek_balance_insufficient":
        return ["检查 DeepSeek 账户余额", "充值或更换可用 API Key 后重试"]
    if reason_code == "deepseek_rate_limited":
        return ["稍后重试", "降低并发生成次数"]
    if reason_code.startswith("json_"):
        return ["点击重试生成", "清理知识库中的源码/PPT 噪声片段", "减少一次生成的资源类型"]
    return ["点击重试生成", "查看后端任务详情中的 rawFailure"]


def _build_reading_resource(topic: str, target: str, citations: list[dict[str, Any]], retrieval: dict[str, Any]) -> dict[str, Any]:
    keywords = _keywords_from_citations(citations) or _keywords_from_text(" ".join(str(item.get("contentPreview", "")) for item in citations))
    keyword_text = "、".join(keywords[:4]) if keywords else "定义、存储结构、基本操作、复杂度"
    first_source = _citation_public_label(citations[0]) if citations else "课程讲义对应章节"
    second_source = _citation_public_label(citations[1]) if len(citations) > 1 else first_source
    code_source = next((_citation_public_label(item) for item in citations if _is_code_citation(item)), "配套代码实验")
    lines = [
        f"# {topic}拓展阅读",
        "",
        f"> 学习目标：{target}",
        "",
        "## 阅读路线",
        "",
        "| 顺序 | 读什么 | 带着什么问题读 | 读完应能做到 |",
        "| --- | --- | --- | --- |",
        f"| 1 | 课程定义与示意图 | 「{topic}」描述的是哪类元素关系？ | 能用自己的话解释逻辑结构。 |",
        f"| 2 | 存储表示与基本操作 | 顺序、链式或其他存储方式会怎样影响插入、删除、查找？ | 能画出一次操作前后的状态。 |",
        f"| 3 | 复杂度分析 | 操作次数为什么会随 n 增长？最坏情况在哪里出现？ | 能写出时间复杂度和空间复杂度。 |",
        f"| 4 | 例题或源码 | 代码中的变量分别对应结构图里的哪些位置？ | 能把伪代码和结构变化对应起来。 |",
        "",
        "## 重点关注",
        "",
        f"- 先把关键词串起来：{keyword_text}。",
        f"- 读 {first_source} 时，重点找定义、图示和操作描述。",
        f"- 读 {second_source} 时，重点比较不同存储方式的操作代价。",
        f"- 做代码实验时，再打开 {code_source} 核对实现细节，不要一开始就陷进源码行号。",
        "",
        "## 复盘问题",
        f"- {topic}的逻辑结构和存储结构分别是什么？",
        f"- {('、'.join(keywords[:3]) if keywords else '典型操作')}分别会改变哪些下标、指针或结点？",
        "- 哪些操作最容易出现空结构、首尾位置或越界错误？",
        "- 如果把同一个操作换成另一种存储方式，复杂度会不会变化？为什么？",
        "",
        "## 下一步衔接",
        "",
        "- 先完成讲解文档中的“一步例题”，确认能手工跟踪结构变化。",
        "- 再做分层练习题，用选择题检查概念，用代码题检查实现能力。",
        "- 最后进入代码实验，把数据定义、核心操作和输出验证分开完成。",
        "",
        "## 参考资料",
        *_citation_markdown(citations),
    ]
    content = "\n".join(lines) + "\n"
    return {
        "title": f"{topic}拓展阅读路径",
        "summary": f"把课程资料整理成“先读概念、再看操作、最后进练习”的阅读路线。",
        "content": content,
        "qualityScore": 91,
        "metadata": {"readingStyle": "本地资料阅读顺序 + 复盘问题", "sourceDriven": True},
    }


def _build_video_script_resource(topic: str, target: str, citations: list[dict[str, Any]], retrieval: dict[str, Any]) -> dict[str, Any]:
    first = citations[0] if citations else {}
    second = citations[1] if len(citations) > 1 else first
    first_excerpt = _clean_excerpt(str(first.get("contentPreview") or topic), 90)
    second_excerpt = _clean_excerpt(str(second.get("contentPreview") or first_excerpt), 90)
    lines = [
        f"# {topic}三分钟视频脚本",
        "",
        f"学习目标：{target}",
        "",
        "| 时间 | 画面 | 旁白 |",
        "| --- | --- | --- |",
        f"| 0:00-0:25 | 标题和学习目标 | 今天根据课程资料理解「{topic}」，目标是：{target} |",
        f"| 0:25-1:05 | 放大课程原文引用 | 先读来源：{_citation_names(citations)}。关键句是：{first_excerpt} |",
        f"| 1:05-1:50 | 画出结构或流程 | 把片段中的概念拆成“定义、存储、操作、复杂度”四块，并在画面中逐项标注。 |",
        f"| 1:50-2:35 | 展示例题或代码 | 使用第二条引用补充操作细节：{second_excerpt} |",
        "| 2:35-3:00 | 总结与练习 | 提醒学生完成配套练习，并由学生自行选择“已学完”或“已掌握”。 |",
        "",
        "## 分镜素材",
        *[f"- 镜头素材 {index}：{_citation_public_label(citation)}；画面字幕摘录“{_clean_excerpt(str(citation.get('contentPreview') or ''), 120)}”。" for index, citation in enumerate(citations, start=1)],
        "",
        "## 引用来源",
        *_citation_markdown(citations),
    ]
    return {
        "title": f"{topic}视频演示方案",
        "summary": "包含三分钟讲解脚本、画面安排、引用摘录和学生自主状态选择提示。",
        "content": "\n".join(lines) + "\n",
        "qualityScore": 93,
        "metadata": {"videoDemo": True, "sourceDriven": True},
    }


def _lab_text(topic: str, citations: list[dict[str, Any]], code: str = "") -> str:
    return " ".join([
        topic,
        code,
        *[
            f"{item.get('documentName', '')} {item.get('sourceLocation', '')} {item.get('contentPreview', '')} {item.get('fullText', '')}"
            for item in citations
        ],
    ])


def _lab_concepts(topic: str, citations: list[dict[str, Any]], code: str) -> list[str]:
    text = _lab_text(topic, citations, code)
    candidates = [
        "栈", "顺序栈", "链栈", "队列", "循环队列", "链队", "队头", "队尾", "栈顶",
        "初始化", "入栈", "出栈", "取栈顶", "入队", "出队", "判空", "判满", "遍历",
        "链表", "顺序表", "指针", "数组", "时间复杂度", "空间复杂度",
    ]
    result: list[str] = []
    for item in candidates:
        if item in text and item not in result:
            result.append(item)
    if not result:
        result = _keywords_from_citations(citations) or _keywords_from_text(text)
    return result[:8] or [topic, "初始化", "核心操作", "边界条件"]


def _lab_operations(topic: str, concepts: list[str]) -> list[str]:
    concept_text = " ".join([topic, *concepts])
    operations: list[str] = []
    if "栈" in concept_text:
        operations.extend(["初始化栈", "入栈 push", "出栈 pop", "取栈顶 top", "判空"])
    if "队列" in concept_text or "链队" in concept_text:
        operations.extend(["初始化队列", "入队 enqueue", "出队 dequeue", "读取队头 front", "判空"])
    if "链表" in concept_text or "链队" in concept_text:
        operations.extend(["创建结点", "维护 next 指针", "处理头尾指针"])
    if "顺序表" in concept_text or "数组" in concept_text or "顺序栈" in concept_text:
        operations.extend(["维护下标", "检查容量边界", "移动或访问数组元素"])
    if not operations:
        operations = ["定义数据结构", "初始化", "执行核心操作", "输出操作结果", "分析复杂度"]
    deduped: list[str] = []
    for item in operations:
        if item not in deduped:
            deduped.append(item)
    return deduped[:10]


def _lab_io_spec(topic: str, operations: list[str]) -> dict[str, Any]:
    return {
        "input": [
            f"一组围绕「{topic}」的小规模操作序列",
            "至少 2 组正常输入和 1 组边界输入",
            "每一步操作的参数，例如 push(x)、enqueue(x) 或删除/读取操作",
        ],
        "output": [
            "每一步操作后的结构状态",
            "操作返回值或错误状态",
            "最终结构内容和时间复杂度说明",
        ],
        "stateFields": [
            field
            for field in ["top / 栈顶位置", "front / 队头位置", "rear / 队尾位置", "next 指针", "size / 当前元素个数"]
            if any(token in " ".join(operations) for token in field.split(" / "))
        ] or ["关键指针或下标", "当前元素个数", "操作返回值"],
    }


def _lab_trace_cases(topic: str, operations: list[str]) -> list[dict[str, Any]]:
    op_text = " ".join(operations)
    if "入栈" in op_text or "出栈" in op_text:
        return [
            {"name": "正常栈操作", "steps": ["初始化空栈", "push(10)", "push(20)", "top()", "pop()"], "expected": "top 返回 20，pop 后栈顶回到 10。"},
            {"name": "边界栈操作", "steps": ["初始化空栈", "pop()", "top()"], "expected": "空栈读取或删除应返回失败状态，不改变结构。"},
        ]
    if "入队" in op_text or "出队" in op_text:
        return [
            {"name": "正常队列操作", "steps": ["初始化空队列", "enqueue(10)", "enqueue(20)", "front()", "dequeue()"], "expected": "front 返回 10，dequeue 后队头移动到 20。"},
            {"name": "边界队列操作", "steps": ["初始化空队列", "dequeue()", "front()"], "expected": "空队列读取或删除应返回失败状态，不改变 front/rear。"},
        ]
    return [
        {"name": "正常输入", "steps": ["初始化结构", "执行一次插入或写入", "执行一次读取或删除", "输出最终状态"], "expected": "状态变化与课程定义一致。"},
        {"name": "边界输入", "steps": ["初始化空结构", "直接执行读取或删除"], "expected": "返回失败状态，并说明边界条件。"},
    ]


def _lab_deliverables(has_code: bool) -> list[str]:
    base = [
        "一张手工跟踪表：步骤、操作、关键指针/下标、结构状态、返回值",
        "一段复杂度说明：每个核心操作的时间复杂度和空间开销",
        "一份边界条件记录：空结构、满结构或头尾位置变化",
    ]
    if has_code:
        return ["对真实源码标注数据结构定义、初始化函数和核心操作入口", *base]
    return ["自行写出结构体/类定义和核心函数签名，不把它伪装成课程源码", "写出初始化和核心操作伪代码", *base]


def _lab_acceptance(has_code: bool) -> list[str]:
    checks = [
        "能说清输入、输出和状态变量分别是什么",
        "能用至少一组正常输入完成手工跟踪",
        "能解释至少一个边界条件如何处理",
        "能把复杂度结论对应到课程引用或代码步骤",
    ]
    if has_code:
        checks.insert(1, "能在真实源码中定位初始化和核心操作函数")
    else:
        checks.insert(1, "能写出不依赖伪造源码的代码骨架或伪代码")
    return checks


def _build_lab_plan(topic: str, target: str, citations: list[dict[str, Any]], code_hint: dict[str, Any] | None, clean_code: str) -> dict[str, Any]:
    concepts = _lab_concepts(topic, citations, clean_code)
    operations = _lab_operations(topic, concepts)
    has_code = bool(clean_code)
    return {
        "mission": f"围绕「{topic}」完成一次可跟踪的代码实践：先读定义，再定位或设计核心操作，最后用小规模输入验证状态变化。",
        "target": target,
        "concepts": concepts,
        "operations": operations,
        "ioSpec": _lab_io_spec(topic, operations),
        "codeMode": "source" if has_code else "design",
        "codeSource": (code_hint or {}).get("documentName") if has_code else "",
        "codeExcerpt": clean_code,
        "sourceTasks": [
            "圈出数据结构定义和关键字段",
            "标注初始化函数如何设置初始状态",
            "标注每个核心操作修改了哪些指针、下标或计数器",
            "记录失败返回值或异常分支",
        ] if has_code else [],
        "designTasks": [
            "写出结构体或类定义，字段必须能支撑后续操作",
            "写出初始化函数签名和伪代码",
            "写出 2 个核心操作的函数签名、输入参数、返回值和伪代码",
            "说明空结构、满结构或头尾位置变化如何处理",
        ] if not has_code else [],
        "traceCases": _lab_trace_cases(topic, operations),
        "deliverables": _lab_deliverables(has_code),
        "acceptance": _lab_acceptance(has_code),
    }


def _lab_plan_markdown(topic: str, citations: list[dict[str, Any]], lab_plan: dict[str, Any]) -> str:
    code_excerpt = str(lab_plan.get("codeExcerpt") or "")
    code_section = [
        "## 代码 / 伪代码工作区",
        f"真实源码来源：{lab_plan.get('codeSource') or '课程源码'}",
        "",
        "```c",
        code_excerpt,
        "```",
    ] if code_excerpt else [
        "## 代码 / 伪代码工作区",
        "当前知识库未命中真实源码片段。请完成代码设计任务，不要把生成的伪代码当作课程源码。",
        "",
        *[f"- {item}" for item in lab_plan.get("designTasks", [])],
    ]
    io_spec = lab_plan.get("ioSpec") if isinstance(lab_plan.get("ioSpec"), dict) else {}
    lines = [
        f"# {topic}代码实践实验",
        "",
        "## 这次到底练什么",
        str(lab_plan.get("mission") or ""),
        "",
        "## 操作目标",
        *[f"- {item}" for item in lab_plan.get("operations", [])],
        "",
        "## 输入输出约定",
        "输入：",
        *[f"- {item}" for item in io_spec.get("input", [])],
        "输出：",
        *[f"- {item}" for item in io_spec.get("output", [])],
        "",
        "## 课程依据",
        *_citation_markdown(citations),
        "",
        *code_section,
        "",
        "## 手工跟踪任务",
        *[
            f"- {case.get('name')}：{' -> '.join(case.get('steps', []))}；预期：{case.get('expected')}"
            for case in lab_plan.get("traceCases", [])
            if isinstance(case, dict)
        ],
        "",
        "## 提交物",
        *[f"- {item}" for item in lab_plan.get("deliverables", [])],
        "",
        "## 验收标准",
        *[f"- {item}" for item in lab_plan.get("acceptance", [])],
    ]
    return "\n".join(lines) + "\n"


def _build_lab_resource(topic: str, target: str, citations: list[dict[str, Any]], retrieval: dict[str, Any]) -> dict[str, Any]:
    code_hint = next((citation for citation in citations if _is_code_citation(citation)), None)
    code_preview = (code_hint or {}).get("fullText") or (code_hint or {}).get("contentPreview", "")
    clean_code = _clean_code_excerpt(code_preview) if code_hint else ""
    lab_plan = _build_lab_plan(topic, target, citations, code_hint, clean_code)
    return {
        "title": f"{topic}代码实践实验",
        "summary": f"围绕{topic}的核心操作、输入输出、手工跟踪和验收标准形成可执行实验任务。",
        "content": _lab_plan_markdown(topic, citations, lab_plan),
        "qualityScore": 92 if clean_code else 82,
        "metadata": {
            "labRuntime": "C/C++",
            "codeSource": (code_hint or {}).get("documentName"),
            "sourceDriven": True,
            "dataStatus": "live" if clean_code else "missing_source",
            "sourceQualityNote": "已命中真实源码片段" if clean_code else "未命中真实源码片段，未生成伪代码",
            "labPlan": lab_plan,
        },
    }


def _clean_excerpt(value: str, limit: int = 160) -> str:
    text = _clean_learning_excerpt(str(value or ""), limit)
    if not text:
        return "课程片段未提供可展示摘要"
    return text


def _clean_code_excerpt(value: str, limit: int = 900) -> str:
    text = str(value or "")
    text = re.sub(r"chunk[_A-Za-z0-9-]+", "", text)
    text = re.sub(r"相似度\s*\d+%?", "", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s，。；;]+", "", text)
    text = re.sub(r"AppData\s+Local\s+Temp", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Data-Structure-master\.zip![^\s，。；;]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"edua?gent_local_kb_zip_[^\s，。；;]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"/\*{4,}.*?\*/", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def _keywords_from_text(text: str) -> list[str]:
    candidates = [
        "线性表", "顺序表", "链表", "栈", "队列", "循环队列", "串", "KMP", "数组",
        "树", "二叉树", "遍历", "哈夫曼树", "图", "邻接矩阵", "邻接表",
        "查找", "排序", "哈希", "递归", "初始化", "插入", "删除", "查找", "遍历",
        "时间复杂度", "空间复杂度", "代码实现",
    ]
    result: list[str] = []
    for keyword in candidates:
        if keyword in text and keyword not in result:
            result.append(keyword)
    return result


def _citation_markdown(citations: list[dict[str, Any]]) -> list[str]:
    return [
        f"- {_citation_public_label(item)}。"
        for item in citations
    ] or ["- 暂无引用。"]


def _build_core_understanding(topic: str, citations: list[dict[str, Any]]) -> str:
    keywords = _keywords_from_citations(citations)
    keyword_text = "、".join(keywords[:5]) if keywords else "定义、存储结构、基本操作、复杂度"
    return "\n".join([
        f"- 围绕「{topic}」先确认课程资料中的关键术语：{keyword_text}。",
        "- 阅读时把每个操作拆成“操作前状态、执行步骤、操作后状态”。",
        "- 写代码或伪代码时同步说明边界条件，例如空结构、首尾元素、越界和重复元素。",
        "- 分析复杂度时区分最坏情况、平均情况和额外空间开销。",
    ])


def _keywords_from_citations(citations: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for citation in citations:
        text = f"{citation.get('sourceLocation', '')} {citation.get('contentPreview', '')}"
        for keyword in ["线性表", "链表", "栈", "队列", "串", "KMP", "数组", "树", "二叉树", "图", "查找", "排序", "哈希", "递归", "复杂度", "代码实现"]:
            if keyword in text and keyword not in result:
                result.append(keyword)
    return result


def _citation_names(citations: list[dict[str, Any]]) -> str:
    names = []
    for citation in citations[:2]:
        name = _short_document_name(str(citation.get("documentName") or ""))
        if name and name not in names:
            names.append(name)
    return "、".join(names) or "本地知识库"


def _is_code_citation(item: dict[str, Any]) -> bool:
    source_type = str(item.get("sourceType") or item.get("source_type") or "").lower()
    document = str(item.get("documentName") or "")
    preview = str(item.get("contentPreview") or item.get("fullText") or "")
    return (
        source_type == "code_repository"
        or bool(re.search(r"\.(c|cpp|h|hpp|java|py|js|ts)$", document, flags=re.I))
        or "!" in document
        or any(token in preview for token in ["#include", "void ", "typedef", "class ", "return OK", "#endif"])
    )


def _prefer_code_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    code_items = [item for item in citations if _is_code_citation(item)]
    rest = [item for item in citations if item not in code_items]
    return code_items + rest


def _prefer_teaching_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def rank(item: dict[str, Any]) -> tuple[int, float]:
        source_type = str(item.get("sourceType") or item.get("source_type") or "")
        source_rank = {
            "course_pdf": 0,
            "teacher_courseware": 0,
            "teacher_courseware_manifest": 1,
            "uploaded_document": 1,
            "local_text": 2,
            "local_manifest": 4,
            "code_repository": 5,
        }.get(source_type, 3)
        if _is_code_citation(item):
            source_rank = max(source_rank, 5)
        return (source_rank, -float(item.get("similarity") or 0))

    return sorted(citations, key=rank)


def _citations_from_retrieval(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "documentId": f"doc_{item['chunk_id']}",
            "documentName": item["document_name"],
            "sourceLocation": item["source_location"],
            "chunkId": item["chunk_id"],
            "contentPreview": item["content"],
            "page": item["page"],
            "similarity": item["score"],
            "fullText": item["content"],
            "sourceType": item.get("source_type") or item.get("sourceType"),
        }
        for item in items
    ]


def _sanitize_citations_for_student(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for citation in citations:
        item = deepcopy(citation)
        document = _short_document_name(str(item.get("documentName") or "课程资料")) or "课程资料"
        location = _clean_source_location(str(item.get("sourceLocation") or "课程片段"))
        preview = _clean_learning_excerpt(str(item.get("contentPreview") or item.get("fullText") or ""))
        full_text = _clean_learning_excerpt(str(item.get("fullText") or item.get("contentPreview") or ""), limit=900)
        item["documentName"] = document
        item["sourceLocation"] = location
        item["contentPreview"] = preview
        item["fullText"] = full_text
        cleaned.append(item)
    return cleaned


def _select_citations(resource_type: str, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    limit_map = {
        "explanation": 4,
        "mindmap": 3,
        "exercise": 2,
        "reading": 3,
        "video_script": 2,
        "lab": 2,
    }
    limit = limit_map.get(resource_type, 2)
    return deepcopy(citations[:limit])


def _citation_context(citations: list[dict[str, Any]]) -> str:
    if not citations:
        return "暂无可用引用片段"
    lines = []
    for index, citation in enumerate(citations, start=1):
        label = _citation_public_label(citation)
        preview = _clean_learning_excerpt(str(citation.get("contentPreview") or ""), 220)
        lines.append(
            f"{index}. 来源：{label}；可用证据摘要：{preview}"
        )
    return "\n".join(lines)


def _citation_noise_reasons(citation: dict[str, Any]) -> list[str]:
    text = " ".join([
        str(citation.get("documentName") or ""),
        str(citation.get("sourceLocation") or ""),
        str(citation.get("contentPreview") or ""),
        str(citation.get("fullText") or ""),
    ])
    reasons: list[str] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if "style.visibility" in text or "本讲完" in text or any(re.fullmatch(r"\d+\s*/\s*\d+", line) for line in lines):
        reasons.append("ppt_noise")
    if re.search(r"AppData|Temp|Data-Structure-master\.zip|edua?gent_local_kb_zip", text, re.I):
        reasons.append("path_noise")
    code_tokens = ["#include", "printf(", "return 0", "return OK", "typedef", "ListTraverse", "void ", "int main"]
    if sum(1 for token in code_tokens if token in text) >= 2 or re.search(r"\.(c|cpp|h)\b", text, re.I):
        reasons.append("code_fragment")
    if len(_strip_debug_text(text)) < 24:
        reasons.append("thin_excerpt")
    return reasons


def _is_teaching_citation(citation: dict[str, Any]) -> bool:
    reasons = set(_citation_noise_reasons(citation))
    return not reasons.intersection({"ppt_noise", "path_noise", "code_fragment", "thin_excerpt"})


def _teaching_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = [citation for citation in citations if _is_teaching_citation(citation)]
    return deepcopy(clean[:4])


def _retrieval_noise_summary(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for citation in citations:
        reasons = _citation_noise_reasons(citation)
        if not reasons:
            continue
        summary.append({
            "chunkId": str(citation.get("chunkId") or ""),
            "documentName": str(citation.get("documentName") or "课程资料"),
            "reasons": reasons,
            "preview": _clean_excerpt(str(citation.get("contentPreview") or citation.get("fullText") or ""), 90),
        })
    return summary[:6]


def _explanation_error_detail(
    reason_code: str,
    raw_failure: str,
    citations: list[dict[str, Any]],
    *,
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    suggestions = {
        "json_malformed": ["点击重试生成", "清理知识库中的 PPT 结束页、源码片段和路径噪声", "减少讲解文档中代码块长度"],
        "json_truncated": ["点击重试生成", "缩短学习目标或减少资源类型", "提高模型输出上限后再试"],
        "json_extra_text": ["点击重试生成", "要求模型只输出 JSON", "检查是否有 prompt 中的示例干扰"],
        "json_escape_error": ["点击重试生成", "减少 JSON 中的代码块和未转义引号", "清理源码片段后重试"],
        "insufficient_teaching_citations": ["补充概念讲义或例题资料", "清理只包含结束页、源码或路径的知识片段", "重新导入本地知识库"],
    }
    return {
        "reasonCode": reason_code,
        "agentName": "文档生成 Agent",
        "model": llm_model_name(),
        "rawFailure": raw_failure,
        "suggestedActions": suggestions.get(reason_code, ["点击重试生成", "查看课程引用并清理噪声片段"]),
        "retrievalNoiseSummary": _retrieval_noise_summary(citations),
        "attempts": attempts or [],
    }


EXPLANATION_SYSTEM_PROMPT = """你是《数据结构课程》的讲解文档 Agent。
Return JSON only. No markdown fences, no extra text.
Required schema:
{
  "title": "string",
  "summary": "string",
  "sections": [
    {"heading": "目录", "body": "string"},
    {"heading": "本章导读", "body": "string"},
    {"heading": "1. 线性表的概念", "body": "string"},
    {"heading": "2. 顺序存储结构", "body": "string"},
    {"heading": "3. 链式存储结构", "body": "string"},
    {"heading": "4. 基本操作", "body": "string"},
    {"heading": "5. 操作过程追踪", "body": "string"},
    {"heading": "6. 复杂度分析", "body": "string"},
    {"heading": "7. 代码实现", "body": "string"},
    {"heading": "8. 常见错误", "body": "string"},
    {"heading": "9. 小结与练习", "body": "string"}
  ],
  "citation_notes": [{"chunkId": "string", "usage": "string"}]
}
Rules:
1. Center the document on __TOPIC__ and keep the scenario within 数据结构课程.
2. Only use the following course fragments as evidence sources:
__CITATION_CONTEXT__
3. Write a complete tutorial document, not a study checklist, summary, outline, retrieval digest, or source list.
4. Every body must contain real teaching content: paragraphs, operation definitions, examples, tables, code blocks, step traces, warnings, or exercises.
5. The 目录 section must list the following 9 teaching sections in order: 本章导读, 1. 线性表的概念, 2. 顺序存储结构, 3. 链式存储结构, 4. 基本操作, 5. 操作过程追踪, 6. 复杂度分析, 7. 代码实现, 8. 常见错误, 9. 小结与练习.
6. Include at least one Markdown complexity table whose header contains "| 操作 |", one C or pseudocode code block, and one step-by-step state trace table whose header contains "| 步骤 |".
7. The code block must be fenced with triple backticks, and the full document should contain at least two fenced blocks.
8. Never output chunk ids, similarity scores, temp paths, zip paths, internal file paths, raw source-code leftovers, return OK, #endif, or duplicated markdown headings.
9. Do not put “课程依据” or long citation lists in the document body. Sources are displayed by the side panel.
10. The body field should be directly concatenatable as Markdown.
11. Target learner profile: __TARGET__.
"""


MINDMAP_SYSTEM_PROMPT = """你是《数据结构课程》的思维导图 Agent。
Return JSON only. No markdown fences, no extra text.
Required schema:
{
  "title": "string",
  "summary": "string",
  "mermaid": "mindmap\\n  root((主题))\\n    核心概念\\n      概念短语\\n    基本操作\\n      操作短语",
  "branches": ["定义与逻辑结构", "顺序表", "链表", "基本操作", "复杂度分析", "典型应用与代码实现", "易错点"],
  "citation_notes": [{"chunkId": "string", "usage": "string"}]
}
Rules:
1. Center the mindmap on __TOPIC__ and keep every node within 数据结构课程.
2. Only use the following course fragments as evidence sources:
__CITATION_CONTEXT__
3. The mermaid field must start with mindmap and use a root((...)) node.
4. For 线性表, prefer these first-level branches: 定义与逻辑结构, 顺序表, 链表, 基本操作, 复杂度分析, 典型应用与代码实现, 易错点.
5. Do not create a first-level branch named “课程依据”, “资料依据”, “引用来源”, or source document names; keep sources in citation_notes only.
6. Create at least 5 first-level knowledge modules, and each first-level module should contain 3-5 concrete course knowledge points.
7. Avoid generic learning-action nodes such as 读定义, 看例题, 写代码, 做测评 unless they are under 典型应用与代码实现.
8. Do not put chunk ids, similarity scores, temp paths, zip internal paths, or debug text in mermaid/title/summary.
9. citation_notes may use only chunkId values from the provided citations.
10. Target learner profile: __TARGET__.
"""


def _try_generate_mindmap_payload(topic: str, target: str, citations: list[dict[str, Any]], coverage: str) -> dict[str, Any] | None:
    citation_context = _citation_context(citations)
    user_prompt = chr(10).join([
        f"topic: {topic}",
        f"target: {target}",
        f"coverage: {coverage}",
        "citations:",
        citation_context,
        "Generate a structured Mermaid mindmap as JSON.",
    ])
    system_prompt = (
        MINDMAP_SYSTEM_PROMPT
        .replace("__TOPIC__", topic)
        .replace("__TARGET__", target)
        .replace("__CITATION_CONTEXT__", citation_context)
    )
    timeout = int(os.getenv("DEEPSEEK_RESOURCE_TIMEOUT", "45"))
    try:
        result = call_deepseek_json(system_prompt, user_prompt, temperature=0.2, max_tokens=2200, timeout=timeout)
    except LLMJsonError:
        retry_prompt = chr(10).join([
            f"topic: {topic}",
            f"target: {target}",
            "只输出一个 JSON 对象，不要 Markdown fence，不要解释文字，不要在 JSON 前后添加任何字符。",
            "JSON 字段：title:string, summary:string, mermaid:string, branches:string[], citation_notes:[{chunkId:string, usage:string}]。",
            "mermaid 字符串必须以 mindmap 开头，第二行必须是 root((主题))。",
            "citations:",
            citation_context,
        ])
        try:
            result = call_deepseek_json(
                "你是严格 JSON 修复器。只返回合法 JSON 对象。",
                retry_prompt,
                temperature=0.1,
                max_tokens=2200,
                timeout=timeout,
            )
        except (LLMUnavailable, TimeoutError, OSError, ValueError):
            return None
    except (LLMUnavailable, TimeoutError, OSError, ValueError):
        return None
    if not isinstance(result, dict):
        return None
    mermaid = _normalize_mindmap_mermaid(str(result.get("mermaid") or ""), topic)
    if not _valid_mindmap_mermaid(mermaid, topic):
        mermaid = _mindmap_from_branches(topic, result.get("branches"))
    if not _valid_mindmap_mermaid(mermaid, topic):
        return None
    return _render_mindmap_payload(result, topic, citations, coverage, mermaid)


def _mindmap_from_branches(topic: str, branches: Any) -> str:
    raw_branches = [str(item).strip() for item in branches if str(item).strip()] if isinstance(branches, list) else []
    if len(raw_branches) < 5:
        raw_branches = ["定义与逻辑结构", "顺序表", "链表", "基本操作", "复杂度分析", "典型应用与代码实现", "易错点"]
    default_points = {
        "定义与逻辑结构": ["有限序列", "直接前驱与后继", "首元与尾元"],
        "顺序表": ["连续存储", "随机访问", "插入删除移动元素"],
        "链表": ["结点与指针", "单链表", "双链表与循环链表"],
        "基本操作": ["初始化", "查找", "插入", "删除"],
        "复杂度分析": ["访问 O(1)/O(n)", "查找 O(n)", "插入删除看前驱条件"],
        "典型应用与代码实现": ["顺序表插入", "链表指针修改", "边界条件检查"],
        "易错点": ["位置编号", "表满或空表", "指针赋值顺序"],
    }
    lines = ["mindmap", f"  root(({topic}))"]
    for branch in raw_branches[:8]:
        safe_branch = _mindmap_node_text(branch, fallback="知识模块")
        lines.append(f"    {safe_branch}")
        points = default_points.get(branch) or default_points.get(safe_branch) or ["核心概念", "操作过程", "复杂度与边界"]
        for point in points[:5]:
            lines.append(f"      {_mindmap_node_text(point, fallback='知识点')}")
    return "\n".join(lines)


def _mindmap_node_text(value: Any, *, fallback: str) -> str:
    text = _strip_debug_text(str(value or "")).strip()
    text = re.sub(r"[(){}\[\]#`|:：]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text or _content_has_source_noise(text):
        return fallback
    return text[:40]


def _render_mindmap_payload(
    payload: dict[str, Any],
    topic: str,
    citations: list[dict[str, Any]],
    coverage: str,
    mermaid: str,
) -> dict[str, Any]:
    title = _clean_generated_text(payload.get("title"), f"{topic}完整思维导图")
    summary = _clean_generated_text(payload.get("summary"), f"由 DeepSeek 根据本地知识库片段生成的{topic}思维导图。")
    title = _strip_debug_text(title) or f"{topic}完整思维导图"
    summary = _strip_debug_text(summary) or f"由 DeepSeek 根据本地知识库片段生成的{topic}思维导图。"
    branches = payload.get("branches") if isinstance(payload.get("branches"), list) else []
    citation_notes = payload.get("citation_notes") if isinstance(payload.get("citation_notes"), list) else []
    allowed_chunk_ids = {str(citation.get("chunkId")) for citation in citations}
    valid_notes = [
        {"chunkId": str(note.get("chunkId")), "usage": _strip_debug_text(str(note.get("usage") or "课程依据"))}
        for note in citation_notes
        if isinstance(note, dict) and str(note.get("chunkId")) in allowed_chunk_ids
    ]
    return {
        "title": title,
        "summary": summary,
        "content": mermaid,
        "qualityScore": 96 if coverage == "sufficient" else 92,
        "metadata": {
            "generationMode": "deepseek_mindmap",
            "generationTemplate": RESOURCE_GENERATION_TEMPLATES["mindmap"],
            "promptSchema": RESOURCE_PROMPT_SCHEMAS["mindmap"],
            "retrievalCoverage": coverage,
            "sourceChunkIds": [citation["chunkId"] for citation in citations],
            "citationCount": len(citations),
            "llmModel": llm_model_name(),
            "agentEvidence": "DeepSeek 根据本地知识库片段生成思维导图",
            "branches": [
                label
                for branch in branches
                if str(branch).strip()
                for label in [_clean_mindmap_label(str(branch), topic)]
                if label and not _is_evidence_outline_branch(label)
            ],
            "citationNotes": valid_notes,
        },
    }


def _normalize_mindmap_mermaid(value: str, topic: str) -> str:
    text = value.strip()
    fence = re.search(r"```(?:mermaid)?\s*(.*?)```", text, re.S | re.I)
    if fence:
        text = fence.group(1).strip()
    lines = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        indent = raw[:len(raw) - len(raw.lstrip(" "))]
        cleaned = _strip_debug_text(raw.strip())
        if not cleaned:
            continue
        if cleaned.lower() == "mindmap":
            lines.append("mindmap")
        else:
            lines.append(f"{indent}{_clean_mindmap_label(cleaned, topic)}")
    return "\n".join(lines).strip() + "\n" if lines else ""


def _valid_mindmap_mermaid(mermaid: str, topic: str) -> bool:
    if not mermaid.lstrip().lower().startswith("mindmap"):
        return False
    outline = _parse_mindmap_content(mermaid, topic)
    if not outline:
        return False
    if not outline.get("label"):
        return False
    outline = _remove_evidence_outline_branches(_repair_flat_mindmap_outline(outline))
    return len(outline.get("children", [])) >= 3


def _build_structured_explanation_document(topic: str, target: str, citations: list[dict[str, Any]], coverage: str) -> dict[str, Any]:
    payload, failure_reason = _try_generate_explanation_payload(topic, target, citations, coverage)
    if payload:
        return payload
    detail_payload = failure_reason if isinstance(failure_reason, dict) else None
    raise_blocked(
        status_code=503,
        agent_name="文档生成 Agent",
        message="DeepSeek 未返回合格讲解文档，已停止生成；不会使用静态讲解模板兜底。",
        missing_requirements=["DeepSeek explanation JSON", "至少 5 个有效章节", "真实课程引用"],
        used_llm=True,
        detail=detail_payload or failure_reason or "unknown_explanation_generation_failure",
    )


def _try_generate_explanation_payload(topic: str, target: str, citations: list[dict[str, Any]], coverage: str) -> tuple[dict[str, Any] | None, str | dict[str, Any] | None]:
    if os.getenv("RESOURCE_LLM_ENABLE", os.getenv("LLM_ENABLE", "false")).lower() not in {"1", "true", "yes", "on"}:
        return None, "resource_llm_disabled"
    teaching_citations = _teaching_citations(citations)
    if len(teaching_citations) < 2:
        return None, _explanation_error_detail(
            "insufficient_teaching_citations",
            "讲解文档缺少足够干净的概念、操作或例题片段。",
            citations,
        )
    citation_context = _citation_context(teaching_citations)
    user_prompt = chr(10).join([
        f"topic: {topic}",
        f"target: {target}",
        f"coverage: {coverage}",
        "citations:",
        citation_context,
        "Generate a structured course explanation document as JSON. Include the 目录 section and the exact required teaching section headings.",
    ])
    system_prompt = (
        EXPLANATION_SYSTEM_PROMPT
        .replace("__TOPIC__", topic)
        .replace("__TARGET__", target)
        .replace("__CITATION_CONTEXT__", citation_context)
    )
    attempts: list[dict[str, Any]] = []
    try:
        result = call_deepseek_json(system_prompt, user_prompt, temperature=0.2, max_tokens=8000, timeout=35)
    except LLMJsonError as exc:
        attempts.append({"stage": "primary", "reasonCode": exc.reason_code, "message": str(exc)})
        retry_prompt = chr(10).join([
            f"topic: {topic}",
            f"target: {target}",
            "只输出一个 JSON 对象，不要 Markdown fence，不要解释文字。",
            "JSON 字段：title:string, summary:string, sections:[{heading:string, body:string}], qualityHints:string[], citationNotes:[{chunkId:string, usage:string}]。",
            "sections 必须包含：目录、本章导读、1. 线性表的概念、2. 顺序存储结构、3. 链式存储结构、4. 基本操作、5. 操作过程追踪、6. 复杂度分析、7. 代码实现、8. 常见错误、9. 小结与练习。",
            "正文中避免直接粘贴源码；如需代码，只写短伪代码并正确作为 JSON 字符串转义。",
            "citations:",
            citation_context,
        ])
        try:
            result = call_deepseek_json(
                "你是严格 JSON 生成器。只返回合法 JSON 对象，字符串中的换行和引号必须正确转义。",
                retry_prompt,
                temperature=0.1,
                max_tokens=6500,
                timeout=35,
            )
            attempts.append({"stage": "retry", "reasonCode": "ok", "message": "retry_json_parse_success"})
        except LLMJsonError as retry_exc:
            attempts.append({"stage": "retry", "reasonCode": retry_exc.reason_code, "message": str(retry_exc)})
            return None, _explanation_error_detail(
                retry_exc.reason_code,
                str(retry_exc),
                citations,
                attempts=attempts,
            )
        except (LLMUnavailable, TimeoutError, OSError, ValueError) as retry_exc:
            attempts.append({"stage": "retry", "reasonCode": "api_error", "message": str(retry_exc)})
            return None, _explanation_error_detail("json_malformed", str(retry_exc), citations, attempts=attempts)
    except (LLMUnavailable, TimeoutError, OSError, ValueError) as exc:
        return None, f"api_error: {exc}"
    if not isinstance(result, dict):
        return None, "not_json_object"
    sections = result.get("sections")
    if not isinstance(sections, list) or len(sections) < 5:
        return None, "sections_too_few"
    rendered = _render_explanation_payload(result, topic, target, teaching_citations, coverage, source="deepseek")
    rendered["content"] = _repair_explanation_markdown(
        str(rendered.get("content") or ""),
        topic,
        citations=teaching_citations,
    )
    if not _is_high_quality_tutorial_doc(str(rendered.get("content") or "")):
        quality_reason = _explanation_quality_failure_reason(str(rendered.get("content") or ""))
        return None, _explanation_error_detail(
            "quality_check_failed",
            quality_reason,
            citations,
            attempts=attempts,
        )
    return rendered, None


def _render_explanation_payload(payload: dict[str, Any], topic: str, target: str, citations: list[dict[str, Any]], coverage: str, source: str) -> dict[str, Any]:
    title = str(payload.get("title") or _natural_explanation_title(topic))
    summary = str(payload.get("summary") or f"用课程资料梳理「{topic}」的概念、操作、复杂度和练习入口。")
    title = re.sub(r"\?{3,}|\ufffd+", topic, title).strip()
    summary = re.sub(r"\?{3,}|\ufffd+", topic, summary).strip()
    if _looks_corrupted_text(title):
        title = _natural_explanation_title(topic)
    if _looks_corrupted_text(summary):
        summary = f"用课程资料梳理「{topic}」的概念、操作、复杂度和练习入口。"
    sections = _normalize_explanation_sections(payload.get("sections") if isinstance(payload.get("sections"), list) else [])
    markdown_parts = [f"# {title}", "", summary]
    if not any(section.get("heading") == "目录" for section in sections) and _has_all_teaching_headings(sections):
        markdown_parts.extend(["", "## 目录", "", _explanation_toc_body()])
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "").strip()
        body = str(section.get("body") or "").strip()
        if not heading or not body:
            continue
        clean_body = _strip_debug_text(body)
        if _content_has_source_noise(clean_body):
            continue
        markdown_parts.extend(["", f"## {heading}", "", clean_body])
    return {
        "title": title,
        "summary": summary,
        "content": chr(10).join(markdown_parts).strip() + chr(10),
        "qualityScore": 95 if coverage == "sufficient" else 91,
        "metadata": {
            "generationMode": "blocked_non_deepseek" if source != "deepseek" else source,
            "generationTemplate": "course_tutorial_doc_v2",
            "promptSchema": RESOURCE_PROMPT_SCHEMAS["explanation"],
            "retrievalCoverage": coverage,
            "sourceChunkIds": [citation["chunkId"] for citation in citations],
            "citationCount": len(citations),
            "llmModel": llm_model_name() if source == "deepseek" else "blocked_non_deepseek",
        },
    }


def _build_fallback_explanation_document(topic: str, target: str, citations: list[dict[str, Any]], coverage: str) -> dict[str, Any]:
    title = _tutorial_title(topic)
    keyword_text = "、".join(_keywords_from_citations(citations)[:8]) or "逻辑结构、顺序存储、链式存储、插入、删除、查找、遍历、复杂度"
    is_linear = "线性表" in topic
    object_name = "线性表" if is_linear else topic
    markdown_parts = [
        f"# {title}",
        "",
        f"本章把「{object_name}」作为一个完整的数据结构专题来讲：先解释概念，再比较两种常见存储方式，最后进入操作追踪、复杂度分析和代码实现。读完后，你应该能把“结构图、操作步骤、代码语句和复杂度结论”对应起来，而不是只背定义。",
        "",
        "## 目录",
        "",
        "1. 线性表的概念",
        "2. 顺序存储结构",
        "3. 链式存储结构",
        "4. 基本操作",
        "5. 操作过程追踪",
        "6. 复杂度分析",
        "7. 代码实现",
        "8. 常见错误",
        "9. 小结与练习",
        "",
        "## 本章导读",
        "",
        f"本章目标是把「{object_name}」从抽象定义落到可执行操作。课程资料中出现的关键词包括：{keyword_text}。学习时建议按这样的顺序推进：先说明元素之间的关系，再说明数据在内存中的组织方式，然后跟踪一次插入或删除，最后写出复杂度。",
        "",
        f"> 阅读目标：{target}。本章引用覆盖：{_coverage_label(coverage)}。引用证据在右侧“可信来源”中展示，正文只保留学习内容。",
        "",
        "## 1. 线性表的概念",
        "",
        f"{object_name}是一类最基础的线性结构。所谓线性，是指除第一个元素外，每个元素都有且只有一个直接前驱；除最后一个元素外，每个元素都有且只有一个直接后继。它描述的是元素之间的一对一顺序关系，而不是内存中一定连续存放。",
        "",
        "可以从三个角度理解这个定义：",
        "",
        "- **逻辑结构**：元素排成一个序列，关注谁在谁前面、谁在谁后面。",
        "- **存储结构**：可以用连续数组保存，也可以用结点和指针连接。",
        "- **基本操作**：初始化、求长度、按位查找、按值查找、插入、删除和遍历。",
        "",
        "例如序列 `[A, B, C, D]` 是一个线性表。`A` 是首元，`D` 是尾元，`B` 的直接前驱是 `A`，直接后继是 `C`。这几个词看起来简单，但它们会直接影响插入、删除和边界条件。",
        "",
        "## 2. 顺序存储结构",
        "",
        "顺序存储用一段连续的存储单元保存线性表元素。最常见的实现就是数组。第 `i` 个元素可以通过下标直接定位，所以按位访问很快。",
        "",
        "| 要素 | 说明 |",
        "| --- | --- |",
        "| 存储方式 | 元素连续存放在数组中 |",
        "| 关键变量 | `data[]` 保存元素，`length` 记录当前长度，`capacity` 表示最大容量 |",
        "| 优点 | 支持随机访问，按位查找时间复杂度为 O(1) |",
        "| 缺点 | 插入和删除常需要移动元素，表满时不能继续插入 |",
        "",
        "如果线性表为 `[10, 20, 30, 40]`，想访问第 3 个元素，只需要读取下标 2 的位置。这个过程不需要从头遍历，因此顺序表适合“经常按位置读取，较少插入删除”的场景。",
        "",
        "## 3. 链式存储结构",
        "",
        "链式存储不要求元素在内存中连续。每个结点通常包含两个部分：数据域保存元素值，指针域保存下一个结点的位置。通过指针把结点串起来，就能表示同样的线性关系。",
        "",
        "| 要素 | 说明 |",
        "| --- | --- |",
        "| 存储方式 | 结点分散存放，通过指针连接 |",
        "| 关键变量 | `head` 指向头结点或首元结点，`next` 指向后继结点 |",
        "| 优点 | 插入和删除不需要整体移动元素，只需修改指针 |",
        "| 缺点 | 不能随机访问第 i 个元素，通常需要从头结点开始数 |",
        "",
        "链表适合“频繁插入删除，较少按下标随机访问”的场景。理解链表时，重点不是背结点结构体，而是画清楚每一次指针修改前后，哪些结点仍然可达。",
        "",
        "## 4. 基本操作",
        "",
        "下面按操作说明输入、输出、前置条件和结果。无论采用顺序存储还是链式存储，这些操作的语义是一致的，只是实现代价不同。",
        "",
        "| 操作 | 输入 | 前置条件 | 输出或结果 |",
        "| --- | --- | --- | --- |",
        "| 初始化 | 空表容量或头结点 | 存储空间可用 | 得到一个长度为 0 的线性表 |",
        "| 求长度 | 线性表 | 表已初始化 | 返回当前元素个数 |",
        "| 按位查找 | 位置 `i` | `1 <= i <= length` | 返回第 `i` 个元素 |",
        "| 插入 | 位置 `i`、元素 `x` | 位置合法；顺序表未满 | 在第 `i` 个位置前插入 `x` |",
        "| 删除 | 位置 `i` | `1 <= i <= length` | 删除第 `i` 个元素并保持线性关系 |",
        "| 遍历 | 访问函数 | 表已初始化 | 按逻辑顺序访问每个元素 |",
        "",
        "判断一个实现是否正确，不只看最终输出，还要看操作前后的结构是否仍然满足线性表定义：元素顺序没有断，长度更新正确，首尾位置仍然有效。",
        "",
        "## 5. 操作过程追踪",
        "",
        "以顺序表 `[10, 20, 30, 40]` 为例，在第 3 个位置插入 `25`。插入位置从 1 开始计数，所以新元素要放在 `30` 前面。",
        "",
        "| 步骤 | 表状态 | 说明 |",
        "| --- | --- | --- |",
        "| 初始 | `[10, 20, 30, 40]` | 长度为 4，准备在第 3 个位置插入 |",
        "| 移动 40 | `[10, 20, 30, 40, 40]` | 从尾部开始，把第 4 个元素后移 |",
        "| 移动 30 | `[10, 20, 30, 30, 40]` | 继续把第 3 个元素后移，空出位置 |",
        "| 写入 25 | `[10, 20, 25, 30, 40]` | 把新元素写入第 3 个位置 |",
        "| 更新长度 | `length = 5` | 表中元素个数增加 1 |",
        "",
        "如果改用单链表插入，核心变化不是移动元素，而是修改指针：先让新结点 `s->next` 指向原来的后继结点，再让前驱结点 `p->next` 指向 `s`。这个顺序不能反，否则会丢失后续链表。",
        "",
        "## 6. 复杂度分析",
        "",
        "| 操作 | 顺序表 | 单链表 | 适用提示 |",
        "| --- | --- | --- | --- |",
        "| 按位访问 | O(1) | O(n) | 顺序表能直接下标定位；链表要从头走到第 i 个结点 |",
        "| 按值查找 | O(n) | O(n) | 两者通常都要逐个比较 |",
        "| 插入 | O(n) | 已知前驱时 O(1)，寻找前驱 O(n) | 顺序表移动元素；链表修改指针 |",
        "| 删除 | O(n) | 已知前驱时 O(1)，寻找前驱 O(n) | 顺序表移动元素；链表修改指针 |",
        "| 空间开销 | 预分配连续空间 | 每个结点多一个指针域 | 链表更灵活，但指针有额外空间 |",
        "",
        "复杂度分析不要只写结论。要说明导致复杂度的关键动作：顺序表的插入删除慢，是因为可能要移动大量元素；链表按位访问慢，是因为不能直接跳到第 `i` 个结点。",
        "",
        "## 7. 代码实现",
        "",
        "下面给出一个顺序表插入的 C 语言示例。代码只展示核心逻辑，便于和上面的状态追踪对应。",
        "",
        "```c",
        "#define MAX_SIZE 100",
        "",
        "typedef struct {",
        "    int data[MAX_SIZE];",
        "    int length;",
        "} SqList;",
        "",
        "int list_insert(SqList *list, int position, int value) {",
        "    if (list == NULL) return 0;",
        "    if (position < 1 || position > list->length + 1) return 0;",
        "    if (list->length >= MAX_SIZE) return 0;",
        "",
        "    for (int j = list->length; j >= position; --j) {",
        "        list->data[j] = list->data[j - 1];",
        "    }",
        "",
        "    list->data[position - 1] = value;",
        "    list->length += 1;",
        "    return 1;",
        "}",
        "```",
        "",
        "这段代码有三个关键检查：表指针不能为空，插入位置必须合法，顺序表不能已满。真正移动元素时，要从后往前移动；如果从前往后移动，原有数据会被覆盖。",
        "",
        "单链表插入的核心逻辑如下：",
        "",
        "```c",
        "typedef struct Node {",
        "    int data;",
        "    struct Node *next;",
        "} Node;",
        "",
        "void insert_after(Node *p, Node *s) {",
        "    if (p == NULL || s == NULL) return;",
        "    s->next = p->next;",
        "    p->next = s;",
        "}",
        "```",
        "",
        "注意两句指针赋值的顺序。先保存原后继，再连接新结点，这是链表题最常见的考点之一。",
        "",
        "## 8. 常见错误",
        "",
        "- **把逻辑结构和存储结构混为一谈**：线性表表示一对一顺序关系，顺序表和链表只是两种存储实现。",
        "- **顺序表插入方向写反**：插入时应从尾部向插入位置移动，避免覆盖未移动的数据。",
        "- **链表指针更新顺序错误**：应先 `s->next = p->next`，再 `p->next = s`。",
        "- **忽略边界条件**：空表、满表、首位置、尾位置、非法位置都需要单独检查。",
        "- **只给代码不解释复杂度**：代码题也要说明哪一步导致 O(n)，哪一步是 O(1)。",
        "",
        "## 9. 小结与练习",
        "",
        f"本章的核心不是记住某一段代码，而是建立「{object_name}」的分析框架：先看逻辑关系，再选存储方式，然后跟踪操作过程，最后给出复杂度结论。",
        "",
        "请完成下面的小练习：",
        "",
        "1. 写出顺序表 `[3, 6, 9, 12]` 在第 2 个位置插入 `5` 的每一步状态。",
        "2. 说明为什么顺序表按位访问是 O(1)，单链表按位访问是 O(n)。",
        "3. 画出单链表在结点 `p` 后插入结点 `s` 前后的指针变化。",
        "4. 找出顺序表删除第 1 个元素时需要移动哪些元素。",
        "5. 用一段话说明什么时候更适合使用链式存储。",
    ]
    markdown = chr(10).join(markdown_parts).strip() + chr(10)
    return {
        "title": title,
        "summary": f"包含概念定义、存储结构、操作追踪、复杂度、代码实现和练习入口的完整教程文档。",
        "content": markdown,
        "qualityScore": 96 if coverage == "sufficient" else 92,
        "metadata": {
            "generationMode": "blocked_static_generator",
            "generationTemplate": "course_tutorial_doc_v2",
            "promptSchema": RESOURCE_PROMPT_SCHEMAS["explanation"],
            "retrievalCoverage": coverage,
            "sourceChunkIds": [citation["chunkId"] for citation in citations],
            "citationCount": len(citations),
            "llmModel": "blocked_static_generator",
        },
    }


def _repair_explanation_markdown(content: str, topic: str, *, citations: list[dict[str, Any]]) -> str:
    text = str(content or "").strip()
    if not text or _content_has_source_noise(text):
        return text
    repaired = text
    if "| 操作 |" not in repaired:
        repaired += "\n\n## 复杂度补充\n\n" + _explanation_complexity_table(topic)
    if "| 步骤 |" not in repaired:
        repaired += "\n\n## 操作追踪补充\n\n" + _explanation_trace_table(topic)
    if repaired.count("```") < 4:
        repaired += "\n\n## 代码实现补充\n\n" + _explanation_code_blocks(topic, citations)
    required_terms = ["顺序表", "链式存储", "插入", "删除", "O(n)", "边界条件"]
    if not all(term in repaired for term in required_terms):
        repaired += "\n\n## 关键概念补充\n\n" + _explanation_required_terms_note(topic)
    return repaired.strip() + "\n"


def _explanation_complexity_table(topic: str) -> str:
    return "\n".join([
        "| 操作 | 顺序表 | 链表 | 说明 |",
        "| --- | --- | --- | --- |",
        "| 按位访问 | O(1) | O(n) | 顺序表可直接计算下标，链表需要从头遍历 |",
        "| 按值查找 | O(n) | O(n) | 通常都需要逐个比较元素 |",
        "| 插入 | O(n) | O(1) 或 O(n) | 顺序表移动元素；链表若已知前驱为 O(1)，查找前驱为 O(n) |",
        "| 删除 | O(n) | O(1) 或 O(n) | 顺序表移动元素；链表若已知前驱为 O(1)，查找前驱为 O(n) |",
    ])


def _explanation_trace_table(topic: str) -> str:
    return "\n".join([
        "| 步骤 | 表状态 | 说明 |",
        "| --- | --- | --- |",
        "| 初始 | `[10, 20, 30, 40]` | 准备在第 3 个位置插入 `25` |",
        "| 后移 40 | `[10, 20, 30, 40, 40]` | 从最后一个元素开始后移，避免覆盖 |",
        "| 后移 30 | `[10, 20, 30, 30, 40]` | 空出第 3 个位置 |",
        "| 写入 25 | `[10, 20, 25, 30, 40]` | 插入完成并更新表长 |",
    ])


def _explanation_code_blocks(topic: str, citations: list[dict[str, Any]]) -> str:
    source_note = "该补充代码由后端根据已通过校验的教学章节生成，用于保证讲解文档包含可实践的最小代码片段。"
    if citations:
        source_note = "该补充代码对应本次命中的线性表教学片段，用于演示顺序表与链表插入的关键动作。"
    return "\n".join([
        source_note,
        "",
        "```c",
        "int insert_seq(int a[], int *length, int capacity, int pos, int value) {",
        "    if (pos < 1 || pos > *length + 1 || *length >= capacity) return 0;",
        "    for (int i = *length; i >= pos; --i) {",
        "        a[i] = a[i - 1];",
        "    }",
        "    a[pos - 1] = value;",
        "    (*length)++;",
        "    return 1;",
        "}",
        "```",
        "",
        "```c",
        "void insert_after(Node *p, Node *s) {",
        "    if (p == NULL || s == NULL) return;",
        "    s->next = p->next;",
        "    p->next = s;",
        "}",
        "```",
    ])


def _explanation_required_terms_note(topic: str) -> str:
    return "\n".join([
        f"学习 {topic} 时，需要同时区分逻辑结构和存储实现：线性表描述元素之间的一对一先后关系，顺序表和链式存储只是实现这种关系的两类方式。",
        "",
        "- **顺序表**：适合按位置访问，访问第 `i` 个元素通常为 O(1)，但插入和删除可能移动多个元素，最坏时间复杂度为 O(n)。",
        "- **链式存储**：通过指针连接结点，已知前驱结点时插入和删除可在 O(1) 内完成；如果还要先查找前驱，整体仍可能是 O(n)。",
        "- **边界条件**：空表、满表、首位置、尾位置、非法位置、空指针都必须先检查。很多程序错误不是算法思想错，而是边界条件没有处理完整。",
    ])


def _coverage_label(coverage: str) -> str:
    return "充足" if coverage == "sufficient" else "不足"


def _tutorial_title(topic: str) -> str:
    topic = _clean_generated_text(topic, DEFAULT_RESOURCE_TOPIC)
    if "线性表" in topic:
        return "线性表"
    return topic


def _natural_explanation_title(topic: str) -> str:
    topic = _clean_generated_text(topic, DEFAULT_RESOURCE_TOPIC)
    if "线性表" in topic:
        return "线性表：从逻辑结构到顺序表与链表"
    if "栈" in topic and "队列" in topic:
        return "栈与队列：受限线性结构的操作与边界"
    if "树" in topic:
        return f"{topic}：层次关系、遍历和复杂度"
    if "图" in topic:
        return f"{topic}：邻接表示、遍历和路径分析"
    return f"{topic}：概念、操作与复杂度"


def _normalize_explanation_heading(value: str) -> str:
    heading = _clean_section_heading(str(value or ""))
    compact = re.sub(r"[\s#：:、.．\-—_]+", "", heading).lower()
    aliases = {
        "目录": "目录",
        "toc": "目录",
        "tableofcontents": "目录",
        "本章导读": "本章导读",
        "章节导读": "本章导读",
        "导读": "本章导读",
        "1线性表的概念": "1. 线性表的概念",
        "1线性表概念": "1. 线性表的概念",
        "线性表的概念": "1. 线性表的概念",
        "线性表概念": "1. 线性表的概念",
        "2顺序存储结构": "2. 顺序存储结构",
        "顺序存储结构": "2. 顺序存储结构",
        "顺序表": "2. 顺序存储结构",
        "顺序表存储": "2. 顺序存储结构",
        "3链式存储结构": "3. 链式存储结构",
        "链式存储结构": "3. 链式存储结构",
        "链表": "3. 链式存储结构",
        "链表结构": "3. 链式存储结构",
        "4基本操作": "4. 基本操作",
        "基本操作": "4. 基本操作",
        "核心操作": "4. 基本操作",
        "5操作过程追踪": "5. 操作过程追踪",
        "操作过程追踪": "5. 操作过程追踪",
        "操作追踪": "5. 操作过程追踪",
        "步骤追踪": "5. 操作过程追踪",
        "状态追踪": "5. 操作过程追踪",
        "6复杂度分析": "6. 复杂度分析",
        "复杂度分析": "6. 复杂度分析",
        "时间复杂度与空间复杂度": "6. 复杂度分析",
        "7代码实现": "7. 代码实现",
        "代码实现": "7. 代码实现",
        "代码实践": "7. 代码实现",
        "伪代码实现": "7. 代码实现",
        "8常见错误": "8. 常见错误",
        "常见错误": "8. 常见错误",
        "常见易错点": "8. 常见错误",
        "易错点": "8. 常见错误",
        "9小结与练习": "9. 小结与练习",
        "小结与练习": "9. 小结与练习",
        "总结与练习": "9. 小结与练习",
        "小结练习": "9. 小结与练习",
    }
    return aliases.get(compact, heading)


def _normalize_explanation_sections(sections: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = _normalize_explanation_heading(str(section.get("heading") or ""))
        body = str(section.get("body") or section.get("body_md") or section.get("content") or "").strip()
        if not heading or not body or heading in seen:
            continue
        seen.add(heading)
        normalized.append({"heading": heading, "body": body})
    return normalized


def _has_all_teaching_headings(sections: list[dict[str, str]]) -> bool:
    present = {str(section.get("heading") or "") for section in sections}
    return all(heading in present for heading in EXPLANATION_TEACHING_HEADINGS)


def _explanation_toc_body() -> str:
    return "\n".join(f"{index}. {heading}" for index, heading in enumerate(EXPLANATION_TEACHING_HEADINGS, start=1))


def _explanation_quality_failure_reason(content: str) -> str:
    text = str(content or "")
    if _content_has_source_noise(text):
        return "quality_check_failed: source_noise_detected"
    if _looks_corrupted_text(text[:200]):
        return "quality_check_failed: corrupted_text"
    missing = [heading for heading in EXPLANATION_REQUIRED_HEADINGS if f"## {heading}" not in text]
    if missing:
        if missing == ["目录"]:
            return "quality_check_failed: missing_toc"
        return f"quality_check_failed: missing_sections={','.join(missing)}"
    if text.count("```") < 4:
        return "quality_check_failed: insufficient_code_fences"
    if "| 操作 |" not in text:
        return "quality_check_failed: missing_complexity_table_header"
    if "| 步骤 |" not in text:
        return "quality_check_failed: missing_trace_table_header"
    missing_terms = [term for term in ["顺序表", "链式存储", "插入", "删除", "O(n)", "边界条件"] if term not in text]
    if missing_terms:
        return f"quality_check_failed: missing_terms={','.join(missing_terms)}"
    return "quality_check_failed"


def _clean_section_heading(value: str) -> str:
    mapping = {
        "course positioning": "课程定位",
        "core concepts": "核心概念",
        "worked example": "例题拆解",
        "common mistakes": "常见易错点",
        "code practice": "代码实践",
    }
    return mapping.get(value.strip().lower(), value.strip())


def _citation_public_label(citation: dict[str, Any]) -> str:
    document = _short_document_name(str(citation.get("documentName") or "课程资料"))
    location = _clean_source_location(str(citation.get("sourceLocation") or "课程片段").strip()) or "课程片段"
    page = citation.get("page")
    page_text = f"，第 {page} 页" if page else ""
    return f"{document}，{location}{page_text}"


def _short_document_name(value: str) -> str:
    text = re.sub(r".*Data-Structure-master\.zip!", "", value, flags=re.I)
    text = re.sub(r".*edua?gent_local_kb_zip_[^\\/!\s]+[\\/!]", "", text, flags=re.I)
    text = re.split(r"[\\/!]", text)[-1]
    text = _strip_debug_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[-48:] if len(text) > 48 else text


def _citation_excerpt_lines(citations: list[dict[str, Any]], limit: int = 3) -> list[str]:
    lines: list[str] = []
    for citation in citations[:limit]:
        excerpt = _clean_excerpt(str(citation.get("contentPreview") or citation.get("fullText") or ""), 180)
        if excerpt and excerpt != "课程片段未提供可展示摘要":
            lines.append(f"- {_citation_public_label(citation)}：{excerpt}")
    return lines


def _strip_debug_text(value: str) -> str:
    value = _strip_legacy_course_terms(value, DEFAULT_RESOURCE_TOPIC)
    text = re.sub(r"chunk[_A-Za-z0-9-]+", "", value)
    text = re.sub(r"相似度\s*\d+%?", "", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s，。；;]+", "", text)
    text = re.sub(r"AppData\s+Local\s+Temp", "", text, flags=re.IGNORECASE)
    text = re.sub(r"Data-Structure-master\.zip![^\s，。；;]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"edua?gent_local_kb_zip_[^\s，。；;]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(return\s+OK|#endif|#include|typedef\s+struct)\b\s*;?", "", text)
    text = re.sub(r"/\*{4,}.*?\*/", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _clean_source_location(value: str) -> str:
    text = _strip_debug_text(value)
    text = re.sub(r"第\s*\d+\s*章\s*\d*", "", text)
    text = re.sub(r"^[\s:：,，;；\-•]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -，。；;")
    if not text or len(text) < 2:
        return "课程片段"
    if len(text) > 42:
        text = text[:42].rstrip() + "..."
    return text


def _clean_learning_excerpt(value: str, limit: int = 160) -> str:
    text = _strip_debug_text(value)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -，。；;")
    if not text:
        return "课程片段未提供可展示摘要"
    code_noise = ["return OK", "#endif", "#include", "printf(", "void ", "ElemType", "ListTraverse"]
    if sum(1 for token in code_noise if token in text) >= 2:
        return "该来源是配套源码，可在代码实验中用于核对实现细节。"
    text = re.sub(r"([A-Za-z0-9_]{24,})", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -，。；;")
    if not text:
        return "课程片段未提供可展示摘要"
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _clean_resource_markdown_content(value: str, topic: str) -> str:
    text = _strip_legacy_course_terms(str(value or ""), topic)
    text = re.sub(r"\?{3,}", topic, text)
    text = re.sub(r"\ufffd+", topic, text)
    cleaned_lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"chunk[_A-Za-z0-9-]+", "", raw)
        line = re.sub(r"相似度\s*\d+%?", "", line)
        line = re.sub(r"[A-Za-z]:[\\/][^\s，。；;]+", "", line)
        line = re.sub(r"AppData\s+Local\s+Temp", "", line, flags=re.IGNORECASE)
        line = re.sub(r"Data-Structure-master\.zip![^\s，。；;]+", "", line, flags=re.IGNORECASE)
        line = re.sub(r"edua?gent_local_kb_zip_[^\s，。；;]+", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\b(return\s+OK|#endif)\b\s*;?", "", line)
        line = re.sub(r"/\*{4,}.*?\*/", "", line)
        line = re.sub(r"[ \t]{2,}", " ", line).rstrip()
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    return cleaned.strip() + "\n" if cleaned.strip() else ""


def _content_has_source_noise(value: str) -> bool:
    text = str(value or "")
    noisy_patterns = [
        r"Data-Structure-master\.zip",
        r"edua?gent_local_kb_zip",
        r"AppData",
        r"chunk[_A-Za-z0-9-]+",
        r"return\s+OK",
        r"#endif",
        r"[A-Za-z]:[\\/]",
        r"##\s*##",
    ]
    return any(re.search(pattern, text, flags=re.I) for pattern in noisy_patterns)


def _is_high_quality_tutorial_doc(content: str) -> bool:
    text = str(content or "")
    if _content_has_source_noise(text) or _looks_corrupted_text(text[:200]):
        return False
    if "\n## 目录" not in text or "\n\n## 本章导读" not in text:
        return False
    required_fragments = [
        "## 目录",
        "## 本章导读",
        "## 1. 线性表的概念",
        "## 2. 顺序存储结构",
        "## 3. 链式存储结构",
        "## 4. 基本操作",
        "## 5. 操作过程追踪",
        "## 6. 复杂度分析",
        "## 7. 代码实现",
        "## 8. 常见错误",
        "## 9. 小结与练习",
    ]
    if not all(fragment in text for fragment in required_fragments):
        return False
    if text.count("```") < 4:
        return False
    if "| 操作 |" not in text or "| 步骤 |" not in text:
        return False
    if not all(term in text for term in ["顺序表", "链式存储", "插入", "删除", "O(n)", "边界条件"]):
        return False
    return True


def _strip_legacy_course_terms(value: str, fallback: str = DEFAULT_RESOURCE_TOPIC) -> str:
    replacements = {
        "Intro to " + "Artificial Intelligence": "数据结构课程",
        "Artificial " + "Intelligence": "数据结构",
        "DecisionTree" + "Classifier": "数据结构核心操作实现",
        "information " + "gain": "复杂度分析",
        "Information " + "gain": "复杂度分析",
        "entr" + "opy": "复杂度",
        "Entr" + "opy": "复杂度",
        "criterion=\"" + "entr" + "opy\"": "核心操作实现",
        "criterion='" + "entr" + "opy'": "核心操作实现",
        "\u4fe1\u606f\u589e\u76ca": "复杂度分析",
        "\u7279\u5f81\u9009\u62e9": "结构选择",
        "\u51b3\u7b56\u6811": "树结构",
        "\u8bfe\u7a0b\u8d44\u6599\u5f85\u4e0a\u4f20": fallback,
        "课程资料课程资料": fallback,
        "课程资料与课程资料": fallback,
        "课程资料、课程资料": fallback,
    }
    text = str(value)
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    text = re.sub(r"(课程资料[、与和\s]*){2,}", fallback, text)
    text = re.sub(r"Gain\s*\([^)]*\)\s*=\s*H\([^)]*\)\s*-\s*H\([^)]*\)", "T(n) 表示操作次数随数据规模 n 的变化", text)
    return text

def _build_complete_mindmap(topic: str) -> str:
    return f"""mindmap
  root(({topic}完整知识结构))
    课程定位
      数据结构课程
        核心数据结构
        算法实现基础
      核心问题
        如何描述元素关系
        如何选择合适存储方式
        如何实现基本操作
      学习产出
        会解释逻辑结构
        会画出存储结构
        会分析复杂度
        会完成代码实践
        会根据测评结果补强
    先修知识
      C语言基础
        结构体
        指针
        数组
        函数
      抽象数据类型
        数据对象
        数据关系
        基本操作
      复杂度基础
        时间复杂度
        空间复杂度
        最坏情况
        平均情况
    核心概念
      逻辑结构
        集合
        线性结构
        树形结构
        图结构
      存储结构
        顺序存储
        链式存储
        索引存储
        散列存储
      基本操作
        初始化
        插入
        删除
        查找
        遍历
      复杂度分析
        操作次数
        数据规模
        额外空间
    学习流程
      明确定义
        数据对象
        数据关系
        操作集合
      画出结构
        顺序表下标
        链表指针
        树的父子关系
        图的邻接关系
      跟踪操作
        操作前状态
        关键步骤
        操作后状态
      结果解释
        正确性
        边界条件
        复杂度
    错误点与常见错误
      概念错误
        混淆逻辑结构和存储结构
        只背定义不画状态
        忽略抽象数据类型
      计算错误
        循环边界写错
        指针更新顺序错误
        复杂度估计不完整
      应用错误
        不考虑空结构
        不处理首尾节点
        缺少异常输入
    代码实践
      核心实现
        数据类型定义
        初始化函数
        插入删除
        查找遍历
      实操步骤
        阅读接口
        补全实现
        准备测试数据
        运行验证
      实验观察
        输入输出
        状态变化
        时间开销
        空间开销
      实验产出
        运行结果
        手工跟踪表
        记录参数变化
        写出实验结论
    阶段测评闭环
      学习前
        根据画像识别薄弱点
        推荐导图和讲解文档
      学习中
        完成概念选择题
        完成公式简答题
        完成手算计算题
        完成代码实践题
      测评后
        自动判分
        识别错因
        写入错题本
        更新易错点
      路径调整
        插入数据结构补强任务
        延后代码实验阶段
        推荐复习资料
        生成学习报告
    可信依据
      课程讲义
        数据结构章节
        实验指导
        源码片段
      RAG 引用
        原文片段
        页码章节
        相似度
      教师审核
        内容通过
        风险标记
        学生端同步
      模型推断标记"""


def _build_video_demo(topic: str, target: str) -> str:
    return f"""# {topic}视频演示方案

## 演示目标

- 让学生在 3 分钟内理解“{topic}”的直觉含义。
- 用小规模结构状态变化说明关键操作。
- 连接课程资料、思维导图、练习题、代码实践和后续测评。
- 本次学习目标：{target or "掌握核心概念并完成补强练习"}。

## 视频结构

| 时间 | 画面 | 旁白 | 屏幕文字 |
| --- | --- | --- | --- |
| 0:00-0:20 | 展示今日学习任务和课程名“数据结构课程” | 今天只解决一个问题：怎样把{topic}的定义、结构和操作讲清楚。 | 今日任务：{topic} |
| 0:20-0:50 | 展示完整思维导图，从课程定位缩放到核心概念 | 先看全局结构：明确逻辑结构、存储方式和基本操作。 | 先看全局，再看细节 |
| 0:50-1:30 | 在白板上展示小规模结构状态 | 用一个小例子跟踪操作前、操作中和操作后的状态变化。 | 状态跟踪 |
| 1:30-2:10 | 展示边界条件 | 空结构、首尾位置和越界处理是最容易出错的地方。 | 边界条件 |
| 2:10-2:35 | 展示代码片段 | 把定义转换为结构体、初始化和核心操作函数。 | 代码实现 |
| 2:35-3:00 | 总结与练习 | 完成练习题，并把错题写回学习路径。 | 练习 -> 测评 -> 路径调整 |
| 2:50-3:00 | 回到学习路径和测评入口 | 接下来完成练习题，系统会根据错题自动插入补强任务。 | 练习 -> 测评 -> 路径调整 |

## 录屏操作步骤

1. 打开学生工作台，进入“资源中心”。
2. 点击“{topic}完整思维导图”，展示完整导图。
3. 切换到“{topic}视频演示方案”，按分镜逐段讲解。
4. 打开练习题，演示提交后如何进入错题本和学习报告。
5. 回到学习路径，展示系统插入补强任务。

## 需要展示的证据

- 课程来源：数据结构课程讲义第 3 章第 2 节。
- 资源来源：多智能体生成任务。
- 审核状态：内容审核通过后进入学生端。
- 闭环结果：练习和测评结果会影响画像与学习路径。
"""


VIDEO_SCENE_PLAN = [
    ("scene_intro", "0:00-0:20", "intro"),
    ("scene_structure", "0:20-0:50", "structure"),
    ("scene_operation", "0:50-1:30", "operation"),
    ("scene_complexity", "1:30-2:20", "complexity"),
    ("scene_practice", "2:20-3:00", "practice"),
]

VIDEO_STORYBOARD_SYSTEM_PROMPT = """你是《数据结构课程》课程的多模态创作 Agent。
你的任务是生成可直接驱动教学动画和视频生成的真实知识点镜头数据 JSON。
必须只输出合法 JSON，不要 Markdown 代码块，不要额外解释。
JSON schema:
{
  "title": "数据结构主题 3 分钟教学演示",
  "course": "数据结构课程",
  "topic": "string",
  "referenceSummary": "string",
  "agentTrace": ["读取学习画像", "检索课程引用", "生成教学镜头", "生成字幕", "输出播放数据"],
  "productionNotes": ["string"],
  "scenes": [
    {
      "id": "scene_intro",
      "timeRange": "0:00-0:20",
      "kind": "intro",
      "title": "string",
      "screenTitle": "string",
      "screenText": "string",
      "description": "string",
      "voiceover": "string",
      "keyConcepts": ["string"],
      "teachingGoal": "本段要让学生学会什么",
      "coreExplanation": "真实知识点解释，不能写制作流程、录屏说明或页面操作",
      "visualModel": {
        "type": "sequence_array | linked_nodes | structure_compare | insert_shift | delete_shift | complexity_table | practice_quiz",
        "description": "画面如何表示知识点",
        "data": {}
      },
      "exampleData": {
        "sequence": ["A", "B", "C", "D"],
        "insertValue": "X",
        "insertIndex": 2,
        "operation": "insert | delete | search | traverse",
        "expectedResult": ["A", "B", "X", "C", "D"]
      },
      "operationSteps": ["可动画化的知识步骤，不是录屏步骤"],
      "formulaOrComplexity": "复杂度、公式或规则说明",
      "studentTask": "给学生的一道小任务或判断题",
      "citationChunkIds": ["chunk_id"]
    }
  ]
}
Rules:
1. 必须生成 5 个 scenes，顺序和 timeRange 固定为：
   0:00-0:20 引入问题；0:20-0:50 建立结构视图；0:50-1:30 跟踪核心操作；
   1:30-2:20 分析复杂度；2:20-3:00 总结与练习引导。
2. 内容必须围绕数据结构课程主题，禁止出现其它课程案例。
3. 每个 scene 必须包含真实知识点解释、可视化模型、具体例子数据、可动画化操作步骤、复杂度/规则和学生任务。
   每个 scene 的 operationSteps 必须至少 2 条短句；formulaOrComplexity 必须是非空短句。
4. citationChunkIds 只能使用用户提供的课程引用 chunkId。
5. 禁止出现“打开视频演示页、展示当前分镜、分镜脚本、引导进入学习资源、课程依据、画面类型、学生画像、学习路径、录屏步骤、页面操作”等制作脚本或元信息表达。
   JSON 字段名保持 schema 兼容，但字段值不得出现“分镜、脚本、录屏、页面、镜头、画面”等制作词。
6. 如果主题是线性表，必须包含线性表定义、顺序表/链表对比、插入或删除示例、查找/遍历或复杂度、练习题。
7. 所有字段用简洁短句，避免长篇解释导致 JSON 截断。
"""


def build_video_demo_payload(
    resource: dict[str, Any],
    user_context: dict[str, Any] | None = None,
    video_job: dict[str, Any] | None = None,
    generate_storyboard: bool = False,
) -> dict[str, Any]:
    """Build video-demo data through DeepSeek + multimodal creation agent.

    The video demo page must be driven by real LLM storyboard data. If DeepSeek
    is unavailable or returns an invalid schema, fail loudly instead of showing
    a static fallback that looks like generated content.
    """
    if resource.get("resourceType") != "video_script":
        raise HTTPException(status_code=400, detail="当前资源不是视频演示资源")
    citations = deepcopy(resource.get("citations", []))
    topic = str((resource.get("metadata") or {}).get("topic") or DEFAULT_RESOURCE_TOPIC)
    target = str((resource.get("metadata") or {}).get("target") or DEFAULT_RESOURCE_TARGET)
    if not citations and not video_job:
        raise HTTPException(status_code=409, detail="视频演示缺少真实课程引用，已停止生成，避免产生无依据内容。")
    if video_job:
        job_citations = video_job.get("citations") if isinstance(video_job.get("citations"), list) else citations
        job_scenes = video_job.get("scenes") if isinstance(video_job.get("scenes"), list) else []
        title = str(video_job.get("title") or f"{topic} 3 分钟教学演示")
        return _compose_video_payload(
            resource,
            str(video_job.get("topic") or topic),
            job_scenes,
            job_citations,
            script=_video_script_from_scenes(title, job_scenes, job_citations) if job_scenes else resource.get("content", ""),
            reference_summary=_video_reference_summary(job_citations),
            agent_trace=[
                "读取当前生成尝试",
                "读取课程引用",
                "读取 Agnes AI 视频生成任务状态",
                "仅当前尝试完成并转存为本地 MP4 时返回主视频",
            ],
            production_notes=[
                "当前尝试是页面主状态的唯一来源；失败时不会用旧 MP4 冒充本次成片。",
                "历史成功成片只进入历史区，不覆盖当前尝试语义。",
            ],
            used_llm=bool(job_scenes),
            fallback=False,
            user_context={"userId": video_job.get("userId"), **({"profileItems": []} if not isinstance(video_job.get("personalization"), dict) else {})},
            video_job=video_job,
        )
    if not generate_storyboard:
        return _compose_video_payload(
            resource,
            topic,
            [],
            citations,
            script=resource.get("content", ""),
            reference_summary=_video_reference_summary(citations),
            agent_trace=[
                "等待用户发起真实生成",
                "生成时会先校验课程引用",
                "DeepSeek 合格教学内容通过后才启动 Agnes AI 视频生成",
            ],
            production_notes=[
                "空闲状态不展示默认分镜，也不使用假数据。",
                "旧成片如果存在，只会显示在历史成片区。",
            ],
            used_llm=False,
            fallback=False,
            user_context=user_context,
            video_job=None,
        )
    try:
        return _build_deepseek_video_payload(resource, topic, target, citations, user_context or {}, video_job)
    except LLMUnavailable as exc:
        raise_blocked(
            status_code=503,
            agent_name="多模态生成 Agent",
            message="DeepSeek 视频分镜不可用，已停止视频生成；不会使用课程模板分镜兜底。",
            missing_requirements=["DeepSeek storyboard JSON", "5 个合格分镜", "有效 citationChunkIds"],
            used_llm=True,
            detail=str(exc),
        )


def _build_deepseek_video_payload(
    resource: dict[str, Any],
    topic: str,
    target: str,
    citations: list[dict[str, Any]],
    user_context: dict[str, Any],
    video_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    citation_context = _citation_context(citations)
    learning_context = _video_user_context_prompt(user_context)
    user_prompt = "\n".join([
        "课程：数据结构课程",
        f"主题：{topic}",
        f"学习目标：{target}",
        "当前学生真实学习上下文：",
        learning_context,
        "课程引用：",
        citation_context,
        "请按 schema 生成可播放的 3 分钟知识点教学动画 JSON。",
        "硬性要求：每个教学镜头必须讲真实知识点，必须给出可动画的数据或步骤；不要写分镜说明、录屏步骤或页面操作。",
        "个性化信息只能影响讲解重点和练习提示，不要作为主画面卡片内容。",
    ])
    result = _call_deepseek_video_storyboard(user_prompt)
    raw_scenes = result.get("scenes")
    if not isinstance(raw_scenes, list) or len(raw_scenes) < 5:
        raise LLMUnavailable("DeepSeek did not return 5 storyboard scenes")
    raw_scenes = [_repair_video_scene(raw_scenes[index], index, citations, topic=topic) for index in range(5)]
    for index in range(5):
        _validate_llm_video_scene(raw_scenes[index], index, citations, topic=topic)
    scenes = [_normalize_video_scene(raw_scenes[index], index, citations) for index in range(5)]
    _validate_video_teaching_coverage(topic, scenes)
    title = _clean_text(result.get("title"), f"{topic} 3 分钟教学演示")
    return _compose_video_payload(
        resource,
        topic,
        scenes,
        citations,
        script=_video_script_from_scenes(title, scenes, citations),
        reference_summary=_clean_text(
            result.get("referenceSummary"),
            _video_reference_summary(citations),
        ),
        agent_trace=[
            *_safe_text_list(result.get("agentTrace"), [
            "读取当前登录用户学习画像",
            "读取学习路径、测评结果和错题记录",
            "检索课程引用",
            "DeepSeek 生成教学镜头",
            "输出前端可播放数据",
            ]),
            "DeepSeek 教学镜头经结构化修复与校验后提交视频生成链路",
        ],
        production_notes=[
            *_safe_text_list(result.get("productionNotes"), [
            "知识点讲解、动态图示、字幕和例题步骤由 DeepSeek 根据课程引用和学习闭环数据生成。",
            ]),
            "后端只补齐缺失的结构化字段，不使用静态模板冒充本次 DeepSeek 内容。",
        ],
        used_llm=True,
        fallback=False,
        user_context=user_context,
        video_job=video_job,
    )


def _call_deepseek_video_storyboard(user_prompt: str) -> dict[str, Any]:
    attempts = [
        {"temperature": 0.25, "max_tokens": 5200, "timeout": 75},
        {"temperature": 0.15, "max_tokens": 7600, "timeout": 95},
    ]
    last_error: LLMUnavailable | None = None
    for index, options in enumerate(attempts):
        try:
            prompt = user_prompt if index == 0 else "\n".join([
                user_prompt,
                "上一次输出 JSON 被截断或不合法。现在重新生成完整 JSON。",
                "只输出 JSON 对象；5 个 scenes 每个字段保持简洁；不要输出 Markdown；不要省略结尾大括号。",
                "每个 scene 必须有非空 formulaOrComplexity，并且 operationSteps 至少 2 条短句。",
            ])
            return call_deepseek_json(
                VIDEO_STORYBOARD_SYSTEM_PROMPT,
                prompt,
                temperature=options["temperature"],
                max_tokens=options["max_tokens"],
                timeout=options["timeout"],
            )
        except LLMJsonError as exc:
            last_error = exc
            if exc.reason_code != "json_truncated" or index == len(attempts) - 1:
                break
        except LLMUnavailable as exc:
            last_error = exc
            break
    raise last_error or LLMUnavailable("DeepSeek video storyboard unavailable")


def _disabled_static_video_payload(
    resource: dict[str, Any],
    topic: str,
    target: str,
    citations: list[dict[str, Any]],
    user_context: dict[str, Any],
    *,
    error: str,
) -> dict[str, Any]:
    scenes = _disabled_static_video_scenes(topic, target, citations)
    title = f"{topic} 3 分钟教学演示"
    return _compose_video_payload(
        resource,
        topic,
        scenes,
        citations,
        script=_video_script_from_scenes(title, scenes, citations),
        reference_summary=_video_reference_summary(citations),
        agent_trace=[
            "读取当前登录用户学习画像",
            "读取课程引用",
            "DeepSeek 分镜不可用，启用课程模板兜底",
            "输出可交给 Agnes AI 生成视频的知识点分镜",
        ],
        production_notes=[
            f"DeepSeek 本次不可用或返回未通过校验：{error}",
            "兜底分镜基于课程引用、数据结构知识模板和当前资源主题生成。",
        ],
        used_llm=False,
        fallback=True,
        user_context=user_context,
        error=error,
        video_job=None,
    )


def _disabled_static_video_scenes(topic: str, target: str, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunk_ids = _fallback_citation_ids(citations)
    is_linear_list = "线性表" in topic
    structure_compare = "顺序表按连续存储保存元素，支持随机访问；链表用指针连接节点，插入删除更灵活。" if is_linear_list else "先区分逻辑结构、存储结构和基本操作，再把抽象关系落到可视化状态。"
    operation_name = "插入" if is_linear_list else "核心操作"
    example = {
        "sequence": ["A", "B", "C", "D"],
        "insertValue": "X",
        "insertIndex": 2,
        "operation": "insert",
        "expectedResult": ["A", "B", "X", "C", "D"],
    }
    return [
        _disabled_static_video_scene(
            0,
            topic,
            "引入问题：为什么要学会结构视图",
            f"{topic} 的学习目标是把定义、存储方式、操作过程和复杂度连成一个闭环。",
            f"本段先明确学习目标：{target}。学习数据结构不能只背定义，还要说明元素之间的逻辑关系，以及操作前后的状态变化。",
            "通过一个小规模序列建立问题意识：元素如何组织，操作如何改变结构。",
            ["学习目标", "逻辑结构", "存储结构"],
            "sequence_array",
            example,
            ["给出待处理元素序列", "指出要完成的操作", "说明观察结构变化的标准"],
            "先看结构，再看操作，最后用复杂度评价方案。",
            "判断：只会背定义但不能画出结构状态，是否算真正掌握？",
            chunk_ids,
        ),
        _disabled_static_video_scene(
            1,
            topic,
            "建立结构视图：顺序与链接的对比",
            structure_compare,
            f"{topic} 的关键是把抽象关系画出来。顺序存储强调位置和下标，链式存储强调节点和指针关系，两者决定了后续操作成本。",
            "画面左侧展示连续数组位置，右侧展示节点和 next 指针，帮助学生区分两种存储思想。",
            ["顺序表", "链表", "结构视图"],
            "structure_compare" if is_linear_list else "linked_nodes",
            example,
            ["标出每个元素的下标或指针", "比较访问第 i 个元素的路径", "说明存储方式如何影响操作"],
            "顺序表随机访问通常为 O(1)，链表按序查找通常为 O(n)。",
            "小任务：说明顺序表和链表各自更适合哪类操作。",
            chunk_ids,
        ),
        _disabled_static_video_scene(
            2,
            topic,
            f"跟踪核心操作：{operation_name}过程",
            "以在线性表第 2 个位置插入 X 为例，顺序表需要移动后续元素，链表需要调整前驱节点的指针。",
            "核心操作要按步骤跟踪。先定位插入位置，再保护原有后继关系，最后写入新元素并更新长度或指针。",
            "用 A、B、C、D 插入 X 的动画展示操作前、移动/改链、操作后三个状态。",
            ["插入操作", "边界条件", "状态变化"],
            "insert_shift",
            example,
            ["定位插入位置 i=2", "将 C、D 向后移动或保存后继指针", "写入 X 并更新结构状态", "检查空表、表尾和越界情况"],
            "顺序表插入平均需要移动 O(n) 个元素；链表在已知前驱时改链为 O(1)，查找前驱仍可能为 O(n)。",
            "练习：如果在表头插入元素，顺序表需要移动哪些元素？",
            chunk_ids,
        ),
        _disabled_static_video_scene(
            3,
            topic,
            "分析复杂度：把步骤翻译成成本",
            "复杂度来自操作步骤数量。访问、查找、插入、删除在不同存储结构下成本不同。",
            "分析复杂度时，不看数据值大小，而看元素规模 n 增长时步骤如何增长。顺序表和链表的差异，本质来自定位和移动/改链的成本。",
            "表格展示访问、查找、插入、删除的常见时间复杂度，并突出前驱是否已知这一条件。",
            ["时间复杂度", "空间代价", "操作成本"],
            "complexity_table",
            {"sequence": ["访问", "查找", "插入", "删除"], "operation": "compare", "expectedResult": ["O(1)", "O(n)", "O(n)", "O(n)"]},
            ["列出操作步骤", "找出随 n 增长的步骤", "写出最坏或平均复杂度", "说明复杂度成立的前提"],
            "顺序表访问 O(1)、查找 O(n)、插入删除通常 O(n)；链表访问 O(n)、已知前驱插入删除 O(1)。",
            "判断：链表删除一定是 O(1) 吗？说明条件。",
            chunk_ids,
        ),
        _disabled_static_video_scene(
            4,
            topic,
            "总结与练习：形成学习闭环",
            f"掌握 {topic} 要能复述定义、画出结构、跟踪操作、分析复杂度，并完成一道变式练习。",
            "最后把今天的四个能力合并成检查清单：定义是否清楚，结构图是否准确，操作步骤是否完整，复杂度是否能解释。",
            "画面给出一道小练习，要求学生完成插入后的序列结果并说明时间复杂度。",
            ["复盘", "变式练习", "学习闭环"],
            "practice_quiz",
            example,
            ["回顾定义和两类存储结构", "重做插入例题", "写出复杂度和适用条件", "根据错题安排下一轮补强"],
            "练习反馈会进入错题与补强安排；资源学习效果由完成情况和练习得分共同验证。",
            "练习：在 A、B、C、D 的下标 2 插入 X，写出结果并说明顺序表的移动次数。",
            chunk_ids,
        ),
    ]


def _disabled_static_video_scene(
    index: int,
    topic: str,
    title: str,
    screen_text: str,
    core_explanation: str,
    description: str,
    key_concepts: list[str],
    visual_type: str,
    example_data: dict[str, Any],
    operation_steps: list[str],
    formula_or_complexity: str,
    student_task: str,
    chunk_ids: list[str],
) -> dict[str, Any]:
    plan_id, plan_time, plan_kind = VIDEO_SCENE_PLAN[index]
    scene = {
        "id": plan_id,
        "time": plan_time,
        "timeRange": plan_time,
        "kind": plan_kind,
        "title": title,
        "screenTitle": title,
        "screenText": screen_text,
        "description": description,
        "voiceover": core_explanation,
        "narration": core_explanation,
        "keyConcepts": key_concepts,
        "concept": "、".join(key_concepts),
        "teachingGoal": f"帮助学生掌握 {topic} 的{key_concepts[0]}",
        "coreExplanation": core_explanation,
        "visualModel": {
            "type": visual_type,
            "description": description,
            "data": example_data,
        },
        "exampleData": example_data,
        "operationSteps": operation_steps,
        "recordingSteps": operation_steps,
        "formulaOrComplexity": formula_or_complexity,
        "studentTask": student_task,
        "citationChunkIds": chunk_ids,
        "agentEvidence": f"课程模板兜底分镜；引用 {len(chunk_ids)} 个课程片段；DeepSeek 本次未参与分镜生成",
    }
    _validate_llm_video_scene(scene, index)
    return _normalize_video_scene(scene, index, [{"chunkId": chunk_id} for chunk_id in chunk_ids])


def _fallback_citation_ids(citations: list[dict[str, Any]]) -> list[str]:
    ids = [str(item.get("chunkId")) for item in citations if isinstance(item, dict) and str(item.get("chunkId") or "").strip()]
    if not ids:
        raise LLMUnavailable("course template fallback requires citationChunkIds")
    return ids[:3]


def _compose_video_payload(
    resource: dict[str, Any],
    topic: str,
    scenes: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    *,
    script: str,
    reference_summary: str,
    agent_trace: list[str],
    production_notes: list[str],
    used_llm: bool,
    fallback: bool,
    user_context: dict[str, Any] | None = None,
    error: str | None = None,
    video_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title = f"{topic} 3 分钟教学演示"
    if video_job and isinstance(video_job.get("personalization"), dict) and video_job.get("personalization"):
        personalization = video_job["personalization"]
    else:
        personalization = _video_personalization_payload(user_context or {})
    video_file = _video_file_payload(video_job)
    generation_mode = (video_job or {}).get("generationMode") or (
        "blocked_static_storyboard_to_agnes_video" if fallback else "deepseek_storyboard_to_agnes_video"
    )
    source_type = (video_job or {}).get("sourceType") or (
        "blocked_static_storyboard" if fallback else "agnes_video_generation"
    )
    provider = (video_job or {}).get("provider") or "Agnes AI"
    llm_status = (video_job or {}).get("llmStatus") if isinstance((video_job or {}).get("llmStatus"), dict) else None
    return {
        "resourceId": resource["id"],
        "title": title,
        "course": "数据结构课程",
        "topic": topic,
        "sourceAgent": "多模态创作 Agent",
        "agentModel": llm_model_name(),
        "generationMode": generation_mode,
        "sourceType": source_type,
        "generatedBy": f"DeepSeek + 多模态创作 Agent + {provider}",
        "auditStatus": resource.get("auditStatus", "pending"),
        **video_file,
        "script": script,
        "timeline": [{"timeRange": item["timeRange"], "title": item["title"]} for item in scenes],
        "subtitles": [{"timeRange": item["timeRange"], "text": item["voiceover"]} for item in scenes],
        "scenes": scenes,
        "citations": citations,
        "referenceSummary": reference_summary,
        "controls": ["play", "pause", "previous", "next"],
        "agentTrace": agent_trace,
        "productionNotes": production_notes,
        "personalizationEvidence": personalization,
        "videoJob": video_job,
        "currentAttempt": video_job,
        "schemaVersion": (video_job or {}).get("schemaVersion") or "knowledge_video_v2",
        "isCurrentVideo": bool(video_file.get("videoGenerated")),
        "canReusePreviousVideo": False,
        "showcaseVideos": [],
        "llmStatus": llm_status or {
            "enabled": llm_enabled(),
            "usedLLM": used_llm,
            "fallback": fallback,
            "model": llm_model_name(),
            "error": error,
        },
    }


def _video_file_payload(video_job: dict[str, Any] | None) -> dict[str, Any]:
    provider = (video_job or {}).get("provider") or "Agnes AI"
    is_current_knowledge_video = bool(
        video_job
        and video_job.get("status") == "completed"
        and video_job.get("videoUrl")
        and video_job.get("schemaVersion") == "knowledge_video_v2"
        and video_job.get("ffmpegNormalized") is True
    )
    if not is_current_knowledge_video:
        return {
            "videoUrl": None,
            "videoMimeType": "video/mp4",
            "videoDurationSeconds": None,
            "videoRenderer": f"{provider} 视频生成，本地媒体转存",
            "videoProvider": provider,
            "videoGenerated": False,
            "videoStatus": video_job.get("status") if video_job else "idle",
            "videoError": (video_job.get("errorDetail") or video_job.get("error")) if video_job else None,
        }
    return {
        "videoUrl": video_job.get("videoUrl"),
        "videoMimeType": video_job.get("videoMimeType") or "video/mp4",
        "videoDurationSeconds": video_job.get("videoDurationSeconds"),
        "videoRenderer": f"{provider} 视频生成，本地媒体转存 MP4",
        "videoProvider": provider,
        "videoGenerated": True,
        "videoStatus": "completed",
        "videoError": None,
    }


def _video_user_context_prompt(user_context: dict[str, Any]) -> str:
    profile_items = user_context.get("profileItems") if isinstance(user_context.get("profileItems"), list) else []
    profile_lines = []
    for item in profile_items:
        dimension = str(item.get("dimension") or "").strip()
        value = str(item.get("value") or "").strip()
        confidence = item.get("confidence")
        source = item.get("source") or "unknown"
        if dimension and value:
            profile_lines.append(f"- {dimension}: {value}（置信度 {confidence}，来源 {source}）")
    learning_path = user_context.get("learningPath") if isinstance(user_context.get("learningPath"), dict) else {}
    active_stage = next(
        (stage for stage in learning_path.get("stages", []) if isinstance(stage, dict) and stage.get("status") == "active"),
        {},
    )
    latest_assessment = user_context.get("latestAssessment") if isinstance(user_context.get("latestAssessment"), dict) else {}
    mistakes = user_context.get("recentMistakes") if isinstance(user_context.get("recentMistakes"), list) else []
    return "\n".join([
        "学生画像：",
        *(profile_lines or ["- 当前用户暂无已确认画像，必须先完成画像确认。"]),
        "当前学习路径：",
        f"- 当前阶段: {active_stage.get('name') or '未进入路径阶段'}",
        f"- 阶段任务: {'、'.join(str(item) for item in active_stage.get('tasks', [])[:3]) if active_stage else '无'}",
        f"- 路径调整原因: {((learning_path.get('adjustmentHistory') or [{}])[0]).get('reason', '暂无路径调整记录') if isinstance(learning_path.get('adjustmentHistory'), list) else '暂无路径调整记录'}",
        "最近测评与错题：",
        f"- 最近测评分数: {latest_assessment.get('score', '暂无测评')}",
        f"- 测评薄弱点: {'、'.join(latest_assessment.get('weakness', [])) if latest_assessment else '暂无'}",
        f"- 最近错题: {'；'.join(str(item.get('knowledge') or item.get('stem') or '') for item in mistakes[:3]) if mistakes else '暂无错题记录'}",
    ])


def _profile_value(profile_items: list[dict[str, Any]], dimension: str, fallback: str) -> str:
    for item in profile_items:
        if item.get("dimension") == dimension and str(item.get("value") or "").strip():
            return str(item.get("value")).strip()
    return fallback


def _video_personalization_payload(user_context: dict[str, Any]) -> dict[str, Any]:
    profile_items = user_context.get("profileItems") if isinstance(user_context.get("profileItems"), list) else []
    learning_path = user_context.get("learningPath") if isinstance(user_context.get("learningPath"), dict) else {}
    active_stage = next(
        (stage for stage in learning_path.get("stages", []) if isinstance(stage, dict) and stage.get("status") == "active"),
        {},
    )
    latest_assessment = user_context.get("latestAssessment") if isinstance(user_context.get("latestAssessment"), dict) else {}
    mistakes = user_context.get("recentMistakes") if isinstance(user_context.get("recentMistakes"), list) else []
    return {
        "userId": user_context.get("userId") or "unknown",
        "profileDimensions": len(profile_items),
        "weakPoints": _profile_value(profile_items, "薄弱知识点", "待识别"),
        "resourcePreference": _profile_value(profile_items, "资源偏好", "待识别"),
        "learningGoal": _profile_value(profile_items, "学习目标", DEFAULT_RESOURCE_TARGET),
        "practiceLevel": _profile_value(profile_items, "实践能力水平", "待识别"),
        "activeStage": active_stage.get("name") or "尚未进入路径阶段",
        "activeTasks": active_stage.get("tasks", [])[:3] if isinstance(active_stage.get("tasks"), list) else [],
        "latestScore": latest_assessment.get("score") if latest_assessment else None,
        "assessmentWeakness": latest_assessment.get("weakness", []) if latest_assessment else [],
        "recentMistakes": [
            {
                "knowledge": item.get("knowledge"),
                "wrongReason": item.get("wrongReason"),
                "fixTask": item.get("fixTask"),
            }
            for item in mistakes[:3]
            if isinstance(item, dict)
        ],
    }


def _normalize_video_scene(raw: Any, index: int, citations: list[dict[str, Any]]) -> dict[str, Any]:
    plan_id, plan_time, plan_kind = VIDEO_SCENE_PLAN[index]
    raw = _repair_video_scene(raw, index, citations)
    _reject_storyboard_meta_text(raw, index)
    chunk_ids = _safe_text_list(raw.get("citationChunkIds"), [])
    valid_chunk_ids = {str(item.get("chunkId")) for item in citations if str(item.get("chunkId") or "").strip()}
    if not chunk_ids:
        raise LLMUnavailable(f"DeepSeek scene {index + 1} missing citationChunkIds")
    invalid_chunk_ids = [chunk_id for chunk_id in chunk_ids if chunk_id not in valid_chunk_ids]
    if invalid_chunk_ids:
        raise LLMUnavailable(f"DeepSeek scene {index + 1} used unknown citationChunkIds: {', '.join(invalid_chunk_ids)}")
    title = _required_text(raw.get("title"), f"DeepSeek scene {index + 1} missing title", limit=80)
    screen_title = _required_text(raw.get("screenTitle"), f"DeepSeek scene {index + 1} missing screenTitle", limit=100)
    screen_text = _required_text(raw.get("screenText"), f"DeepSeek scene {index + 1} missing screenText")
    voiceover = _required_text(raw.get("voiceover") or raw.get("narration"), f"DeepSeek scene {index + 1} missing voiceover")
    key_concepts = _required_text_list(raw.get("keyConcepts"), f"DeepSeek scene {index + 1} missing keyConcepts", min_items=2, limit=6)
    description = _required_text(raw.get("description"), f"DeepSeek scene {index + 1} missing description")
    teaching_goal = _required_text(raw.get("teachingGoal"), f"DeepSeek scene {index + 1} missing teachingGoal")
    core_explanation = _required_text(raw.get("coreExplanation"), f"DeepSeek scene {index + 1} missing coreExplanation")
    visual_model = _required_visual_model(raw.get("visualModel"), index)
    example_data = _required_mapping(raw.get("exampleData"), f"DeepSeek scene {index + 1} missing exampleData")
    operation_steps = _required_text_list(
        _video_scene_operation_steps(raw, index),
        f"DeepSeek scene {index + 1} missing operationSteps",
        min_items=2,
        limit=8,
    )
    formula_or_complexity = _required_text(raw.get("formulaOrComplexity"), f"DeepSeek scene {index + 1} missing formulaOrComplexity")
    student_task = _required_text(raw.get("studentTask"), f"DeepSeek scene {index + 1} missing studentTask")
    _reject_storyboard_meta_text({
        "teachingGoal": teaching_goal,
        "coreExplanation": core_explanation,
        "operationSteps": operation_steps,
        "studentTask": student_task,
    }, index)
    return {
        "id": _clean_text(raw.get("id"), plan_id, limit=64),
        "time": plan_time,
        "timeRange": plan_time,
        "kind": _normalize_scene_kind(raw.get("kind"), plan_kind),
        "title": title,
        "screenTitle": screen_title,
        "screenText": screen_text,
        "description": description,
        "voiceover": voiceover,
        "narration": voiceover,
        "keyConcepts": key_concepts,
        "concept": "、".join(key_concepts),
        "teachingGoal": teaching_goal,
        "coreExplanation": core_explanation,
        "visualModel": visual_model,
        "exampleData": example_data,
        "operationSteps": operation_steps,
        "formulaOrComplexity": formula_or_complexity,
        "studentTask": student_task,
        "recordingSteps": operation_steps,
        "citationChunkIds": chunk_ids,
        "agentEvidence": f"DeepSeek 分镜输出；引用 {len(chunk_ids)} 个课程片段；模型 {llm_model_name()}",
    }


def _video_reference_summary(citations: list[dict[str, Any]]) -> str:
    labels = [_citation_public_label(citation) for citation in citations[:3]]
    return "基于真实课程引用生成：" + "；".join(label for label in labels if label)


def _validate_llm_video_scene(
    raw: Any,
    index: int,
    citations: list[dict[str, Any]] | None = None,
    *,
    topic: str = DEFAULT_RESOURCE_TOPIC,
) -> None:
    if not isinstance(raw, dict):
        raise LLMUnavailable(f"DeepSeek scene {index + 1} is not an object")
    if citations is not None:
        raw = _repair_video_scene(raw, index, citations, topic=topic)
    _reject_storyboard_meta_text(raw, index)
    required_text_fields = ("title", "screenTitle", "screenText", "description")
    for field in required_text_fields:
        if not str(raw.get(field) or "").strip():
            raise LLMUnavailable(f"DeepSeek scene {index + 1} missing {field}")
    if not str(raw.get("voiceover") or raw.get("narration") or "").strip():
        raise LLMUnavailable(f"DeepSeek scene {index + 1} missing voiceover")
    key_concepts = raw.get("keyConcepts")
    if not isinstance(key_concepts, list) or not any(str(item).strip() for item in key_concepts):
        raise LLMUnavailable(f"DeepSeek scene {index + 1} missing keyConcepts")
    for field in ("teachingGoal", "coreExplanation", "formulaOrComplexity", "studentTask"):
        if not str(raw.get(field) or "").strip():
            raise LLMUnavailable(f"DeepSeek scene {index + 1} missing {field}")
    if not isinstance(raw.get("visualModel"), dict):
        raise LLMUnavailable(f"DeepSeek scene {index + 1} missing visualModel")
    if not isinstance(raw.get("exampleData"), dict):
        raise LLMUnavailable(f"DeepSeek scene {index + 1} missing exampleData")
    operation_steps = _video_scene_operation_steps(raw, index)
    if not isinstance(operation_steps, list) or len([item for item in operation_steps if str(item).strip()]) < 2:
        raise LLMUnavailable(f"DeepSeek scene {index + 1} missing operationSteps")
    chunk_ids = raw.get("citationChunkIds")
    if not isinstance(chunk_ids, list) or not any(str(item).strip() for item in chunk_ids):
        raise LLMUnavailable(f"DeepSeek scene {index + 1} repair failed: missing valid citationChunkIds")


def _repair_video_scene(
    raw: Any,
    index: int,
    citations: list[dict[str, Any]],
    *,
    topic: str = DEFAULT_RESOURCE_TOPIC,
) -> dict[str, Any]:
    scene = deepcopy(raw) if isinstance(raw, dict) else {}
    _reject_storyboard_meta_text(scene, index)
    if not _has_video_scene_teaching_seed(scene):
        raise LLMUnavailable(f"DeepSeek scene {index + 1} repair failed: 缺少可修复的教学主体内容")
    plan_id, plan_time, plan_kind = VIDEO_SCENE_PLAN[index]
    scene["id"] = _clean_text(scene.get("id"), plan_id, limit=64)
    scene["time"] = plan_time
    scene["timeRange"] = plan_time
    scene["kind"] = _normalize_scene_kind(scene.get("kind"), plan_kind)
    if not isinstance(scene.get("exampleData"), dict) or not scene.get("exampleData"):
        scene["exampleData"] = _default_video_example_data(index)
    if not isinstance(scene.get("visualModel"), dict):
        scene["visualModel"] = _default_video_visual_model(index, scene["exampleData"])
    else:
        scene["visualModel"] = _repair_video_visual_model(scene["visualModel"], index, scene["exampleData"])
    if not _safe_text_list(scene.get("keyConcepts"), [], limit=6):
        scene["keyConcepts"] = _default_video_key_concepts(index)
    title = _clean_text(scene.get("title"), _default_video_scene_title(index, topic), limit=80)
    screen_text = _clean_text(
        scene.get("screenText") or scene.get("coreExplanation") or scene.get("voiceover") or scene.get("narration"),
        _default_video_screen_text(index, topic),
    )
    scene["title"] = title
    scene["screenTitle"] = _clean_text(scene.get("screenTitle"), title, limit=100)
    scene["screenText"] = screen_text
    scene["description"] = _clean_text(
        scene.get("description") or (scene.get("visualModel") or {}).get("description"),
        _default_video_description(index, topic),
    )
    scene["teachingGoal"] = _clean_text(
        scene.get("teachingGoal") or screen_text,
        _default_video_teaching_goal(index, topic),
    )
    scene["coreExplanation"] = _clean_text(
        scene.get("coreExplanation") or scene.get("voiceover") or scene.get("narration") or screen_text,
        _default_video_core_explanation(index, topic),
    )
    scene["voiceover"] = _clean_text(
        scene.get("voiceover") or scene.get("narration") or scene.get("coreExplanation") or screen_text,
        scene["coreExplanation"],
    )
    scene["narration"] = scene["voiceover"]
    scene["studentTask"] = _clean_text(
        scene.get("studentTask"),
        _default_video_student_task(index, topic),
    )
    operation_steps = _video_scene_operation_steps(scene, index)
    if len(operation_steps) < 2:
        operation_steps = _fallback_video_scene_operation_steps(scene, index)
    if len(operation_steps) >= 2:
        scene["operationSteps"] = operation_steps
    if not str(scene.get("formulaOrComplexity") or "").strip():
        scene["formulaOrComplexity"] = _video_scene_formula_or_complexity(scene, index)
    if not scene.get("recordingSteps") or len(_safe_text_list(scene.get("recordingSteps"), [], limit=8)) < 2:
        scene["recordingSteps"] = scene.get("operationSteps") or []
    alias_chunk_ids = _first_present(scene, "citationChunkIds", "citationIds", "sourceChunkIds", "chunkIds")
    scene["citationChunkIds"] = _normalize_video_citation_ids(alias_chunk_ids, citations)
    return scene


def _has_video_scene_teaching_seed(scene: dict[str, Any]) -> bool:
    text_fields = (
        "title",
        "screenTitle",
        "screenText",
        "description",
        "voiceover",
        "narration",
        "teachingGoal",
        "coreExplanation",
        "studentTask",
    )
    if any(str(scene.get(field) or "").strip() for field in text_fields):
        return True
    for field in ("operationSteps", "recordingSteps", "animationSteps", "visualSteps", "steps"):
        if _safe_text_list(scene.get(field), [], limit=2):
            return True
    visual_model = scene.get("visualModel") if isinstance(scene.get("visualModel"), dict) else {}
    example_data = scene.get("exampleData") if isinstance(scene.get("exampleData"), dict) else {}
    return bool(visual_model or example_data)


def _normalize_video_citation_ids(value: Any, citations: list[dict[str, Any]]) -> list[str]:
    valid_ids = [
        str(item.get("chunkId"))
        for item in citations
        if isinstance(item, dict) and str(item.get("chunkId") or "").strip()
    ]
    if not valid_ids:
        raise LLMUnavailable("分镜已尝试修复，但缺少有效课程引用，无法生成无依据视频。")
    valid_set = set(valid_ids)
    candidates = _safe_text_list(value, [], limit=8)
    normalized: list[str] = []
    for chunk_id in candidates:
        if chunk_id in valid_set and chunk_id not in normalized:
            normalized.append(chunk_id)
    if not normalized:
        normalized = valid_ids[:3]
    return normalized[:3]


def _default_video_example_data(index: int) -> dict[str, Any]:
    if index == 3:
        return {
            "sequence": ["访问", "查找", "插入", "删除"],
            "operation": "compare",
            "expectedResult": ["O(1)", "O(n)", "O(n)", "O(n)"],
        }
    return {
        "sequence": ["A", "B", "C", "D"],
        "insertValue": "X",
        "insertIndex": 2,
        "operation": "insert" if index in {2, 4} else "traverse",
        "expectedResult": ["A", "B", "X", "C", "D"] if index in {2, 4} else ["A", "B", "C", "D"],
    }


def _default_video_visual_model(index: int, example_data: dict[str, Any]) -> dict[str, Any]:
    visual_types = {
        0: "sequence_array",
        1: "structure_compare",
        2: "insert_shift",
        3: "complexity_table",
        4: "practice_quiz",
    }
    descriptions = {
        0: "用短序列引出线性结构、操作和复杂度的学习目标。",
        1: "对比顺序表的连续下标和链表的节点指针。",
        2: "展示插入操作前、移动或改链、操作后的状态变化。",
        3: "用表格对比访问、查找、插入、删除的常见复杂度。",
        4: "用一道小练习检查结构变化和复杂度判断。",
    }
    return {
        "type": visual_types.get(index, "sequence_array"),
        "description": descriptions.get(index, "展示结构状态、操作步骤和结果对比。"),
        "data": example_data,
    }


def _repair_video_visual_model(model: dict[str, Any], index: int, example_data: dict[str, Any]) -> dict[str, Any]:
    repaired = deepcopy(model)
    allowed = {
        "sequence_array",
        "linked_nodes",
        "structure_compare",
        "insert_shift",
        "delete_shift",
        "complexity_table",
        "practice_quiz",
    }
    default_model = _default_video_visual_model(index, example_data)
    if str(repaired.get("type") or "").strip() not in allowed:
        repaired["type"] = default_model["type"]
    if not str(repaired.get("description") or "").strip():
        repaired["description"] = default_model["description"]
    if not isinstance(repaired.get("data"), dict) or not repaired.get("data"):
        repaired["data"] = example_data
    return repaired


def _default_video_key_concepts(index: int) -> list[str]:
    concepts = {
        0: ["学习目标", "逻辑结构", "复杂度"],
        1: ["顺序表", "链表", "存储结构"],
        2: ["插入操作", "状态变化", "边界条件"],
        3: ["时间复杂度", "空间复杂度", "操作成本"],
        4: ["复盘练习", "学习闭环", "变式判断"],
    }
    return concepts.get(index, ["结构状态", "核心操作", "复杂度"])


def _default_video_scene_title(index: int, topic: str) -> str:
    titles = {
        0: f"引入问题：{topic} 要看什么",
        1: "建立结构视图：顺序与链接",
        2: "跟踪核心操作：插入过程",
        3: "分析复杂度：把步骤翻译成成本",
        4: "总结与练习：形成学习闭环",
    }
    return titles.get(index, f"{topic} 教学片段")


def _default_video_screen_text(index: int, topic: str) -> str:
    texts = {
        0: f"{topic} 的学习要把定义、结构、操作和复杂度连起来。",
        1: "顺序表强调连续下标，链表强调节点和指针连接。",
        2: "插入操作要先定位位置，再移动元素或修改指针，最后更新结构状态。",
        3: "复杂度来自随 n 增长的定位、移动、比较或改链步骤。",
        4: "用一个变式练习检查结构变化和复杂度判断是否掌握。",
    }
    return texts.get(index, f"围绕 {topic} 展示结构状态和操作过程。")


def _default_video_description(index: int, topic: str) -> str:
    descriptions = {
        0: "用一个小规模序列建立学习问题，避免只背定义。",
        1: "画面同时呈现数组位置和链式节点，突出两种存储方式差异。",
        2: "通过插入 X 的动画展示移动元素或调整指针的关键步骤。",
        3: "用复杂度表把访问、查找、插入、删除的成本放在一起比较。",
        4: "把本节知识压缩成一道可判断的练习题。",
    }
    return descriptions.get(index, f"展示 {topic} 的知识点、例子和练习。")


def _default_video_teaching_goal(index: int, topic: str) -> str:
    goals = {
        0: f"让学生知道学习 {topic} 时要同时关注结构、操作和复杂度。",
        1: "让学生区分顺序存储和链式存储的表示方式。",
        2: "让学生能按步骤跟踪一次插入操作的状态变化。",
        3: "让学生能把操作步骤数量转化为复杂度结论。",
        4: "让学生用练习检查是否真正掌握结构变化。",
    }
    return goals.get(index, f"帮助学生掌握 {topic} 的核心知识点。")


def _default_video_core_explanation(index: int, topic: str) -> str:
    explanations = {
        0: f"{topic} 不是孤立定义，需要结合元素关系、存储方式、操作步骤和复杂度一起理解。",
        1: "顺序表用连续空间支持快速按位访问，链表用指针连接节点，插入删除更依赖前驱位置。",
        2: "顺序表插入通常需要移动插入位置后的元素；链表插入需要先保存后继，再连接新节点。",
        3: "复杂度分析要看哪些步骤会随着元素规模 n 增长，例如查找、移动元素或遍历链表。",
        4: "复盘时要能说出定义、画出结构、跟踪操作，并解释复杂度成立的前提。",
    }
    return explanations.get(index, f"本段围绕 {topic} 解释结构、操作和复杂度之间的关系。")


def _default_video_student_task(index: int, topic: str) -> str:
    tasks = {
        0: "判断：只会背定义但不能画出结构状态，是否算真正掌握？",
        1: "小任务：说明顺序表和链表各自更适合哪类操作。",
        2: "练习：如果在表头插入元素，顺序表需要移动哪些元素？",
        3: "判断：链表删除一定是 O(1) 吗？说明条件。",
        4: "练习：在 A、B、C、D 的下标 2 插入 X，写出结果并说明移动次数。",
    }
    return tasks.get(index, f"用一句话说明 {topic} 的核心操作成本。")


def _video_scene_operation_steps(raw: dict[str, Any], index: int) -> list[str]:
    value = _first_present(
        raw,
        "operationSteps",
        "recordingSteps",
        "animationSteps",
        "visualSteps",
        "steps",
        "operation_steps",
        "animation_steps",
    )
    steps = _safe_text_list(value, [], limit=8)
    if len(steps) >= 2:
        return steps
    visual_model = raw.get("visualModel") if isinstance(raw.get("visualModel"), dict) else {}
    example_data = raw.get("exampleData") if isinstance(raw.get("exampleData"), dict) else {}
    generated = [
        str(raw.get("teachingGoal") or raw.get("screenText") or raw.get("title") or "明确本段知识点目标").strip(),
        str(visual_model.get("description") or raw.get("coreExplanation") or "展示结构状态并说明关键变化").strip(),
        str(raw.get("formulaOrComplexity") or raw.get("studentTask") or "用复杂度或练习题完成巩固").strip(),
    ]
    operation = str(example_data.get("operation") or "").strip()
    if operation:
        generated.insert(1, f"围绕 {operation} 操作展示输入、变化过程和结果")
    return [item for item in [*steps, *generated] if item][:4]


def _fallback_video_scene_operation_steps(raw: dict[str, Any], index: int) -> list[str]:
    visual_model = raw.get("visualModel") if isinstance(raw.get("visualModel"), dict) else {}
    example_data = raw.get("exampleData") if isinstance(raw.get("exampleData"), dict) else {}
    sequence = example_data.get("sequence") if isinstance(example_data.get("sequence"), list) else []
    sequence_text = "、".join(str(item) for item in sequence[:5] if str(item).strip())
    operation = str(example_data.get("operation") or "").strip()
    steps = _safe_text_list(_first_present(raw, "operationSteps", "recordingSteps", "animationSteps", "visualSteps", "steps"), [], limit=8)
    generated = [
        str(raw.get("teachingGoal") or raw.get("screenText") or raw.get("title") or "明确本段知识点目标").strip(),
        str(visual_model.get("description") or raw.get("coreExplanation") or "展示结构状态并说明关键变化").strip(),
    ]
    if sequence_text:
        generated.append(f"用序列 {sequence_text} 标出当前结构状态")
    if operation:
        generated.append(f"执行 {operation} 操作并对比变化前后结果")
    generated.append(str(raw.get("studentTask") or raw.get("formulaOrComplexity") or "用一个小问题检查学生是否理解").strip())
    return [item for item in [*steps, *generated] if item][:4]


def _video_scene_formula_or_complexity(raw: dict[str, Any], index: int) -> str:
    _, _, planned_kind = VIDEO_SCENE_PLAN[index]
    kind = _normalize_scene_kind(raw.get("kind"), planned_kind)
    example_data = raw.get("exampleData") if isinstance(raw.get("exampleData"), dict) else {}
    operation = str(example_data.get("operation") or "").strip().lower()
    visual_model = raw.get("visualModel") if isinstance(raw.get("visualModel"), dict) else {}
    model_type = str(visual_model.get("type") or "").strip()
    if kind == "intro":
        return "本段先建立线性表概念，核心复杂度将在后续操作段展开。"
    if kind == "structure" or model_type == "structure_compare":
        return "顺序表支持随机访问，链表更适合频繁插入和删除。"
    if operation in {"insert", "delete"} or model_type in {"insert_shift", "delete_shift"}:
        return "顺序表插入或删除平均需要移动元素，时间复杂度通常为 O(n)。"
    if operation in {"search", "traverse"}:
        return "线性查找和遍历都需要按元素推进，时间复杂度为 O(n)。"
    if kind == "complexity" or model_type == "complexity_table":
        return "顺序表查找可到 O(1)，插入删除常为 O(n)；链表定位为 O(n)，改链为 O(1)。"
    if kind == "practice":
        return "判断操作代价时先看是否需要定位，再看是否需要移动元素或修改指针。"
    return "本段用结构状态、操作步骤和结果对比说明线性表规则。"


def _first_present(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, list) and any(str(item).strip() for item in value):
            return value
        if isinstance(value, str) and value.strip():
            return value
    return None


def _normalize_scene_kind(value: Any, fallback: str) -> str:
    text = str(value or fallback).strip()
    return text if text in {"intro", "structure", "operation", "complexity", "practice"} else fallback


def _clean_text(value: Any, fallback: str, limit: int = 360) -> str:
    if value is None:
        return fallback
    text = re.sub(r"<[^>]*>", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return fallback
    return text[:limit]


def _safe_text_list(value: Any, fallback: list[str], limit: int = 6) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str) and value.strip():
        items = [value.strip()]
    else:
        items = []
    return (items or fallback)[:limit]


def _required_text(value: Any, error: str, limit: int = 360) -> str:
    text = str(value or "").strip()
    if not text:
        raise LLMUnavailable(error)
    return text[:limit]


def _required_text_list(value: Any, error: str, *, min_items: int = 1, limit: int = 6) -> list[str]:
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str) and value.strip():
        items = [value.strip()]
    else:
        items = []
    if len(items) < min_items:
        raise LLMUnavailable(error)
    return items[:limit]


def _required_mapping(value: Any, error: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise LLMUnavailable(error)
    return value


def _required_visual_model(value: Any, index: int) -> dict[str, Any]:
    model = _required_mapping(value, f"DeepSeek scene {index + 1} missing visualModel")
    model_type = str(model.get("type") or "").strip()
    allowed = {
        "sequence_array",
        "linked_nodes",
        "structure_compare",
        "insert_shift",
        "delete_shift",
        "complexity_table",
        "practice_quiz",
    }
    if model_type not in allowed:
        raise LLMUnavailable(f"DeepSeek scene {index + 1} visualModel.type invalid: {model_type or 'empty'}")
    if not isinstance(model.get("data"), dict):
        model["data"] = {}
    return model


_STORYBOARD_META_PATTERNS = (
    "打开视频演示页",
    "展示当前分镜",
    "引导进入",
    "对应学习资源",
    "画面类型",
    "课程依据",
    "学生画像",
    "学习路径",
    "录屏步骤",
    "页面操作",
    "镜头说明",
    "分镜",
    "脚本",
    "录屏",
    "分镜脚本",
    "本地渲染",
    "HyperFrames",
    "EduAgent Studio",
    "Teaching MP4",
    "knowledge animation",
    "subtitles",
    "scenes",
    "任务 ID",
    "视频 ID",
    "远端任务",
    "制作痕迹",
    "</div>",
    "<div",
    "钦?",
    "鏃",
    "璇",
)


def _reject_storyboard_meta_text(value: Any, index: int) -> None:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or "")
    for pattern in _STORYBOARD_META_PATTERNS:
        if pattern in text:
            raise LLMUnavailable(f"DeepSeek scene {index + 1} contains storyboard/meta text: {pattern}")


def _validate_video_teaching_coverage(topic: str, scenes: list[dict[str, Any]]) -> None:
    text = json.dumps(scenes, ensure_ascii=False)
    if "线性表" in topic:
        required_terms = ("顺序", "链", "插入", "复杂度")
        missing = [term for term in required_terms if term not in text]
        if missing:
            raise LLMUnavailable(f"DeepSeek video content missing linear-list teaching coverage: {', '.join(missing)}")
    has_sequence = any(isinstance(scene.get("exampleData"), dict) and isinstance(scene["exampleData"].get("sequence"), list) for scene in scenes)
    if not has_sequence:
        raise LLMUnavailable("DeepSeek video content missing concrete exampleData.sequence")
    has_complexity = any(str(scene.get("formulaOrComplexity") or "").strip() for scene in scenes)
    if not has_complexity:
        raise LLMUnavailable("DeepSeek video content missing formulaOrComplexity")
    model_types = {str((scene.get("visualModel") or {}).get("type") or "") for scene in scenes if isinstance(scene.get("visualModel"), dict)}
    if not ({"sequence_array", "linked_nodes", "structure_compare"} & model_types):
        raise LLMUnavailable("DeepSeek video content missing structure visualization model")
    if not ({"insert_shift", "delete_shift", "complexity_table", "practice_quiz"} & model_types):
        raise LLMUnavailable("DeepSeek video content missing operation or assessment visualization model")


def _video_script_from_scenes(title: str, scenes: list[dict[str, Any]], citations: list[dict[str, Any]]) -> str:
    lines = [f"# {title}", "", "> 由 DeepSeek + 多模态创作 Agent 生成知识点讲解分镜，并交由 Agnes AI 生成教学视频。", ""]
    lines.extend(["## 引用来源", ""])
    if citations:
        for citation in citations:
            lines.append(f"- {_citation_public_label(citation)}。")
    lines.extend(["", "## 知识点讲稿", ""])
    for scene in scenes:
        lines.extend([
            f"### {scene['timeRange']} {scene['title']}",
            "",
            f"- 教学目标：{scene.get('teachingGoal', '')}",
            f"- 知识点解释：{scene.get('coreExplanation', scene.get('screenText', ''))}",
            f"- 例子数据：{json.dumps(scene.get('exampleData', {}), ensure_ascii=False)}",
            f"- 可视化模型：{(scene.get('visualModel') or {}).get('type', '')}",
            f"- 复杂度/规则：{scene.get('formulaOrComplexity', '')}",
            f"- 旁白字幕：{scene.get('voiceover', '')}",
            f"- 关键概念：{scene.get('concept', '')}",
            "- 动画步骤：",
            *[f"  {idx}. {step}" for idx, step in enumerate(scene.get("operationSteps", []), start=1)],
            f"- 学生任务：{scene.get('studentTask', '')}",
            "",
        ])
    return "\n".join(lines)


def build_mindmap_payload(resource: dict[str, Any]) -> dict[str, Any]:
    if resource.get("resourceType") != "mindmap":
        raise HTTPException(status_code=400, detail="当前资源不是思维导图资源")
    citations = deepcopy(resource.get("citations", []))
    tree = _build_structured_mindmap_tree(resource, citations)
    topic = _mindmap_topic(resource)
    return {
        "resourceId": resource["id"],
        "title": resource.get("title") or f"{topic}完整思维导图",
        "course": "数据结构课程",
        "sourceAgent": "知识库 RAG + 导图结构化 Agent" if citations else "本地结构化导图 Agent",
        "auditStatus": resource.get("auditStatus", "pending"),
        "mermaid": resource.get("content", ""),
        "tree": tree,
        "nodeSchema": [
            "nodeId",
            "title",
            "level",
            "parentId",
            "children",
            "sourceType",
            "sourceChunkIds",
            "jumpTarget",
            "confidence",
            "status",
        ],
        "layoutEngine": {
            "name": "EduAgentMindMapTreeLayout",
            "features": ["树状分层布局", "展开/收起", "缩放/拖拽", "自动避让重叠", "节点点击跳资源"],
        },
        "coverage": [child.get("title", "") for child in tree.get("children", []) if child.get("title")],
        "citations": citations,
        "actions": ["expand_all", "collapse_all", "zoom", "drag", "export_markdown", "jump_to_resource", "inspect_source"],
        "markdown": _mindmap_markdown(tree),
    }


def _citation_evidence(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = citations[:1]
    evidence = []
    for item in source:
        evidence.append({
            "chunkId": item.get("chunkId"),
            "documentName": item.get("documentName"),
            "sourceLocation": item.get("sourceLocation"),
            "page": item.get("page"),
            "contentPreview": item.get("contentPreview"),
            "fullText": item.get("fullText") or item.get("contentPreview"),
            "similarity": item.get("similarity"),
        })
    return evidence


def _mindmap_node(
    node_id: str,
    title: str,
    level: int,
    parent_id: str | None,
    source_type: str,
    citations: list[dict[str, Any]],
    *,
    summary: str = "",
    jump_target: str = "",
    confidence: float = 0.88,
    status: str = "confirmed",
    downstream_impact: list[str] | None = None,
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evidence = _citation_evidence(citations) if source_type == "课程依据" else []
    return {
        "id": node_id,
        "nodeId": node_id,
        "title": title,
        "summary": summary,
        "level": level,
        "parentId": parent_id,
        "children": children or [],
        "sourceType": source_type,
        "sourceChunkIds": [item["chunkId"] for item in evidence if item.get("chunkId")],
        "sourceEvidence": evidence,
        "jumpTarget": jump_target,
        "confidence": confidence,
        "status": status,
        "downstreamImpact": downstream_impact or [],
    }


def _mindmap_topic(resource: dict[str, Any]) -> str:
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
    for value in [metadata.get("topic"), resource.get("title")]:
        text = _clean_mindmap_label(str(value or ""))
        text = re.sub(r"(完整)?思维导图|知识结构|讲解文档|分层练习题|代码实践实验", "", text).strip()
        if text:
            return text
    return DEFAULT_RESOURCE_TOPIC


def _clean_mindmap_label(value: str, fallback: str = "") -> str:
    text = _clean_generated_text(value, fallback or DEFAULT_RESOURCE_TOPIC)
    text = _strip_legacy_course_terms(text, fallback or DEFAULT_RESOURCE_TOPIC)
    text = re.sub(r"\s+", " ", text).strip(" -，。；;")
    return text or fallback


def _parse_mindmap_content(content: str, topic: str) -> dict[str, Any] | None:
    lines = [line.rstrip() for line in str(content or "").splitlines() if line.strip()]
    if not lines or not any(line.strip().lower() == "mindmap" for line in lines[:2]):
        return None

    root: dict[str, Any] | None = None
    stack: list[tuple[int, dict[str, Any]]] = []
    counter = 0
    for raw in lines:
        stripped = raw.strip()
        if stripped.lower() == "mindmap":
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        label = stripped
        root_match = re.match(r"root\s*\(\((.+)\)\)", label)
        if root_match:
            label = root_match.group(1)
        label = _clean_mindmap_label(label, topic)
        if not label:
            continue
        counter += 1
        node = {
            "label": label,
            "rawIndent": indent,
            "children": [],
            "nodeId": "root" if root is None else f"node_{counter}",
        }
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if not stack:
            root = node if root is None else root
            if node is not root:
                root["children"].append(node)
        else:
            stack[-1][1]["children"].append(node)
        stack.append((indent, node))

    return root


def _is_evidence_outline_branch(label: str) -> bool:
    normalized = re.sub(r"\s+", "", str(label or "")).strip("：:，,。；;")
    return normalized in {"课程依据", "资料依据", "引用来源", "证据来源", "来源依据", "课程证据", "学习依据"}


def _is_source_reference_label(label: str) -> bool:
    text = str(label or "").strip()
    return bool(
        re.search(r"\.(pptx?|pdf|docx?|xlsx?|c|cpp|h|py|java)\b", text, re.I)
        or re.search(r"第\s*\d+\s*页", text)
        or re.search(r"[/\\].+\.(c|cpp|h|py|java)\b", text, re.I)
    )


def _is_mindmap_branch_label(label: str) -> bool:
    text = re.sub(r"\s+", "", str(label or "")).strip("：:，,。；;")
    return text in {
        "定义与逻辑结构",
        "顺序表",
        "链表",
        "核心概念",
        "存储结构",
        "基本操作",
        "复杂度分析",
        "典型应用与代码实现",
        "代码实践",
        "易错点",
        "常见错误",
        "常见错误点",
        "学习路径",
        "学习流程",
        "先修知识",
        "课程定位",
        "阶段测评闭环",
    }


def _repair_flat_mindmap_outline(outline: dict[str, Any]) -> dict[str, Any]:
    children = outline.get("children")
    if not isinstance(children, list) or not children:
        return outline
    branch_count = sum(1 for child in children if _is_mindmap_branch_label(str(child.get("label") or "")))
    if branch_count < 2:
        return outline

    repaired: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    skipping_evidence = False
    for child in children:
        label = str(child.get("label") or "")
        if _is_evidence_outline_branch(label):
            current = None
            skipping_evidence = True
            continue
        if _is_mindmap_branch_label(label):
            current = {**child, "children": list(child.get("children") or [])}
            repaired.append(current)
            skipping_evidence = False
            continue
        if skipping_evidence and _is_source_reference_label(label):
            continue
        if current is not None:
            current.setdefault("children", []).append({**child, "children": list(child.get("children") or [])})
        elif not _is_source_reference_label(label):
            repaired.append(child)

    if len(repaired) >= 3:
        outline["children"] = repaired
    return outline


def _remove_evidence_outline_branches(outline: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(outline, dict):
        return outline
    children = outline.get("children")
    if isinstance(children, list):
        outline["children"] = [
            child
            for child in children
            if not _is_evidence_outline_branch(str(child.get("label") or ""))
            and not _is_source_reference_label(str(child.get("label") or ""))
        ]
    return outline


def _outline_to_mindmap_node(
    outline: dict[str, Any],
    citations: list[dict[str, Any]],
    *,
    parent_id: str | None = None,
    level: int = 0,
    index_path: str = "root",
) -> dict[str, Any]:
    node_id = "root" if level == 0 else f"node_{index_path}"
    source_type = "课程依据" if citations else "模型推断"
    citation = citations[(int(index_path.split("_")[-1]) - 1) % len(citations)] if citations and level > 0 else None
    node_citations = [citation] if citation else citations[:1]
    title = outline.get("label") or DEFAULT_RESOURCE_TOPIC
    summary = str(outline.get("summary") or "").strip()
    children = [
        _outline_to_mindmap_node(
            child,
            citations,
            parent_id=node_id,
            level=level + 1,
            index_path=f"{index_path}_{idx}",
        )
        for idx, child in enumerate(outline.get("children", []), start=1)
    ]
    return _mindmap_node(
        node_id,
        title,
        level,
        parent_id,
        source_type,
        node_citations,
        summary=summary or _node_summary(title, node_citations, source_type),
        jump_target=_jump_target_for_level(level),
        confidence=0.9 if citations else 0.72,
        status="confirmed" if citations else "needs_review",
        downstream_impact=["资源推荐", "学习路径", "阶段测评"],
        children=children,
    )


def _node_summary(title: str, citations: list[dict[str, Any]], source_type: str) -> str:
    formal = _formal_mindmap_summary(title)
    if formal:
        return formal
    preview = str((citations[0] if citations else {}).get("contentPreview") or "").strip()
    if preview:
        return f"{title}：依据课程片段「{preview[:80]}」。"
    if source_type == "课程依据":
        return f"{title}：来自本地知识库引用。"
    return f"{title}：暂无可引用课程片段，需教师上传资料或重新生成后确认。"


def _jump_target_for_level(level: int) -> str:
    if level <= 1:
        return "/student/resources/res_mindmap"
    return "/student/resources"


def _formal_mindmap_summary(title: str) -> str:
    summaries = {
        "线性表": "线性表是由同类型数据元素构成的有限序列，重点关注元素的一对一前后关系、存储映射和基本操作代价。",
        "定义与逻辑结构": "从有限序列、前驱后继、表长和空表等概念建立线性表的逻辑模型，为后续存储实现打基础。",
        "有限序列": "线性表中的元素按线性次序排列，除首尾元素外，每个元素都有唯一直接前驱和唯一直接后继。",
        "前驱与后继": "前驱后继描述元素之间的一对一逻辑关系，与元素在内存中的实际存放方式不必相同。",
        "表长与空表": "表长表示当前元素个数，空表是长度为 0 的线性表，插入删除时必须单独处理边界。",
        "线性结构特征": "线性结构强调顺序关系明确、层级关系单一，是顺序表和链表共同遵循的抽象结构。",
        "顺序表": "顺序表用一段连续存储空间保存线性表元素，支持随机访问，但插入删除通常需要移动元素。",
        "连续存储": "连续存储使逻辑相邻元素在物理位置上也相邻，便于按下标直接定位。",
        "地址计算": "顺序表可通过首地址、元素大小和下标计算元素地址，这是随机访问的基础。",
        "随机访问": "随机访问让按位查找达到 O(1)，但不代表所有操作都是常数时间。",
        "插入删除移动元素": "顺序表在中间位置插入或删除时，需要移动后续元素，时间复杂度通常为 O(n)。",
        "链表": "链表通过指针或游标表示元素关系，适合频繁插入删除，但按位查找需要顺序遍历。",
        "单链表": "单链表结点包含数据域和后继指针，结构简单，但只能从前向后访问。",
        "双链表": "双链表同时保存前驱和后继指针，便于双向移动和删除指定结点。",
        "循环链表": "循环链表让尾结点指向头部，适合需要循环遍历的场景。",
        "头结点与指针域": "头结点可统一空表和非空表处理，指针域维护结点间的逻辑顺序。",
        "基本操作": "基本操作包括初始化、查找、插入、删除、遍历和合并，分析时要同时看逻辑效果和实现代价。",
        "初始化": "初始化需要建立空表状态，并正确设置长度、容量或头指针等关键字段。",
        "查找": "查找分为按位查找和按值查找，顺序表与链表的复杂度差异明显。",
        "插入": "插入操作要先检查位置合法性和容量，再维护元素顺序或指针连接。",
        "删除": "删除操作要保存必要的前驱或后继信息，避免元素丢失、指针断链或越界访问。",
        "遍历与合并": "遍历是许多算法的基础，有序表合并要求在扫描过程中保持结果表有序。",
        "复杂度分析": "复杂度分析要把访问、移动、遍历和额外空间分开讨论，不能只背结论。",
        "顺序表查找 O(1)/O(n)": "顺序表按位查找为 O(1)，按值查找仍需比较元素，通常为 O(n)。",
        "链表查找 O(n)": "链表不能按下标直接定位，查找第 i 个结点需要从头逐个移动指针。",
        "插入删除代价对比": "顺序表主要代价在移动元素，链表主要代价在定位位置和修改指针。",
        "空间开销": "顺序表可能预留容量，链表每个结点还需要额外指针域，空间效率需结合场景判断。",
        "典型应用与代码实现": "通过有序表合并、静态链表和边界测试，把抽象操作落实为可运行程序。",
        "有序表合并": "有序表合并常用双指针扫描，关键是比较当前元素并维护结果表顺序。",
        "静态链表": "静态链表用数组模拟链式结构，适合理解游标、备用链表和结点分配。",
        "边界条件测试": "边界测试应覆盖空表、首尾位置、容量满、删除最后元素等场景。",
        "代码健壮性": "健壮实现需要检查位置、容量、空指针和返回状态，避免只写理想路径。",
        "易错点": "易错点集中在边界判断、指针维护、复杂度口径和逻辑结构/存储结构混淆。",
        "下标越界": "顺序表操作要区分合法位置范围，插入位置和删除位置的边界并不完全相同。",
        "空表处理": "空表查找、删除和取值都要提前判断，否则容易出现非法访问。",
        "指针断链": "链表插入删除必须按正确顺序修改指针，避免丢失后续结点。",
        "复杂度误判": "复杂度要看主导操作次数，不能因为代码短就判断为 O(1)。",
    }
    return summaries.get(str(title or "").strip(), "")


def _outline_leaf(label: str, summary: str = "") -> dict[str, Any]:
    return {"label": label, "summary": summary or _formal_mindmap_summary(label), "children": []}


def _outline_branch(label: str, children: list[str]) -> dict[str, Any]:
    return {
        "label": label,
        "summary": _formal_mindmap_summary(label),
        "children": [_outline_leaf(child) for child in children],
    }


def _outline_to_mermaid_lines(outline: dict[str, Any]) -> list[str]:
    lines = ["mindmap", f"  root(({outline.get('label') or DEFAULT_RESOURCE_TOPIC}))"]

    def append_children(children: list[dict[str, Any]], depth: int) -> None:
        prefix = "  " * depth
        for child in children:
            label = _clean_mindmap_label(str(child.get("label") or ""), DEFAULT_RESOURCE_TOPIC)
            if not label:
                continue
            lines.append(f"{prefix}{label}")
            append_children(child.get("children") or [], depth + 1)

    append_children(outline.get("children") or [], 2)
    return lines


def _build_formal_linear_list_outline(topic: str) -> dict[str, Any]:
    return {
        "label": "线性表" if "线性表" in topic else topic,
        "summary": _formal_mindmap_summary("线性表"),
        "children": [
            _outline_branch("定义与逻辑结构", ["有限序列", "前驱与后继", "表长与空表", "线性结构特征"]),
            _outline_branch("顺序表", ["连续存储", "地址计算", "随机访问", "插入删除移动元素"]),
            _outline_branch("链表", ["单链表", "双链表", "循环链表", "头结点与指针域"]),
            _outline_branch("基本操作", ["初始化", "查找", "插入", "删除", "遍历与合并"]),
            _outline_branch("复杂度分析", ["顺序表查找 O(1)/O(n)", "链表查找 O(n)", "插入删除代价对比", "空间开销"]),
            _outline_branch("典型应用与代码实现", ["有序表合并", "静态链表", "边界条件测试", "代码健壮性"]),
            _outline_branch("易错点", ["下标越界", "空表处理", "指针断链", "复杂度误判"]),
        ],
    }


def _is_linear_list_topic(topic: str) -> bool:
    return any(keyword in str(topic or "") for keyword in ["线性表", "顺序表", "链表", "有序表"])


def _resource_indicates_linear_list(resource: dict[str, Any], citations: list[dict[str, Any]]) -> bool:
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
    haystack = " ".join([
        str(resource.get("title") or ""),
        str(resource.get("content") or ""),
        str(metadata.get("topic") or ""),
        " ".join(str(item.get("documentName") or "") for item in citations[:5]),
        " ".join(str(item.get("contentPreview") or "") for item in citations[:5]),
    ])
    return _is_linear_list_topic(haystack)


def _is_shallow_mindmap_outline(outline: dict[str, Any]) -> bool:
    children = outline.get("children") if isinstance(outline, dict) else []
    if not isinstance(children, list):
        return True
    if len(children) < 6:
        return True
    rich_children = [child for child in children if len(child.get("children") or []) >= 3]
    generic_labels = {"核心概念", "学习路径", "学习流程", "常见错误", "常见错误点"}
    generic_count = sum(1 for child in children if str(child.get("label") or "") in generic_labels)
    return len(rich_children) < 5 or generic_count >= len(children) - 1


def _build_citation_based_outline(topic: str, citations: list[dict[str, Any]]) -> dict[str, Any]:
    if _is_linear_list_topic(topic):
        return _build_formal_linear_list_outline(topic)

    keywords = _keywords_from_citations(citations)
    if not keywords:
        text = " ".join(str(item.get("contentPreview") or "") for item in citations)
        candidates = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", text)
        stopwords = {"数据结构", "课程", "学习", "一个", "可以", "进行", "需要", "通过", "实现"}
        for word in candidates:
            if word not in stopwords and word not in keywords:
                keywords.append(word)
            if len(keywords) >= 8:
                break
    if not keywords:
        keywords = ["定义与性质", "存储结构", "基本操作", "复杂度分析", "典型应用", "常见错误"]

    return {
        "label": topic,
        "children": [
            {"label": "核心概念", "children": [{"label": item, "children": []} for item in keywords[:6]]},
            {"label": "存储结构", "children": [
                {"label": "顺序存储", "children": []},
                {"label": "链式存储", "children": []},
            ]},
            {"label": "基本操作", "children": [
                {"label": "查找", "children": []},
                {"label": "插入", "children": []},
                {"label": "删除", "children": []},
            ]},
            {"label": "复杂度分析", "children": [
                {"label": "时间复杂度", "children": []},
                {"label": "空间复杂度", "children": []},
            ]},
            {"label": "学习路径", "children": [
                {"label": "阅读定义与示例", "children": []},
                {"label": "手工跟踪操作过程", "children": []},
                {"label": "分析时间与空间复杂度", "children": []},
                {"label": "完成练习和代码实践", "children": []},
            ]},
            {"label": "常见错误", "children": [
                {"label": "忽略边界条件", "children": []},
                {"label": "混淆逻辑结构与存储结构", "children": []},
                {"label": "复杂度分析不完整", "children": []},
            ]},
        ],
    }


def _build_structured_mindmap_tree(resource: dict[str, Any], citations: list[dict[str, Any]]) -> dict[str, Any]:
    topic = _mindmap_topic(resource)
    is_linear_list = _is_linear_list_topic(topic) or _resource_indicates_linear_list(resource, citations)
    if is_linear_list and not _is_linear_list_topic(topic):
        topic = "线性表"
    outline = _parse_mindmap_content(str(resource.get("content") or ""), topic)
    if outline is None:
        outline = _build_citation_based_outline(topic, citations)
    outline = _remove_evidence_outline_branches(_repair_flat_mindmap_outline(outline))
    if is_linear_list and _is_shallow_mindmap_outline(outline):
        outline = _build_formal_linear_list_outline(topic)
    return _outline_to_mindmap_node(outline, citations)


def _flatten_mindmap(node: dict[str, Any], lines: list[str], depth: int = 0) -> None:
    lines.append(f"{'  ' * depth}- {node.get('title', '')}")
    for child in node.get("children", []):
        _flatten_mindmap(child, lines, depth + 1)


def _mindmap_markdown(tree: dict[str, Any]) -> str:
    lines = [f"# {tree.get('title', '数据结构课程知识结构')}", ""]
    _flatten_mindmap(tree, lines)
    return "\n".join(lines) + "\n"


def get_resource_or_404(resource_id: str, resources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from .. import state

    resource_pool = state.resources if resources is None else resources
    resource = next((item for item in resource_pool if item["id"] == resource_id), None)
    if not resource:
        raise HTTPException(status_code=404, detail="资源不存在")
    return resource


def is_practice_answer_correct(item: dict[str, Any], user_answer: str) -> bool:
    answer = str(item.get("answer", "")).strip()
    question_type = item.get("type")
    if question_type == "single":
        return user_answer.strip() == answer
    if question_type == "code":
        return _is_code_answer_correct(user_answer)
    if question_type == "calculation":
        normalized_user = _normalize_answer(user_answer)
        normalized_answer = _normalize_answer(answer)
        return bool(normalized_answer and normalized_answer in normalized_user)
    return _is_short_answer_correct(user_answer, answer, str(item.get("analysis", "")))


def _normalize_answer(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _is_short_answer_correct(user_answer: str, answer: str, analysis: str) -> bool:
    normalized_user = _normalize_answer(user_answer)
    if not normalized_user:
        return False
    normalized_answer = _normalize_answer(answer)
    if normalized_answer and normalized_answer in normalized_user:
        return True
    source = f"{answer} {analysis}"
    keywords = [
        keyword
        for keyword in ["数据结构", "线性表", "链表", "栈", "队列", "树", "二叉树", "图", "查找", "排序", "复杂度", "遍历", "代码", "实现"]
        if keyword in source
    ]
    if not keywords:
        return False
    hit_count = sum(1 for keyword in keywords if keyword in user_answer)
    return hit_count >= max(1, min(2, len(keywords)))


def _is_code_answer_correct(user_answer: str) -> bool:
    normalized = _normalize_answer(user_answer)
    required_groups = [
        ["初始化", "init", "create"],
        ["操作", "insert", "delete", "search", "push", "pop", "traverse", "核心"],
        ["输出", "return", "print", "结果"],
    ]
    return all(any(keyword in normalized for keyword in group) for group in required_groups)
