from __future__ import annotations

import json
import re
from copy import deepcopy
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from .. import state
from ..demo_data import PROFILE, now_text
from ..persistence import load_json, save_json
from ..schemas import (
    ProfileConfirmRequest,
    ProfileDialogSessionRequest,
    ProfileDialogTurnRequest,
    ProfileExtractRequest,
    ProfileManualUpdateDraftRequest,
    ProfileUpdateConfirmRequest,
    ProfileUpdateRejectRequest,
)
from ..services.llm_service import LLMUnavailable, call_deepseek_json, llm_model_name, stream_deepseek_json
from ..services.strict_generation import blocked_detail
from ..services.profile_update_service import (
    build_profile_context,
    confirm_profile_update_drafts,
    create_profile_update_draft,
    list_profile_update_drafts,
    reject_profile_update_draft,
)
from ..services.vector_service import index_profile, vector_status
from ..utils import is_seed_user, ok, user_id_from_authorization, user_scoped_key

router = APIRouter(prefix="/api/profile", tags=["profile"])


def _profile_key(user_id: str) -> str:
    return user_scoped_key("profile_items", user_id)


def _profile_dialog_session_key(user_id: str) -> str:
    return user_scoped_key("profile_dialog_session", user_id)


def _profile_default(user_id: str) -> list[dict]:
    return deepcopy(PROFILE) if is_seed_user(user_id) else []


def _load_profile_items(user_id: str) -> list[dict]:
    return load_json(_profile_key(user_id), _profile_default(user_id))


def _save_profile_items(user_id: str, items: list[dict]) -> None:
    save_json(_profile_key(user_id), items)


IMPACT_BY_DIMENSION = {
    "专业背景": "影响案例语境、术语解释深度和课程知识边界。",
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

DIMENSION_ALIASES = {
    "学习进度": "年级 / 学习阶段",
    "可学习时间": "可用学习时间",
    "易错知识点": "易错点",
    "代码能力水平": "实践能力水平",
}

REQUIRED_DIMENSIONS = [
    "专业背景",
    "年级 / 学习阶段",
    "知识基础",
    "学习目标",
    "薄弱知识点",
    "认知风格",
    "资源偏好",
    "可用学习时间",
    "易错点",
    "实践能力水平",
]

MIN_PROFILE_DIMENSIONS_TO_EXTRACT = 5

REGISTRATION_CONFIRMED_DIMENSIONS = {"专业背景", "年级 / 学习阶段"}

GRADE_PATTERN = r"(大一|大二|大三|大四|研一|研二|研三|本科[一二三四]年级)"

PLACEHOLDER_VALUE_PATTERNS = [
    "课程资料待上传",
    "待确认",
    "待补充",
    "未知",
    "默认值",
    "待学生确认",
    "待通过测评识别",
    "待通过练习题和阶段测评识别",
]

PROFILE_SYSTEM_PROMPT = """
你是《数据结构课程》课程的学习画像构建 Agent。
请根据学生自然语言描述抽取学习画像，围绕真实数据结构课程资料、当前学习阶段、薄弱知识点、资源偏好和代码实践生成结构化结果。

必须只输出合法 JSON，不要输出 Markdown，不要解释。
JSON 格式：
{
  "dimensions": [
    {
      "dimension": "专业背景",
      "value": "学生明确回答的专业背景",
      "confidence": 0.9,
      "source": "dialog",
      "status": "draft",
      "reason": "学生明确提到自己的专业或学习背景",
      "impact": "影响案例语境、资源难度和代码实践深度"
    }
  ],
  "missing_dimensions": [],
  "followup_questions": [],
  "need_confirm": true
}

dimensions 围绕以下 10 个维度抽取，有明确证据的维度才生成：
专业背景、年级 / 学习阶段、知识基础、学习目标、薄弱知识点、认知风格、资源偏好、可用学习时间、易错点、实践能力水平。

要求：
1. 严禁编造画像。只能根据学生明确回答、历史画像上下文、测评/错题/行为上下文抽取。
2. 如果某项没有足够证据，不要生成该 dimension，必须写入 missing_dimensions，并在 followup_questions 中给出追问。
2. source 固定为 dialog，status 固定为 draft。
3. value 必须是中文自然语言，不要写 null、待确认、待补充、未知、默认值。
4. impact 要说明该画像项如何影响资源推荐、学习路径、智能辅导或测评。
5. confidence 必须是 0 到 1 的数字；没有证据的维度不要降低置信度硬凑。
6. need_confirm 只有在至少 5 个维度有明确证据时才为 true；缺失维度继续放入 missing_dimensions。
"""

PROFILE_DIALOG_TURN_PROMPT = """
你是《数据结构课程》的学习画像对话 Agent。
你要根据学生已经说过的话，判断 10 个画像维度哪些已有明确证据，哪些还缺，然后生成下一句自然追问。

必须只输出合法 JSON，不要输出 Markdown，不要解释。
JSON 格式：
{
  "assistantMessage": "我已经理解你提到的学习目标。还需要确认你更容易在哪类题或实验步骤中出错，请举一个例子。",
  "coveredDimensions": ["学习目标"],
  "missingDimensions": ["薄弱知识点", "易错点"],
  "nextQuestionTitle": "确认薄弱点和易错点",
  "canExtract": false,
  "agentTrace": ["读取学生回答", "核对画像维度覆盖", "生成下一句追问"],
  "llmStatus": {"usedLLM": true, "model": "deepseek-v4-flash", "fallback": false}
}

10 个画像维度固定为：
专业背景、年级 / 学习阶段、知识基础、学习目标、薄弱知识点、认知风格、资源偏好、可用学习时间、易错点、实践能力水平。

要求：
1. 严禁编造学生信息。coveredDimensions 只能包含学生明确回答或 current_profile_context 中已有真实画像能支持的维度。
2. 如果某维度没有明确证据，必须放入 missingDimensions，不要用默认值补齐。
3. canExtract 只有在至少 5 个维度已有明确证据时才为 true；缺失维度仍必须放入 missingDimensions。
4. assistantMessage 要像真实老师追问一样自然、简短、有上下文，不要重复机械问卷。
5. 不要输出思维链，只输出面向学生的高层状态和下一句回复。
"""


def _normalize_dimension(dimension: str) -> str:
    return DIMENSION_ALIASES.get(dimension, dimension)


def _safe_confidence(value: object, default: float = 0.76) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = default
    return max(0.0, min(1.0, score))


def _is_placeholder_profile_value(value: object) -> bool:
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return True
    if text in {"45分钟", "45min", "45m"}:
        return True
    return any(pattern in text for pattern in PLACEHOLDER_VALUE_PATTERNS)


def _clean_dimension_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        dimension = _normalize_dimension(str(value).strip())
        if dimension in REQUIRED_DIMENSIONS and dimension not in result:
            result.append(dimension)
    return result


def _has_enough_profile_dimensions(dimensions: set[str] | list[str]) -> bool:
    return len(set(dimensions)) >= MIN_PROFILE_DIMENSIONS_TO_EXTRACT


def _sanitize_dialog_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    sanitized: list[dict[str, str]] = []
    for item in messages:
        role = str(item.get("role") or "").strip()
        text = str(item.get("text") or "").strip()
        if role not in {"agent", "student"}:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_DIALOG_MESSAGE", "message": "画像对话消息角色必须是 agent 或 student。"},
            )
        if not text:
            continue
        sanitized.append({"role": role, "text": text})
    return sanitized


def _sanitize_dialog_drafts(drafts: list[dict]) -> list[dict]:
    sanitized: list[dict] = []
    for item in drafts:
        if not isinstance(item, dict):
            continue
        dimension = _normalize_dimension(str(item.get("dimension", "")).strip())
        if dimension not in REQUIRED_DIMENSIONS:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_PROFILE_DIMENSION", "message": f"画像维度「{dimension or '未知'}」不在允许范围内。"},
            )
        sanitized.append({
            **item,
            "dimension": dimension,
            "impact": item.get("impact") or IMPACT_BY_DIMENSION.get(dimension, "影响后续学习路径、资源推荐和智能辅导。"),
        })
    return sanitized


def _sanitize_profile_dialog_session(payload: ProfileDialogSessionRequest) -> dict:
    covered = _clean_dimension_list(payload.coveredDimensions)
    missing = [
        dimension
        for dimension in _clean_dimension_list(payload.missingDimensions)
        if dimension not in covered
    ]
    missing.extend(dimension for dimension in REQUIRED_DIMENSIONS if dimension not in covered and dimension not in missing)
    return {
        "messages": _sanitize_dialog_messages(payload.messages),
        "rawProfileInput": str(payload.rawProfileInput or "").strip(),
        "coveredDimensions": covered,
        "missingDimensions": missing,
        "nextQuestionTitle": str(payload.nextQuestionTitle or "先介绍你的学习情况").strip(),
        "canExtract": bool(payload.canExtract) and _has_enough_profile_dimensions(covered),
        "draftItems": _sanitize_dialog_drafts(payload.draftItems),
        "saveCompleted": bool(payload.saveCompleted),
        "updatedAt": now_text(),
    }


def _profile_dialog_turn_payload(result: dict, explicit_values: dict[str, str | None] | None = None) -> dict | None:
    assistant_message = str(result.get("assistantMessage", "")).strip()
    if not assistant_message:
        return None
    covered = _clean_dimension_list(result.get("coveredDimensions", []))
    explicit_values = explicit_values or {}
    for dimension, value in explicit_values.items():
        if dimension in REQUIRED_DIMENSIONS and value and dimension not in covered:
            covered.append(dimension)
    missing = _clean_dimension_list(result.get("missingDimensions", []))
    missing.extend(dimension for dimension in REQUIRED_DIMENSIONS if dimension not in covered)
    missing = [dimension for dimension in missing if dimension not in covered]
    missing = list(dict.fromkeys(missing))
    return {
        "assistantMessage": assistant_message,
        "coveredDimensions": covered,
        "missingDimensions": missing,
        "nextQuestionTitle": str(result.get("nextQuestionTitle") or "继续补充画像信息").strip(),
        "canExtract": _has_enough_profile_dimensions(covered),
        "agentTrace": [
            str(item).strip()
            for item in result.get("agentTrace", [])
            if str(item).strip()
        ] or ["读取学生回答", "核对画像维度覆盖", "生成下一句追问"],
        "llmStatus": {
            "usedLLM": True,
            "model": llm_model_name(),
            "fallback": False,
        },
    }


def _profile_payload_from_llm_result(result: dict) -> dict | None:
    raw_dimensions = result.get("dimensions", [])
    if not isinstance(raw_dimensions, list):
        return None

    drafts: list[dict] = []
    seen: set[str] = set()
    for raw in raw_dimensions:
        if not isinstance(raw, dict):
            continue
        dimension = _normalize_dimension(str(raw.get("dimension", "")).strip())
        if dimension not in REQUIRED_DIMENSIONS or dimension in seen:
            continue
        value = str(raw.get("value", "")).strip()
        if _is_placeholder_profile_value(value):
            continue
        seen.add(dimension)
        drafts.append({
            "id": f"draft_llm_{uuid4().hex[:8]}",
            "dimension": dimension,
            "value": value,
            "confidence": _safe_confidence(raw.get("confidence")),
            "source": raw.get("source") or "dialog",
            "status": "draft",
            "updatedAt": now_text(),
            "reason": raw.get("reason") or "由 DeepSeek 根据学生自然语言描述抽取。",
            "impact": raw.get("impact") or IMPACT_BY_DIMENSION.get(dimension, "影响后续学习路径、资源推荐和智能辅导。"),
            "version": 1,
        })

    missing = [
        _normalize_dimension(str(item).strip())
        for item in result.get("missing_dimensions", [])
        if _normalize_dimension(str(item).strip()) in REQUIRED_DIMENSIONS
    ]
    missing.extend(dimension for dimension in REQUIRED_DIMENSIONS if dimension not in seen)
    missing = list(dict.fromkeys(missing))

    followups = [
        str(item).strip()
        for item in result.get("followup_questions", [])
        if str(item).strip()
    ]

    if not _has_enough_profile_dimensions(seen):
        return {
            "dimensions": drafts,
            "need_confirm": False,
            "missingDimensions": missing,
            "followupQuestions": followups or [f"请补充：{dimension}" for dimension in missing[:4]],
            "agentTrace": [
                f"调用 DeepSeek 模型：{llm_model_name()}",
                "按画像维度抽取结构化 JSON",
                "校验字段证据和必需维度",
                f"有效维度少于 {MIN_PROFILE_DIMENSIONS_TO_EXTRACT} 个，未生成画像草稿",
            ],
            "parsedFields": {
                "provider": "deepseek",
                "model": llm_model_name(),
                "dimensionCount": len(drafts),
                "fallback": False,
                "insufficient": True,
                "minRequiredDimensions": MIN_PROFILE_DIMENSIONS_TO_EXTRACT,
            },
            "llmStatus": {
                "usedLLM": True,
                "model": llm_model_name(),
                "fallback": False,
            },
        }

    return {
        "dimensions": drafts,
        "need_confirm": True,
        "agentTrace": [
            f"调用 DeepSeek 模型：{llm_model_name()}",
            "按 10 个画像维度抽取结构化 JSON",
            "校验字段、置信度和推荐影响",
            "核心维度达到生成门槛，缺失项保留为待完善",
        ],
        "parsedFields": {
            "provider": "deepseek",
            "model": llm_model_name(),
            "dimensionCount": len(drafts),
            "fallback": False,
            "minRequiredDimensions": MIN_PROFILE_DIMENSIONS_TO_EXTRACT,
        },
        "missingDimensions": missing,
        "followupQuestions": followups if missing else [],
        "llmStatus": {
            "usedLLM": True,
            "model": llm_model_name(),
            "fallback": False,
        },
    }


def _raise_profile_extraction_error(payload: dict | None = None, *, detail: str | None = None) -> None:
    if payload:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROFILE_EXTRACTION_INSUFFICIENT",
                "message": "画像信息不足，未生成画像草稿。请补充缺失维度后重试。",
                "agentName": "画像构建 Agent",
                "missingRequirements": payload.get("missingDimensions", []),
                "missingDimensions": payload.get("missingDimensions", []),
                "followupQuestions": payload.get("followupQuestions", []),
                "llmStatus": payload.get("llmStatus", {
                    "usedLLM": True,
                    "model": llm_model_name(),
                    "fallback": False,
                }),
            },
        )
    raise HTTPException(
        status_code=503,
        detail=blocked_detail(
            agent_name="画像构建 Agent",
            code="LLM_UNAVAILABLE",
            message="DeepSeek 画像抽取服务暂不可用，未生成画像草稿。",
            missing_requirements=["DeepSeek 画像抽取 JSON"],
            detail=detail or "LLM unavailable",
        ) | {
            "code": "LLM_UNAVAILABLE",
        },
    )


def _confirmed_profile_map(user_id: str) -> dict[str, dict]:
    return {
        _normalize_dimension(str(item.get("dimension", ""))): item
        for item in _load_profile_items(user_id)
        if item.get("status") == "confirmed" and str(item.get("value") or "").strip()
    }


def _similar_profile_value(old_value: str, new_value: str) -> bool:
    old_value = re.sub(r"\s+", "", old_value)
    new_value = re.sub(r"\s+", "", new_value)
    if not old_value or not new_value:
        return False
    return old_value == new_value or old_value in new_value or new_value in old_value


def _split_major_grade_text(value: str | None) -> tuple[str | None, str | None]:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ，。,；;")
    if not text:
        return None, None
    grade_match = re.search(GRADE_PATTERN, text)
    grade = grade_match.group(1) if grade_match else None
    if not grade:
        return text, None

    candidates = [
        part.strip(" ，。,；;/|")
        for part in re.split(r"[/|，,；;\s]+", text)
        if part.strip(" ，。,；;/|")
    ]
    major = next((part for part in candidates if grade not in part), "")
    if not major:
        major = text[:grade_match.start()].strip(" ，。,；;/|")
    major = re.sub(r"(专业|年级|学习阶段|我是|我现在是|目前是|为|是|:|：)+$", "", major).strip(" ，。,；;/|")
    return (major or None), grade


def _explicit_major_from_message(message: str, labeled_major: str | None = None) -> str | None:
    if labeled_major:
        major, _grade = _split_major_grade_text(labeled_major)
        return major or labeled_major
    composite_major, _composite_grade = _split_major_grade_text(
        _line_value(message, ["专业/年级", "专业年级", "专业 / 年级"])
    )
    if composite_major:
        return composite_major
    patterns = [
        r"(?:我的)?专业(?:是|为|：|:)\s*([^，。；;\n]{2,30})",
        rf"(?:我是|我现在是|目前是)\s*([^，。；;\n]{{2,30}}?)(?:专业)?\s*{GRADE_PATTERN}",
        r"(?:转专业到|转到)\s*([^，。；;\n]{2,30}?专业)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            major, _grade = _split_major_grade_text(match.group(1).strip())
            return major or match.group(1).strip()
    return None


def _explicit_stage_from_message(
    message: str,
    *,
    labeled_grade: str | None = None,
    labeled_stage: str | None = None,
) -> str | None:
    if labeled_stage and labeled_grade:
        return f"{labeled_grade}，{labeled_stage}"
    if labeled_stage:
        return labeled_stage
    if labeled_grade:
        _major, grade = _split_major_grade_text(labeled_grade)
        return grade or labeled_grade
    _composite_major, composite_grade = _split_major_grade_text(
        _line_value(message, ["专业/年级", "专业年级", "专业 / 年级"])
    )
    if composite_grade:
        return composite_grade
    grade_match = re.search(rf"(?:我现在是|我是|目前是|年级(?:是|为|：|:)\s*)?{GRADE_PATTERN}", message)
    if grade_match:
        return grade_match.group(1)
    return None


def _explicit_profile_draft(dimension: str, value: str) -> dict:
    return {
        "id": f"draft_explicit_{uuid4().hex[:8]}",
        "dimension": dimension,
        "value": value,
        "confidence": 0.92,
        "source": "dialog",
        "status": "draft",
        "updatedAt": now_text(),
        "reason": "学生在本轮画像对话中明确填写该信息。",
        "impact": IMPACT_BY_DIMENSION.get(dimension, "影响后续学习路径、资源推荐和智能辅导。"),
        "version": 1,
    }


def _merge_explicit_profile_drafts(drafts: list[dict], explicit_values: dict[str, str | None]) -> list[dict]:
    merged = list(drafts)
    existing = {_normalize_dimension(str(item.get("dimension", ""))) for item in merged}
    for dimension, value in explicit_values.items():
        clean_value = str(value or "").strip()
        if (
            dimension in REGISTRATION_CONFIRMED_DIMENSIONS
            and clean_value
            and dimension not in existing
            and not _is_placeholder_profile_value(clean_value)
        ):
            merged.append(_explicit_profile_draft(dimension, clean_value))
            existing.add(dimension)
    return merged


def _protect_confirmed_registration_dimensions(
    user_id: str,
    drafts: list[dict],
    *,
    explicit_values: dict[str, str | None] | None = None,
) -> tuple[list[dict], list[dict]]:
    confirmed = _confirmed_profile_map(user_id)
    explicit_values = explicit_values or {}
    protected: list[dict] = []
    update_drafts: list[dict] = []

    for item in drafts:
        dimension = _normalize_dimension(str(item.get("dimension", "")))
        existing = confirmed.get(dimension)
        if dimension not in REGISTRATION_CONFIRMED_DIMENSIONS or not existing:
            protected.append(item)
            continue

        explicit_value = (explicit_values.get(dimension) or "").strip()
        candidate_value = explicit_value or str(item.get("value") or "").strip()
        old_value = str(existing.get("value") or "").strip()
        if explicit_value and not _similar_profile_value(old_value, explicit_value):
            update_drafts.append(create_profile_update_draft(
                user_id,
                dimension=dimension,
                value=explicit_value,
                source="dialog",
                trigger="用户在画像对话中主动更新注册身份信息",
                evidence=f"本次对话明确提到「{explicit_value}」，原画像为「{old_value}」。",
                confidence=_safe_confidence(item.get("confidence"), 0.9),
                old_value=old_value,
                impact=IMPACT_BY_DIMENSION.get(dimension),
            ))
        elif not _similar_profile_value(old_value, candidate_value) and explicit_value:
            update_drafts.append(create_profile_update_draft(
                user_id,
                dimension=dimension,
                value=candidate_value,
                source="dialog",
                trigger="用户在画像对话中主动更新注册身份信息",
                evidence=f"本次对话明确提到「{candidate_value}」，原画像为「{old_value}」。",
                confidence=_safe_confidence(item.get("confidence"), 0.86),
                old_value=old_value,
                impact=IMPACT_BY_DIMENSION.get(dimension),
            ))

    return protected, update_drafts


def _profile_context_message(message: str, profile_context: dict | None = None) -> str:
    if not profile_context:
        return message
    return json.dumps(
        {"student_message": message, "profile_context": profile_context},
        ensure_ascii=False,
    )


def _llm_profile_payload(message: str, profile_context: dict | None = None) -> dict | None:
    try:
        result = call_deepseek_json(
            PROFILE_SYSTEM_PROMPT,
            _profile_context_message(message, profile_context),
            temperature=0.2,
            max_tokens=3200,
        )
    except LLMUnavailable:
        return None
    payload = _profile_payload_from_llm_result(result)
    if payload and profile_context:
        payload["profileContext"] = profile_context
    return payload


def _explicit_registration_values(message: str) -> dict[str, str | None]:
    major_value = _line_value(message, ["专业背景", "专业"])
    grade_value = _line_value(message, ["年级"])
    stage_value = _line_value(message, ["学习阶段", "阶段"])
    return {
        "专业背景": _explicit_major_from_message(message, major_value),
        "年级 / 学习阶段": _explicit_stage_from_message(
            message,
            labeled_grade=grade_value,
            labeled_stage=stage_value,
        ),
    }


def _extract_profile_payload_for_user(user_id: str, message: str) -> dict:
    profile_context = build_profile_context(user_id, message)
    llm_payload = _llm_profile_payload(message, profile_context)
    if not llm_payload:
        _raise_profile_extraction_error(detail="DeepSeek 未返回可用画像结果")

    explicit_values = _explicit_registration_values(message)
    llm_payload["dimensions"] = _merge_explicit_profile_drafts(llm_payload["dimensions"], explicit_values)
    present_dimensions = {
        _normalize_dimension(str(item.get("dimension", "")))
        for item in llm_payload["dimensions"]
        if str(item.get("value") or "").strip()
    }
    missing_dimensions = [
        dimension
        for dimension in llm_payload.get("missingDimensions", [])
        if dimension not in present_dimensions
    ]
    llm_payload["missingDimensions"] = missing_dimensions
    llm_payload["need_confirm"] = _has_enough_profile_dimensions(present_dimensions)

    if not llm_payload.get("need_confirm"):
        _raise_profile_extraction_error(llm_payload)

    dimensions, update_drafts = _protect_confirmed_registration_dimensions(
        user_id,
        llm_payload["dimensions"],
        explicit_values=explicit_values,
    )
    llm_payload["dimensions"] = dimensions
    llm_payload["need_confirm"] = bool(dimensions)
    llm_payload["registrationProfileReused"] = True
    llm_payload["updateDrafts"] = update_drafts
    llm_payload["profileContext"] = profile_context
    if not dimensions:
        _raise_profile_extraction_error({
            **llm_payload,
            "missingDimensions": REQUIRED_DIMENSIONS,
            "followupQuestions": ["请补充完整学习情况后重新生成画像草稿。"],
        })
    return llm_payload


def _confirm_profile_items(user_id: str, dimensions: list[dict]) -> list[dict]:
    incoming = []
    for item in dimensions:
        dimension = _normalize_dimension(item.get("dimension", ""))
        value = item.get("value", "")
        if dimension not in REQUIRED_DIMENSIONS or _is_placeholder_profile_value(value):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "INVALID_PROFILE_VALUE",
                    "message": f"画像「{dimension or '未知维度'}」缺少真实学生证据，未写入画像。",
                },
            )
        incoming.append({
            **item,
            "dimension": dimension,
            "impact": item.get("impact") or IMPACT_BY_DIMENSION.get(dimension, "影响后续学习路径、资源推荐和智能辅导。"),
            "status": "confirmed",
            "updatedAt": now_text(),
        })
    with state.lock:
        profile_items = _load_profile_items(user_id)
        incoming_dimensions = {item["dimension"] for item in incoming}
        profile_items = [
            item for item in profile_items
            if _normalize_dimension(item.get("dimension", "")) not in incoming_dimensions
        ]
        profile_items.extend(incoming)
        _save_profile_items(user_id, profile_items)
        index_profile(profile_items, user_id=user_id)
        if is_seed_user(user_id):
            state.profile_items[:] = profile_items
            state.persist_state()
    return deepcopy(profile_items)


def _create_manual_profile_update_drafts(user_id: str, items: list) -> list[dict]:
    if not items:
        raise HTTPException(
            status_code=400,
            detail={"code": "EMPTY_PROFILE_UPDATE", "message": "请至少选择一个需要更新的画像维度。"},
        )

    confirmed = _confirmed_profile_map(user_id)
    candidates: list[dict[str, str]] = []
    invalid_messages: list[str] = []
    seen_dimensions: set[str] = set()

    for raw in items:
        dimension = _normalize_dimension(str(raw.dimension or "").strip())
        value = str(raw.value or "").strip()
        note = str(raw.note or "").strip()

        if dimension in seen_dimensions:
            continue
        seen_dimensions.add(dimension)

        if dimension not in REQUIRED_DIMENSIONS:
            invalid_messages.append(f"画像维度「{dimension or '未知'}」不在允许范围内。")
            continue
        if _is_placeholder_profile_value(value):
            invalid_messages.append(f"画像「{dimension}」需要填写真实的新内容。")
            continue

        old_value = str(confirmed.get(dimension, {}).get("value") or "").strip()
        if _similar_profile_value(old_value, value):
            invalid_messages.append(f"画像「{dimension}」的新内容与当前画像一致。")
            continue

        candidates.append({
            "dimension": dimension,
            "value": value,
            "old_value": old_value,
            "evidence": note or f"用户在画像档案页主动将「{dimension}」更新为「{value}」。",
        })

    if invalid_messages or not candidates:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_PROFILE_UPDATE",
                "message": "；".join(invalid_messages) or "没有可更新内容。",
            },
        )

    for candidate in candidates:
        create_profile_update_draft(
            user_id,
            dimension=candidate["dimension"],
            value=candidate["value"],
            source="manual",
            trigger="用户主动更新画像",
            evidence=candidate["evidence"],
            confidence=0.9,
            old_value=candidate["old_value"],
            impact=IMPACT_BY_DIMENSION.get(candidate["dimension"]),
        )

    return list_profile_update_drafts(user_id)


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _line_value(message: str, labels: list[str]) -> str | None:
    for label in labels:
        pattern = rf"(?:^|\n)\s*{re.escape(label)}\s*[:：]\s*(.+?)(?=\n\s*[\u4e00-\u9fa5A-Za-z /]+[:：]|\Z)"
        match = re.search(pattern, message, re.S)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip(" ，。,；;")
    return None


@router.get("/me")
def get_profile(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    return ok(deepcopy(_load_profile_items(user_id)))


@router.get("/context")
def get_profile_context(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    return ok(build_profile_context(user_id))


@router.get("/update-drafts")
def get_profile_update_drafts(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    return ok(list_profile_update_drafts(user_id))


@router.post("/update-drafts/manual")
def create_manual_profile_update_drafts_api(
    payload: ProfileManualUpdateDraftRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    return ok({"drafts": _create_manual_profile_update_drafts(user_id, payload.items)})


@router.post("/update-drafts/confirm")
def confirm_profile_update_drafts_api(
    payload: ProfileUpdateConfirmRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    return ok(confirm_profile_update_drafts(user_id, payload.draft_ids))


@router.post("/update-drafts/reject")
def reject_profile_update_draft_api(
    payload: ProfileUpdateRejectRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    return ok(reject_profile_update_draft(user_id, payload.draft_id))


@router.get("/vector-status")
def get_profile_vector_status() -> dict:
    return ok(vector_status())


@router.post("/dialog")
def profile_dialog(payload: ProfileExtractRequest) -> dict:
    return extract_profile(payload)


@router.get("/dialog/session")
def get_profile_dialog_session(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    session = load_json(_profile_dialog_session_key(user_id), {})
    return ok(session or None)


@router.post("/dialog/session")
def save_profile_dialog_session(
    payload: ProfileDialogSessionRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    session = _sanitize_profile_dialog_session(payload)
    save_json(_profile_dialog_session_key(user_id), session)
    return ok(session)


@router.post("/dialog/session/reset")
def reset_profile_dialog_session(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    save_json(_profile_dialog_session_key(user_id), {})
    return ok({"cleared": True})


@router.post("/dialog/turn")
def profile_dialog_turn(
    payload: ProfileDialogTurnRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    profile_context = payload.current_profile_context or build_profile_context(user_id)
    user_prompt = json.dumps(
        {
            "conversation": payload.conversation,
            "answered_dimensions": payload.answered_dimensions,
            "required_dimensions": payload.required_dimensions or REQUIRED_DIMENSIONS,
            "current_profile_context": profile_context,
        },
        ensure_ascii=False,
    )
    try:
        result = call_deepseek_json(
            PROFILE_DIALOG_TURN_PROMPT,
            user_prompt,
            temperature=0.25,
            max_tokens=1600,
        )
    except LLMUnavailable as exc:
        _raise_profile_extraction_error(detail=str(exc))
    conversation_text = "\n".join(
        str(item.get("text") or "")
        for item in payload.conversation
        if str(item.get("role") or "") == "student"
    )
    explicit_values = _explicit_registration_values(conversation_text)
    dialog_payload = _profile_dialog_turn_payload(result, explicit_values)
    if not dialog_payload:
        _raise_profile_extraction_error(detail="DeepSeek 未返回可用画像对话回合")
    return ok(dialog_payload)


@router.post("/extract/stream")
def extract_profile_stream(
    payload: ProfileExtractRequest,
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    user_id = user_id_from_authorization(authorization)
    profile_context = build_profile_context(user_id, payload.message)
    explicit_values = _explicit_registration_values(payload.message)

    def event_stream():
        yield _sse_event("status", {"message": "正在连接 DeepSeek V4 Flash 画像构建 Agent"})
        yield _sse_event("status", {"message": "正在读取学生自然语言描述"})
        try:
            yield _sse_event("status", {"message": "正在逐字接收课程资料结构化画像输出"})
            llm_message = _profile_context_message(payload.message, profile_context)
            for item in stream_deepseek_json(PROFILE_SYSTEM_PROMPT, llm_message, temperature=0.2, max_tokens=3200):
                if item["type"] == "delta":
                    yield _sse_event("token", {"text": item["content"]})
                    continue
                llm_payload = _profile_payload_from_llm_result(item["content"])
                if not llm_payload:
                    raise LLMUnavailable("DeepSeek 返回的画像维度不完整")
                llm_payload["dimensions"] = _merge_explicit_profile_drafts(llm_payload["dimensions"], explicit_values)
                present_dimensions = {
                    _normalize_dimension(str(draft.get("dimension", "")))
                    for draft in llm_payload["dimensions"]
                    if str(draft.get("value") or "").strip()
                }
                missing_dimensions = [
                    dimension
                    for dimension in llm_payload.get("missingDimensions", [])
                    if dimension not in present_dimensions
                ]
                llm_payload["missingDimensions"] = missing_dimensions
                llm_payload["need_confirm"] = _has_enough_profile_dimensions(present_dimensions)
                if not llm_payload.get("need_confirm"):
                    yield _sse_event("error", {
                        "message": f"画像信息不足，至少需要 {MIN_PROFILE_DIMENSIONS_TO_EXTRACT} 个维度才能生成画像草稿。",
                        "detail": "PROFILE_EXTRACTION_INSUFFICIENT",
                        "missingDimensions": llm_payload.get("missingDimensions", []),
                        "followupQuestions": llm_payload.get("followupQuestions", []),
                        "llmStatus": llm_payload.get("llmStatus"),
                    })
                    return
                dimensions, update_drafts = _protect_confirmed_registration_dimensions(
                    user_id,
                    llm_payload["dimensions"],
                    explicit_values=explicit_values,
                )
                llm_payload["dimensions"] = dimensions
                llm_payload["need_confirm"] = bool(dimensions)
                llm_payload["registrationProfileReused"] = True
                llm_payload["updateDrafts"] = update_drafts
                llm_payload["profileContext"] = profile_context
                yield _sse_event("status", {"message": "已沿用注册信息，生成需要确认的画像草稿"})
                yield _sse_event("done", llm_payload)
                return
        except LLMUnavailable as exc:
            yield _sse_event("error", {
                "message": "DeepSeek 流式画像抽取失败，未生成画像草稿。",
                "detail": str(exc),
                "llmStatus": {
                    "usedLLM": False,
                    "model": llm_model_name(),
                    "fallback": False,
                },
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/extract")
def extract_profile(
    payload: ProfileExtractRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    return ok(_extract_profile_payload_for_user(user_id, payload.message))


@router.post("/extract-confirm")
def extract_and_confirm_profile(
    payload: ProfileExtractRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    user_id = user_id_from_authorization(authorization)
    llm_payload = _extract_profile_payload_for_user(user_id, payload.message)
    profile_items = _confirm_profile_items(user_id, llm_payload["dimensions"])
    return ok({
        **llm_payload,
        "profileItems": profile_items,
        "confirmedDimensions": llm_payload["dimensions"],
        "saved": True,
    })


@router.post("/confirm")
def confirm_profile(payload: ProfileConfirmRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    return ok(_confirm_profile_items(user_id, payload.dimensions))
