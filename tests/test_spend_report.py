"""التقريرُ الشهريّ للإنفاق (البند ٥ من #295؛ `tools/spend_report.py`): مجموعُ كتل الشهر صحيح، والفاتورةُ معطاةٌ لا مخمَّنة."""
from __future__ import annotations

import json

import pytest

from tools import probe_evidence as pe
from tools import spend_report as sr


def _spend(basis, calls=1, prompt=10, completion=5, cost=0.0):
    return {"cloud_calls": calls, "prompt_tokens": prompt, "completion_tokens": completion, "cost_usd": cost,
            "cost_basis": basis}


EVIDENCE = {
    "a-20261001.json": {"date": "2026-10-01", "spend": _spend("estimated_from_prices", 2, 600, 150, 0.15)},
    "a2-20261001.json": {"date": "2026-10-01", "spend": _spend("estimated_from_prices", 1, 400, 50, 0.1)},
    "b-20261002.json": {"date": "2026-10-02T10:00:00Z", "spend": _spend("reported_by_provider", 2, 400, 100, 0.5)},
    "c-20261003.json": {"date": "2026-10-03", "spend": _spend("subscription_flat", 4, 50, 50, 0.0)},
    "d-20261004.json": {"date": "2026-10-04", "spend": _spend("unpriced", 1, 7, 3, None)},
    "e-20260930.json": {"date": "2026-09-30", "spend": _spend("estimated_from_prices", 9, 9, 9, 9.0)},
    "f-20261005.json": {"date": "2026-10-05", "spend": _spend("free_tier", 0, 0, 0, 0.0)},
    "g-undated.json": {"spend": _spend("estimated_from_prices", 1, 1, 1, 1.0)},
    "h-20261006.json": {"date": "2026-10-06", "results": []},
}
HF = {"cost_usd": 1.0, "read_on": "2026-11-01", "source": "https://huggingface.co/settings/billing"}


def test_the_month_sums_its_valid_spend_blocks_by_basis():
    totals = sr.evidence_totals(EVIDENCE, "2026-10")
    assert totals["by_basis"] == {
        "estimated_from_prices": {"files": 2, "cloud_calls": 3, "prompt_tokens": 1000, "completion_tokens": 200, "cost_usd": 0.25},
        "reported_by_provider": {"files": 1, "cloud_calls": 2, "prompt_tokens": 400, "completion_tokens": 100, "cost_usd": 0.5},
        "subscription_flat": {"files": 1, "cloud_calls": 4, "prompt_tokens": 50, "completion_tokens": 50, "cost_usd": 0.0},
        "unpriced": {"files": 1, "cloud_calls": 1, "prompt_tokens": 7, "completion_tokens": 3, "cost_usd": None},
    }
    assert totals["known_cost_usd"] == 0.75 and totals["metered_cost_usd"] == 0.75
    assert totals["unpriced_files"] == ["d-20261004.json"]


def test_a_malformed_spend_block_is_named_not_summed():
    totals = sr.evidence_totals(EVIDENCE, "2026-10")
    assert totals["rejected_spend"] == ["spend_cloud_basis_without_calls:f-20261005.json"]
    assert "free_tier" not in totals["by_basis"]


def test_spend_without_a_date_is_named_in_every_month():
    assert sr.evidence_totals(EVIDENCE, "2026-10")["undated_spend"] == ["g-undated.json"]
    assert sr.evidence_totals(EVIDENCE, "2026-09")["undated_spend"] == ["g-undated.json"]


def test_the_hf_invoice_difference_is_what_no_evidence_accounts_for():
    report = sr.build(EVIDENCE, "2026-10", sr.invoice(1.0, "2026-11-01", HF["source"], "2026-10"), "2026-11-01")
    assert report["hf_invoice"] == HF and report["hf_invoice_status"] == "read"
    assert report["hf_unaccounted_usd"] == 0.25


def test_without_an_invoice_nothing_is_guessed():
    report = sr.build(EVIDENCE, "2026-10", None, "2026-10-06")
    assert report["hf_invoice"] is None and report["hf_invoice_status"] == "not_read"
    assert report["hf_unaccounted_usd"] is None


@pytest.mark.parametrize("amount, read_on, source, code", [
    pytest.param(1.0, None, None, "hf_invoice_incomplete", id="incomplete"),
    pytest.param(float("nan"), "2026-11-01", HF["source"], "hf_invoice_amount_invalid", id="nan"),
    pytest.param(-1.0, "2026-11-01", HF["source"], "hf_invoice_amount_invalid", id="negative"),
    pytest.param(1.0, "2026-09-30", HF["source"], "hf_invoice_read_on_invalid", id="read_before_the_month"),
    pytest.param(1.0, "2026-11-1", HF["source"], "hf_invoice_read_on_invalid", id="malformed_day"),
    pytest.param(1.0, "2026-11-01", "huggingface.co/settings/billing", "hf_invoice_source_invalid", id="not_https"),
])
def test_an_invoice_is_refused_by_name(amount, read_on, source, code):
    with pytest.raises(ValueError, match=f"^{code}$"):
        sr.invoice(amount, read_on, source, "2026-10")


def test_a_written_report_is_checked_against_its_evidence(tmp_path):
    report = sr.build(EVIDENCE, "2026-10", HF, "2026-11-01")
    assert sr.check(report, EVIDENCE) == []
    edited = json.loads(json.dumps(report))
    edited["evidence"]["known_cost_usd"] = 0.0
    assert sr.check(edited, EVIDENCE) == ["evidence_totals_differ"]
    edited = json.loads(json.dumps(report))
    edited["hf_unaccounted_usd"] = 0.0
    assert sr.check(edited, EVIDENCE) == ["hf_unaccounted_usd_differs"]
    late = dict(EVIDENCE, **{"i-20261020.json": {"date": "2026-10-20", "spend": _spend("estimated_from_prices", cost=1.0)}})
    assert sr.check(report, late) == ["evidence_totals_differ", "hf_unaccounted_usd_differs"]


def test_the_build_command_writes_a_report_that_its_check_passes(tmp_path):
    for name, payload in EVIDENCE.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    assert sr.main(["--probe", str(tmp_path), "build", "--month", "2026-10", "--hf-invoice-usd", "1",
                    "--hf-read-on", "2026-11-01", "--hf-source", HF["source"], "--today", "2026-11-01"]) == 0
    assert sr.main(["--probe", str(tmp_path), "check"]) == 0
    assert sr.main(["--probe", str(tmp_path), "build", "--month", "2026-13"]) == 2


def test_the_report_carries_no_field_the_privacy_guard_withholds():
    report = sr.build(EVIDENCE, "2026-10", sr.invoice(1.0, "2026-11-01", HF["source"], "2026-10"), "2026-11-01")

    def keys(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                yield key
                yield from keys(inner)
        elif isinstance(value, list):
            for inner in value:
                yield from keys(inner)

    assert not set(keys(report)) & pe.OPERATIONAL_FIELDS
    assert not [key for key in keys(report) if pe.TIMING_FIELD.search(key)]

