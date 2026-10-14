"""Rebuild all five experiments from the existing corpus."""

from __future__ import annotations

import argparse
import json
import sys

from kb.indexer import BuildBlocked, build_index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", help="Local Hugging Face BGE-M3 directory")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()
    try:
        manifest = build_index(model_path=args.model_path, device=args.device, batch_size=args.batch_size)
    except BuildBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": manifest["status"], "corpus_count": manifest["corpus"]["count"], "groups": list(manifest["experiments"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
