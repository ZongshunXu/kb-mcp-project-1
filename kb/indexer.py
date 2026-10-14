"""Build the five fixed experiments, sharing only two dense FAISS indexes."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

from kb.chunker import READING_STRATEGY, build_relations, chunk_document
from kb.embedding import BGEEncoder, MAX_LENGTH, MODEL_ID, ModelUnavailable, file_sha256, model_fingerprint, resolve_model_path, tokenizer_fingerprint

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
EXPECTED_EXPERIMENTS = {
    "F1": {"kind": "M0", "search_size": 240, "search_overlap": 50, "parent_size": None, "parent_overlap": None},
    "F2": {"kind": "M0", "search_size": 960, "search_overlap": 200, "parent_size": None, "parent_overlap": None},
    "P1": {"kind": "M1", "search_size": 240, "search_overlap": 50, "parent_size": 960, "parent_overlap": 200},
    "P2": {"kind": "M1", "search_size": 240, "search_overlap": 50, "parent_size": 960, "parent_overlap": 300},
    "P3": {"kind": "M1", "search_size": 240, "search_overlap": 50, "parent_size": 1200, "parent_overlap": 300},
}


class BuildBlocked(RuntimeError):
    """Metadata is available, but a real dense index could not be built."""


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_corpus(root: Path) -> tuple[dict, dict[str, str]]:
    corpus_dir = root / "data" / "corpus"
    paths = sorted(
        (path for path in corpus_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".md"),
        key=lambda path: path.relative_to(corpus_dir).as_posix(),
    )
    if len(paths) != 300:
        raise ValueError(f"Expected exactly 300 Markdown documents in {corpus_dir}; found {len(paths)}.")
    files = []
    texts = {}
    for path in paths:
        if not path.resolve().is_relative_to(corpus_dir.resolve()):
            raise ValueError(f"Corpus file resolves outside data/corpus: {path}")
        uri = path.relative_to(corpus_dir).as_posix()
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
        texts[uri] = text
        files.append({"uri": uri, "sha256": hashlib.sha256(raw).hexdigest(), "characters": len(text)})
    return {"count": len(files), "digest": hashlib.sha256(_json_bytes(files)).hexdigest(), "files": files}, texts


def corpus_manifest(root: Path = PROJECT_ROOT) -> dict:
    """Recompute the exact manifest digest that an annotation review binds to."""
    return _read_corpus(Path(root).resolve())[0]


def load_experiments(root: Path = PROJECT_ROOT) -> dict:
    config = json.loads((Path(root) / "config" / "experiments.json").read_text(encoding="utf-8"))
    if config != EXPECTED_EXPERIMENTS:
        raise ValueError("config/experiments.json must contain the five fixed F1/F2/P1/P2/P3 configurations.")
    return config


def versions() -> dict:
    result = {"python": platform.python_version()}
    for name in ("mcp", "torch", "transformers", "numpy", "faiss-cpu"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_nodes(path: Path, nodes: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for node in nodes:
            stream.write(_json_bytes(node).decode("utf-8") + "\n")
    temporary.replace(path)


def _model_reference(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def build_index(root: Path = PROJECT_ROOT, model_path: str | Path | None = None, device: str = "cpu", batch_size: int = 8) -> dict:
    root = Path(root).resolve()
    artifacts = root / "artifacts"
    manifest_path = artifacts / "manifest.json"
    manifest = {"schema_version": SCHEMA_VERSION, "status": "BUILDING", "reading_strategy": READING_STRATEGY, "versions": versions()}
    _write_json(manifest_path, manifest)
    try:
        config = load_experiments(root)
        corpus, documents = _read_corpus(root)
        manifest.update({
            "corpus": corpus,
            "embedding": {"name": MODEL_ID, "model_path": None, "fingerprint": None, "files": [], "pooling": "CLS", "normalization": "L2", "max_length": MAX_LENGTH, "dimension": None, "device": device, "batch_size": batch_size},
            "tokenizer": {"name": MODEL_ID, "model_path": None, "version": manifest["versions"]["transformers"], "fingerprint": None, "max_length": MAX_LENGTH},
            "size_unit": "MB (1,000,000 bytes)",
            "scope_implemented": False,
            "experiments": {},
            "checksums": {},
        })
        nodes = {}
        for name, size, overlap in (("small", 240, 50), ("large", 960, 200), ("parent_P2", 960, 300), ("parent_P3", 1200, 300)):
            nodes[name] = [node for uri, text in documents.items() for node in chunk_document(text, uri, size, overlap)]
            if not nodes[name]:
                raise ValueError("The corpus contains no searchable text.")
            relative = f"artifacts/nodes/{name}.jsonl"
            _write_nodes(root / relative, nodes[name])
            manifest["checksums"][relative] = file_sha256(root / relative)
        for group, parameters in config.items():
            search_name = "large" if group == "F2" else "small"
            parent_name = ("large" if group == "P1" else f"parent_{group}") if group.startswith("P") else None
            search_relative = f"artifacts/nodes/{search_name}.jsonl"
            parent_relative = f"artifacts/nodes/{parent_name}.jsonl" if parent_name else None
            relations_relative = f"artifacts/relations/{group}.json" if parent_name else None
            if parent_name:
                relations = build_relations(nodes[search_name], nodes[parent_name])
                _write_json(root / relations_relative, relations)
                manifest["checksums"][relations_relative] = file_sha256(root / relations_relative)
            metadata_files = {item for item in (search_relative, parent_relative, relations_relative) if item}
            manifest["experiments"][group] = {
                **parameters,
                "search_nodes": search_relative, "parent_nodes": parent_relative, "relations": relations_relative,
                "faiss": f"artifacts/faiss/{search_name}.faiss", "search_chunks": len(nodes[search_name]),
                "parent_chunks": len(nodes[parent_name]) if parent_name else 0,
                "faiss_mb": None, "metadata_mb": sum((root / item).stat().st_size for item in metadata_files) / 1_000_000,
                "total_index_mb": None,
            }
        _write_json(manifest_path, manifest)
    except Exception as error:
        manifest.update(status="FAILED", error=str(error))
        _write_json(manifest_path, manifest)
        raise
    try:
        import faiss
        import torch
        resolved = resolve_model_path(model_path, root)
        reference = _model_reference(resolved, root)
        fingerprint = model_fingerprint(resolved)
        token_fingerprint = tokenizer_fingerprint(resolved)
        manifest["embedding"].update(model_path=reference, fingerprint=fingerprint["digest"], files=fingerprint["files"], torch_threads=torch.get_num_threads(), faiss_threads=faiss.omp_get_max_threads())
        manifest["tokenizer"].update(model_path=reference, fingerprint=token_fingerprint["digest"])
        _write_json(manifest_path, manifest)
        encoder = BGEEncoder(resolved, device=device, batch_size=batch_size, root=root)
        encoder.progress = True
        manifest["embedding"]["dimension"] = encoder.dimension
        for name in ("small", "large"):
            vectors = encoder.encode([node["text"] for node in nodes[name]])
            index = faiss.IndexFlatIP(encoder.dimension)
            index.add(vectors)
            path = artifacts / "faiss" / f"{name}.faiss"
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".faiss.tmp")
            faiss.write_index(index, str(temporary))
            temporary.replace(path)
            manifest["checksums"][path.relative_to(root).as_posix()] = file_sha256(path)
        for experiment in manifest["experiments"].values():
            experiment["faiss_mb"] = (root / experiment["faiss"]).stat().st_size / 1_000_000
            experiment["total_index_mb"] = experiment["faiss_mb"] + experiment["metadata_mb"]
        manifest["status"] = "READY"
        _write_json(manifest_path, manifest)
        return manifest
    except Exception as error:
        message = str(error) if isinstance(error, ModelUnavailable) else f"Real BGE-M3/FAISS indexing could not complete: {error}"
        manifest.update(status="BLOCKED", error=message, blocked_reasons=[message])
        _write_json(manifest_path, manifest)
        raise BuildBlocked(message) from error
