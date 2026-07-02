from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from .. import state
from ..demo_data import now_text
from ..persistence import (
    list_knowledge_chunks,
    list_knowledge_documents,
    load_json,
    save_json,
    upsert_knowledge_document,
)
from ..services.document_parser import DEFAULT_AI_INTRO_MARKDOWN, ensure_default_knowledge_base, parse_document_text
from ..services.courseware_importer import ensure_courseware_knowledge_base, import_courseware_zip
from ..utils import ok

router = APIRouter(prefix="/api/admin", tags=["admin"])

DEFAULT_MODEL_CONFIG = {
    "version": "v2.2",
    "activePrompt": "audit",
    "prompts": {
        "audit": "你是内容审核 Agent。请检查生成内容是否包含课程资料引用、事实是否正确、答案是否可靠、难度是否匹配学生画像，并输出 JSON 审核结果。",
        "resource": "你是资源生成 Agent。请基于《数据结构课程》课程知识片段和学生画像生成分层学习资源，不得编造引用，不确定内容必须标注为模型推断。",
        "tutor": "你是智能辅导 Agent。回答必须优先基于课程资料，给出分层解释、引用来源和可执行下一步。",
    },
    "thresholds": {"citationCoverage": 80, "lowConfidence": 70, "autoPassScore": 88},
    "agents": [
        {"name": "画像构建 Agent", "model": "DeepSeek", "temp": 0.2, "status": "稳定", "guard": "低置信画像必须确认"},
        {"name": "知识检索 Agent", "model": "BGE-M3 + Chroma/SQLite", "temp": 0.1, "status": "稳定", "guard": "必须返回引用"},
        {"name": "文档生成 Agent", "model": "DeepSeek", "temp": 0.35, "status": "稳定", "guard": "结论必须绑定引用"},
        {"name": "题库生成 Agent", "model": "DeepSeek", "temp": 0.35, "status": "稳定", "guard": "答案需要可校验"},
        {"name": "多模态生成 Agent", "model": "DeepSeek + 前端动画", "temp": 0.4, "status": "稳定", "guard": "不改变知识结论"},
        {"name": "代码实操 Agent", "model": "DeepSeek", "temp": 0.2, "status": "稳定", "guard": "代码需包含运行步骤"},
        {"name": "路径规划 Agent", "model": "DeepSeek", "temp": 0.25, "status": "稳定", "guard": "必须说明推荐原因"},
        {"name": "内容审核 Agent", "model": "DeepSeek", "temp": 0.1, "status": "稳定", "guard": "高风险自动拦截"},
        {"name": "学习评估 Agent", "model": "DeepSeek", "temp": 0.2, "status": "稳定", "guard": "测评结果反向更新画像和路径"},
    ],
}


@router.get("/dashboard")
def admin_dashboard() -> dict:
    ensure_default_knowledge_base()
    docs = list_knowledge_documents()
    pending = len([item for item in state.resources if item.get("auditStatus") != "passed"])
    warning = len([item for item in state.resources if item.get("auditStatus") in {"warning", "rejected"}])
    chunks = sum(int(item.get("chunks", 0)) for item in docs)
    coverage = max([int(item.get("coverage", 0)) for item in docs] or [0])
    return ok({
        "teacherMetrics": [
            {"label": "待审核资源", "value": str(pending), "trend": f"{warning} 个有风险"},
            {"label": "知识库片段", "value": str(chunks), "trend": "已完成向量化"},
            {"label": "薄弱学生占比", "value": "31%", "trend": "集中在课程资料待上传"},
        ],
        "adminMetrics": [
            {"label": "运行中的 Agent 任务", "value": "2", "trend": "平均耗时 8.4s"},
            {"label": "模型调用成功率", "value": "98.6%", "trend": "近 24 小时"},
            {"label": "待处理风险", "value": str(warning + 3), "trend": "引用缺失为主"},
        ],
        "status": {
            "knowledgeCoverage": coverage,
            "auditPassRate": 83,
            "agentSuccessRate": 96,
            "pathAdjustments": 18,
        },
    })


@router.get("/documents")
def list_documents() -> dict:
    ensure_default_knowledge_base()
    return ok(list_knowledge_documents())


@router.post("/documents/parse")
def parse_document(payload: dict) -> dict:
    filename = payload.get("name") or f"数据结构课程补充资料-{uuid4().hex[:4]}.md"
    content = payload.get("content") or DEFAULT_AI_INTRO_MARKDOWN
    parsed = parse_document_text(filename, content)
    return ok({
        "document": parsed["document"],
        "documents": list_knowledge_documents(),
        "chunks": parsed["chunks"][:12],
    })


@router.post("/documents/import-courseware")
def import_courseware(payload: dict | None = None) -> dict:
    payload = payload or {}
    force = bool(payload.get("force", False))
    source_path = payload.get("path")
    result = import_courseware_zip(source_path, force=force) if source_path else ensure_courseware_knowledge_base(force=force)
    return ok({
        "importResult": {
            "imported": result.get("imported"),
            "reason": result.get("reason"),
            "sourcePath": result.get("sourcePath"),
            "fileCount": result.get("fileCount", len(result.get("documents", []))),
            "chunkCount": result.get("chunkCount", len(result.get("chunks", []))),
        },
        "documents": list_knowledge_documents(),
        "chunks": result.get("chunks", [])[:12],
    })


@router.get("/documents/{doc_id}/chunks")
def list_document_chunks(doc_id: str) -> dict:
    ensure_default_knowledge_base()
    chunks = list_knowledge_chunks(document_id=doc_id)
    if not chunks:
        raise HTTPException(status_code=404, detail="该资料没有解析片段")
    return ok(chunks)


@router.post("/documents/{doc_id}/confirm")
def confirm_document(doc_id: str) -> dict:
    ensure_default_knowledge_base()
    docs = list_knowledge_documents()
    target = next((item for item in docs if item["id"] == doc_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="资料不存在")
    updated = {
        "id": target["id"],
        "course_id": target.get("courseId", "course_data_structure"),
        "filename": target["name"],
        "file_type": target.get("fileType", "md"),
        "status": "已入库",
        "chunk_count": target.get("chunks", 0),
        "coverage": max(int(target.get("coverage", 0)), 88),
        "issue": "教师已确认来源，可用于学生端引用和资源生成。",
    }
    upsert_knowledge_document(updated)
    return ok({"document": next(item for item in list_knowledge_documents() if item["id"] == doc_id), "documents": list_knowledge_documents()})


@router.get("/model-config")
def get_model_config() -> dict:
    return ok(load_json("model_config", deepcopy(DEFAULT_MODEL_CONFIG)))


@router.post("/model-config")
def save_model_config(payload: dict) -> dict:
    current = load_json("model_config", deepcopy(DEFAULT_MODEL_CONFIG))
    current.update(payload)
    if "version" not in payload:
        current["version"] = "v2.3"
    current["updatedAt"] = now_text()
    save_json("model_config", current)
    return ok(current)


@router.post("/model-config/rollback")
def rollback_model_config() -> dict:
    config = deepcopy(DEFAULT_MODEL_CONFIG)
    config["version"] = "v2.1-rollback"
    config["updatedAt"] = now_text()
    save_json("model_config", config)
    return ok(config)


@router.get("/analytics")
def student_analytics() -> dict:
    return ok({
        "metrics": {"students": 36, "active": 29, "averageMastery": 78, "intervention": 12},
        "weakPoints": [
            {"name": "真实课程资料待确认", "value": 31, "action": "补充课程资料后重新统计"},
            {"name": "课程资料与过拟合", "value": 24, "action": "补充对比案例"},
            {"name": "代码参数理解", "value": 18, "action": "安排 max_depth 实验"},
        ],
        "students": [
            {"name": "张同学", "progress": "课程资料补强", "mastery": 76, "risk": "需补强"},
            {"name": "李同学", "progress": "代码实践应用", "mastery": 84, "risk": "稳定"},
            {"name": "王同学", "progress": "基础概念理解", "mastery": 62, "risk": "低活跃"},
        ],
        "suggestion": "当前统计需结合真实课程资料复核后再生成班级补强建议。",
    })


@router.post("/analytics/remedial-task")
def create_class_remedial_task() -> dict:
    return ok({
        "id": f"class_task_{uuid4().hex[:8]}",
        "title": "课程资料班级补强任务",
        "status": "已创建",
        "createdAt": now_text(),
        "studentCount": 12,
    })
