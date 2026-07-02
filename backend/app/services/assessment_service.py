from __future__ import annotations

from copy import deepcopy
import json
import os
import re
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from .. import state
from ..demo_data import now_text
from ..persistence import load_json
from ..utils import user_scoped_key
from .knowledge_service import search_chunks
from .knowledge_point_service import (
    clean_rubric_keywords,
    context_from_citation,
    normalize_knowledge_point,
    sanitize_knowledge_points,
)
from .resource_service import is_practice_answer_correct, sanitize_resource_for_display
from .strict_generation import raise_blocked

QUESTION_BLUEPRINT = [
    "single", "single", "single", "single", "single",
    "short", "short",
    "calculation", "calculation",
    "code", "code",
    "case",
]

DIFFICULTY_BY_TYPE = {
    "single": "基础",
    "short": "进阶",
    "calculation": "进阶",
    "code": "综合",
    "case": "综合",
}


def generate_assessment_paper(user_id: str) -> dict[str, Any]:
    context = _assessment_context(user_id)
    active_stage = next(
        (
            stage
            for stage in state.load_user_learning_path(user_id).get("stages", [])
            if stage.get("status") in {"active", "awaiting_assessment"}
        ),
        None,
    )
    citations = _collect_citations(context["topics"], context["resources"])
    if len(citations) < 2:
        raise_blocked(
            status_code=409,
            agent_name="学习评估 Agent",
            message="本地知识库命中不足，无法生成正式测评题。",
            missing_requirements=["至少 2 条真实课程引用", "已审核学习资源或知识库命中"],
        )

    questions = _questions_from_resource_exercises(context["resources"], citations)
    questions.extend(_questions_from_citations(context["topics"], citations))
    questions = _dedupe_questions(questions)
    questions = _balance_questions(questions, citations)
    if len(questions) < 10 or len({item["type"] for item in questions}) < 4:
        raise_blocked(
            status_code=409,
            agent_name="学习评估 Agent",
            message="真实课程资料不足以覆盖正式测评题型，已停止生成；不会使用静态题库补齐。",
            missing_requirements=["至少 10 道真实依据题", "至少 4 类题型"],
        )

    questions = questions[:12]
    for index, question in enumerate(questions, start=1):
        question["id"] = f"q{index}"
        question["order"] = index

    paper = {
        "id": f"assessment_{uuid4().hex[:8]}",
        "assessmentId": "",
        "userId": user_id,
        "title": "数据结构课程阶段测评",
        "course": "数据结构课程",
        "questions": questions,
        "sourceSummary": _source_summary(citations, context),
        "createdAt": now_text(),
        "rubricVersion": "rubric_data_structure_dynamic_v1",
        "stageSnapshot": {
            "id": active_stage.get("id"),
            "name": active_stage.get("name"),
            "knowledgePoints": deepcopy(active_stage.get("knowledgePoints", [])),
            "resourceIds": deepcopy(active_stage.get("resources", [])),
        } if active_stage else None,
        "metadata": {
            "generationMode": "local_knowledge_base_and_resource_snapshot",
            "topicCandidates": context["topics"],
            "questionCount": len(questions),
            "questionTypes": sorted({item["type"] for item in questions}),
            "sourceChunkIds": [item["chunkId"] for item in citations],
        },
    }
    paper["assessmentId"] = paper["id"]
    return paper


def evaluate_answers(paper: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    questions = paper.get("questions", [])
    details = [score_question(question, answers.get(question["id"])) for question in questions]
    total_weight = sum(float(question.get("scoreWeight", 1)) for question in questions) or 1
    weighted_score = sum(
        item["score"] * float(question.get("scoreWeight", 1))
        for item, question in zip(details, questions)
    )
    score = round(weighted_score / total_weight)
    weak_points = sanitize_knowledge_points(sorted({
        item["knowledge_point"]
        for item in details
        if item["score"] < 70 and item.get("knowledge_point")
    }))
    error_reasons = [item["error_reason"] for item in details if item.get("error_reason")]
    return {
        "score": score,
        "weakness": weak_points,
        "error_reasons": error_reasons,
        "question_details": details,
        "rubric_version": paper.get("rubricVersion", "rubric_data_structure_dynamic_v1"),
    }


def score_question(question: dict[str, Any], answer: Any) -> dict[str, Any]:
    question_type = str(question.get("type") or "")
    user_answer = str(answer or "").strip()
    expected = str(question.get("answer") or "").strip()
    rubric = _normalize_rubric(question)
    if question_type == "single":
        correct = _normalize_answer(user_answer) == _normalize_answer(expected)
        score = 100 if correct else 0
        missing = [] if correct else [expected]
        hit = [expected] if correct else []
        error_reason = "" if correct else "单选题选项错误，未能识别课程资料中的关键概念。"
    elif question_type in {"short", "calculation", "case"}:
        hit, missing = _rubric_hits(user_answer, rubric)
        score = round(len(hit) / max(len(rubric), 1) * 100)
        correct = score >= 70
        error_reason = "" if correct else f"{_type_label(question_type)}缺少关键评分点：{'、'.join(missing[:3]) or '课程依据'}。"
    elif question_type == "code":
        hit, missing = _rubric_hits(user_answer, rubric)
        base_score = round(len(hit) / max(len(rubric), 1) * 80)
        structure_bonus = 20 if is_practice_answer_correct({"type": "code", "answer": expected}, user_answer) else 0
        score = min(100, base_score + structure_bonus)
        correct = score >= 70
        error_reason = "" if correct else f"代码题缺少关键步骤：{'、'.join(missing[:3]) or '初始化、核心操作、输出'}。"
    else:
        hit, missing = _rubric_hits(user_answer, rubric)
        score = round(len(hit) / max(len(rubric), 1) * 100)
        correct = score >= 70
        error_reason = "" if correct else "答案未达到本题 Rubric 要求。"

    return {
        "question_id": question["id"],
        "knowledge_point": normalize_knowledge_point(
            question.get("knowledgePoint"),
            context=" ".join([
                str(question.get("stem") or ""),
                str(question.get("analysis") or ""),
                context_from_citation((question.get("citations") or [{}])[0]),
            ]),
        ),
        "score": score,
        "correct": correct,
        "hit_keywords": hit,
        "missing_keywords": missing,
        "error_reason": error_reason,
        "rubric": f"Rubric 关键词评分：{'、'.join(rubric)}",
    }


def _assessment_context(user_id: str) -> dict[str, Any]:
    path = state.load_user_learning_path(user_id)
    resources = [sanitize_resource_for_display(item) for item in state.load_user_resources(user_id)]
    profile_items = load_json(user_scoped_key("profile_items", user_id), [])
    latest = next((item for item in state.assessment_results if item.get("userId") == user_id), {})
    topics: list[str] = []

    active_stage = next((stage for stage in path.get("stages", []) if stage.get("status") == "active"), None)
    if isinstance(active_stage, dict):
        topics.extend(str(item) for item in active_stage.get("knowledgePoints", []) if str(item).strip())
        topics.append(str(active_stage.get("chapterName") or active_stage.get("name") or "").strip())
    for item in profile_items:
        if str(item.get("dimension", "")).find("薄弱") >= 0:
            topics.extend(re.split(r"[、,，;\s]+", str(item.get("value", ""))))
    topics.extend(str(item) for item in latest.get("weakness", []) if str(item).strip())
    for resource in resources:
        metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
        for value in [metadata.get("topic"), resource.get("title")]:
            if str(value or "").strip():
                topics.append(str(value).strip())
    topics.append("数据结构")

    return {
        "path": path,
        "resources": resources,
        "topics": _unique_nonempty(topics)[:8],
        "profileItems": profile_items,
    }


def _collect_citations(topics: list[str], resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for resource in resources:
        for citation in resource.get("citations", []) or []:
            normalized = _normalize_citation(citation)
            if normalized:
                citations.append(normalized)
    for topic in topics[:5]:
        retrieval = _search_chunks_for_assessment(topic, 8)
        if retrieval.get("coverage") == "none":
            continue
        for item in retrieval.get("items", []):
            normalized = _normalize_citation(item)
            if normalized:
                citations.append(normalized)
    return _dedupe_citations(citations)


def _questions_from_resource_exercises(resources: list[dict[str, Any]], citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    citation_by_id = {str(item.get("chunkId")): item for item in citations}
    for resource in resources:
        if resource.get("resourceType") != "exercise":
            continue
        try:
            exercises = json.loads(str(resource.get("content") or "[]"))
        except json.JSONDecodeError:
            continue
        if not isinstance(exercises, list):
            continue
        for item in exercises:
            if not isinstance(item, dict):
                continue
            citation = citation_by_id.get(str(item.get("citationChunkId"))) or citations[len(questions) % len(citations)]
            question = _question_base(
                item.get("type", "short"),
                str(item.get("stem") or ""),
                str(item.get("answer") or ""),
                str(item.get("analysis") or ""),
                citation,
                options=item.get("options"),
            )
            if question["stem"]:
                questions.append(question)
    return questions


def _questions_from_citations(topics: list[str], citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for index, question_type in enumerate(QUESTION_BLUEPRINT):
        citation = citations[index % len(citations)]
        topic = topics[index % len(topics)] if topics else "数据结构"
        keywords = _keywords_for_citation(citation, topic)
        concept = keywords[0]
        operation = keywords[1] if len(keywords) > 1 else topic
        excerpt = _clean_excerpt(str(citation.get("contentPreview") or citation.get("fullText") or topic), 160)
        if question_type == "single":
            answer = f"{concept}的定义、存储方式、基本操作和复杂度"
            stem = f"根据课程资料，学习「{topic}」时最应优先对应哪组内容？"
            options = [
                answer,
                "只记住资料标题和页码",
                "只阅读代码文件名，不分析操作过程",
                "跳过边界条件，直接背结论",
            ]
            analysis = f"课程片段强调从概念、结构、操作和复杂度建立联系。依据：{excerpt}"
            questions.append(_question_base("single", stem, answer, analysis, citation, options=options, rubric=[concept, "基本操作", "复杂度"]))
        elif question_type == "short":
            stem = f"结合课程引用，用 2-3 句话说明「{topic}」中「{concept}」的作用。"
            analysis = f"回答应围绕课程片段中的定义、结构关系或操作语句展开。依据：{excerpt}"
            questions.append(_question_base("short", stem, concept, analysis, citation, rubric=[concept, topic, "作用"]))
        elif question_type == "calculation":
            stem = f"分析「{topic}」中「{operation}」相关操作时，至少写出时间复杂度和空间复杂度的判断依据。"
            answer = "时间复杂度 空间复杂度 操作次数 数据规模"
            analysis = f"复杂度题需要说明操作次数如何随数据规模变化，并补充空间占用。依据：{excerpt}"
            questions.append(_question_base("calculation", stem, answer, analysis, citation, rubric=["时间复杂度", "空间复杂度", "操作次数", "数据规模"]))
        elif question_type == "code":
            stem = f"写出「{topic}」中「{operation}」的伪代码框架，要求体现初始化、核心操作和结果输出。"
            answer = "初始化 核心操作 输出 边界条件"
            analysis = f"代码题重点检查能否把课程知识转成可执行步骤。依据：{excerpt}"
            questions.append(_question_base("code", stem, answer, analysis, citation, rubric=["初始化", "核心操作", "输出", "边界"]))
        else:
            stem = f"综合应用：如果要向同学讲清楚「{topic}」，请基于课程资料设计“概念解释 -> 操作追踪 -> 复杂度分析 -> 代码验证”的学习方案。"
            answer = "概念解释 操作追踪 复杂度分析 代码验证"
            analysis = f"综合题要求把课程引用转化为完整学习闭环。依据：{excerpt}"
            questions.append(_question_base("case", stem, answer, analysis, citation, rubric=["概念", "操作", "复杂度", "代码"]))
    return questions


def _balance_questions(questions: list[dict[str, Any]], citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_type: dict[str, list[dict[str, Any]]] = {item: [] for item in ["single", "short", "calculation", "code", "case"]}
    for question in questions:
        question_type = str(question.get("type") or "short")
        if question_type in by_type:
            by_type[question_type].append(question)
    balanced: list[dict[str, Any]] = []
    targets = {"single": 5, "short": 2, "calculation": 2, "code": 2, "case": 1}
    for question_type, count in targets.items():
        pool = by_type.get(question_type, [])
        balanced.extend(pool[:count])
    return balanced


def _question_base(
    question_type: Any,
    stem: str,
    answer: str,
    analysis: str,
    citation: dict[str, Any],
    *,
    options: Any = None,
    rubric: list[str] | None = None,
) -> dict[str, Any]:
    normalized_type = str(question_type or "short")
    if normalized_type not in {"single", "short", "calculation", "code", "case"}:
        normalized_type = "short"
    keywords = _keywords_for_citation(citation, "数据结构")
    citation_context = context_from_citation(citation)
    item = {
        "id": "",
        "type": normalized_type,
        "difficulty": DIFFICULTY_BY_TYPE.get(normalized_type, "进阶"),
        "knowledgePoint": normalize_knowledge_point(
            keywords[0] if keywords else "",
            context=citation_context,
            fallback="线性表" if "线性表" in citation_context else None,
        ) or "数据结构课程资料",
        "stem": stem.strip(),
        "answer": answer.strip(),
        "analysis": analysis.strip(),
        "citationChunkId": citation.get("chunkId"),
        "citations": [citation],
        "rubric": clean_rubric_keywords(
            rubric or _rubric_from_answer(answer, analysis, keywords),
            context=f"{citation_context} {stem} {analysis}",
        ),
        "scoreWeight": 1.2 if normalized_type in {"code", "case"} else 1,
    }
    if isinstance(options, list) and options:
        item["options"] = [str(option).strip() for option in options if str(option).strip()]
    return item


def _normalize_citation(raw: dict[str, Any]) -> dict[str, Any] | None:
    chunk_id = raw.get("chunkId") or raw.get("chunk_id")
    preview = raw.get("contentPreview") or raw.get("content") or raw.get("fullText")
    if not chunk_id or not str(preview or "").strip():
        return None
    return {
        "documentId": str(raw.get("documentId") or raw.get("document_id") or ""),
        "documentName": str(raw.get("documentName") or raw.get("document_name") or "数据结构课程资料"),
        "sourceLocation": str(raw.get("sourceLocation") or raw.get("source_location") or raw.get("section") or "课程资料"),
        "chunkId": str(chunk_id),
        "contentPreview": _clean_excerpt(str(preview), 180),
        "fullText": str(raw.get("fullText") or raw.get("content") or preview),
        "page": int(raw.get("page") or 1),
        "similarity": raw.get("similarity") or raw.get("score"),
        "keywords": raw.get("keywords") or [],
    }


def _dedupe_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for citation in citations:
        key = str(citation.get("chunkId"))
        if key in seen:
            continue
        seen.add(key)
        result.append(citation)
    return result[:16]


def _search_chunks_for_assessment(topic: str, top_k: int) -> dict[str, Any]:
    previous = os.environ.get("KNOWLEDGE_VECTOR_QUERY_ENABLE")
    os.environ["KNOWLEDGE_VECTOR_QUERY_ENABLE"] = "false"
    try:
        return search_chunks(topic, top_k)
    finally:
        if previous is None:
            os.environ.pop("KNOWLEDGE_VECTOR_QUERY_ENABLE", None)
        else:
            os.environ["KNOWLEDGE_VECTOR_QUERY_ENABLE"] = previous


def _dedupe_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for question in questions:
        key = re.sub(r"\s+", "", str(question.get("stem") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(question)
    return result


def _source_summary(citations: list[dict[str, Any]], context: dict[str, Any]) -> str:
    docs = _unique_nonempty([str(item.get("documentName") or "") for item in citations])[:3]
    topics = context.get("topics", [])[:4]
    return f"本试卷基于 {len(citations)} 条真实课程引用生成，覆盖：{'、'.join(topics)}；主要来源：{'、'.join(docs)}。"


def _normalize_rubric(question: dict[str, Any]) -> list[str]:
    rubric = question.get("rubric")
    if isinstance(rubric, list):
        values = [str(item).strip() for item in rubric if str(item).strip()]
    else:
        values = []
    context = " ".join([
        str(question.get("stem") or ""),
        str(question.get("analysis") or ""),
        context_from_citation((question.get("citations") or [{}])[0]),
    ])
    return clean_rubric_keywords(
        values or _rubric_from_answer(str(question.get("answer") or ""), str(question.get("analysis") or ""), []),
        context=context,
    ) or ["课程依据"]


def _rubric_hits(user_answer: str, rubric: list[str]) -> tuple[list[str], list[str]]:
    normalized = _normalize_answer(user_answer)
    hit = [keyword for keyword in rubric if _normalize_answer(keyword) in normalized]
    missing = [keyword for keyword in rubric if keyword not in hit]
    return hit, missing


def _rubric_from_answer(answer: str, analysis: str, keywords: list[str]) -> list[str]:
    candidates = keywords + re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,18}", f"{answer} {analysis}")
    stopwords = {"根据课程资料", "课程片段", "回答", "参考", "依据", "说明", "要求"}
    return clean_rubric_keywords(
        [item for item in _unique_nonempty(candidates) if item not in stopwords],
        context=f"{answer} {analysis}",
    )[:4] or ["课程依据"]


def _keywords_for_citation(citation: dict[str, Any], fallback: str) -> list[str]:
    raw_keywords = citation.get("keywords") or []
    if isinstance(raw_keywords, str):
        keywords = re.split(r"[、,，;\s]+", raw_keywords)
    elif isinstance(raw_keywords, list):
        keywords = [str(item) for item in raw_keywords]
    else:
        keywords = []
    text = context_from_citation(citation)
    candidates = keywords + re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,12}", text)
    return sanitize_knowledge_points(candidates, context=text, fallback=fallback)[:6]


def _normalize_answer(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _clean_excerpt(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact if len(compact) <= limit else f"{compact[:limit]}..."


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        item = str(value or "").strip(" ，。、;；")
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _type_label(question_type: str) -> str:
    return {
        "short": "简答题",
        "calculation": "计算题",
        "case": "综合应用题",
    }.get(question_type, "主观题")
