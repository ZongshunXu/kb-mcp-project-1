"""Local-only BGE-M3 dense encoding; never download or substitute a model."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "BAAI/bge-m3"
MAX_LENGTH = 8192
TOKENIZER_FILES = {
    "config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
    "sentencepiece.bpe.model", "tokenizer.model", "vocab.json", "vocab.txt", "merges.txt",
    "added_tokens.json",
}


class ModelUnavailable(RuntimeError):
    pass


def _is_model_directory(path: Path) -> bool:
    return path.is_dir() and (path / "config.json").is_file()


def resolve_model_path(model_path: str | Path | None = None, root: Path = PROJECT_ROOT) -> Path:
    supplied = model_path or os.environ.get("KB_MODEL_PATH")
    if supplied:
        candidate = Path(supplied).expanduser()
        if not candidate.is_absolute():
            candidate = Path(root) / candidate
        if not _is_model_directory(candidate):
            raise ModelUnavailable(f"BGE-M3 model directory is missing config.json: {candidate}")
        return candidate.resolve()
    project_model = Path(root) / "models" / "bge-m3"
    if _is_model_directory(project_model):
        return project_model.resolve()
    caches = []
    if os.environ.get("HF_HUB_CACHE"):
        caches.append(Path(os.environ["HF_HUB_CACHE"]))
    if os.environ.get("HUGGINGFACE_HUB_CACHE"):
        caches.append(Path(os.environ["HUGGINGFACE_HUB_CACHE"]))
    if os.environ.get("HF_HOME"):
        caches.append(Path(os.environ["HF_HOME"]) / "hub")
    caches.append(Path.home() / ".cache" / "huggingface" / "hub")
    for cache in caches:
        repository = cache / "models--BAAI--bge-m3"
        main_ref = repository / "refs" / "main"
        if main_ref.is_file():
            revision = main_ref.read_text(encoding="utf-8").strip()
            candidate = repository / "snapshots" / revision
            if _is_model_directory(candidate):
                return candidate.resolve()
        snapshots = repository / "snapshots"
        if snapshots.is_dir():
            for candidate in sorted(snapshots.iterdir(), key=lambda item: item.name):
                if _is_model_directory(candidate):
                    return candidate.resolve()
    raise ModelUnavailable(
        "Local BGE-M3 was not found. Place it in models/bge-m3, set KB_MODEL_PATH, "
        "or provide --model-path; network downloads and replacement models are disabled."
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint(path: Path, tokenizer_only: bool = False) -> dict:
    entries = []
    for item in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        if not item.is_file() or ".git" in item.relative_to(path).parts:
            continue
        is_tokenizer = item.name in TOKENIZER_FILES
        is_weight = item.suffix in {".safetensors", ".bin"} or item.name.endswith(".index.json")
        if is_tokenizer or (not tokenizer_only and is_weight):
            entries.append({"name": item.relative_to(path).as_posix(), "sha256": file_sha256(item), "bytes": item.stat().st_size})
    encoded = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"digest": hashlib.sha256(encoded).hexdigest(), "files": entries}


def model_fingerprint(path: str | Path) -> dict:
    return _fingerprint(Path(path))


def tokenizer_fingerprint(path: str | Path) -> dict:
    return _fingerprint(Path(path), tokenizer_only=True)


def load_tokenizer(model_path: str | Path | None = None, expected_fingerprint: str | None = None, root: Path = PROJECT_ROOT):
    path = resolve_model_path(model_path, root)
    if expected_fingerprint and tokenizer_fingerprint(path)["digest"] != expected_fingerprint:
        raise ModelUnavailable("BGE-M3 tokenizer fingerprint differs from the build manifest; rebuild the index.")
    try:
        from transformers import AutoTokenizer
        return AutoTokenizer.from_pretrained(str(path), local_files_only=True)
    except Exception as error:
        raise ModelUnavailable(f"Cannot load the local BGE-M3 tokenizer: {error}") from error


class BGEEncoder:
    def __init__(self, model_path: str | Path | None = None, device: str = "cpu", batch_size: int = 8, root: Path = PROJECT_ROOT):
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        self.path = resolve_model_path(model_path, root)
        self.device = device
        self.batch_size = batch_size
        self.progress = False
        try:
            import torch
            from transformers import AutoModel
            self.tokenizer = load_tokenizer(self.path)
            self.model = AutoModel.from_pretrained(str(self.path), local_files_only=True, add_pooling_layer=False)
            if (self.model.config.model_type != "xlm-roberta"
                    or self.model.config.hidden_size != 1024
                    or self.model.config.max_position_embeddings != 8194):
                raise ValueError("The supplied model configuration does not match BGE-M3 (XLM-R, 1024 dimensions, 8194 position embeddings).")
            self.model.to(device)
            self.model.eval()
            self.dimension = self.model.config.hidden_size
        except Exception as error:
            raise ModelUnavailable(f"Cannot load local BGE-M3 dense weights on {device}: {error}") from error

    def encode(self, texts: list[str]):
        import numpy as np
        import torch
        import torch.nn.functional as functional

        batches = []
        next_progress = 0
        with torch.inference_mode():
            for start in range(0, len(texts), self.batch_size):
                if self.progress and start >= next_progress:
                    print(f"BGE-M3: {start}/{len(texts)} texts encoded", file=sys.stderr, flush=True)
                    next_progress = start + 100
                features = self.tokenizer(
                    texts[start:start + self.batch_size], padding=True, truncation=True,
                    max_length=MAX_LENGTH, return_tensors="pt",
                )
                features = {name: value.to(self.device) for name, value in features.items()}
                cls_vectors = self.model(**features).last_hidden_state[:, 0]
                normalized = functional.normalize(cls_vectors, p=2, dim=1)
                batches.append(normalized.cpu().float().numpy())
        if self.progress:
            print(f"BGE-M3: {len(texts)}/{len(texts)} texts encoded", file=sys.stderr, flush=True)
        if not batches:
            return np.empty((0, self.dimension), dtype=np.float32)
        result = np.ascontiguousarray(np.concatenate(batches), dtype=np.float32)
        if not np.isfinite(result).all() or not np.allclose(np.linalg.norm(result, axis=1), 1.0, atol=1e-5):
            raise ModelUnavailable("BGE-M3 produced non-finite or non-normalized dense vectors.")
        return result
