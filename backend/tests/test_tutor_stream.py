from __future__ import annotations

import json
import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.routers import tutor
from app.schemas import TutorChatRequest, TutorExtraRequest
from app.services.llm_service import LLMUnavailable, _http_error


def parse_sse(chunks: list[str]) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in "".join(chunks).split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        event = next(line[6:].strip() for line in lines if line.startswith("event:"))
        data = next(json.loads(line[5:].strip()) for line in lines if line.startswith("data:"))
        events.append((event, data))
    return events


class TutorStreamRouterTest(unittest.IsolatedAsyncioTestCase):
    async def collect_response(self, response) -> list[str]:
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return chunks

    async def test_stream_returns_status_delta_and_done(self) -> None:
        context = {
            "retrieval": {"coverage": "sufficient"},
            "citations": [{"chunkId": "chunk-1"}],
        }
        done = {
            "answer": "线性表是一种线性结构。",
            "citations": context["citations"],
            "confidence": 0.88,
            "inferred": False,
            "generationMode": "rag_llm",
            "llm": {"model": "deepseek-v4-flash"},
        }
        with (
            patch.object(tutor, "prepare_tutor_stream", return_value=context),
            patch.object(tutor, "stream_tutor_answer", return_value=iter(["线性表", "是一种线性结构。"])),
            patch.object(tutor, "tutor_stream_result", return_value=done),
        ):
            response = tutor.tutor_chat_stream(TutorChatRequest(message="什么是线性表"), None)
            events = parse_sse(await self.collect_response(response))

        self.assertEqual([event for event, _ in events], ["status", "status", "status", "delta", "delta", "done"])
        self.assertEqual(events[-1][1]["answer"], done["answer"])

    async def test_stream_exposes_safe_retryable_error(self) -> None:
        error = LLMUnavailable(
            "secret provider detail",
            code="deepseek_timeout",
            retryable=True,
            public_message="DeepSeek 响应超时，请重新生成。",
        )
        with patch.object(tutor, "prepare_tutor_stream", side_effect=error):
            response = tutor.tutor_chat_stream(TutorChatRequest(message="什么是栈"), None)
            events = parse_sse(await self.collect_response(response))

        self.assertEqual(events[-1][0], "error")
        self.assertEqual(events[-1][1]["code"], "deepseek_timeout")
        self.assertTrue(events[-1][1]["retryable"])
        self.assertNotIn("secret provider detail", events[-1][1]["message"])

    def test_legacy_chat_endpoint_remains_compatible(self) -> None:
        with patch.object(tutor, "answer_tutor_question", return_value={"answer": "兼容回答"}):
            response = tutor.tutor_chat(TutorChatRequest(message="问题"), None)
        self.assertEqual(response["code"], 0)
        self.assertEqual(response["data"]["answer"], "兼容回答")

    def test_extra_endpoint_returns_requested_payload(self) -> None:
        generated = {"type": "diagram", "diagram": {"markdown": "# 线性表"}}
        with patch.object(tutor, "generate_tutor_extra", return_value=generated):
            response = tutor.tutor_extras(
                TutorExtraRequest(message="线性表", answer="回答", type="diagram"),
                None,
            )
        self.assertEqual(response["data"]["type"], "diagram")
        self.assertIn("requestId", response["data"])


class LLMErrorClassificationTest(unittest.TestCase):
    def test_auth_error_is_not_retryable(self) -> None:
        error = urllib.error.HTTPError(
            "https://api.deepseek.com/chat/completions",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":"invalid api key"}'),
        )
        classified = _http_error(error)
        self.assertEqual(classified.code, "deepseek_auth_error")
        self.assertFalse(classified.retryable)

    def test_rate_limit_is_retryable(self) -> None:
        error = urllib.error.HTTPError(
            "https://api.deepseek.com/chat/completions",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b'{"error":"rate limited"}'),
        )
        classified = _http_error(error)
        self.assertEqual(classified.code, "deepseek_rate_limited")
        self.assertTrue(classified.retryable)


if __name__ == "__main__":
    unittest.main()
