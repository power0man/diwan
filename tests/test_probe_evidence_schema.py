"""The publishable #188 guard is proved only with synthetic evidence."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import probe_evidence as pe  # noqa: E402


def _valid() -> dict:
    return {
        "agent": "openai/codex",
        "date": "2026-09-30",
        "measurement_limits": ["synthetic_fixture_not_a_product_measurement"],
    }


def test_current_evidence_requires_agent_date_and_limits():
    payload = _valid()
    assert pe.validate_payload(payload) == []
    payload.pop("agent")
    assert "missing_agent" in pe.validate_payload(payload)
    payload = _valid()
    payload["date"] = "2026-9-30"
    assert "invalid_date" in pe.validate_payload(payload)
    payload = _valid()
    payload["measurement_limits"] = []
    assert "missing_measurement_limits" in pe.validate_payload(payload)


def test_historical_evidence_requires_a_safe_existing_successor():
    payload = {"historical": True, "superseded_by": "new.json"}
    assert pe.validate_payload(payload, available_names={"old.json", "new.json"}) == []
    assert "superseded_by_missing" in pe.validate_payload(
        payload, available_names={"old.json"}
    )
    payload["superseded_by"] = "../new.json"
    assert "invalid_superseded_by" in pe.validate_payload(payload)


def test_private_access_metadata_is_rejected_without_echoing_its_value():
    payload = {**_valid(), "api_key": "synthetic-placeholder"}
    errors = pe.validate_payload(payload)
    assert errors == ["private_access_metadata"]
    assert "synthetic-placeholder" not in json.dumps(errors)


def test_local_operation_metadata_is_rejected_by_category_only():
    payload = {**_valid(), "endpoint": "http://127.0.0.1:9999/synthetic"}
    assert pe.validate_payload(payload) == ["private_operational_metadata"]


def test_remote_operation_timing_and_provider_are_rejected():
    payload = {
        **_valid(),
        "runtime": "synthetic cloud job",
        "attempt_seconds": 3,
        "provider": "synthetic-vendor",
    }
    assert pe.validate_payload(payload) == [
        "private_operational_metadata",
        "private_operational_provider",
        "private_operational_timing",
    ]


def test_payment_response_metadata_is_rejected():
    payload = {**_valid(), "response": "synthetic HTTP 402 response"}
    assert pe.validate_payload(payload) == ["private_operational_metadata"]


def test_named_redaction_marker_is_accepted():
    payload = {
        **_valid(),
        "host": pe.REDACTION_MARKER,
        "api_key": pe.REDACTION_MARKER,
    }
    assert pe.validate_payload(payload) == []


def test_cli_report_contains_no_input_name_path_or_value(tmp_path):
    evidence = tmp_path / "synthetic-private-name.json"
    evidence.write_text(
        json.dumps({**_valid(), "api_key": "synthetic-private-value"}),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "probe_evidence.py"), str(evidence)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "private_access_metadata" in completed.stdout
    assert "synthetic-private-name" not in completed.stdout
    assert "synthetic-private-value" not in completed.stdout
    assert str(tmp_path) not in completed.stdout
