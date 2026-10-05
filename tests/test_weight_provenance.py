"""دليلُ مصدر الأوزان يُنزّل الوزنَ من أصله المعلن ونصَّ رخصته من مصدره ويطابق البصمات (ملاحظة Codex على #307)."""
from __future__ import annotations

import bz2
import gzip
import hashlib
import io
import json
import lzma
import tarfile
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


def _tar(*members: tuple[str, bytes | None], mode: str = "w") -> bytes:
    """أرشيفُ TAR بأعضائه؛ والعضوُ بلا بايتات دليلٌ (مجلّد)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            else:
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _zip(*members: tuple[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
    return buffer.getvalue()


ARCHIVE = _zip(("w.pth", WEIGHT_BYTES), ("readme.txt", b"x"))
WEIGHT = {"file": "w.pth", "sha256": _sha(WEIGHT_BYTES), "origin": "https://example.org/w.zip", "origin_sha256": _sha(ARCHIVE),
          "license": "mit", "license_source": "https://github.com/up/r/blob/c0ffee/LICENSE", "read_on": "2026-10-05",
          "license_text_sha256": _sha(LICENSE_BYTES), "attribution": "Copyright (c) 2019 Example"}
SERVED = {"https://example.org/w.zip": ARCHIVE, "https://github.com/up/r/blob/c0ffee/LICENSE": LICENSE_BYTES}
OTHER_BYTES, MEMBER_MISSING = _zip(("w.pth", b"other")), _zip(("v.pth", WEIGHT_BYTES))
MEMBER_AMBIGUOUS = _zip(("a/w.pth", WEIGHT_BYTES), ("b/w.pth", WEIGHT_BYTES))
# الوزنُ نفسُه في أرشيفٍ آخر: عضوُه مطابق، والأرشيفُ المقيَّد غيرُه (ملاحظة Codex على #307)
REPACKED = _zip(("w.pth", WEIGHT_BYTES), ("readme.txt", b"changed"))
TAR_MISSING, TAR_AMBIGUOUS = _tar(("v.pth", WEIGHT_BYTES)), _tar(("a/w.pth", WEIGHT_BYTES), ("b/w.pth", WEIGHT_BYTES))
ZSTD_FRAME = b"\x28\xb5\x2f\xfd" + b"frame"
RAW_OTHER = b"other weights"
# رخصةُ MIT بإشعارَي حقوق نشر: كلاهما يُشترط نشرُه (ملاحظة Codex على #307)
TWO_NOTICES = ("Copyright (c) 2019 First\nCopyright (c) 2020 Second\n\n" + MIT_BODY).encode()


def _models(**change) -> dict:
    return {"ocr": {"license": "apache-2.0", "source": "https://example.org", "read_on": "2026-10-05",
                    "weights": [{k: v for k, v in {**WEIGHT, **change}.items() if v is not None}]}}


MODEL_LICENSE = "https://github.com/up/r/blob/v1/LICENSE"


def _measured(**change) -> dict:
    """نموذجٌ قُيّدت له أوزانٌ يُقيَّد نصُّ رخصته ويُقاس من مصدره (ملاحظة Codex على #307)."""
    return {"ocr": {**_models(**change)["ocr"], "source": MODEL_LICENSE, "license_text_sha256": _sha(APACHE)}}


def test_a_weight_measured_at_its_origin_is_recorded_and_the_evidence_passes_both_guards():
    models = _measured(license_text_sha256=_sha(LICENSE_BYTES))
    evidence, problems = wp.measure(models, "2026-10-05", {**SERVED, MODEL_LICENSE: APACHE}.__getitem__)
    assert problems == []
    record = evidence["models"]["ocr"]["weight_provenance"][0]
    assert record == {"file": "w.pth", "sha256": WEIGHT["sha256"], "origin": WEIGHT["origin"],
                      "origin_sha256": _sha(SERVED[WEIGHT["origin"]]), "license_source": WEIGHT["license_source"],
                      "license": WEIGHT["license"], "read_on": "2026-10-05", "license_text_sha256": _sha(LICENSE_BYTES),
                      "attribution": WEIGHT["attribution"]}
    assert evidence["models"]["ocr"]["models_sha256"] == {"w.pth": WEIGHT["sha256"]}
    registry = {"enforced_from": "2026-10-05", "historical_evidence": [], "models": models}
    assert ml.findings(registry, {"p.json": evidence}, None) == []
    assert probe_spend.spend_findings("p.json", evidence["spend"]) == []
    assert ml.provenance_findings(_models(origin="https://evil.invalid/w.zip"), {"p.json": evidence}) == [
        "weight_provenance_not_in_evidence:ocr:w.pth"]
    # بصمةُ الأرشيف المعلنة في السجلّ مربوطةٌ بما قيس، فلا يمرّ أصفارٌ مكانها (ملاحظة Codex على #307)
    assert ml.provenance_findings(_models(origin_sha256="0" * 64), {"p.json": evidence}) == [
        "weight_provenance_not_in_evidence:ocr:w.pth"]


def test_an_attribution_read_in_the_license_text_is_recorded_and_bound():
    """ملاحظة Codex على #307: الإسنادُ المنشور في THIRD-PARTY.md كان يُنسخ من السجلّ. فهو سطرٌ في نصّ الرخصة المقيس يُسجَّل في
    سجلّ المصدر، وتغييرُه في السجلّ بعد القياس يُسمّى."""
    models = _measured(attribution="Copyright (c) 2019 Example")
    evidence, problems = wp.measure(models, "2026-10-05", {**SERVED, MODEL_LICENSE: APACHE}.__getitem__)
    assert problems == [] and evidence["models"]["ocr"]["weight_provenance"][0]["attribution"] == "Copyright (c) 2019 Example"
    registry = {"enforced_from": "2026-10-05", "historical_evidence": [], "models": models}
    assert ml.findings(registry, {"p.json": evidence}, None) == []
    moved = {**registry, "models": _measured(attribution="Copyright (c) Other")}
    assert ml.findings(moved, {"p.json": evidence}, None) == ["weight_provenance_not_in_evidence:ocr:w.pth"]


def test_a_weight_served_raw_is_hashed_as_it_is():
    served = {"https://example.org/w.pth": WEIGHT_BYTES, **SERVED}
    evidence, problems = wp.measure(_models(origin="https://example.org/w.pth", origin_sha256=WEIGHT["sha256"]), "2026-10-05",
                                    served.__getitem__)
    assert problems == [] and evidence["models"]["ocr"]["weight_provenance"][0]["origin_sha256"] == WEIGHT["sha256"]


def test_a_zip_format_weight_served_raw_is_not_opened():
    """ملاحظة Codex على #307: وزنٌ صيغتُه نفسُها ZIP (حفظُ torch الحديث) كان يُفتح أرشيفًا فيُبحث فيه عن عضوٍ باسمه، فيُسمّى
    `weight_not_in_origin` وبايتاتُه مطابِقة. فما طابقت بصمتُه البصمةَ المقيَّدة يُقاس كما هو."""
    checkpoint = _zip(("w/data.pkl", b"tensors"), ("w/version", b"3"))
    served = {"https://example.org/w.pth": checkpoint, **SERVED}
    models = _models(origin="https://example.org/w.pth", origin_sha256=_sha(checkpoint), sha256=_sha(checkpoint))
    evidence, problems = wp.measure(models, "2026-10-05", served.__getitem__)
    assert problems == [] and evidence["models"]["ocr"]["models_sha256"] == {"w.pth": _sha(checkpoint)}


@pytest.mark.parametrize("wrapped", [
    pytest.param(_tar(("model/w.pth", WEIGHT_BYTES)), id="tar"),
    pytest.param(_tar(("model/w.pth", WEIGHT_BYTES), mode="w:gz"), id="tar_gz"),
    pytest.param(_tar(("w.pth", None), ("model/w.pth", WEIGHT_BYTES)), id="tar_with_a_directory_named_like_the_weight"),
    pytest.param(gzip.compress(WEIGHT_BYTES), id="gzip_stream"),
    pytest.param(bz2.compress(WEIGHT_BYTES), id="bzip2_stream"),
    pytest.param(lzma.compress(WEIGHT_BYTES), id="xz_stream"),
])
def test_a_weight_in_a_tar_or_a_compressed_stream_is_unwrapped(wrapped):
    """ملاحظة Codex على #307: الوزنُ في غلافٍ غيرِ ZIP كان يُقاس الغلافُ نفسُه فيُرفض وهو صحيح. فأرشيفُ TAR بأيّ ضغطٍ تقرؤه
    `tarfile` يُستخرج منه الملفُّ الوحيد الذي اسمُه اسمُ الوزن، وضغطُ الملفّ الواحد يُفكّ."""
    served = {"https://example.org/w.bin": wrapped, **SERVED}
    models = _models(origin="https://example.org/w.bin", origin_sha256=_sha(wrapped))
    evidence, problems = wp.measure(models, "2026-10-05", served.__getitem__)
    assert problems == [] and evidence["models"]["ocr"]["models_sha256"] == {"w.pth": WEIGHT["sha256"]}


def test_every_mit_notice_in_the_text_is_the_attribution():
    """ملاحظة Codex على #307: إسنادٌ بأحد إشعارَي الرخصة كان يُقبل، فيسقط الآخرُ من THIRD-PARTY.md. فالإسنادُ أسطرُ حقوق النشر
    كلُّها، سطرًا لكلّ إشعار."""
    served = {**SERVED, WEIGHT["license_source"]: TWO_NOTICES}
    every = _models(license_text_sha256=_sha(TWO_NOTICES), attribution="Copyright (c) 2019 First\nCopyright (c) 2020 Second")
    evidence, problems = wp.measure(every, "2026-10-05", served.__getitem__)
    assert problems == [] and evidence["models"]["ocr"]["weight_provenance"][0]["attribution"] == every["ocr"]["weights"][0]["attribution"]
    one = _models(license_text_sha256=_sha(TWO_NOTICES), attribution="Copyright (c) 2019 First")
    assert wp.measure(one, "2026-10-05", served.__getitem__)[1] == ["attribution_not_in_text:ocr:w.pth"]


@pytest.mark.parametrize("served, change, found", [
    pytest.param({"https://example.org/w.zip": OTHER_BYTES}, {"origin_sha256": _sha(OTHER_BYTES)},
                 "weight_digest_differs_at_origin", id="other_bytes_at_origin"),
    pytest.param({"https://example.org/w.zip": MEMBER_MISSING}, {"origin_sha256": _sha(MEMBER_MISSING)}, "weight_not_in_origin",
                 id="member_missing"),
    pytest.param({"https://example.org/w.zip": MEMBER_AMBIGUOUS}, {"origin_sha256": _sha(MEMBER_AMBIGUOUS)},
                 "weight_not_in_origin", id="member_ambiguous"),
    pytest.param({"https://example.org/w.zip": REPACKED}, {}, "origin_digest_differs_at_origin", id="archive_repacked"),
    pytest.param({"https://example.org/w.zip": TAR_MISSING}, {"origin_sha256": _sha(TAR_MISSING)}, "weight_not_in_origin",
                 id="tar_member_missing"),
    pytest.param({"https://example.org/w.zip": TAR_AMBIGUOUS}, {"origin_sha256": _sha(TAR_AMBIGUOUS)}, "weight_not_in_origin",
                 id="tar_member_ambiguous"),
    pytest.param({"https://example.org/w.zip": ZSTD_FRAME}, {"origin_sha256": _sha(ZSTD_FRAME)}, "origin_wrapper_unsupported",
                 id="zstandard_wrapper"),
    pytest.param({"https://example.org/w.zip": RAW_OTHER}, {"origin_sha256": _sha(RAW_OTHER)}, "weight_digest_differs_at_origin",
                 id="raw_other_bytes"),
    pytest.param({"https://example.org/w.zip": OTHER_BYTES}, {}, "origin_digest_differs_at_origin",
                 id="archive_of_other_bytes"),
    pytest.param({}, {"license_text_sha256": "0" * 64}, "license_text_differs_at_source", id="license_text_changed"),
    pytest.param({}, {"license": "proprietary"}, "license_not_the_text", id="license_relabelled"),
    pytest.param({}, {"read_on": "2026-10-04"}, "read_on_not_the_measurement_day", id="read_on_not_the_day"),
    pytest.param({}, {"attribution": "Copyright (c) Other"}, "attribution_not_in_text", id="attribution_not_in_text"),
    pytest.param({}, {"attribution": "MIT"}, "attribution_not_in_text", id="attribution_a_fragment"),
    pytest.param({}, {"attribution": None}, "attribution_missing", id="attribution_missing"),
    pytest.param({"https://github.com/up/r/blob/c0ffee/LICENSE": APACHE},
                 {"license": "apache-2.0", "license_text_sha256": _sha(APACHE),
                  "attribution": "Copyright [yyyy] [name of copyright owner]"}, "attribution_not_in_text",
                 id="attribution_from_the_apache_appendix"),
])
def test_what_differs_at_the_origin_is_named_and_not_recorded(served, change, found):
    evidence, problems = wp.measure(_models(**change), "2026-10-05", {**SERVED, **served}.__getitem__)
    assert problems == [f"{found}:ocr:w.pth"]
    assert evidence["models"] == {}


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
    assert json.loads(capsys.readouterr().out)["findings"] == ["origin_digest_differs_at_origin:ocr:w.pth"]
    monkeypatch.setattr(wp, "fetch", SERVED.__getitem__)
    assert wp.main(["--write", "--day", "2026-10-05"]) == 0
    assert json.loads((tmp_path / "weight-provenance-20261005.json").read_text(encoding="utf-8"))["date"] == "2026-10-05"
