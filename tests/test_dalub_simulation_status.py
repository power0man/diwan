"""The historical score table cannot be emitted as measured model evidence."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

import pytest

from core.canonical import digest
from tools import evaluate_dalub_arms as arms


@pytest.mark.parametrize("opt_in", [False, None, 1, "true"])
def test_api_requires_literal_simulation_opt_in_before_reading_suite(tmp_path, opt_in):
    with pytest.raises(ValueError, match="simulation_required"):
        arms.run_dalub_three_arm_benchmark(tmp_path / "absent.json", simulation=opt_in)


def test_cli_without_simulation_refuses_before_any_output(tmp_path, monkeypatch, capsys):
    output = tmp_path / "not-created" / "result.json"
    monkeypatch.setattr(sys, "argv", ["evaluate_dalub_arms.py", "--out", str(output)])
    with pytest.raises(SystemExit) as exc:
        arms.main()
    assert exc.value.code == 2
    assert "--simulation is required" in capsys.readouterr().err
    assert not output.parent.exists()


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_simulation_emits_honest_artifact_without_network(tmp_path, monkeypatch, capsys, as_json):
    output = tmp_path / "var" / "simulation.json"
    monkeypatch.setattr(arms, "DEFAULT_OUT", output)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("simulation used network"))
    monkeypatch.setattr(sys, "argv", ["evaluate_dalub_arms.py", "--simulation"] + (["--json"] if as_json else []))
    assert arms.main() == 0
    report = json.loads(output.read_text())
    assert report["status"] == "simulation_only"
    assert report["measured_model_comparison"] is False
    assert report["certified"] is False
    assert report["model_calls"] == 0
    assert len(report["arm_provenance"]) == 3
    assert "baseline_scores_are_constants_case_labels_and_case_id_parity" in report["measurement_limits"]
    assert "quality_columns_are_synthetic_not_measured_answer_quality" in report["measurement_limits"]
    console = capsys.readouterr().out
    if as_json:
        assert json.loads(console) == report
    else:
        assert "simulation_only" in console
        assert "التقرير المعتمد" not in console


def test_default_artifact_is_under_untracked_runtime_state():
    assert arms.DEFAULT_OUT.parent == arms.ROOT / "var"


def test_identifier_parity_changes_simulated_baseline_without_model_answer():
    case = {"id": "SEM-010", "pillar": "semantics", "input": "نفس النص"}
    other = {**case, "id": "SEM-011"}
    assert arms.evaluate_arm_case(case, "model_alone")["passed"] is True
    assert arms.evaluate_arm_case(other, "model_alone")["passed"] is False
    first = arms.evaluate_arm_case(case, "naive_rag")
    second = arms.evaluate_arm_case({**case, "input": "نص مختلف تمامًا"}, "naive_rag")
    assert first == second  # Illustrates the fixed scores; it does not validate retrieval.


def test_failing_component_checks_still_cannot_become_model_certification(monkeypatch):
    monkeypatch.setattr(arms, "evaluate_case", lambda case: {"passed": False})
    report = arms.run_dalub_three_arm_benchmark(simulation=True)
    assert report["summary"]["diwan_full"]["passed_cases"] == 0
    assert report["status"] == "simulation_only"
    assert report["measured_model_comparison"] is False
    assert report["certified"] is False


def test_content_receipt_binds_the_disclosure_and_suite_not_run_time():
    report = arms.run_dalub_three_arm_benchmark(simulation=True)
    payload = {k: v for k, v in report.items() if k not in {
        "schema_version", "title", "generated_at", "duration_seconds",
        "receipt_hash", "receipt_scope", "by_pillar"}}
    assert digest(payload) == report["receipt_hash"]
    assert payload["suite_sha256"] == hashlib.sha256(arms.DEFAULT_SUITE.read_bytes()).hexdigest()
    assert digest({**payload, "measured_model_comparison": True}) != report["receipt_hash"]
    repeat = arms.run_dalub_three_arm_benchmark(simulation=True)
    assert repeat["receipt_hash"] == report["receipt_hash"]
    assert "not_unique_run_identity" in report["receipt_scope"]


def test_empty_simulation_suite_refuses_namedly(tmp_path):
    suite = tmp_path / "empty.json"
    suite.write_text('{"cases": []}')
    with pytest.raises(ValueError, match="empty_simulation_suite"):
        arms.run_dalub_three_arm_benchmark(suite, simulation=True)


@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink"])
def test_historical_report_cannot_be_overwritten_even_with_opt_in(tmp_path, monkeypatch, capsys, alias):
    historical = tmp_path / "historical.json"
    old = b'{"status":"certified","historical":true}\n'
    historical.write_bytes(old)
    output = historical
    if alias == "symlink":
        output = tmp_path / "symlink.json"
        output.symlink_to(historical)
    elif alias == "hardlink":
        output = tmp_path / "hardlink.json"
        os.link(historical, output)
    monkeypatch.setattr(arms, "HISTORICAL_REPORT", historical)
    monkeypatch.setattr(sys, "argv", ["evaluate_dalub_arms.py", "--simulation", "--out", str(output)])
    with pytest.raises(SystemExit) as exc:
        arms.main()
    assert exc.value.code == 2
    assert "historical_report_protected" in capsys.readouterr().err
    assert historical.read_bytes() == old
