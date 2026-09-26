"""غ٨ (#48): مُشغِّلُ قياس OCR على بنك الوسائط المجمَّد — يقيس ما يُقال إنه يقيسه.

- المحرّكُ الذي يعيد النصَّ الحقَّ ينال صفرًا ويبلغ العتبة، والذي لا يقرأ شيئًا ينال واحدًا ولا يبلغها.
- ما يرميه محرّكٌ على صفحةٍ يُسمّى ويُعدّ خطأً كاملًا، ولا يُسقط من المقام.
- البنكُ المعدَّل بعد التجميد يُرفض قبل أن يُنادى المحرّك.
- الصفحاتُ المشكولة تحمل CER بالحركات أيضًا.
- التقريرُ المنشور في docs/probe يُعاد حسابُه من نصوصه المسجَّلة فيطابق رقمَه.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from evaluation.media_bank import BANK
from evaluation.ocr_runner import Engine, OCRRefused, easyocr, rescore, run, tesseract

ROOT = Path(__file__).resolve().parents[1]
OCR = json.loads((BANK / "ocr.json").read_text(encoding="utf-8"))
TEXTS = {t["text_id"]: t["text"] for t in json.loads((BANK / "ocr_texts.json").read_text(encoding="utf-8"))["texts"]}
BY_IMAGE = {(BANK / item["image"]).resolve(): TEXTS[item["text_id"]] for item in OCR["items"]}
PUBLISHED = sorted((ROOT / "docs" / "probe").glob("g8-ocr-*.json"))


def _engine(read) -> Engine:
    return Engine("fake", "0", read, {"note": "اختبار"})


def test_an_engine_that_reads_the_truth_scores_zero_and_meets_every_threshold():
    result = run(_engine(lambda image: BY_IMAGE[image.resolve()]))
    assert result["report"] == {"cer": {"clean": 0.0, "moderate": 0.0, "heavy": 0.0}, "meets": True}
    assert len(result["items"]) == 30 and result["errors"] == {}
    assert result["engine"] == {"name": "fake", "version": "0", "settings": {"note": "اختبار"}}


def test_an_engine_that_reads_nothing_scores_one_and_meets_nothing():
    result = run(_engine(lambda image: ""))
    assert result["report"] == {"cer": {"clean": 1.0, "moderate": 1.0, "heavy": 1.0}, "meets": False}


def test_a_page_the_engine_fails_on_is_named_and_counted_as_a_full_error():
    failing = (BANK / OCR["items"][0]["image"]).resolve()

    def read(image):
        if image.resolve() == failing:
            raise RuntimeError("انهار")
        return BY_IMAGE[image.resolve()]

    result = run(_engine(read))
    first = OCR["items"][0]
    assert list(result["errors"]) == [first["id"]] and "RuntimeError" in result["errors"][first["id"]]
    row = next(r for r in result["items"] if r["id"] == first["id"])
    assert row["cer"] == 1.0 and row["hypothesis"] == ""
    same_level = [i for i in OCR["items"] if i["level"] == first["level"]]
    assert result["report"]["cer"][first["level"]] == round(1 / len(same_level), 4)


def test_a_bank_changed_after_freezing_is_refused_before_the_engine_runs(tmp_path):
    root = tmp_path / "media_v1"
    shutil.copytree(BANK, root)
    page = root / OCR["items"][3]["image"]
    page.write_bytes(page.read_bytes() + b"\0")
    with pytest.raises(OCRRefused) as err:
        run(_engine(lambda image: pytest.fail("نودي المحرّكُ على بنكٍ معدَّل")), root)
    assert err.value.code == "bank_invalid"


def test_vocalized_pages_also_carry_the_error_rate_with_diacritics():
    result = run(_engine(lambda image: BY_IMAGE[image.resolve()]))
    vocalized = [r for r in result["items"] if r["tashkeel"]]
    assert vocalized and all(r["cer_with_diacritics"] == 0.0 for r in vocalized)
    assert all("cer_with_diacritics" not in r for r in result["items"] if not r["tashkeel"])


def test_a_missing_tesseract_is_refused_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(OCRRefused) as err:
        tesseract()
    assert err.value.code == "ocr_engine_unavailable"


def test_a_missing_easyocr_is_refused_by_name(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "easyocr", None)
    with pytest.raises(OCRRefused) as err:
        easyocr()
    assert err.value.code == "ocr_engine_unavailable"


@pytest.mark.parametrize("path", PUBLISHED, ids=lambda p: p.name)
def test_every_published_ocr_number_is_recomputed_from_its_own_evidence(path):
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["bank_manifest_sha256"] == __import__("hashlib").sha256((BANK / "manifest.json").read_bytes()).hexdigest()
    assert rescore(result) == result["report"]
    assert result["thresholds"] == OCR["thresholds"] and result["measurement_limits"]
    assert [r["id"] for r in result["items"]] == [i["id"] for i in OCR["items"]]


def test_a_published_number_edited_by_hand_no_longer_matches_its_evidence():
    result = json.loads(PUBLISHED[0].read_text(encoding="utf-8"))
    result["report"]["cer"]["clean"] = round(result["report"]["cer"]["clean"] / 2, 4)
    assert rescore(result) != result["report"]


def test_at_least_one_ocr_measurement_is_published():
    assert PUBLISHED
