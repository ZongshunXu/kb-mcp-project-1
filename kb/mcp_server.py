"""The single stdio MCP server, with exactly two tools."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field


def create_server(provider: Any = None) -> FastMCP:
    server = FastMCP("kb-retrieval", log_level="WARNING")

    @lru_cache(maxsize=1)
    def retriever() -> Any:
        if provider is not None:
            return provider
        from kb.indexer import PROJECT_ROOT
        from kb.retriever import Retriever

        return Retriever(os.environ.get("KB_EXPERIMENT_ID", "F1"), root=PROJECT_ROOT)

    @server.tool(structured_output=True)
    def kb_search(query: str, k: Annotated[int, Field(strict=True, gt=0)] = 5, scope: str | None = None) -> list[dict[str, Any]]:
        """Search knowledge-base blocks. scope is reserved and currently ignored."""
        if not query.strip():
            raise ValueError("query must be non-empty")
        if isinstance(k, bool) or k <= 0:
            raise ValueError("k must be a positive integer")
        return retriever().search(query, k, scope)

    @server.tool(structured_output=True)
    def kb_fetch(node_id: str) -> dict[str, Any]:
        """Fetch context using the node_id returned by kb_search."""
        if not node_id.strip():
            raise ValueError("node_id must be non-empty")
        return retriever().fetch(node_id)

    return server


def main() -> None:
    # On Windows, loading the native FAISS DLL after stdio worker threads start
    # can block its module initializer. Load it before entering the async loop.
    try:
        import faiss  # noqa: F401
    except ImportError:
        # Keep tool schemas available; retrieval reports the missing dependency.
        pass
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
