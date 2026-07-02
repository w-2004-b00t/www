from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from typing import Any

from ..env import load_backend_env


class LLMUnavailable(RuntimeError):
    """Raised when the configured LLM cannot return a usable response."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "llm_unavailable",
        retryable: bool = True,
        public_message: str = "大模型服务暂时不可用，请稍后重试。",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.public_message = public_message


class LLMJsonError(LLMUnavailable):
    """Raised when an LLM response cannot be parsed as a JSON object."""

    def __init__(self, reason_code: str, message: str, *, raw_excerpt: str = "") -> None:
        super().__init__(
            message,
            code=reason_code,
            retryable=True,
            public_message="模型返回内容格式不完整，请重新生成。",
        )
        self.reason_code = reason_code
        self.raw_excerpt = raw_excerpt


load_backend_env()


def llm_enabled() -> bool:
    return os.getenv("LLM_ENABLE", "false").lower() in {"1", "true", "yes", "on"}


def llm_model_name() -> str:
    return os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


def _configuration_error(message: str, code: str) -> LLMUnavailable:
    return LLMUnavailable(
        message,
        code=code,
        retryable=False,
        public_message="大模型配置未完成，请联系管理员检查服务配置。",
    )


def _http_error(exc: urllib.error.HTTPError) -> LLMUnavailable:
    detail = exc.read().decode("utf-8", errors="ignore")[:500]
    normalized = detail.lower()
    if exc.code in {401, 403}:
        return LLMUnavailable(
            f"DeepSeek HTTP {exc.code}: {detail}",
            code="deepseek_auth_error",
            retryable=False,
            public_message="DeepSeek 鉴权失败，请检查 API Key。",
        )
    if exc.code == 402 or "insufficient balance" in normalized or "余额" in detail:
        return LLMUnavailable(
            f"DeepSeek HTTP {exc.code}: {detail}",
            code="deepseek_balance_insufficient",
            retryable=False,
            public_message="DeepSeek 账户余额不足，请充值或更换 API Key。",
        )
    if exc.code == 429:
        return LLMUnavailable(
            f"DeepSeek HTTP 429: {detail}",
            code="deepseek_rate_limited",
            retryable=True,
            public_message="DeepSeek 请求过于频繁，请稍后重试。",
        )
    return LLMUnavailable(
        f"DeepSeek HTTP {exc.code}: {detail}",
        code=f"deepseek_http_{exc.code}",
        retryable=exc.code >= 500,
        public_message="DeepSeek 服务返回异常，请稍后重试。",
    )


def _network_error(exc: BaseException) -> LLMUnavailable:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return LLMUnavailable(
            f"DeepSeek timeout: {exc}",
            code="deepseek_timeout",
            retryable=True,
            public_message="DeepSeek 响应超时，请重新生成。",
        )
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return LLMUnavailable(
            f"DeepSeek timeout: {reason}",
            code="deepseek_timeout",
            retryable=True,
            public_message="DeepSeek 响应超时，请重新生成。",
        )
    return LLMUnavailable(
        f"DeepSeek network error: {reason}",
        code="deepseek_network_error",
        retryable=True,
        public_message="无法连接 DeepSeek，请检查网络后重试。",
    )


def _require_llm_configuration() -> tuple[str, str]:
    if not llm_enabled():
        raise _configuration_error("LLM_ENABLE is not true", "llm_disabled")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise _configuration_error("DEEPSEEK_API_KEY is missing", "deepseek_api_key_missing")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    return api_key, base_url


def _chat_request(payload: dict[str, Any]) -> urllib.request.Request:
    api_key, base_url = _require_llm_configuration()
    return urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )


def _json_error_reason(text: str, exc: json.JSONDecodeError | None = None) -> str:
    stripped = text.strip()
    if not stripped or "{" not in stripped:
        return "json_not_found"
    if "}" not in stripped:
        return "json_truncated"
    if stripped.count("{") > stripped.count("}") or stripped.count("[") > stripped.count("]"):
        return "json_truncated"
    if exc and "control character" in exc.msg.lower():
        return "json_escape_error"
    if stripped.find("{") > 0 or stripped.rfind("}") < len(stripped) - 1:
        return "json_extra_text"
    return "json_malformed"


def _clean_json_candidate(text: str) -> str:
    cleaned = text.strip().lstrip("\ufeff")
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.S | re.I)
    if fence:
        cleaned = fence.group(1).strip()
    cleaned = cleaned.replace("\r", "")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned.strip()


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    return text[start:end + 1].strip()


def parse_llm_json(content: str) -> dict[str, Any]:
    text = _clean_json_candidate(content)
    candidates = [text]
    extracted = _extract_json_object(text)
    if extracted and extracted not in candidates:
        candidates.append(extracted)
    first_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            first_error = first_error or exc
    else:
        reason = _json_error_reason(text, first_error)
        raise LLMJsonError(
            reason,
            f"LLM response JSON parse failed: {reason}",
            raw_excerpt=text[:500],
        ) from first_error
    if not isinstance(value, dict):
        raise LLMJsonError("json_root_not_object", "LLM response root must be an object", raw_excerpt=text[:500])
    return value


def call_deepseek_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 3000,
    timeout: int = 35,
) -> dict[str, Any]:
    payload = {
        "model": llm_model_name(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    request = _chat_request(payload)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise _network_error(exc) from exc

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(
            "DeepSeek response envelope is not JSON",
            code="deepseek_response_malformed",
            public_message="DeepSeek 返回内容异常，请重新生成。",
        ) from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailable("DeepSeek response has no message content") from exc
    return parse_llm_json(content)


def stream_deepseek_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 3000,
    timeout: int = 60,
):
    payload = {
        "model": llm_model_name(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    request = _chat_request(payload)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_parts: list[str] = []
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    payload_line = json.loads(data)
                    delta = payload_line["choices"][0].get("delta", {}).get("content", "")
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    continue
                if delta:
                    content_parts.append(delta)
                    yield {"type": "delta", "content": delta}
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise _network_error(exc) from exc

    final_content = "".join(content_parts)
    yield {"type": "result", "content": parse_llm_json(final_content)}


def stream_deepseek_text(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.2,
    max_tokens: int = 1800,
    timeout: int = 60,
):
    payload = {
        "model": llm_model_name(),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    request = _chat_request(payload)
    received = False
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    payload_line = json.loads(data)
                    delta = payload_line["choices"][0].get("delta", {}).get("content", "")
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    continue
                if delta:
                    received = True
                    yield delta
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise _network_error(exc) from exc
    if not received:
        raise LLMUnavailable(
            "DeepSeek stream returned no text content",
            code="deepseek_empty_response",
            retryable=True,
            public_message="DeepSeek 没有返回有效内容，请重新生成。",
        )
