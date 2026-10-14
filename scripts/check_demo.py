"""Check the fixed-data demo over real MCP; never writes formal reports."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from scripts.demo_server import DEMO_NODE_ID, DEMO_TEXT, DEMO_URI, MODE
from scripts.mcp_client import normalize_result

ROOT = Path(__file__).resolve().parents[1]


async def check() -> dict:
    environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    environment.pop("DEEPSEEK_API_KEY", None)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "scripts.demo_server"],
        cwd=ROOT,
        env=environment,
    )
    checks = {}
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)
            checks["initialize"] = True
            listing = await asyncio.wait_for(session.list_tools(), timeout=20)
            tools = {tool.name: tool for tool in listing.tools}
            checks["exactly_two_tools"] = set(tools) == {"kb_search", "kb_fetch"}
            checks["scope_in_schema"] = "scope" in tools["kb_search"].inputSchema.get("properties", {})
            hits = normalize_result(await asyncio.wait_for(
                session.call_tool("kb_search", {"query": "离职后如何回收权限？", "k": 5, "scope": "ignored"}), timeout=20,
            ), expected_type=list)
            checks["flat_search"] = hits == [{"node_id": DEMO_NODE_ID, "parent_id": None, "uri": DEMO_URI, "text": DEMO_TEXT, "score": 1.0}]
            fetched = normalize_result(await asyncio.wait_for(
                session.call_tool("kb_fetch", {"node_id": hits[0]["node_id"]}), timeout=20,
            ))
            checks["fetch_via_search_id"] = fetched == {
                "node_id": DEMO_NODE_ID, "uri": DEMO_URI, "text": DEMO_TEXT,
                "neighbors": {"parent": None, "children": [], "siblings": []},
            }
            invalid = await asyncio.wait_for(session.call_tool("kb_fetch", {"node_id": "invalid-demo-node"}), timeout=20)
            checks["invalid_id_is_error"] = invalid.isError is True
            empty = await asyncio.wait_for(session.call_tool("kb_search", {"query": "", "k": 5}), timeout=20)
            checks["empty_query_is_error"] = empty.isError is True
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "mode": MODE, "transport": "stdio", "real_mcp_client": True,
        "formal_evaluation": False, "checks": checks, "hits": hits, "fetched": fetched,
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        report = asyncio.run(check())
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
            error = error.exceptions[0]
        report = {"status": "FAIL", "mode": MODE, "formal_evaluation": False, "error": f"{type(error).__name__}: {error}"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
