from __future__ import annotations

from datetime import datetime

from .course_config import EMPTY_LEARNING_PATH


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


USERS = [
    {"id": "user_student_demo", "username": "student_demo", "name": "学生体验账号", "role": "student", "major": "计算机类", "grade": "大二"},
    {"id": "user_teacher_demo", "username": "teacher_demo", "name": "课程教师", "role": "teacher"},
    {"id": "user_admin_demo", "username": "admin_demo", "name": "系统管理员", "role": "admin"},
]

PROFILE: list[dict] = []
CITATIONS: list[dict] = []
RESOURCES: list[dict] = []
LEARNING_PATH = EMPTY_LEARNING_PATH.copy()
QUESTIONS: list[dict] = []

AGENT_STEPS = [
    {
        "name": "knowledge_agent",
        "title": "知识检索 Agent",
        "status": "failed",
        "summary": "暂无数据结构课程资料，无法生成高可信学习资源。",
        "inputSummary": "课程 ID、学习主题、教师上传资料范围",
        "outputSummary": "未命中可引用课程片段",
        "tools": ["课程文档解析", "关键词检索", "引用格式化"],
        "responsibility": "只检索真实课程资料，不生成学习内容。",
        "confidence": 0,
        "auditStatus": "blocked",
        "durationMs": 0,
        "citations": [],
        "structuredOutput": {
            "coverage": "none",
            "matched_chunks": [],
            "missing_knowledge": ["暂无数据结构课程资料"],
        },
        "errorReason": "请教师先上传数据结构课程资料。",
        "handoff": {
            "from": "课程资料库",
            "to": "资源生成 Agent",
            "fields": ["course", "matched_chunks", "citations"],
            "rule": "没有真实引用时阻止高可信资源生成。",
        },
        "failureCases": ["课程知识库为空", "未命中相关课程片段"],
        "retryStrategy": "上传真实课程资料后重新检索。",
        "downstreamImpact": ["资源生成暂停", "学习路径等待真实资料"],
        "evidence": [
            {"title": "资料状态", "value": "暂无真实数据结构课程资料。", "type": "risk"},
            {"title": "处理建议", "value": "教师上传课程讲义或课件后再生成资源。", "type": "output"},
        ],
    }
]
