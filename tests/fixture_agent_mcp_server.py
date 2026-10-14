"""Agent protocol fixture only: no model weights, corpus, or production index."""

import os
import time

from kb.mcp_server import create_server


class FixtureProvider:
    def __init__(self, group):
        self.parent = group.startswith("P")

    def search(self, query, k=5, scope=None):
        if query == "fixture-disconnect":
            os._exit(0)
        if query == "fixture-slow":
            time.sleep(.25)
        if query == "fixture-tool-error":
            raise ValueError("fixture tool error; this is not a production retrieval result")
        return [{"node_id": node_id, "parent_id": "fixture-parent" if self.parent else None,
                 "uri": "fixture.md", "text": text, "score": score}
                for node_id, text, score in (("fixture-child", "fixture source passage", .9),
                                             ("fixture-child-2", "fixture alternate passage", .8))][:k]

    def fetch(self, node_id):
        if node_id not in {"fixture-child", "fixture-child-2"}:
            raise ValueError("Unknown fixture search node")
        return {"node_id": "fixture-parent" if self.parent else node_id, "uri": "fixture.md",
                "text": "fixture parent original context" if self.parent else "fixture source passage",
                "neighbors": {"parent": None, "children": ["fixture-child", "fixture-child-2"] if self.parent else [],
                              "siblings": []}}


if __name__ == "__main__":
    create_server(FixtureProvider(os.environ["KB_EXPERIMENT_ID"])).run(transport="stdio")
