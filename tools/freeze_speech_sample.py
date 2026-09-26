#!/usr/bin/env python3
"""يجمّد عيّنةَ التفريغ العربيّ في بنك الوسائط (غ٧) من نسخة Common Voice التي نزّلها المالك.

يُشغَّل على الماك مرّةً واحدة قبل قياس أيّ نموذج:

    python3 tools/freeze_speech_sample.py --corpus <مجلد ar في Common Voice> --release <اسم الإصدار>

القاعدةُ مسجَّلةٌ سلفًا في `evaluation/media_v1/speech.json`، فالأداةُ تطبّقها ولا تختار:
- من `test.tsv` الصفوفُ ذات `up_votes ≥ 2` و`down_votes = 0` وجملةٍ من أربع كلمات فأكثر.
- تُرتَّب ببصمة SHA-256 لاسم المقطع، ويُؤخذ أوّلُ خمسين.

يُنسخ المقطع وجملته وبصمته ورخصته فقط، ولا يُنسخ `client_id` ولا العمرُ ولا الجنس ولا اللهجة. ويرفض
الأداةُ الكتابةَ فوق عيّنةٍ مجمَّدة: العيّنةُ تُجمَّد مرّة.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANK = ROOT / "evaluation" / "media_v1"
SIZE = 50


class FreezeRefused(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code = code


def select(rows: list[dict], size: int = SIZE) -> list[dict]:
    """القاعدةُ المسجَّلة، حرفيًّا."""
    eligible = [r for r in rows
                if int(r.get("up_votes") or 0) >= 2 and int(r.get("down_votes") or 0) == 0
                and len((r.get("sentence") or "").split()) >= 4]
    eligible.sort(key=lambda r: hashlib.sha256(r["path"].encode("utf-8")).hexdigest())
    if len(eligible) < size:
        raise FreezeRefused("sample_too_small", f"{len(eligible)} مقطعًا مؤهّلًا فقط، والمطلوب {size}")
    return eligible[:size]


def freeze(corpus: Path, release: str, bank: Path = BANK) -> dict:
    speech = json.loads((bank / "speech.json").read_text(encoding="utf-8"))
    if speech["asr"]["status"] != "pending_fetch" or (bank / "speech_sample.json").exists():
        raise FreezeRefused("sample_already_frozen", "العيّنةُ مجمَّدة؛ لا تُجمَّد مرّتين")
    if not release.strip():
        raise FreezeRefused("release_missing", "اسمُ إصدار Common Voice مطلوب")
    with (corpus / "test.tsv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t", quoting=csv.QUOTE_NONE))
    chosen = select(rows)
    target = bank / "speech"
    target.mkdir(exist_ok=False)
    clips = []
    for index, row in enumerate(chosen, start=1):
        source = corpus / "clips" / row["path"]
        if source.is_symlink() or not source.is_file():
            raise FreezeRefused("clip_missing", f"المقطع {row['path']} غائب")
        name = f"speech/a{index:02d}{source.suffix}"
        shutil.copyfile(source, bank / name)
        raw = (bank / name).read_bytes()
        clips.append({"id": f"a{index:02d}", "audio": name, "sha256": hashlib.sha256(raw).hexdigest(),
                      "bytes": len(raw), "sentence": row["sentence"], "source_path": row["path"]})
    sample = {"schema_version": 1, "dataset": speech["asr"]["source"]["dataset"], "release": release,
              "license": speech["asr"]["source"]["license"], "rule": speech["asr"]["selection"]["rule"],
              "clips": clips}
    (bank / "speech_sample.json").write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    speech["asr"]["status"] = "frozen"
    (bank / "speech.json").write_text(json.dumps(speech, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = json.loads((bank / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] += [{"path": c["audio"], "sha256": c["sha256"], "bytes": c["bytes"]} for c in clips]
    (bank / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "frozen", "clips": len(clips), "bytes": sum(c["bytes"] for c in clips)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--release", required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(freeze(args.corpus, args.release), ensure_ascii=False))
    except FreezeRefused as exc:
        print(json.dumps({"status": "refused", "code": exc.code, "detail": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
