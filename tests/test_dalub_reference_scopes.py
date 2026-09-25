"""Disclose what the legacy deterministic DALUB scores actually measure."""
import hashlib
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import pytest

import acceptance_m16 as m16
from tools import evaluate_dalub as dalub
from evaluation import dalub_runner

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evaluation/suites/dalub_v1.json"


def test_existing_scores_remain_compatible_while_reference_and_component_scopes_differ():
    report = dalub.evaluate_suite(SUITE)
    assert (report["status"], report["total_cases"], report["total_passed"],
            report["overall_accuracy"]) == ("passed", 400, 400, 1.0)
    assert report["scope"] == "component_and_reference_self_checks"
    assert report["model_calls"] == 0
    assert report["product_readiness"] == "not_assessed"
    assert report["certified"] is False
    for pillar, result in report["pillars"].items():
        assert (result["total"], result["passed"], result["accuracy"]) == (80, 80, 1.0)
        reference = pillar in {"syntax", "semantics"}
        assert result["scope"] == ("reference_self_check" if reference else "component_check")
        assert (result["component"] is None) is reference


@pytest.mark.parametrize("case", [
    {"id": "synthetic-syntax", "pillar": "syntax", "type": "negative_polarity", "is_negated": True},
    {"id": "synthetic-semantics", "pillar": "semantics", "domain": "arbitrary", "expected_keywords": ["x", "y", "z"]},
])
def test_reference_checks_can_pass_without_any_input_or_system_answer(case, monkeypatch):
    monkeypatch.setattr(dalub, "analyze", lambda *a: pytest.fail("reference check invoked a component"))
    original = dalub.evaluate_case(case)  # No input or candidate answer exists.
    changed = dalub.evaluate_case({**case, "input": "unrelated text", "answer": "wrong answer"})
    assert original == changed
    assert original["passed"] is True
    assert original["scope"] == "reference_self_check"
    assert original["component"] is None


def test_component_scope_depends_on_actual_component_output(monkeypatch):
    case = {"id": "synthetic-morphology", "pillar": "morphology", "input": "مكتب",
            "expected_root": "كتب", "expected_pattern": "مفعل"}
    seen = []
    def component(word):
        seen.append(word)
        return SimpleNamespace(root="different", pattern="different")
    monkeypatch.setattr(dalub, "analyze", component)
    result = dalub.evaluate_case(case)
    assert seen == [case["input"]]
    assert result["passed"] is False
    assert result["scope"] == "component_check"
    assert result["component"] == "projections.morphology.analyze"


def test_failed_fixture_keeps_the_legacy_status_and_score(tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps({"cases": [{"id": "bad-ref", "pillar": "syntax",
        "type": "negative_polarity", "is_negated": False}]}))
    report = dalub.evaluate_suite(path)
    assert (report["status"], report["total_passed"], report["overall_accuracy"]) == ("failed", 0, 0.0)
    assert report["product_readiness"] == "not_assessed"


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_emits_limits_alongside_scores_without_model_or_network(monkeypatch, capsys, as_json):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("network is outside fixture checking"))
    monkeypatch.setattr(sys, "argv", ["evaluate_dalub.py"] + (["--json"] if as_json else []))
    assert dalub.main() == 0
    output = capsys.readouterr().out
    if as_json:
        report = json.loads(output)
        assert report["model_calls"] == 0 and report["certified"] is False
        assert report["pillars"]["semantics"]["scope"] == "reference_self_check"
    else:
        assert "فحص مرجع ذاتي" in output
        assert "جاهزية المنتج غير مقاسة" in output
        assert "معيار ديوان الوطني" not in output


def test_m16_preserves_six_checks_but_emits_current_counts_and_bounded_meaning(monkeypatch, capsys):
    original = m16.run_acceptance_m16
    captured = []
    def run(path):
        result = original(path)
        captured.append(result)
        return result
    monkeypatch.setattr(m16, "run_acceptance_m16", run)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("M16 must not invoke a model or network"))
    assert m16.main() == 0
    result = captured[0]
    assert result["status"] == "passed" and len(result["checks"]) == 6
    assert all(result["checks"].values())
    assert result["model_calls"] == 0 and result["legal_determination"] is False
    assert result["certified"] is False and result["product_readiness"] == "not_assessed"
    assert result["check_scopes"]["decision_q46_codified"] == "historical_text_presence_only"
    assert result["check_scopes"]["statutory_public_domain_article_4"] == "software_classification_fixtures_only"
    output = capsys.readouterr().out
    assert "400/400" in output and "200/200" not in output
    assert "مجاز" not in output and "سقوط الحماية" not in output
    assert "ليس اعتمادًا حاليًا" in output and "جاهزية المنتج غير مقاسة" in output


def test_bank_and_historical_score_report_remain_byte_identical():
    assert hashlib.sha256(SUITE.read_bytes()).hexdigest() == "1a91f6627fd76e7b5538432df3574f416a44f591b3bceaf1d66943cce1098b29"
    historical = ROOT / "docs/probe/dalub-benchmark-results.json"
    assert hashlib.sha256(historical.read_bytes()).hexdigest() == "df4ab1910ac15d00eca41a6fab3bf46162b491eb6c949fb551dd8c0decc4c71e"


@pytest.mark.parametrize("split,total,receipt", [
    ("all", 400, "3d845608aef2c90c855bc0d9ae2728db4e079f3dc470bb05407f45cad4d720e9"),
    ("dev", 320, "ca040f3a19a5e12d2c83cbcb1b7c7b5eabb0618d8e37983b89dccca487c60aee"),
    ("held_out", 80, "7d35137cc095a8c1a870e36ae0ffa9b4f099176508704ae237a4a5fd19aa6ab6"),
])
def test_split_runner_adds_limits_without_changing_legacy_receipt(split, total, receipt):
    report = dalub_runner.run_benchmark(split=split)
    assert report["receipt_hash"] == receipt
    assert (report["status"], report["total_cases"], report["total_passed"]) == ("passed", total, total)
    assert report["scope"] == "component_and_reference_self_checks"
    assert report["model_calls"] == 0 and report["certified"] is False
    assert report["product_readiness"] == "not_assessed"
    assert "unsigned_content_hash" in report["receipt_scope"]
    assert report["pillars"]["syntax"]["scope"] == "reference_self_check"
    assert report["pillars"]["quarantine"]["scope"] == "component_check"


def test_split_cli_states_that_held_out_is_not_hidden_and_receipt_is_not_signed(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["dalub_runner.py", "--split", "held_out"])
    assert dalub_runner.main() == 0
    output = capsys.readouterr().out
    assert "تسمية تاريخية لشطر مكشوف" in output
    assert "بصمة درجات غير موقعة" in output
    assert "reference_self_check" in output
    assert "جاهزية المنتج غير مقاسة" in output
