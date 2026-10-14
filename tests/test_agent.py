"""Mock model choices over a real MCP stdio fixture; never calls DeepSeek."""

import asyncio
from contextlib import asynccontextmanager
import copy
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from agent.runner import AgentLimits, AgentRunner

ROOT = Path(__file__).resolve().parents[1]


def tool_call(name, arguments, call_id="fixture-call"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments),
    }}


def response(*calls, answer=None, reasoning=None):
    message = {"role": "assistant", "content": answer}
    if calls:
        message["tool_calls"] = list(calls)
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def search(query="fixture question", call_id="search"):
    return tool_call("kb_search", {"query": query, "scope": "reserved-and-ignored"}, call_id)


def fetch(node_id="fixture-child", call_id="fetch"):
    return tool_call("kb_fetch", {"node_id": node_id}, call_id)


class FixtureModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def complete(self, messages, tools, *, tool_choice="auto"):
        self.requests.append(copy.deepcopy({"messages": messages, "tools": tools, "tool_choice": tool_choice}))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return await item()
        return item


@asynccontextmanager
async def fixture_connect(group="P1", **kwargs):
    env = {**os.environ, "KB_EXPERIMENT_ID": group, "PYTHONIOENCODING": "utf-8"}
    env.pop("DEEPSEEK_API_KEY", None)
    params = StdioServerParameters(command=sys.executable, args=["-m", "tests.fixture_agent_mcp_server"],
                                  cwd=str(ROOT), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def run_fixture(model, **kwargs):
    async def run():
        async with fixture_connect() as session:
            return await AgentRunner(session, model, **kwargs).run("fixture question")
    return asyncio.run(run())


def test_real_mcp_model_loop_preserves_reasoning_and_tool_call_ids():
    model = FixtureModel([response(search(), reasoning="fixture reasoning"), response(fetch()),
                          response(answer="Fixture answer [fixture.md#fixture-parent]")])
    result = run_fixture(model)
    assert result.status == "OK" and result.exit_code == 0
    assert result.sources == [{"uri": "fixture.md", "node_id": "fixture-parent",
                               "search_node_ids": ["fixture-child"]}]
    assert {tool["function"]["name"] for tool in model.requests[0]["tools"]} == {"kb_search", "kb_fetch"}
    assert "scope" in next(tool["function"]["parameters"]["properties"] for tool in model.requests[0]["tools"]
                           if tool["function"]["name"] == "kb_search")
    second = model.requests[1]["messages"]
    assert second[2]["reasoning_content"] == "fixture reasoning"
    assert second[3]["role"] == "tool" and second[3]["tool_call_id"] == "search"
    assert json.loads(second[3]["content"])[0]["node_id"] == "fixture-child"
    third = model.requests[2]["messages"]
    assert third[-1]["tool_call_id"] == "fetch"
    assert json.loads(third[-1]["content"])["text"] == "fixture parent original context"
    assert result.trace["actual_mcp_calls"] == 2


def test_fetch_parent_dedup_records_cached_result_without_second_mcp_call():
    model = FixtureModel([response(search()), response(fetch(), fetch("fixture-child-2", "fetch-2")),
                          response(answer="Fixture answer")])
    result = run_fixture(model)
    events = [event for event in result.trace["events"] if event["kind"] == "tool"]
    assert result.status == "OK"
    assert [event["cached"] for event in events] == [False, False, True]
    assert events[-1]["actual_mcp"] is False
    assert events[-1]["cache_from_tool_call_id"] == "fetch"
    assert result.trace["actual_mcp_calls"] == 2
    assert result.sources[0]["search_node_ids"] == ["fixture-child", "fixture-child-2"]


@pytest.mark.parametrize("invalid", [tool_call("unknown_tool", {}), fetch(),
    tool_call("kb_search", {"query": "fixture", "k": True}),
    {"id": "invalid-json", "type": "function", "function": {"name": "kb_search", "arguments": "not JSON"}}])
def test_invalid_model_calls_return_tool_error_and_never_retrieve(invalid):
    model = FixtureModel([response(invalid), response(answer="Unsupported fixture answer")])
    result = run_fixture(model)
    assert result.status == "UNSUPPORTED" and result.answer == ""
    assert result.trace["actual_mcp_calls"] == 0
    assert json.loads(model.requests[1]["messages"][-1]["content"])["isError"] is True


def test_parent_id_is_rejected_then_valid_child_fetch_can_recover():
    model = FixtureModel([response(search()), response(fetch("fixture-parent", "wrong-parent")),
                          response(fetch(call_id="valid-child")), response(answer="Recovered fixture answer")])
    result = run_fixture(model)
    assert result.status == "OK" and result.trace["actual_mcp_calls"] == 2
    assert "parent_id" in json.loads(model.requests[2]["messages"][-1]["content"])["error"]


def test_server_tool_error_is_returned_to_model_and_can_recover():
    model = FixtureModel([response(search("fixture-tool-error", "bad-search")), response(search()),
                          response(fetch()), response(answer="Recovered fixture answer")])
    result = run_fixture(model)
    assert result.status == "OK" and result.trace["actual_mcp_calls"] == 3
    assert json.loads(model.requests[1]["messages"][-1]["content"])["isError"] is True


def test_final_answer_without_fetch_is_not_reported_as_grounded():
    model = FixtureModel([response(search()), response(answer="Confident but unsupported fixture answer")])
    result = run_fixture(model)
    assert result.status == "UNSUPPORTED" and result.exit_code == 2
    assert result.answer == "" and result.sources == []


@pytest.mark.parametrize("finish_reason", ["length", "content_filter", "aborted", "insufficient_system_resource"])
def test_filtered_or_incomplete_answer_is_not_success_even_after_fetch(finish_reason):
    final = response(answer="Nonempty incomplete fixture answer")
    final["choices"][0]["finish_reason"] = finish_reason
    result = run_fixture(FixtureModel([response(search()), response(fetch()), final]))
    assert result.status == "MODEL_ERROR" and result.exit_code == 1
    assert result.answer == "" and result.sources
    assert finish_reason in result.error


def test_round_cap_and_tool_cap_keep_partial_trace():
    result = run_fixture(FixtureModel([response(search())]), limits=AgentLimits(max_turns=1))
    assert result.status == "TURN_LIMIT" and result.exit_code == 1
    assert any(event["kind"] == "tool" for event in result.trace["events"])
    result = run_fixture(FixtureModel([response(search(), fetch())]), limits=AgentLimits(max_tool_calls=1))
    assert result.status == "TOOL_LIMIT" and result.trace["actual_mcp_calls"] == 1


def test_model_timeout_and_error_are_not_answers_and_are_redacted():
    async def slow_model():
        await asyncio.sleep(.1)
        return response(answer="late")
    result = run_fixture(FixtureModel([slow_model]), limits=AgentLimits(model_timeout=.01))
    assert result.status == "MODEL_TIMEOUT" and result.answer == ""
    secret = "unit-test-credential-never-use"
    result = run_fixture(FixtureModel([RuntimeError("fixture failure " + secret)]), secrets=(secret,))
    assert result.status == "MODEL_ERROR" and secret not in json.dumps(result.trace)


def test_mcp_timeout_and_disconnection_keep_partial_trace(monkeypatch, tmp_path):
    from scripts import agent as cli

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key-not-real")
    monkeypatch.setattr(cli, "connect", fixture_connect)
    model = FixtureModel([response(search("fixture-slow"))])
    monkeypatch.setattr(cli, "DeepSeekClient", lambda config: model)
    path = tmp_path / "fixture-timeout-trace.json"
    # SDK 1.18 may also report a BrokenResourceError when a late fixture
    # response races with stdio teardown. The CLI must preserve the original
    # timeout trace and exit nonzero even when that outer cleanup also fails.
    assert cli.main(["--query", "fixture question", "--mcp-timeout", ".03", "--trace", str(path)]) == 1
    trace = json.loads(path.read_text(encoding="utf-8"))
    assert trace["runs"][0]["status"] == "MCP_TIMEOUT"
    assert trace["runs"][0]["events"][-1]["status"] == "MCP_TIMEOUT"
    result = run_fixture(FixtureModel([response(search("fixture-disconnect"))]))
    assert result.status == "MCP_ERROR" and result.answer == ""
    assert result.trace["events"][-1]["status"] == "MCP_ERROR"


def test_cli_missing_key_and_check_config_never_start_mcp(monkeypatch, capsys):
    from scripts import agent as cli

    def forbidden(*args, **kwargs):
        pytest.fail("Configuration checks must not start MCP or a model request")
    monkeypatch.setattr(cli, "connect", forbidden)
    monkeypatch.setattr(cli, "DeepSeekClient", forbidden)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert cli.main(["--query", "fixture question"]) == 2
    assert "BLOCKED" in capsys.readouterr().err
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key-not-real")
    assert cli.main(["--check-config"]) == 0
    output = capsys.readouterr().out
    assert "CONFIG_OK" in output and "fixture-key-not-real" not in output


def test_cli_model_failure_writes_redacted_partial_trace(monkeypatch, tmp_path, capsys):
    from scripts import agent as cli

    secret = "fixture-key-never-contact-provider"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    monkeypatch.setattr(cli, "connect", fixture_connect)
    model = FixtureModel([response(search()), RuntimeError("fixture model error " + secret)])
    monkeypatch.setattr(cli, "DeepSeekClient", lambda config: model)
    path = tmp_path / "fixture-agent-trace.json"
    assert cli.main(["--query", "fixture question", "--trace", str(path)]) == 1
    trace_text = path.read_text(encoding="utf-8")
    assert secret not in trace_text
    assert json.loads(trace_text)["runs"][0]["status"] == "MODEL_ERROR"
    assert any(event["kind"] == "tool" for event in json.loads(trace_text)["runs"][0]["events"])
    assert secret not in capsys.readouterr().err


def test_cli_header_redacts_key_even_if_model_is_misconfigured_as_key(monkeypatch, capsys):
    from scripts import agent as cli

    secret = "fixture-key-misconfigured-as-model"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    monkeypatch.setenv("DEEPSEEK_MODEL", secret)
    monkeypatch.setattr(cli, "connect", fixture_connect)
    model = FixtureModel([response(search()), response(fetch()), response(answer="Fixture answer")])
    monkeypatch.setattr(cli, "DeepSeekClient", lambda config: model)
    assert cli.main(["--query", "fixture question"]) == 0
    output = capsys.readouterr()
    assert secret not in output.out + output.err


def test_cli_interactive_question_then_exit_uses_real_mcp_fixture(monkeypatch, tmp_path, capsys):
    from scripts import agent as cli

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-key-not-real")
    monkeypatch.setattr(cli, "connect", fixture_connect)
    model = FixtureModel([response(search()), response(fetch()), response(answer="Fixture terminal answer")])
    monkeypatch.setattr(cli, "DeepSeekClient", lambda config: model)
    questions = iter(["fixture question", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(questions))
    path = tmp_path / "fixture-interactive-trace.json"
    assert cli.main(["--trace", str(path)]) == 0
    output = capsys.readouterr().out
    assert "Fixture terminal answer" in output and "fixture.md" in output
    assert len(json.loads(path.read_text(encoding="utf-8"))["runs"]) == 1
