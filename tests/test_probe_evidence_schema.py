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


def test_explicit_file_set_checks_successors_and_index_without_inventory(tmp_path):
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(
        json.dumps({"historical": True, "superseded_by": "new.json"}),
        encoding="utf-8",
    )
    new.write_text(json.dumps(_valid()), encoding="utf-8")
    index = tmp_path / "INDEX.md"
    index.write_text(
        "# فهرس مصطنع\n\n"
        "| الدليل | الحكم |\n|---|---|\n"
        "| [`old.json`](old.json) | تاريخي |\n",
        encoding="utf-8",
    )

    checked, errors = pe.validate_files([old], index_path=index)
    assert checked == 1
    assert errors == {"superseded_by_missing": 1}

    checked, errors = pe.validate_files([old, new], index_path=index)
    assert checked == 2
    assert errors == {"index_missing_rows": 1}

    index.write_text(
        index.read_text(encoding="utf-8")
        + "| [`new.json`](new.json) | لم يُراجَع |\n",
        encoding="utf-8",
    )
    assert pe.validate_files([old, new], index_path=index) == (2, {})


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
    index = tmp_path / "synthetic-private-index.md"
    evidence.write_text(
        json.dumps({**_valid(), "api_key": "synthetic-private-value"}),
        encoding="utf-8",
    )
    index.write_text(
        "| [`synthetic-private-name.json`](synthetic-private-name.json) | مصطنع |\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "probe_evidence.py"),
         "--index", str(index), str(evidence)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "private_access_metadata" in completed.stdout
    assert "synthetic-private-name" not in completed.stdout
    assert "synthetic-private-index" not in completed.stdout
    assert "synthetic-private-value" not in completed.stdout
    assert str(tmp_path) not in completed.stdout


def test_malformed_url_is_rejected_without_cli_traceback_or_values(tmp_path):
    evidence = tmp_path / "synthetic-malformed-url.json"
    evidence.write_text(json.dumps({**_valid(), "note": "http://["}), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "probe_evidence.py"), str(evidence)],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 1
    assert completed.stderr == ""
    report = json.loads(completed.stdout)
    assert report["error_counts"] == {"private_access_metadata": 1}
    assert "http://[" not in completed.stdout
    assert str(evidence) not in completed.stdout


def test_historical_file_cannot_name_itself_as_successor(tmp_path):
    evidence = tmp_path / "self.json"
    evidence.write_text(
        json.dumps({"historical": True, "superseded_by": "self.json"}), encoding="utf-8"
    )
    assert pe.validate_files([evidence]) == (1, {"superseded_by_self": 1})


def test_schemeless_github_access_urls_are_rejected():
    for url in ("github.com/power0man/diwan/secrets",
                "www.github.com/synthetic/private/actions/runs/1",
                "api.github.com/repos/synthetic/private/actions/runs/1"):
        assert pe.validate_payload({**_valid(), "note": url}) == ["private_access_metadata"]
    assert pe.validate_payload({**_valid(), "note": "github.com/power0man/diwan/pull/245"}) == []


def test_public_repository_api_secrets_are_not_public_evidence():
    for url in ("https://api.github.com/repos/power0man/diwan/actions/secrets",
                "api.github.com/repos/power0man/diwan/actions/secrets"):
        assert pe.validate_payload({**_valid(), "note": url}) == ["private_access_metadata"]
    assert pe.validate_payload({**_valid(), "note": "https://api.github.com/repos/power0man/diwan/pulls/245"}) == []
