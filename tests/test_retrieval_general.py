"""غ٣: البنكُ العامّ مجمَّدٌ ببصمته، والهجينُ دمجٌ حقيقيٌّ للقناتين، والتقريرُ يُعاد بلا نموذج.

المُضمِّنُ في هذه الاختبارات `HashEmbedder` الحتميّ غيرُ الدلاليّ، فلا يشهد برقمٍ دلاليّ؛
الرقمُ الحيّ في `docs/probe/g3-hybrid-vs-bm25-<التاريخ>.json`.
"""
from __future__ import annotations

import json

import pytest

from core.vector_retrieval import HashEmbedder
from evaluation.retrieval_general import (BANK, MIN_QUERIES, RetrievalBankError, load_bank, paired, rrf, run,
                                          wilson)


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
