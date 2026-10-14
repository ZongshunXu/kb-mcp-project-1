"""Ask one question through the real MCP server and print source context."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from scripts.mcp_client import EXPERIMENT_IDS, MCPToolError, connect, normalize_result


async def ask(query: str, group: str, k: int, *, search_only: bool = False) -> dict:
    async with connect(group) as session:
        hits = normalize_result(
            await session.call_tool("kb_search", {"query": query, "k": k}),
            expected_type=list,
        )
        if not isinstance(hits, list):
            raise MCPToolError("kb_search did not return a result list")
        contexts = []
        fetched_ids = set()
        if not search_only:
            for hit in hits:
                context_id = hit["parent_id"] or hit["node_id"]
                if context_id in fetched_ids:
                    continue
                context = normalize_result(
                    await session.call_tool("kb_fetch", {"node_id": hit["node_id"]})
                )
                if not isinstance(context, dict):
                    raise MCPToolError("kb_fetch did not return a context object")
                contexts.append(context)
                fetched_ids.add(context_id)
    return {"group": group, "query": query, "k": k, "hits": hits, "contexts": contexts}


def print_result(result: dict, *, search_only: bool) -> None:
    print(f"实验组：{result['group']}\n问题：{result['query']}")
    print(f"\n检索片段（{len(result['hits'])} 个）：")
    for rank, hit in enumerate(result["hits"], 1):
        print(f"\n[{rank}] 相似度：{hit['score']:.6f}")
        print(f"源文件：data/corpus/{hit['uri']}")
        print(f"检索节点：{hit['node_id']}")
        print(hit["text"])
    if not result["hits"]:
        print("没有检索结果。")
    if not search_only:
        print(f"\n获取的上下文（去重后 {len(result['contexts'])} 个）：")
        for rank, context in enumerate(result["contexts"], 1):
            print(f"\n--- 上下文 {rank} ---")
            print(f"源文件：data/corpus/{context['uri']}")
            print(f"上下文节点：{context['node_id']}")
            print(context["text"])


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("k 必须为正整数") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("k 必须为正整数")
    return number


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="输入一个问题，通过真实 MCP 查看检索片段和原文上下文。")
    parser.add_argument("--query", required=True, help="要检索的问题")
    parser.add_argument("--group", choices=EXPERIMENT_IDS, default="P1", help="实验组，默认 P1")
    parser.add_argument("--k", type=positive_int, default=5, help="检索结果数，默认 5")
    parser.add_argument("--search-only", action="store_true", help="只查看 Search 片段，不调用 Fetch")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出命中和上下文")
    args = parser.parse_args()
    if not args.query.strip():
        parser.error("问题不能为空")
    print("正在启动 MCP 并加载本地模型，请稍候……", file=sys.stderr)
    try:
        result = asyncio.run(ask(args.query, args.group, args.k, search_only=args.search_only))
    except KeyboardInterrupt:
        print("已停止检索。", file=sys.stderr)
        return 130
    except Exception as exc:
        # ClientSession can wrap a tool failure in nested task-group errors.
        while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
            exc = exc.exceptions[0]
        print(f"检索失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("请确认在 new_project 运行，且 artifacts/manifest.json 的状态为 READY。", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_result(result, search_only=args.search_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
