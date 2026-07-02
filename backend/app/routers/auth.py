from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException

from ..demo_data import USERS, now_text
from ..persistence import load_json, save_json
from ..schemas import LoginRequest, RegisterRequest
from ..services.profile_update_service import PROFILE_IMPACTS, load_profile_items, save_profile_items
from ..services.vector_service import index_profile
from ..utils import ok, public_user, user_id_from_authorization

router = APIRouter(prefix="/api/auth", tags=["auth"])

REGISTERED_USERS_KEY = "registered_users"
ALLOWED_PORTAL_ROLES = {"student", "teacher"}


def registered_users() -> list[dict]:
    return load_json(REGISTERED_USERS_KEY, [])


def all_users() -> list[dict]:
    return [*USERS, *registered_users()]


def issue_token(user: dict) -> dict:
    return {"access_token": f"demo-token-{user['id']}", "token_type": "bearer", "user": public_user(user)}


def _registration_profile_item(dimension: str, value: str) -> dict:
    return {
        "id": f"profile_register_{uuid4().hex[:8]}",
        "dimension": dimension,
        "value": value,
        "confidence": 1.0,
        "source": "manual",
        "status": "confirmed",
        "updatedAt": "",
        "reason": "学生注册时填写并确认。",
        "impact": PROFILE_IMPACTS.get(dimension, "影响后续学习路径、资源推荐和智能辅导。"),
        "version": 1,
    }


def _sync_registration_profile(user: dict) -> None:
    if user.get("role") != "student":
        return
    seeds = [
        ("专业背景", str(user.get("major") or "").strip()),
        ("年级 / 学习阶段", str(user.get("grade") or "").strip()),
    ]
    seeds = [(dimension, value) for dimension, value in seeds if value]
    if not seeds:
        return

    profile_items = load_profile_items(user["id"])
    now = now_text()
    for dimension, value in seeds:
        existing_index = next(
            (idx for idx, item in enumerate(profile_items) if item.get("dimension") == dimension),
            -1,
        )
        existing = profile_items[existing_index] if existing_index >= 0 else {}
        item = {
            **_registration_profile_item(dimension, value),
            "id": existing.get("id") or f"profile_register_{uuid4().hex[:8]}",
            "updatedAt": now,
            "version": int(existing.get("version", 0) or 0) + 1 if existing else 1,
        }
        if existing_index >= 0:
            profile_items[existing_index] = item
        else:
            profile_items.append(item)

    save_profile_items(user["id"], profile_items)
    index_profile(profile_items, user_id=user["id"])


@router.post("/login")
def login(payload: LoginRequest) -> dict:
    user = next((item for item in all_users() if item["username"] == payload.username), None)
    if not user:
        raise HTTPException(status_code=401, detail="用户名不存在")
    if payload.role and user.get("role") != payload.role:
        raise HTTPException(status_code=403, detail="账号身份与所选职业身份不一致")
    if user.get("password") and payload.password and payload.password != user.get("password"):
        raise HTTPException(status_code=401, detail="密码不正确")
    return ok(issue_token(user))


@router.post("/register")
def register(payload: RegisterRequest) -> dict:
    if payload.role not in ALLOWED_PORTAL_ROLES:
        raise HTTPException(status_code=400, detail="注册身份仅支持学生或教师")
    if any(item["username"] == payload.username for item in all_users()):
        raise HTTPException(status_code=409, detail="用户名已存在")

    users = registered_users()
    user = {
        "id": f"user_{payload.role}_{uuid4().hex[:10]}",
        "username": payload.username,
        "password": payload.password or "123456",
        "name": payload.name,
        "role": payload.role,
    }
    if payload.major:
        user["major"] = payload.major
    if payload.grade:
        user["grade"] = payload.grade

    users.append(user)
    save_json(REGISTERED_USERS_KEY, users)
    _sync_registration_profile(user)
    return ok(issue_token(user))


@router.get("/me")
def me(authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    user = next((item for item in all_users() if item.get("id") == user_id), None)
    if not user:
        raise HTTPException(status_code=401, detail="登录状态已失效")
    return ok(public_user(user))


@router.post("/logout")
def logout() -> dict:
    return ok(True)
