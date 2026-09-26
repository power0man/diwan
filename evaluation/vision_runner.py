"""غ١١ (#52): قياسُ نماذج الرؤية على شطر الفهم المجمَّد من بنك غ٧ (٤٠ صورة) بعتباته المسجَّلة.

- **البنكُ يُفحص أوّلًا** (`validate_media_bank`)، ثم يُسأل النموذجُ سؤالَ كلِّ صورةٍ كما هو في البنك، بلا موجّه نظام.
- **الحكمُ على سطر «الجواب:» وحده** (`score_vision`)، والجوابُ الغائب خطأ.
- **ما يرميه النموذج على صورةٍ يُسمّى،** وتُعدّ خطأً ولا تُسقط من المقام.
- **التقريرُ يحمل ردَّ النموذج كاملًا لكل صورة،** فيُعاد حسابُه منه (`rescore`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.media_bank import BANK, score_vision, validate_media_bank, vision_report  # noqa: E402


class VisionBankRefused(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code, self.reason = code, reason


def _load(root: Path) -> dict:
    problems = validate_media_bank(root)
    if problems:
        raise VisionBankRefused("bank_invalid", "بنكُ الوسائط لا يطابق تجميده: " + "؛ ".join(problems[:5]))
    return json.loads((root / "vision.json").read_text(encoding="utf-8"))


def run(ask: Callable[[Path, str], str], engine: dict, root: Path = BANK) -> dict:
    bank = _load(root)
    responses: dict[str, str] = {}
    errors: dict[str, str] = {}
    for item in bank["items"]:
        try:
            responses[item["id"]] = ask(root / item["image"], item["question"])
        except Exception as exc:                     # يُسمّى ويُعدّ خطأً، ولا يُسقط من المقام
            errors[item["id"]] = f"{type(exc).__name__}: {str(exc)[:200]}"
    items = [{"id": item["id"], "category": item["category"], "response": responses.get(item["id"], ""),
              "correct": score_vision(item, responses.get(item["id"], ""))} for item in bank["items"]]
    return {"schema_version": 1, "suite_id": bank["suite_id"],
            "bank_manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
            "engine": engine, "thresholds": bank["thresholds"], "report": vision_report(bank, responses),
            "errors": errors, "items": items}


def rescore(result: dict, root: Path = BANK) -> dict:
    """يعيد حسابَ التقرير من الردود المسجَّلة فيه وحدها."""
    return vision_report(_load(root), {row["id"]: row["response"] for row in result["items"]})


def main(argv: list[str] | None = None) -> int:
    from evaluation.ollama_vision import OllamaVision, VisionRefused
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True, help="نموذجُ رؤيةٍ مثبَّتٌ في Ollama المحلي")
    parser.add_argument("--out", type=Path, help="يُكتب التقرير هنا (JSON)؛ وإلّا يُطبع")
    args = parser.parse_args(argv)
    try:
        client = OllamaVision(args.model)
        result = run(client.ask, {"name": f"ollama:{args.model}", "version": f"{client.digest[:12]} / ollama "
                                  f"{client.runtime}", "settings": client.settings})
    except (VisionRefused, VisionBankRefused) as exc:
        print(json.dumps({"status": "refused", "code": exc.code, "reason": exc.reason}, ensure_ascii=False))
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=1) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(json.dumps({"status": "ok", "engine": result["engine"]["name"], **result["report"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
