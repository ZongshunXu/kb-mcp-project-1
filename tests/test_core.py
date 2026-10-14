"""Core invariants; lightweight fake encoders exist only inside tmp_path tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

import kb.indexer as indexer
import kb.retriever as retriever
from kb.chunker import build_relations, chunk_document, read_text, windows
from kb.embedding import ModelUnavailable


@pytest.mark.parametrize("length", [0, 1, 239, 240, 241, 960, 961, 3000, 8193])
def test_windows_cover_exact_text_and_stop_at_end(length):
    text = "甲" * length
    chunks = chunk_document(text, "nested/doc.md", 240, 50)
    assert [(node["start"], node["end"]) for node in chunks] == windows(length, 240, 50)
    if length:
        assert chunks[0]["start"] == 0
        assert chunks[-1]["end"] == length
        assert all(node["end"] < length for node in chunks[:-1])
        assert all(node["text"] == text[node["start"]:node["end"]] for node in chunks)
        assert all(right["start"] - left["start"] == 190 for left, right in zip(chunks, chunks[1:]))
    else:
        assert chunks == []


@pytest.mark.parametrize("size,overlap", [(960, 200), (960, 300), (1200, 300)])
@pytest.mark.parametrize("length", [1, 239, 240, 241, 960, 961, 1200, 1201, 4000])
def test_every_child_fully_contained_in_earliest_parent(size, overlap, length):
    text = "甲" * length
    children = chunk_document(text, "doc.md", 240, 50)
    parents = chunk_document(text, "doc.md", size, overlap)
    relations = build_relations(children, parents)
    by_id = {node["node_id"]: node for node in parents}
    for child in children:
        parent = by_id[relations[child["node_id"]]]
        candidates = [node for node in parents if node["start"] <= child["start"] and child["end"] <= node["end"]]
        assert parent == min(candidates, key=lambda node: (node["start"], node["node_id"]))
        assert parent["start"] <= child["start"] <= child["end"] <= parent["end"]


def test_uncontained_child_and_invalid_windows_fail():
    with pytest.raises(ValueError, match="No fully containing"):
        build_relations(chunk_document("a" * 500, "doc.md", 240, 50), chunk_document("a" * 500, "doc.md", 100, 0))
    for size, overlap in ((0, 0), (10, -1), (10, 10), (True, 0)):
        with pytest.raises(ValueError):
            windows(100, size, overlap)


def test_strict_utf8_and_normalized_offsets_preserve_bom(tmp_path):
    path = tmp_path / "doc.md"
    path.write_bytes(b"\xef\xbb\xbf" + "甲\r\n乙\r丙\n".encode("utf-8"))
    assert read_text(path) == "\ufeff甲\n乙\n丙\n"
    path.write_bytes(b"\xff")
    with pytest.raises(UnicodeDecodeError):
        read_text(path)


@pytest.fixture
def corpus_root(tmp_path, monkeypatch):
    monkeypatch.delenv("KB_MODEL_PATH", raising=False)
    corpus = tmp_path / "data" / "corpus"
    corpus.mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "experiments.json").write_text(json.dumps(indexer.EXPECTED_EXPERIMENTS), encoding="utf-8")
    for number in range(300):
        (corpus / f"document_{number:03}.md").write_bytes((f"文档 {number}\r\n" + "甲" * 1100).encode("utf-8"))
    return tmp_path


def test_corpus_digest_binds_raw_bytes_and_normalized_character_count(corpus_root):
    manifest = indexer.corpus_manifest(corpus_root)
    assert manifest["count"] == 300
    assert manifest["files"][0]["characters"] == len("文档 0\n" + "甲" * 1100)
    encoded = json.dumps(manifest["files"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert manifest["digest"] == hashlib.sha256(encoded).hexdigest()
    path = corpus_root / "data" / "corpus" / "document_000.md"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    assert indexer.corpus_manifest(corpus_root)["digest"] != manifest["digest"]


def test_missing_model_saves_true_costs_without_vectors(corpus_root, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ModelUnavailable("No local model in this isolated test")
    monkeypatch.setattr(indexer, "resolve_model_path", unavailable)
    with pytest.raises(indexer.BuildBlocked, match="No local model"):
        indexer.build_index(corpus_root)
    manifest = json.loads((corpus_root / "artifacts" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "BLOCKED"
    assert manifest["corpus"]["count"] == 300
    for group in manifest["experiments"].values():
        assert group["search_chunks"] > 0
        assert group["metadata_mb"] > 0
        assert group["faiss_mb"] is None
        assert group["total_index_mb"] is None
        assert not (corpus_root / group["faiss"]).exists()
    with pytest.raises(retriever.IndexUnavailable, match="BLOCKED"):
        retriever.Retriever("F1", corpus_root).fetch("invalid")


def test_corpus_requires_exactly_300_documents(corpus_root):
    (corpus_root / "data" / "corpus" / "document_000.md").unlink()
    with pytest.raises(ValueError, match="found 299"):
        indexer.build_index(corpus_root)
    manifest = json.loads((corpus_root / "artifacts" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "FAILED"


@pytest.fixture
def ready_root(corpus_root, monkeypatch):
    # This fixture never touches project artifacts or supplies research measurements.
    path = corpus_root / "models" / "test-model"
    path.mkdir(parents=True)
    (path / "config.json").write_text("{}", encoding="utf-8")
    fingerprint = {"digest": "test-only", "files": []}

    class FakeEncoder:
        dimension = 2

        def __init__(self, *args, **kwargs):
            pass

        def encode(self, texts):
            return np.tile(np.array([[1, 0]], dtype=np.float32), (len(texts), 1))

    monkeypatch.setattr(indexer, "BGEEncoder", FakeEncoder)
    monkeypatch.setattr(retriever, "BGEEncoder", FakeEncoder)
    monkeypatch.setattr(indexer, "model_fingerprint", lambda _: fingerprint)
    monkeypatch.setattr(indexer, "tokenizer_fingerprint", lambda _: fingerprint)
    monkeypatch.setattr(retriever, "model_fingerprint", lambda _: fingerprint)
    manifest = indexer.build_index(corpus_root, model_path=path)
    return corpus_root, manifest


def test_shared_artifacts_costs_and_reproducible_metadata(ready_root):
    root, manifest = ready_root
    groups = manifest["experiments"]
    assert manifest["status"] == "READY"
    for group in ("P1", "P2", "P3"):
        assert groups[group]["search_nodes"] == groups["F1"]["search_nodes"]
        assert groups[group]["faiss"] == groups["F1"]["faiss"]
    assert groups["P1"]["parent_nodes"] == groups["F2"]["search_nodes"]
    assert len({group["faiss"] for group in groups.values()}) == 2
    for group in groups.values():
        metadata = {group[key] for key in ("search_nodes", "parent_nodes", "relations") if group[key]}
        assert group["metadata_mb"] == sum((root / path).stat().st_size for path in metadata) / 1_000_000
        assert group["total_index_mb"] == group["metadata_mb"] + group["faiss_mb"]
    before = {name: (root / name).read_bytes() for name in manifest["checksums"] if not name.endswith(".faiss")}
    repeated = indexer.build_index(root, model_path=root / manifest["embedding"]["model_path"])
    assert repeated["corpus"] == manifest["corpus"]
    assert all((root / name).read_bytes() == contents for name, contents in before.items())


def test_ties_are_global_and_shared_search_results_are_equal(ready_root):
    root, _ = ready_root
    results = []
    for group in ("F1", "P1", "P2", "P3"):
        engine = retriever.Retriever(group, root)
        hits = engine.search("真实接口排序单元测试", k=5, scope="ignored")
        assert len(hits) == 5
        assert [hit["node_id"] for hit in hits] == sorted(node["node_id"] for node in engine.nodes)[:5]
        assert all(set(hit) == {"node_id", "parent_id", "uri", "text", "score"} for hit in hits)
        results.append([(hit["node_id"], hit["score"]) for hit in hits])
    assert all(result == results[0] for result in results)


@pytest.mark.parametrize("group", ["F1", "F2", "P1", "P2", "P3"])
def test_fetch_uses_search_id_and_returns_actual_text_node(ready_root, group):
    root, _ = ready_root
    engine = retriever.Retriever(group, root)
    hit = engine.search("测试", k=1)[0]
    fetched = engine.fetch(hit["node_id"])
    assert set(fetched) == {"node_id", "uri", "text", "neighbors"}
    assert set(fetched["neighbors"]) == {"parent", "children", "siblings"}
    assert fetched["neighbors"]["parent"] is None
    if group.startswith("P"):
        assert fetched["node_id"] == hit["parent_id"]
        assert hit["node_id"] in fetched["neighbors"]["children"]
        assert fetched["text"] == engine.parent_by_id[hit["parent_id"]]["text"]
        assert all(node_id in engine.parent_by_id for node_id in fetched["neighbors"]["siblings"])
        with pytest.raises(ValueError, match="not parent_id"):
            engine.fetch(hit["parent_id"])
    else:
        assert fetched["node_id"] == hit["node_id"]
        assert fetched["text"] == hit["text"]


def test_changed_corpus_or_artifact_prevents_stale_retrieval(ready_root):
    root, manifest = ready_root
    corpus_file = root / "data" / "corpus" / "document_000.md"
    original = corpus_file.read_bytes()
    corpus_file.write_bytes(original + b" ")
    with pytest.raises(retriever.IndexUnavailable, match="Original corpus"):
        retriever.Retriever("F1", root).search("test")
    corpus_file.write_bytes(original)
    artifact = root / manifest["experiments"]["F1"]["search_nodes"]
    with artifact.open("a", encoding="utf-8") as stream:
        stream.write("{}\n")
    with pytest.raises(retriever.IndexUnavailable, match="checksum mismatch"):
        retriever.Retriever("F1", root).search("test")


def test_clear_parameter_and_absent_index_errors(tmp_path):
    with pytest.raises(ValueError, match="Unknown experiment"):
        retriever.Retriever("M2", tmp_path)
    engine = retriever.Retriever("F1", tmp_path)
    for query, k in ((" ", 5), ("valid", 0), ("valid", True)):
        with pytest.raises(ValueError):
            engine.search(query, k)
    with pytest.raises(retriever.IndexUnavailable, match="manifest.json is missing"):
        engine.search("valid")
