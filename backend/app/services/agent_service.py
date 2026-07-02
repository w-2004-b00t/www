from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from ..demo_data import AGENT_STEPS
from .knowledge_service import search_chunks


def _step(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(item for item in steps if item["name"] == name)


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
        }
        for item in items
    ]


def _keywords_from_retrieval(items: list[dict[str, Any]], fallback: str) -> list[str]:
    keywords: list[str] = []
    stopwords = {"数据结构", "课程", "资料", "学习", "可以", "进行", "需要", "通过", "实现", "基本", "操作"}
    for item in items:
        raw_keywords = item.get("keywords") or []
        if isinstance(raw_keywords, str):
            raw_keywords = [raw_keywords]
        for value in raw_keywords:
            text = str(value).strip()
            if text and text not in stopwords and text not in keywords:
                keywords.append(text)
        content = str(item.get("content") or "")
        for text in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,16}", content):
            if text and text not in stopwords and text not in keywords:
                keywords.append(text)
            if len(keywords) >= 8:
                break
        if len(keywords) >= 8:
            break
    return keywords[:8] or [fallback]


def build_agent_steps_for_topic(topic: str, target: str, resource_types: list[str]) -> list[dict[str, Any]]:
    safe_topic = topic.strip() or "当前知识点"
    steps = deepcopy(AGENT_STEPS)
    retrieval = search_chunks(safe_topic, 5)
    top_chunk = retrieval["items"][0] if retrieval["items"] else None
    citations = _citations_from_retrieval(retrieval["items"])
    keywords = _keywords_from_retrieval(retrieval["items"], safe_topic)
    primary_concept = keywords[0] if keywords else safe_topic
    practice_focus = "、".join(keywords[:3]) if keywords else safe_topic
    resource_type_labels = {
        "explanation": "讲解文档",
        "mindmap": "思维导图",
        "exercise": "练习题",
        "reading": "拓展阅读",
        "lab": "代码实验",
        "video_script": "视频演示",
    }
    selected_labels = [resource_type_labels.get(item, item) for item in resource_types]

    profile_step = _step(steps, "profile_agent")
    profile_step["structuredOutput"] = {
        "course": "数据结构课程",
        "professional_background": "计算机科学与技术专业学生",
        "grade_stage": "大二，当前章节需结合真实课程资料确认",
        "learning_goal": target,
        "foundation": "理解部分基础概念，公式推导与代码实践仍需结合真实资料确认",
        "cognitive_style": "先图解建立直觉，再用例题巩固",
        "weak_points": [safe_topic, "先修概念"],
        "preferences": ["图解", "例题", "代码实践"],
        "daily_minutes": 45,
        "error_points": [f"{safe_topic}边界条件", "概念与操作过程混淆"],
        "practice_level": "能阅读课程资料示例代码，需要明确实验步骤和输入输出验证",
        "confidence": 0.86,
        "need_user_confirm": True,
    }
    profile_step["outputSummary"] = f"围绕“{safe_topic}”识别专业、阶段、基础、目标、薄弱点、风格、偏好、时间、易错点和实践能力"
    profile_step["evidence"] = [
        {"title": "原始输入", "value": target or safe_topic, "type": "input"},
        {"title": "低置信确认", "value": f"“{safe_topic}”被识别为薄弱点，进入画像确认。", "type": "risk"},
    ]

    knowledge_step = _step(steps, "knowledge_agent")
    knowledge_step["citations"] = citations
    knowledge_step["structuredOutput"] = {
        "query": safe_topic,
        "matched_chunks": [
            {
                "document": item["document_name"],
                "page": item["page"],
                "section": item["source_location"],
                "chunk_id": item["chunk_id"],
                "score": item["score"],
                "preview": item["content"][:42],
            }
            for item in retrieval["items"]
        ],
        "knowledge_points": [safe_topic, "课程定义", "典型例题"],
        "citation_count": len(retrieval["items"]),
        "top_chunk": top_chunk["chunk_id"] if top_chunk else None,
        "coverage": retrieval["coverage"],
        "missing_knowledge": retrieval["missing_knowledge"],
    }
    knowledge_step["outputSummary"] = (
        f"命中 {len(retrieval['items'])} 个“{safe_topic}”相关课程片段，覆盖度 {retrieval['coverage']}"
        if retrieval["items"]
        else f"未命中“{safe_topic}”的高可信课程片段"
    )
    knowledge_step["auditStatus"] = "引用充分" if retrieval["coverage"] == "sufficient" else "命中不足"
    knowledge_step["evidence"] = [
        {"title": "检索 Query", "value": safe_topic, "type": "tool"},
        {
            "title": "最高命中",
            "value": f"{top_chunk['document_name']} 第 {top_chunk['page']} 页，相似度 {top_chunk['score']}" if top_chunk else "无高可信命中",
            "type": "citation",
        },
    ]

    document_step = _step(steps, "document_agent")
    document_step["citations"] = citations[:2]
    document_step["structuredOutput"] = {
        "resource_type": "课程讲解文档",
        "topic": safe_topic,
        "sections": ["概念直觉", "公式推导", "手算例题", "常见误区"],
        "format": "Markdown",
        "citation_count": len(document_step["citations"]),
    }
    document_step["outputSummary"] = f"生成“{safe_topic}”Markdown 讲解，包含概念、公式、例题和引用"
    document_step["evidence"] = [
        {"title": "文档结构", "value": "概念、公式、例题、误区四段式讲解。", "type": "output"},
        {"title": "引用绑定", "value": f"绑定 {len(document_step['citations'])} 条课程片段。", "type": "citation"},
    ]

    exercise_step = _step(steps, "exercise_agent")
    exercise_step["citations"] = citations[:2]
    exercise_step["structuredOutput"] = {
        "topic": safe_topic,
        "question_types": ["选择题", "简答题", "计算题", "代码题"],
        "rubric_ready": True,
        "weakness_tags": [safe_topic, "课程资料", "代码实践"],
        "answer_check": "pending_audit",
    }
    exercise_step["outputSummary"] = f"生成“{safe_topic}”选择、简答、计算和代码题，并附 Rubric"
    exercise_step["evidence"] = [
        {"title": "题型覆盖", "value": "选择题、简答题、计算题、代码题。", "type": "output"},
        {"title": "错因标签", "value": f"错题可写入“{safe_topic}”薄弱点。", "type": "handoff"},
    ]

    multimodal_step = _step(steps, "multimodal_agent")
    multimodal_step["citations"] = citations[:2]
    multimodal_step["structuredOutput"] = {
        "artifacts": [label for label in selected_labels if label in {"思维导图", "视频演示"}] or ["完整思维导图", "视频演示"],
        "mindmap_nodes": ["先修知识", "核心概念", "计算流程", "常见错误", "代码实践", "测评闭环"],
        "video_timeline": ["0:00-0:20", "0:20-0:50", "0:50-1:30", "1:30-2:20", "2:20-3:00"],
        "visual_strategy": "先概念后公式，再例题",
        "source_resource_id": "document_agent.output.markdown",
    }
    multimodal_step["outputSummary"] = f"生成“{safe_topic}”完整导图和 3 分钟视频演示分镜"
    multimodal_step["evidence"] = [
        {"title": "表达策略", "value": "先概念后公式，再例题。", "type": "output"},
        {"title": "导图覆盖", "value": "先修、概念、计算、错误、代码、测评闭环。", "type": "output"},
    ]

    code_step = _step(steps, "code_agent")
    code_step["citations"] = citations[:1]
    code_step["structuredOutput"] = {
        "library": "课程资料",
        "case": f"{safe_topic}核心操作伪代码与边界验证",
        "parameters": ["输入规模", "初始状态", "操作序列"],
        "experiment": f"围绕{practice_focus}设计小规模样例，跟踪每一步结构变化和复杂度",
    }
    code_step["outputSummary"] = f"生成“{safe_topic}”课程资料代码案例、运行步骤和实验任务"
    code_step["evidence"] = [
        {"title": "代码案例", "value": f"{primary_concept}核心操作伪代码与状态追踪。", "type": "output"},
        {"title": "实验任务", "value": f"设计输入序列，验证{practice_focus}的操作结果和复杂度。", "type": "output"},
    ]

    path_step = _step(steps, "path_agent")
    path_step["structuredOutput"] = {
        "path_title": f"{safe_topic}补强学习路径",
        "sequence": ["课程讲解文档", "完整思维导图", "不同类型练习题", "视频演示", "代码实操案例", "阶段测评"],
        "checkpoint": f"能够解释并完成“{safe_topic}”相关例题",
        "adjustment_reason": f"画像显示“{safe_topic}”薄弱，且今日可用学习时间为 45 分钟。",
    }
    path_step["outputSummary"] = f"规划“{safe_topic}”资源学习顺序和阶段检查点"
    path_step["evidence"] = [
        {"title": "路径依据", "value": f"薄弱点：{safe_topic}；偏好：图解、例题、代码实践。", "type": "input"},
        {"title": "完成标准", "value": f"能够解释并完成“{safe_topic}”相关例题。", "type": "output"},
    ]

    audit_step = _step(steps, "audit_agent")
    audit_step["citations"] = citations
    audit_step["structuredOutput"] = {
        "passed": max(len(resource_types) - 1, 0),
        "warning": 1 if resource_types else 0,
        "risk_types": ["引用不足", "难度不匹配", "答案不完整"],
        "can_show_to_student": False if resource_types else True,
    }
    audit_step["outputSummary"] = f"审核 {len(resource_types)} 类资源：{max(len(resource_types) - 1, 0)} 类通过，1 类需教师复核"
    audit_step["auditStatus"] = "1 项待教师复核" if resource_types else "无待审资源"
    audit_step["evidence"] = [
        {"title": "风险位置", "value": "练习题答案解析段落。", "type": "risk"},
        {"title": "处理建议", "value": "补充课程引用后再推荐给学生。", "type": "risk"},
    ]

    assessment_step = _step(steps, "assessment_agent")
    assessment_step["structuredOutput"] = {
        "score": 86,
        "weakness": [safe_topic],
        "error_reasons": ["公式代入不稳定", "概念混淆"],
        "profile_update_draft": {"dimension": "易错点", "value": safe_topic, "confidence": 0.89},
        "path_adjustment": {"before": "代码实践", "after": f"{safe_topic}补强任务"},
    }
    assessment_step["evidence"] = [
        {"title": "路径变化", "value": f"代码实践 -> {safe_topic}补强任务", "type": "handoff"},
        {"title": "画像草稿", "value": f"易错点新增：{safe_topic}", "type": "output"},
    ]
    for step in steps:
        step.setdefault("citations", [])
        step.setdefault("downstreamImpact", step.get("affects", []))
    return steps
