from __future__ import annotations

import sys
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import agnes_video_service
from app.services import local_open_video_service


class AgnesVideoJobTest(unittest.TestCase):
    def test_start_job_does_not_wait_for_remote_task_creation(self) -> None:
        saved_jobs: list[dict] = []

        with (
            patch.object(agnes_video_service, "validate_agnes_video_configuration"),
            patch.object(agnes_video_service, "_create_agnes_video_task") as create_remote,
            patch.object(agnes_video_service, "_save_job", side_effect=lambda job: saved_jobs.append(dict(job))),
            patch.object(agnes_video_service, "_set_latest_attempt"),
        ):
            result = agnes_video_service.start_agnes_video_job(
                resource_id="resource-1",
                user_id="user-1",
                title="线性表教学",
                topic="线性表",
                scenes=[{"title": "认识线性表"}],
                citations=[{"chunkId": "chunk-1"}],
            )

        create_remote.assert_not_called()
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["providerStatus"], "local_queued")
        self.assertIsNone(result["providerVideoId"])
        self.assertNotIn("prompt", result)
        self.assertEqual(len(saved_jobs), 1)
        self.assertTrue(saved_jobs[0]["prompt"])

    def test_background_submission_persists_remote_task_id(self) -> None:
        job = {
            "jobId": "agnes-local-1",
            "prompt": "生成线性表教学视频",
            "agentTrace": ["已创建本地视频任务"],
        }
        saved_jobs: list[dict] = []

        with (
            patch.object(agnes_video_service, "_load_job", return_value=job),
            patch.object(agnes_video_service, "_update_job_stage"),
            patch.object(
                agnes_video_service,
                "_create_agnes_video_task",
                return_value={"data": {"task_id": "agnes-remote-1"}},
            ),
            patch.object(agnes_video_service, "_save_job", side_effect=lambda value: saved_jobs.append(dict(value))),
        ):
            task_id = agnes_video_service._submit_agnes_task_for_job("agnes-local-1")

        self.assertEqual(task_id, "agnes-remote-1")
        self.assertEqual(saved_jobs[-1]["providerVideoId"], "agnes-remote-1")
        self.assertEqual(saved_jobs[-1]["providerStatus"], "queued")
        self.assertIn("Agnes AI 已返回远端任务 ID", saved_jobs[-1]["agentTrace"])

    def test_completed_response_uses_remixed_from_video_id(self) -> None:
        payload = {
            "id": "task-1",
            "video_id": "video-1",
            "status": "completed",
            "progress": 100,
            "remixed_from_video_id": "https://cdn.example.com/video.mp4",
        }
        self.assertEqual(
            agnes_video_service._remote_video_url(payload),
            "https://cdn.example.com/video.mp4",
        )
        self.assertEqual(agnes_video_service._video_id(payload), "video-1")

    def test_completed_remote_task_moves_directly_to_download(self) -> None:
        job = {
            "jobId": "agnes-1",
            "providerTaskId": "task-1",
            "providerVideoId": None,
            "status": "rendering",
            "pollCount": 3,
            "segmentProgress": {},
        }
        saved: list[dict] = []
        remote = {
            "id": "task-1",
            "video_id": "video-1",
            "status": "completed",
            "progress": 100,
            "remixed_from_video_id": "https://cdn.example.com/video.mp4",
        }
        with (
            patch.object(agnes_video_service, "_load_job", return_value=job),
            patch.object(agnes_video_service, "_get_agnes_video_task", return_value=remote),
            patch.object(agnes_video_service, "_save_job", side_effect=lambda value: saved.append(dict(value))),
        ):
            agnes_video_service._poll_remote_job("agnes-1")
        self.assertEqual(saved[-1]["status"], "downloading")
        self.assertEqual(saved[-1]["providerVideoId"], "video-1")
        self.assertEqual(saved[-1]["remoteVideoUrl"], "https://cdn.example.com/video.mp4")

    def test_ssl_eof_is_retried(self) -> None:
        request = httpx.Request("GET", "https://example.com")
        response = httpx.Response(200, json={"status": "queued"}, request=request)
        client = MagicMock()
        client.request.side_effect = [
            httpx.ReadError("[SSL: UNEXPECTED_EOF_WHILE_READING]", request=request),
            response,
        ]
        with (
            patch.object(agnes_video_service, "_client", return_value=client),
            patch.object(agnes_video_service.time, "sleep"),
        ):
            result = agnes_video_service._request_with_retries(
                "GET",
                "https://example.com",
                timeout=httpx.Timeout(5),
                attempts=2,
            )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(client.request.call_count, 2)

    def test_idempotent_start_reuses_running_job(self) -> None:
        existing = {
            "jobId": "agnes-existing",
            "resourceId": "resource-1",
            "userId": "user-1",
            "status": "rendering",
            "schemaVersion": "knowledge_video_v2",
            "videoUrl": None,
            "ffmpegNormalized": False,
            "citations": [],
        }
        with (
            patch.object(agnes_video_service, "validate_agnes_video_configuration"),
            patch.object(agnes_video_service, "find_video_job_by_idempotency", return_value=existing),
            patch.object(agnes_video_service, "_save_job") as save_job,
        ):
            result = agnes_video_service.start_agnes_video_job(
                resource_id="resource-1",
                user_id="user-1",
                title="线性表教学",
                topic="线性表",
                scenes=[{"title": "线性表"}],
            )
        save_job.assert_called_once()
        self.assertEqual(result["jobId"], "agnes-existing")
        self.assertEqual(result["reuseReason"], "running_job")
        self.assertEqual(result["renderMode"], "full_hybrid")

    def test_legacy_completed_remote_failure_becomes_recoverable(self) -> None:
        legacy = {
            "jobId": "agnes-old",
            "resourceId": "resource-1",
            "userId": "user-1",
            "status": "failed",
            "contentHash": "hash",
            "remoteTask": {
                "status": "completed",
                "video_id": "video-old",
                "remixed_from_video_id": "https://cdn.example.com/old.mp4",
            },
        }
        normalized = agnes_video_service._normalize_legacy_job(legacy)
        self.assertEqual(normalized["status"], "orphaned")
        self.assertTrue(normalized["retryable"])
        self.assertEqual(normalized["remoteVideoUrl"], "https://cdn.example.com/old.mp4")

    def test_agnes_overlay_filter_contains_tall_clip_inside_fixed_box(self) -> None:
        graph = local_open_video_service._agnes_overlay_filter_graph(384, 216)

        self.assertIn("scale=384:216:force_original_aspect_ratio=decrease", graph)
        self.assertIn("pad=384:216:(ow-iw)/2:(oh-ih)/2:black", graph)
        self.assertIn("setsar=1[agnes]", graph)
        self.assertNotIn("scale=384:-2", graph)

    def test_agnes_overlay_filter_keeps_wide_clip_letterboxed(self) -> None:
        graph = local_open_video_service._agnes_overlay_filter_graph(320, 180)

        self.assertIn("scale=320:180:force_original_aspect_ratio=decrease", graph)
        self.assertIn("pad=320:180:(ow-iw)/2:(oh-ih)/2:black", graph)

    def test_compact_process_error_keeps_actionable_ffmpeg_tail(self) -> None:
        stderr = "\n".join(
            [
                "ffmpeg version 7.1-essentials_build-www.gyan.dev Copyright (c) 2000-2024",
                "configuration: " + "--enable-libx264 " * 120,
                "[Parsed_pad_1 @ 000001] Padded dimensions cannot be smaller than input dimensions.",
                "[Parsed_pad_1 @ 000001] Failed to configure input pad on Parsed_pad_1",
                "Conversion failed!",
            ]
        )
        result = subprocess.CompletedProcess(["ffmpeg"], 1, stdout="", stderr=stderr)

        compact = local_open_video_service._compact_process_error(result)

        self.assertIn("Padded dimensions cannot be smaller than input dimensions", compact)
        self.assertIn("Conversion failed", compact)
        self.assertNotIn("--enable-libx264 --enable-libx264 --enable-libx264", compact)


if __name__ == "__main__":
    unittest.main()
