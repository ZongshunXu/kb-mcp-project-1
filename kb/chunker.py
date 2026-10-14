"""Deterministic character windows over strictly decoded Markdown text."""

from __future__ import annotations

from pathlib import Path

READING_STRATEGY = {
    "encoding": "utf-8",
    "errors": "strict",
    "newlines": "CRLF and CR normalized to LF",
    "bom": "preserved",
    "offset_unit": "Python Unicode characters",
    "interval": "[start,end)",
}


def read_text(path: str | Path) -> str:
    """Read the exact text used for every offset, preserving a UTF-8 BOM."""
    return Path(path).read_bytes().decode("utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n")


def windows(length: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """Stop when the first window reaches the document end."""
    if type(length) is not int or length < 0:
        raise ValueError("length must be a non-negative integer")
    if type(size) is not int or size <= 0 or type(overlap) is not int or not 0 <= overlap < size:
        raise ValueError("size must be positive and 0 <= overlap < size")
    result = []
    for start in range(0, length, size - overlap):
        end = min(length, start + size)
        result.append((start, end))
        if end == length:
            break
    return result


def chunk_document(text: str, uri: str, size: int, overlap: int) -> list[dict]:
    return [
        {
            "node_id": f"{uri}::w{size}o{overlap}:{start}-{end}",
            "uri": uri,
            "start": start,
            "end": end,
            "text": text[start:end],
        }
        for start, end in windows(len(text), size, overlap)
    ]


def build_relations(children: list[dict], parents: list[dict]) -> dict[str, str]:
    """Choose the containing parent with the smallest start, then stable ID."""
    by_uri: dict[str, list[dict]] = {}
    for parent in parents:
        by_uri.setdefault(parent["uri"], []).append(parent)
    for entries in by_uri.values():
        entries.sort(key=lambda node: (node["start"], node["node_id"]))
    relations = {}
    for child in children:
        matches = [
            parent for parent in by_uri.get(child["uri"], [])
            if parent["start"] <= child["start"] and child["end"] <= parent["end"]
        ]
        if not matches:
            raise ValueError(f"No fully containing parent for {child['node_id']}")
        if child["node_id"] in relations:
            raise ValueError(f"Duplicate child node ID: {child['node_id']}")
        relations[child["node_id"]] = matches[0]["node_id"]
    return relations
