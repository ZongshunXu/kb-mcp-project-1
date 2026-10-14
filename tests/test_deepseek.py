"""Offline transport tests; these do not claim a real DeepSeek API run."""

import asyncio
from copy import deepcopy
import json
import traceback

import httpx
import pytest

from agent.deepseek import DeepSeekClient, DeepSeekConfig, DeepSeekConfigError, DeepSeekError

KEY = "sk-offline-test-secret"
MESSAGES = [{"role": "user", "content": "检索问题"}]
TOOLS = [{"type": "function", "function": {"name": "kb_search", "parameters": {"type": "object"}}}]


def response_payload():
    return {"id": "offline-example", "object": "chat.completion", "model": "deepseek-flash",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "答复"}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25,
                      "prompt_cache_hit_tokens": 3}}


def call_with(handler, *, config=None, messages=None, tools=None, tool_choice="auto"):
    async def run():
        async with DeepSeekClient(config or DeepSeekConfig(KEY), transport=httpx.MockTransport(handler)) as client:
            return await client.complete(messages or MESSAGES, TOOLS if tools is None else tools,
                                         tool_choice=tool_choice)
    return asyncio.run(run())


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL", "DEEPSEEK_TIMEOUT", "DEEPSEEK_MAX_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_missing_key_is_blocked(clean_env):
    with pytest.raises(DeepSeekConfigError, match="BLOCKED.*DEEPSEEK_API_KEY"):
        DeepSeekConfig.from_env()


def test_configuration_defaults_and_overrides_are_safe(clean_env):
    clean_env.setenv("DEEPSEEK_API_KEY", KEY)
    config = DeepSeekConfig.from_env()
    assert config.safe_summary() == {"model": "deepseek-flash", "base_url": "https://api.deepseek.com",
                                     "timeout": 60.0, "max_tokens": 4096}
    assert KEY not in repr(config)
    clean_env.setenv("DEEPSEEK_MODEL", "custom-model")
    clean_env.setenv("DEEPSEEK_BASE_URL", "https://configured.example/v1/")
    clean_env.setenv("DEEPSEEK_TIMEOUT", "12.5")
    clean_env.setenv("DEEPSEEK_MAX_TOKENS", "500")
    overridden = DeepSeekConfig.from_env()
    assert overridden.safe_summary() == {"model": "custom-model", "base_url": "https://configured.example/v1",
                                        "timeout": 12.5, "max_tokens": 500}


@pytest.mark.parametrize("name,value", [("DEEPSEEK_TIMEOUT", "nan"), ("DEEPSEEK_TIMEOUT", "0"),
                                      ("DEEPSEEK_MAX_TOKENS", "-1"), ("DEEPSEEK_MAX_TOKENS", KEY),
                                      ("DEEPSEEK_MODEL", " "),
                                      ("DEEPSEEK_BASE_URL", "file:///local"),
                                      ("DEEPSEEK_BASE_URL", f"https://{KEY}@configured.example"),
                                      ("DEEPSEEK_BASE_URL", f"https://configured.example?key={KEY}")])
def test_invalid_configuration_never_echoes_values(clean_env, name, value):
    clean_env.setenv("DEEPSEEK_API_KEY", KEY)
    clean_env.setenv(name, value)
    with pytest.raises(DeepSeekConfigError) as caught:
        DeepSeekConfig.from_env()
    assert "BLOCKED" in str(caught.value)
    assert KEY not in "".join(traceback.format_exception(caught.value))


def test_request_uses_only_configured_endpoint_and_preserves_response(caplog):
    observed = []
    expected = response_payload()
    def handler(request):
        observed.append(request)
        assert str(request.url) == "https://configured.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer " + KEY
        assert request.extensions["timeout"]["read"] == 9
        body = json.loads(request.content)
        assert body == {"model": "custom-model", "messages": MESSAGES, "tools": TOOLS,
                        "tool_choice": "required", "stream": False,
                        "thinking": {"type": "disabled"}, "max_tokens": 100}
        return httpx.Response(200, json=expected)
    result = call_with(handler, config=DeepSeekConfig(KEY, "custom-model", "https://configured.example/v1", 9, 100),
                       tool_choice="required")
    assert len(observed) == 1
    assert result == expected
    assert KEY not in caplog.text


def test_tool_call_and_reasoning_are_preserved_for_next_request():
    expected = response_payload()
    expected["choices"][0].update(finish_reason="tool_calls", message={
        "role": "assistant", "content": None, "reasoning_content": "optional provider field",
        "tool_calls": [{"id": "call-1", "type": "function",
                        "function": {"name": "kb_search", "arguments": '{"query":"权限"}'}}]})
    result = call_with(lambda request: httpx.Response(200, json=expected))
    assert result == expected
    history = MESSAGES + [result["choices"][0]["message"],
                          {"role": "tool", "tool_call_id": "call-1", "content": "[]"}]
    def next_handler(request):
        assert json.loads(request.content)["messages"][1]["reasoning_content"] == "optional provider field"
        return httpx.Response(200, json=response_payload())
    call_with(next_handler, messages=history)


@pytest.mark.parametrize("status", [301, 401, 429, 500])
def test_http_errors_do_not_leak_body_headers_or_follow_redirects(status):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, json={"error": {"message": "Bearer " + KEY}},
                              headers={"Location": "https://unrequested.example", "X-Error": KEY})
    with pytest.raises(DeepSeekError, match=f"HTTP {status}") as caught:
        call_with(handler)
    rendered = "".join(traceback.format_exception(caught.value))
    assert KEY not in rendered and "Bearer" not in rendered and "X-Error" not in rendered
    assert calls == ["https://api.deepseek.com/chat/completions"]


@pytest.mark.parametrize("error_type,message", [(httpx.ReadTimeout, "timed out"),
                                               (httpx.ConnectError, "request failed")])
def test_network_exception_chain_is_sanitized(error_type, message):
    def handler(request):
        raise error_type("Authorization: Bearer " + KEY, request=request)
    with pytest.raises(DeepSeekError, match=message) as caught:
        call_with(handler)
    rendered = "".join(traceback.format_exception(caught.value))
    assert KEY not in rendered and "Authorization" not in rendered
    assert caught.value.__suppress_context__


def test_non_json_response_cannot_echo_key():
    with pytest.raises(DeepSeekError, match="non-JSON") as caught:
        call_with(lambda request: httpx.Response(200, text=KEY))
    assert KEY not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("mutation", [
    lambda payload: payload.update(choices=[]),
    lambda payload: payload.update(choices=[{}]),
    lambda payload: payload.update(usage=None),
    lambda payload: payload["usage"].update(prompt_tokens=True),
    lambda payload: payload["usage"].update(total_tokens=-1),
    lambda payload: payload["choices"][0]["message"].update(role="user"),
    lambda payload: payload["choices"][0]["message"].update(content=123),
    lambda payload: payload["choices"][0]["message"].update(content=None),
    lambda payload: payload["choices"][0]["message"].update(reasoning_content={"secret": KEY}),
    lambda payload: payload["choices"][0]["message"].update(tool_calls=[{"id": KEY}]),
])
def test_malformed_response_schema_is_rejected_without_body(mutation):
    payload = deepcopy(response_payload())
    payload["echoed_secret"] = KEY
    mutation(payload)
    with pytest.raises(DeepSeekError, match="response schema") as caught:
        call_with(lambda request: httpx.Response(200, json=payload))
    assert KEY not in "".join(traceback.format_exception(caught.value))


def test_success_response_also_redacts_echoed_credentials():
    payload = response_payload()
    payload["choices"][0]["message"]["content"] = "echo " + KEY
    payload["metadata"] = {KEY: ["Bearer " + KEY]}
    result = call_with(lambda request: httpx.Response(200, json=payload))
    assert KEY not in json.dumps(result)
    assert result["choices"][0]["message"]["content"] == "echo [REDACTED]"


def test_client_requires_context_and_closes_after_exit():
    async def run():
        client = DeepSeekClient(DeepSeekConfig(KEY), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=response_payload())))
        with pytest.raises(DeepSeekError, match="async with"):
            await client.complete(MESSAGES, TOOLS)
        async with client:
            underlying = client._client
            await client.complete(MESSAGES, TOOLS)
        assert underlying.is_closed
        with pytest.raises(DeepSeekError, match="async with"):
            await client.complete(MESSAGES, TOOLS)
    asyncio.run(run())
