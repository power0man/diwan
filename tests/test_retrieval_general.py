"""غ٣: البنكُ العامّ مجمَّدٌ ببصمته، والهجينُ دمجٌ حقيقيٌّ للقناتين، والتقريرُ يُعاد بلا نموذج.

المُضمِّنُ في هذه الاختبارات `HashEmbedder` الحتميّ غيرُ الدلاليّ، فلا يشهد برقمٍ دلاليّ؛
الرقمُ الحيّ في `docs/probe/g3-hybrid-vs-bm25-<التاريخ>.json`.
"""
from __future__ import annotations

import json

import pytest

from core.vector_retrieval import HashEmbedder
from evaluation import ablation
from evaluation.retrieval_general import (BANK, MIN_QUERIES, ROOT, RetrievalBankError, cluster_bootstrap, comparisons,
                                          design_effect, hit_interval, hybrid_rows_from_channels, load_bank, paired,
                                          rows_from_report, rrf, run, summaries, wilson)

EVIDENCE = ROOT / "docs" / "probe" / "g3-hybrid-vs-bm25-20260927.json"


def test_the_bank_is_frozen_general_and_large_enough():
    bank = load_bank()
    assert len(bank["queries"]) >= MIN_QUERIES
    assert {q["type"] for q in bank["queries"]} == {"lexical", "paraphrase"}
    assert len({d["topic"] for d in bank["documents"]}) >= 10


def test_an_edited_bank_is_refused_before_measuring(tmp_path):
    edited = tmp_path / "bank.json"
    data = json.loads(BANK.read_text(encoding="utf-8"))
    data["queries"][0]["text"] += " "
    edited.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RetrievalBankError) as err:
        load_bank(edited)
    assert err.value.code == "bank_not_frozen"


def test_rrf_lifts_a_passage_both_channels_found_above_either_channels_first():
    fused = rrf([["a", "b", "c"], ["c", "d"]])
    assert fused[0] == "c", "المقطعُ الذي وجدته القناتان يتقدّم"
    assert set(fused) == {"a", "b", "c", "d"}, "الهجينُ يضمّ ما وجدته كلُّ قناة"


def test_the_full_run_reports_three_arms_and_the_hybrid_is_the_fusion_of_the_two():
    report = run(load_bank(), HashEmbedder())
    rows = report["rows"]
    assert set(report["arms"]) == {"bm25", "vectors", "hybrid"}
    assert all(len(rows[arm]) == len(rows["bm25"]) >= MIN_QUERIES for arm in rows)
    differs = sum(h["top5"] != b["top5"] for h, b in zip(rows["hybrid"], rows["bm25"]))
    assert differs > 0, "الهجينُ لا يختلف عن BM25 في أي استعلام: الدمجُ معطَّل"
    on, off = paired(rows["hybrid"], rows["bm25"])
    assert [r["id"] for r in on] == [r["id"] for r in off]


def test_wilson_interval_is_inside_zero_one_and_contains_the_rate():
    low, high = wilson(90, 120)
    assert 0 <= low < 0.75 < high <= 1


def _recorded():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    rows = rows_from_report(evidence["rows"], load_bank())
    return evidence, rows, summaries(rows)


def test_the_published_numbers_are_recomputed_from_the_recorded_rows():
    """الدليلُ يُعاد من صفوفه المسجَّلة والبنك وحدهما، بلا مُضمِّن: الأذرعُ ومجالاتُها والمقارناتُ وحكمُ البروتوكول."""
    evidence, rows, arms = _recorded()
    assert evidence["arms"] == arms
    assert evidence["comparisons"] == comparisons(rows, arms)
    # وصفوفُ الهجين تُعاد من رتبتَي القناتين إن سُجّلتا، وإلا فالدليلُ يعلن أنها تسبق ترتيبَ التعادل في المنتج
    if "channels" in evidence:
        assert evidence["rows"]["hybrid"] == hybrid_rows_from_channels(evidence["channels"], load_bank())
    else:
        assert any(limit.startswith("hybrid_rows_predate_the_product_tie_order")
                   for limit in evidence["measurement_limits"])


def test_a_full_run_records_both_channels_so_the_fusion_is_recomputed_without_a_model():
    report = run(load_bank(), HashEmbedder())
    channels = {qid: {name: " ".join(ids) for name, ids in pair.items()} for qid, pair in report["channels"].items()}
    recorded = [{k: r[k] for k in ("id", "type", "rank", "hit_at_5", "ndcg_at_10")} for r in report["rows"]["hybrid"]]
    assert hybrid_rows_from_channels(channels, load_bank()) == recorded


def test_rrf_ties_go_to_the_earlier_channel_as_in_the_product():
    """ملاحظةُ Codex على #132: `HybridRetriever` يرتّب بالنقاط وحدها ترتيبًا مستقرًّا، فالتعادلُ لـBM25 لا للمعرّف."""
    assert rrf([["b"], ["a"]]) == ["b", "a"]
    assert rrf([["a"], ["b"]]) == ["a", "b"]


def test_a_sample_where_everything_succeeds_keeps_its_uncertainty():
    """ملاحظةُ Codex على #132: ٦٠/٦٠ نُشرت بمجال [1.0, 1.0]؛ Wilson على الحجم الفعليّ يُبقي الحدَّ الأدنى دون الواحد،
    واستعلاما المقطع المتطابقان يُعدّان واحدًا (أثرُ التصميم ٢)."""
    singles = [{"cluster": f"p{i}", "hit_at_5": True} for i in range(60)]
    assert hit_interval(singles)[0] < 0.95 and hit_interval(singles)[1] == 1.0
    twins = [{"cluster": f"p{i}", "hit_at_5": i % 2 == 0} for i in range(60) for _ in range(2)]
    assert design_effect(twins, lambda r: float(r["hit_at_5"])) == 2.0
    assert hit_interval(twins) == wilson(30, 60)


def test_these_synthetic_arms_never_yield_a_protocol_decision(monkeypatch):
    """ملاحظاتُ Codex على #132: قاعدةُ ك٤٦ تقرّر قناةَ المتّجهات في `HybridRetriever` المنتج، وهذه الأذرعُ ليست هو
    (BM25 بلا توسيعٍ صرفيّ، والهجينُ بلا exact/any، والمقاطعُ عناقيدُ لا يراها الحكم). فلا حكمَ منها ولو صار المكوّنُ
    جاهزًا، ولا يُستدعى `ablation.judge`."""
    _, rows, arms = _recorded()
    ready = ablation.protocol()
    ready["components"]["vectors"]["status"] = "ready"
    monkeypatch.setattr(ablation, "protocol", lambda: ready)
    monkeypatch.setattr(ablation, "judge", lambda *a, **k: pytest.fail("حكمٌ من أذرعٍ ليست المنتج"))
    result = comparisons(rows, arms)
    assert {v["protocol"]["decision"] for v in result.values()} == {"not_applied"}
    assert result["hybrid_vs_bm25"]["protocol"]["component_status"] == "ready"


def test_intervals_resample_gold_passages_not_queries():
    """ملاحظةُ Codex على #132: استعلاما المقطع الواحد مترابطان، فإعادةُ المعاينة بالمقطع.
    ستون مقطعًا لكلٍّ استعلامان متطابقان: مجالُها مجالُ ستين قيمة، أعرضُ من مئةٍ وعشرين مستقلّة بنحو √٢."""
    values = [float(i % 2) for i in range(60)]
    twins = [{"cluster": f"p{i}", "v": v} for i, v in enumerate(values) for _ in range(2)]
    singles = [{"cluster": f"q{i}", "v": row["v"]} for i, row in enumerate(twins)]
    width = lambda ci: ci[1] - ci[0]
    assert width(cluster_bootstrap(twins, lambda r: r["v"])) > 1.3 * width(cluster_bootstrap(singles, lambda r: r["v"]))
