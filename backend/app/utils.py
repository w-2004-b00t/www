from __future__ import annotations

from typing import Any

from .demo_data import USERS


def ok(data: Any = None) -> dict[str, Any]:
    return {"code": 0, "message": "success", "data": data}


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in user.items() if key != "password"}


def user_id_from_authorization(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        return "anonymous"
    token = authorization.split(" ", 1)[1].strip()
    for prefix in ("demo-token-", "local-token-", "token-"):
        if token.startswith(prefix):
            return token[len(prefix):]
    return "anonymous"


def is_seed_user(user_id: str) -> bool:
    return any(user.get("id") == user_id for user in USERS)


def user_scoped_key(base_key: str, user_id: str) -> str:
    return f"{base_key}::{user_id or 'anonymous'}"
