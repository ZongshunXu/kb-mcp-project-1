"""Regression for reordered Markdown sections in annotation drafts."""

import json

from scripts import prepare_annotations


def test_evidence_uses_semantic_headings_instead_of_fixed_numbers(tmp_path, monkeypatch):
    folder = tmp_path / "data/corpus"
    folder.mkdir(parents=True)
    (tmp_path / "data/qrels").mkdir()
    text = "系统名称：测试系统\n业务场景：测试场景\n\n## 2. 业务规则：测试场景\n\n先撤销角色再注销会话。\n\n## 3. 例外与边界\n\n撤回后不得恢复旧角色。\n\n## 5. 数据存储\n\nMySQL\n\n## 6. 运行参数\n\nlimit=10\n"
    (folder / "fixture.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(prepare_annotations, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(prepare_annotations, "corpus_manifest", lambda: {"files": [{"uri": "fixture.md"}], "digest": "fixture-digest"})
    assert prepare_annotations.main() == 0
    row = json.loads((tmp_path / "data/qrels/draft_candidates.jsonl").read_text(encoding="utf-8"))
    assert row["review_status"] == "unreviewed"
    assert row["evidence_excerpts_for_review"] == ["先撤销角色再注销会话。", "撤回后不得恢复旧角色。"]
    assert [text[ev["start"]:ev["end"]] for ev in row["evidence"]] == row["evidence_excerpts_for_review"]
