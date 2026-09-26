"""بنكُ الوسائط (غ٧، `docs/MEDIA-BANK.md`): المدقّقُ ومعاييرُ الحكم، بالمكتبة القياسية وحدها.

- `validate_media_bank` يفحص الأجزاء الأربعة: الأعداد، والبصمات مقابل البيان، والرخص، والعتبات المسجَّلة.
  وأيُّ ملفّ وسائط غير مذكورٍ في البيان خللٌ مسمًّى.
- `score_vision` يحكم على سطر «الجواب:» وحده، فلا ينجح جوابٌ يسرد الخيارات أو ينفيها.
- `cer` نسبةُ خطأ المحارف بعد التطبيع المعلن، لـ OCR والتفريغ.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

BANK = Path(__file__).resolve().parent / "media_v1"
MEDIA_DIRS = ("vision", "ocr", "speech")
COUNTS = {"image_gen": 50, "vision": 40, "ocr": 30, "ocr_texts": 10, "tts_sentences": 20, "minimal_pairs": 10,
          "asr_clips": 50}
_DIACRITICS = re.compile(r"[ً-ٰٟۖ-ۭ]")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_ANSWER = re.compile(r"^\s*الجواب\s*[:：]\s*(.+?)\s*$")
_NUMBER_WORDS = {"صفر": 0, "واحد": 1, "واحدة": 1, "اثنان": 2, "اثنين": 2, "اثنتان": 2, "ثلاثة": 3, "ثلاث": 3,
                 "أربعة": 4, "أربع": 4, "خمسة": 5, "خمس": 5, "ستة": 6, "ست": 6, "سبعة": 7, "سبع": 7,
                 "ثمانية": 8, "ثماني": 8, "ثمان": 8, "تسعة": 9, "تسع": 9, "عشرة": 10, "عشر": 10}


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(text: str, *, keep_diacritics: bool = False) -> str:
    """NFKC، وحذفُ التطويل (والحركات إلا إن طُلبت)، وتوحيدُ المسافات. ولا توحيدَ للهمزات ولا للتاء المربوطة."""
    text = unicodedata.normalize("NFKC", text).replace("ـ", "")
    if not keep_diacritics:
        text = _DIACRITICS.sub("", text)
    return " ".join(text.split())


def _plain(text: str) -> str:
    return " ".join(_PUNCT.sub(" ", normalize(text)).split())


def answer_line(response: str) -> str | None:
    """آخرُ سطرٍ بصيغة «الجواب: …»، أو لا شيء."""
    lines = [m.group(1) for m in map(_ANSWER.match, response.splitlines()) if m]
    return lines[-1] if lines else None


def _number(text: str) -> int | None:
    digits = unicodedata.normalize("NFKC", text).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    found = re.findall(r"\d+", digits)
    if len(found) == 1:
        return int(found[0])
    words = [_NUMBER_WORDS[w] for w in _plain(text).split() if w in _NUMBER_WORDS]
    return words[0] if len(words) == 1 and not found else None


def score_vision(item: dict, response: str) -> bool:
    """الحكمُ على سطر الجواب وحده. عددٌ واحدٌ يطابق، أو خيارٌ واحدٌ هو المتوقَّع، أو عبارةٌ تطابق بعد التطبيع."""
    answer = answer_line(response)
    if answer is None:
        return False
    check = item["check"]
    if check["type"] == "number":
        return _number(answer) == check["expected"]
    if check["type"] == "choice":
        return _plain(answer) == _plain(check["expected"])
    return _plain(answer) == _plain(check["expected"])


def cer(reference: str, hypothesis: str, *, keep_diacritics: bool = False) -> float:
    """نسبةُ خطأ المحارف: مسافةُ التحرير بعد التطبيع مقسومةً على طول المرجع."""
    ref, hyp = normalize(reference, keep_diacritics=keep_diacritics), normalize(hypothesis, keep_diacritics=keep_diacritics)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, a in enumerate(ref, 1):
        current = [i]
        for j, b in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1] / len(ref)


def vision_report(bank: dict, responses: dict[str, str]) -> dict:
    """الدقّةُ الكلية ولكل فئة، والحكمُ بالعتبة المسجَّلة. والغائبُ من الأجوبة خطأ."""
    by_category: dict[str, list[bool]] = {}
    for item in bank["items"]:
        by_category.setdefault(item["category"], []).append(score_vision(item, responses.get(item["id"], "")))
    total = [ok for oks in by_category.values() for ok in oks]
    accuracy = sum(total) / len(total)
    categories = {name: sum(oks) / len(oks) for name, oks in by_category.items()}
    limits = bank["thresholds"]
    return {"accuracy": round(accuracy, 4), "categories": {k: round(v, 4) for k, v in categories.items()},
            "meets": accuracy >= limits["accuracy"] and min(categories.values()) >= limits["min_category_accuracy"]}


def ocr_report(bank: dict, texts: dict[str, str], hypotheses: dict[str, str]) -> dict:
    levels: dict[str, list[float]] = {}
    for item in bank["items"]:
        levels.setdefault(item["level"], []).append(cer(texts[item["text_id"]], hypotheses.get(item["id"], "")))
    means = {level: round(sum(v) / len(v), 4) for level, v in levels.items()}
    limits = bank["thresholds"]["cer_max"]
    return {"cer": means, "meets": all(means[level] <= limits[level] for level in limits)}


def validate_media_bank(root: Path = BANK) -> list[str]:
    """خللُ البنك مسمًّى، أو قائمةٌ فارغة."""
    problems: list[str] = []
    try:
        image_gen, vision = _read(root / "image_gen.json"), _read(root / "vision.json")
        ocr, texts = _read(root / "ocr.json"), _read(root / "ocr_texts.json")
        speech, manifest = _read(root / "speech.json"), _read(root / "manifest.json")
    except (OSError, json.JSONDecodeError) as exc:
        return [f"bank_unreadable: {exc}"]
    for name, part in (("image_gen", image_gen), ("vision", vision), ("ocr", ocr), ("ocr_texts", texts),
                       ("manifest", manifest)):
        if not part.get("license"):
            problems.append(f"license_missing: {name}")
    # — المولّد —
    prompts = image_gen.get("prompts", [])
    if len(prompts) != COUNTS["image_gen"] or len({p["id"] for p in prompts}) != len(prompts):
        problems.append("image_gen_count")
    if any(len(p.get("checks", [])) < 2 or not p.get("prompt") for p in prompts):
        problems.append("image_gen_checks")
    limits = image_gen.get("thresholds", {})
    if not {"mean_prompt_score", "min_category_score", "gating_excludes"} <= set(limits):
        problems.append("image_gen_thresholds")
    # — الفهم —
    listed = {entry["path"]: entry for entry in manifest.get("files", [])}
    items = vision.get("items", [])
    if len(items) != COUNTS["vision"]:
        problems.append("vision_count")
    if not {"accuracy", "min_category_accuracy"} <= set(vision.get("thresholds", {})):
        problems.append("vision_thresholds")
    for item in items:
        check = item.get("check", {})
        if check.get("type") not in ("number", "choice", "text"):
            problems.append(f"vision_check_type: {item.get('id')}")
        if check.get("type") == "choice" and check.get("expected") not in check.get("choices", []):
            problems.append(f"vision_expected_not_a_choice: {item.get('id')}")
        if "الجواب:" not in item.get("question", ""):
            problems.append(f"vision_answer_format: {item.get('id')}")
    # — OCR —
    known = {t["text_id"]: t for t in texts.get("texts", [])}
    if len(known) != COUNTS["ocr_texts"]:
        problems.append("ocr_texts_count")
    pages = ocr.get("items", [])
    if len(pages) != COUNTS["ocr"]:
        problems.append("ocr_count")
    for text_id in known:
        if sorted(p["level"] for p in pages if p["text_id"] == text_id) != ["clean", "heavy", "moderate"]:
            problems.append(f"ocr_levels: {text_id}")
    if set(ocr.get("thresholds", {}).get("cer_max", {})) != {"clean", "moderate", "heavy"}:
        problems.append("ocr_thresholds")
    # — عيّنةُ التفريغ: تُجمَّد على الماك (tools/freeze_speech_sample.py)، وقبلها لا ملفَّ صوت —
    asr = speech.get("asr", {})
    clips = []
    if asr.get("status") == "frozen":
        try:
            sample = _read(root / "speech_sample.json")
        except (OSError, json.JSONDecodeError):
            sample = {}
            problems.append("speech_sample_missing")
        clips = [{"image": c["audio"], "sha256": c["sha256"]} for c in sample.get("clips", [])]
        if len(clips) != COUNTS["asr_clips"] or not sample.get("license") or not sample.get("release"):
            problems.append("speech_sample_invalid")
    # — البصمات: كلُّ ملفٍّ في بنده وفي البيان وعلى القرص، ولا ملفَّ خارج البيان —
    for item in items + pages + clips:
        path = root / item["image"]
        entry = listed.get(item["image"])
        if not path.is_file():
            problems.append(f"media_missing: {item['image']}")
            continue
        actual = _sha(path)
        if entry is None or entry["sha256"] != actual or item["sha256"] != actual or entry["bytes"] != path.stat().st_size:
            problems.append(f"media_digest: {item['image']}")
    on_disk = {p.relative_to(root).as_posix() for d in MEDIA_DIRS if (root / d).is_dir()
               for p in (root / d).rglob("*") if p.is_file()}
    for path in sorted(on_disk - set(listed)):
        problems.append(f"media_unlisted: {path}")
    for path in sorted(set(listed) - {i["image"] for i in items + pages + clips}):
        problems.append(f"manifest_orphan: {path}")
    # — الصوت —
    tts = speech.get("tts", {})
    if asr.get("status") not in ("pending_fetch", "frozen") or not {"wer_max", "cer_max"} <= set(asr.get("thresholds", {})):
        problems.append("speech_asr_protocol")
    if not asr.get("source", {}).get("license"):
        problems.append("license_missing: speech_asr")
    if len(tts.get("sentences", [])) != COUNTS["tts_sentences"] or len(tts.get("minimal_pairs", [])) != COUNTS["minimal_pairs"]:
        problems.append("speech_tts_count")
    return problems
