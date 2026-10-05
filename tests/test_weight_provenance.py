"""دليلُ مصدر الأوزان يُنزّل الوزنَ من أصله المعلن ونصَّ رخصته من مصدره ويطابق البصمات (ملاحظة Codex على #307)."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from tools import model_licenses as ml
from tools import probe_spend
from tools import weight_provenance as wp

WEIGHT_BYTES, LICENSE_BYTES = b"weights", b"MIT License"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip(*members: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
    return buffer.getvalue()


WEIGHT = {"file": "w.pth", "sha256": _sha(WEIGHT_BYTES), "origin": "https://example.org/w.zip", "license": "mit",
          "license_source": "https://github.com/up/r/blob/c0ffee/LICENSE", "read_on": "2026-10-05",
          "license_text_sha256": _sha(LICENSE_BYTES)}
SERVED = {"https://example.org/w.zip": _zip(("w.pth", WEIGHT_BYTES), ("readme.txt", b"x")),
          "https://github.com/up/r/blob/c0ffee/LICENSE": LICENSE_BYTES}


def _models(**change) -> dict:
    return {"ocr": {"license": "apache-2.0", "source": "https://example.org", "read_on": "2026-10-05",
                    "weights": [{**WEIGHT, **change}]}}


def test_a_weight_measured_at_its_origin_is_recorded_and_the_evidence_passes_both_guards():
    models = _models(license_text_sha256=_sha(LICENSE_BYTES))
    evidence, problems = wp.measure(models, "2026-10-05", SERVED.__getitem__)
    assert problems == []
    record = evidence["models"]["ocr"]["weight_provenance"][0]
    assert record == {"file": "w.pth", "sha256": WEIGHT["sha256"], "origin": WEIGHT["origin"],
                      "origin_sha256": _sha(SERVED[WEIGHT["origin"]]), "license_source": WEIGHT["license_source"],
                      "license": WEIGHT["license"], "license_text_sha256": _sha(LICENSE_BYTES)}
    assert evidence["models"]["ocr"]["models_sha256"] == {"w.pth": WEIGHT["sha256"]}
    registry = {"enforced_from": "2026-10-05", "historical_evidence": [], "models": models}
    assert ml.findings(registry, {"p.json": evidence}, None) == []
    assert probe_spend.spend_findings("p.json", evidence["spend"]) == []
    assert ml.provenance_findings(_models(origin="https://evil.invalid/w.zip"), {"p.json": evidence}) == [
        "weight_provenance_not_in_evidence:ocr:w.pth"]


def test_a_weight_served_raw_is_hashed_as_it_is():
    served = {"https://example.org/w.pth": WEIGHT_BYTES, **SERVED}
    evidence, problems = wp.measure(_models(origin="https://example.org/w.pth"), "2026-10-05", served.__getitem__)
    assert problems == [] and evidence["models"]["ocr"]["weight_provenance"][0]["origin_sha256"] == WEIGHT["sha256"]


@pytest.mark.parametrize("served, change, found", [
    pytest.param({"https://example.org/w.zip": _zip(("w.pth", b"other"))}, {}, "weight_digest_differs_at_origin",
                 id="other_bytes_at_origin"),
    pytest.param({"https://example.org/w.zip": _zip(("v.pth", WEIGHT_BYTES))}, {}, "weight_not_in_origin",
                 id="member_missing"),
    pytest.param({"https://example.org/w.zip": _zip(("a/w.pth", WEIGHT_BYTES), ("b/w.pth", WEIGHT_BYTES))}, {},
                 "weight_not_in_origin", id="member_ambiguous"),
    pytest.param({}, {"license_text_sha256": "0" * 64}, "license_text_differs_at_source", id="license_text_changed"),
])
def test_what_differs_at_the_origin_is_named_and_not_recorded(served, change, found):
    evidence, problems = wp.measure(_models(**change), "2026-10-05", {**SERVED, **served}.__getitem__)
    assert problems == [f"{found}:ocr:w.pth"]
    assert evidence["models"] == {}


MODEL_LICENSE = "https://github.com/up/r/blob/v1/LICENSE"


@pytest.mark.parametrize("served, found", [
    pytest.param(LICENSE_BYTES, [], id="text_read_from_its_source"),
    pytest.param(b"Other License", ["license_text_differs_at_source:ocr"], id="text_changed_at_source"),
])
def test_the_models_license_text_is_read_from_its_source_and_recorded(served, found):
    """ملاحظة Codex على #307: بصمةُ نصّ رخصة النموذج تُقاس من مصدره المقيَّد وتُسجَّل، وما خالفها يُسمّى ولا يُسجَّل."""
    models = {"ocr": {**_models()["ocr"], "source": MODEL_LICENSE, "license_text_sha256": _sha(LICENSE_BYTES)}}
    evidence, problems = wp.measure(models, "2026-10-05", {**SERVED, MODEL_LICENSE: served}.__getitem__)
    assert problems == found
    assert evidence["models"]["ocr"].get("license_provenance") == (
        None if found else {"source": MODEL_LICENSE, "license_text_sha256": _sha(LICENSE_BYTES), "license": "apache-2.0"})
    if not found:
        registry = {"enforced_from": "2026-10-05", "historical_evidence": [], "models": models}
        assert ml.findings(registry, {"p.json": evidence}, None) == []


def test_a_github_blob_is_read_from_its_raw_copy():
    assert wp.raw_url("https://github.com/up/r/blob/c0ffee/dir/LICENSE") == (
        "https://raw.githubusercontent.com/up/r/c0ffee/dir/LICENSE")
    assert wp.raw_url("https://github.com/up/r/releases/download/v1/w.zip") == (
        "https://github.com/up/r/releases/download/v1/w.zip")


def test_the_cli_writes_nothing_when_the_origin_differs(tmp_path, monkeypatch, capsys):
    registry = tmp_path / "r.json"
    registry.write_text(json.dumps({"models": _models()}), encoding="utf-8")
    monkeypatch.setattr(ml, "REGISTRY", registry)
    monkeypatch.setattr(ml, "PROBE", tmp_path)
    monkeypatch.setattr(wp, "ROOT", tmp_path)
    monkeypatch.setattr(wp, "fetch", {**SERVED, "https://example.org/w.zip": _zip(("w.pth", b"x"))}.__getitem__)
    assert wp.main(["--write", "--day", "2026-10-05"]) == 1
    assert not (tmp_path / "weight-provenance-20261005.json").exists()
    assert json.loads(capsys.readouterr().out)["findings"] == ["weight_digest_differs_at_origin:ocr:w.pth"]
    monkeypatch.setattr(wp, "fetch", SERVED.__getitem__)
    assert wp.main(["--write", "--day", "2026-10-05"]) == 0
    assert json.loads((tmp_path / "weight-provenance-20261005.json").read_text(encoding="utf-8"))["date"] == "2026-10-05"
