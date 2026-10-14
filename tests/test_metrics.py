import pytest

from kb.metrics import (
    compare_recall, coverage_details, dedup_context_tokens, evidence_recall,
    macro_average, merge_intervals, serialize_tool_result,
)


def interval(start, end, uri="a.md"):
    return {"uri": uri, "start": start, "end": end}


def test_full_coverage_requires_no_gap_and_respects_uri():
    evidence = [interval(3, 9)]
    assert evidence_recall(evidence, [interval(0, 5), interval(5, 12)]) == 1
    assert evidence_recall(evidence, [interval(0, 5), interval(6, 12)]) == 0
    assert evidence_recall(evidence, [interval(0, 12, "b.md")]) == 0
    assert evidence_recall(evidence, [interval(4, 10)]) == 0


def test_merge_nested_duplicate_and_adjacent_ranges():
    assert merge_intervals([interval(2, 4), interval(0, 5), interval(5, 8),
                            interval(0, 5), interval(0, 1, "b.md")]) == [
        interval(0, 8), interval(0, 1, "b.md"),
    ]
    assert merge_intervals([]) == []


def test_recall_is_query_macro_average():
    first = evidence_recall([interval(0, 1)], [interval(0, 1)])
    second = evidence_recall([interval(2, 3), interval(4, 5), interval(6, 7)], [])
    assert macro_average([first, second]) == 0.5
    assert coverage_details([interval(0, 1)], [interval(0, 1)])[0]["covered"] is True


@pytest.mark.parametrize("invalid", [interval(-1, 3), interval(3, 3), interval(5, 3),
                                       interval(True, 3), interval(0, 3, "")])
def test_invalid_evidence_rejected(invalid):
    with pytest.raises(ValueError):
        evidence_recall([invalid], [])


def test_empty_evidence_and_macro_rejected():
    with pytest.raises(ValueError):
        evidence_recall([], [])
    with pytest.raises(ValueError):
        macro_average([])


class CharacterTokenizer:
    def encode(self, text, add_special_tokens=False):
        assert add_special_tokens is False
        return list(text)


def test_context_tokens_use_raw_union_and_do_not_join_different_docs():
    spans = [interval(0, 3), interval(2, 5), interval(0, 3), interval(0, 2, "b.md")]
    assert dedup_context_tokens(spans, {"a.md": "中文\n证据abcdef", "b.md": "独立"},
                                CharacterTokenizer()) == 7


def test_tool_serialization_counts_sdk_wrapper():
    class Result:
        def model_dump_json(self):
            return '{"structuredContent":{"result":["中文"]},"content":[{"text":"中文","type":"text"}],"isError":false}'
    assert serialize_tool_result(Result()) == (
        '{"content":[{"text":"中文","type":"text"}],"isError":false,'
        '"structuredContent":{"result":["中文"]}}'
    )


def test_relative_gain_handles_zero_baseline():
    assert compare_recall(0, 0.25) == {"absolute_percentage_points": 25,
                                     "relative_percent": None}
    result = compare_recall(0.5, 0.75)
    assert result == {"absolute_percentage_points": 25, "relative_percent": 50}


def test_metric_pipeline_fetches_one_parent_via_first_child_id():
    """A unit fixture verifies scoring/dedup; real stdio has separate tests."""
    import asyncio
    from mcp.types import CallToolResult
    from scripts.eval import query_once

    text = "abcdefgh"
    children = {
        "c1": {"node_id": "c1", "uri": "a.md", "start": 0, "end": 4, "text": text[:4]},
        "c2": {"node_id": "c2", "uri": "a.md", "start": 2, "end": 6, "text": text[2:6]},
    }
    parent = {"node_id": "p1", "uri": "a.md", "start": 0, "end": 8, "text": text}
    hits = [{"node_id": key, "parent_id": "p1", "uri": "a.md", "text": children[key]["text"],
             "score": score} for key, score in (("c1", .9), ("c2", .8))]

    class FixtureSession:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            payload = {"result": hits} if name == "kb_search" else {
                "node_id": "p1", "uri": "a.md", "text": text,
                "neighbors": {"parent": None, "children": ["c1", "c2"], "siblings": []},
            }
            return CallToolResult(content=[], structuredContent=payload)

    session = FixtureSession()
    row = asyncio.run(query_once(
        session, {"query_id": "fixture", "query": "fixture", "evidence": [interval(0, 8)]},
        "P1", {"search": children, "parents": {"p1": parent}, "relations": {"c1": "p1", "c2": "p1"}},
        {"a.md": text}, CharacterTokenizer(), 1,
    ))
    assert session.calls == [("kb_search", {"query": "fixture", "k": 5}),
                             ("kb_fetch", {"node_id": "c1"})]
    assert row["search_recall_at_5"] == 0
    assert row["fetch_recall_at_5"] == 1
    assert row["fetch_calls"] == 1
    assert row["dedup_context_tokens"] == 8
    assert row["tool_tokens"] > row["dedup_context_tokens"]
    assert row["total_ms"] >= row["search_ms"] + row["fetch_ms"]


def test_review_must_bind_both_qrels_and_corpus(tmp_path):
    import json
    from scripts.eval import EvaluationBlocked, load_reviewed_qrels, sha256_file

    qrels = tmp_path / "fixture_qrels.jsonl"
    qrels.write_text(json.dumps({"query_id": "fixture", "query": "fixture question",
                                "evidence": [interval(0, 1)]}) + "\n", encoding="utf-8")
    review = tmp_path / "fixture_review.json"
    payload = {"status": "approved", "reviewer": "unit-test fixture only",
               "reviewed_at": "2026-01-01T00:00:00+08:00", "qrels_sha256": sha256_file(qrels),
               "corpus_digest": "fixture-corpus-digest"}
    review.write_text(json.dumps(payload), encoding="utf-8")
    assert len(load_reviewed_qrels(qrels, review, {"digest": "fixture-corpus-digest"},
                                  {"a.md": "a"})) == 1
    with pytest.raises(EvaluationBlocked, match="corpus_digest"):
        load_reviewed_qrels(qrels, review, {"digest": "different-corpus"}, {"a.md": "a"})
    qrels.write_text(qrels.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(EvaluationBlocked, match="qrels_sha256"):
        load_reviewed_qrels(qrels, review, {"digest": "fixture-corpus-digest"}, {"a.md": "a"})


def test_blocked_report_has_blank_measurements_and_recorded_costs():
    from scripts.eval import blank_results

    rows = blank_results({"experiments": {"F1": {"search_chunks": 10, "parent_chunks": 0,
                                                  "faiss_mb": None, "metadata_mb": 1.2}}})
    assert [row["group"] for row in rows] == ["F1", "F2", "P1", "P2", "P3"]
    assert rows[0]["search_chunks"] == 10
    assert rows[0]["parent_chunks"] == 0
    assert rows[0]["metadata_mb"] == 1.2
    assert rows[0]["faiss_mb"] == ""
    assert all(row["search_recall_at_5"] == row["avg_total_ms"] == "" for row in rows)
