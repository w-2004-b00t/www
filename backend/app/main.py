from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from . import state  # noqa: F401 - 初始化 SQLite 与演示状态
from .services.llm_service import load_backend_env
from .services.agnes_video_service import start_agnes_worker, stop_agnes_worker

load_backend_env()

from .routers import admin, assessments, auth, courses, knowledge, learning_paths, profile, resources, system, tasks, tutor

ROUTERS = [
    system.router,
    auth.router,
    admin.router,
    profile.router,
    courses.router,
    knowledge.router,
    tasks.router,
    resources.router,
    learning_paths.router,
    tutor.router,
    assessments.router,
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    start_agnes_worker()
    try:
        yield
    finally:
        stop_agnes_worker()


def create_app() -> FastAPI:
    app = FastAPI(title="EduAgent Studio API", version="1.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:5174",
            "http://localhost:5174",
            "http://127.0.0.1:5175",
            "http://localhost:5175",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in ROUTERS:
        app.include_router(router)
    media_dir = Path(__file__).resolve().parents[1] / "data" / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")
    return app


app = create_app()


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    message = exc.detail.get("message") if isinstance(exc.detail, dict) else exc.detail
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.status_code, "message": message, "detail": exc.detail, "data": None},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": f"服务内部错误：{exc.__class__.__name__}", "data": None},
    )
