from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .llm_service import llm_model_name


def blocked_detail(
    *,
    agent_name: str,
    message: str,
    code: str = "AGENT_GENERATION_BLOCKED",
    missing_requirements: list[str] | None = None,
    used_llm: bool = False,
    detail: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "agentName": agent_name,
        "missingRequirements": missing_requirements or [],
        "llmStatus": {
            "usedLLM": used_llm,
            "model": llm_model_name(),
            "fallback": False,
        },
    }
    if detail:
        payload["detail"] = detail
    return payload


def raise_blocked(
    *,
    status_code: int,
    agent_name: str,
    message: str,
    code: str = "AGENT_GENERATION_BLOCKED",
    missing_requirements: list[str] | None = None,
    used_llm: bool = False,
    detail: Any | None = None,
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail=blocked_detail(
            agent_name=agent_name,
            message=message,
            code=code,
            missing_requirements=missing_requirements,
            used_llm=used_llm,
            detail=detail,
        ),
    )


BLOCKED_GENERATION_MARKERS = (
    "fallback",
    "template",
    "rule_fallback",
    "local_kb_rag_template",
    "course_template",
)


def is_blocked_generation_mode(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and any(marker in text for marker in BLOCKED_GENERATION_MARKERS))


def is_legacy_fallback_resource(resource: dict[str, Any]) -> bool:
    metadata = resource.get("metadata") if isinstance(resource.get("metadata"), dict) else {}
    return is_blocked_generation_mode(metadata.get("generationMode")) or is_blocked_generation_mode(resource.get("generationMode"))
