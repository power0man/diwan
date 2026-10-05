#!/usr/bin/env python3
"""دليلُ مصدر الأوزان: كلُّ وزنٍ في `registry/model_licenses.json` يُنزَّل من `origin` ونصُّ رخصته من `license_source`، ويُقاس.

ملاحظة Codex على #307: كان حقلا `origin` و`license_source` يُفحصان بصيغة الرابط وحدها، فيقبل السجلُّ رابطًا لا علاقة له
بالبايتات التي قيست. فهذه الأداة تنزّل كلَّ وزنٍ من أصله المعلن (ومن الأرشيف تستخرج العضوَ الذي اسمُه اسمُ الوزن)، وتطابق
بصمتَه بالمسجَّلة، وتقرأ نصَّ الرخصة من مصدره وتطابق بصمتَه إن قيّدها السجلّ. ثم تكتب دليلًا في `docs/probe/` يربط الأربعة:
الملفّ وبصمتُه وأصلُه ومصدرُ رخصته. و`tools/model_licenses.py` يرفض كلَّ وزنٍ مسجَّل لا يطابقه دليلٌ كهذا.

والرخصةُ المعلنة لا تُنسخ من السجلّ: تُسمّى من النصّ المقيس (`identify_license`)، وما خالفها يُسمّى ولا يُكتب.

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
    "an_archive_origin_is_bound_by_its_registered_digest_and_by_the_member_whose_name_is_the_weight_file",
    "the_license_text_is_hashed_as_served_and_its_identifier_is_named_from_that_text_not_copied_from_the_registry",
    "only_apache_2_0_and_mit_are_named_by_their_spdx_body_digest_and_any_other_license_text_cannot_be_measured_until_added",
    "an_mit_preamble_may_hold_its_title_and_copyright_lines_whose_holder_text_is_not_read",
    "an_attribution_is_a_copyright_line_above_the_license_body_and_is_required_only_for_mit_whose_text_carries_one",
]

# جسمُ كلّ رخصةٍ معروفة بعد التطبيع بين علامتين ثابتتين، وبصمتُه من نصّ SPDX الرسميّ (spdx/license-list-data، text/).
# وما يجوز حوله: بعد جسم أباتشي ملحقُه القياسيّ أو لا شيء؛ وقبل جسم MIT عنوانُها وأسطرُ حقوق النشر وحدها (ملاحظة Codex على #307)
KNOWN_LICENSES = {
    "apache-2.0": ("apache license version 2.0, january 2004", "end of terms and conditions",
                   "8c62318b81a63eb0bfdd853666beb1b33fdae84284df9d3483c17b82db65ced8",
                   frozenset({"023cb68c721da92c871369f1a99cc2807e68a5fd788a446c288e10d56177e13f"}), None),
    "mit": ("permission is hereby granted, free of charge", "other dealings in the software.",
            "56959050891f7b737ac4e803051170c7197f02df73d80bd9a78d968e305bf558",
            frozenset(), re.compile(r"(the )?mit license( \(mit\))?|copyright\b.*")),
}


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


def _normalized(text: str) -> str:
    return " ".join(re.sub(r"https?://", "", text.lower()).split())


def _start(text: str, phrase: str) -> re.Match | None:
    return re.search(r"\s+".join(map(re.escape, phrase.split())), text, re.IGNORECASE)


def license_notices(data: bytes) -> set[str]:
    """أسطرُ حقوق النشر قبل جسم الرخصة بعد التطبيع: الإشعارُ الذي تشترط MIT نشرَه مع البرنامج (ملاحظة Codex على #307)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return set()
    starts = [match.start() for start, *_ in KNOWN_LICENSES.values() if (match := _start(text, start))]
    head = text[:min(starts)] if starts else ""
    return {line for line in map(_normalized, head.splitlines()) if re.match(r"copyright\b", line)}


def identify_license(data: bytes) -> str | None:
    """الرخصةُ التي هذا نصُّها: جسمُها بعد التطبيع بصمةُ نصّ SPDX، ولا شيء حوله إلا ما يجوز لها. وما سواها لا يُسمّى."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    norm = _normalized(text)
    for name, (start, end, body, appendices, preamble) in KNOWN_LICENSES.items():
        i = norm.find(start)
        j = norm.find(end, i) if i >= 0 else -1
        if j < 0 or _sha(norm[i:j + len(end)].encode()) != body:
            continue
        tail = norm[j + len(end):].strip()
        if tail and _sha(tail.encode()) not in appendices:
            continue
        head = _start(text, start)
        lines = [line for line in map(_normalized, text[:head.start()].splitlines()) if line] if head else [""]
        if lines and (preamble is None or not all(preamble.fullmatch(line) for line in lines)):
            continue
        return name
    return None


def measure(models: dict, day: str, read: Callable[[str], bytes] = fetch) -> tuple[dict, list[str]]:
    """الدليلُ وما خالف فيه المنزَّلُ السجلَّ. ولا يُعتدّ بالدليل إلا والقائمةُ فارغة."""
    out: dict[str, dict] = {}
    problems = []
    for name, entry in sorted(models.items()):
        # نصُّ رخصة النموذج من مصدره المقيَّد، إن قيّد السجلُّ بصمتَه (ملاحظة Codex على #307)
        if isinstance(entry, dict) and "license_text_sha256" in entry:
            served = read(entry["source"])
            text = _sha(served)
            if text != entry["license_text_sha256"]:
                problems.append(f"license_text_differs_at_source:{name}")
            elif identify_license(served) != entry["license"]:
                problems.append(f"license_not_the_text:{name}")
            elif entry.get("read_on") != day:
                problems.append(f"read_on_not_the_measurement_day:{name}")
            else:
                out.setdefault(name, {})["license_provenance"] = {"source": entry["source"], "license_text_sha256": text,
                                                                  "license": entry["license"], "read_on": day}
        weights = entry.get("weights") if isinstance(entry, dict) else None
        for weight in weights if isinstance(weights, list) else []:
            label = f"{name}:{weight['file']}"
            # تاريخُ القراءة المنشور يومُ هذا القياس، فلا يُعلن السجلُّ يومًا لم يُقرأ فيه النصّ (ملاحظة Codex على #307)
            if weight.get("read_on") != day:
                problems.append(f"read_on_not_the_measurement_day:{label}")
                continue
            origin = read(weight["origin"])
            # الأصلُ المنزَّل كلُّه هو المقيَّد في السجلّ، لا عضوُه وحده؛ فبصمتُه المنشورة مقيسةٌ مطابِقة (ملاحظة Codex على #307)
            if weight.get("origin_sha256") not in (None, _sha(origin)):
                problems.append(f"origin_digest_differs_at_origin:{label}")
                continue
            measured = weight_bytes(origin, weight["file"])
            if measured is None:
                problems.append(f"weight_not_in_origin:{label}")
                continue
            if _sha(measured) != weight["sha256"]:
                problems.append(f"weight_digest_differs_at_origin:{label}")
                continue
            served = read(weight["license_source"])
            license_text = _sha(served)
            if weight.get("license_text_sha256") not in (None, license_text):
                problems.append(f"license_text_differs_at_source:{label}")
                continue
            if identify_license(served) != weight["license"]:
                problems.append(f"license_not_the_text:{label}")
                continue
            # الإسنادُ المنشور سطرُ حقوق نشرٍ قبل جسم الرخصة المقيسة بعينه، لا نصٌّ يُنسخ من السجلّ؛ ولازمٌ لرخصةٍ تشترط
            # نشرَ إشعارها، فلا تبارك إعادةُ التوليد حذفَه (ملاحظتا Codex على #307)
            attribution = weight.get("attribution")
            if attribution is None and weight["license"] in ml.NOTICE_LICENSES:
                problems.append(f"attribution_missing:{label}")
                continue
            if attribution is not None and (not isinstance(attribution, str)
                                            or _normalized(attribution) not in license_notices(served)):
                problems.append(f"attribution_not_in_text:{label}")
                continue
            model = out.setdefault(name, {})
            model.setdefault("models_sha256", {})[weight["file"]] = weight["sha256"]
            model.setdefault("weight_provenance", []).append({
                "file": weight["file"], "sha256": weight["sha256"], "origin": weight["origin"],
                "origin_sha256": _sha(origin), "license_source": weight["license_source"], "license": weight["license"],
                "read_on": day, "license_text_sha256": license_text,
                **({"attribution": attribution} if attribution is not None else {})})
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
