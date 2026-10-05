#!/usr/bin/env python3
"""دليلُ مصدر الأوزان: كلُّ وزنٍ في `registry/model_licenses.json` يُنزَّل من `origin` ونصُّ رخصته من `license_source`، ويُقاس.

ملاحظة Codex على #307: كان حقلا `origin` و`license_source` يُفحصان بصيغة الرابط وحدها، فيقبل السجلُّ رابطًا لا علاقة له
بالبايتات التي قيست. فهذه الأداة تنزّل كلَّ وزنٍ من أصله المعلن (ومن الأرشيف تستخرج العضوَ الذي اسمُه اسمُ الوزن)، وتطابق
بصمتَه بالمسجَّلة، وتقرأ نصَّ الرخصة من مصدره وتطابق بصمتَه إن قيّدها السجلّ. ثم تكتب دليلًا في `docs/probe/` يربط الأربعة:
الملفّ وبصمتُه وأصلُه ومصدرُ رخصته. و`tools/model_licenses.py` يرفض كلَّ وزنٍ مسجَّل لا يطابقه دليلٌ كهذا.

- `--write` يقيس ويكتب `docs/probe/weight-provenance-<التاريخ>.json`. وأيُّ اختلافٍ يُسمّى ولا يُكتب شيء.
- روابطُ GitHub من نوع `blob/` تُقرأ من نسختها الخام نفسِها (`raw.githubusercontent.com`).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import model_licenses as ml  # noqa: E402

BLOB = re.compile(r"^https://github\.com/(?P<repo>[^/]+/[^/]+)/blob/(?P<path>.+)$")
LIMITS = [
    "the_bytes_were_downloaded_from_the_registered_origin_on_the_recorded_day_and_a_later_change_at_that_url_is_not_seen",
    "an_archive_origin_is_bound_by_the_member_whose_name_is_the_weight_file_not_by_the_archive_bytes_alone",
    "the_license_text_is_hashed_as_served_and_its_legal_reading_is_the_registry_entry_not_this_file",
]


def raw_url(url: str) -> str:
    match = BLOB.match(url)
    return f"https://raw.githubusercontent.com/{match['repo']}/{match['path']}" if match else url


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(raw_url(url), timeout=600) as response:
        return response.read()


def weight_bytes(data: bytes, file: str) -> bytes | None:
    """البايتاتُ نفسُها، أو العضوُ الوحيد الذي اسمُه اسمُ الوزن إن كانت أرشيفًا."""
    if not zipfile.is_zipfile(io.BytesIO(data)):
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [name for name in archive.namelist() if name.rsplit("/", 1)[-1] == file]
        return archive.read(members[0]) if len(members) == 1 else None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def measure(models: dict, day: str, read: Callable[[str], bytes] = fetch) -> tuple[dict, list[str]]:
    """الدليلُ وما خالف فيه المنزَّلُ السجلَّ. ولا يُعتدّ بالدليل إلا والقائمةُ فارغة."""
    out: dict[str, dict] = {}
    problems = []
    for name, entry in sorted(models.items()):
        # نصُّ رخصة النموذج من مصدره المقيَّد، إن قيّد السجلُّ بصمتَه (ملاحظة Codex على #307)
        if isinstance(entry, dict) and "license_text_sha256" in entry:
            text = _sha(read(entry["source"]))
            if text != entry["license_text_sha256"]:
                problems.append(f"license_text_differs_at_source:{name}")
            else:
                out.setdefault(name, {})["license_provenance"] = {"source": entry["source"], "license_text_sha256": text}
        weights = entry.get("weights") if isinstance(entry, dict) else None
        for weight in weights if isinstance(weights, list) else []:
            label = f"{name}:{weight['file']}"
            origin = read(weight["origin"])
            measured = weight_bytes(origin, weight["file"])
            if measured is None:
                problems.append(f"weight_not_in_origin:{label}")
                continue
            if _sha(measured) != weight["sha256"]:
                problems.append(f"weight_digest_differs_at_origin:{label}")
                continue
            license_text = _sha(read(weight["license_source"]))
            if weight.get("license_text_sha256") not in (None, license_text):
                problems.append(f"license_text_differs_at_source:{label}")
                continue
            model = out.setdefault(name, {})
            model.setdefault("models_sha256", {})[weight["file"]] = weight["sha256"]
            model.setdefault("weight_provenance", []).append({
                "file": weight["file"], "sha256": weight["sha256"], "origin": weight["origin"],
                "origin_sha256": _sha(origin), "license_source": weight["license_source"],
                "license_text_sha256": license_text})
    evidence = {
        "schema_version": 1, "date": day, "tool": "tools/weight_provenance.py", "models": out,
        "licenses": {name: models[name]["license"] for name in out},
        "spend": {"cloud_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0,
                  "cost_basis": "local_no_charge"},
        "measurement_limits": LIMITS,
    }
    return evidence, problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", required=True)
    parser.add_argument("--day", default=dt.date.today().isoformat())
    args = parser.parse_args(argv)
    models = json.loads(ml.REGISTRY.read_text(encoding="utf-8"))["models"]
    evidence, problems = measure(models, args.day, fetch)
    if problems:
        print(json.dumps({"status": "failed", "findings": problems}, ensure_ascii=False))
        return 1
    out = ml.PROBE / f"weight-provenance-{args.day.replace('-', '')}.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "written", "file": str(out.relative_to(ROOT)),
                      "weights": sum(len(m.get("weight_provenance", [])) for m in evidence["models"].values())},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
