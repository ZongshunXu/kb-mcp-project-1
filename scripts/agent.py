"""Run the minimal DeepSeek tool-calling Agent over the real MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent.deepseek import DeepSeekClient, DeepSeekConfig, DeepSeekConfigError
from agent.runner import AgentLimits, AgentRunner, error_text, redact
from scripts.mcp_client import EXPERIMENT_IDS, ROOT, connect


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def positive_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be finite and > 0")
    return number


def show_result(result) -> None:
    if result.status == "OK":
        print("\n" + result.answer)
        print("\n已 Fetch 的来源：")
        for source in result.sources:
            print(f"- {source['uri']} | node_id={source['node_id']}")
    else:
        print(f"{result.status}: {result.error}", file=sys.stderr)


async def run_cli(args, config, traces: dict) -> int:
    limits = AgentLimits(args.max_turns, args.max_tool_calls,
                         args.model_timeout or config.timeout, args.mcp_timeout)
    exit_code = 0
    try:
        async with DeepSeekClient(config) as model:
            async with connect(args.group, initialize_timeout=args.mcp_timeout) as session:
                while True:
                    if args.query is not None:
                        query = args.query
                    else:
                        try:
                            query = await asyncio.to_thread(input, "\n问题（exit/quit 退出）：")
                        except EOFError:
                            break
                        if query.strip().lower() in {"exit", "quit"}:
                            break
                        if not query.strip():
                            continue
                    runner = AgentRunner(session, model, limits=limits, secrets=(config.api_key,))
                    # Keep a live partial trace for connection/cancellation errors.
                    traces["runs"].append(runner.trace)
                    result = await runner.run(query)
                    show_result(result)
                    exit_code = max(exit_code, result.exit_code)
                    if args.query is not None or result.status in {"MCP_ERROR", "MCP_TIMEOUT"}:
                        break
    except Exception as exc:
        message = redact(error_text(exc), (config.api_key,))
        print("AGENT_ERROR: " + message, file=sys.stderr)
        traces["error"] = message
        exit_code = max(exit_code, 1)
    traces["exit_code"] = exit_code
    return exit_code


def write_trace(traces: dict, requested: str | None) -> Path:
    if requested and requested != "auto":
        path = Path(requested)
        if not path.is_absolute():
            path = ROOT / path
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = ROOT / "logs" / f"agent-{timestamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(traces, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", help="One question; omit to use the interactive terminal")
    parser.add_argument("--group", choices=EXPERIMENT_IDS, default="P1")
    parser.add_argument("--max-turns", type=positive_int, default=8)
    parser.add_argument("--max-tool-calls", "--max-calls", dest="max_tool_calls", type=positive_int, default=24)
    parser.add_argument("--model-timeout", type=positive_float, default=None)
    parser.add_argument("--mcp-timeout", type=positive_float, default=30.0)
    parser.add_argument("--trace", nargs="?", const="auto", help="Save redacted JSON; optional path, default logs/")
    parser.add_argument("--check-config", action="store_true", help="Check configuration without starting MCP or calling a model")
    args = parser.parse_args(argv)
    if args.query is not None and not args.query.strip():
        parser.error("--query must be non-empty")
    try:
        config = DeepSeekConfig.from_env()
    except DeepSeekConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.check_config:
        print(json.dumps({"status": "CONFIG_OK", **config.safe_summary()}, ensure_ascii=False))
        return 0
    print(redact(f"DeepSeek Agent | group={args.group} | model={config.model}", (config.api_key,)), file=sys.stderr)
    traces = {"schema_version": 1, "kind": "deepseek_mcp_agent_cli", "group": args.group,
              "config": config.safe_summary(), "runs": []}
    try:
        exit_code = asyncio.run(run_cli(args, config, traces))
    except KeyboardInterrupt:
        traces.update(exit_code=130, error="Interrupted by user")
        print("已停止 Agent。", file=sys.stderr)
        exit_code = 130
    if args.trace is not None or exit_code:
        try:
            path = write_trace(redact(traces, (config.api_key,)), args.trace)
            print(f"调用轨迹：{path}", file=sys.stderr)
        except OSError as exc:
            print("TRACE_ERROR: " + redact(str(exc), (config.api_key,)), file=sys.stderr)
            exit_code = exit_code or 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
