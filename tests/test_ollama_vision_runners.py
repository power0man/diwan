"""غ٨ وغ١١: عميلُ Ollama للرؤية ومُشغِّلا OCR والفهم عليه — كلُّه بنقلٍ مزيّف بلا شبكة.

- النموذجُ بلا رؤية، أو غيرُ المثبَّت، أو على عنوانٍ غير محلّي: رفضٌ مسمًّى قبل أيّ سؤال.
- الإعداداتُ ثابتة (حرارة ٠ وبذرة ٠)، ونموذجُ التفكير يُطلب بلا تفكير ويُنزع ما سبق الجواب من تفكير.
- موجّهُ OCR مسجَّلٌ ببصمته: تغييرُه بعد النتائج يُسقط هذا الاختبار.
- مُشغِّلُ الفهم: الجوابُ الحقّ يبلغ العتبة والثابتُ لا يبلغها، والعطلُ مسمًّى ومعدودٌ خطأً، والبنكُ المعدَّل
  مرفوضٌ قبل السؤال، والرقمُ يُعاد حسابُه من الردود ولا يطابقه رقمٌ معدَّلٌ باليد.
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from evaluation import ocr_runner, vision_runner
from evaluation.media_bank import BANK
from evaluation.ollama_vision import OllamaVision, VisionRefused

OCR = json.loads((BANK / "ocr.json").read_text(encoding="utf-8"))
TEXTS = {t["text_id"]: t["text"] for t in json.loads((BANK / "ocr_texts.json").read_text(encoding="utf-8"))["texts"]}
VISION = json.loads((BANK / "vision.json").read_text(encoding="utf-8"))
BY_BYTES = {(BANK / i["image"]).read_bytes(): TEXTS[i["text_id"]] for i in OCR["items"]}


def _truth(item) -> str:
    return f"أرى الصورة.\nالجواب: {item['check']['expected']}"


VISION_TRUTH = {(BANK / i["image"]).read_bytes(): _truth(i) for i in VISION["items"]}


class Fake:
    def __init__(self, answer, capabilities=("completion", "vision"), installed=True):
        self.answer, self.capabilities, self.installed, self.chats = answer, list(capabilities), installed, []

    def __call__(self, method, path, payload):
        if path == "/api/show":
            return {"capabilities": self.capabilities}
        if path == "/api/tags":
            return {"models": [{"name": "fake-vl:7b", "digest": "abc123def4567890"}] if self.installed else []}
        if path == "/api/version":
            return {"version": "0.40.0"}
        assert path == "/api/chat" and method == "POST"
        self.chats.append(payload)
        image = base64.b64decode(payload["messages"][0]["images"][0])
        return {"done": True, "message": {"role": "assistant", "content": self.answer(image, payload)}}


@pytest.mark.parametrize("fake, code", [
    (Fake(None, capabilities=("completion",)), "model_not_vision"),
    (Fake(None, installed=False), "model_missing"),
])
def test_a_model_that_cannot_see_or_is_absent_is_refused_by_name(fake, code):
    with pytest.raises(VisionRefused) as err:
        OllamaVision("fake-vl:7b", transport=fake)
    assert err.value.code == code and fake.chats == []


def test_a_remote_address_is_refused_before_any_call():
    with pytest.raises(VisionRefused) as err:
        OllamaVision("fake-vl:7b", base_url="https://ollama.com", transport=lambda *a: pytest.fail("نودي"))
    assert err.value.code == "remote_not_allowed"


def test_settings_are_fixed_and_a_thinking_model_is_asked_not_to_think():
    fake = Fake(lambda image, payload: "<think>تأمّل</think>\nالنص", capabilities=("completion", "vision", "thinking"))
    client = OllamaVision("fake-vl:7b", transport=fake)
    assert client.ask(BANK / OCR["items"][0]["image"], "اقرأ") == "النص"
    (payload,) = fake.chats
    assert payload["think"] is False and payload["options"]["temperature"] == 0 and payload["options"]["seed"] == 0
    assert client.settings["digest"] == "abc123def4567890"


def test_the_ocr_prompt_is_the_registered_one():
    assert hashlib.sha256(ocr_runner.OCR_PROMPT.encode("utf-8")).hexdigest() == \
        "32fa367519473a3b4d42e0905317504d566bc0cfaabc4cd978d522267065293a"


def test_an_ollama_ocr_engine_that_reads_the_truth_meets_every_threshold():
    fake = Fake(lambda image, payload: BY_BYTES[image])
    engine = ocr_runner.ollama("fake-vl:7b", transport=fake)
    result = ocr_runner.run(engine)
    assert result["report"]["meets"] and set(result["report"]["cer"].values()) == {0.0}
    assert engine.name == "ollama:fake-vl:7b" and engine.settings["prompt"] == ocr_runner.OCR_PROMPT
    assert all(chat["messages"][0]["content"] == ocr_runner.OCR_PROMPT for chat in fake.chats)


def test_an_unusable_ollama_model_is_an_unavailable_ocr_engine():
    with pytest.raises(ocr_runner.OCRRefused) as err:
        ocr_runner.ollama("fake-vl:7b", transport=Fake(None, installed=False))
    assert err.value.code == "ocr_engine_unavailable"


def _vision(ask, root=BANK):
    return vision_runner.run(ask, {"name": "fake", "version": "0", "settings": {}}, root)


def test_the_true_answers_meet_the_vision_threshold_and_a_constant_one_does_not():
    good = _vision(lambda image, question: VISION_TRUTH[image.read_bytes()])
    assert good["report"]["accuracy"] == 1.0 and good["report"]["meets"]
    constant = _vision(lambda image, question: "الجواب: ٥")
    assert not constant["report"]["meets"]


def test_each_question_is_asked_as_written_in_the_bank():
    asked = []
    _vision(lambda image, question: asked.append(question) or "")
    assert asked == [item["question"] for item in VISION["items"]]


def test_a_failure_on_one_image_is_named_and_counted_wrong():
    first = VISION["items"][0]

    def ask(image, question):
        if image.name == Path(first["image"]).name:
            raise TimeoutError("بطيء")
        return VISION_TRUTH[image.read_bytes()]

    result = _vision(ask)
    assert list(result["errors"]) == [first["id"]] and "TimeoutError" in result["errors"][first["id"]]
    assert result["report"]["accuracy"] == round(39 / 40, 4)


def test_a_changed_bank_is_refused_before_any_question(tmp_path):
    root = tmp_path / "media_v1"
    shutil.copytree(BANK, root)
    image = root / VISION["items"][5]["image"]
    image.write_bytes(image.read_bytes() + b"\0")
    with pytest.raises(vision_runner.VisionBankRefused) as err:
        _vision(lambda *a: pytest.fail("سُئل النموذج على بنكٍ معدَّل"), root)
    assert err.value.code == "bank_invalid"


def test_the_vision_number_is_recomputed_from_its_responses_and_a_hand_edit_is_caught():
    result = _vision(lambda image, question: VISION_TRUTH[image.read_bytes()] if image.name < "v2" else "لا أدري")
    assert vision_runner.rescore(result) == result["report"]
    result["report"]["accuracy"] = 1.0
    assert vision_runner.rescore(result) != result["report"]


PUBLISHED_VISION = sorted((Path(__file__).resolve().parents[1] / "docs" / "probe").glob("g11-vision-*.json"))


@pytest.mark.parametrize("path", PUBLISHED_VISION, ids=lambda p: p.name)
def test_every_published_vision_number_matches_its_evidence(path):
    result = json.loads(path.read_text(encoding="utf-8"))
    assert vision_runner.rescore(result) == result["report"] and result["measurement_limits"]
