"""دليلُ مصدر الأوزان يُنزّل الوزنَ من أصله المعلن ونصَّ رخصته من مصدره ويطابق البصمات (ملاحظة Codex على #307)."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from tools import model_licenses as ml
from tools import probe_spend
from tools import weight_provenance as wp

MIT_BODY = (
    'Permission is hereby granted, free of charge, to any person obtaining a copy\n'
    'of this software and associated documentation files (the "Software"), to deal\n'
    'in the Software without restriction, including without limitation the rights\n'
    'to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n'
    'copies of the Software, and to permit persons to whom the Software is\n'
    'furnished to do so, subject to the following conditions:\n'
    '\n'
    'The above copyright notice and this permission notice shall be included in\n'
    'all copies or substantial portions of the Software.\n'
    '\n'
    'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n'
    'IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n'
    'FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL THE\n'
    'AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n'
    'LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n'
    'OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN\n'
    'THE SOFTWARE.\n'
)
WEIGHT_BYTES = b"weights"
LICENSE_BYTES = ("Copyright (c) 2019 Example\n\n" + MIT_BODY).encode()
APACHE = (Path(__file__).resolve().parents[1] / "LICENSE").read_bytes()


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
                      "license": WEIGHT["license"], "read_on": "2026-10-05", "license_text_sha256": _sha(LICENSE_BYTES)}
    assert evidence["models"]["ocr"]["models_sha256"] == {"w.pth": WEIGHT["sha256"]}
    registry = {"enforced_from": "2026-10-05", "historical_evidence": [], "models": models}
    assert ml.findings(registry, {"p.json": evidence}, None) == []
    assert probe_spend.spend_findings("p.json", evidence["spend"]) == []
    assert ml.provenance_findings(_models(origin="https://evil.invalid/w.zip"), {"p.json": evidence}) == [
        "weight_provenance_not_in_evidence:ocr:w.pth"]


def test_an_attribution_read_in_the_license_text_is_recorded_and_bound():
    """ملاحظة Codex على #307: الإسنادُ المنشور في THIRD-PARTY.md كان يُنسخ من السجلّ. فهو سطرٌ في نصّ الرخصة المقيس يُسجَّل في
    سجلّ المصدر، وتغييرُه في السجلّ بعد القياس يُسمّى."""
    models = _models(attribution="Copyright (c) 2019 Example")
    evidence, problems = wp.measure(models, "2026-10-05", SERVED.__getitem__)
    assert problems == [] and evidence["models"]["ocr"]["weight_provenance"][0]["attribution"] == "Copyright (c) 2019 Example"
    registry = {"enforced_from": "2026-10-05", "historical_evidence": [], "models": models}
    assert ml.findings(registry, {"p.json": evidence}, None) == []
    moved = {**registry, "models": _models(attribution="Copyright (c) Other")}
    assert ml.findings(moved, {"p.json": evidence}, None) == ["weight_provenance_not_in_evidence:ocr:w.pth"]


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
    pytest.param({}, {"license": "proprietary"}, "license_not_the_text", id="license_relabelled"),
    pytest.param({}, {"read_on": "2026-10-04"}, "read_on_not_the_measurement_day", id="read_on_not_the_day"),
    pytest.param({}, {"attribution": "Copyright (c) Other"}, "attribution_not_in_text", id="attribution_not_in_text"),
    pytest.param({}, {"attribution": "MIT"}, "attribution_not_in_text", id="attribution_a_fragment"),
])
def test_what_differs_at_the_origin_is_named_and_not_recorded(served, change, found):
    evidence, problems = wp.measure(_models(**change), "2026-10-05", {**SERVED, **served}.__getitem__)
    assert problems == [f"{found}:ocr:w.pth"]
    assert evidence["models"] == {}


MODEL_LICENSE = "https://github.com/up/r/blob/v1/LICENSE"


@pytest.mark.parametrize("served, license, read_on, found", [
    pytest.param(APACHE, "apache-2.0", "2026-10-05", [], id="text_read_from_its_source"),
    pytest.param(b"Other License", "apache-2.0", "2026-10-05", ["license_text_differs_at_source:ocr"], id="text_changed_at_source"),
    pytest.param(APACHE, "mit", "2026-10-05", ["license_not_the_text:ocr"], id="text_of_another_license"),
    pytest.param(APACHE, "apache-2.0", "2026-10-04", ["read_on_not_the_measurement_day:ocr"], id="read_on_not_the_day"),
])
def test_the_models_license_text_is_read_from_its_source_and_recorded(served, license, read_on, found):
    """ملاحظتا Codex على #307: بصمةُ نصّ رخصة النموذج تُقاس من مصدره المقيَّد وتُسجَّل، والرخصةُ المعلنة تُسمّى من النصّ لا تُنسخ
    من السجلّ؛ وما خالف أحدَهما يُسمّى ولا يُسجَّل."""
    models = {"ocr": {**_models()["ocr"], "license": license, "read_on": read_on, "source": MODEL_LICENSE,
                      "license_text_sha256": _sha(APACHE)}}
    evidence, problems = wp.measure(models, "2026-10-05", {**SERVED, MODEL_LICENSE: served}.__getitem__)
    assert problems == found
    assert evidence["models"]["ocr"].get("license_provenance") == (
        None if found else {"source": MODEL_LICENSE, "license_text_sha256": _sha(APACHE), "license": "apache-2.0",
                            "read_on": "2026-10-05"})
    if not found:
        registry = {"enforced_from": "2026-10-05", "historical_evidence": [], "models": models}
        assert ml.findings(registry, {"p.json": evidence}, None) == []


END = b"END OF TERMS AND CONDITIONS"


@pytest.mark.parametrize("data, found", [
    pytest.param(LICENSE_BYTES, "mit", id="mit_with_its_copyright"),
    pytest.param(b"MIT License\n\n" + LICENSE_BYTES, "mit", id="mit_with_its_title"),
    pytest.param(APACHE, "apache-2.0", id="apache_with_its_appendix"),
    pytest.param(APACHE[:APACHE.index(END) + len(END)] + b"\n", "apache-2.0", id="apache_without_its_appendix"),
    pytest.param(LICENSE_BYTES + b"\nThe Software may not be used commercially.\n", None, id="mit_with_a_clause_after"),
    pytest.param(b"Non-commercial use only.\n" + LICENSE_BYTES, None, id="mit_with_a_clause_before"),
    pytest.param(LICENSE_BYTES.replace(b"sell", b"rent"), None, id="mit_body_changed"),
    pytest.param(APACHE + b"\nAdditional restriction.\n", None, id="apache_with_a_clause_after"),
    pytest.param(b"Note.\n" + APACHE, None, id="apache_with_a_clause_before"),
    pytest.param(b"\xff\xfe", None, id="not_text"),
    pytest.param(b"MIT License", None, id="a_title_alone"),
])
def test_a_license_is_named_by_its_text(data, found):
    """ملاحظة Codex على #307: الرخصةُ المعلنة كانت تُنسخ من السجلّ إلى الدليل، فإعادةُ التوليد بعد تغيير الوسم تبارك الانحراف.
    فتُسمّى من النصّ المقيس: جسمُ SPDX ببصمته، ولا بندَ زائدًا قبله أو بعده."""
    assert wp.identify_license(data) == found


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
