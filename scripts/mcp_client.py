"""The shared, real stdio MCP client; never imports the retrieval implementation."""

from __future__ import annotations

import json
import asyncio
import math
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_IDS = ("F1", "F2", "P1", "P2", "P3")


class MCPToolError(RuntimeError):
    """An error returned by tools/call, rather than a successful empty result."""


def normalize_result(result: Any, *, expected_type: type | None = None) -> list | dict:
    """Unwrap SDK 1.18 structured results or JSON in TextContent blocks."""
    if getattr(result, "isError", False):
        messages = [getattr(block, "text", "") for block in result.content]
        raise MCPToolError("MCP tools/call failed: " + "\n".join(messages))
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload = structured
        # FastMCP wraps non-object return annotations in {"result": ...}.
        if isinstance(payload, dict) and set(payload) == {"result"}:
            payload = payload["result"]
        if isinstance(payload, dict) and {"node_id", "parent_id", "uri", "text", "score"}.issubset(payload):
            payload = [payload]
        if isinstance(payload, (list, dict)):
            return payload
        raise MCPToolError("MCP structuredContent is not a list or object")
    blocks = [getattr(block, "text", "") for block in result.content
              if getattr(block, "type", None) == "text"]
    if not blocks:
        if expected_type is list:
            return []
        raise MCPToolError("MCP result has neither structuredContent nor JSON text")
    try:
        payload = json.loads("\n".join(blocks))
    except (ValueError, TypeError):
        # Some SDK/tool combinations emit one JSON object per TextContent.
        try:
            payloads = [json.loads(block) for block in blocks]
        except (ValueError, TypeError) as exc:
            raise MCPToolError("MCP TextContent is not valid JSON") from exc
        payload = payloads if len(payloads) > 1 else payloads[0]
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    if isinstance(payload, dict) and {"node_id", "parent_id", "uri", "text", "score"}.issubset(payload):
        payload = [payload]
    if not isinstance(payload, (list, dict)):
        raise MCPToolError("MCP JSON result is not a list or object")
    return payload


@asynccontextmanager
async def connect(
    experiment_id: str,
    root: Path = ROOT,
    extra_env: dict[str, str] | None = None,
    initialize_timeout: float = 30.0,
) -> AsyncIterator[ClientSession]:
    """Start one mode-specific server with the current Python interpreter."""
    if experiment_id not in EXPERIMENT_IDS:
        raise ValueError(f"Unknown experiment ID: {experiment_id}")
    if (isinstance(initialize_timeout, bool) or not isinstance(initialize_timeout, (int, float))
            or not math.isfinite(initialize_timeout) or initialize_timeout <= 0):
        raise ValueError("initialize_timeout must be finite and positive")
    env = dict(os.environ)
    env.update(extra_env or {})
    # The local retrieval server does not call the hosted answer model.
    env.pop("DEEPSEEK_API_KEY", None)
    env["KB_EXPERIMENT_ID"] = experiment_id
    env["PYTHONIOENCODING"] = "utf-8"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kb.mcp_server"],
        cwd=Path(root).resolve(),
        env=env,
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await asyncio.wait_for(session.initialize(), timeout=initialize_timeout)
            yield session
