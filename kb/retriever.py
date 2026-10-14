"""Validated experiment-specific retrieval with a frozen two-tool contract."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from kb.chunker import READING_STRATEGY, build_relations, chunk_document
from kb.embedding import BGEEncoder, ModelUnavailable, file_sha256, model_fingerprint, resolve_model_path
from kb.indexer import EXPECTED_EXPERIMENTS, PROJECT_ROOT, SCHEMA_VERSION, _read_corpus, load_experiments, versions


class IndexUnavailable(RuntimeError):
    pass


class Retriever:
    def __init__(self, experiment_id: str, root: Path = PROJECT_ROOT):
        if experiment_id not in EXPECTED_EXPERIMENTS:
            raise ValueError(f"Unknown experiment {experiment_id!r}; choose F1, F2, P1, P2, or P3.")
        self.experiment_id = experiment_id
        self.root = Path(root).resolve()
        self._loaded = False
        self._encoder = None
        self._lock = threading.RLock()

    def _artifact(self, relative: str) -> Path:
        if not isinstance(relative, str):
            raise IndexUnavailable("Invalid artifact path in manifest.")
        path = (self.root / relative).resolve()
        if not path.is_relative_to((self.root / "artifacts").resolve()) or not path.is_file():
            raise IndexUnavailable(f"Index artifact is absent or outside artifacts/: {relative}")
        expected = self.manifest.get("checksums", {}).get(relative)
        if not expected or file_sha256(path) != expected:
            raise IndexUnavailable(f"Index artifact checksum mismatch: {relative}; rebuild the index.")
        return path

    def _load_nodes(self, relative: str, documents: dict[str, str], size: int, overlap: int) -> list[dict]:
        path = self._artifact(relative)
        with path.open("r", encoding="utf-8") as stream:
            nodes = [json.loads(line) for line in stream if line.strip()]
        expected = [node for uri, text in documents.items() for node in chunk_document(text, uri, size, overlap)]
        if nodes != expected:
            raise IndexUnavailable(f"Node text, original offsets, order, or fixed windows differ: {relative}")
        if len({node["node_id"] for node in nodes}) != len(nodes):
            raise IndexUnavailable(f"Duplicate node IDs in {relative}")
        return nodes

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            path = self.root / "artifacts" / "manifest.json"
            if not path.is_file():
                raise IndexUnavailable("Index is not loaded: artifacts/manifest.json is missing; run python -m scripts.index.")
            self.manifest = json.loads(path.read_text(encoding="utf-8"))
            if self.manifest.get("schema_version") != SCHEMA_VERSION:
                raise IndexUnavailable("Unsupported index manifest schema version; rebuild the index.")
            if self.manifest.get("status") != "READY":
                raise IndexUnavailable(f"Index is not loaded: {self.manifest.get('status')}: {self.manifest.get('error', 'build has not completed')}")
            if self.manifest.get("reading_strategy") != READING_STRATEGY:
                raise IndexUnavailable("Text reading/offset strategy differs from the build manifest; rebuild the index.")
            current_versions = versions()
            for dependency in ("transformers", "torch", "faiss-cpu", "numpy"):
                if self.manifest.get("versions", {}).get(dependency) != current_versions[dependency]:
                    raise IndexUnavailable(f"{dependency} version differs from the build manifest; rebuild the index.")
            config = load_experiments(self.root)
            self.experiment = self.manifest["experiments"][self.experiment_id]
            if any(self.experiment.get(key) != value for key, value in config[self.experiment_id].items()):
                raise IndexUnavailable("Experiment parameters differ from the fixed configuration; rebuild the index.")
            corpus, documents = _read_corpus(self.root)
            if corpus != self.manifest.get("corpus"):
                raise IndexUnavailable("Original corpus or raw-file SHA256 differs from the build manifest; rebuild the index.")
            self.nodes = self._load_nodes(self.experiment["search_nodes"], documents, self.experiment["search_size"], self.experiment["search_overlap"])
            self.by_id = {node["node_id"]: node for node in self.nodes}
            self.parents = []
            self.parent_by_id = {}
            self.relations = {}
            self.children = {}
            self.parent_neighbors = {}
            if self.experiment["kind"] == "M1":
                self.parents = self._load_nodes(self.experiment["parent_nodes"], documents, self.experiment["parent_size"], self.experiment["parent_overlap"])
                self.parent_by_id = {node["node_id"]: node for node in self.parents}
                self.relations = json.loads(self._artifact(self.experiment["relations"]).read_text(encoding="utf-8"))
                if self.relations != build_relations(self.nodes, self.parents):
                    raise IndexUnavailable("Parent relations differ from the full-containment rule; rebuild the index.")
                for child_id, parent_id in self.relations.items():
                    self.children.setdefault(parent_id, []).append(child_id)
                by_uri = {}
                for parent in self.parents:
                    by_uri.setdefault(parent["uri"], []).append(parent["node_id"])
                for ids in by_uri.values():
                    for position, node_id in enumerate(ids):
                        self.parent_neighbors[node_id] = ids[max(0, position - 1):position] + ids[position + 1:position + 2]
            elif self.experiment["parent_nodes"] is not None or self.experiment["relations"] is not None:
                raise IndexUnavailable("Flat experiments cannot contain parent nodes or relations.")
            if len(self.nodes) != self.experiment["search_chunks"] or len(self.parents) != self.experiment["parent_chunks"]:
                raise IndexUnavailable("Node counts differ from the build manifest.")
            import faiss
            self.index = faiss.read_index(str(self._artifact(self.experiment["faiss"])))
            dimension = self.manifest["embedding"]["dimension"]
            if not isinstance(self.index, faiss.IndexFlatIP) or self.index.metric_type != faiss.METRIC_INNER_PRODUCT or self.index.d != dimension or self.index.ntotal != len(self.nodes):
                raise IndexUnavailable("FAISS algorithm, dimension, or row count differs from the manifest.")
            self._loaded = True
        except IndexUnavailable:
            raise
        except Exception as error:
            raise IndexUnavailable(f"Index could not be loaded: {error}") from error

    def _get_encoder(self):
        if self._encoder is None:
            try:
                reference = os.environ.get("KB_MODEL_PATH") or self.manifest["embedding"].get("model_path")
                path = resolve_model_path(reference, self.root)
                if model_fingerprint(path)["digest"] != self.manifest["embedding"].get("fingerprint"):
                    raise IndexUnavailable("BGE-M3 model fingerprint differs from the build manifest; rebuild the index.")
                if self.manifest["embedding"].get("pooling") != "CLS" or self.manifest["embedding"].get("normalization") != "L2" or self.manifest["embedding"].get("max_length") != 8192:
                    raise IndexUnavailable("BGE-M3 preprocessing differs from the fixed build configuration.")
                self._encoder = BGEEncoder(path, device=self.manifest["embedding"]["device"], batch_size=1, root=self.root)
                if self._encoder.dimension != self.index.d:
                    raise IndexUnavailable("Query embedding dimension differs from the FAISS index.")
            except ModelUnavailable as error:
                raise IndexUnavailable(str(error)) from error
        return self._encoder

    def search(self, query: str, k: int = 5, scope: str | None = None) -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if type(k) is not int or k <= 0:
            raise ValueError("k must be a positive integer")
        if scope is not None and not isinstance(scope, str):
            raise ValueError("scope must be a string or null")
        with self._lock:
            self._load()
            encoder = self._get_encoder()
            query_vector = encoder.encode([query])
            # Search every row so equal scores at the TopK boundary remain deterministic.
            scores, rows = self.index.search(query_vector, self.index.ntotal)
            ranked = sorted(
                ((float(score), self.nodes[int(row)]) for score, row in zip(scores[0], rows[0], strict=True)),
                key=lambda item: (-item[0], item[1]["node_id"]),
            )[:k]
            return [
                {"node_id": node["node_id"], "parent_id": self.relations.get(node["node_id"]), "uri": node["uri"], "text": node["text"], "score": score}
                for score, node in ranked
            ]

    def fetch(self, node_id: str) -> dict:
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("node_id must be a non-empty search node ID")
        with self._lock:
            self._load()
            if node_id not in self.by_id:
                raise ValueError(f"Unknown search node ID: {node_id!r}; pass a node_id returned by kb_search, not parent_id.")
            parent_id = self.relations.get(node_id)
            node = self.parent_by_id[parent_id] if parent_id else self.by_id[node_id]
            return {
                "node_id": node["node_id"], "uri": node["uri"], "text": node["text"],
                "neighbors": {"parent": None, "children": list(self.children.get(node["node_id"], [])), "siblings": list(self.parent_neighbors.get(node["node_id"], []))},
            }
