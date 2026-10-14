"""Evidence coverage and reproducible token accounting, independent of retrieval."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

Interval = dict[str, Any]


def validate_interval(interval: Mapping[str, Any]) -> tuple[str, int, int]:
    uri, start, end = interval.get("uri"), interval.get("start"), interval.get("end")
    if not isinstance(uri, str) or not uri:
        raise ValueError("Evidence/node URI must be a non-empty relative path")
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        raise ValueError(f"Invalid half-open interval for {uri}: [{start}, {end})")
    return uri, start, end


def merge_intervals(intervals: Iterable[Mapping[str, Any]]) -> list[Interval]:
    """Union overlapping or adjacent intervals, separately for each URI."""
    by_uri: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for interval in intervals:
        uri, start, end = validate_interval(interval)
        by_uri[uri].append((start, end))
    merged: list[Interval] = []
    for uri in sorted(by_uri):
        spans = sorted(by_uri[uri])
        start, end = spans[0]
        for next_start, next_end in spans[1:]:
            if next_start <= end:
                end = max(end, next_end)
            else:
                merged.append({"uri": uri, "start": start, "end": end})
                start, end = next_start, next_end
        merged.append({"uri": uri, "start": start, "end": end})
    return merged


def coverage_details(
    evidence: Iterable[Mapping[str, Any]], returned: Iterable[Mapping[str, Any]],
) -> list[Interval]:
    """An evidence item counts only if its entire [start, end) is covered."""
    spans = merge_intervals(returned)
    details = []
    for item in evidence:
        uri, start, end = validate_interval(item)
        covered = any(span["uri"] == uri and span["start"] <= start
                      and end <= span["end"] for span in spans)
        details.append({"uri": uri, "start": start, "end": end, "covered": covered})
    if not details:
        raise ValueError("Every query must have at least one evidence interval")
    return details


def evidence_recall(
    evidence: Iterable[Mapping[str, Any]], returned: Iterable[Mapping[str, Any]],
) -> float:
    details = coverage_details(evidence, returned)
    return sum(item["covered"] for item in details) / len(details)


def macro_average(values: Iterable[float]) -> float:
    values = list(values)
    if not values:
        raise ValueError("Cannot compute a macro average over no queries")
    return sum(values) / len(values)


def serialize_tool_result(result: Any) -> str:
    """Count the full SDK result, preserving duplicated structured/text payloads."""
    payload = json.loads(result.model_dump_json())
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def token_count(text: str, tokenizer: Any) -> int:
    return len(tokenizer.encode(text, add_special_tokens=False))


def tool_token_count(result: Any, tokenizer: Any) -> int:
    return token_count(serialize_tool_result(result), tokenizer)


def dedup_context_tokens(
    returned: Iterable[Mapping[str, Any]], corpus_texts: Mapping[str, str], tokenizer: Any,
) -> int:
    """Sum tokens of each merged raw Fetch span; no synthetic join separators."""
    total = 0
    for span in merge_intervals(returned):
        text = corpus_texts[span["uri"]]
        if span["end"] > len(text):
            raise ValueError(f"Returned interval exceeds corpus: {span}")
        total += token_count(text[span["start"]:span["end"]], tokenizer)
    return total


def compare_recall(baseline: float, candidate: float) -> dict[str, float | None]:
    return {
        "absolute_percentage_points": (candidate - baseline) * 100,
        "relative_percent": None if baseline == 0 else (candidate - baseline) / baseline * 100,
    }
