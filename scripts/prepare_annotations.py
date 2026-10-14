"""Prepare deterministic, unreviewed annotation drafts from existing documents."""

from __future__ import annotations

import json
from pathlib import Path
import re

from kb.chunker import read_text
from kb.indexer import PROJECT_ROOT, corpus_manifest


def main() -> int:
    corpus = corpus_manifest()
    root = PROJECT_ROOT / "data/corpus"
    drafts = []
    # Uniform document sampling; no retrieval results or experiment scores used.
    for entry in corpus["files"][::6]:
        text = read_text(root / entry["uri"])
        fields = {}
        for field in ("系统名称", "业务场景"):
            match = re.search(rf"^{field}：(.+)$", text, re.MULTILINE)
            if not match:
                raise ValueError(f"Missing {field} in {entry['uri']}")
            fields[field] = match.group(1).strip()
        evidence, excerpts = [], []
        for section in ("业务规则", "例外与边界"):
            match = re.search(rf"^## \d+\. {section}[^\n]*\n+(.*?)(?=\n## |\Z)", text, re.MULTILINE | re.DOTALL)
            if not match:
                raise ValueError(f"Missing section {section} in {entry['uri']}")
            start, end = match.span(1)
            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if start == end:
                raise ValueError(f"Empty section {section} in {entry['uri']}")
            evidence.append({"uri": entry["uri"], "start": start, "end": end})
            excerpts.append(text[start:end])
        drafts.append({
            "query_id": f"draft{len(drafts) + 1:03d}",
            "query": f"在{fields['系统名称']}的{fields['业务场景']}中，需要遵守哪些业务规则，遇到例外时如何处理？",
            "evidence": evidence,
            "review_status": "unreviewed",
            "draft_method": "fixed template over uniformly sampled semantic headings; no LLM or retrieval",
            "evidence_excerpts_for_review": excerpts,
            "corpus_digest": corpus["digest"],
        })
    output = PROJECT_ROOT / "data/qrels/draft_candidates.jsonl"
    output.parent.mkdir(exist_ok=True)
    # Do not replace a draft that may already contain a person's edits.
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        for row in drafts:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"status": "UNREVIEWED_DRAFT", "count": len(drafts), "path": str(output.relative_to(PROJECT_ROOT))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
