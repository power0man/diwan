#!/usr/bin/env python3
"""تشخيصُ هبوط الهجين في غ٣: لماذا تبلغ المتّجهاتُ وحدها ٠٫٩٩ في hit@5 ويهبط بها الهجينُ إلى ٠٫٨٣؟

يعيد ترتيبَ القناتين المسجَّلتين في تقرير غ٣ (`channels`) بطرق دمجٍ مختلفة، بلا مُضمِّنٍ ولا شبكة. فالمتّجهاتُ هي
التي رتّبها المُضمِّن في التقرير نفسِه، وBM25 يُعاد على البنك المجمَّد. ويحاكي دمجَ المنتج (`HybridRetriever.search`):
مطابقةٌ صارمة بعمق ضعف الحدّ، ثم المفرداتُ الصرفية، ثم «أيُّ كلمة» بنصف الوزن إن قلّت الصارمةُ عن الحدّ، والمتّجهاتُ
بعمق ضعف الحدّ.

    python3 tools/diagnose_hybrid.py --agent <معرّفك> --out docs/probe/g3-hybrid-diagnosis-<التاريخ>.json

وما يخرجه تشخيصٌ لا قياس: الطرقُ تُختار وتُقاس على البنك نفسِه، فلا يُعتمد منها وزنٌ للمنتج بلا بنكٍ مستقلّ.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.linguistics.morphology import analyze  # noqa: E402
from evaluation import retrieval_general as rg  # noqa: E402
from tools.rebuild_index import normalize  # noqa: E402

REPORT = ROOT / "docs" / "probe" / "g3-hybrid-vs-bm25-20260928.json"
LIMIT = 5  # حدُّ النتائج في `hybrid_search` الافتراضيّ، وhit@5 عليه

LIMITS = [
    "replays_the_recorded_channels_of_the_g3_report_no_embedder_was_run_the_vector_rankings_are_that_report_s",
    "variants_are_chosen_and_scored_on_the_same_120_query_bank_so_any_weight_here_is_in_sample_not_a_measurement",
    "product_like_mirrors_hybrid_retriever_search_over_the_bank_bm25_not_the_product_page_index_or_chunking",
    "sixty_short_passages_a_vector_list_of_50_covers_83_percent_of_the_corpus_which_amplifies_double_credit",
    "bank_authored_by_a_developer_family_one_gold_passage_per_query_not_blind",
    "a_diagnosis_not_a_protocol_decision_the_k46_vector_rule_still_needs_the_product_retriever_run_with_vectors_toggled",
]


class DiagnosisRefused(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code


class BankBm25:
    """BM25 على البنك بصياغة `tools/rebuild_index.search`: كلماتُ التطبيع مفصولةً بالمسافة، كلُّها أو أيُّها."""

    def __init__(self, documents: list[dict]):
        self._index = rg._Bm25(documents)

    def search(self, query: str, depth: int, *, match_any: bool) -> list[str]:
        tokens = [t.replace('"', "") for t in normalize(query).split()]
        tokens = [t for t in tokens if t]
        if not tokens:
            return []
        expression = (" OR " if match_any else " ").join('"' + t + '"' for t in tokens)
        rows = self._index.con.execute("SELECT id FROM docs WHERE docs MATCH ? ORDER BY bm25(docs), id LIMIT ?",
                                       (expression, depth)).fetchall()
        return [row[0] for row in rows]


def fuse(lists: list[tuple[list[str], float]], k: int = rg.RRF_K) -> list[str]:
    """RRF موزون: مجموعُ وزن/(k+رتبة+1) عبر القوائم، والتعادلُ لما أدخلته قائمةٌ أسبق (ترتيبٌ مستقرّ كالمنتج)."""
    scores: dict[str, float] = {}
    for ranking, weight in lists:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + weight / (k + rank + 1)
    return sorted(scores, key=lambda key: -scores[key])


def product_like(query: str, vectors: list[str], bm25: BankBm25, *, limit: int = LIMIT,
                 any_weight: float = 0.5) -> list[str]:
    """دمجُ `HybridRetriever.search` على البنك: الصارمةُ بعمق ضعف الحدّ، فالمفرداتُ الصرفية إن قلّت عن الحدّ، فأيُّ كلمةٍ
    بنصف الوزن، والمتّجهاتُ بعمق ضعف الحدّ؛ وكلُّ قائمةٍ بـ RRF ذي k=60."""
    exact = bm25.search(query, limit * 2, match_any=False)
    any_results: list[str] = []
    if len(exact) < limit:
        lemmas: list[str] = []
        for token in (t for t in normalize(query).split() if len(t) >= 2):
            lemma = analyze(token).lemma
            if lemma and lemma != token and lemma not in lemmas:
                lemmas.append(lemma)
        if lemmas:
            exact.extend(d for d in bm25.search(" ".join(lemmas), limit, match_any=False) if d not in exact)
        any_results = bm25.search(query, limit * 2, match_any=True)
    return fuse([(exact, 1.0), (any_results, any_weight), (vectors[:limit * 2], 1.0)])


def hit_rates(rankings: dict[str, list[str]], queries: list[dict]) -> dict:
    by_type: dict[str, list[int]] = {}
    for q in queries:
        hit = any(d in q["relevant"] for d in rankings[q["id"]][:LIMIT])
        by_type.setdefault(q["type"], [0, 0])
        by_type[q["type"]][0] += hit
        by_type[q["type"]][1] += 1
    total = sum(h for h, _ in by_type.values()) / sum(n for _, n in by_type.values())
    return {"hit_at_5": round(total, 4), "by_type": {t: round(h / n, 4) for t, (h, n) in sorted(by_type.items())}}


def load_report(path: Path, bank: dict) -> dict:
    """تقريرُ غ٣ على البنك المجمَّد، وقناتاه تعيدان أذرعَه الثلاثة المسجَّلة برتبها؛ وإلا فلا يُبنى عليه تشخيص."""
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        bank_sha, channels = report["config"]["bank_sha256"], report["channels"]
        recorded = {arm: {row["id"]: (row["rank"], row["hit_at_5"]) for row in report["rows"][arm]} for arm in rg.ARMS}
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        raise DiagnosisRefused("g3_report_unreadable") from None
    if bank_sha != rg.BANK_SHA256:
        raise DiagnosisRefused("g3_report_bank_not_frozen", "التقرير على بنكٍ غير المجمَّد")
    if not isinstance(channels, dict) or set(channels) != {q["id"] for q in bank["queries"]}:
        raise DiagnosisRefused("g3_report_channels_incomplete", "قناتا التقرير لا تغطّيان البنك")
    # قناةٌ غائبةٌ أو غيرُ نصٍّ رفضٌ مسمًّى لا أثرٌ خام (ملاحظة Codex على #293)
    if any(not isinstance(entry, dict) or not all(isinstance(entry.get(name), str) for name in ("bm25", "vectors"))
           for entry in channels.values()):
        raise DiagnosisRefused("g3_report_channels_malformed", "قناةٌ غائبة أو ليست نصًّا")
    # والقناتان من البنك المجمَّد نفسِه: BM25 تُعاد عليه حرفًا بحرف، والمتّجهاتُ معرّفاتٌ منه بلا تكرارٍ وبعمق القياس؛ فمعرّفاتٌ
    # مخترعة في ذيل قناةٍ لا تغيّر رتبةَ الذهبيّ وتغيّر عمقَها المنشور في الآلية (ملاحظة Codex على #293)
    known = {d["id"] for d in bank["documents"]}
    depth, lexical = min(rg.DEPTH, len(known)), rg._Bm25(bank["documents"])
    for q in bank["queries"]:
        vectors = channels[q["id"]]["vectors"].split()
        if (channels[q["id"]]["bm25"].split() != lexical.rank(q["text"], rg.DEPTH)
                or len(vectors) != depth or len(set(vectors)) != depth or not set(vectors) <= known):
            raise DiagnosisRefused("g3_report_channels_not_bank_rankings", "قناةٌ ليست ترتيبًا من البنك المجمَّد")
    # الأذرعُ الثلاث برتبها لا الهجينُ بإصاباته وحدها: فقناةٌ مُبدَلة تحفظ إصاباتِ الهجين تغيّر رتبَ غيره (ملاحظة Codex على #293)
    replayed = rg.arm_rows_from_channels(channels, bank)
    if any((row["rank"], row["hit_at_5"]) != recorded[arm].get(row["id"]) for arm in rg.ARMS for row in replayed[arm]):
        raise DiagnosisRefused("g3_report_channels_do_not_reproduce_its_arms")
    return report


def registered_agent(agent: str) -> str:
    """من يشغّل التشخيصَ يُسمّى بمعرّفه المسجَّل في registry/agents.json، فلا يُنسب الدليلُ إلى معرّفٍ لا يُتحقَّق منه
    (ملاحظة Codex على #293)."""
    agents = json.loads((ROOT / "registry" / "agents.json").read_text(encoding="utf-8"))["agents"]
    if not isinstance(agent, str) or agent not in agents:
        raise DiagnosisRefused("agent_unregistered", "مُشغِّلُ التشخيص غيرُ مسجَّل")
    return agent


def diagnose(report: dict, bank: dict) -> dict:
    queries, channels = bank["queries"], report["channels"]
    bm25 = BankBm25(bank["documents"])
    lexical = {q["id"]: channels[q["id"]]["bm25"].split() for q in queries}
    vectors = {q["id"]: channels[q["id"]]["vectors"].split() for q in queries}

    def variant(fn) -> dict:
        return hit_rates({q["id"]: fn(q) for q in queries}, queries)

    variants = {
        "vectors_only": variant(lambda q: vectors[q["id"]]),
        "bm25_only": variant(lambda q: lexical[q["id"]]),
        "g3_harness_rrf": variant(lambda q: fuse([(lexical[q["id"]], 1.0), (vectors[q["id"]], 1.0)])),
        **{f"g3_harness_vector_depth_{depth}": variant(
            lambda q, depth=depth: fuse([(lexical[q["id"]], 1.0), (vectors[q["id"]][:depth], 1.0)]))
           for depth in (5, 10, 20)},
        **{f"g3_harness_bm25_weight_{str(weight).replace('.', '_')}": variant(
            lambda q, weight=weight: fuse([(lexical[q["id"]], weight), (vectors[q["id"]], 1.0)]))
           for weight in (0.5, 0.25, 0.1)},
        "product_like": variant(lambda q: product_like(q["text"], vectors[q["id"]], bm25)),
        "product_like_without_any_list": variant(
            lambda q: product_like(q["text"], vectors[q["id"]], bm25, any_weight=0.0)),
    }

    # الآلية: ما أخرج المقطعَ الذهبيّ من الخمسة الأولى حيث أصابته المتّجهاتُ وأخطأه دمجُ غ٣
    lost, above_in_both, gold_in_bm25 = [], [], 0
    for q in queries:
        lex, vec = lexical[q["id"]], vectors[q["id"]]
        fused = fuse([(lex, 1.0), (vec, 1.0)])
        if any(d in q["relevant"] for d in vec[:LIMIT]) and not any(d in q["relevant"] for d in fused[:LIMIT]):
            lost.append(q["type"])
            gold = next(d for d in fused if d in q["relevant"])
            above_in_both.append(sum(1 for d in fused[:fused.index(gold)] if d in lex and d in vec))
            gold_in_bm25 += gold in lex
    corpus = len(bank["documents"])
    mechanism = {
        "corpus_passages": corpus,
        "vector_list_depth": max(len(v) for v in vectors.values()),
        "bm25_or_list_median_length": statistics.median(len(v) for v in lexical.values()),
        "queries_lost_by_fusion": len(lost),
        "queries_lost_by_type": {t: lost.count(t) for t in sorted(set(lost))},
        "lost_queries_with_the_gold_absent_from_bm25": len(lost) - gold_in_bm25,
        "median_passages_above_the_gold_credited_by_both_channels": statistics.median(above_in_both) if lost else 0,
    }
    findings = [
        "the_g3_hybrid_arm_is_not_the_product_fusion_so_its_hit_at_5_is_not_the_product_number",
        "the_drop_is_double_credit_the_or_matched_bm25_list_and_a_vector_list_covering_most_of_the_corpus_give_wrong_"
        "passages_two_rrf_terms_while_a_paraphrased_gold_absent_from_bm25_gets_one",
    ]
    return {"variants": variants, "mechanism": mechanism, "findings": findings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--agent", required=True, help="معرّفُ من يشغّل التشخيص، مسجَّلًا في registry/agents.json")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        agent = registered_agent(args.agent)
        bank = rg.load_bank()
        report = load_report(args.report, bank)
    except (DiagnosisRefused, rg.RetrievalBankError) as exc:
        print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
        return 2
    out = {"schema_version": 1, "kind": "g3-hybrid-diagnosis", "task": "تشخيص الهجين (#287)",
           "date": datetime.date.today().isoformat(), "agent": agent,
           "source_report": Path(args.report).name,
           "source_report_sha256": hashlib.sha256(Path(args.report).read_bytes()).hexdigest(),
           "bank_sha256": rg.BANK_SHA256, "limit": LIMIT, "rrf_k": rg.RRF_K,
           **diagnose(report, bank), "measurement_limits": LIMITS}
    text = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
