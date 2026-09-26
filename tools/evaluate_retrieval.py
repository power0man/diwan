#!/usr/bin/env python3
"""يشغّل غ٣: BM25 مقابل المتّجهات مقابل الهجين على البنك العام المجمَّد، بمُضمِّنٍ في Ollama المحلي.

    python3 tools/evaluate_retrieval.py --embedder qwen3-embedding:0.6b --baseline bge-m3 \\
        --out docs/probe/g3-hybrid-vs-bm25-<التاريخ>.json

والتضمينُ محليٌّ وحده: التضمينُ السحابيّ مرفوضٌ لأنه يمرّر النصوص (خطة ٢٦ سبتمبر، غ٣).
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.vector_retrieval import OllamaEmbedder  # noqa: E402
from evaluation import ablation  # noqa: E402
from evaluation.retrieval_general import BANK, BANK_SHA256, RRF_K, comparisons, load_bank, run  # noqa: E402

LIMITS = [
    "bank_authored_by_a_developer_family_one_gold_passage_per_query_not_blind",
    "sixty_short_passages_a_small_corpus_so_absolute_scores_run_high",
    "bm25_arm_has_no_morphological_expansion_unlike_the_product_hybrid_retriever",
    "embedder_is_a_retriever_not_a_judge_gold_labels_decide",
    "single_run_hit_interval_is_wilson_on_the_design_effect_size_ndcg_and_effect_by_seeded_passage_bootstrap",
    "fusion_uses_depth_50_per_channel_and_one_or_matched_bm25_list_unlike_the_product_2x_limit_and_exact_plus_any_lists",
    "the_protocol_statistic_agresti_min_treats_queries_as_independent_the_clustered_effect_interval_is_beside_it",
    "vectors_vs_bm25_is_descriptive_the_k46_vector_rule_decides_the_hybrid_channel_only",
    "no_protocol_decision_while_the_vectors_component_is_blocked_and_461_pairs_would_be_needed_once_ready",
]


def _channels(channels: dict) -> dict:
    return {qid: {name: " ".join(ids) for name, ids in pair.items()} for qid, pair in channels.items()}


def _recorded(rows: dict) -> dict:
    return {arm: [{k: r[k] for k in ("id", "type", "rank", "hit_at_5", "ndcg_at_10")} for r in rows[arm]]
            for arm in rows}


def _digest(model: str, base: str = "http://127.0.0.1:11434") -> str | None:
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=10) as response:
            models = json.loads(response.read().decode("utf-8"))["models"]
    except (OSError, ValueError, KeyError):
        return None
    wanted = model if ":" in model else model + ":latest"
    return next((m.get("digest") for m in models if m.get("name") == wanted), None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--embedder", default="qwen3-embedding:0.6b")
    parser.add_argument("--baseline", help="مُضمِّنٌ ثانٍ خطَّ أساس (مثل bge-m3)")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"التقريرُ قائم: {args.out}")
    bank = load_bank()
    main_run = run(bank, OllamaEmbedder(args.embedder))
    rows = main_run.pop("rows")
    channels = main_run.pop("channels")
    protocol = ablation.protocol()
    report = {
        "schema_version": 1, "kind": "g3_retrieval", "task": "غ٣", "issue": "power0man/diwan#24",
        "date": datetime.date.today().isoformat(), "agent": "anthropic/claude-opus-5-5",
        "engine": None,
        "config": {"bank": str(BANK.relative_to(ROOT)), "bank_sha256": BANK_SHA256, "rrf_k": RRF_K,
                   "embedder": {"model": args.embedder, "digest": _digest(args.embedder), "provider": "ollama-local"},
                   "protocol_sha256": ablation._sha(ablation.PROTOCOL.read_bytes()),
                   "protocol_vectors_status": protocol["components"]["vectors"]["status"]},
        "arms": main_run["arms"], "comparisons": comparisons(rows, main_run["arms"]),
        "rows": _recorded(rows),
        "channels": _channels(channels),
        "measurement_limits": LIMITS,
    }
    if args.baseline:
        base = run(bank, OllamaEmbedder(args.baseline))
        base_rows = base.pop("rows")
        base_channels = base.pop("channels")
        report["baseline"] = {"embedder": {"model": args.baseline, "digest": _digest(args.baseline)},
                              "arms": {arm: base["arms"][arm] for arm in ("vectors", "hybrid")},
                              "rows": _recorded({arm: base_rows[arm] for arm in ("vectors", "hybrid")}),
                              "channels": _channels(base_channels)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({arm: report["arms"][arm]["overall"] for arm in report["arms"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
