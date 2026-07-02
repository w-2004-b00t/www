from __future__ import annotations

from collections import Counter
from copy import deepcopy
from uuid import uuid4

from .. import state
from ..demo_data import PROFILE, now_text
from ..persistence import list_records, load_json, save_json
from .mistake_repository import list_mistakes
from ..utils import is_seed_user, user_scoped_key


PROFILE_IMPACTS = {
    "专业背景": "影响案例语境、术语解释深度和代码实践难度。",
    "年级 / 学习阶段": "影响学习路径起点、任务节奏和资源难度。",
    "知识基础": "影响公式推导粒度、先修知识补充和练习难度。",
    "学习目标": "影响学习路径阶段、今日任务和资源生成主题。",
    "薄弱知识点": "影响补强任务、资源推荐排序和测评题目生成。",
    "认知风格": "影响智能辅导回答结构和多模态资源优先级。",
    "资源偏好": "影响讲解文档、导图、视频、练习和代码案例的推荐顺序。",
    "可用学习时间": "影响今日任务长度、资源数量和路径强度。",
    "易错点": "影响错题本标签、测评反馈和路径调整原因。",
    "实践能力水平": "影响代码案例、实验步骤和代码类测评难度。",
}


def _profile_key(user_id: str) -> str:
    return user_scoped_key("profile_items", user_id)


def _draft_key(user_id: str) -> str:
    return user_scoped_key("profile_update_drafts", user_id)


def _tutor_log_key(user_id: str) -> str:
    return user_scoped_key("tutor_question_logs", user_id)


def _default_profile(user_id: str) -> list[dict]:
    return deepcopy(PROFILE) if is_seed_user(user_id) else []


def load_profile_items(user_id: str) -> list[dict]:
    return load_json(_profile_key(user_id), _default_profile(user_id))


def save_profile_items(user_id: str, items: list[dict]) -> None:
    save_json(_profile_key(user_id), items)


def list_profile_update_drafts(user_id: str) -> list[dict]:
    return load_json(_draft_key(user_id), [])


def save_profile_update_drafts(user_id: str, drafts: list[dict]) -> None:
    save_json(_draft_key(user_id), drafts)


def _latest_assessments(user_id: str) -> list[dict]:
    rows = list_records("assessment_results", 20)
    return [item for item in rows if not item.get("userId") or item.get("userId") == user_id][:5]


def _latest_mistakes(user_id: str) -> list[dict]:
    return list_mistakes(user_id, 10)


def _resource_feedback(user_id: str) -> list[dict]:
    records: list[dict] = []
    for resource in state.resources:
        for feedback in resource.get("feedback", []):
            if feedback.get("userId") and feedback.get("userId") != user_id:
                continue
            records.append({
                **feedback,
                "resourceTitle": resource.get("title"),
                "resourceType": resource.get("resourceType"),
            })
    return records[:10]


def _tutor_logs(user_id: str) -> list[dict]:
    return load_json(_tutor_log_key(user_id), [])[:20]


def build_profile_context(user_id: str, current_message: str | None = None) -> dict:
    profile_items = load_profile_items(user_id)
    assessments = _latest_assessments(user_id)
    mistakes = _latest_mistakes(user_id)
    feedback = _resource_feedback(user_id)
    tutor_logs = _tutor_logs(user_id)
    mistake_counter = Counter(
        item.get("knowledge", "待通过真实课程资料确认")
        for item in mistakes
        if item.get("knowledge")
    )
    tutor_counter = Counter(
        item.get("knowledgePoint", "待通过真实课程资料确认")
        for item in tutor_logs
        if item.get("knowledgePoint")
    )
    latest_goal = current_message or next(
        (item.get("value") for item in profile_items if item.get("dimension") == "学习目标"),
        "掌握真实课程资料覆盖的当前知识点",
    )
    return {
        "current_profile": {item.get("dimension"): item.get("value") for item in profile_items},
        "latest_goal": latest_goal,
        "assessment_history": [
            {
                "id": item.get("id"),
                "score": item.get("score"),
                "weakness": item.get("weakness", []),
                "errorReasons": item.get("errorReasons", []),
                "createdAt": item.get("createdAt"),
            }
            for item in assessments
        ],
        "mistake_summary": [
            {"knowledge": knowledge, "count": count}
            for knowledge, count in mistake_counter.most_common(5)
        ],
        "resource_feedback": feedback,
        "tutor_question_summary": [
            {"knowledgePoint": knowledge, "count": count}
            for knowledge, count in tutor_counter.most_common(5)
        ],
    }


def create_profile_update_draft(
    user_id: str,
    *,
    dimension: str,
    value: str,
    source: str,
    trigger: str,
    evidence: str,
    confidence: float = 0.82,
    old_value: str | None = None,
    impact: str | None = None,
) -> dict:
    old_value = old_value if old_value is not None else next(
        (item.get("value", "") for item in load_profile_items(user_id) if item.get("dimension") == dimension),
        "",
    )
    draft = {
        "id": f"profile_update_{uuid4().hex[:8]}",
        "dimension": dimension,
        "oldValue": old_value,
        "value": value,
        "newValue": value,
        "source": source,
        "trigger": trigger,
        "evidence": evidence,
        "confidence": max(0.0, min(1.0, confidence)),
        "status": "draft",
        "impact": impact or PROFILE_IMPACTS.get(dimension, "影响后续学习路径、资源推荐和智能辅导。"),
        "createdAt": now_text(),
        "updatedAt": now_text(),
    }
    drafts = [item for item in list_profile_update_drafts(user_id) if item.get("status") == "draft"]
    drafts = [item for item in drafts if not (item.get("dimension") == dimension and item.get("source") == source)]
    drafts.insert(0, draft)
    save_profile_update_drafts(user_id, drafts[:20])
    return draft


def confirm_profile_update_drafts(user_id: str, draft_ids: list[str] | None = None) -> dict:
    drafts = list_profile_update_drafts(user_id)
    selected = [
        item for item in drafts
        if item.get("status") == "draft" and (not draft_ids or item.get("id") in draft_ids)
    ]
    profile_items = load_profile_items(user_id)
    applied: list[dict] = []
    for draft in selected:
        dimension = draft.get("dimension", "")
        existing_index = next((idx for idx, item in enumerate(profile_items) if item.get("dimension") == dimension), -1)
        existing = profile_items[existing_index] if existing_index >= 0 else {}
        item = {
            "id": existing.get("id") or f"profile_{uuid4().hex[:8]}",
            "dimension": dimension,
            "value": draft.get("newValue") or draft.get("value", ""),
            "confidence": draft.get("confidence", 0.82),
            "source": draft.get("source", "behavior"),
            "status": "confirmed",
            "updatedAt": now_text(),
            "reason": draft.get("evidence", ""),
            "impact": draft.get("impact") or PROFILE_IMPACTS.get(dimension, "影响后续学习路径、资源推荐和智能辅导。"),
            "version": int(existing.get("version", 0) or 0) + 1,
        }
        if existing_index >= 0:
            profile_items[existing_index] = item
        else:
            profile_items.append(item)
        draft["status"] = "confirmed"
        draft["updatedAt"] = now_text()
        applied.append(item)
    save_profile_items(user_id, profile_items)
    save_profile_update_drafts(user_id, drafts)
    if is_seed_user(user_id):
        state.profile_items[:] = profile_items
        state.persist_state()
    return {"applied": applied, "profileItems": profile_items, "drafts": drafts}


def reject_profile_update_draft(user_id: str, draft_id: str) -> dict:
    drafts = list_profile_update_drafts(user_id)
    for draft in drafts:
        if draft.get("id") == draft_id:
            draft["status"] = "rejected"
            draft["updatedAt"] = now_text()
            break
    save_profile_update_drafts(user_id, drafts)
    return {"drafts": drafts}


def log_tutor_question(user_id: str, question: str, knowledge_point: str) -> dict | None:
    logs = _tutor_logs(user_id)
    record = {
        "id": f"tutor_log_{uuid4().hex[:8]}",
        "question": question,
        "knowledgePoint": knowledge_point,
        "createdAt": now_text(),
    }
    logs.insert(0, record)
    save_json(_tutor_log_key(user_id), logs[:50])
    repeated = sum(1 for item in logs if item.get("knowledgePoint") == knowledge_point)
    if repeated >= 2:
        return create_profile_update_draft(
            user_id,
            dimension="薄弱知识点",
            value=f"{knowledge_point}相关概念反复提问，需要继续补强",
            source="behavior",
            trigger="智能辅导反复提问",
            evidence=f"最近多次在智能辅导中提问「{knowledge_point}」相关问题。",
            confidence=0.82,
        )
    return None
