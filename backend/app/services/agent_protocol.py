from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


AGENT_EVENT_PROTOCOL = "eduagent.agent.event.v1"


class AgentRuntimeMessage(BaseModel):
    """Structured message exchanged between agent steps and the audit UI."""

    protocol: str = AGENT_EVENT_PROTOCOL
    eventId: str = Field(default_factory=lambda: f"evt_{uuid4().hex[:12]}")
    taskId: str
    agentName: str | None = None
    agentTitle: str | None = None
    eventType: str
    status: str | None = None
    progress: int | None = None
    message: str | None = None
    inputSummary: str | None = None
    tools: list[str] = Field(default_factory=list)
    outputSummary: str | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    handoff: dict[str, Any] | None = None
    downstreamImpact: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    createdAt: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


def agent_event_payload(
    task_id: str,
    agent_name: str | None,
    event_type: str,
    *,
    task: dict[str, Any] | None = None,
    step: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    step = step or {}
    message = AgentRuntimeMessage(
        taskId=task_id,
        agentName=agent_name,
        agentTitle=step.get("title"),
        eventType=event_type,
        status=(task or {}).get("status") or step.get("status"),
        progress=(task or {}).get("progress"),
        message=(task or {}).get("message"),
        inputSummary=step.get("inputSummary"),
        tools=step.get("tools") or [],
        outputSummary=step.get("outputSummary"),
        citations=step.get("citations") or [],
        handoff=step.get("handoff"),
        downstreamImpact=step.get("downstreamImpact") or step.get("affects") or [],
        payload=payload or {},
    )
    return message.model_dump()
