from __future__ import annotations

import json
import os
from typing import Any

from ..env import load_backend_env
from ..persistence import append_task_event, list_task_events, load_task, save_task


load_backend_env()


class AgentStateStore:
    """Write-through task state store.

    SQLite remains the durable source of truth. Redis is used when configured to
    mirror current task snapshots and recent event messages for realtime demos.
    """

    def __init__(self) -> None:
        self.redis_url = os.getenv("REDIS_URL", "").strip()
        self.key_prefix = os.getenv("AGENT_STATE_PREFIX", "eduagent")
        self.expire_seconds = int(os.getenv("AGENT_STATE_TTL_SECONDS", "86400"))
        self.redis_client: Any | None = None
        self.redis_error: str | None = None
        self._connect_redis()

    def _connect_redis(self) -> None:
        if not self.redis_url:
            return
        try:
            import redis  # type: ignore

            client = redis.Redis.from_url(self.redis_url, decode_responses=True)
            client.ping()
            self.redis_client = client
        except Exception as exc:  # pragma: no cover - optional runtime dependency
            self.redis_error = f"{exc.__class__.__name__}: {exc}"
            self.redis_client = None

    @property
    def redis_enabled(self) -> bool:
        return self.redis_client is not None

    def _task_key(self, task_id: str) -> str:
        return f"{self.key_prefix}:task:{task_id}"

    def _event_key(self, task_id: str) -> str:
        return f"{self.key_prefix}:task:{task_id}:events"

    def save_task(self, task: dict[str, Any]) -> None:
        save_task(task)
        if not self.redis_client:
            return
        self.redis_client.setex(
            self._task_key(task["id"]),
            self.expire_seconds,
            json.dumps(task, ensure_ascii=False),
        )

    def load_task(self, task_id: str) -> dict[str, Any] | None:
        if self.redis_client:
            raw = self.redis_client.get(self._task_key(task_id))
            if raw:
                return json.loads(raw)
        return load_task(task_id)

    def append_event(self, task_id: str, agent_name: str | None, event_type: str, payload: dict[str, Any]) -> None:
        append_task_event(task_id, agent_name, event_type, payload)
        if not self.redis_client:
            return
        event = {
            "agentName": agent_name,
            "eventType": event_type,
            "payload": payload,
        }
        key = self._event_key(task_id)
        self.redis_client.rpush(key, json.dumps(event, ensure_ascii=False))
        self.redis_client.expire(key, self.expire_seconds)

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        events = list_task_events(task_id)
        if events or not self.redis_client:
            return events
        raw_events = self.redis_client.lrange(self._event_key(task_id), 0, -1)
        return [json.loads(item) for item in raw_events]

    def status(self) -> dict[str, Any]:
        return {
            "backend": "redis+sqlite" if self.redis_enabled else "sqlite",
            "redisEnabled": self.redis_enabled,
            "redisUrlConfigured": bool(self.redis_url),
            "redisError": self.redis_error,
            "durableStore": "sqlite",
            "eventTables": ["task_store", "task_events"],
        }


agent_state_store = AgentStateStore()


def save_agent_task(task: dict[str, Any]) -> None:
    agent_state_store.save_task(task)


def load_agent_task(task_id: str) -> dict[str, Any] | None:
    return agent_state_store.load_task(task_id)


def append_agent_event(task_id: str, agent_name: str | None, event_type: str, payload: dict[str, Any]) -> None:
    agent_state_store.append_event(task_id, agent_name, event_type, payload)


def list_agent_events(task_id: str) -> list[dict[str, Any]]:
    return agent_state_store.list_events(task_id)


def agent_state_status() -> dict[str, Any]:
    return agent_state_store.status()
