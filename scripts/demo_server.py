"""M0 fixed-data MCP protocol example, separate from the formal experiments."""

from __future__ import annotations

import argparse

from kb.mcp_server import create_server

MODE = "fixed-data-no-formal-eval"
DEMO_NODE_ID = "demo:fixed:flat:001"
DEMO_URI = "demo/fixed-example.md"
DEMO_TEXT = "固定样例：员工离职后，应撤销系统角色并注销会话。"


class DemoProvider:
    """Return a fixed flat block, with a fixed score; no model or index exists."""

    def search(self, query: str, k: int = 5, scope: str | None = None) -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if type(k) is not int or k < 1:
            raise ValueError("k must be a positive integer")
        if scope is not None and not isinstance(scope, str):
            raise ValueError("scope must be a string or null")
        return [{"node_id": DEMO_NODE_ID, "parent_id": None, "uri": DEMO_URI, "text": DEMO_TEXT, "score": 1.0}]

    def fetch(self, node_id: str) -> dict:
        if node_id != DEMO_NODE_ID:
            raise ValueError("Unknown demo search node ID; use node_id returned by kb_search.")
        return {
            "node_id": DEMO_NODE_ID, "uri": DEMO_URI, "text": DEMO_TEXT,
            "neighbors": {"parent": None, "children": [], "siblings": []},
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="M0固定数据MCP协议示例，不进行Embedding、正式检索或Recall评测。")
    parser.add_argument("--check", action="store_true", help="启动真实stdio Client，检查两工具并输出JSON")
    args = parser.parse_args()
    if args.check:
        from scripts.check_demo import main as check_main
        return check_main()
    create_server(provider=DemoProvider()).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
