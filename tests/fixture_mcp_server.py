"""Protocol-only fixture; never used by index/eval/check production commands."""

import os

from kb.mcp_server import create_server


class FixtureProvider:
    def __init__(self, group: str):
        self.group = group

    def search(self, query, k=5, scope=None):
        if not query.strip() or isinstance(k, bool) or k <= 0:
            raise ValueError("Invalid query or k")
        return [{"node_id": "fixture-child", "parent_id": "fixture-parent" if self.group.startswith("P") else None, "uri": "fixture.md", "text": "fixture child text", "score": 0.9}][:k]

    def fetch(self, node_id):
        if node_id != "fixture-child":
            raise ValueError("Unknown search node ID")
        parent = self.group.startswith("P")
        return {"node_id": "fixture-parent" if parent else node_id, "uri": "fixture.md", "text": "fixture parent context" if parent else "fixture child text", "neighbors": {"parent": None, "children": ["fixture-child"] if parent else [], "siblings": []}}


if __name__ == "__main__":
    create_server(FixtureProvider(os.environ["KB_EXPERIMENT_ID"])).run(transport="stdio")
