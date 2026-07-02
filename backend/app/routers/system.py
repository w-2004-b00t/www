from __future__ import annotations

from fastapi import APIRouter

from ..course_config import COURSE_DESCRIPTION, COURSE_ID, COURSE_NAME
from ..services.agent_orchestrator import agent_runtime_status
from ..services.agent_state import agent_state_status
from ..services.vector_service import vector_status
from ..utils import ok

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict:
    return ok({
        "status": "ok",
        "version": "1.1.0",
        "mode": "real_data_first",
        "persistence": "sqlite",
        "retrieval": "keyword_recall_rerank",
        "profile_vectorization": vector_status(),
        "assessment": "rubric_scoring",
        "agent_retry": "single_step",
        "agent_orchestration": agent_runtime_status(),
        "agent_state": agent_state_status(),
    })


@router.get("/courses")
def courses() -> dict:
    return ok([
        {
            "id": COURSE_ID,
            "name": COURSE_NAME,
            "description": COURSE_DESCRIPTION,
            "status": "active",
        },
    ])
