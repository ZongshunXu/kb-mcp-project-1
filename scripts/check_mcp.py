"""Check every experiment through real MCP stdio subprocesses."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from scripts.mcp_client import connect, normalize_result

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("F1", "F2", "P1", "P2", "P3")


def nodes(path: str | None) -> dict:
    if not path:
        return {}
    with (ROOT / path).open(encoding="utf-8") as handle:
        return {row["node_id"]: row for row in map(json.loads, handle)}


async def check_group(group: str, query: str, manifest: dict) -> dict:
    outcome = {"group": group, "status": "PASS", "checks": {}}
    checks = outcome["checks"]
    try:
        async with connect(group, root=ROOT) as session:
            listing = await session.list_tools()
            tools = {tool.name: tool for tool in listing.tools}
            checks["initialize"] = True
            checks["tools_exactly_two"] = set(tools) == {"kb_search", "kb_fetch"}
            checks["scope_in_schema"] = "scope" in tools["kb_search"].inputSchema.get("properties", {})
            if not all(checks.values()):
                raise AssertionError("MCP tool schema mismatch")
            invalid = await session.call_tool("kb_search", {"query": "", "k": 5})
            checks["empty_query_is_error"] = invalid.isError is True
            invalid = await session.call_tool("kb_search", {"query": query, "k": 0})
            checks["invalid_k_is_error"] = invalid.isError is True
            invalid = await session.call_tool("kb_fetch", {"node_id": "invalid-node-id"})
            checks["invalid_id_is_error"] = invalid.isError is True
            if not all(checks.values()):
                raise AssertionError("MCP validation errors were not reported")
            raw = await session.call_tool("kb_search", {"query": query, "k": 5, "scope": "ignored"})
            if manifest.get("status") != "READY":
                checks["unloaded_index_is_error"] = raw.isError is True
                if not checks["unloaded_index_is_error"]:
                    raise AssertionError("Missing index must produce an error")
                outcome.update(status="BLOCKED", reason="Index is not READY; initialize/schema/error checks passed")
                return outcome
            hits = normalize_result(raw)
            assert isinstance(hits, list) and 0 < len(hits) <= 5
            spec = manifest["experiments"][group]
            search_nodes = nodes(spec["search_nodes"])
            parent_nodes = nodes(spec["parent_nodes"])
            relations = json.loads((ROOT / spec["relations"]).read_text(encoding="utf-8")) if spec["relations"] else {}
            checks["search_topk_and_order"] = all(hits[i]["score"] >= hits[i + 1]["score"] for i in range(len(hits) - 1))
            fetches = []
            for hit in hits:
                stored = search_nodes[hit["node_id"]]
                assert hit["uri"] == stored["uri"] and hit["text"] == stored["text"]
                expected_parent = relations.get(hit["node_id"])
                assert hit["parent_id"] == expected_parent
                fetched = normalize_result(await session.call_tool("kb_fetch", {"node_id": hit["node_id"]}))
                expected = parent_nodes[expected_parent] if expected_parent else stored
                assert fetched["node_id"] == expected["node_id"]
                assert fetched["uri"] == expected["uri"] and fetched["text"] == expected["text"]
                neighbors = fetched["neighbors"]
                assert set(neighbors) == {"parent", "children", "siblings"}
                known_ids = set(search_nodes) | set(parent_nodes)
                refs = ([neighbors["parent"]] if neighbors["parent"] else []) + neighbors["children"] + neighbors["siblings"]
                assert all(isinstance(ref, str) and ref in known_ids for ref in refs)
                fetches.append(fetched["node_id"])
            checks["search_and_fetch_texts_match_metadata"] = True
            checks["fetch_via_search_node_id"] = True
            checks["neighbors_are_node_id_references"] = True
            outcome["search_node_ids"] = [hit["node_id"] for hit in hits]
            outcome["scores"] = [hit["score"] for hit in hits]
            outcome["fetch_node_ids"] = fetches
            if not all(checks.values()):
                raise AssertionError("One or more checks failed")
    except Exception as exc:
        outcome.update(status="FAIL", reason=f"{type(exc).__name__}: {exc}")
    return outcome


async def run(query: str) -> int:
    path = ROOT / "artifacts/manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    # Sequential servers avoid simultaneously loading five large CPU models.
    results = [await check_group(group, query, manifest) for group in GROUPS]
    shared_equal = None
    if all(row["status"] == "PASS" for row in results):
        baseline = results[0]
        shared_equal = all(row["search_node_ids"] == baseline["search_node_ids"] and row["scores"] == baseline["scores"] for row in results[2:])
    status = "FAIL" if any(row["status"] == "FAIL" for row in results) or shared_equal is False else "BLOCKED" if any(row["status"] == "BLOCKED" for row in results) else "PASS"
    report = {"status": status, "transport": "stdio", "real_mcp_client": True, "query": query, "shared_small_search_equal": shared_equal, "experiments": results}
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports/mcp_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "groups": {row["group"]: row["status"] for row in results}}, ensure_ascii=False))
    return 1 if status == "FAIL" else 2 if status == "BLOCKED" else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="离职员工权限回收时如何处理角色和会话？")
    return asyncio.run(run(parser.parse_args().query))


if __name__ == "__main__":
    raise SystemExit(main())
