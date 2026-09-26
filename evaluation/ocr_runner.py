"""غ٨ (#48): قياسُ محرّكات OCR على شطر OCR من بنك الوسائط المجمَّد (غ٧) بعتباته المسجَّلة.

- **البنكُ يُفحص أوّلًا:** `validate_media_bank` قبل أيّ قراءة، فلا يُقاس محرّكٌ على بنكٍ عُدّل بعد التجميد.
- **المحرّكُ دالّةٌ من صورةٍ إلى نصّ** باسمٍ وإصدارٍ وإعدادات. وما يرميه محرّكٌ على صفحةٍ يُسمّى في التقرير
  وتُعدّ الصفحةُ خطأً كاملًا (CER ١)، ولا تُسقط من المقام.
- **الإعداداتُ الافتراضية:** المحرّكُ لا يُضبط على هذا البنك. فإعدادٌ يُختار بعد رؤية نتائجه على البنك نفسِه
  رقمٌ مضبوطٌ على الاختبار.
- **التقريرُ يحمل دليلَه:** نصَّ كلِّ صفحةٍ كما قرأها المحرّك، وCER لكل صفحة، وبصمةَ بيان البنك. فيُعاد حسابُه
  من ملفّه وحده (`rescore`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.media_bank import BANK, cer, ocr_report, validate_media_bank  # noqa: E402

TIMEOUT_S = 120


class OCRRefused(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code, self.reason = code, reason


@dataclass(frozen=True)
class Engine:
    name: str
    version: str
    read: Callable[[Path], str]
    settings: dict = field(default_factory=dict)


def _load(root: Path) -> tuple[dict, dict[str, str]]:
    problems = validate_media_bank(root)
    if problems:
        raise OCRRefused("bank_invalid", "بنكُ الوسائط لا يطابق تجميده: " + "؛ ".join(problems[:5]))
    bank = json.loads((root / "ocr.json").read_text(encoding="utf-8"))
    texts = json.loads((root / "ocr_texts.json").read_text(encoding="utf-8"))
    return bank, {t["text_id"]: t["text"] for t in texts["texts"]}


def run(engine: Engine, root: Path = BANK) -> dict:
    bank, texts = _load(root)
    hypotheses: dict[str, str] = {}
    errors: dict[str, str] = {}
    for item in bank["items"]:
        try:
            hypotheses[item["id"]] = engine.read(root / item["image"])
        except Exception as exc:                     # يُسمّى ويُعدّ خطأً كاملًا، ولا يُسقط من المقام
            errors[item["id"]] = f"{type(exc).__name__}: {str(exc)[:200]}"
    items = []
    for item in bank["items"]:
        reference, hypothesis = texts[item["text_id"]], hypotheses.get(item["id"], "")
        row = {"id": item["id"], "text_id": item["text_id"], "level": item["level"], "font": item["font"],
               "tashkeel": item["tashkeel"], "cer": round(cer(reference, hypothesis), 4), "hypothesis": hypothesis}
        if item["tashkeel"]:
            row["cer_with_diacritics"] = round(cer(reference, hypothesis, keep_diacritics=True), 4)
        items.append(row)
    return {"schema_version": 1, "suite_id": bank["suite_id"],
            "bank_manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
            "engine": {"name": engine.name, "version": engine.version, "settings": engine.settings},
            "thresholds": bank["thresholds"], "report": ocr_report(bank, texts, hypotheses),
            "errors": errors, "items": items}


def rescore(result: dict, root: Path = BANK) -> dict:
    """يعيد حسابَ التقرير من نصوص المحرّك المسجَّلة فيه وحدها: الرقمُ المنشور يطابق دليلَه."""
    bank, texts = _load(root)
    return ocr_report(bank, texts, {row["id"]: row["hypothesis"] for row in result["items"]})


# ————— المحرّكات —————

def tesseract(executable: str | None = None, language: str = "ara") -> Engine:
    """Tesseract بإعداداته الافتراضية (تقسيمُ الصفحة الآليّ ومحرّكُ LSTM)، ولا ضبطَ على البنك."""
    executable = executable or shutil.which("tesseract")
    if not executable:
        raise OCRRefused("ocr_engine_unavailable", "tesseract غائب؛ ثبّته مع بيانات العربية (tesseract-ocr-ara)")
    try:
        version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=30)
        listing = subprocess.run([executable, "--list-langs"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OCRRefused("ocr_engine_unavailable", f"تعذّر تشغيلُ tesseract: {type(exc).__name__}") from None
    languages = listing.stdout.splitlines()
    if language not in (line.strip() for line in languages[1:]):
        raise OCRRefused("ocr_engine_unavailable", f"بياناتُ اللغة «{language}» غائبةٌ عن tesseract")
    tessdata = re.search(r'"(.+?)"', languages[0]) if languages else None
    model = Path(tessdata.group(1)) / f"{language}.traineddata" if tessdata else None
    settings = {"language": language, "arguments": "default (no --psm, no --oem)",
                "traineddata_sha256": hashlib.sha256(model.read_bytes()).hexdigest()
                if model and model.is_file() else None}

    def read(image: Path) -> str:
        done = subprocess.run([executable, str(image), "-", "-l", language], capture_output=True, text=True,
                              timeout=TIMEOUT_S)
        if done.returncode != 0:
            raise RuntimeError(f"tesseract exit {done.returncode}: {done.stderr.strip()[-200:]}")
        return done.stdout

    return Engine("tesseract", (version.stdout or version.stderr).splitlines()[0].strip(), read, settings)


ENGINES = {"tesseract": tesseract}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", choices=sorted(ENGINES), required=True)
    parser.add_argument("--out", type=Path, help="يُكتب التقرير هنا (JSON)؛ وإلّا يُطبع")
    args = parser.parse_args(argv)
    try:
        result = run(ENGINES[args.engine]())
    except OCRRefused as exc:
        print(json.dumps({"status": "refused", "code": exc.code, "reason": exc.reason}, ensure_ascii=False))
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=1) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps({"status": "ok", "engine": result["engine"]["name"], **result["report"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
