from __future__ import annotations

import html
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import imageio_ffmpeg
from fastapi import HTTPException

from ..persistence import load_json, save_json


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MEDIA_DIR = BACKEND_ROOT / "data" / "media"
VIDEO_DIR = MEDIA_DIR / "videos"
SHOWCASE_DIR = MEDIA_DIR / "showcase"
HYPERFRAMES_DIR = MEDIA_DIR / "hyperframes"
for directory in (VIDEO_DIR, SHOWCASE_DIR, HYPERFRAMES_DIR):
    directory.mkdir(parents=True, exist_ok=True)

JOB_STORE_KEY = "local_open_video_jobs_v1"
LATEST_STORE_KEY = "local_open_video_latest_v1"
PROVIDER_NAME = "HyperFrames"
GENERATOR_NAME = "hyperframes-cli"
MODEL_NAME = "html-css-js-composition"
SOURCE_TYPE = "hyperframes_composition"
KNOWLEDGE_VIDEO_SCHEMA_VERSION = "knowledge_video_v2"
LEGACY_VIDEO_SCHEMA_VERSION = "legacy_storyboard_video"
TARGET_WIDTH = 1280
TARGET_HEIGHT = 720
TARGET_FPS = 30
TARGET_DURATION_SECONDS = 180
MIN_VALID_MP4_BYTES = 64 * 1024
RENDER_PROFILES = {
    "animated_lesson": {
        "width": TARGET_WIDTH,
        "height": TARGET_HEIGHT,
        "fps": TARGET_FPS,
        "durationSeconds": TARGET_DURATION_SECONDS,
        "timeoutSeconds": 720,
    },
    "fast_preview": {
        "width": 960,
        "height": 540,
        "fps": 15,
        "durationSeconds": 90,
        "timeoutSeconds": 420,
    },
    "full_hybrid": {
        "width": TARGET_WIDTH,
        "height": TARGET_HEIGHT,
        "fps": TARGET_FPS,
        "durationSeconds": TARGET_DURATION_SECONDS,
        "timeoutSeconds": 900,
    },
}
_RUNNING_JOBS: set[str] = set()
_RUNNING_JOBS_LOCK = threading.Lock()
STORYBOARD_LEAKAGE_PATTERNS = (
    "打开视频演示页",
    "展示当前分镜",
    "分镜脚本",
    "录屏步骤",
    "页面操作",
    "本地渲染",
    "HyperFrames",
    "EduAgent Studio",
)


def validate_local_video_configuration() -> None:
    """Fail early when the local HyperFrames rendering toolchain is unavailable."""

    node_version = _command_output(["node", "--version"], "无法执行 node --version，请先安装 Node.js 22 或更高版本。")
    major = _parse_node_major(node_version)
    if major is None or major < 22:
        raise HTTPException(
            status_code=503,
            detail=f"HyperFrames 需要 Node.js 22 或更高版本，当前检测到：{node_version or '未知版本'}。",
        )
    ffmpeg_path()
    ffprobe_path()
    _command_output(["npx", "--yes", "hyperframes", "--help"], "无法执行 npx --yes hyperframes --help，请先安装或允许 npx 拉取 HyperFrames CLI。")


def ffmpeg_path() -> str:
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"无法找到可用 FFmpeg：{exc}") from exc


def ffprobe_path() -> str:
    bundled = _bundled_ffprobe_path()
    if bundled and bundled.exists():
        return str(bundled)
    discovered = shutil.which("ffprobe")
    if discovered:
        return discovered
    raise HTTPException(status_code=503, detail="无法找到可用 FFprobe。HyperFrames 渲染和 MP4 校验都需要 ffprobe.exe。")


def start_local_video_job(
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
    render_mode: str = "animated_lesson",
    render_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_local_video_configuration()
    profile = _render_profile(render_mode, render_profile)
    job_id = f"hyperframes_{uuid4().hex[:10]}"
    work_dir = _job_work_dir(job_id)
    now = _now()
    job = {
        "jobId": job_id,
        "generationAttemptId": job_id,
        "resourceId": resource_id,
        "userId": user_id,
        "status": "queued",
        "schemaVersion": KNOWLEDGE_VIDEO_SCHEMA_VERSION,
        "contentHash": _content_hash(topic, scenes, citations or []),
        "sourceCitationIds": _source_citation_ids(citations or []),
        "isCurrentVideo": False,
        "canReusePreviousVideo": False,
        "provider": PROVIDER_NAME,
        "providerVideoId": job_id,
        "providerStatus": "queued",
        "title": title,
        "topic": topic,
        "generator": GENERATOR_NAME,
        "modelName": MODEL_NAME,
        "sourceType": source_type or SOURCE_TYPE,
        "generationMode": generation_mode or "deepseek_storyboard_to_hyperframes_mp4",
        "renderMode": render_mode,
        "renderProfile": profile,
        "visualQuality": "animated_lesson" if render_mode == "animated_lesson" else "hybrid_video",
        "storyboardLeakageScore": _storyboard_leakage_score(scenes),
        "llmStatus": llm_status or {},
        "agentTrace": agent_trace or [],
        "productionNotes": production_notes or [],
        "compositionPath": str(work_dir / "index.html"),
        "workDir": str(work_dir),
        "ffmpegNormalized": False,
        "videoUrl": None,
        "videoMimeType": "video/mp4",
        "videoDurationSeconds": None,
        "progress": 5,
        "stageMessage": "任务已创建，等待后台渲染。",
        "compositionStartedAt": None,
        "lastHeartbeatAt": now,
        "compositionTimeoutSeconds": profile["timeoutSeconds"],
        "compositionStage": "queued",
        "error": None,
        "errorCode": None,
        "errorDetail": None,
        "renderLogTail": [],
        "scenes": scenes,
        "citations": citations or [],
        "personalization": personalization or {},
        "generatedAt": None,
        "createdAt": now,
        "updatedAt": now,
        "startedAt": None,
        "finishedAt": None,
    }
    _save_job(job)
    _set_latest_attempt(resource_id, user_id, job_id)
    return _public_job(job)


def latest_local_video_job(resource_id: str, user_id: str) -> dict[str, Any] | None:
    job_id = _latest_record(resource_id, user_id).get("latestAttemptJobId")
    if not job_id:
        return None
    job = _load_job(str(job_id))
    return _public_job(job) if job else None


def last_successful_local_video_job(resource_id: str, user_id: str) -> dict[str, Any] | None:
    job_id = _latest_record(resource_id, user_id).get("lastSuccessfulJobId")
    if not job_id:
        return None
    job = _load_job(str(job_id))
    if not job or job.get("status") != "completed" or not job.get("videoUrl"):
        return None
    return _public_job(job)


def record_failed_local_video_attempt(
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
    job_id = f"hyperframes_{uuid4().hex[:10]}"
    now = _now()
    job = {
        "jobId": job_id,
        "generationAttemptId": job_id,
        "resourceId": resource_id,
        "userId": user_id,
        "status": "failed",
        "schemaVersion": KNOWLEDGE_VIDEO_SCHEMA_VERSION,
        "contentHash": _content_hash(topic, [], citations or []),
        "sourceCitationIds": _source_citation_ids(citations or []),
        "isCurrentVideo": False,
        "canReusePreviousVideo": False,
        "provider": PROVIDER_NAME,
        "providerVideoId": job_id,
        "providerStatus": "failed",
        "title": title,
        "topic": topic,
        "generator": GENERATOR_NAME,
        "modelName": MODEL_NAME,
        "sourceType": SOURCE_TYPE,
        "ffmpegNormalized": False,
        "videoUrl": None,
        "videoMimeType": "video/mp4",
        "videoDurationSeconds": None,
        "progress": 100,
        "stageMessage": "生成失败，未产出新的知识点教学 MP4。",
        "error": error,
        "errorCode": error_code,
        "errorDetail": error,
        "renderLogTail": [line.strip() for line in str(error).splitlines() if line.strip()][-40:],
        "scenes": [],
        "citations": citations or [],
        "personalization": personalization or {},
        "generatedAt": None,
        "createdAt": now,
        "updatedAt": now,
        "startedAt": None,
        "finishedAt": now,
    }
    _save_job(job)
    _set_latest_attempt(resource_id, user_id, job_id)
    return _public_job(job)


def refresh_local_video_job(job_id: str, *, user_id: str | None = None) -> dict[str, Any]:
    job = _load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="未找到 HyperFrames 视频生成任务。")
    if user_id and job.get("userId") not in {user_id, "anonymous"}:
        raise HTTPException(status_code=403, detail="无权查看该 HyperFrames 视频生成任务。")
    _mark_orphaned_if_stale(job)
    return _public_job(job)


def retry_local_video_job(job_id: str, *, user_id: str | None = None) -> dict[str, Any]:
    job = _load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="未找到 HyperFrames 视频生成任务。")
    if user_id and job.get("userId") not in {user_id, "anonymous"}:
        raise HTTPException(status_code=403, detail="无权重试该 HyperFrames 视频生成任务。")
    if job.get("status") == "completed":
        return _public_job(job)
    profile = _render_profile(str(job.get("renderMode") or "fast_preview"), job.get("renderProfile") if isinstance(job.get("renderProfile"), dict) else None)
    now = _now()
    job["status"] = "queued"
    job["providerStatus"] = "queued"
    job["progress"] = 8
    job["stageMessage"] = "已安排重新渲染当前 HyperFrames 阶段。"
    job["renderProfile"] = profile
    job["compositionTimeoutSeconds"] = profile["timeoutSeconds"]
    job["compositionStage"] = "queued"
    job["lastHeartbeatAt"] = now
    job["compositionStartedAt"] = None
    job["startedAt"] = None
    job["finishedAt"] = None
    job["error"] = None
    job["errorCode"] = None
    job["errorDetail"] = None
    job["updatedAt"] = now
    _save_job(job)
    return _public_job(job)


def render_local_video_job(job_id: str) -> None:
    with _RUNNING_JOBS_LOCK:
        if job_id in _RUNNING_JOBS:
            return
        _RUNNING_JOBS.add(job_id)
    try:
        job = _load_job(job_id)
        if not job or job.get("status") in {"completed", "failed"}:
            return
        _update_job_stage(job_id, "rendering", 12, "正在校验本地 HyperFrames、FFmpeg 与 FFprobe。", started=True, composition_stage="toolchain_check")
        validate_local_video_configuration()
        job = _load_job(job_id) or job
        profile = _render_profile(str(job.get("renderMode") or "full_hybrid"), job.get("renderProfile") if isinstance(job.get("renderProfile"), dict) else None)
        job["renderProfile"] = profile
        job["compositionTimeoutSeconds"] = profile["timeoutSeconds"]
        _save_job(job)
        work_dir = Path(str(job["workDir"]))
        output_name = f"{_safe_name(job['resourceId'])}_{_safe_name(job['jobId'])}.mp4"
        public_output_path = VIDEO_DIR / output_name
        _clean_render_directory(work_dir)
        _update_job_stage(job_id, "rendering", 22, "正在写入 HyperFrames composition。", composition_stage="write_composition")
        _write_hyperframes_project(job, work_dir, profile)
        _update_job_stage(job_id, "rendering", 35, "正在调用 HyperFrames CLI 输出 MP4。", composition_stage="hyperframes_render")
        rendered_path, logs = _render_hyperframes_project_strict(work_dir, output_name, timeout_seconds=profile["timeoutSeconds"])
        _merge_job_logs(job_id, logs)
        _update_job_stage(job_id, "verifying", 82, "正在使用 FFprobe 校验 MP4 真实性。", composition_stage="ffprobe_verify")
        leakage_score = _storyboard_leakage_score(job.get("scenes") or [])
        if leakage_score:
            raise RuntimeError(f"教学动画文本仍包含制作痕迹，已阻止成片：score={leakage_score}")
        probe = _verify_mp4(rendered_path)
        tmp_public_path = public_output_path.with_suffix(public_output_path.suffix + ".tmp")
        if tmp_public_path.exists():
            tmp_public_path.unlink()
        shutil.copy2(rendered_path, tmp_public_path)
        _verify_mp4(tmp_public_path)
        os.replace(tmp_public_path, public_output_path)
        job = _load_job(job_id) or job
        job["videoUrl"] = f"/media/videos/{public_output_path.name}"
        job["videoDurationSeconds"] = probe["duration"]
        job["visualQuality"] = "animated_lesson" if job.get("renderMode") == "animated_lesson" else "hybrid_video"
        job["storyboardLeakageScore"] = leakage_score
        job["ffmpegNormalized"] = True
        job["status"] = "completed"
        job["providerStatus"] = "completed"
        job["progress"] = 100
        job["stageMessage"] = "已生成并校验真实 MP4。"
        job["compositionStage"] = "completed"
        job["lastHeartbeatAt"] = _now()
        job["error"] = None
        job["errorCode"] = None
        job["errorDetail"] = None
        job["schemaVersion"] = job.get("schemaVersion") or KNOWLEDGE_VIDEO_SCHEMA_VERSION
        job["generationAttemptId"] = job.get("generationAttemptId") or job_id
        job["generatedAt"] = _now()
        job["isCurrentVideo"] = True
        job["canReusePreviousVideo"] = False
        job["sourceCitationIds"] = job.get("sourceCitationIds") or _source_citation_ids(job.get("citations") or [])
        job["contentHash"] = job.get("contentHash") or _content_hash(job.get("topic"), job.get("scenes") or [], job.get("citations") or [])
        job["finishedAt"] = _now()
        job["updatedAt"] = _now()
        _save_job(job)
        _set_last_successful(str(job["resourceId"]), str(job.get("userId") or "anonymous"), job_id)
    except Exception as exc:
        _fail_job(job_id, exc)
    finally:
        with _RUNNING_JOBS_LOCK:
            _RUNNING_JOBS.discard(job_id)


def verify_mp4(path: Path) -> dict[str, Any]:
    """Public MP4 validation entry point shared by hybrid video jobs."""
    return _verify_mp4(path)


def render_hybrid_video(
    job: dict[str, Any],
    agnes_clip_path: Path,
    output_path: Path,
    *,
    progress_callback=None,
    render_profile: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Render the deterministic 180-second lesson and embed an Agnes motion clip."""
    validate_local_video_configuration()
    profile = _render_profile(str(job.get("renderMode") or "full_hybrid"), render_profile or (job.get("renderProfile") if isinstance(job.get("renderProfile"), dict) else None))
    work_dir = _job_work_dir(f"hybrid_{job['jobId']}")
    output_name = f"{_safe_name(job['resourceId'])}_{_safe_name(job['jobId'])}_base.mp4"
    _clean_render_directory(work_dir)
    if progress_callback:
        progress_callback(74, "正在写入 HyperFrames 教学 composition。", "write_composition")
    _write_hyperframes_project(job, work_dir, profile)
    if progress_callback:
        progress_callback(78, "正在渲染结构图、操作过程、复杂度和练习画面。", "hyperframes_render")
    rendered_path, logs = _render_hyperframes_project_strict(work_dir, output_name, timeout_seconds=profile["timeoutSeconds"])
    _verify_mp4(rendered_path)
    leakage_score = _storyboard_leakage_score(job.get("scenes") or [])
    if leakage_score:
        raise RuntimeError(f"教学动画文本仍包含制作痕迹，已阻止混合成片：score={leakage_score}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f"{output_path.stem}.tmp.mp4")
    tmp_path.unlink(missing_ok=True)
    if progress_callback:
        progress_callback(92, "正在用 FFmpeg 嵌入 Agnes 动态片段并标准化 MP4。", "ffmpeg_mux")
    overlay_width = max(240, min(384, int(profile["width"] * 0.34)))
    overlay_height = max(136, int(overlay_width * 9 / 16))
    filter_graph = _agnes_overlay_filter_graph(overlay_width, overlay_height)
    command = [
        ffmpeg_path(),
        "-y",
        "-i",
        str(rendered_path),
        "-i",
        str(agnes_clip_path),
        "-filter_complex",
        filter_graph,
        "-map",
        "[video]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        "-t",
        str(profile["durationSeconds"]),
        str(tmp_path),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=profile["timeoutSeconds"],
    )
    logs.extend(_process_log_lines(result))
    if result.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        logs.extend(_ffmpeg_failure_context(result, rendered_path, agnes_clip_path, filter_graph))
        raise RuntimeError(f"FFmpeg hybrid composition failed: {_compact_process_error(result)}")
    probe = _verify_mp4(tmp_path)
    if probe["duration"] < profile["durationSeconds"] - 2:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"混合成片时长不足：{probe['duration']} 秒，目标为 {profile['durationSeconds']} 秒。")
    os.replace(tmp_path, output_path)
    final_probe = _verify_mp4(output_path)
    if progress_callback:
        progress_callback(98, "正在执行最终 FFprobe 媒体校验。", "final_ffprobe")
    return final_probe, logs[-40:]


def _agnes_overlay_filter_graph(overlay_width: int, overlay_height: int) -> str:
    width = max(2, int(overlay_width))
    height = max(2, int(overlay_height))
    return (
        f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:0x0f172a,"
        "setsar=1[agnes];"
        "[0:v][agnes]overlay=W-w-34:H-h-118:enable='between(t,8,34)'[video]"
    )


def showcase_video_payloads() -> list[dict[str, Any]]:
    items = []
    for path in sorted(SHOWCASE_DIR.glob("*"), key=lambda item: item.stat().st_mtime, reverse=True):
        if path.suffix.lower() not in {".mp4", ".mov", ".webm", ".mkv"}:
            continue
        items.append({
            "title": path.stem,
            "videoUrl": f"/media/showcase/{path.name}",
            "sourceType": "showcase_video",
            "provider": "灞曠ず绱犳潗",
        })
    return items[:8]


def _write_hyperframes_project(job: dict[str, Any], work_dir: Path, render_profile: dict[str, Any] | None = None) -> None:
    assets_dir = work_dir / "assets"
    renders_dir = work_dir / "renders"
    assets_dir.mkdir(parents=True, exist_ok=True)
    renders_dir.mkdir(parents=True, exist_ok=True)
    _copy_chinese_font(assets_dir)
    scenes = _strict_normalized_scenes(job.get("scenes"))
    profile = _render_profile(str(job.get("renderMode") or "full_hybrid"), render_profile or (job.get("renderProfile") if isinstance(job.get("renderProfile"), dict) else None))
    meta = {
        "title": job.get("title") or "教学视频",
        "topic": job.get("topic") or "数据结构课程",
        "durationSeconds": profile["durationSeconds"],
        "width": profile["width"],
        "height": profile["height"],
        "fps": profile["fps"],
        "renderer": PROVIDER_NAME,
        "scenes": scenes,
        "citations": job.get("citations") or [],
        "personalization": job.get("personalization") or {},
    }
    (work_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (work_dir / "index.html").write_text(_composition_html_v3(meta), encoding="utf-8")


def _clean_render_directory(work_dir: Path) -> None:
    renders_dir = work_dir / "renders"
    if renders_dir.exists():
        shutil.rmtree(renders_dir)
    renders_dir.mkdir(parents=True, exist_ok=True)


def _copy_chinese_font(assets_dir: Path) -> None:
    target = assets_dir / "msyh.ttc"
    if target.exists():
        return
    for font_path in [Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf"), Path("C:/Windows/Fonts/simsun.ttc")]:
        if font_path.exists():
            shutil.copy2(font_path, target)
            return


def _render_hyperframes_project_strict(work_dir: Path, output_name: str, *, timeout_seconds: int = 900) -> tuple[Path, list[str]]:
    output_path = work_dir / "renders" / output_name
    commands = [
        ["npx", "--yes", "hyperframes", "render", ".", "--output", str(output_path)],
        ["npx", "--yes", "hyperframes", "render", ".", "-o", str(output_path)],
    ]
    failures: list[str] = []
    logs: list[str] = []
    for command in commands:
        try:
            result = subprocess.run(
                _resolved_command(command),
                cwd=work_dir,
                env=_hyperframes_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            timeout_logs = _timeout_log_lines(exc)
            logs.extend(timeout_logs)
            rendered = _find_rendered_mp4(work_dir, output_path)
            if rendered:
                logs.append("HyperFrames CLI timed out, but a rendered MP4 was found for FFprobe verification.")
                return rendered, logs[-40:]
            failures.append(f"HyperFrames CLI timed out after {timeout_seconds} seconds and no rendered MP4 was found. " + " ".join(timeout_logs))
            continue
        logs.extend(_process_log_lines(result))
        if result.returncode == 0:
            rendered = _find_rendered_mp4(work_dir, output_path)
            if rendered:
                return rendered, logs[-40:]
            failures.append("HyperFrames command exited successfully, but no MP4 was found in renders.")
        else:
            failures.append(_compact_process_error(result))
    raise RuntimeError("HyperFrames render failed: " + "; ".join(item for item in failures if item))


def _find_rendered_mp4(work_dir: Path, preferred_path: Path) -> Path | None:
    if preferred_path.exists() and preferred_path.stat().st_size > 0:
        return preferred_path
    candidates = sorted((work_dir / "renders").glob("*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0] if candidates and candidates[0].stat().st_size > 0 else None


def _render_profile(render_mode: str, override: dict[str, Any] | None = None) -> dict[str, int]:
    base = dict(RENDER_PROFILES.get(render_mode) or RENDER_PROFILES["animated_lesson"])
    if isinstance(override, dict):
        for source_key, target_key in [
            ("width", "width"),
            ("height", "height"),
            ("fps", "fps"),
            ("durationSeconds", "durationSeconds"),
            ("timeoutSeconds", "timeoutSeconds"),
            ("compositionTimeoutSeconds", "timeoutSeconds"),
        ]:
            try:
                value = int(override.get(source_key)) if override.get(source_key) is not None else None
            except (TypeError, ValueError):
                value = None
            if value:
                base[target_key] = value
    base["width"] = max(480, min(1920, int(base["width"])))
    base["height"] = max(270, min(1080, int(base["height"])))
    base["fps"] = max(8, min(30, int(base["fps"])))
    base["durationSeconds"] = max(15, min(240, int(base["durationSeconds"])))
    base["timeoutSeconds"] = max(120, min(1200, int(base["timeoutSeconds"])))
    return base


def _duration_seconds(path: Path) -> float | None:
    command = [ffmpeg_path(), "-i", str(path), "-hide_banner"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    text = result.stderr or result.stdout
    marker = "Duration: "
    if marker not in text:
        return None
    value = text.split(marker, 1)[1].split(",", 1)[0].strip()
    try:
        hours, minutes, seconds = value.split(":")
        return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 1)
    except ValueError:
        return None


def _verify_mp4(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"MP4 校验失败：文件不存在：{path}")
    if path.stat().st_size < MIN_VALID_MP4_BYTES:
        raise RuntimeError(f"MP4 校验失败：文件过小，可能是空文件或半成品：{path.stat().st_size} bytes")
    command = [
        ffprobe_path(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    if result.returncode != 0:
        raise RuntimeError(f"MP4 校验失败：ffprobe 无法解析文件。{_compact_process_error(result)}")
    try:
        probe = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("MP4 校验失败：ffprobe 返回了无效 JSON。") from exc
    streams = probe.get("streams") if isinstance(probe.get("streams"), list) else []
    video_stream = next((item for item in streams if isinstance(item, dict) and item.get("codec_type") == "video"), None)
    if not isinstance(video_stream, dict):
        raise RuntimeError("MP4 校验失败：文件中没有视频流。")
    duration = _probe_duration(probe, video_stream)
    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    if duration <= 0:
        raise RuntimeError("MP4 校验失败：视频时长无效。")
    if width <= 0 or height <= 0:
        raise RuntimeError("MP4 校验失败：视频宽高无效。")
    return {"duration": round(duration, 1), "width": width, "height": height}


def _probe_duration(probe: dict[str, Any], video_stream: dict[str, Any]) -> float:
    values = []
    if isinstance(probe.get("format"), dict):
        values.append(probe["format"].get("duration"))
    values.append(video_stream.get("duration"))
    for value in values:
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration
    return 0.0


def _composition_html_v3(meta: dict[str, Any]) -> str:
    width = int(meta.get("width") or TARGET_WIDTH)
    height = int(meta.get("height") or TARGET_HEIGHT)
    fps = int(meta.get("fps") or TARGET_FPS)
    duration = int(meta.get("durationSeconds") or TARGET_DURATION_SECONDS)
    scene_markup = "\n".join(_scene_clip_html_v3(scene, index, meta) for index, scene in enumerate(meta["scenes"]))
    caption_markup = "\n".join(_caption_clip_html(scene, index, len(meta["scenes"])) for index, scene in enumerate(meta["scenes"]))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_escape(meta["title"])}</title>
  <style>
    @font-face {{
      font-family: "EduChinese";
      src: url("./assets/msyh.ttc") format("truetype");
      font-weight: 400 800;
      font-style: normal;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      width: {width}px;
      height: {height}px;
      margin: 0;
      overflow: hidden;
      background: #08111f;
      color: #f8fafc;
      font-family: "EduChinese", Arial, sans-serif;
    }}
    #eduagent-video {{
      position: relative;
      width: {width}px;
      height: {height}px;
      overflow: hidden;
      background: linear-gradient(135deg, #07111f 0%, #0f2a3f 46%, #101827 100%);
    }}
    .grid {{
      position: absolute;
      inset: 0;
      opacity: 0.14;
      background-image: linear-gradient(rgba(191,219,254,.25) 1px, transparent 1px), linear-gradient(90deg, rgba(191,219,254,.25) 1px, transparent 1px);
      background-size: 50px 50px;
    }}
    .chrome {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 16px;
      height: 100%;
      padding: 30px 42px 26px;
    }}
    header {{
      display: grid;
      grid-template-columns: 1fr auto;
      align-items: start;
      gap: 18px;
    }}
    h1 {{
      max-width: 860px;
      margin: 0;
      font-size: 38px;
      line-height: 1.14;
      letter-spacing: 0;
    }}
    .meta {{
      display: grid;
      gap: 7px;
      justify-items: end;
      color: #cbd5e1;
      font-size: 16px;
    }}
    .progress {{
      width: 260px;
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(148,163,184,.28);
    }}
    .progress span {{
      display: block;
      width: 100%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #38bdf8, #facc15);
      transform-origin: left center;
      animation: fill {duration}s linear both;
    }}
    main {{
      position: relative;
      min-height: 0;
    }}
    .scene {{
      position: absolute;
      inset: 0;
      display: grid;
      grid-template-columns: 0.58fr 1.42fr;
      gap: 22px;
      opacity: 0;
      animation: sceneIn .6s ease both;
    }}
    .lesson, .board {{
      min-height: 0;
      border: 1px solid rgba(191,219,254,.22);
      border-radius: 8px;
      background: rgba(15,23,42,.78);
      box-shadow: 0 20px 56px rgba(2,6,23,.34);
    }}
    .lesson {{
      display: grid;
      align-content: start;
      gap: 14px;
      padding: 24px;
    }}
    .lesson-label {{
      width: fit-content;
      padding: 7px 12px;
      border: 1px solid rgba(125,211,252,.32);
      border-radius: 999px;
      color: #bfdbfe;
      font-size: 15px;
      font-weight: 800;
      background: rgba(8,47,73,.54);
    }}
    h2 {{
      margin: 0;
      font-size: 31px;
      line-height: 1.17;
      letter-spacing: 0;
    }}
    .goal {{
      margin: 0;
      color: #fde68a;
      font-size: 18px;
      line-height: 1.42;
    }}
    .explain {{
      margin: 0;
      color: #e2e8f0;
      font-size: 20px;
      line-height: 1.44;
    }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .chips span {{
      padding: 6px 10px;
      border: 1px solid rgba(125,211,252,.3);
      border-radius: 999px;
      color: #bae6fd;
      font-size: 15px;
      background: rgba(8,47,73,.5);
    }}
    .formula {{
      border-left: 3px solid #60a5fa;
      padding-left: 12px;
      color: #bfdbfe;
      font-size: 17px;
      line-height: 1.44;
    }}
    .board {{
      position: relative;
      overflow: hidden;
      padding: 28px;
    }}
    .board-title {{
      margin: 0 0 18px;
      color: #dbeafe;
      font-size: 20px;
      font-weight: 800;
    }}
    .array-row, .node-row {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      min-height: 116px;
      margin-top: 20px;
    }}
    .cell {{
      position: relative;
      display: grid;
      place-items: center;
      width: 82px;
      height: 70px;
      border: 2px solid #60a5fa;
      border-radius: 8px;
      color: #fff;
      font-size: 28px;
      font-weight: 800;
      background: rgba(37,99,235,.34);
      animation: popIn .7s ease both, cellPulse 4.2s ease-in-out infinite;
    }}
    .cell small {{
      position: absolute;
      left: 0;
      right: 0;
      bottom: -24px;
      color: #93c5fd;
      font-size: 14px;
      font-weight: 600;
      text-align: center;
    }}
    .node {{
      display: grid;
      grid-template-columns: 70px 34px;
      height: 68px;
      border: 2px solid #22c55e;
      border-radius: 8px;
      overflow: hidden;
      background: rgba(20,83,45,.44);
      animation: popIn .7s ease both, nodeGlow 4.2s ease-in-out infinite;
    }}
    .node b, .node span {{
      display: grid;
      place-items: center;
      font-size: 24px;
    }}
    .node span {{
      border-left: 1px solid rgba(187,247,208,.5);
      color: #bbf7d0;
    }}
    .arrow {{
      color: #facc15;
      font-size: 28px;
      font-weight: 900;
      animation: arrowFlow 1.8s ease-in-out infinite;
    }}
    .compare {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      height: 330px;
      align-items: stretch;
    }}
    .compare-panel, .quiz-card {{
      border: 1px solid rgba(125,211,252,.25);
      border-radius: 8px;
      padding: 18px;
      background: rgba(8,47,73,.56);
    }}
    .compare-panel h3, .quiz-card h3 {{
      margin: 0 0 14px;
      color: #f8fafc;
      font-size: 23px;
    }}
    .shift-row {{
      display: flex;
      justify-content: center;
      gap: 9px;
      margin-top: 24px;
      min-height: 110px;
    }}
    .shift-row .cell.inserted {{
      border-color: #facc15;
      background: rgba(113,63,18,.7);
      animation: insertDrop 2.6s ease infinite;
    }}
    .step-list {{
      display: grid;
      gap: 8px;
      margin-top: 18px;
      color: #e2e8f0;
      font-size: 17px;
      line-height: 1.38;
    }}
    .step-list div {{
      border-left: 3px solid rgba(96,165,250,.8);
      padding: 8px 10px;
      border-radius: 0 8px 8px 0;
      background: rgba(15,23,42,.54);
      animation: stepReveal 3.8s ease both;
    }}
    .complexity-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      align-items: stretch;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 8px;
      color: #e2e8f0;
      font-size: 18px;
      background: rgba(15,23,42,.62);
    }}
    th, td {{
      border: 1px solid rgba(148,163,184,.28);
      padding: 12px;
      text-align: center;
    }}
    th {{
      color: #bfdbfe;
      background: rgba(37,99,235,.26);
    }}
    .chart {{
      position: relative;
      min-height: 265px;
      border-left: 2px solid rgba(226,232,240,.58);
      border-bottom: 2px solid rgba(226,232,240,.58);
      margin: 12px 10px 4px;
    }}
    .chart-line {{
      position: absolute;
      left: 36px;
      right: 36px;
      bottom: 52px;
      height: 126px;
      border-bottom: 6px solid #60a5fa;
      border-right: 6px solid #60a5fa;
      transform: skewY(-18deg);
      transform-origin: bottom left;
      animation: chartGrow 3.5s ease both;
    }}
    .chart-label {{
      position: absolute;
      color: #dbeafe;
      font-size: 16px;
    }}
    .quiz {{
      display: grid;
      grid-template-columns: 1.1fr .9fr;
      gap: 18px;
      align-items: stretch;
    }}
    .answer-box {{
      display: grid;
      place-items: center;
      min-height: 160px;
      border: 1px dashed rgba(250,204,21,.55);
      border-radius: 8px;
      color: #fde68a;
      font-size: 22px;
      line-height: 1.45;
      text-align: center;
      background: rgba(69,26,3,.38);
    }}
    .caption {{
      position: relative;
      min-height: 86px;
      padding: 18px 22px;
      border: 1px solid rgba(148,163,184,.24);
      border-radius: 8px;
      background: rgba(2,6,23,.84);
    }}
    .caption span {{
      position: absolute;
      left: 22px;
      right: 22px;
      bottom: 15px;
      opacity: 0;
      color: #f8fafc;
      font-size: 21px;
      line-height: 1.42;
      animation: sceneIn .5s ease both;
    }}
    @keyframes fill {{ from {{ transform: scaleX(0); }} to {{ transform: scaleX(1); }} }}
    @keyframes sceneIn {{ from {{ opacity: 0; transform: translateY(12px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    @keyframes popIn {{ from {{ opacity: 0; transform: translateY(16px) scale(.96); }} to {{ opacity: 1; transform: translateY(0) scale(1); }} }}
    @keyframes insertDrop {{ 0% {{ transform: translateY(-26px); }} 35%,100% {{ transform: translateY(0); }} }}
    @keyframes chartGrow {{ from {{ clip-path: inset(0 100% 0 0); }} to {{ clip-path: inset(0 0 0 0); }} }}
    @keyframes cellPulse {{ 0%,100% {{ box-shadow: 0 0 0 rgba(96,165,250,0); }} 50% {{ box-shadow: 0 0 30px rgba(96,165,250,.42); }} }}
    @keyframes nodeGlow {{ 0%,100% {{ box-shadow: 0 0 0 rgba(34,197,94,0); }} 50% {{ box-shadow: 0 0 26px rgba(34,197,94,.34); }} }}
    @keyframes arrowFlow {{ 0%,100% {{ transform: translateX(0); opacity: .72; }} 50% {{ transform: translateX(8px); opacity: 1; }} }}
    @keyframes stepReveal {{ from {{ opacity: 0; transform: translateX(-10px); }} to {{ opacity: 1; transform: translateX(0); }} }}
  </style>
</head>
<body>
  <div id="eduagent-video" data-composition-id="eduagent-video" data-start="0" data-duration="{duration}" data-width="{width}" data-height="{height}" data-fps="{fps}">
    <div class="grid"></div>
    <div class="chrome">
      <header>
        <div>
          <h1>{_escape(meta["title"])}</h1>
        </div>
        <div class="meta">
          <span>结构动画</span>
          <span>操作推演</span>
          <div class="progress"><span></span></div>
        </div>
      </header>
      <main>
{scene_markup}
      </main>
      <footer class="caption">
{caption_markup}
      </footer>
    </div>
  </div>
  <script>
    const noopTimeline = {{
      seek() {{ return this; }},
      time() {{ return this; }},
      pause() {{ return this; }},
      progress() {{ return this; }},
      duration() {{ return {TARGET_DURATION_SECONDS}; }},
      totalDuration() {{ return {TARGET_DURATION_SECONDS}; }}
    }};
    window.__timelines = window.__timelines || {{}};
    window.__timelines["eduagent-video"] = noopTimeline;
    window.__playerReady = true;
  </script>
</body>
</html>
"""


def _scene_clip_html_v3(scene: dict[str, Any], index: int, meta: dict[str, Any]) -> str:
    start, duration = _scene_timing(scene, index, len(meta["scenes"]))
    concepts = "".join(f"<span>{_escape(item)}</span>" for item in scene.get("keyConcepts", [])[:5])
    visual = _scene_visual_html_v3(scene)
    scene_id = _safe_name(str(scene.get("id") or f"scene_{index + 1}"))
    return f"""        <section id="{scene_id}" class="scene clip" data-start="{start}" data-duration="{duration}" data-track-index="{index}">
          <section class="lesson">
            <div class="lesson-label">知识讲解</div>
            <h2>{_escape(_teaching_heading(scene))}</h2>
            <p class="goal">{_escape(_short_video_text(scene.get("teachingGoal") or scene.get("screenText") or "", 72))}</p>
            <p class="explain">{_escape(_short_video_text(scene.get("screenText") or scene.get("coreExplanation") or "", 88))}</p>
            <div class="chips">{concepts}</div>
            <div class="formula">{_escape(_short_video_text(scene.get("formulaOrComplexity") or "", 64))}</div>
          </section>
          <section class="board">
            {visual}
          </section>
        </section>"""


def _scene_visual_html_v3(scene: dict[str, Any]) -> str:
    visual_model = scene.get("visualModel") if isinstance(scene.get("visualModel"), dict) else {}
    model_type = str(visual_model.get("type") or "")
    example = scene.get("exampleData") if isinstance(scene.get("exampleData"), dict) else {}
    steps = [str(item) for item in scene.get("operationSteps", []) if str(item).strip()]
    title = _escape(_board_heading(scene))
    if model_type in {"sequence_array", "linked_nodes"}:
        sequence = _example_sequence(example)
        visual = _linked_nodes_html(sequence) if model_type == "linked_nodes" else _array_cells_html(sequence)
        return f'<p class="board-title">{title}</p>{visual}<div class="step-list">{_steps_html(steps[:3])}</div>'
    if model_type == "structure_compare":
        return f'<p class="board-title">{title}</p><div class="compare"><section class="compare-panel"><h3>顺序表</h3>{_array_cells_html(_example_sequence(example))}<div class="step-list">{_steps_html(steps[:2])}</div></section><section class="compare-panel"><h3>链表</h3>{_linked_nodes_html(_example_sequence(example))}<div class="step-list">{_steps_html(steps[2:4] or steps[:2])}</div></section></div>'
    if model_type in {"insert_shift", "delete_shift"}:
        sequence = _operation_result_sequence(example, model_type)
        return f'<p class="board-title">{title}</p><div class="shift-row">{_array_cells(sequence, inserted=str(example.get("insertValue") or ""))}</div><div class="step-list">{_steps_html(steps[:5])}</div>'
    if model_type == "complexity_table":
        return f'<p class="board-title">{title}</p><div class="complexity-grid">{_complexity_table_html(example)}<div class="chart"><div class="chart-line"></div><span class="chart-label" style="left: 12px; bottom: 8px;">n</span><span class="chart-label" style="right: 16px; top: 12px;">T(n)</span><span class="chart-label" style="left: 84px; top: 95px;">{_escape(scene.get("formulaOrComplexity") or "O(n)")}</span></div></div>'
    return f'<p class="board-title">{title}</p><div class="quiz"><section class="quiz-card"><h3>练习题</h3><p class="explain">{_escape(scene.get("studentTask") or "")}</p>{_array_cells_html(_example_sequence(example))}</section><section class="answer-box">{_escape(_expected_result_text(example, scene))}</section></div>'


def _teaching_heading(scene: dict[str, Any]) -> str:
    title = str(scene.get("screenTitle") or "").strip()
    if title and not _looks_like_production_label(title):
        return title
    concepts = scene.get("keyConcepts") if isinstance(scene.get("keyConcepts"), list) else []
    first_concept = next((str(item).strip() for item in concepts if str(item).strip()), "")
    return first_concept or "本节要点"


def _board_heading(scene: dict[str, Any]) -> str:
    visual_model = scene.get("visualModel") if isinstance(scene.get("visualModel"), dict) else {}
    description = str(visual_model.get("description") or "").strip()
    if description and not _looks_like_production_label(description):
        return description[:42]
    concepts = scene.get("keyConcepts") if isinstance(scene.get("keyConcepts"), list) else []
    useful = [str(item).strip() for item in concepts if str(item).strip()]
    return "、".join(useful[:2]) or "结构变化演示"


def _looks_like_production_label(value: str) -> bool:
    return any(token in value for token in ("分镜", "镜头", "画面", "字幕", "旁白", "时间段", "录屏", "脚本"))


def _expected_result_text(example: dict[str, Any], scene: dict[str, Any]) -> str:
    value = example.get("expectedResult")
    if isinstance(value, list):
        return " -> ".join(str(item) for item in value[:8])
    if str(value or "").strip():
        return str(value).strip()
    return str(scene.get("formulaOrComplexity") or "暂停思考，再看答案")


def _example_sequence(example: dict[str, Any]) -> list[str]:
    sequence = example.get("sequence")
    if isinstance(sequence, list):
        return [str(item) for item in sequence if str(item).strip()][:8] or ["A", "B", "C", "D"]
    return ["A", "B", "C", "D"]


def _operation_result_sequence(example: dict[str, Any], model_type: str) -> list[str]:
    expected = example.get("expectedResult")
    if isinstance(expected, list) and expected:
        return [str(item) for item in expected][:8]
    sequence = _example_sequence(example)
    if model_type == "insert_shift":
        value = str(example.get("insertValue") or "X")
        try:
            index = max(0, min(len(sequence), int(example.get("insertIndex", 2))))
        except (TypeError, ValueError):
            index = 2
        return [*sequence[:index], value, *sequence[index:]][:8]
    if len(sequence) > 2:
        return [*sequence[:2], *sequence[3:]]
    return sequence


def _array_cells_html(sequence: list[str]) -> str:
    return f'<div class="array-row">{_array_cells(sequence)}</div>'


def _array_cells(sequence: list[str], inserted: str = "") -> str:
    cells = []
    for index, item in enumerate(sequence):
        klass = "cell inserted" if inserted and str(item) == inserted else "cell"
        cells.append(f'<div class="{klass}" style="animation-delay:{index * 120}ms">{_escape(item)}<small>i={index}</small></div>')
    return "".join(cells)


def _linked_nodes_html(sequence: list[str]) -> str:
    parts = []
    for index, item in enumerate(sequence):
        parts.append(f'<div class="node" style="animation-delay:{index * 120}ms"><b>{_escape(item)}</b><span>next</span></div>')
        if index < len(sequence) - 1:
            parts.append('<div class="arrow">→</div>')
    return f'<div class="node-row">{"".join(parts)}</div>'


def _steps_html(steps: list[str]) -> str:
    return "".join(f'<div style="animation-delay:{idx * 260}ms">{_escape(_short_video_text(step, 44))}</div>' for idx, step in enumerate(steps))


def _short_video_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip("，。；、 ") + "…"


def _complexity_table_html(example: dict[str, Any]) -> str:
    rows = example.get("complexities")
    if not isinstance(rows, list) or not rows:
        rows = [
            {"operation": "按位访问", "complexity": "O(1)"},
            {"operation": "查找/遍历", "complexity": "O(n)"},
            {"operation": "插入/删除", "complexity": "O(n)"},
        ]
    body = "".join(
        f"<tr><td>{_escape((row or {}).get('operation') or '操作')}</td><td>{_escape((row or {}).get('complexity') or 'O(n)')}</td></tr>"
        for row in rows[:5]
        if isinstance(row, dict)
    )
    return f"<table><thead><tr><th>操作</th><th>复杂度</th></tr></thead><tbody>{body}</tbody></table>"


def _composition_html_v2(meta: dict[str, Any]) -> str:
    scene_markup = "\n".join(_scene_clip_html_v2(scene, index, meta) for index, scene in enumerate(meta["scenes"]))
    caption_markup = "\n".join(_caption_clip_html(scene, index, len(meta["scenes"])) for index, scene in enumerate(meta["scenes"]))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_escape(meta["title"])}</title>
  <style>
    @font-face {{
      font-family: "EduChinese";
      src: url("./assets/msyh.ttc") format("truetype");
      font-weight: 400 800;
      font-style: normal;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      width: {TARGET_WIDTH}px;
      height: {TARGET_HEIGHT}px;
      margin: 0;
      overflow: hidden;
      background: #08111f;
      color: #f8fafc;
      font-family: "EduChinese", Arial, sans-serif;
    }}
    #eduagent-video {{
      position: relative;
      width: {TARGET_WIDTH}px;
      height: {TARGET_HEIGHT}px;
      overflow: hidden;
      background: linear-gradient(135deg, #08111f 0%, #123154 48%, #16213a 100%);
    }}
    .grid {{
      position: absolute;
      inset: 0;
      opacity: 0.16;
      background-image: linear-gradient(rgba(191, 219, 254, 0.25) 1px, transparent 1px), linear-gradient(90deg, rgba(191, 219, 254, 0.25) 1px, transparent 1px);
      background-size: 52px 52px;
    }}
    .chrome {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 18px;
      height: 100%;
      padding: 34px 46px 28px;
    }}
    header {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 24px;
      align-items: start;
    }}
    .eyebrow {{
      margin-bottom: 8px;
      color: #93c5fd;
      font-size: 18px;
      font-weight: 700;
    }}
    h1 {{
      max-width: 840px;
      margin: 0;
      font-size: 40px;
      line-height: 1.16;
      letter-spacing: 0;
    }}
    .meta {{
      display: grid;
      gap: 8px;
      justify-items: end;
      color: #cbd5e1;
      font-size: 17px;
    }}
    .progress {{
      width: 260px;
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(148, 163, 184, 0.32);
    }}
    .progress span {{
      display: block;
      width: 100%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #38bdf8, #facc15);
      transform-origin: left center;
      animation: fill {TARGET_DURATION_SECONDS}s linear both;
    }}
    main {{
      position: relative;
      min-height: 0;
    }}
    .scene {{
      position: absolute;
      inset: 0;
      display: grid;
      grid-template-columns: 0.95fr 1.05fr;
      gap: 26px;
      opacity: 0;
      animation: sceneIn 0.65s ease both;
    }}
    .copy, .visual {{
      min-height: 0;
      border: 1px solid rgba(191, 219, 254, 0.25);
      border-radius: 8px;
      background: rgba(15, 23, 42, 0.78);
      box-shadow: 0 22px 58px rgba(2, 6, 23, 0.34);
    }}
    .copy {{
      display: grid;
      align-content: start;
      gap: 14px;
      padding: 26px;
    }}
    .time {{
      width: fit-content;
      padding: 7px 13px;
      border-radius: 999px;
      color: #bfdbfe;
      font-size: 16px;
      font-weight: 700;
      background: rgba(37, 99, 235, 0.36);
    }}
    h2 {{
      margin: 0;
      font-size: 34px;
      line-height: 1.18;
      letter-spacing: 0;
    }}
    .screen-text {{
      margin: 0;
      color: #e2e8f0;
      font-size: 23px;
      line-height: 1.54;
    }}
    .concepts {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .concepts span {{
      padding: 7px 10px;
      border: 1px solid rgba(250, 204, 21, 0.36);
      border-radius: 999px;
      color: #fef3c7;
      font-size: 16px;
      background: rgba(113, 63, 18, 0.28);
    }}
    .steps {{
      display: grid;
      gap: 7px;
      margin-top: 2px;
      color: #cbd5e1;
      font-size: 17px;
      line-height: 1.42;
    }}
    .source {{
      margin-top: 4px;
      color: #93c5fd;
      font-size: 15px;
      line-height: 1.42;
    }}
    .visual {{
      position: relative;
      overflow: hidden;
      padding: 26px;
    }}
    .visual-title {{
      position: relative;
      z-index: 2;
      margin: 0 0 16px;
      color: #bfdbfe;
      font-size: 20px;
      font-weight: 800;
    }}
    .knowledge-map {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      height: 300px;
      align-content: center;
    }}
    .knowledge-card, .operation-step, .practice-loop article {{
      border-radius: 8px;
      padding: 16px;
      animation: nodeRise 0.85s ease both;
    }}
    .knowledge-card {{
      border: 1px solid rgba(125, 211, 252, 0.28);
      background: rgba(8, 47, 73, 0.74);
    }}
    .knowledge-card strong, .operation-step b, .practice-loop strong {{
      display: block;
      margin-bottom: 8px;
      color: #ffffff;
      font-size: 20px;
    }}
    .knowledge-card span, .operation-step span, .practice-loop span {{
      color: #dbeafe;
      font-size: 16px;
      line-height: 1.44;
    }}
    .operation-flow {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      align-items: center;
      min-height: 300px;
    }}
    .operation-step {{
      min-height: 182px;
      border: 1px solid rgba(250, 204, 21, 0.34);
      background: rgba(69, 26, 3, 0.44);
    }}
    .operation-step b {{
      color: #fde68a;
    }}
    .operation-step span {{
      color: #fef3c7;
    }}
    .complexity-chart {{
      position: relative;
      z-index: 1;
      height: 300px;
      border-left: 2px solid rgba(226, 232, 240, 0.58);
      border-bottom: 2px solid rgba(226, 232, 240, 0.58);
      margin: 16px 18px 6px;
    }}
    .complexity-line {{
      position: absolute;
      left: 36px;
      right: 40px;
      bottom: 54px;
      height: 130px;
      border-bottom: 6px solid #60a5fa;
      border-right: 6px solid #60a5fa;
      transform: skewY(-18deg);
      transform-origin: bottom left;
      animation: chartGrow 3.5s ease both;
    }}
    .chart-label {{
      position: absolute;
      color: #dbeafe;
      font-size: 17px;
    }}
    .practice-loop {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      align-content: center;
      min-height: 300px;
    }}
    .practice-loop article {{
      min-height: 150px;
      border: 1px solid rgba(134, 239, 172, 0.3);
      background: rgba(20, 83, 45, 0.44);
    }}
    .practice-loop strong {{
      color: #bbf7d0;
    }}
    .practice-loop span {{
      color: #dcfce7;
    }}
    .caption {{
      position: relative;
      display: grid;
      gap: 6px;
      min-height: 88px;
      padding: 18px 22px;
      border: 1px solid rgba(148, 163, 184, 0.24);
      border-radius: 8px;
      background: rgba(2, 6, 23, 0.84);
    }}
    .caption strong {{
      color: #93c5fd;
      font-size: 16px;
    }}
    .caption span {{
      position: absolute;
      left: 22px;
      right: 22px;
      bottom: 16px;
      opacity: 0;
      color: #f8fafc;
      font-size: 22px;
      line-height: 1.42;
      animation: sceneIn 0.5s ease both;
    }}
    @keyframes fill {{
      from {{ transform: scaleX(0); }}
      to {{ transform: scaleX(1); }}
    }}
    @keyframes sceneIn {{
      from {{ opacity: 0; transform: translateY(12px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @keyframes nodeRise {{
      from {{ transform: translateY(18px); }}
      to {{ transform: translateY(0); }}
    }}
    @keyframes chartGrow {{
      from {{ clip-path: inset(0 100% 0 0); }}
      to {{ clip-path: inset(0 0 0 0); }}
    }}
  </style>
</head>
<body>
  <div id="eduagent-video" data-composition-id="eduagent-video" data-start="0" data-duration="{TARGET_DURATION_SECONDS}" data-width="{TARGET_WIDTH}" data-height="{TARGET_HEIGHT}" data-fps="{TARGET_FPS}">
    <div class="grid"></div>
    <div class="chrome">
      <header>
        <div>
          <div class="eyebrow">EduAgent Studio / HyperFrames 本地渲染</div>
          <h1>{_escape(meta["title"])}</h1>
        </div>
        <div class="meta">
          <span>{len(meta["scenes"])} 个分镜 / {TARGET_DURATION_SECONDS} 秒</span>
          <span>无音频版本：动态画面 + 字幕</span>
          <div class="progress"><span></span></div>
        </div>
      </header>
      <main>
{scene_markup}
      </main>
      <footer class="caption">
        <strong>鏃佺櫧瀛楀箷</strong>
{caption_markup}
      </footer>
    </div>
  </div>
  <script>
    const noopTimeline = {{
      seek() {{ return this; }},
      time() {{ return this; }},
      pause() {{ return this; }},
      progress() {{ return this; }},
      duration() {{ return {TARGET_DURATION_SECONDS}; }},
      totalDuration() {{ return {TARGET_DURATION_SECONDS}; }}
    }};
    window.__timelines = window.__timelines || {{}};
    window.__timelines["eduagent-video"] = noopTimeline;
    window.__playerReady = true;
  </script>
</body>
</html>
"""


def _scene_clip_html_v2(scene: dict[str, Any], index: int, meta: dict[str, Any]) -> str:
    start, duration = _scene_timing(scene, index, len(meta["scenes"]))
    concepts = "".join(f"<span>{_escape(item)}</span>" for item in scene.get("keyConcepts", [])[:5])
    steps = "".join(f"<div>{idx}. {_escape(step)}</div>" for idx, step in enumerate(scene.get("recordingSteps", [])[:4], start=1))
    source = _scene_source_text(scene, meta)
    visual = _scene_visual_html(scene, meta)
    scene_id = _safe_name(str(scene.get("id") or f"scene_{index + 1}"))
    return f"""        <section id="{scene_id}" class="scene clip" data-start="{start}" data-duration="{duration}" data-track-index="{index}">
          <section class="copy">
            <div class="time">{_escape(scene.get("timeRange") or scene.get("time") or "")}</div>
            <h2>{_escape(scene.get("screenTitle") or scene.get("title") or "")}</h2>
            <p class="screen-text">{_escape(scene.get("screenText") or "")}</p>
            <div class="concepts">{concepts}</div>
            <div class="steps">{steps}</div>
            <div class="source">{_escape(source)}</div>
          </section>
          <section class="visual">
            {visual}
          </section>
        </section>"""


def _scene_visual_html(scene: dict[str, Any], meta: dict[str, Any]) -> str:
    concepts = [str(item) for item in scene.get("keyConcepts", []) if str(item).strip()]
    steps = [str(item) for item in scene.get("recordingSteps", []) if str(item).strip()]
    title = _escape(scene.get("title") or scene.get("screenTitle") or "")
    kind = str(scene.get("kind") or "teaching")
    if kind == "operation":
        cards = []
        labels = ["操作前", "执行步骤", "操作后"]
        for idx, label in enumerate(labels):
            text = steps[idx] if idx < len(steps) else (concepts[idx] if idx < len(concepts) else scene.get("screenText", ""))
            cards.append(f'<article class="operation-step"><b>{_escape(label)}</b><span>{_escape(text)}</span></article>')
        return f'<p class="visual-title">{title}</p><div class="operation-flow">{"".join(cards)}</div>'
    if kind == "complexity":
        first = _escape(concepts[0] if concepts else scene.get("title", "复杂度"))
        second = _escape(concepts[1] if len(concepts) > 1 else scene.get("screenText", "瑙勬ā鍙樺寲"))
        return f'<p class="visual-title">{title}</p><div class="complexity-chart"><div class="complexity-line"></div><span class="chart-label" style="left: 16px; bottom: 8px;">鏁版嵁瑙勬ā n</span><span class="chart-label" style="right: 18px; top: 10px;">{first}</span><span class="chart-label" style="left: 88px; top: 92px;">{second}</span></div>'
    if kind == "practice":
        labels = ["练习", "测评", "路径补强"]
        cards = []
        for idx, label in enumerate(labels):
            text = steps[idx] if idx < len(steps) else (concepts[idx] if idx < len(concepts) else scene.get("screenText", ""))
            cards.append(f'<article><strong>{_escape(label)}</strong><span>{_escape(text)}</span></article>')
        return f'<p class="visual-title">{title}</p><div class="practice-loop">{"".join(cards)}</div>'
    cards = []
    source_bits = [scene.get("description") or "", _personalization_line(meta.get("personalization") or {})]
    values = [*concepts[:3], *[item for item in source_bits if str(item).strip()]]
    for idx, value in enumerate(values[:4]):
        heading = concepts[idx] if idx < len(concepts) else ("学习依据" if idx == 3 else "课程证据")
        cards.append(f'<article class="knowledge-card"><strong>{_escape(heading)}</strong><span>{_escape(value)}</span></article>')
    return f'<p class="visual-title">{title}</p><div class="knowledge-map">{"".join(cards)}</div>'


def _scene_source_text(scene: dict[str, Any], meta: dict[str, Any]) -> str:
    chunk_ids = set(str(item) for item in scene.get("citationChunkIds", []) if str(item).strip())
    for citation in meta.get("citations", []):
        if str(citation.get("chunkId") or "") in chunk_ids:
            return f"引用：{citation.get('documentName') or '课程资料'} / {citation.get('sourceLocation') or '课程片段'} / 第 {citation.get('page') or '-'} 页"
    return "引用：课程资料与 DeepSeek 教学内容校验"


def _personalization_line(personalization: dict[str, Any]) -> str:
    parts = [
        str(personalization.get("weakPoints") or "").strip(),
        str(personalization.get("activeStage") or "").strip(),
        str(personalization.get("resourcePreference") or "").strip(),
    ]
    return "；".join(part for part in parts if part)


def _composition_html(meta: dict[str, Any]) -> str:
    scene_markup = "\n".join(_scene_clip_html(scene, index, meta) for index, scene in enumerate(meta["scenes"]))
    caption_markup = "\n".join(_caption_clip_html(scene, index, len(meta["scenes"])) for index, scene in enumerate(meta["scenes"]))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{_escape(meta["title"])}</title>
  <style>
    @font-face {{
      font-family: "EduChinese";
      src: url("./assets/msyh.ttc") format("truetype");
      font-weight: 400 800;
      font-style: normal;
    }}
    :root {{
      color-scheme: dark;
      font-family: "EduChinese", Arial, sans-serif;
      background: #07111f;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{
      width: {TARGET_WIDTH}px;
      height: {TARGET_HEIGHT}px;
      margin: 0;
      overflow: hidden;
      background: #07111f;
    }}
    body {{
      display: grid;
      place-items: stretch;
      color: #f8fafc;
    }}
    #eduagent-video {{
      position: relative;
      width: {TARGET_WIDTH}px;
      height: {TARGET_HEIGHT}px;
      overflow: hidden;
      background:
        radial-gradient(circle at 75% 30%, rgba(45, 212, 191, 0.26), transparent 30%),
        radial-gradient(circle at 22% 70%, rgba(96, 165, 250, 0.28), transparent 34%),
        linear-gradient(135deg, #08111f 0%, #123154 52%, #0f172a 100%);
    }}
    .grid {{
      position: absolute;
      inset: 0;
      opacity: 0.18;
      background-image:
        linear-gradient(rgba(191, 219, 254, 0.24) 1px, transparent 1px),
        linear-gradient(90deg, rgba(191, 219, 254, 0.24) 1px, transparent 1px);
      background-size: 56px 56px;
    }}
    .chrome {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-rows: auto 1fr auto;
      gap: 20px;
      height: 100%;
      padding: 36px 52px 30px;
    }}
    header {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 24px;
      align-items: start;
    }}
    .eyebrow {{
      margin-bottom: 10px;
      color: #93c5fd;
      font-size: 18px;
      font-weight: 700;
    }}
    h1 {{
      max-width: 860px;
      margin: 0;
      font-size: 42px;
      line-height: 1.16;
      letter-spacing: 0;
    }}
    .meta {{
      display: grid;
      gap: 8px;
      justify-items: end;
      color: #cbd5e1;
      font-size: 18px;
    }}
    .progress {{
      width: 260px;
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(148, 163, 184, 0.32);
    }}
    .progress span {{
      display: block;
      width: 100%;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, #38bdf8, #facc15);
      transform-origin: left center;
      animation: fill 180s linear both;
    }}
    @keyframes fill {{
      from {{ transform: scaleX(0); }}
      to {{ transform: scaleX(1); }}
    }}
    main {{
      position: relative;
      display: grid;
      min-height: 0;
      align-items: stretch;
    }}
    .scene {{
      position: absolute;
      inset: 0;
      display: grid;
      grid-template-columns: 1.05fr 0.95fr;
      gap: 34px;
      opacity: 0;
      animation: sceneIn 0.7s ease both;
    }}
    .copy, .visual {{
      min-height: 0;
      border: 1px solid rgba(191, 219, 254, 0.24);
      border-radius: 8px;
      background: rgba(15, 23, 42, 0.72);
      box-shadow: 0 22px 60px rgba(2, 6, 23, 0.32);
    }}
    .copy {{
      display: grid;
      align-content: start;
      gap: 18px;
      padding: 30px;
    }}
    .time {{
      width: fit-content;
      padding: 8px 14px;
      border-radius: 999px;
      color: #bfdbfe;
      font-size: 17px;
      font-weight: 700;
      background: rgba(37, 99, 235, 0.36);
    }}
    h2 {{
      margin: 0;
      font-size: 38px;
      line-height: 1.2;
      letter-spacing: 0;
    }}
    .screen-text {{
      margin: 0;
      color: #e2e8f0;
      font-size: 25px;
      line-height: 1.58;
    }}
    .concepts {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .concepts span {{
      padding: 8px 12px;
      border: 1px solid rgba(250, 204, 21, 0.36);
      border-radius: 999px;
      color: #fef3c7;
      font-size: 17px;
      background: rgba(113, 63, 18, 0.28);
    }}
    .steps {{
      display: grid;
      gap: 8px;
      margin-top: 2px;
      color: #cbd5e1;
      font-size: 18px;
    }}
    .visual {{
      position: relative;
      overflow: hidden;
      padding: 30px;
    }}
    .orbit {{
      position: absolute;
      inset: 46px;
      border: 2px solid rgba(147, 197, 253, 0.26);
      border-radius: 50%;
      animation: rotateOrbit 16s linear infinite;
    }}
    .nodes {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      height: 100%;
      align-content: center;
    }}
    .node {{
      min-height: 96px;
      padding: 18px;
      border: 1px solid rgba(191, 219, 254, 0.22);
      border-radius: 8px;
      background: rgba(30, 64, 175, 0.44);
      animation: nodeRise 0.8s ease both;
    }}
    .node strong {{
      display: block;
      margin-bottom: 8px;
      color: #ffffff;
      font-size: 23px;
    }}
    .node span {{
      color: #bfdbfe;
      font-size: 17px;
      line-height: 1.45;
    }}
    .knowledge-map {{
      position: relative;
      z-index: 1;
      display: grid;
      gap: 14px;
      height: 100%;
      align-content: center;
    }}
    .knowledge-card {{
      border: 1px solid rgba(125, 211, 252, 0.28);
      border-radius: 8px;
      padding: 16px 18px;
      background: rgba(8, 47, 73, 0.7);
      animation: nodeRise 0.8s ease both;
    }}
    .knowledge-card strong {{
      display: block;
      color: #ffffff;
      font-size: 21px;
      margin-bottom: 8px;
    }}
    .knowledge-card span {{
      color: #bae6fd;
      font-size: 17px;
      line-height: 1.46;
    }}
    .operation-flow {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      align-items: center;
      height: 100%;
    }}
    .operation-step {{
      min-height: 172px;
      border: 1px solid rgba(250, 204, 21, 0.34);
      border-radius: 8px;
      padding: 18px 14px;
      background: rgba(69, 26, 3, 0.42);
      animation: nodeRise 0.8s ease both;
    }}
    .operation-step b {{
      display: block;
      margin-bottom: 10px;
      color: #fde68a;
      font-size: 20px;
    }}
    .operation-step span {{
      color: #fef3c7;
      font-size: 16px;
      line-height: 1.45;
    }}
    .complexity-chart {{
      position: relative;
      z-index: 1;
      height: 100%;
      min-height: 260px;
      border-left: 2px solid rgba(226, 232, 240, 0.5);
      border-bottom: 2px solid rgba(226, 232, 240, 0.5);
      margin: 20px 18px 10px;
    }}
    .complexity-line {{
      position: absolute;
      left: 28px;
      right: 28px;
      bottom: 46px;
      height: 120px;
      border-bottom: 5px solid #60a5fa;
      border-right: 5px solid #60a5fa;
      transform: skewY(-18deg);
      transform-origin: bottom left;
      animation: chartGrow 3.5s ease both;
    }}
    .chart-label {{
      position: absolute;
      color: #dbeafe;
      font-size: 18px;
    }}
    .practice-loop {{
      position: relative;
      z-index: 1;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      align-content: center;
      height: 100%;
    }}
    .practice-loop article {{
      min-height: 148px;
      border: 1px solid rgba(134, 239, 172, 0.3);
      border-radius: 8px;
      padding: 18px 14px;
      background: rgba(20, 83, 45, 0.44);
    }}
    .practice-loop strong {{
      display: block;
      margin-bottom: 10px;
      color: #bbf7d0;
      font-size: 20px;
    }}
    .practice-loop span {{
      color: #dcfce7;
      font-size: 16px;
      line-height: 1.45;
    }}
    .caption {{
      position: relative;
      display: grid;
      gap: 6px;
      min-height: 88px;
      padding: 18px 22px;
      border: 1px solid rgba(148, 163, 184, 0.24);
      border-radius: 8px;
      background: rgba(2, 6, 23, 0.82);
    }}
    .caption strong {{
      color: #93c5fd;
      font-size: 16px;
    }}
    .caption span {{
      position: absolute;
      left: 22px;
      right: 22px;
      bottom: 16px;
      opacity: 0;
      color: #f8fafc;
      font-size: 22px;
      line-height: 1.45;
      animation: sceneIn 0.5s ease both;
    }}
    @keyframes sceneIn {{
      from {{ opacity: 0; transform: translateY(12px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    @keyframes nodeRise {{
      from {{ transform: translateY(18px); }}
      to {{ transform: translateY(0); }}
    }}
    @keyframes rotateOrbit {{
      from {{ transform: rotate(0deg); }}
      to {{ transform: rotate(48deg); }}
    }}
    @keyframes chartGrow {{
      from {{ clip-path: inset(0 100% 0 0); }}
      to {{ clip-path: inset(0 0 0 0); }}
    }}
  </style>
</head>
<body>
  <div id="eduagent-video" data-composition-id="eduagent-video" data-start="0" data-duration="{TARGET_DURATION_SECONDS}" data-width="{TARGET_WIDTH}" data-height="{TARGET_HEIGHT}" data-fps="{TARGET_FPS}">
    <div class="grid"></div>
    <div class="chrome">
      <header>
        <div>
          <div class="eyebrow">EduAgent Studio / HyperFrames 本地渲染</div>
          <h1>{_escape(meta["title"])}</h1>
        </div>
        <div class="meta">
          <span>{len(meta["scenes"])} 个分镜 / {TARGET_DURATION_SECONDS} 秒</span>
          <div class="progress"><span></span></div>
        </div>
      </header>
      <main>
{scene_markup}
      </main>
      <footer class="caption">
        <strong>鏃佺櫧瀛楀箷</strong>
{caption_markup}
      </footer>
    </div>
  </div>
  <script>
    const noopTimeline = {{
      seek() {{ return this; }},
      time() {{ return this; }},
      pause() {{ return this; }},
      progress() {{ return this; }},
      duration() {{ return {TARGET_DURATION_SECONDS}; }},
      totalDuration() {{ return {TARGET_DURATION_SECONDS}; }}
    }};
    window.__timelines = window.__timelines || {{}};
    window.__timelines["eduagent-video"] = noopTimeline;
    window.__playerReady = true;
  </script>
</body>
</html>
"""


def _scene_clip_html(scene: dict[str, Any], index: int, meta: dict[str, Any]) -> str:
    start, duration = _scene_timing(scene, index, len(meta["scenes"]))
    concepts = "".join(f"<span>{_escape(item)}</span>" for item in scene.get("keyConcepts", [])[:5])
    steps = "".join(f"<div>{idx}. {_escape(step)}</div>" for idx, step in enumerate(scene.get("recordingSteps", [])[:3], start=1))
    nodes = [
        ("璇剧▼渚濇嵁", scene.get("agentEvidence") or meta.get("topic")),
        ("鐢婚潰绫诲瀷", scene.get("kind") or "teaching"),
        ("学生画像", (meta.get("personalization") or {}).get("weakPoints") or "待识别"),
        ("学习路径", (meta.get("personalization") or {}).get("activeStage") or "当前阶段"),
    ]
    node_markup = "".join(
        f"<article class=\"node\"><strong>{_escape(title)}</strong><span>{_escape(text)}</span></article>"
        for title, text in nodes
    )
    scene_id = _safe_name(str(scene.get("id") or f"scene_{index + 1}"))
    return f"""        <section id="{scene_id}" class="scene clip" data-start="{start}" data-duration="{duration}" data-track-index="{index}">
          <section class="copy">
            <div class="time">{_escape(scene.get("timeRange") or scene.get("time") or "")}</div>
            <h2>{_escape(scene.get("title") or "教学分镜")}</h2>
            <p class="screen-text">{_escape(scene.get("screenText") or scene.get("description") or "")}</p>
            <div class="concepts">{concepts}</div>
            <div class="steps">{steps}</div>
          </section>
          <section class="visual">
            <div class="orbit"></div>
            <div class="nodes">{node_markup}</div>
          </section>
        </section>"""


def _caption_clip_html(scene: dict[str, Any], index: int, scene_count: int) -> str:
    start, duration = _scene_timing(scene, index, scene_count)
    text = scene.get("voiceover") or scene.get("narration") or ""
    return f'        <span id="caption-{index + 1}" class="clip" data-start="{start}" data-duration="{duration}" data-track-index="{index + 20}">{_escape(text)}</span>'


def _scene_timing(scene: dict[str, Any], index: int, scene_count: int) -> tuple[int, int]:
    parsed = _parse_time_range(str(scene.get("timeRange") or scene.get("time") or ""))
    if parsed:
        return parsed
    base = TARGET_DURATION_SECONDS // max(1, scene_count)
    start = index * base
    end = TARGET_DURATION_SECONDS if index == scene_count - 1 else (index + 1) * base
    return start, max(1, end - start)


def _parse_time_range(value: str) -> tuple[int, int] | None:
    if "-" not in value:
        return None
    start, end = [item.strip() for item in value.split("-", 1)]
    start_seconds = _time_to_seconds(start)
    end_seconds = _time_to_seconds(end)
    if end_seconds <= start_seconds:
        return None
    return start_seconds, end_seconds - start_seconds


def _time_to_seconds(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 2:
        return 0
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return 0


def _strict_normalized_scenes(value: Any) -> list[dict[str, Any]]:
    scenes = value if isinstance(value, list) else []
    if not scenes:
        raise RuntimeError("HyperFrames composition 缺少 DeepSeek 分镜，已停止生成。")
    normalized = []
    fallback_duration = TARGET_DURATION_SECONDS // max(1, len(scenes))
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise RuntimeError(f"HyperFrames composition 第 {index + 1} 个分镜不是对象。")
        start = index * fallback_duration
        end = TARGET_DURATION_SECONDS if index == len(scenes) - 1 else (index + 1) * fallback_duration
        title = str(scene.get("title") or "").strip()
        screen_text = str(scene.get("screenText") or "").strip()
        voiceover = str(scene.get("voiceover") or scene.get("narration") or "").strip()
        concepts = _safe_string_list(scene.get("keyConcepts"))
        teaching_goal = str(scene.get("teachingGoal") or "").strip()
        core_explanation = str(scene.get("coreExplanation") or "").strip()
        visual_model = scene.get("visualModel") if isinstance(scene.get("visualModel"), dict) else {}
        example_data = scene.get("exampleData") if isinstance(scene.get("exampleData"), dict) else {}
        operation_steps = _safe_string_list(scene.get("operationSteps"))
        formula_or_complexity = str(scene.get("formulaOrComplexity") or "").strip()
        student_task = str(scene.get("studentTask") or "").strip()
        if (
            not title
            or not screen_text
            or not voiceover
            or not concepts
            or not teaching_goal
            or not core_explanation
            or not visual_model
            or not example_data
            or not operation_steps
            or not formula_or_complexity
            or not student_task
        ):
            raise RuntimeError(f"HyperFrames composition scene {index + 1} is missing knowledge-teaching fields.")
        normalized.append({
            "id": str(scene.get("id") or f"scene_{index + 1}"),
            "time": str(scene.get("time") or scene.get("timeRange") or f"{start // 60}:{start % 60:02d}-{end // 60}:{end % 60:02d}"),
            "timeRange": str(scene.get("timeRange") or scene.get("time") or f"{start // 60}:{start % 60:02d}-{end // 60}:{end % 60:02d}"),
            "kind": str(scene.get("kind") or "teaching"),
            "title": title,
            "screenTitle": str(scene.get("screenTitle") or title),
            "screenText": screen_text,
            "description": str(scene.get("description") or ""),
            "voiceover": voiceover,
            "narration": voiceover,
            "keyConcepts": concepts,
            "teachingGoal": teaching_goal,
            "coreExplanation": core_explanation,
            "visualModel": visual_model,
            "exampleData": example_data,
            "operationSteps": operation_steps,
            "formulaOrComplexity": formula_or_complexity,
            "studentTask": student_task,
            "recordingSteps": operation_steps,
            "citationChunkIds": _safe_string_list(scene.get("citationChunkIds")),
            "agentEvidence": str(scene.get("agentEvidence") or ""),
        })
    return normalized


def _normalized_scenes(value: Any) -> list[dict[str, Any]]:
    scenes = value if isinstance(value, list) else []
    if not scenes:
        scenes = [{"title": "教学视频", "screenText": "暂无分镜数据", "voiceover": "请稍后重新生成。"}]
    normalized = []
    fallback_duration = TARGET_DURATION_SECONDS // max(1, len(scenes))
    for index, scene in enumerate(scenes):
        scene = scene if isinstance(scene, dict) else {}
        start = index * fallback_duration
        end = TARGET_DURATION_SECONDS if index == len(scenes) - 1 else (index + 1) * fallback_duration
        normalized.append({
            "id": str(scene.get("id") or f"scene_{index + 1}"),
            "time": str(scene.get("time") or scene.get("timeRange") or f"{start // 60}:{start % 60:02d}-{end // 60}:{end % 60:02d}"),
            "timeRange": str(scene.get("timeRange") or scene.get("time") or f"{start // 60}:{start % 60:02d}-{end // 60}:{end % 60:02d}"),
            "kind": str(scene.get("kind") or "teaching"),
            "title": str(scene.get("title") or "教学分镜"),
            "screenText": str(scene.get("screenText") or scene.get("description") or ""),
            "description": str(scene.get("description") or ""),
            "voiceover": str(scene.get("voiceover") or scene.get("narration") or ""),
            "narration": str(scene.get("narration") or scene.get("voiceover") or ""),
            "keyConcepts": _safe_string_list(scene.get("keyConcepts")),
            "recordingSteps": _safe_string_list(scene.get("recordingSteps")),
            "agentEvidence": str(scene.get("agentEvidence") or ""),
        })
    return normalized


def _safe_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _command_output(command: list[str], error_message: str) -> str:
    try:
        result = subprocess.run(_resolved_command(command), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=error_message) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=503, detail=f"{error_message} 命令超时。") from exc
    if result.returncode != 0:
        detail = _compact_process_error(result)
        raise HTTPException(status_code=503, detail=f"{error_message} {detail}")
    return (result.stdout or result.stderr or "").strip()


def _resolved_command(command: list[str]) -> list[str]:
    executable = shutil.which(command[0])
    if not executable:
        raise FileNotFoundError(command[0])
    return [executable, *command[1:]]


def _hyperframes_env() -> dict[str, str]:
    env = dict(os.environ)
    ffmpeg_dir = str(_ascii_ffmpeg_dir())
    current_path = env.get("PATH", "")
    env["PATH"] = f"{ffmpeg_dir};{current_path}" if current_path else ffmpeg_dir
    env["HYPERFRAMES_FFMPEG_PATH"] = str(Path(ffmpeg_dir) / "ffmpeg.exe")
    return env


def _ascii_ffmpeg_dir() -> Path:
    ffmpeg_source = Path(ffmpeg_path())
    ffprobe_source = Path(ffprobe_path())
    cache_dir = Path(tempfile.gettempdir()) / "eduagent-hyperframes-ffmpeg"
    cache_dir.mkdir(parents=True, exist_ok=True)
    _copy_tool_if_needed(ffmpeg_source, cache_dir / "ffmpeg.exe")
    _copy_tool_if_needed(ffprobe_source, cache_dir / "ffprobe.exe")
    return cache_dir


def _bundled_ffprobe_path() -> Path | None:
    bundled = HYPERFRAMES_DIR / "ffmpeg-bin" / "ffprobe.exe"
    if bundled.exists():
        return bundled
    extracted = HYPERFRAMES_DIR / "ffmpeg-extract"
    matches = sorted(extracted.glob("**/ffprobe.exe")) if extracted.exists() else []
    return matches[0] if matches else None


def _copy_tool_if_needed(source: Path, target: Path) -> None:
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        shutil.copy2(source, target)


def _parse_node_major(value: str) -> int | None:
    match = re.search(r"v?(\d+)", value or "")
    return int(match.group(1)) if match else None


def _compact_process_error(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip()
    if not text:
        return f"退出码 {result.returncode}"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    meaningful_patterns = (
        "error",
        "failed",
        "invalid",
        "cannot",
        "could not",
        "nothing was written",
        "conversion failed",
    )
    meaningful = [line for line in lines if any(pattern in line.lower() for pattern in meaningful_patterns)]
    selected = meaningful[-8:] if meaningful else lines[-12:]
    compact = re.sub(r"\s+", " ", " ".join(selected)).strip()
    if len(compact) > 1400:
        compact = compact[-1400:]
    return compact or f"退出码 {result.returncode}"


def _ffmpeg_failure_context(
    result: subprocess.CompletedProcess[str],
    rendered_path: Path,
    agnes_clip_path: Path,
    filter_graph: str,
) -> list[str]:
    lines = [
        f"FFmpeg return code: {result.returncode}",
        f"Base video input: {rendered_path}",
        f"Agnes clip input: {agnes_clip_path}",
        f"Filter graph: {filter_graph}",
    ]
    output_lines = _process_log_lines(result)
    if output_lines:
        lines.append("FFmpeg stderr/stdout tail:")
        lines.extend(output_lines[-36:])
    return lines[-40:]


def _failure_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return str(exc) or exc.__class__.__name__


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
    job["status"] = status
    job["providerStatus"] = status
    job["progress"] = max(0, min(100, int(progress)))
    job["stageMessage"] = message
    job["updatedAt"] = _now()
    job["lastHeartbeatAt"] = _now()
    if composition_stage:
        job["compositionStage"] = composition_stage
    if status in {"rendering", "verifying"} and not job.get("compositionStartedAt"):
        job["compositionStartedAt"] = _now()
    if started and not job.get("startedAt"):
        job["startedAt"] = _now()
    _save_job(job)


def _fail_job(job_id: str, exc: Exception) -> None:
    job = _load_job(job_id)
    if not job:
        return
    detail = _failure_message(exc)
    job["status"] = "failed"
    job["providerStatus"] = "failed"
    job["progress"] = 100
    job["stageMessage"] = "生成失败，未产出可验证 MP4。"
    job["error"] = detail
    job["errorCode"] = exc.__class__.__name__
    job["errorDetail"] = detail
    job["schemaVersion"] = job.get("schemaVersion") or KNOWLEDGE_VIDEO_SCHEMA_VERSION
    job["generationAttemptId"] = job.get("generationAttemptId") or job_id
    job["isCurrentVideo"] = False
    job["canReusePreviousVideo"] = False
    if not job.get("renderLogTail"):
        job["renderLogTail"] = [line.strip() for line in detail.splitlines() if line.strip()][-40:]
    job["videoUrl"] = None
    job["videoDurationSeconds"] = None
    job["ffmpegNormalized"] = False
    job["finishedAt"] = _now()
    job["updatedAt"] = _now()
    _save_job(job)


def _merge_job_logs(job_id: str, lines: list[str]) -> None:
    if not lines:
        return
    job = _load_job(job_id)
    if not job:
        return
    current = job.get("renderLogTail") if isinstance(job.get("renderLogTail"), list) else []
    job["renderLogTail"] = [str(item) for item in [*current, *lines] if str(item).strip()][-40:]
    job["updatedAt"] = _now()
    _save_job(job)


def _process_log_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    text = "\n".join(part for part in [result.stdout, result.stderr] if part)
    return [line.strip() for line in text.splitlines() if line.strip()][-40:]


def _timeout_log_lines(exc: subprocess.TimeoutExpired) -> list[str]:
    parts = []
    for value in (exc.stdout, exc.stderr):
        if isinstance(value, bytes):
            parts.append(value.decode("utf-8", errors="replace"))
        elif value:
            parts.append(str(value))
    text = "\n".join(parts).strip()
    return [line.strip() for line in text.splitlines() if line.strip()][-40:]


def _job_work_dir(job_id: str) -> Path:
    path = Path(tempfile.gettempdir()) / "eduagent-hyperframes-jobs" / _safe_name(job_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_job(job_id: str) -> dict[str, Any] | None:
    return load_json(JOB_STORE_KEY, {}).get(job_id)


def _save_job(job: dict[str, Any]) -> None:
    jobs = load_json(JOB_STORE_KEY, {})
    jobs[str(job["jobId"])] = job
    save_json(JOB_STORE_KEY, jobs)


def _set_latest_attempt(resource_id: str, user_id: str, job_id: str) -> None:
    latest = load_json(LATEST_STORE_KEY, {})
    key = _latest_key(resource_id, user_id)
    record = _latest_record(resource_id, user_id)
    record["latestAttemptJobId"] = job_id
    latest[key] = record
    save_json(LATEST_STORE_KEY, latest)


def _set_last_successful(resource_id: str, user_id: str, job_id: str) -> None:
    latest = load_json(LATEST_STORE_KEY, {})
    key = _latest_key(resource_id, user_id)
    record = _latest_record(resource_id, user_id)
    record["latestAttemptJobId"] = record.get("latestAttemptJobId") or job_id
    record["lastSuccessfulJobId"] = job_id
    latest[key] = record
    save_json(LATEST_STORE_KEY, latest)


def _latest_record(resource_id: str, user_id: str) -> dict[str, str | None]:
    latest = load_json(LATEST_STORE_KEY, {})
    raw = latest.get(_latest_key(resource_id, user_id))
    if isinstance(raw, dict):
        return {
            "latestAttemptJobId": str(raw.get("latestAttemptJobId") or "") or None,
            "lastSuccessfulJobId": str(raw.get("lastSuccessfulJobId") or "") or None,
        }
    if isinstance(raw, str) and raw:
        job = _load_job(raw)
        last_successful = raw if job and job.get("status") == "completed" and job.get("videoUrl") else None
        return {"latestAttemptJobId": raw, "lastSuccessfulJobId": last_successful}
    return {"latestAttemptJobId": None, "lastSuccessfulJobId": None}


def _latest_key(resource_id: str, user_id: str) -> str:
    return f"{user_id or 'anonymous'}::{resource_id}"


def _mark_orphaned_if_stale(job: dict[str, Any]) -> None:
    if job.get("status") not in {"queued", "rendering", "verifying", "composing"}:
        return
    heartbeat = str(job.get("lastHeartbeatAt") or job.get("updatedAt") or "").strip()
    if not heartbeat:
        return
    try:
        last = datetime.strptime(heartbeat, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return
    timeout = int(job.get("compositionTimeoutSeconds") or 900)
    if (datetime.now() - last).total_seconds() <= timeout:
        return
    job["status"] = "orphaned"
    job["providerStatus"] = "orphaned"
    job["stageMessage"] = "HyperFrames 合成阶段超过心跳超时时间，已标记为可重试。"
    job["errorCode"] = "COMPOSITION_HEARTBEAT_TIMEOUT"
    job["errorDetail"] = f"最后心跳：{heartbeat}，超时阈值：{timeout} 秒。"
    job["updatedAt"] = _now()
    _save_job(job)


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: value
        for key, value in job.items()
        if key not in {"compositionPath", "workDir"}
    }
    schema_version = _job_schema_version(job)
    public["schemaVersion"] = schema_version
    public["generationAttemptId"] = public.get("generationAttemptId") or public.get("jobId")
    public["renderMode"] = public.get("renderMode") or "full_hybrid"
    public["renderProfile"] = public.get("renderProfile") or _render_profile(str(public["renderMode"]), None)
    public["compositionStartedAt"] = public.get("compositionStartedAt")
    public["lastHeartbeatAt"] = public.get("lastHeartbeatAt")
    public["compositionTimeoutSeconds"] = public.get("compositionTimeoutSeconds") or public["renderProfile"].get("timeoutSeconds")
    public["compositionStage"] = public.get("compositionStage") or str(public.get("status") or "queued")
    public["isCurrentVideo"] = bool(
        public.get("isCurrentVideo")
        and public.get("status") == "completed"
        and public.get("videoUrl")
        and schema_version == KNOWLEDGE_VIDEO_SCHEMA_VERSION
    )
    public["canReusePreviousVideo"] = schema_version == LEGACY_VIDEO_SCHEMA_VERSION
    public["sourceCitationIds"] = public.get("sourceCitationIds") or _source_citation_ids(public.get("citations") or [])
    return public


def _job_schema_version(job: dict[str, Any] | None) -> str:
    if not job:
        return KNOWLEDGE_VIDEO_SCHEMA_VERSION
    return str(job.get("schemaVersion") or LEGACY_VIDEO_SCHEMA_VERSION)


def _source_citation_ids(citations: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("chunkId")) for item in citations if isinstance(item, dict) and str(item.get("chunkId") or "").strip()]


def _content_hash(topic: Any, scenes: list[dict[str, Any]], citations: list[dict[str, Any]]) -> str:
    payload = {
        "topic": topic,
        "scenes": scenes,
        "citationIds": _source_citation_ids(citations),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _storyboard_leakage_score(value: Any) -> int:
    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else str(value or "")
    return sum(text.count(pattern) for pattern in STORYBOARD_LEAKAGE_PATTERNS)


def _safe_name(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))
    return safe[:64] or "video"


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
