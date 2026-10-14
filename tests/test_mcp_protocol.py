"""Real stdio protocol tests using an explicitly labelled fixture provider."""

import asyncio
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from scripts.mcp_client import normalize_result

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("group", ["F1", "F2", "P1", "P2", "P3"])
def test_real_stdio_contract_and_errors(group):
    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "tests.fixture_mcp_server"], cwd=str(ROOT), env={**os.environ, "KB_EXPERIMENT_ID": group, "PYTHONIOENCODING": "utf-8"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                assert {tool.name for tool in listing.tools} == {"kb_search", "kb_fetch"}
                schema = next(tool.inputSchema for tool in listing.tools if tool.name == "kb_search")
                assert "scope" in schema["properties"]
                hits = normalize_result(await session.call_tool("kb_search", {"query": "test", "scope": "ignored"}))
                assert isinstance(hits, list) and len(hits) == 1
                hit = hits[0]
                fetched = normalize_result(await session.call_tool("kb_fetch", {"node_id": hit["node_id"]}))
                assert fetched["node_id"] == (hit["parent_id"] or hit["node_id"])
                assert fetched["text"] == ("fixture parent context" if group.startswith("P") else hit["text"])
                assert set(fetched["neighbors"]) == {"parent", "children", "siblings"}
                for query, k in [("", 5), (" ", 5), ("test", 0), ("test", -1), ("test", True), ("test", 1.5)]:
                    result = await session.call_tool("kb_search", {"query": query, "k": k})
                    assert result.isError
                    with pytest.raises(Exception):
                        normalize_result(result)
                invalid = await session.call_tool("kb_fetch", {"node_id": "fixture-parent"})
                assert invalid.isError
                with pytest.raises(Exception):
                    normalize_result(invalid)
    asyncio.run(run())
