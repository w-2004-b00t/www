from __future__ import annotations

import importlib.util
import os
from typing import Any

from ..env import load_backend_env
from .agent_protocol import AGENT_EVENT_PROTOCOL


AGENT_FRAMEWORK_ENV = "AGENT_ORCHESTRATOR"
AGENT_COLLAB_ENV = "AGENT_COLLAB_MODE"


load_backend_env()


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _selected_orchestrator() -> str:
    return os.getenv(AGENT_FRAMEWORK_ENV, "custom").strip().lower() or "custom"


def _selected_collaboration() -> str:
    return os.getenv(AGENT_COLLAB_ENV, "structured").strip().lower() or "structured"


def agent_runtime_status() -> dict[str, Any]:
    selected = _selected_orchestrator()
    collaboration = _selected_collaboration()
    langgraph_available = _module_available("langgraph")
    autogen_available = _module_available("autogen") or _module_available("autogen_agentchat")
    langgraph_active = selected == "langgraph" and langgraph_available
    autogen_active = collaboration == "autogen" and autogen_available
    return {
        "orchestrator": "langgraph" if langgraph_active else "custom",
        "requestedOrchestrator": selected,
        "langGraphAvailable": langgraph_available,
        "langGraphActive": langgraph_active,
        "collaborationMode": "autogen" if autogen_active else "structured",
        "requestedCollaborationMode": collaboration,
        "autoGenAvailable": autogen_available,
        "autoGenActive": autogen_active,
        "messageProtocol": AGENT_EVENT_PROTOCOL,
        "fallback": None if langgraph_active else "custom_sequential_workflow",
    }


def build_task_runtime_metadata() -> dict[str, Any]:
    status = agent_runtime_status()
    return {
        "framework": status["orchestrator"],
        "collaborationMode": status["collaborationMode"],
        "messageProtocol": AGENT_EVENT_PROTOCOL,
        "runtimeStatus": status,
    }


def build_execution_order(agent_steps: list[dict[str, Any]]) -> list[str]:
    """Return the agent execution order.

    When LangGraph is installed and selected, compile a minimal StateGraph to
    validate the handoff graph. If the optional runtime is unavailable, return
    the same deterministic order used by the lightweight workflow.
    """

    names = [step["name"] for step in agent_steps]
    status = agent_runtime_status()
    if not status["langGraphActive"] or not names:
        return names
    try:  # pragma: no cover - optional runtime dependency
        from langgraph.graph import END, START, StateGraph  # type: ignore

        def make_node(name: str):
            def node(runtime_state: dict[str, Any]) -> dict[str, Any]:
                visited = [*runtime_state.get("visited", []), name]
                return {**runtime_state, "visited": visited}

            return node

        graph = StateGraph(dict)
        for name in names:
            graph.add_node(name, make_node(name))
        graph.add_edge(START, names[0])
        for before, after in zip(names, names[1:]):
            graph.add_edge(before, after)
        graph.add_edge(names[-1], END)
        compiled = graph.compile()
        result = compiled.invoke({"visited": []})
        return result.get("visited") or names
    except Exception:
        return names
