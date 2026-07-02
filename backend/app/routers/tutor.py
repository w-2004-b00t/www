from __future__ import annotations

import json
import logging
import time
from copy import deepcopy
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from .. import state
from ..demo_data import now_text
from ..schemas import (
    MistakeCorrectionRequest,
    MistakeSimilarRequest,
    MistakeStatusRequest,
    MistakeVerificationRequest,
    TutorActionRequest,
    TutorChatRequest,
    TutorExtraRequest,
    TutorFeedbackRequest,
    TutorMistakeRequest,
    TutorNoteRequest,
)
from ..services.assessment_service import score_question
from ..services.mistake_generation_service import generate_mistake_variants
from ..services.mistake_repository import (
    MistakeVersionConflict,
    create_mistake,
    get_mistake,
    list_mistakes,
    normalize_mistake,
    update_mistake,
)
from ..services.knowledge_service import search_chunks
from ..services.llm_service import LLMUnavailable
from ..services.path_planner_service import build_remedial_stage
from ..services.profile_update_service import log_tutor_question
from ..services.tutor_service import (
    answer_tutor_question,
    generate_tutor_extra,
    generate_document_from_tutor,
    generate_exercise_from_tutor,
    prepare_tutor_stream,
    stream_tutor_answer,
    tutor_stream_result,
)
from ..utils import ok, user_id_from_authorization

router = APIRouter(prefix="/api/tutor", tags=["tutor"])
logger = logging.getLogger("eduagent.tutor")
MISTAKE_PASS_SCORE = 70


def _rubric_keywords(record: dict) -> list[str]:
    rubric = record.get("rubric")
    if isinstance(rubric, list):
        values = [str(item).strip() for item in rubric if str(item).strip()]
        if values:
            return values
    answer = str(record.get("answer") or "").strip()
    if answer:
        values = [item for item in answer.replace("，", " ").replace("、", " ").split() if len(item) >= 2]
        if values:
            return values[:4]
    knowledge = str(record.get("knowledge") or "核心概念").strip()
    return [knowledge]


def _normalize_mistake(record: dict) -> dict:
    item = normalize_mistake(record)
    if not item.get("rubric"):
        item["rubric"] = _rubric_keywords(item)
    return item


def _mistake_question(record: dict, *, question_id: str | None = None) -> dict:
    normalized = _normalize_mistake(record)
    question = {
        "id": question_id or normalized["id"],
        "type": normalized["type"],
        "knowledgePoint": normalized.get("knowledge", "课程资料"),
        "stem": normalized["stem"],
        "answer": normalized["answer"],
        "analysis": normalized["analysis"],
        "rubric": normalized["rubric"],
        "citations": normalized["citations"],
    }
    if normalized.get("options"):
        question["options"] = normalized["options"]
    return question


def _find_user_mistake(mistake_id: str, user_id: str) -> dict:
    item = get_mistake(mistake_id, user_id)
    if not item:
        raise HTTPException(status_code=404, detail="错题不存在")
    return _normalize_mistake(item)


def _persist_mistake(record: dict, expected_version: int | None = None) -> dict:
    try:
        version = expected_version if expected_version is not None else int(record.get("version") or 1)
        return update_mistake(record, version)
    except MistakeVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "错题已在其他操作中更新，请刷新后重试。", "current": exc.current},
        ) from exc


def _ensure_mistake_remedial_stage(user_id: str, mistake: dict, feedback: str) -> None:
    path = state.load_user_learning_path(user_id)
    stage_id = f"mistake_remedial_{mistake['id']}"
    stages = path.setdefault("stages", [])
    stage = next((item for item in stages if item.get("id") == stage_id), None)
    if stage:
        stage["status"] = "active"
        stage["aiReason"] = feedback
    else:
        for item in stages:
            if item.get("status") == "active":
                item["status"] = "pending"
        stages.insert(0, {
            "id": stage_id,
            "name": f"{mistake.get('knowledge', '错题')}订正补强",
            "days": 1,
            "status": "active",
            "knowledgePoints": [mistake.get("knowledge", "课程资料")],
            "resources": [],
            "tasks": ["重做原错题", "对照解析复盘错因", "完成变式题验证"],
            "acceptance": "原题订正和变式题验证均达到 70 分。",
            "aiReason": feedback,
            "source": "mistake",
            "mistakeId": mistake["id"],
        })
    state.save_user_learning_path(user_id, path)


def _complete_mistake_remedial_stage(user_id: str, mistake_id: str) -> None:
    path = state.load_user_learning_path(user_id)
    stages = path.get("stages", [])
    changed = False
    for stage in stages:
        if stage.get("mistakeId") == mistake_id:
            stage["status"] = "completed"
            changed = True
    if changed and not any(stage.get("status") == "active" for stage in stages):
        next_stage = next((stage for stage in stages if stage.get("status") == "pending"), None)
        if next_stage:
            next_stage["status"] = "active"
        state.save_user_learning_path(user_id, path)


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _llm_error_payload(exc: LLMUnavailable, request_id: str) -> dict:
    return {
        "code": getattr(exc, "code", "llm_unavailable"),
        "message": getattr(exc, "public_message", "智能辅导暂时不可用，请稍后重试。"),
        "retryable": bool(getattr(exc, "retryable", True)),
        "requestId": request_id,
    }


def _citation_from_chunk(chunk: dict) -> dict:
    return {
        "documentId": chunk.get("chunk_id", "doc_ai_intro"),
        "documentName": chunk.get("document_name", "数据结构课程课程资料"),
        "sourceLocation": chunk.get("source_location", ""),
        "chunkId": chunk.get("chunk_id", ""),
        "contentPreview": chunk.get("content", "")[:120],
        "page": chunk.get("page"),
        "similarity": chunk.get("score"),
        "fullText": chunk.get("content", ""),
    }


def _retrieve_context(question: str) -> tuple[list[dict], str, str]:
    result = search_chunks(question, top_k=3)
    chunks = result["items"]
    citations = [_citation_from_chunk(item) for item in chunks]
    evidence = "\n".join(f"- {item['section']}：{item['content']}" for item in chunks)
    return citations, evidence, result["coverage"]


def _clean_message(message: str) -> tuple[str, str]:
    mode = "问知识点"
    text = message.strip()
    if text.startswith("[") and "]" in text:
        mode, text = text[1:].split("]", 1)
        text = text.strip()
    return mode, text


def _build_tutor_answer(mode: str, question: str, evidence: str, coverage: str) -> tuple[str, bool, float]:
    normalized = question.lower()
    if coverage == "none":
        return f"""# 需要补充课程上下文

你问的是：{question}

当前《数据结构课程》知识库没有检索到足够相关的课程片段。为了避免像普通聊天机器人一样自由发挥，我先不把回答标记为高可信内容。

## 你可以继续提供

1. 题目原文或代码片段。
2. 你卡住的具体步骤。
3. 课程讲义中的相关段落。

补充后我会重新基于课程资料回答，并标明引用来源。
""", True, 0.45

    if mode == "解释代码" or "criterion" in normalized or "max_depth" in normalized or "代码" in question:
        return f"""# 代码参数解释

你问的是：`{question}`

## 课程资料依据

{evidence or "- 当前回答主要依据课程资料、课程资料与课程资料相关课程资料。"}

## criterion="entropy"

在课程资料中，`criterion="entropy"` 表示用**课程资料 / 课程资料**作为划分依据。模型会比较不同特征划分后不确定性降低了多少，课程资料越大，越适合作为当前节点的划分特征。

## max_depth

`max_depth` 控制树的最大深度。它不是直接改变课程资料公式，而是限制树继续向下划分的层数：

- 值太小：模型可能欠拟合，很多规则还没学到。
- 值太大：模型可能过拟合，把训练集里的偶然噪声也学进去。
- 实验建议：从 `max_depth=2,3,4,5` 逐步比较训练集和验证集准确率。

## 你可以这样做

先固定 `criterion="entropy"`，只调整 `max_depth`，观察验证集分数变化。这样能更清楚地看到“树深度”对过拟合的影响。
""", coverage != "sufficient", 0.9 if coverage == "sufficient" else 0.78

    if mode == "分析错题" or "错" in question or "错因" in question:
        return f"""# 错因分析

你提交的问题是：{question}

## 课程资料依据

{evidence or "- 当前回答主要依据课程中课程资料、课程资料和课程资料划分特征的定义。"}

## 可能错因

你把“课程资料”理解成了单纯的准确率或样本数量变化，但它真正衡量的是：**划分前后不确定性减少了多少**。

## 正确理解

如果一个特征划分后，每个子集合里的类别更集中，说明分类不确定性降低得更多，这个特征的课程资料就更大。

## 补强任务

1. 先写出划分前的课程资料。
2. 再分别计算每个子集的课程资料。
3. 用“划分前熵 - 加权划分后熵”得到课程资料。
""", coverage != "sufficient", 0.89 if coverage == "sufficient" else 0.76

    if mode == "生成练习" or "练习" in question or "题" in question:
        return f"""# 相似练习

## 生成依据

{evidence or "- 基于《数据结构课程》课程资料与课程资料知识点生成。"}

## 练习 1：单选题
课程资料主要衡量什么？

A. 样本数量是否增加  
B. 使用特征划分后，不确定性降低了多少  
C. 模型训练轮数是否增加  
D. 损失函数是否变复杂  

参考答案：B

## 练习 2：简答题
为什么课程资料大的特征更适合作为课程资料划分特征？

参考要点：它能让划分后的子集合类别更集中，使分类不确定性降低更多。
""", coverage != "sufficient", 0.88 if coverage == "sufficient" else 0.75

    if mode == "调整学习计划" or "计划" in question or "路径" in question:
        return f"""# 学习计划调整建议

## 调整依据

{evidence or "- 当前建议结合学习画像、路径阶段和课程资料中的课程资料知识点。"}

你现在不适合直接进入代码实验，建议先插入一个 30-45 分钟的补强任务。

## 新顺序

1. 复习课程资料的直觉含义。
2. 手算 2 个课程资料例题。
3. 对比两个特征的划分效果。
4. 再回到 实践任务 课程资料实验。

## 完成标准

你能用自己的话解释“为什么某个特征课程资料更大”，再进入代码实践。
""", coverage != "sufficient", 0.87 if coverage == "sufficient" else 0.74

    if "课程资料" in question or "熵" in question:
        return f"""# 课程资料解释

## 课程资料依据

{evidence or "- 当前回答未命中足够真实课程引用，请补充课程资料后复核。"}

课程资料可以理解为：**用了某个特征划分数据后，分类的不确定性减少了多少**。

如果一个特征划分后，每个子集合里的类别更单一，说明它让问题变清楚了，课程资料就较大。

## 小例子

如果按“是否有稳定工作”划分后，大多数样本能明显分到不同类别，这个特征就比一个几乎随机的特征更有用。

## 下一步

建议你先画一张“划分前 → 按特征划分 → 划分后”的小表，再手算一次课程资料和课程资料。
""", coverage != "sufficient", 0.88 if coverage == "sufficient" else 0.76

    if coverage in {"sufficient", "low"} and evidence:
        return f"""# 基于课程资料的回答

你问的是：{question}

## 课程资料命中

{evidence}

## 回答

这个问题需要结合《数据结构课程》的真实资料定位。你可以先把它放回当前路径阶段理解：先确认概念定义，再看它如何影响操作过程，最后通过练习或代码实验验证。

## 个性化建议

结合你的画像，建议先用图解或表格把概念关系画出来，再完成 1 道基础题和 1 道代码观察题。如果你愿意，我可以继续把它转换成练习题或加入路径补强。
""", coverage != "sufficient", 0.82 if coverage == "sufficient" else 0.68

    return f"""# 针对你的问题

你问的是：{question}

我会先按当前课程「数据结构课程」和你的学习画像来解释。如果这个问题不属于当前课程资料范围，我会标记为模型推断。

## 建议

请补充你卡住的具体知识点、题目截图文字或代码片段，我可以继续帮你拆成概念解释、例题和补强任务。
""", True, 0.74


def _infer_knowledge_point(question: str) -> str:
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
    return "待通过真实课程资料确认"


@router.post("/chat")
def tutor_chat(
    payload: TutorChatRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    return ok(answer_tutor_question(user_id, payload.message, payload.course_id))


@router.post("/chat/stream")
def tutor_chat_stream(
    payload: TutorChatRequest,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    user_id = user_id_from_authorization(authorization)
    request_id = f"tutor_{uuid4().hex[:12]}"

    def event_stream():
        started_at = time.perf_counter()
        first_delta_at: float | None = None
        answer_parts: list[str] = []
        try:
            yield _sse_event("status", {"stage": "retrieval", "message": "正在检索课程资料", "requestId": request_id})
            context = prepare_tutor_stream(user_id, payload.message, payload.course_id)
            coverage = context["retrieval"].get("coverage")
            logger.info(
                "tutor_stream request_id=%s status=retrieved model=%s coverage=%s citations=%s",
                request_id,
                "deepseek",
                coverage,
                len(context["citations"]),
            )
            yield _sse_event("status", {"stage": "connecting", "message": "正在连接 DeepSeek", "requestId": request_id})
            for delta in stream_tutor_answer(context):
                if first_delta_at is None:
                    first_delta_at = time.perf_counter()
                    logger.info(
                        "tutor_stream request_id=%s status=first_delta coverage=%s first_delta_ms=%d",
                        request_id,
                        coverage,
                        int((first_delta_at - started_at) * 1000),
                    )
                    yield _sse_event("status", {"stage": "generating", "message": "正在生成回答", "requestId": request_id})
                answer_parts.append(delta)
                yield _sse_event("delta", {"text": delta, "requestId": request_id})
            answer = "".join(answer_parts).strip()
            result = tutor_stream_result(context, answer, request_id)
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            logger.info(
                "tutor_stream request_id=%s status=completed model=%s coverage=%s total_ms=%d chars=%d",
                request_id,
                result["llm"]["model"],
                coverage,
                elapsed_ms,
                len(answer),
            )
            yield _sse_event("done", result)
        except LLMUnavailable as exc:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            logger.warning(
                "tutor_stream request_id=%s status=failed code=%s retryable=%s total_ms=%d detail=%s",
                request_id,
                getattr(exc, "code", "llm_unavailable"),
                getattr(exc, "retryable", True),
                elapsed_ms,
                str(exc)[:300],
            )
            yield _sse_event("error", _llm_error_payload(exc, request_id))
        except Exception:
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            logger.exception(
                "tutor_stream request_id=%s status=failed code=internal_error total_ms=%d",
                request_id,
                elapsed_ms,
            )
            yield _sse_event("error", {
                "code": "internal_error",
                "message": "智能辅导服务发生内部错误，请稍后重试。",
                "retryable": True,
                "requestId": request_id,
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/extras")
def tutor_extras(
    payload: TutorExtraRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    request_id = f"tutor_extra_{uuid4().hex[:12]}"
    started_at = time.perf_counter()
    try:
        result = generate_tutor_extra(
            user_id,
            message=payload.message,
            answer=payload.answer,
            extra_type=payload.type,
            course_id=payload.course_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_extra_type", "message": str(exc)}) from exc
    except LLMUnavailable as exc:
        logger.warning(
            "tutor_extra request_id=%s type=%s status=failed code=%s detail=%s",
            request_id,
            payload.type,
            getattr(exc, "code", "llm_unavailable"),
            str(exc)[:300],
        )
        status_code = 503 if getattr(exc, "retryable", True) else 409
        raise HTTPException(status_code=status_code, detail=_llm_error_payload(exc, request_id)) from exc
    logger.info(
        "tutor_extra request_id=%s type=%s status=completed total_ms=%d",
        request_id,
        payload.type,
        int((time.perf_counter() - started_at) * 1000),
    )
    result["requestId"] = request_id
    return ok(result)


@router.post("/notes")
def save_tutor_note(payload: TutorNoteRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    note = {
        "id": f"note_{uuid4().hex[:8]}",
        "userId": user_id,
        "title": payload.title,
        "content": payload.content,
        "source": "tutor",
        "createdAt": now_text(),
    }
    with state.lock:
        state.tutor_notes.insert(0, note)
        state.persist_state()
    return ok(deepcopy(note))


@router.post("/mistakes")
def save_tutor_mistake(payload: TutorMistakeRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    mistake = {
        "id": f"mistake_{uuid4().hex[:8]}",
        "userId": user_id,
        "knowledge": payload.knowledge,
        "stem": payload.stem,
        "wrongReason": payload.wrongReason,
        "fixTask": payload.fixTask or "完成 3 道针对性补强题并复述错因",
        "type": payload.type or "short",
        "options": payload.options,
        "userAnswer": "",
        "answer": payload.answer or "",
        "analysis": payload.analysis or payload.wrongReason,
        "rubric": payload.rubric,
        "citations": payload.citations,
        "correctionAttempts": [],
        "verificationQuestions": [],
        "verificationAttempts": [],
        "masteryEvidence": [],
        "status": "待订正",
        "createdAt": now_text(),
    }
    return ok(create_mistake(mistake))


@router.post("/exercises")
def generate_tutor_exercise(payload: TutorActionRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    try:
        exercise = generate_exercise_from_tutor(
            user_id,
            message=payload.message,
            mode=payload.mode,
            answer=payload.answer,
            course_id=payload.course_id,
        )
    except LLMUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    with state.lock:
        state.tutor_notes.insert(0, {
            "id": f"note_{uuid4().hex[:8]}",
            "userId": user_id,
            "title": "智能辅导生成的相似练习",
            "content": str(exercise["items"]),
            "source": "tutor_exercise",
            "createdAt": now_text(),
        })
        state.persist_state()
    return ok(exercise)


@router.post("/documents")
def generate_tutor_document(payload: TutorActionRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    try:
        document = generate_document_from_tutor(
            user_id,
            message=payload.message,
            mode=payload.mode,
            answer=payload.answer,
            course_id=payload.course_id,
        )
    except LLMUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    with state.lock:
        state.tutor_notes.insert(0, {
            "id": document["id"],
            "userId": user_id,
            "title": title,
            "content": content,
            "source": "tutor_document",
            "createdAt": document["createdAt"],
        })
        state.persist_state()
    return ok(document)


@router.post("/remedial-task")
def create_tutor_remedial_task(payload: TutorActionRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    learning_path = state.load_user_learning_path(user_id)
    topic = (payload.message or "智能辅导问题").strip()[:24]
    stage = build_remedial_stage(
        user_id=user_id,
        weakness=[topic],
        suggestion="能够结合真实课程资料复述本次问题的关键概念，并完成相似练习。",
        score=0,
        error_rate=100,
        error_reasons=["学生主动请求智能辅导补强"],
        existing_path=learning_path,
    )
    stage["id"] = f"tutor_remedial_{uuid4().hex[:8]}"
    stage["name"] = "智能辅导补强任务"
    stage["source"] = "manual"
    stage["aiReason"] = f"由智能辅导问题「{topic}」触发。"
    with state.lock:
        before_path = [item.get("name", "") for item in learning_path.get("stages", [])]
        before_active = next((item.get("name", "") for item in learning_path.get("stages", []) if item.get("status") == "active"), "当前学习阶段")
        for item in learning_path.get("stages", []):
            if item.get("status") == "active":
                item["status"] = "pending"
        learning_path.setdefault("stages", []).insert(1, stage)
        learning_path.setdefault("adjustmentHistory", []).insert(0, {
            "id": f"path_log_{uuid4().hex[:8]}",
            "source": "manual",
            "trigger": "智能辅导补强",
            "reason": "学生在智能辅导中请求加入路径补强。",
            "before": before_active,
            "after": stage["name"],
            "beforePath": before_path,
            "afterPath": [item.get("name", "") for item in learning_path.get("stages", [])],
            "evidence": [payload.message[:80]],
            "createdAt": now_text(),
        })
        state.save_user_learning_path(user_id, learning_path)
    return ok({"stage": stage, "learningPath": deepcopy(learning_path)})


@router.post("/feedback")
def save_tutor_feedback(payload: TutorFeedbackRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    record = {
        "id": f"tutor_feedback_{uuid4().hex[:8]}",
        "userId": user_id,
        "type": payload.type,
        "message": payload.message,
        "answerPreview": (payload.answer or "")[:80],
        "createdAt": now_text(),
    }
    with state.lock:
        state.tutor_notes.insert(0, {
            "id": record["id"],
            "userId": user_id,
            "title": f"智能辅导反馈：{payload.type}",
            "content": str(record),
            "source": "tutor_feedback",
            "createdAt": record["createdAt"],
        })
        state.persist_state()
    return ok(record)


@router.get("/mistakes")
def list_tutor_mistakes(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    user_mistakes = [_normalize_mistake(item) for item in list_mistakes(user_id)]
    return ok(deepcopy(user_mistakes))


@router.post("/mistakes/{mistake_id}/status")
def update_mistake_status(
    mistake_id: str,
    payload: MistakeStatusRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id_from_authorization(authorization)
    raise HTTPException(status_code=409, detail="错题状态由订正和变式题评分自动更新，不能手动修改。")


@router.post("/mistakes/{mistake_id}/correction")
def submit_mistake_correction(
    mistake_id: str,
    payload: MistakeCorrectionRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    if not payload.answer.strip():
        raise HTTPException(status_code=400, detail="请先完成原题订正。")
    with state.lock:
        mistake = _find_user_mistake(mistake_id, user_id)
        if mistake["status"] == "已掌握":
            raise HTTPException(status_code=409, detail="该错题已经完成掌握验证。")
        result = score_question(_mistake_question(mistake), payload.answer)
        attempt = {
            "answer": payload.answer,
            "score": result["score"],
            "correct": result["score"] >= MISTAKE_PASS_SCORE,
            "hitKeywords": result["hit_keywords"],
            "missingKeywords": result["missing_keywords"],
            "errorReason": result["error_reason"],
            "createdAt": now_text(),
        }
        mistake["correctionAttempts"].append(attempt)
        mistake["status"] = "待验证" if attempt["correct"] else "订正中"
        mistake["latestCorrection"] = attempt
        if attempt["correct"]:
            mistake["masteryEvidence"].append(f"原题订正 {attempt['score']} 分")
        mistake = _persist_mistake(mistake, payload.expectedVersion)
        if not attempt["correct"]:
            _ensure_mistake_remedial_stage(user_id, mistake, result["error_reason"] or "原题订正未达到 70 分。")
    return ok({"mistake": deepcopy(mistake), "result": attempt})


@router.post("/mistakes/{mistake_id}/similar")
def generate_similar_mistake(
    mistake_id: str,
    payload: MistakeSimilarRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    source = _find_user_mistake(mistake_id, user_id)
    if source["status"] not in {"待验证", "已掌握"}:
        raise HTTPException(status_code=409, detail="请先完成原题订正，达到 70 分后再生成变式题。")
    if payload.expectedVersion is not None and payload.expectedVersion != int(source.get("version") or 1):
        raise HTTPException(
            status_code=409,
            detail={"message": "错题已在其他操作中更新，请刷新后重试。", "current": source},
        )
    if not source["verificationQuestions"]:
        generated = generate_mistake_variants(source)
        source["verificationQuestions"] = generated["questions"]
        source["generationMode"] = generated["generationMode"]
        source["generationReason"] = generated["generationReason"]
        source = _persist_mistake(source, payload.expectedVersion)
    else:
        generated = {
            "generationMode": source.get("generationMode", "rule_fallback"),
            "generationReason": source.get("generationReason", "复用已生成的变式题。"),
        }
    return ok({
        "mistake": deepcopy(source),
        "questions": deepcopy(source["verificationQuestions"]),
        **generated,
    })


@router.post("/mistakes/{mistake_id}/verification")
def submit_mistake_verification(
    mistake_id: str,
    payload: MistakeVerificationRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    with state.lock:
        mistake = _find_user_mistake(mistake_id, user_id)
        if mistake["status"] != "待验证":
            raise HTTPException(status_code=409, detail="请先通过原题订正，再进行变式题验证。")
        questions = mistake.get("verificationQuestions", [])
        if not questions:
            raise HTTPException(status_code=409, detail="请先生成变式题。")
        missing = [question["id"] for question in questions if not str(payload.answers.get(question["id"], "")).strip()]
        if missing:
            raise HTTPException(status_code=400, detail="请完成全部变式题后再提交。")
        results = []
        for question in questions:
            scored = score_question(question, payload.answers.get(question["id"], ""))
            results.append({
                "questionId": question["id"],
                "answer": payload.answers.get(question["id"], ""),
                "score": scored["score"],
                "correct": scored["score"] >= MISTAKE_PASS_SCORE,
                "hitKeywords": scored["hit_keywords"],
                "missingKeywords": scored["missing_keywords"],
                "errorReason": scored["error_reason"],
            })
        passed = all(item["correct"] for item in results)
        attempt = {"results": results, "passed": passed, "createdAt": now_text()}
        mistake["verificationAttempts"].append(attempt)
        mistake["latestVerification"] = attempt
        if passed:
            mistake["status"] = "已掌握"
            mistake["masteredAt"] = now_text()
            mistake["masteryEvidence"].append(
                "变式题验证：" + "、".join(f"{item['score']} 分" for item in results)
            )
        else:
            mistake["status"] = "待验证"
            failed = [item for item in results if not item["correct"]]
            feedback = "；".join(item["errorReason"] for item in failed if item["errorReason"]) or "变式题验证未达到 70 分。"
        mistake = _persist_mistake(mistake, payload.expectedVersion)
        if passed:
            _complete_mistake_remedial_stage(user_id, mistake_id)
        else:
            _ensure_mistake_remedial_stage(user_id, mistake, feedback)
    return ok({
        "mistake": deepcopy(mistake),
        "passed": passed,
        "results": results,
        "suggestion": "已通过迁移验证。" if passed else "请根据缺失评分点复盘后再次完成变式题。",
    })
