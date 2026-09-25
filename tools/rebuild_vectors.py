#!/usr/bin/env python3
"""يبني فهرسَ المتّجهات فوق متنٍ أو فوق ملفّات المالك، ويسأله (غ٢).

    python3 tools/rebuild_vectors.py --corpus maritime --embedder ollama \
        --model qwen3-embedding:0.6b --out projections/maritime-vec.sqlite
    python3 tools/rebuild_vectors.py --files ~/Documents/notes --embedder ollama --out var/notes-vec.sqlite
    python3 tools/rebuild_vectors.py --out projections/maritime-vec.sqlite --query "مياه الصابورة"

`--embedder hash` مُضمِّنٌ حتميٌّ بلا نموذج لإثبات المسار في بيئةٍ بلا Ollama، **وليس
دلاليًّا**: لا يُنشر به رقم، والفهرسُ يحمل `semantic: false` في هويّته. والفهرسُ القائم لا
يُستبدل إلا بـ`--replace`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.canonical import PayloadRejected  # noqa: E402
from core.vector_retrieval import (HashEmbedder, OllamaEmbedder, VectorIndex,  # noqa: E402
                                   corpus_passages, file_passages)


def embedder_from(args):
    if args.embedder == "hash":
        return HashEmbedder(args.dim)
    return OllamaEmbedder(args.model, args.base_url)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--corpus", help="متنٌ مسجَّل (مثل maritime)")
    source.add_argument("--files", type=Path, help="جذرُ ملفّات المالك النصّية")
    parser.add_argument("--embedder", choices=("ollama", "hash"), default="ollama")
    parser.add_argument("--model", default="qwen3-embedding:0.6b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--dim", type=int, default=256, help="بُعدُ المُضمِّن الحتميّ وحده")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--query", help="بعد البناء أو على فهرسٍ قائم: استعلامٌ يُطبع أقربُ عشرة")
    args = parser.parse_args(argv)
    try:
        embedder = embedder_from(args)
        if args.corpus or args.files:
            passages = corpus_passages(args.corpus) if args.corpus else file_passages(args.files)
            summary = VectorIndex.build(args.out, passages, embedder, replace=args.replace)
            print(json.dumps({"status": "built", **summary}, ensure_ascii=False))
        if args.query:
            hits = VectorIndex(args.out).search(args.query, embedder, limit=10)
            print(json.dumps({"status": "searched", "identity": embedder.identity(), "hits": hits},
                             ensure_ascii=False, indent=1))
        if not (args.corpus or args.files or args.query):
            parser.error("اطلب بناءً (--corpus أو --files) أو استعلامًا (--query)")
    except PayloadRejected as exc:
        print(json.dumps({"status": "refused", "code": exc.code, "reason": getattr(exc, "reason", "")},
                         ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
