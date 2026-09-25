"""فحوص بنك DALUB وتصنيفات برمجية على fixtures (م١٦).

الفحوصات:
1. سلامة فحوص المكونات والمرجع والأرقام القديمة؛ لا تقييم نموذج.
2. مخرجات تصنيف الوثائق الرسمية في الشيفرة؛ لا حكم حقوق على نصوص حقيقية.
3. مخرجات تصنيف التراث والشروح على المدخلات المصطنعة المحددة.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

from core.corpus import (
    RIGHTS_HERITAGE_PUBLIC_DOMAIN,
    RIGHTS_RESTRICTED_COMMENTARY,
    RIGHTS_STATUTORY_PUBLIC_DOMAIN,
    classify_copyright_status,
)
from tools.evaluate_dalub import evaluate_suite

ROOT = Path(__file__).resolve().parent.parent


def test_dalub_suite_passes_all_pillars_completely():
    suite_path = ROOT / "evaluation" / "suites" / "dalub_v1.json"
    assert suite_path.exists()
    report = evaluate_suite(suite_path)
    assert report["status"] == "passed"
    assert report["total_cases"] == 400
    assert report["total_passed"] == 400
    assert report["overall_accuracy"] == 1.0
    assert set(report["pillars"].keys()) == {
        "morphology", "syntax", "semantics", "attribution", "quarantine"
    }
    assert all(p["total"] == 80 and p["accuracy"] == 1.0 for p in report["pillars"].values())


def test_dalub_runner_splits_and_receipts():
    from evaluation.dalub_runner import run_benchmark
    dev_res = run_benchmark(split="dev")
    assert dev_res["status"] == "passed"
    assert dev_res["total_cases"] == 320
    assert dev_res["overall_accuracy"] == 1.0
    assert len(dev_res["receipt_hash"]) == 64

    held_res = run_benchmark(split="held_out")
    assert held_res["status"] == "passed"
    assert held_res["total_cases"] == 80
    assert held_res["overall_accuracy"] == 1.0
    assert len(held_res["receipt_hash"]) == 64
    assert held_res["receipt_hash"] != dev_res["receipt_hash"]


@pytest.mark.parametrize("doc_type", [
    "regulation", "law", "decision", "judgment", "treaty", "official",
    "لائحة", "نظام", "قرار", "حكم", "معاهدة",
])
def test_statutory_official_documents_are_public_domain_under_article_4(doc_type):
    """هذه الأسماء تنتج تصنيفًا برمجيًا مسمى؛ ليس إقرارًا قانونيًا."""
    status = classify_copyright_status(doc_type)
    assert status == RIGHTS_STATUTORY_PUBLIC_DOMAIN


def test_heritage_texts_older_than_50_years_are_heritage_public_domain():
    """المدخل الزمني المصطنع ينتج هذا التصنيف في البرنامج."""
    status = classify_copyright_status("literature", author_death_year=1300, current_year=2026)
    assert status == RIGHTS_HERITAGE_PUBLIC_DOMAIN


def test_contemporary_commercial_commentary_is_restricted_to_internal():
    """علم الشرح التجاري يغيّر التصنيف البرمجي للمدخل المصطنع."""
    status = classify_copyright_status(
        "literature", author_death_year=1300, current_year=2026, has_commercial_commentary=True
    )
    assert status == RIGHTS_RESTRICTED_COMMENTARY


def test_dalub_three_arm_table_is_an_explicit_simulation():
    """تحقق شكل المحاكاة؛ لا استنتاج تفوق نموذج من الدرجات المفروضة."""
    from tools.evaluate_dalub_arms import run_dalub_three_arm_benchmark

    report = run_dalub_three_arm_benchmark(split="all", simulation=True)
    assert report["status"] == "simulation_only"
    assert report["measured_model_comparison"] is False
    assert report["certified"] is False
    assert report["model_calls"] == 0
    assert report["total_cases"] == 400
    assert len(report["receipt_hash"]) == 64
    assert set(report["summary"]) == {"model_alone", "naive_rag", "diwan_full"}
    assert set(report["by_pillar"]) == {
        "morphology", "syntax", "semantics", "attribution", "quarantine"}
    for summary in report["summary"].values():
        assert summary["total_cases"] == 400
        assert 0 <= summary["passed_cases"] <= summary["total_cases"]
        for key, value in summary.items():
            if key.endswith("_bp"):
                assert 0 <= value <= 10000


def test_dalub_simulation_splits_have_distinct_content_receipts():
    """فصل شطري البنك لا يجعل أيهما مقارنة نماذج مستقلة."""
    from tools.evaluate_dalub_arms import run_dalub_three_arm_benchmark

    dev_rep = run_dalub_three_arm_benchmark(split="dev", simulation=True)
    held_rep = run_dalub_three_arm_benchmark(split="held_out", simulation=True)
    assert dev_rep["total_cases"] == 320
    assert held_rep["total_cases"] == 80
    for report in (dev_rep, held_rep):
        assert report["status"] == "simulation_only"
        assert report["measured_model_comparison"] is False
        assert "held_out_split_is_not_independently_authored" in report["measurement_limits"]
    assert dev_rep["receipt_hash"] != held_rep["receipt_hash"]


def test_dalub_historical_mislabeled_report_is_preserved_as_evidence():
    """وسم certified القديم شاهد خطأ محفوظ؛ لا اعتماد حالي أو بوابة جودة."""
    probe_path = ROOT / "docs" / "probe" / "dalub-benchmark-results.json"
    raw = probe_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "df4ab1910ac15d00eca41a6fab3bf46162b491eb6c949fb551dd8c0decc4c71e"
    data = json.loads(raw)
    assert data["status"] == "certified"  # التاريخ كما وقع، بما فيه الوسم المضلل.
    assert data["total_cases"] == 400
