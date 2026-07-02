from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import HTTPException

from ..env import load_backend_env
from ..persistence import (
    acquire_video_job_lease,
    find_video_job_by_idempotency,
    latest_video_job,
    list_runnable_video_jobs,
    load_json,
    load_video_job,
    release_video_job_lease,
    save_video_job,
)
from .local_open_video_service import render_hybrid_video, verify_mp4


load_backend_env()

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MEDIA_DIR = BACKEND_ROOT / "data" / "media"
VIDEO_DIR = MEDIA_DIR / "videos"
CLIP_DIR = MEDIA_DIR / "agnes-clips"
for directory in (VIDEO_DIR, CLIP_DIR):
    directory.mkdir(parents=True, exist_ok=True)

LEGACY_JOB_STORE_KEY = "local_open_video_jobs_v1"
PROVIDER_NAME = "Agnes AI + HyperFrames"
GENERATOR_NAME = "agnes-video-v2.0+hyperframes-cli"
DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"
DEFAULT_MODEL_NAME = "agnes-video-v2.0"
SOURCE_TYPE = "hybrid_video_generation"
GENERATION_MODE = "deepseek_agnes_clips_hyperframes_composition"
KNOWLEDGE_VIDEO_SCHEMA_VERSION = "knowledge_video_v2"
LEGACY_VIDEO_SCHEMA_VERSION = "legacy_storyboard_video"
GENERATION_PROFILE = "hybrid-180s-v1"
TARGET_WIDTH = 1152
TARGET_HEIGHT = 768
TARGET_FPS = 24
TARGET_NUM_FRAMES = 121
DEFAULT_HYBRID_RENDER_PROFILE = {
    "width": 1152,
    "height": 768,
    "fps": 24,
    "durationSeconds": 180,
    "timeoutSeconds": 900,
}
AGNES_CLIP_RENDER_PROFILE = {
    "width": 960,
    "height": 540,
    "fps": 15,
    "durationSeconds": 8,
    "timeoutSeconds": 300,
}
MIN_VALID_MP4_BYTES = 64 * 1024
POLL_INTERVAL_SECONDS = 10
MAX_TRANSIENT_RETRIES = 12
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "orphaned"}
WORKING_STATUSES = {"queued", "submitting", "rendering", "retry_wait", "downloading", "validating", "composing"}

_HTTP_CLIENT: httpx.Client | None = None
_HTTP_CLIENT_LOCK = threading.Lock()
_WORKER_THREAD: threading.Thread | None = None
_WORKER_STOP = threading.Event()
_WORKER_WAKE = threading.Event()
_WORKER_LOCK = threading.Lock()
_RUNNING_JOBS: set[str] = set()
_RUNNING_JOBS_LOCK = threading.Lock()
_WORKER_OWNER = f"agnes-worker-{uuid4().hex[:12]}"


class AgnesError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def validate_agnes_video_configuration() -> None:
    if not _api_key():
        raise HTTPException(status_code=503, detail="AGNES_API_KEY is missing，无法调用 Agnes AI 视频生成服务。")


def start_agnes_worker() -> None:
    global _WORKER_THREAD
    with _WORKER_LOCK:
        if _WORKER_THREAD and _WORKER_THREAD.is_alive():
            return
        _migrate_legacy_jobs()
        _WORKER_STOP.clear()
        _WORKER_THREAD = threading.Thread(target=_worker_loop, name="agnes-video-worker", daemon=True)
        _WORKER_THREAD.start()
        _WORKER_WAKE.set()


def stop_agnes_worker() -> None:
    _WORKER_STOP.set()
    _WORKER_WAKE.set()
    thread = _WORKER_THREAD
    if thread and thread.is_alive():
        thread.join(timeout=5)
    with _HTTP_CLIENT_LOCK:
        global _HTTP_CLIENT
        if _HTTP_CLIENT is not None:
            _HTTP_CLIENT.close()
            _HTTP_CLIENT = None


def wake_agnes_worker() -> None:
    _WORKER_WAKE.set()


def start_agnes_video_job(
    *,
    resource_id: str,
    user_id: str,
    title: str,
    topic: str,
    scenes: list[dict[str, Any]],
    citations: list[dict[str, Any]] | None = None,
    personalization: dict[str, Any] | None = None,
    source_type: str | None = None,
    generation_mode: str | None = None,
    llm_status: dict[str, Any] | None = None,
    agent_trace: list[str] | None = None,
    production_notes: list[str] | None = None,
    render_mode: str = "full_hybrid",
    render_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_agnes_video_configuration()
    citations = citations or []
    profile = _render_profile(render_mode, render_profile)
    content_hash = _content_hash(f"{render_mode}:{topic}", scenes, citations)
    idempotency_key = _idempotency_key(user_id, resource_id, content_hash)
    existing = find_video_job_by_idempotency(idempotency_key)
    if existing:
        _normalize_public_state(existing)
    if existing and existing.get("status") in WORKING_STATUSES | {"completed"} and not _is_preview_fallback(existing):
        existing["reuseReason"] = "running_job" if existing.get("status") != "completed" else "completed_video"
        return _public_job(existing)

    job_id = f"agnes_{uuid4().hex[:10]}"
    now = _now()
    job = {
        "jobId": job_id,
        "generationAttemptId": job_id,
        "resourceId": resource_id,
        "userId": user_id,
        "status": "queued",
        "phase": "queued",
        "schemaVersion": KNOWLEDGE_VIDEO_SCHEMA_VERSION,
        "generationProfile": GENERATION_PROFILE,
        "idempotencyKey": idempotency_key,
        "contentHash": content_hash,
        "sourceCitationIds": _source_citation_ids(citations),
        "isCurrentVideo": False,
        "canReusePreviousVideo": False,
        "provider": PROVIDER_NAME,
        "providerTaskId": None,
        "providerVideoId": None,
        "providerStatus": "local_queued",
        "providerProgress": 0,
        "providerResponseSummary": None,
        "title": title,
        "topic": topic,
        "generator": GENERATOR_NAME,
        "modelName": _model_name(),
        "sourceType": source_type or SOURCE_TYPE,
        "generationMode": generation_mode or GENERATION_MODE,
        "renderMode": render_mode,
        "renderProfile": profile,
        "visualQuality": "script_like" if render_mode == "agnes_clip" else "hybrid_video",
        "storyboardLeakageScore": _storyboard_leakage_score(scenes),
        "compositionStartedAt": None,
        "lastHeartbeatAt": now,
        "compositionTimeoutSeconds": profile["timeoutSeconds"],
        "compositionStage": "queued",
        "llmStatus": llm_status or {},
        "agentTrace": [*(agent_trace or []), "已创建可恢复的视频任务"],
        "productionNotes": [
            *(production_notes or []),
            "Agnes AI 生成关键动态片段，HyperFrames 与 FFmpeg 合成并校验 3 分钟教学 MP4。",
        ],
        "prompt": _build_agnes_prompt(title, topic, scenes, citations, personalization or {}),
        "remoteTask": None,
        "remoteVideoUrl": None,
        "agnesClipUrl": None,
        "agnesClipPath": None,
        "downloadedAt": None,
        "pollCount": 0,
        "transientErrorCount": 0,
        "retryCount": 0,
        "retryable": True,
        "nextRetryAt": now,
        "lastProviderContactAt": None,
        "lastTransientError": None,
        "stageTimings": {},
        "segmentProgress": {"agnes": 0, "composition": 0, "validation": 0},
        "ffmpegNormalized": False,
        "videoUrl": None,
        "videoMimeType": "video/mp4",
        "videoDurationSeconds": None,
        "progress": 5,
        "stageMessage": "教学分镜已就绪，等待提交 Agnes AI 关键片段任务。",
        "error": None,
        "errorCode": None,
        "errorDetail": None,
        "renderLogTail": [],
        "scenes": scenes,
        "citations": citations,
        "personalization": personalization or {},
        "generatedAt": None,
        "createdAt": now,
        "updatedAt": now,
        "startedAt": None,
        "finishedAt": None,
        "reuseReason": "new_job",
    }
    _save_job(job)
    _set_latest_attempt(resource_id, user_id, job_id)
    wake_agnes_worker()
    return _public_job(job)


def render_agnes_video_job(job_id: str) -> None:
    """Compatibility entry point used by older callers and tests."""
    _process_job_guarded(job_id)


def latest_agnes_video_job(resource_id: str, user_id: str) -> dict[str, Any] | None:
    job = latest_video_job(resource_id, user_id)
    if not job:
        _migrate_legacy_jobs()
        job = latest_video_job(resource_id, user_id)
    if job:
        _normalize_public_state(job)
    return _public_job(job) if job else None


def refresh_agnes_video_job(job_id: str, *, user_id: str | None = None) -> dict[str, Any]:
    job = _load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="未找到 Agnes AI 视频生成任务。")
    if user_id and job.get("userId") not in {user_id, "anonymous"}:
        raise HTTPException(status_code=403, detail="无权查看该 Agnes AI 视频生成任务。")
    _normalize_public_state(job)
    return _public_job(job)


def retry_agnes_video_job(job_id: str, *, user_id: str | None = None) -> dict[str, Any]:
    job = _load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="未找到 Agnes AI 视频生成任务。")
    if user_id and job.get("userId") not in {user_id, "anonymous"}:
        raise HTTPException(status_code=403, detail="无权重试该 Agnes AI 视频生成任务。")
    if job.get("status") == "completed" and not _is_preview_fallback(job):
        return _public_job(job)
    if _is_preview_fallback(job):
        job["providerTaskId"] = None
        job["providerVideoId"] = None
        job["providerStatus"] = "local_queued"
        job["providerProgress"] = 0
        job["remoteTask"] = None
        job["remoteVideoUrl"] = None
        job["agnesClipUrl"] = None
        job["agnesClipPath"] = None
        job["downloadedAt"] = None
        job["pollCount"] = 0
        job["segmentProgress"] = {"agnes": 0, "composition": 0, "validation": 0}
        job["fallbackVideoUrl"] = None
        job["fallbackReason"] = None
        job["compositionWarning"] = None
        job["videoUrl"] = None
        job["videoDurationSeconds"] = None
        job["ffmpegNormalized"] = False
    job["status"] = "retry_wait"
    job["phase"] = _retry_phase(job)
    job["retryable"] = True
    job["nextRetryAt"] = _now()
    job["finishedAt"] = None
    job["error"] = None
    job["errorCode"] = None
    job["errorDetail"] = None
    job["stageMessage"] = "已安排从当前阶段自动重试。"
    job["updatedAt"] = _now()
    _save_job(job)
    wake_agnes_worker()
    return _public_job(job)


def record_failed_agnes_video_attempt(
    *,
    resource_id: str,
    user_id: str,
    title: str,
    topic: str,
    error: str,
    error_code: str,
    citations: list[dict[str, Any]] | None = None,
    personalization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    job_id = f"agnes_{uuid4().hex[:10]}"
    now = _now()
    job = {
        "jobId": job_id,
        "generationAttemptId": job_id,
        "resourceId": resource_id,
        "userId": user_id,
        "status": "failed",
        "phase": "failed",
        "schemaVersion": KNOWLEDGE_VIDEO_SCHEMA_VERSION,
        "generationProfile": GENERATION_PROFILE,
        "idempotencyKey": None,
        "contentHash": _content_hash(topic, [], citations or []),
        "sourceCitationIds": _source_citation_ids(citations or []),
        "isCurrentVideo": False,
        "canReusePreviousVideo": False,
        "provider": PROVIDER_NAME,
        "providerTaskId": None,
        "providerVideoId": None,
        "providerStatus": "failed",
        "title": title,
        "topic": topic,
        "generator": GENERATOR_NAME,
        "modelName": _model_name(),
        "sourceType": SOURCE_TYPE,
        "generationMode": GENERATION_MODE,
        "progress": 100,
        "retryable": False,
        "retryCount": 0,
        "nextRetryAt": None,
        "stageTimings": {},
        "segmentProgress": {},
        "stageMessage": "视频生成启动失败。",
        "error": error,
        "errorCode": error_code,
        "errorDetail": error,
        "renderLogTail": [line.strip() for line in str(error).splitlines() if line.strip()][-40:],
        "scenes": [],
        "citations": citations or [],
        "personalization": personalization or {},
        "ffmpegNormalized": False,
        "videoUrl": None,
        "videoMimeType": "video/mp4",
        "videoDurationSeconds": None,
        "createdAt": now,
        "updatedAt": now,
        "startedAt": None,
        "finishedAt": now,
    }
    _save_job(job)
    return _public_job(job)


def _worker_loop() -> None:
    while not _WORKER_STOP.is_set():
        jobs = list_runnable_video_jobs(limit=5)
        if not jobs:
            _WORKER_WAKE.wait(timeout=2)
            _WORKER_WAKE.clear()
            continue
        for job in jobs:
            if _WORKER_STOP.is_set():
                break
            job_id = str(job["jobId"])
            if not acquire_video_job_lease(job_id, _WORKER_OWNER):
                continue
            try:
                _process_job_guarded(job_id)
            finally:
                release_video_job_lease(job_id, _WORKER_OWNER)


def _process_job_guarded(job_id: str) -> None:
    with _RUNNING_JOBS_LOCK:
        if job_id in _RUNNING_JOBS:
            return
        _RUNNING_JOBS.add(job_id)
    try:
        _process_job(job_id)
    finally:
        with _RUNNING_JOBS_LOCK:
            _RUNNING_JOBS.discard(job_id)


def _process_job(job_id: str) -> None:
    job = _load_job(job_id)
    if not job or job.get("status") in TERMINAL_STATUSES:
        return
    try:
        if not job.get("providerTaskId"):
            _submit_agnes_task_for_job(job_id)
            return
        if not job.get("remoteVideoUrl"):
            _poll_remote_job(job_id)
            return
        if not job.get("agnesClipPath"):
            _download_remote_clip(job_id)
            return
        _compose_hybrid_job(job_id)
    except AgnesError as exc:
        if exc.retryable:
            _schedule_retry(job_id, exc)
        else:
            _fail_job(job_id, exc)
    except Exception as exc:
        _fail_job(job_id, AgnesError(_classify_unexpected_error(exc), str(exc) or exc.__class__.__name__, retryable=False))


def _submit_agnes_task_for_job(job_id: str) -> str:
    job = _load_job(job_id)
    if not job:
        raise AgnesError("AGNES_LOCAL_JOB_MISSING", "Agnes AI local job is missing", retryable=False)
    prompt = str(job.get("prompt") or "").strip()
    if not prompt:
        raise AgnesError("AGNES_PROMPT_EMPTY", "Agnes AI prompt is empty", retryable=False)
    _update_job_stage(job_id, "submitting", 8, "正在提交 Agnes AI 关键动态片段任务。", started=True)
    profile = _render_profile(str(job.get("renderMode") or "full_hybrid"), job.get("renderProfile") if isinstance(job.get("renderProfile"), dict) else None)
    remote = _create_agnes_video_task(prompt, profile)
    task_id = _task_id(remote)
    video_id = _video_id(remote)
    if not task_id and not video_id:
        raise AgnesError("AGNES_TASK_ID_MISSING", "Agnes AI 创建任务成功但未返回任务 ID。", retryable=False)
    job = _load_job(job_id) or job
    job["providerTaskId"] = task_id or video_id
    job["providerVideoId"] = video_id or task_id
    job["providerStatus"] = _remote_status(remote) if _has_remote_status(remote) else "queued"
    job["providerProgress"] = _remote_progress(remote)
    job["remoteTask"] = remote
    job["providerResponseSummary"] = _response_summary(remote)
    job["status"] = "queued"
    job["phase"] = "queued"
    job["progress"] = 12
    job.setdefault("segmentProgress", {})["agnes"] = max(1, job["providerProgress"])
    job["stageMessage"] = "Agnes AI 关键片段任务已创建，等待生成。"
    job["lastProviderContactAt"] = _now()
    job["nextRetryAt"] = _after(POLL_INTERVAL_SECONDS)
    job["updatedAt"] = _now()
    trace = job.get("agentTrace") if isinstance(job.get("agentTrace"), list) else []
    if "Agnes AI 已返回远端任务 ID" not in trace:
        job["agentTrace"] = [*trace, "Agnes AI 已返回远端任务 ID"]
    _save_job(job)
    return str(job["providerTaskId"])


def _poll_remote_job(job_id: str) -> None:
    job = _load_job(job_id)
    if not job:
        return
    remote = _get_agnes_video_task(
        str(job.get("providerTaskId") or ""),
        str(job.get("providerVideoId") or ""),
    )
    status = _remote_status(remote)
    video_id = _video_id(remote) or str(job.get("providerVideoId") or "")
    video_url = _remote_video_url(remote)
    job["remoteTask"] = remote
    job["providerVideoId"] = video_id or None
    job["providerStatus"] = status
    job["providerProgress"] = _remote_progress(remote)
    job["providerResponseSummary"] = _response_summary(remote)
    job["pollCount"] = int(job.get("pollCount") or 0) + 1
    job["lastProviderContactAt"] = _now()
    job["transientErrorCount"] = 0
    job["lastTransientError"] = None
    job["segmentProgress"]["agnes"] = job["providerProgress"]
    job["updatedAt"] = _now()
    if status in {"failed", "error", "cancelled", "canceled"}:
        raise AgnesError("AGNES_REMOTE_FAILED", _remote_error(remote) or "Agnes AI 视频生成失败。", retryable=False)
    if status in {"completed", "succeeded", "success", "done"}:
        if not video_url:
            raise AgnesError("AGNES_RESULT_URL_MISSING", "Agnes AI 已完成，但响应中没有视频地址。", retryable=True)
        job["remoteVideoUrl"] = video_url
        job["agnesClipUrl"] = video_url
        job["status"] = "downloading"
        job["phase"] = "downloading"
        job["progress"] = 55
        job["stageMessage"] = "Agnes AI 关键片段已生成，准备可靠下载。"
        job["nextRetryAt"] = _now()
        _save_job(job)
        return
    job["status"] = "rendering"
    job["phase"] = "rendering"
    job["progress"] = min(52, 15 + int(job["providerProgress"] * 0.35))
    job["stageMessage"] = "Agnes AI 正在生成关键动态片段。"
    job["nextRetryAt"] = _after(POLL_INTERVAL_SECONDS)
    _save_job(job)


def _download_remote_clip(job_id: str) -> None:
    job = _load_job(job_id)
    if not job:
        return
    remote_url = str(job.get("remoteVideoUrl") or "")
    if not remote_url:
        raise AgnesError("AGNES_RESULT_URL_MISSING", "Agnes AI 视频地址为空。", retryable=True)
    _update_job_stage(job_id, "downloading", 58, "正在下载 Agnes AI 关键片段。")
    output_path = CLIP_DIR / f"{_safe_name(job_id)}.mp4"
    try:
        _download_video(remote_url, output_path)
    except AgnesError as exc:
        if exc.code != "VIDEO_DOWNLOAD_UNAUTHORIZED":
            raise
        refreshed_url = _refresh_remote_video_url(job_id)
        if not refreshed_url or refreshed_url == remote_url:
            raise
        _download_video(refreshed_url, output_path)
    _update_job_stage(job_id, "validating", 64, "正在使用 FFprobe 校验 Agnes AI 片段。")
    try:
        clip_probe = verify_mp4(output_path)
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        raise AgnesError("VIDEO_VALIDATION_FAILED", f"Agnes 片段校验失败：{exc}", retryable=True) from exc
    job = _load_job(job_id) or job
    job["agnesClipPath"] = str(output_path)
    job["downloadedAt"] = _now()
    job["clipProbe"] = clip_probe
    job["segmentProgress"]["agnes"] = 100
    job["segmentProgress"]["validation"] = 35
    if job.get("renderMode") == "agnes_clip":
        now = _now()
        job["videoUrl"] = f"/media/agnes-clips/{output_path.name}"
        job["videoDurationSeconds"] = clip_probe.get("duration")
        job["visualQuality"] = "script_like"
        job["storyboardLeakageScore"] = _storyboard_leakage_score(job.get("scenes") or [])
        job["ffmpegNormalized"] = True
        job["status"] = "completed"
        job["phase"] = "completed"
        job["providerStatus"] = "completed"
        job["progress"] = 100
        job["segmentProgress"] = {"agnes": 100, "composition": 0, "validation": 100}
        job["stageMessage"] = "Agnes AI 片段已下载并通过 FFprobe 校验，未进入 HyperFrames 合成。"
        job["compositionStage"] = "agnes_clip_completed"
        job["lastHeartbeatAt"] = now
        job["retryable"] = False
        job["nextRetryAt"] = None
        job["generatedAt"] = now
        job["isCurrentVideo"] = True
        job["finishedAt"] = now
        job["updatedAt"] = now
        _finish_stage_timing(job)
        _save_job(job)
        return
    job["status"] = "composing"
    job["phase"] = "composing"
    job["progress"] = 68
    job["stageMessage"] = "关键片段校验通过，准备合成 3 分钟教学视频。"
    job["nextRetryAt"] = _now()
    job["updatedAt"] = _now()
    _save_job(job)


def _compose_hybrid_job(job_id: str) -> None:
    job = _load_job(job_id)
    if not job:
        return
    clip_path = Path(str(job.get("agnesClipPath") or ""))
    if not clip_path.exists():
        raise AgnesError("VIDEO_DOWNLOAD_FAILED", "Agnes 关键片段文件不存在，需要重新下载。", retryable=True)
    _update_job_stage(job_id, "composing", 70, "HyperFrames 正在生成结构图、操作过程、复杂度与练习画面。")
    profile = _render_profile(str(job.get("renderMode") or "full_hybrid"), job.get("renderProfile") if isinstance(job.get("renderProfile"), dict) else None)
    output_path = VIDEO_DIR / f"{_safe_name(job['resourceId'])}_{_safe_name(job_id)}.mp4"

    def progress(percent: int, message: str, composition_stage: str | None = None) -> None:
        current = _load_job(job_id)
        if not current:
            return
        current["status"] = "composing" if percent < 95 else "validating"
        current["phase"] = current["status"]
        current["progress"] = max(70, min(98, percent))
        current["stageMessage"] = message
        current["segmentProgress"]["composition"] = max(0, min(100, int((percent - 70) / 0.25)))
        current["lastHeartbeatAt"] = _now()
        if composition_stage:
            current["compositionStage"] = composition_stage
        current["updatedAt"] = _now()
        _save_job(current)

    try:
        probe, logs = render_hybrid_video(job, clip_path, output_path, progress_callback=progress, render_profile=profile)
    except Exception as exc:
        fallback = _complete_with_agnes_clip_fallback(job_id, clip_path, exc)
        if fallback:
            return
        raise AgnesError("COMPOSITION_FAILED", f"3 分钟教学视频合成失败：{exc}", retryable=True) from exc
    job = _load_job(job_id) or job
    now = _now()
    job["videoUrl"] = f"/media/videos/{output_path.name}"
    job["videoDurationSeconds"] = probe["duration"]
    job["visualQuality"] = "hybrid_video"
    job["storyboardLeakageScore"] = _storyboard_leakage_score(job.get("scenes") or [])
    job["ffmpegNormalized"] = True
    job["status"] = "completed"
    job["phase"] = "completed"
    job["providerStatus"] = "completed"
    job["progress"] = 100
    job["segmentProgress"] = {"agnes": 100, "composition": 100, "validation": 100}
    job["stageMessage"] = "Agnes 关键片段与本地教学动画已合成为 3 分钟 MP4，并通过 FFprobe 校验。"
    job["compositionStage"] = "completed"
    job["lastHeartbeatAt"] = now
    job["renderLogTail"] = [str(line) for line in logs][-40:]
    job["error"] = None
    job["errorCode"] = None
    job["errorDetail"] = None
    job["retryable"] = False
    job["nextRetryAt"] = None
    job["generatedAt"] = now
    job["isCurrentVideo"] = True
    job["finishedAt"] = now
    job["updatedAt"] = now
    _finish_stage_timing(job)
    _save_job(job)


def _complete_with_agnes_clip_fallback(job_id: str, clip_path: Path, exc: Exception) -> bool:
    try:
        clip_probe = verify_mp4(clip_path)
    except Exception:
        return False
    job = _load_job(job_id)
    if not job:
        return False
    now = _now()
    warning = "已生成可播放片段，完整教学合成失败，可重试生成完整版。"
    detail = str(exc) or exc.__class__.__name__
    job["videoUrl"] = f"/media/agnes-clips/{clip_path.name}"
    job["fallbackVideoUrl"] = job["videoUrl"]
    job["fallbackReason"] = "composition_failed"
    job["compositionWarning"] = warning
    job["videoDurationSeconds"] = clip_probe.get("duration")
    job["visualQuality"] = "script_like"
    job["storyboardLeakageScore"] = _storyboard_leakage_score(job.get("scenes") or [])
    job["ffmpegNormalized"] = True
    job["status"] = "completed"
    job["phase"] = "completed"
    job["providerStatus"] = "completed"
    job["progress"] = 100
    job["segmentProgress"] = {"agnes": 100, "composition": 0, "validation": 100}
    job["stageMessage"] = warning
    job["compositionStage"] = "agnes_clip_fallback"
    job["lastHeartbeatAt"] = now
    job["error"] = detail
    job["errorCode"] = "COMPOSITION_FAILED"
    job["errorDetail"] = detail
    existing_logs = job.get("renderLogTail") if isinstance(job.get("renderLogTail"), list) else []
    job["renderLogTail"] = [
        *[str(line) for line in existing_logs],
        f"COMPOSITION_FAILED: {detail}",
        f"Fallback video URL: {job['videoUrl']}",
    ][-40:]
    job["retryable"] = True
    job["nextRetryAt"] = None
    job["generatedAt"] = now
    job["isCurrentVideo"] = True
    job["finishedAt"] = now
    job["updatedAt"] = now
    _finish_stage_timing(job)
    _save_job(job)
    return True


def _create_agnes_video_task(prompt: str, render_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = render_profile or AGNES_CLIP_RENDER_PROFILE
    num_frames = max(1, min(TARGET_NUM_FRAMES, int(profile["fps"]) * min(int(profile["durationSeconds"]), 8)))
    return _request_json(
        f"{_base_url()}/videos",
        method="POST",
        payload={
            "model": _model_name(),
            "prompt": prompt,
            "height": int(profile["height"]),
            "width": int(profile["width"]),
            "num_frames": num_frames,
            "frame_rate": int(profile["fps"]),
        },
        timeout=float(os.getenv("AGNES_CREATE_TIMEOUT_SECONDS", "120")),
    )


def _get_agnes_video_task(task_id: str, video_id: str = "") -> dict[str, Any]:
    if video_id and not video_id.startswith("task_"):
        try:
            return _request_json(
                f"{_base_url()}/agnesapi?video_id={quote(video_id)}",
                method="GET",
                timeout=45,
            )
        except AgnesError as exc:
            if exc.code not in {"AGNES_HTTP_400", "AGNES_HTTP_404", "AGNES_HTTP_405"}:
                raise
    if not task_id:
        raise AgnesError("AGNES_TASK_ID_MISSING", "Agnes AI task id is empty", retryable=False)
    return _request_json(f"{_base_url()}/videos/{quote(task_id)}", method="GET", timeout=45)


def _request_json(
    url: str,
    *,
    method: str,
    payload: dict[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    response = _request_with_retries(
        method,
        url,
        json=payload,
        timeout=httpx.Timeout(timeout, connect=min(15.0, timeout)),
        attempts=4,
    )
    try:
        result = response.json()
    except ValueError as exc:
        raise AgnesError("AGNES_INVALID_JSON", f"Agnes AI returned invalid JSON: {response.text[:300]}", retryable=True) from exc
    if not isinstance(result, dict):
        raise AgnesError("AGNES_INVALID_JSON", "Agnes AI response root is not an object", retryable=True)
    return result


def _request_with_retries(
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    timeout: httpx.Timeout,
    attempts: int,
) -> httpx.Response:
    last_error: AgnesError | None = None
    for attempt in range(attempts):
        try:
            response = _client().request(method, url, json=json, timeout=timeout)
            if response.status_code in {429, 500, 502, 503, 504}:
                code = "AGNES_RATE_LIMITED" if response.status_code == 429 else f"AGNES_HTTP_{response.status_code}"
                raise AgnesError(code, f"Agnes AI HTTP {response.status_code}: {response.text[:500]}", retryable=True)
            if response.status_code >= 400:
                raise AgnesError(
                    f"AGNES_HTTP_{response.status_code}",
                    f"Agnes AI HTTP {response.status_code}: {response.text[:500]}",
                    retryable=False,
                )
            return response
        except AgnesError as exc:
            last_error = exc
        except httpx.TimeoutException as exc:
            last_error = AgnesError("AGNES_TIMEOUT", f"Agnes AI timeout: {exc}", retryable=True)
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.NetworkError) as exc:
            text = str(exc)
            code = "AGNES_SSL_EOF" if "EOF" in text.upper() or "SSL" in text.upper() else "AGNES_NETWORK_ERROR"
            last_error = AgnesError(code, f"Agnes AI network error: {text}", retryable=True)
        if last_error and (not last_error.retryable or attempt == attempts - 1):
            raise last_error
        time.sleep(_backoff_seconds(attempt))
    raise last_error or AgnesError("AGNES_NETWORK_ERROR", "Agnes AI request failed", retryable=True)


def _refresh_remote_video_url(job_id: str) -> str:
    job = _load_job(job_id)
    if not job:
        return ""
    remote = _get_agnes_video_task(
        str(job.get("providerTaskId") or ""),
        str(job.get("providerVideoId") or ""),
    )
    refreshed_url = _remote_video_url(remote)
    if not refreshed_url:
        return ""
    job["remoteTask"] = remote
    job["remoteVideoUrl"] = refreshed_url
    job["agnesClipUrl"] = refreshed_url
    job["providerStatus"] = _remote_status(remote)
    job["providerProgress"] = _remote_progress(remote)
    job["providerResponseSummary"] = _response_summary(remote)
    job["lastProviderContactAt"] = _now()
    job["updatedAt"] = _now()
    _save_job(job)
    return refreshed_url


def _download_headers(include_auth: bool) -> dict[str, str]:
    headers = {
        "Accept": "video/mp4,video/*,*/*",
        "User-Agent": "EduAgentStudio/2.0",
    }
    if include_auth and _api_key():
        headers["Authorization"] = f"Bearer {_api_key()}"
    return headers


def _download_video(remote_url: str, output_path: Path) -> None:
    tmp_path = output_path.with_suffix(".mp4.part")
    tmp_path.unlink(missing_ok=True)
    last_error: AgnesError | None = None
    for attempt in range(4):
        for include_auth in (False, True):
            try:
                with httpx.stream(
                    "GET",
                    remote_url,
                    headers=_download_headers(include_auth),
                    timeout=httpx.Timeout(240, connect=20),
                    follow_redirects=True,
                ) as response:
                    if response.status_code in {401, 403}:
                        raise AgnesError(
                            "VIDEO_DOWNLOAD_UNAUTHORIZED",
                            f"视频下载 HTTP {response.status_code}",
                            retryable=True,
                        )
                    if response.status_code in {429, 500, 502, 503, 504}:
                        raise AgnesError("VIDEO_DOWNLOAD_FAILED", f"视频下载 HTTP {response.status_code}", retryable=True)
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    if content_type and not any(token in content_type for token in ("video", "octet-stream", "application/mp4")):
                        raise AgnesError("VIDEO_DOWNLOAD_FAILED", f"视频下载返回了异常 Content-Type：{content_type}", retryable=True)
                    with tmp_path.open("wb") as handle:
                        for chunk in response.iter_bytes(1024 * 1024):
                            if chunk:
                                handle.write(chunk)
                if tmp_path.stat().st_size < MIN_VALID_MP4_BYTES:
                    raise AgnesError("VIDEO_DOWNLOAD_FAILED", "Agnes AI 返回的视频文件过小。", retryable=True)
                with tmp_path.open("rb") as handle:
                    head = handle.read(64)
                if b"ftyp" not in head:
                    raise AgnesError("VIDEO_VALIDATION_FAILED", "下载文件不是有效 MP4 容器。", retryable=True)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp_path, output_path)
                return
            except AgnesError as exc:
                last_error = exc
                tmp_path.unlink(missing_ok=True)
                if exc.code == "VIDEO_DOWNLOAD_UNAUTHORIZED":
                    continue
                break
            except (httpx.HTTPError, OSError) as exc:
                last_error = AgnesError("VIDEO_DOWNLOAD_FAILED", f"视频下载失败：{exc}", retryable=True)
                break
        tmp_path.unlink(missing_ok=True)
        if attempt < 3:
            time.sleep(_backoff_seconds(attempt))
    raise last_error or AgnesError("VIDEO_DOWNLOAD_FAILED", "视频下载失败", retryable=True)


def _schedule_retry(job_id: str, exc: AgnesError) -> None:
    job = _load_job(job_id)
    if not job:
        return
    retries = int(job.get("retryCount") or 0) + 1
    if retries > MAX_TRANSIENT_RETRIES:
        _fail_job(job_id, AgnesError(exc.code, f"{exc}；已超过自动重试上限。", retryable=False))
        return
    delay = min(300, 5 * (2 ** min(retries - 1, 5))) + random.randint(0, 5)
    job["status"] = "retry_wait"
    job["phase"] = _retry_phase(job)
    job["retryable"] = True
    job["retryCount"] = retries
    job["transientErrorCount"] = int(job.get("transientErrorCount") or 0) + 1
    job["lastTransientError"] = str(exc)
    job["errorCode"] = exc.code
    job["errorDetail"] = str(exc)
    job["nextRetryAt"] = _after(delay)
    job["stageMessage"] = f"临时网络或供应商错误，系统将在约 {delay} 秒后自动重试当前阶段。"
    job["renderLogTail"] = [*list(job.get("renderLogTail") or []), f"{exc.code}: {exc}"][-40:]
    job["updatedAt"] = _now()
    _save_job(job)


def _fail_job(job_id: str, exc: AgnesError) -> None:
    job = _load_job(job_id)
    if not job:
        return
    detail = str(exc) or exc.__class__.__name__
    job["status"] = "failed"
    job["phase"] = "failed"
    job["providerStatus"] = "failed"
    job["progress"] = 100
    job["stageMessage"] = "视频生成失败，未产出可验证的 3 分钟 MP4。"
    job["error"] = detail
    job["errorCode"] = exc.code
    job["errorDetail"] = detail
    job["retryable"] = exc.retryable
    job["nextRetryAt"] = None
    job["isCurrentVideo"] = False
    job["videoUrl"] = None
    job["videoDurationSeconds"] = None
    job["ffmpegNormalized"] = False
    job["renderLogTail"] = [*list(job.get("renderLogTail") or []), f"{exc.code}: {detail}"][-40:]
    job["finishedAt"] = _now()
    job["updatedAt"] = _now()
    _finish_stage_timing(job)
    _save_job(job)


def _update_job_stage(
    job_id: str,
    status: str,
    progress: int,
    message: str,
    *,
    started: bool = False,
    composition_stage: str | None = None,
) -> None:
    job = _load_job(job_id)
    if not job:
        return
    previous_phase = str(job.get("phase") or job.get("status") or "")
    if previous_phase != status:
        _record_stage_transition(job, previous_phase, status)
    job["status"] = status
    job["phase"] = status
    job["progress"] = max(0, min(100, int(progress)))
    job["stageMessage"] = message
    job["updatedAt"] = _now()
    job["lastHeartbeatAt"] = _now()
    if composition_stage:
        job["compositionStage"] = composition_stage
    if status in {"composing", "validating"} and not job.get("compositionStartedAt"):
        job["compositionStartedAt"] = _now()
    if started and not job.get("startedAt"):
        job["startedAt"] = _now()
    _save_job(job)


def _load_job(job_id: str) -> dict[str, Any] | None:
    job = load_video_job(job_id)
    if job:
        return job
    legacy = load_json(LEGACY_JOB_STORE_KEY, {}).get(job_id)
    if isinstance(legacy, dict):
        migrated = _normalize_legacy_job(legacy)
        _save_job(migrated)
        return migrated
    return None


def _save_job(job: dict[str, Any]) -> None:
    save_video_job(job)


def _migrate_legacy_jobs() -> None:
    jobs = load_json(LEGACY_JOB_STORE_KEY, {})
    if not isinstance(jobs, dict):
        return
    for raw in jobs.values():
        if not isinstance(raw, dict) or not raw.get("jobId"):
            continue
        if load_video_job(str(raw["jobId"])):
            continue
        save_video_job(_normalize_legacy_job(raw))


def _normalize_legacy_job(raw: dict[str, Any]) -> dict[str, Any]:
    job = dict(raw)
    task_id = str(job.get("providerTaskId") or job.get("providerVideoId") or "")
    remote = job.get("remoteTask") if isinstance(job.get("remoteTask"), dict) else {}
    video_id = _video_id(remote)
    video_url = _remote_video_url(remote)
    job["providerTaskId"] = task_id or None
    job["providerVideoId"] = video_id or None
    job["phase"] = job.get("phase") or job.get("status") or "queued"
    job["generationProfile"] = job.get("generationProfile") or GENERATION_PROFILE
    job["idempotencyKey"] = job.get("idempotencyKey") or _idempotency_key(
        str(job.get("userId") or "anonymous"),
        str(job.get("resourceId") or ""),
        str(job.get("contentHash") or ""),
    )
    job["retryable"] = job.get("status") != "completed"
    job["retryCount"] = int(job.get("retryCount") or 0)
    job["transientErrorCount"] = int(job.get("transientErrorCount") or 0)
    job["stageTimings"] = job.get("stageTimings") or {}
    job["segmentProgress"] = job.get("segmentProgress") or {}
    job["lastProviderContactAt"] = job.get("lastProviderContactAt")
    if video_url and not job.get("remoteVideoUrl"):
        job["remoteVideoUrl"] = video_url
    if remote.get("status") == "completed" and video_url and job.get("status") == "failed":
        job["status"] = "orphaned"
        job["phase"] = "downloading"
        job["retryable"] = True
        job["nextRetryAt"] = None
        job["stageMessage"] = "检测到历史远端任务已成功，可点击“重试当前阶段”恢复下载与合成。"
    elif job.get("status") in WORKING_STATUSES:
        job["status"] = "orphaned"
        job["phase"] = _retry_phase(job)
        job["nextRetryAt"] = None
        job["stageMessage"] = "检测到历史未完成任务，可点击“重试当前阶段”继续处理。"
    job["createdAt"] = job.get("createdAt") or _now()
    job["updatedAt"] = _now()
    return job


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in job.items() if key not in {"prompt", "leaseOwner", "leaseExpiresAt"}}
    public["schemaVersion"] = str(job.get("schemaVersion") or LEGACY_VIDEO_SCHEMA_VERSION)
    public["renderMode"] = public.get("renderMode") or "full_hybrid"
    public["renderProfile"] = public.get("renderProfile") or _render_profile(str(public["renderMode"]), None)
    public["compositionStartedAt"] = public.get("compositionStartedAt")
    public["lastHeartbeatAt"] = public.get("lastHeartbeatAt")
    public["compositionTimeoutSeconds"] = public.get("compositionTimeoutSeconds") or public["renderProfile"].get("timeoutSeconds")
    public["compositionStage"] = public.get("compositionStage") or str(public.get("phase") or public.get("status") or "queued")
    public["generationAttemptId"] = public.get("generationAttemptId") or public.get("jobId")
    public["isCurrentVideo"] = bool(
        public.get("status") == "completed"
        and public.get("videoUrl")
        and (
            public.get("ffmpegNormalized") is True
            or bool(public.get("fallbackVideoUrl"))
            or bool(public.get("compositionWarning"))
        )
        and public.get("schemaVersion") == KNOWLEDGE_VIDEO_SCHEMA_VERSION
    )
    public["isPreviewVideo"] = _is_preview_fallback(public)
    public["canReusePreviousVideo"] = False
    public["sourceCitationIds"] = public.get("sourceCitationIds") or _source_citation_ids(public.get("citations") or [])
    return public


def _is_preview_fallback(job: dict[str, Any]) -> bool:
    return bool(
        job
        and job.get("status") == "completed"
        and (
            job.get("compositionWarning")
            or job.get("fallbackVideoUrl")
            or job.get("fallbackReason") == "composition_failed"
            or job.get("compositionStage") == "agnes_clip_fallback"
        )
    )


def _normalize_public_state(job: dict[str, Any]) -> None:
    changed = False
    status = str(job.get("status") or "")
    if (
        status != "completed"
        and str(job.get("errorCode") or "") == "COMPOSITION_FAILED"
        and str(job.get("agnesClipPath") or "").strip()
    ):
        detail = str(job.get("errorDetail") or job.get("error") or "COMPOSITION_FAILED")
        if _complete_with_agnes_clip_fallback(str(job.get("jobId") or ""), Path(str(job.get("agnesClipPath"))), RuntimeError(detail)):
            repaired = _load_job(str(job.get("jobId") or ""))
            if repaired:
                job.clear()
                job.update(repaired)
            return
    if not job.get("renderMode"):
        job["renderMode"] = "full_hybrid"
        changed = True
    if status in WORKING_STATUSES and (job.get("errorCode") or job.get("errorDetail")):
        job["status"] = "failed"
        job["phase"] = "failed"
        job["providerStatus"] = "failed"
        job["progress"] = 100
        job["retryable"] = True
        job["nextRetryAt"] = None
        job["stageMessage"] = "视频生成失败，未产出可验证 MP4。"
        job["finishedAt"] = job.get("finishedAt") or _now()
        changed = True
    elif status in {"composing", "validating", "downloading", "rendering"}:
        heartbeat = str(job.get("lastHeartbeatAt") or job.get("updatedAt") or "").strip()
        timeout = int(job.get("compositionTimeoutSeconds") or 900)
        if heartbeat:
            try:
                last = datetime.strptime(heartbeat, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                last = None
            if last and (datetime.now() - last).total_seconds() > timeout:
                job["status"] = "orphaned"
                job["phase"] = _retry_phase(job)
                job["retryable"] = True
                job["nextRetryAt"] = None
                job["stageMessage"] = "生成任务长时间没有心跳，已标记为可重试。"
                changed = True
    if changed:
        job["updatedAt"] = _now()
        _save_job(job)


def _render_profile(render_mode: str, override: dict[str, Any] | None = None) -> dict[str, Any]:
    base = AGNES_CLIP_RENDER_PROFILE if render_mode == "agnes_clip" else DEFAULT_HYBRID_RENDER_PROFILE
    profile = dict(base)
    if isinstance(override, dict):
        for key in ("width", "height", "fps", "durationSeconds", "timeoutSeconds"):
            value = override.get(key)
            if isinstance(value, (int, float)) and value > 0:
                profile[key] = int(value)
    return profile


def _client() -> httpx.Client:
    global _HTTP_CLIENT
    with _HTTP_CLIENT_LOCK:
        if _HTTP_CLIENT is None:
            _HTTP_CLIENT = httpx.Client(
                headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5, keepalive_expiry=30),
                follow_redirects=True,
            )
        return _HTTP_CLIENT


def _task_id(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for value in (payload.get("task_id"), payload.get("taskId"), payload.get("id"), data.get("task_id"), data.get("taskId"), data.get("id")):
        if str(value or "").strip().startswith("task_"):
            return str(value).strip()
    for value in (payload.get("id"), data.get("id")):
        if str(value or "").strip():
            return str(value).strip()
    for value in (payload.get("task_id"), payload.get("taskId"), data.get("task_id"), data.get("taskId")):
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _video_id(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for value in (payload.get("video_id"), payload.get("videoId"), data.get("video_id"), data.get("videoId")):
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _remote_status(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    value = payload.get("status") or data.get("status") or payload.get("state") or data.get("state") or "rendering"
    return str(value).strip().lower()


def _has_remote_status(payload: dict[str, Any]) -> bool:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return any(payload.get(key) is not None for key in ("status", "state")) or any(
        data.get(key) is not None for key in ("status", "state")
    )


def _remote_progress(payload: dict[str, Any]) -> int:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    try:
        return max(0, min(100, int(payload.get("progress", data.get("progress", 0)))))
    except (TypeError, ValueError):
        return 0


def _remote_video_url(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    for container in (payload, data, output):
        for key in ("remixed_from_video_id", "video_url", "videoUrl", "url"):
            value = container.get(key)
            if str(value or "").strip().startswith(("http://", "https://")):
                return str(value).strip()
    return ""


def _remote_error(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(payload.get("error") or payload.get("message") or data.get("error") or data.get("message") or "").strip()


def _response_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": _remote_status(payload),
        "progress": _remote_progress(payload),
        "taskId": _task_id(payload) or None,
        "videoId": _video_id(payload) or None,
        "hasVideoUrl": bool(_remote_video_url(payload)),
        "error": _remote_error(payload) or None,
        "keys": sorted(str(key) for key in payload.keys()),
    }


def _build_agnes_prompt(
    title: str,
    topic: str,
    scenes: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    personalization: dict[str, Any],
) -> str:
    scene_lines: list[str] = []
    for index, scene in enumerate(scenes[:3], start=1):
        concepts = "、".join(str(item) for item in scene.get("keyConcepts", [])[:4])
        steps = "；".join(str(item) for item in scene.get("operationSteps", [])[:3])
        scene_lines.append(
            f"{index}. {scene.get('screenTitle') or scene.get('title') or topic}："
            f"{scene.get('screenText') or scene.get('coreExplanation') or ''}；"
            f"关键概念：{concepts}；动画动作：{steps}"
        )
    citations_text = "；".join(
        f"{item.get('documentName', '课程资料')} {item.get('sourceLocation', '')}"
        for item in citations[:3]
        if isinstance(item, dict)
    )
    return "\n".join(
        [
            f"生成一段约 5 秒的中文数据结构动态教学片段：{title}",
            f"主题：{topic}",
            "画面用于嵌入 3 分钟教学成片，只展示一个清晰的数据结构变化过程。",
            "使用稳定的数组、节点、指针、移动、高亮和对比动画；避免人物采访、营销元素和大段文字。",
            "不要出现分镜、脚本、录屏、页面操作、制作工具或后台流程等元信息。",
            "屏幕可有极少量短字幕，但主体必须是结构变化动画。",
            *scene_lines,
            f"课程依据：{citations_text}",
            f"个性化重点：{json.dumps(personalization, ensure_ascii=False)}",
        ]
    )

def _record_stage_transition(job: dict[str, Any], previous: str, current: str) -> None:
    now = _now()
    timings = job.setdefault("stageTimings", {})
    if previous:
        record = timings.setdefault(previous, {})
        record.setdefault("startedAt", job.get("startedAt") or job.get("createdAt") or now)
        record["finishedAt"] = now
    timings.setdefault(current, {}).setdefault("startedAt", now)


def _finish_stage_timing(job: dict[str, Any]) -> None:
    phase = str(job.get("phase") or "")
    if phase:
        job.setdefault("stageTimings", {}).setdefault(phase, {})["finishedAt"] = _now()


def _retry_phase(job: dict[str, Any]) -> str:
    if job.get("agnesClipPath"):
        return "composing"
    if job.get("remoteVideoUrl"):
        return "downloading"
    if job.get("providerTaskId"):
        return "rendering"
    return "submitting"


def _idempotency_key(user_id: str, resource_id: str, content_hash: str) -> str:
    raw = f"{user_id}::{resource_id}::{content_hash}::{GENERATION_PROFILE}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _content_hash(topic: Any, scenes: list[dict[str, Any]], citations: list[dict[str, Any]]) -> str:
    raw = json.dumps(
        {"topic": topic, "scenes": scenes, "citationIds": _source_citation_ids(citations)},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_citation_ids(citations: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("chunkId")) for item in citations if isinstance(item, dict) and str(item.get("chunkId") or "").strip()]


def _storyboard_leakage_score(value: Any) -> int:
    patterns = ("打开视频演示页", "展示当前分镜", "分镜脚本", "录屏步骤", "页面操作", "本地渲染", "HyperFrames")
    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else str(value or "")
    return sum(text.count(pattern) for pattern in patterns)


def _set_latest_attempt(resource_id: str, user_id: str, job_id: str) -> None:
    """Compatibility hook; latest attempts are now selected from the video_jobs table."""
    return None


def _backoff_seconds(attempt: int) -> float:
    return min(20.0, 1.0 * (2**attempt)) + random.uniform(0.1, 0.8)


def _classify_unexpected_error(exc: Exception) -> str:
    text = str(exc).lower()
    if "ffprobe" in text or "mp4" in text:
        return "VIDEO_VALIDATION_FAILED"
    if "hyperframes" in text or "ffmpeg" in text:
        return "COMPOSITION_FAILED"
    return exc.__class__.__name__.upper()


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))
    return safe[:64] or "video"


def _base_url() -> str:
    return os.getenv("AGNES_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _api_key() -> str:
    return os.getenv("AGNES_API_KEY", "").strip()


def _model_name() -> str:
    return os.getenv("AGNES_VIDEO_MODEL", DEFAULT_MODEL_NAME).strip() or DEFAULT_MODEL_NAME


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _after(seconds: int | float) -> str:
    return (datetime.now() + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")

