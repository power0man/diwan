#!/usr/bin/env python3
"""يشغّل غ٣: BM25 مقابل المتّجهات مقابل الهجين على البنك العام المجمَّد، بمُضمِّنٍ في Ollama المحلي.

    python3 tools/evaluate_retrieval.py --embedder qwen3-embedding:0.6b --license Apache-2.0 --baseline bge-m3 \\
        --agent <معرّفك> --out docs/probe/g3-hybrid-vs-bm25-<التاريخ>.json

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
from evaluation.retrieval_general import BANK, BANK_SHA256, BOOTSTRAP, RRF_K, comparisons, load_bank, run  # noqa: E402

# خطُّ الأساس المسجَّل في مواصفة غ٣ (docs/PLAN-20260926.md)، ورخصتُه من بطاقته (BAAI/bge-m3)
BASELINE = "bge-m3"
BASELINE_LICENSE = "MIT"

LIMITS = [
    "bank_authored_by_a_developer_family_one_gold_passage_per_query_not_blind",
    "sixty_short_passages_a_small_corpus_so_absolute_scores_run_high",
    "bm25_arm_has_no_morphological_expansion_unlike_the_product_hybrid_retriever",
    "embedder_is_a_retriever_not_a_judge_gold_labels_decide",
    "single_run_hit_interval_is_wilson_on_the_design_effect_size_ndcg_and_effect_by_seeded_passage_bootstrap",
    "fusion_uses_depth_50_per_channel_and_one_or_matched_bm25_list_unlike_the_product_2x_limit_and_exact_plus_any_lists",
    "the_protocol_statistic_agresti_min_treats_queries_as_independent_the_clustered_effect_interval_is_beside_it",
    "vectors_vs_bm25_is_descriptive_the_k46_vector_rule_decides_the_hybrid_channel_only",
    "no_protocol_decision_here_the_k46_vector_rule_needs_the_product_hybrid_retriever_with_only_vector_toggled",
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
    # خطُّ الأساس لازم: مواصفةُ غ٣ تقارن بـbge-m3، وتقريرٌ بلا خطّ أساسٍ لا يستوفي المهمّة (ملاحظة Codex على #132)
    parser.add_argument("--license", required=True, help="رخصةُ أوزان المُضمِّن كما في بطاقته")
    parser.add_argument("--baseline", required=True, help="مُضمِّنُ خطّ الأساس، وهو bge-m3 في مواصفة غ٣ لا غيرُه")
    parser.add_argument("--agent", required=True, help="معرّفُ من يشغّل القياس، مسجَّلًا في registry/agents.json")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"التقريرُ قائم: {args.out}")
    if args.agent not in json.loads((ROOT / "registry" / "agents.json").read_text(encoding="utf-8"))["agents"]:
        parser.error(f"agent_unregistered: {args.agent}")
    # البصمةُ قبل أيّ تضمين: تقريرٌ بالوسم وحده لا يسمّي الأوزانَ التي أنتجت المتّجهات (ملاحظة Codex على #132)
    # bge-m3 بعينه لا أيُّ مُضمِّنٍ آخر (ملاحظة Codex على #132)؛ والوسمُ بلا إصدارٍ هو latest عند Ollama
    if args.baseline not in (BASELINE, BASELINE + ":latest"):
        parser.error(f"baseline_not_registered: خطُّ أساس غ٣ هو {BASELINE}")
    if args.baseline == args.embedder:
        parser.error("baseline_is_the_embedder: خطُّ الأساس مُضمِّنٌ آخر")
    digests = {name: _digest(name) for name in (args.embedder, args.baseline)}
    missing = sorted(name for name, value in digests.items() if not value)
    if missing:
        print(json.dumps({"status": "refused", "code": "embedder_digest_unresolved", "models": missing},
                         ensure_ascii=False))
        return 2
    bank = load_bank()
    main_run = run(bank, OllamaEmbedder(args.embedder))
    base = run(bank, OllamaEmbedder(args.baseline))
    # والبصمةُ نفسُها بعد آخر تضمين، فوسمٌ أُعيد توجيهُه أثناء التشغيل لا يُنسب إليه ما ضمّنه غيرُه
    drifted = sorted(name for name, value in digests.items() if _digest(name) != value)
    if drifted:
        print(json.dumps({"status": "refused", "code": "embedder_digest_drifted", "models": drifted},
                         ensure_ascii=False))
        return 2
    rows = main_run.pop("rows")
    channels = main_run.pop("channels")
    protocol = ablation.protocol()
    report = {
        "schema_version": 1, "kind": "g3_retrieval", "task": "غ٣", "issue": "power0man/diwan#24",
        "date": datetime.date.today().isoformat(), "agent": args.agent,
        # المحرّكُ هنا المُضمِّن: لا نموذجَ توليد في غ٣ (ملاحظة Codex على #132: التقريرُ يحمل المحرّكَ ورخصتَه وبذورَه)
        "engine": {"provider": "ollama-local", "model": args.embedder, "digest": digests[args.embedder],
                   "license": args.license},
        "config": {"bank": str(BANK.relative_to(ROOT)), "bank_sha256": BANK_SHA256, "rrf_k": RRF_K,
                   "embedder": {"model": args.embedder, "digest": digests[args.embedder], "provider": "ollama-local"},
                   "protocol_sha256": ablation._sha(ablation.PROTOCOL.read_bytes()),
                   "protocol_vectors_status": protocol["components"]["vectors"]["status"],
                   "bootstrap": BOOTSTRAP},
        "arms": main_run["arms"], "comparisons": comparisons(rows, main_run["arms"]),
        "rows": _recorded(rows),
        "channels": _channels(channels),
        "measurement_limits": LIMITS,
    }
    base_rows = base.pop("rows")
    base_channels = base.pop("channels")
    report["baseline"] = {"embedder": {"model": args.baseline, "digest": digests[args.baseline],
                                       "license": BASELINE_LICENSE},
                          "arms": {arm: base["arms"][arm] for arm in ("vectors", "hybrid")},
                          "rows": _recorded({arm: base_rows[arm] for arm in ("vectors", "hybrid")}),
                          "channels": _channels(base_channels)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({arm: report["arms"][arm]["overall"] for arm in report["arms"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
