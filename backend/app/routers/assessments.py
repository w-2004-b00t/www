from __future__ import annotations

import threading
from copy import deepcopy
from functools import wraps
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException

from .. import state
from ..demo_data import now_text
from ..persistence import list_records, load_json, load_record, save_record
from ..schemas import AssessmentSubmitRequest
from ..services.assessment_service import evaluate_answers, generate_assessment_paper
from ..services.course_progress_service import next_topic_for_progress
from ..services.knowledge_point_service import sanitize_knowledge_points
from ..services.profile_update_service import create_profile_update_draft
from ..services.mistake_repository import create_mistake, list_mistakes, mistake_analytics
from ..utils import ok, user_id_from_authorization, user_scoped_key

router = APIRouter(prefix="/api/assessments", tags=["assessments"])
_ASSESSMENT_SUBMIT_LOCK = threading.RLock()


PROFILE_IMPACT_BY_DIMENSION = {
    "薄弱知识点": "影响补强任务、资源推荐排序和测评题目生成。",
    "易错点": "影响错题本标签、测评反馈和路径调整原因。",
}


def _serialized_submission(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        with _ASSESSMENT_SUBMIT_LOCK:
            return func(*args, **kwargs)

    return wrapped


def _find_existing_submission(user_id: str, assessment_paper_id: str) -> dict | None:
    existing = next(
        (
            item
            for item in state.assessment_results
            if item.get("userId") == user_id and item.get("assessmentPaperId") == assessment_paper_id
        ),
        None,
    )
    if existing:
        return deepcopy(existing)
    return next(
        (
            item
            for item in list_records("assessment_results", 100)
            if item.get("userId") == user_id and item.get("assessmentPaperId") == assessment_paper_id
        ),
        None,
    )


def _submission_response(record: dict, learning_path: dict) -> dict:
    stored = record.get("responsePayload")
    if isinstance(stored, dict):
        response = deepcopy(stored)
        response["idempotent"] = True
        return response
    drafts = deepcopy(record.get("profileUpdateDrafts") or [])
    weakness = deepcopy(record.get("weakness") or [])
    return {
        "score": record.get("score", 0),
        "weakness": weakness,
        "suggestion": (
            f"建议围绕 {', '.join(weakness)} 创建补强任务。"
            if weakness
            else "测评表现较好，建议进入下一阶段代码实验并保持复盘。"
        ),
        "error_reasons": deepcopy(record.get("errorReasons") or []),
        "question_details": deepcopy(record.get("questionDetails") or []),
        "rubric_version": record.get("rubricVersion", "rubric_data_structure_dynamic_v1"),
        "profile_update_drafts": drafts,
        "profileUpdateDrafts": deepcopy(drafts),
        "path_adjustment": deepcopy(record.get("pathAdjustment") or {}),
        "adjustedPath": deepcopy(learning_path),
        "assessmentId": record.get("id"),
        "mistakes_added": record.get("mistakesAdded", 0),
        "idempotent": True,
    }


def _upsert_profile_item(dimension: str, value: str, confidence: float, reason: str) -> dict:
    item = {
        "id": f"profile_assessment_{uuid4().hex[:8]}",
        "dimension": dimension,
        "value": value,
        "confidence": confidence,
        "source": "assessment",
        "status": "confirmed",
        "updatedAt": now_text(),
        "reason": reason,
        "impact": PROFILE_IMPACT_BY_DIMENSION.get(dimension, "影响后续学习路径、资源推荐和智能辅导。"),
        "version": 1,
    }
    for index, existing in enumerate(state.profile_items):
        if existing.get("dimension") == dimension:
            item["id"] = existing.get("id", item["id"])
            item["version"] = int(existing.get("version", 1)) + 1
            state.profile_items[index] = item
            return item
    state.profile_items.append(item)
    return item


@router.post("/generate")
def generate_assessment(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    paper = generate_assessment_paper(user_id)
    save_record("assessment_papers", paper)
    return ok({
        "assessmentId": paper["assessmentId"],
        "title": paper["title"],
        "questions": deepcopy(paper["questions"]),
        "sourceSummary": paper["sourceSummary"],
        "createdAt": paper["createdAt"],
    })


@router.post("/submit")
@_serialized_submission
def submit_assessment(
    payload: AssessmentSubmitRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    if not payload.assessmentId:
        raise HTTPException(status_code=400, detail="缺少 assessmentId，无法按正式试卷快照评分。")
    paper = load_record("assessment_papers", payload.assessmentId)
    if not paper or paper.get("userId") != user_id:
        raise HTTPException(status_code=404, detail="测评试卷不存在或不属于当前用户，请重新生成试卷。")
    existing_submission = _find_existing_submission(user_id, payload.assessmentId)
    if existing_submission:
        return ok(_submission_response(
            existing_submission,
            state.load_user_learning_path(user_id),
        ))
    questions = paper.get("questions", [])
    result = evaluate_answers(paper, payload.answers)
    learning_path = state.load_user_learning_path(user_id)
    progress = state.load_user_learning_progress(user_id)
    mastered_points = set(progress.get("masteredKnowledgePoints", []))
    weakness = sanitize_knowledge_points([
        point for point in result["weakness"] if point not in mastered_points
    ])
    display_weakness = weakness or ["待通过真实课程测评识别"]
    suggestion = (
        f"请先在错题本订正 {', '.join(display_weakness)} 相关原题；若订正或变式验证未通过，系统再插入补强任务。"
        if result["error_reasons"]
        else "测评表现较好，建议进入下一阶段代码实验并保持复盘。"
    )
    before_path = [stage.get("name", "") for stage in learning_path.get("stages", [])]
    before_active = next((stage.get("name") for stage in learning_path.get("stages", []) if stage.get("status") == "active"), "当前学习阶段")
    total_questions = len(result["question_details"])
    correct_count = sum(1 for item in result["question_details"] if item.get("correct"))
    error_rate = 0 if not total_questions else round((1 - correct_count / max(total_questions, 1)) * 100)
    question_map = {item["id"]: item for item in questions}
    wrong_details = [item for item in result["question_details"] if not item.get("correct")]
    mistake_records = []
    for detail in wrong_details:
        question = question_map.get(detail["question_id"], {})
        mistake_records.append({
            "id": f"mistake_{uuid4().hex[:8]}",
            "userId": user_id,
            "source": "assessment",
            "assessmentId": "",
            "knowledge": detail.get("knowledge_point", "待通过真实课程测评识别"),
            "stem": question.get("stem", detail.get("question_id", "")),
            "type": question.get("type", "short"),
            "options": deepcopy(question.get("options", [])),
            "userAnswer": payload.answers.get(detail["question_id"], ""),
            "answer": question.get("answer", ""),
            "analysis": question.get("analysis", ""),
            "rubric": deepcopy(question.get("rubric", [])),
            "citations": deepcopy(question.get("citations", [])),
            "wrongReason": detail.get("error_reason") or "阶段测评作答未达到 Rubric 要求。",
            "fixTask": f"完成「{detail.get('knowledge_point', '待通过真实课程测评识别')}」补强练习并复述错因。",
            "correctionAttempts": [],
            "verificationQuestions": [],
            "verificationAttempts": [],
            "masteryEvidence": [],
            "status": "待订正",
            "createdAt": now_text(),
        })

    assessment_id = f"assessment_{uuid4().hex[:8]}"
    for mistake in mistake_records:
        mistake["assessmentId"] = assessment_id

    with state.lock:
        if result["score"] >= 80:
            mastered_points = sorted({
                item.get("knowledge_point", "")
                for item in result["question_details"]
                if item.get("correct") and item.get("knowledge_point")
            })
            active_stage = next(
                (
                    stage
                    for stage in learning_path.get("stages", [])
                    if stage.get("status") in {"active", "awaiting_assessment"}
                ),
                None,
            )
            if active_stage:
                mastered_points.extend(active_stage.get("knowledgePoints", []))
            state.record_learning_progress(
                user_id,
                source="assessment",
                mastered_knowledge_points=mastered_points,
                score=result["score"],
                evidence=[f"阶段测评分数：{result['score']} 分", f"正确题数：{correct_count}/{total_questions}"],
            )
        after_path = [stage.get("name", "") for stage in learning_path.get("stages", [])]
        after_active = next((stage.get("name") for stage in learning_path.get("stages", []) if stage.get("status") == "active"), "继续当前阶段")
        path_adjustment = {
            "before": before_active,
            "after": after_active,
            "reason": (
                f"测评产生错题（错误率 {error_rate}%），先进入错题本订正；路径暂不插入补强任务。"
                if weakness
                else "测评表现稳定，保持原路径。"
            ),
            "beforePath": before_path,
            "afterPath": after_path,
        }
        assessment_record = {
            "id": assessment_id,
            "userId": user_id,
            "score": result["score"],
            "correctCount": correct_count,
            "total": total_questions,
            "weakness": weakness,
            "errorReasons": result["error_reasons"],
            "questionDetails": result["question_details"],
            "answers": payload.answers,
            "assessmentPaperId": payload.assessmentId,
            "stageId": str((paper.get("stageSnapshot") or {}).get("id") or ""),
            "stageKnowledgePoints": deepcopy(
                (paper.get("stageSnapshot") or {}).get("knowledgePoints", [])
            ),
            "stageSnapshot": deepcopy(paper.get("stageSnapshot")),
            "title": paper.get("title", "数据结构课程阶段测评"),
            "sourceSummary": paper.get("sourceSummary", ""),
            "rubricVersion": result["rubric_version"],
            "pathAdjustment": path_adjustment,
            "profileUpdateDraft": {
                "dimension": "易错点",
                "value": "、".join(display_weakness),
                "confidence": 0.89 if weakness else 0.72,
                "source": "assessment",
            },
            "mistakesAdded": len(mistake_records),
            "createdAt": now_text(),
        }
        profile_updates = []
        profile_update_drafts = []
        if weakness:
            joined_weakness = "、".join(weakness)
            update_reason = f"阶段测评 {result['score']} 分，错因集中在：{'; '.join(result['error_reasons']) or joined_weakness}。"
            profile_update_drafts.append(create_profile_update_draft(
                user_id,
                dimension="薄弱知识点",
                value=joined_weakness,
                source="assessment",
                trigger="阶段测评提交",
                evidence=update_reason,
                confidence=0.9,
            ))
            profile_update_drafts.append(create_profile_update_draft(
                user_id,
                dimension="易错点",
                value="、".join(result["error_reasons"] or weakness),
                source="assessment",
                trigger="阶段测评提交",
                evidence=update_reason,
                confidence=0.89,
            ))
            assessment_record["profileUpdateDrafts"] = deepcopy(profile_update_drafts)
        response_payload = {
            "score": result["score"],
            "weakness": weakness,
            "suggestion": suggestion,
            "error_reasons": result["error_reasons"],
            "question_details": result["question_details"],
            "rubric_version": result["rubric_version"],
            "profile_update_draft": {
                "dimension": "易错点",
                "value": "、".join(display_weakness),
                "confidence": 0.89 if weakness else 0.72,
                "status": "draft",
                "source": "assessment",
            },
            "profile_updates": [],
            "profile_update_drafts": deepcopy(profile_update_drafts) if weakness else [],
            "profileUpdateDrafts": deepcopy(profile_update_drafts) if weakness else [],
            "path_adjustment": deepcopy(path_adjustment),
            "adjustedPath": deepcopy(learning_path),
            "assessmentId": assessment_id,
            "mistakes_added": len(mistake_records),
            "idempotent": False,
        }
        assessment_record["responsePayload"] = deepcopy(response_payload)
        state.assessment_results.insert(0, assessment_record)
        state.save_user_learning_path(user_id, learning_path)
        state.persist_state()

    save_record("assessment_results", assessment_record)
    for mistake in mistake_records:
        create_mistake(mistake)
    return ok(response_payload)


@router.get("/report")
def assessment_report(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    assessments = deepcopy([item for item in state.assessment_results if item.get("userId") == user_id])
    latest = assessments[0] if assessments else None
    scores = [item.get("score", 0) for item in assessments]
    avg_score = round(sum(scores) / len(scores)) if scores else 0
    resource_feedback = [
        feedback
        for resource in state.load_user_resources(user_id)
        for feedback in resource.get("feedback", [])
    ]
    resource_practice = [
        resource.get("metadata", {}).get("lastPractice")
        for resource in state.load_user_resources(user_id)
        if resource.get("metadata", {}).get("lastPractice")
    ]
    learning_path = state.load_user_learning_path(user_id)
    progress = state.load_user_learning_progress(user_id)
    next_topic = next_topic_for_progress(progress, state.annotate_learning_path_with_progress(learning_path, progress))
    path_adjustments = deepcopy(learning_path.get("adjustmentHistory", []))
    profile_changes = [
        {
            "dimension": item.get("dimension"),
            "value": item.get("value"),
            "source": item.get("source"),
            "confidence": item.get("confidence"),
            "updatedAt": item.get("updatedAt"),
        }
        for item in load_json(user_scoped_key("profile_items", user_id), [])
    ]
    weak_points = sorted({
        point
        for item in assessments
        for point in item.get("weakness", [])
    })
    mistakes = list_mistakes(user_id, 200)
    mistake_stats = mistake_analytics(mistakes)
    next_actions = []
    if mistake_stats["pendingCorrection"]:
        next_actions.append(f"订正 {mistake_stats['pendingCorrection']} 道原错题")
    elif mistake_stats["pendingVerification"]:
        next_actions.append(f"完成 {mistake_stats['pendingVerification']} 道错题的变式验证")
    elif mistake_stats["mastered"] and not next_topic.get("blocked") and next_topic.get("topic"):
        next_actions.append(f"错题已完成掌握验证，继续学习：{next_topic['topic']}")
    elif weak_points:
        next_actions.append("完成测评识别出的薄弱点复盘")
    if path_adjustments:
        next_actions.append("查看学习路径调整记录")
    if not next_topic.get("blocked") and next_topic.get("topic"):
        next_actions.append(f"继续学习：{next_topic['topic']}")
    report = {
        "mastery": round(avg_score / 100, 2),
        "summary": (
            f"最近一次测评 {latest['score']} 分，薄弱点集中在：{', '.join(latest.get('weakness', []))}。"
            if latest
            else "暂无正式测评记录，报告暂不生成学习结论。"
        ),
        "next_actions": next_actions,
        "dataSources": {
            "assessmentResults": len(assessments),
            "resourceFeedback": len(resource_feedback),
            "resourcePracticeRecords": len(resource_practice),
            "pathAdjustments": len(path_adjustments),
            "profileDimensions": len(profile_changes),
            "mistakeRecords": len(mistakes),
        },
        "assessmentSummary": {
            "latest": latest,
            "averageScore": avg_score,
            "history": assessments[:10],
            "weakPoints": weak_points,
        },
        "resourceEffect": {
            "feedback": resource_feedback[:10],
            "practice": resource_practice[:10],
        },
        "pathAdjustments": path_adjustments[:10],
        "profileChanges": profile_changes,
        "mistakeSummary": deepcopy(mistakes[:10]),
        "mistakeAnalytics": mistake_stats,
    }
    return ok(report)
