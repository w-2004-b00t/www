from __future__ import annotations

import asyncio
from copy import deepcopy

from fastapi import APIRouter, Header, HTTPException
from starlette.responses import StreamingResponse

from .. import state
from ..persistence import is_database_busy_error, list_tasks as list_persisted_tasks
from ..schemas import ResourceGenerateRequest
from ..services.task_service import create_resource_task, encode_task_event, get_task_or_404, retry_agent_step
from ..utils import ok, user_id_from_authorization

router = APIRouter(prefix="/api", tags=["tasks"])


@router.post("/resources/generate")
def generate_resources(payload: ResourceGenerateRequest, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    try:
        return ok(create_resource_task(payload, user_id=user_id))
    except HTTPException:
        raise
    except Exception as exc:
        if is_database_busy_error(exc):
            raise HTTPException(
                status_code=503,
                detail="后端数据库繁忙，请稍后重试。若频繁出现，请等待当前生成任务完成后再提交。",
            ) from exc
        raise HTTPException(
            status_code=500,
            detail=f"资源生成任务创建失败：{exc.__class__.__name__}: {exc}",
        ) from exc


@router.get("/tasks/{task_id}")
def get_task(task_id: str, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    return ok(get_task_or_404(task_id, user_id=user_id))


@router.get("/tasks")
def list_tasks() -> dict:
    tasks_by_id = {task["id"]: deepcopy(task) for task in list_persisted_tasks()}
    for task in state.tasks.values():
        tasks_by_id[task["id"]] = deepcopy(task)
    tasks = sorted(
        tasks_by_id.values(),
        key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
        reverse=True,
    )
    return ok(tasks)


@router.post("/tasks/{task_id}/agents/{agent_name}/retry")
def retry_task_agent(task_id: str, agent_name: str, authorization: str | None = Header(default=None)) -> dict:
    user_id = user_id_from_authorization(authorization)
    return ok(retry_agent_step(task_id, agent_name, user_id=user_id))


@router.get("/stream/tasks/{task_id}")
async def stream_task(task_id: str) -> StreamingResponse:
    async def events():
        while True:
            task = state.tasks.get(task_id)
            if not task:
                yield 'data: {"status":"failed","message":"task not found"}\n\n'
                return
            yield encode_task_event(task)
            if task["status"] in {"success", "failed", "cancelled"}:
                return
            await asyncio.sleep(0.8)

    return StreamingResponse(events(), media_type="text/event-stream")
