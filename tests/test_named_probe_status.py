"""Guard the one public historical report explicitly named in issue #188.

No repository inventory, private export, or measurement rerun is performed.
"""
import hashlib
import json
from pathlib import Path

from tools import probe_evidence


REPORT = Path(__file__).resolve().parents[1] / "docs/probe/dalub-benchmark-results.json"
ORIGINAL_FIELDS = (
    "schema_version", "benchmark", "title", "version", "date", "split",
    "total_cases", "duration_seconds", "receipt_hash", "summary", "by_pillar",
)


def test_named_dalub_report_is_withdrawn_and_has_required_evidence_metadata():
    payload = json.loads(REPORT.read_bytes())
    assert payload["status"] == "withdrawn"
    assert probe_evidence.validate_files([REPORT]) == (1, {})


def test_public_import_does_not_claim_to_identify_the_measurement_author():
    payload = json.loads(REPORT.read_bytes())
    assert payload["agent"] == "unrecorded"
    assert payload["date"] == "2026-09-23"
    provenance = payload["provenance"]
    assert provenance["first_public_commit"] == "23e5e5aca94a8b85a5af791ae4263424a7b0c050"
    assert provenance["first_public_date"] == "2026-09-25"
    assert provenance["first_public_agent"] == "anthropic/claude-fable-5-1"
    assert provenance["scope"] == "public_import_not_measurement_authorship"


def test_named_dalub_original_measurement_payload_is_unchanged():
    payload = json.loads(REPORT.read_bytes())
    original = {key: payload[key] for key in ORIGINAL_FIELDS}
    raw = json.dumps(original, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    # main@30515a3, excluding only the withdrawn certification label. Includes
    # every original score, count, date, duration, and unsigned receipt hash.
    assert hashlib.sha256(raw).hexdigest() == "e94d01b992f8246b9e1701c8fe3d70e58492252b4b6a871151350737b259725b"
