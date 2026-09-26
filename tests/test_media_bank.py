"""غ٧: بنكُ الوسائط سليمٌ ومجمَّد، ومعاييرُ حكمه لا تُخدع.

الحرّاس: البنكُ يمرّ مدقّقه؛ وصورةٌ عُدّلت أو ملفٌّ خارج البيان يُسمّى؛ والجوابُ الصحيح بسطره ينجح،
وسردُ الخيارات أو الجوابُ الثابت أو التردّد بين عددين لا ينجح؛ ونسبةُ خطأ المحارف كما أُعلنت.
"""
from __future__ import annotations

import json
import shutil

import pytest

from evaluation.media_bank import (BANK, answer_line, cer, ocr_report, score_vision, validate_media_bank,
                                   vision_report)


def _load(name):
    return json.loads((BANK / name).read_text(encoding="utf-8"))


VISION = _load("vision.json")
OCR = _load("ocr.json")
TEXTS = {t["text_id"]: t["text"] for t in _load("ocr_texts.json")["texts"]}


def _answer(value):
    return f"أرى في الصورة ما يلي.\nالجواب: {value}"


def test_the_frozen_bank_validates():
    assert validate_media_bank() == []


@pytest.fixture
def copy(tmp_path):
    target = tmp_path / "media_v1"
    shutil.copytree(BANK, target)
    return target


def test_a_changed_image_is_named(copy):
    path = copy / VISION["items"][0]["image"]
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 0xFF
    path.write_bytes(bytes(raw))
    assert f"media_digest: {VISION['items'][0]['image']}" in validate_media_bank(copy)


def test_a_media_file_outside_the_manifest_is_named(copy):
    (copy / "vision" / "extra.png").write_bytes(b"x")
    assert "media_unlisted: vision/extra.png" in validate_media_bank(copy)


def test_an_expected_answer_outside_its_choices_is_named(copy):
    data = json.loads((copy / "vision.json").read_text(encoding="utf-8"))
    item = next(i for i in data["items"] if i["check"]["type"] == "choice")
    item["check"]["expected"] = "بنفسجي"
    (copy / "vision.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert f"vision_expected_not_a_choice: {item['id']}" in validate_media_bank(copy)


def test_perfect_answers_meet_the_threshold_and_constant_answers_do_not():
    perfect = {item["id"]: _answer(item["check"]["expected"]) for item in VISION["items"]}
    assert vision_report(VISION, perfect) == {**vision_report(VISION, perfect), "accuracy": 1.0, "meets": True}
    for constant in ("فوق", "أحمر", "٥", "المربع", "مارس"):
        report = vision_report(VISION, {item["id"]: _answer(constant) for item in VISION["items"]})
        assert report["meets"] is False, constant


def _item(kind, expected, choices=None):
    return {"check": {"type": kind, "expected": expected, **({"choices": choices} if choices else {})}}


@pytest.mark.parametrize("response, ok", [
    ("الجواب: 8", True), ("الجواب: ٨", True), ("الجواب: ثمانية", True), ("في الصورة 8 مربعات.\nالجواب: 8 مربعات", True),
    ("الجواب: 7 أو 8", False), ("أظنها ثمانية", False), ("الجواب: 9", False), ("", False),
])
def test_a_number_answer_is_read_from_its_line_only(response, ok):
    assert score_vision(_item("number", 8), response) is ok


@pytest.mark.parametrize("response, ok", [
    ("الجواب: أحمر", True), ("الجواب: أحمر.", True), ("الجواب: أحمر، أزرق، أخضر", False),
    ("الجواب: ليس أحمر", False), ("اللون أحمر", False),
])
def test_a_choice_must_be_the_one_expected_choice(response, ok):
    assert score_vision(_item("choice", "أحمر", ["أحمر", "أزرق", "أخضر"]), response) is ok


def test_a_sign_is_read_exactly_after_normalization():
    item = _item("text", "الدفع نقدًا فقط")
    assert score_vision(item, "الجواب: الدفع نقداً فقط") is True
    assert score_vision(item, "الجواب: «الدفع نقدا فقط»") is True
    assert score_vision(item, "الجواب: الدفع نقدا") is False


def test_the_last_answer_line_wins():
    assert answer_line("الجواب: 3\nتصحيح:\nالجواب: 4") == "4"


def test_cer_ignores_diacritics_unless_asked():
    assert cer("كَتَبَ الدرسَ", "كتب الدرس") == 0.0
    assert cer("كَتَبَ", "كتب", keep_diacritics=True) > 0
    assert cer("سلام", "سلم") == 0.25
    assert cer("أحمد", "احمد") == 0.25              # الهمزةُ لا تُوحَّد: خطؤها خطأ


def test_the_ocr_ground_truth_scores_zero_and_an_empty_reading_fails():
    exact = {item["id"]: TEXTS[item["text_id"]] for item in OCR["items"]}
    assert ocr_report(OCR, TEXTS, exact) == {"cer": {"clean": 0.0, "moderate": 0.0, "heavy": 0.0}, "meets": True}
    assert ocr_report(OCR, TEXTS, {})["meets"] is False


def test_every_ocr_page_covers_a_text_at_three_levels_and_tashkeel_is_declared():
    levels = {}
    for item in OCR["items"]:
        levels.setdefault(item["text_id"], []).append(item["level"])
        assert item["tashkeel"] is any(t["text_id"] == item["text_id"] and t["tashkeel"]
                                       for t in _load("ocr_texts.json")["texts"])
    assert all(sorted(v) == ["clean", "heavy", "moderate"] for v in levels.values()) and len(levels) == 10


def _corpus(tmp_path, count=60):
    corpus = tmp_path / "cv-ar"
    (corpus / "clips").mkdir(parents=True)
    rows = ["client_id\tpath\tsentence\tup_votes\tdown_votes\tage\tgender"]
    for i in range(count):
        name = f"common_voice_ar_{i:05d}.mp3"
        (corpus / "clips" / name).write_bytes(f"audio-{i}".encode())
        sentence = "هذه جملة عربية قصيرة للاختبار" if i % 7 else "قصيرة"
        up, down = (1, 0) if i % 11 == 0 else (3, 0) if i % 13 else (3, 1)
        rows.append(f"speaker-{i}\t{name}\t{sentence} {i}\t{up}\t{down}\tthirties\tfemale")
    (corpus / "test.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return corpus


def test_freezing_the_speech_sample_applies_the_rule_and_keeps_no_speaker_data(copy, tmp_path):
    from tools.freeze_speech_sample import FreezeRefused, freeze, select
    corpus = _corpus(tmp_path, count=200)
    out = freeze(corpus, "cv-corpus-test", bank=copy)
    assert out == {"status": "frozen", "clips": 50, "bytes": out["bytes"]}
    sample = json.loads((copy / "speech_sample.json").read_text(encoding="utf-8"))
    rows = [dict(zip(("client_id", "path", "sentence", "up_votes", "down_votes"), line.split("\t")[:5]))
            for line in (corpus / "test.tsv").read_text(encoding="utf-8").splitlines()[1:]]
    assert [c["source_path"] for c in sample["clips"]] == [r["path"] for r in select(rows)]
    by_path = {r["path"]: r for r in rows}
    for clip in sample["clips"]:
        row = by_path[clip["source_path"]]
        assert int(row["up_votes"]) >= 2 and int(row["down_votes"]) == 0 and len(row["sentence"].split()) >= 4
    import hashlib
    keys = [hashlib.sha256(c["source_path"].encode()).hexdigest() for c in sample["clips"]]
    assert keys == sorted(keys)
    dump = json.dumps(sample, ensure_ascii=False)
    assert "speaker-" not in dump and "female" not in dump and "thirties" not in dump
    assert validate_media_bank(copy) == []
    with pytest.raises(FreezeRefused) as err:
        freeze(corpus, "cv-corpus-test", bank=copy)
    assert err.value.code == "sample_already_frozen"
    (copy / "speech" / "a01.mp3").write_bytes(b"changed")
    assert "media_digest: speech/a01.mp3" in validate_media_bank(copy)


def test_a_sample_smaller_than_the_rule_asks_is_refused(copy, tmp_path):
    from tools.freeze_speech_sample import FreezeRefused, freeze
    with pytest.raises(FreezeRefused) as err:
        freeze(_corpus(tmp_path, count=20), "cv-corpus-test", bank=copy)
    assert err.value.code == "sample_too_small" and not (copy / "speech").exists()
