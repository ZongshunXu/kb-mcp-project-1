"""Small async DeepSeek Chat Completions transport; no SDK or implicit retries."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from typing import Any
from urllib.parse import urlsplit

import httpx


class DeepSeekError(RuntimeError):
    """A request or response failed; messages never include HTTP bodies or keys."""


class DeepSeekConfigError(DeepSeekError):
    """The optional agent cannot run with the supplied environment configuration."""


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    api_key: str = field(repr=False)
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    timeout: float = 60.0
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise DeepSeekConfigError("BLOCKED: set DEEPSEEK_API_KEY to run the DeepSeek agent.")
        if any(char.isspace() for char in self.api_key.strip()):
            raise DeepSeekConfigError("BLOCKED: DEEPSEEK_API_KEY contains whitespace.")
        if not isinstance(self.model, str) or not self.model.strip():
            raise DeepSeekConfigError("BLOCKED: DEEPSEEK_MODEL must be nonempty.")
        if not isinstance(self.base_url, str):
            raise DeepSeekConfigError("BLOCKED: DEEPSEEK_BASE_URL must be an HTTP(S) base URL.")
        try:
            parsed = urlsplit(self.base_url.strip())
            valid_url = (parsed.scheme in {"http", "https"} and parsed.hostname
                         and not parsed.username and not parsed.password
                         and not parsed.query and not parsed.fragment)
            parsed.port  # Reject an invalid port before sending any request.
        except ValueError:
            valid_url = False
        if not valid_url:
            raise DeepSeekConfigError("BLOCKED: DEEPSEEK_BASE_URL must be HTTP(S), without credentials, query or fragment.")
        if (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float))
                or not math.isfinite(self.timeout) or self.timeout <= 0):
            raise DeepSeekConfigError("BLOCKED: DEEPSEEK_TIMEOUT must be a finite positive number of seconds.")
        if type(self.max_tokens) is not int or self.max_tokens <= 0:
            raise DeepSeekConfigError("BLOCKED: DEEPSEEK_MAX_TOKENS must be a positive integer.")
        object.__setattr__(self, "api_key", self.api_key.strip())
        object.__setattr__(self, "model", self.model.strip())
        object.__setattr__(self, "base_url", self.base_url.strip().rstrip("/"))

    @classmethod
    def from_env(cls) -> "DeepSeekConfig":
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key.strip():
            raise DeepSeekConfigError("BLOCKED: set DEEPSEEK_API_KEY to run the DeepSeek agent.")
        try:
            timeout = float(os.environ.get("DEEPSEEK_TIMEOUT", "60"))
            max_tokens = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "4096"))
        except ValueError:
            raise DeepSeekConfigError("BLOCKED: invalid DEEPSEEK_TIMEOUT or DEEPSEEK_MAX_TOKENS.") from None
        return cls(api_key=key, model=os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
                   base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                   timeout=timeout, max_tokens=max_tokens)

    def safe_summary(self) -> dict:
        """Configuration suitable for CLI display, excluding the API key."""
        return _redact({"model": self.model, "base_url": self.base_url,
                        "timeout": self.timeout, "max_tokens": self.max_tokens}, self.api_key)


def _redact(value: Any, api_key: str) -> Any:
    if isinstance(value, str):
        return value.replace(api_key, "[REDACTED]")
    if isinstance(value, list):
        return [_redact(item, api_key) for item in value]
    if isinstance(value, dict):
        return {_redact(key, api_key): _redact(item, api_key) for key, item in value.items()}
    return value


def _validate_response(payload: Any) -> dict:
    """Check fields consumed by the runner while preserving provider metadata."""
    def invalid() -> None:
        raise DeepSeekError("DeepSeek returned an invalid Chat Completions response schema.")

    if not isinstance(payload, dict):
        invalid()
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        invalid()
    choice = choices[0]
    if not isinstance(choice.get("finish_reason"), str) or not choice["finish_reason"]:
        invalid()
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant" or "content" not in message:
        invalid()
    content = message["content"]
    if content is not None and not isinstance(content, str):
        invalid()
    reasoning = message.get("reasoning_content")
    if reasoning is not None and not isinstance(reasoning, str):
        invalid()
    tool_calls = message.get("tool_calls")
    if tool_calls is not None:
        if not isinstance(tool_calls, list):
            invalid()
        for call in tool_calls:
            if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]:
                invalid()
            function = call.get("function")
            if call.get("type") != "function" or not isinstance(function, dict):
                invalid()
            if (not isinstance(function.get("name"), str) or not function["name"]
                    or not isinstance(function.get("arguments"), str)):
                invalid()
    if content is None and not tool_calls:
        invalid()
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        invalid()
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if type(usage.get(name)) is not int or usage[name] < 0:
            invalid()
    return payload


class DeepSeekClient:
    def __init__(self, config: DeepSeekConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "DeepSeekClient":
        if self._client is not None:
            raise DeepSeekError("DeepSeekClient is already open.")
        self._client = httpx.AsyncClient(timeout=self.config.timeout, transport=self._transport,
                                        follow_redirects=False, trust_env=False)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(self, messages: list[dict], tools: list[dict], *, tool_choice="auto") -> dict:
        if self._client is None:
            raise DeepSeekError("Use DeepSeekClient inside an async with block.")
        if (not isinstance(messages, list) or not messages or not all(isinstance(item, dict) for item in messages)
                or not isinstance(tools, list) or not all(isinstance(item, dict) for item in tools)):
            raise DeepSeekError("DeepSeek messages and tools must be lists of objects; messages cannot be empty.")
        payload = {"model": self.config.model, "messages": messages, "tools": tools,
                   "tool_choice": tool_choice, "stream": False, "thinking": {"type": "disabled"},
                   "max_tokens": self.config.max_tokens}
        try:
            response = await self._client.post(
                self.config.base_url + "/chat/completions", json=payload,
                headers={"Authorization": "Bearer " + self.config.api_key})
        except httpx.TimeoutException:
            raise DeepSeekError("DeepSeek request timed out; check network and DEEPSEEK_TIMEOUT.") from None
        except httpx.HTTPError:
            raise DeepSeekError("DeepSeek request failed; check the configured endpoint and network.") from None
        except (TypeError, ValueError):
            raise DeepSeekError("DeepSeek request could not be encoded or sent.") from None
        if not 200 <= response.status_code < 300:
            # Provider bodies and headers may echo credentials; never include them.
            raise DeepSeekError(f"DeepSeek HTTP {response.status_code}; check credentials, quota and endpoint.")
        try:
            result = response.json()
        except ValueError:
            raise DeepSeekError("DeepSeek returned a non-JSON response.") from None
        return _redact(_validate_response(result), self.config.api_key)
