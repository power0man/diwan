#!/usr/bin/env python3
"""THIRD-PARTY.md: رخصةُ كلِّ حزمةٍ في `uv.lock` وكلِّ نموذجٍ في `registry/model_licenses.json` (جديد-license-tagging، #301).

- `--write` يقرأ رخصةَ كلِّ حزمةٍ بنسختها المقفلة من بيانات PyPI المنشورة (`/pypi/<name>/<version>/json`)، ولا يخمّنها.
  وما قُرئ من قبلُ للحزمة بنسختها نفسِها يُبقى، فتغييرُ سجلّ النماذج وحده لا يحتاج شبكة؛ و`--refresh` يقرأ الكلَّ من جديد.
  ورخصُ النماذج من سجلّها كما هي، والمنتظِرُ منها يُكتب منتظِرًا بسببه.
- `--check` بلا شبكة: الجدولُ يطابق القفلَ حزمةً حزمةً بنسختها، ويطابق السجلَّ نموذجًا نموذجًا برخصته، ووزنًا وزنًا برخصته وإسناده.
  فحزمةٌ تُضاف إلى القفل أو نموذجٌ إلى السجلّ بلا إعادة توليدٍ يسقط في CI.

ترتيبُ الرخصة: `license_expression` (PEP 639)، ثم حقلُ `license` إن كان سطرًا قصيرًا، ثم مصنِّفاتُ `License ::`،
وإلا «غيرُ معلنة» باسمها. والنسخةُ المحلية (`+cpu`) تُقرأ بنسختها العامة، فالرخصةُ رخصةُ الحزمة لا البناء.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "uv.lock"
MODELS = ROOT / "registry" / "model_licenses.json"
OUT = ROOT / "THIRD-PARTY.md"
PROJECT = "diwan"
UNSTATED = "unstated_in_package_metadata"
PACKAGE_ROW = re.compile(r"^\| `(?P<name>[^`]+)` \| `(?P<version>[^`]+)` \| (?P<license>[^|]+) \| (?P<source>[^|]+) \|$")
MODEL_ROW = re.compile(r"^\| `(?P<name>[^`]+)` \| (?P<license>[^|]+) \| (?P<source>[^|]+) \|$")
WEIGHT_ROW = re.compile(r"^\| `(?P<model>[^`]+)` \| `(?P<file>[^`]+)` \| (?P<license>[^|]+) \| (?P<source>[^|]+) \|$")
MODELS_HEADING = "## النماذج"
WEIGHTS_HEADING = "## أوزانُ النماذج"
PACKAGES_HEADING = "## الحزم"


def locked_packages(lock: Path = LOCK) -> list[tuple[str, str]]:
    """أزواجُ (الاسم، النسخة) في القفل بلا المشروع نفسِه؛ والاسمُ قد يرد بنسختين بحسب المنصّة فيُعدّ كلٌّ منهما."""
    data = tomllib.loads(lock.read_text(encoding="utf-8"))
    return sorted({(p["name"], p["version"]) for p in data["package"] if p["name"] != PROJECT})


def license_of(info: dict) -> str:
    """الرخصةُ كما نشرتها الحزمة في بيانات PyPI لنسختها."""
    expression = info.get("license_expression")
    if isinstance(expression, str) and expression.strip():
        return expression.strip()
    text = info.get("license")
    if isinstance(text, str) and text.strip() and "\n" not in text.strip() and len(text.strip()) <= 80:
        return text.strip()
    classifiers = [c.rsplit("::", 1)[1].strip() for c in info.get("classifiers") or []
                   if isinstance(c, str) and c.startswith("License ::") and "::" in c]
    return " / ".join(dict.fromkeys(classifiers)) if classifiers else UNSTATED


def _cell(value: str) -> str:
    return value.replace("|", "/").strip()


def package_source(name: str, version: str) -> str:
    return f"https://pypi.org/project/{name}/{version.split('+', 1)[0]}/"


def model_source(entry: dict) -> str:
    return "—" if "pending" in entry else f"{entry['source']} ({entry['read_on']})"


def model_weights(models: dict) -> list[tuple[str, dict]]:
    """أوزانُ كلِّ نموذجٍ في السجلّ (حقلُ `weights`)، مرتّبةً بالنموذج ثم بالملفّ."""
    return [(name, weight) for name, entry in sorted(models.items())
            for weight in sorted(entry.get("weights", []), key=lambda w: w["file"])]


def weight_license(weight: dict) -> str:
    """رخصةُ الوزن من ناشره الأصليّ، ومعها إسنادُه إن قيّده السجلّ (كاشفُ CRAFT بـMIT لا برخصة EasyOCR: Codex على #307)."""
    attribution = weight.get("attribution")
    return _cell(weight["license"] + (f"؛ {attribution}" if attribution else ""))


def weight_source(weight: dict) -> str:
    return f"{weight['license_source']} ({weight['read_on']})"


def fetch(name: str, version: str) -> dict:
    public = version.split("+", 1)[0]
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{public}/json", timeout=30) as response:
        return json.loads(response.read())["info"]


def render(packages: list[tuple[str, str, str]], models: dict) -> str:
    lines = [
        "# مكوّناتُ الأطراف الثالثة ورخصُها",
        "",
        "مولَّدٌ بـ`python3 tools/third_party.py --write`، ولا يُحرَّر يدويًّا. و`--check` يطابقه بـ`uv.lock` وبـ`registry/model_licenses.json`",
        "في CI. رخصةُ ديوان نفسِه Apache-2.0 (`LICENSE`، و`NOTICE`).",
        "",
        "قُرئت رخصةُ كلِّ حزمةٍ من بيانات PyPI المنشورة لنسختها حين دخلت القفل. وهي حقلُ الرخصة كما أعلنته الحزمة، لا قراءةٌ",
        "قانونيّة لشروطها. ورخصُ النماذج من سجلّها بمصدرها وتاريخ قراءتها؛ والمنتظِرُ منها لم يُقرأ بعد، فلا يُعدّ معروفًا.",
        "",
        PACKAGES_HEADING,
        "",
        "| الحزمة | النسخة | الرخصة كما أعلنتها | المصدر |",
        "|---|---|---|---|",
    ]
    for name, version, license_name in packages:
        lines.append(f"| `{name}` | `{version}` | {_cell(license_name)} | {package_source(name, version)} |")
    lines += ["", MODELS_HEADING, "", "| النموذج | الرخصة | المصدر |", "|---|---|---|"]
    for name, entry in sorted(models.items()):
        license_cell = f"تنتظر القراءة: `{entry['pending']}`" if "pending" in entry else _cell(entry["license"])
        lines.append(f"| `{name}` | {license_cell} | {model_source(entry)} |")
    lines += ["", WEIGHTS_HEADING, "",
              "الوزنُ ملفٌّ يحمّله المحرّك، ورخصتُه رخصةُ ناشره الأصليّ وإن وزّعه غيرُه، ومعها إسنادُه إن طلبته.", "",
              "| النموذج | الوزن | الرخصة والإسناد | مصدر الرخصة |", "|---|---|---|---|"]
    for name, weight in model_weights(models):
        lines.append(f"| `{name}` | `{weight['file']}` | {weight_license(weight)} | {weight_source(weight)} |")
    return "\n".join(lines) + "\n"


def _section(text: str, heading: str, row: re.Pattern) -> list[dict]:
    rows, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.strip() == heading
            continue
        match = row.match(line) if inside else None
        if match:
            rows.append(match.groupdict())
    return rows


def listed_licenses(text: str) -> dict[tuple[str, str], str]:
    """رخصُ الحزم المكتوبة من قبلُ بنسخها، لتُبقى ولا يُعاد طلبُها."""
    return {(r["name"], r["version"]): r["license"].strip() for r in _section(text, PACKAGES_HEADING, PACKAGE_ROW)}


def check(text: str, lock: list[tuple[str, str]], models: dict) -> list[str]:
    """ما يخالف فيه الجدولُ القفلَ والسجلّ، مرتّبًا باسمه."""
    problems = []
    listed = _section(text, PACKAGES_HEADING, PACKAGE_ROW)
    pairs = {(r["name"], r["version"]) for r in listed}
    problems += [f"package_not_listed:{n}=={v}" for n, v in lock if (n, v) not in pairs]
    problems += [f"package_not_locked:{n}=={v}" for n, v in sorted(pairs) if (n, v) not in set(lock)]
    # المصدرُ مشتقٌّ من الاسم والنسخة، فخانتُه المحرَّرة يدويًّا تخالفه (ملاحظة Codex على #302)
    problems += [f"package_source_differs:{r['name']}=={r['version']}" for r in listed
                 if r["source"].strip() != package_source(r["name"], r["version"])]
    model_rows = _section(text, MODELS_HEADING, MODEL_ROW)
    # صفٌّ مكرَّر يُخفي ما قبله في القاموس، فيمرّ صفٌّ مناقضٌ قبل الصحيح (ملاحظة Codex على #307)
    problems += _twice("model_listed_twice", [r["name"] for r in model_rows])
    rows = {r["name"]: r for r in model_rows}
    for name, entry in sorted(models.items()):
        row = rows.get(name)
        if row is None:
            problems.append(f"model_not_listed:{name}")
        elif "pending" in entry:
            if entry["pending"] not in row["license"]:
                problems.append(f"model_license_differs:{name}")
        elif row["license"].strip() != _cell(entry["license"]):
            problems.append(f"model_license_differs:{name}")
        # مصدرُ الرخصة وتاريخُ قراءتها جزءٌ من القيد: تصحيحُهما في السجلّ بلا إعادة توليدٍ يترك الجدولَ بمصدرٍ قديم (Codex على #302)
        if row is not None and row["source"].strip() != model_source(entry):
            problems.append(f"model_source_differs:{name}")
    problems += [f"model_not_in_registry:{name}" for name in sorted(rows) if name not in models]
    weight_rows = _section(text, WEIGHTS_HEADING, WEIGHT_ROW)
    problems += _twice("weight_listed_twice", [f"{r['model']}/{r['file']}" for r in weight_rows])
    listed_weights = {(r["model"], r["file"]): r for r in weight_rows}
    expected = model_weights(models)
    for name, weight in expected:
        key, row = f"{name}/{weight['file']}", listed_weights.get((name, weight["file"]))
        if row is None:
            problems.append(f"weight_not_listed:{key}")
            continue
        if row["license"].strip() != weight_license(weight):
            problems.append(f"weight_license_differs:{key}")
        if row["source"].strip() != weight_source(weight):
            problems.append(f"weight_source_differs:{key}")
    known = {(name, weight["file"]) for name, weight in expected}
    problems += [f"weight_not_in_registry:{m}/{f}" for m, f in sorted(listed_weights) if (m, f) not in known]
    return sorted(problems)


def _twice(code: str, keys: list[str]) -> list[str]:
    return [f"{code}:{key}" for key in sorted(set(keys)) if keys.count(key) > 1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="مع --write: يقرأ كلَّ رخصةٍ من PyPI ولا يُبقي المكتوب")
    args = parser.parse_args(argv)
    lock = locked_packages()
    models = json.loads(MODELS.read_text(encoding="utf-8"))["models"]
    if args.write:
        known = {} if args.refresh or not OUT.is_file() else listed_licenses(OUT.read_text(encoding="utf-8"))
        packages = [(name, version, known.get((name, version)) or license_of(fetch(name, version)))
                    for name, version in lock]
        OUT.write_text(render(packages, models), encoding="utf-8")
        unstated = [f"{n}=={v}" for n, v, lic in packages if lic == UNSTATED]
        print(json.dumps({"status": "written", "packages": len(packages), "models": len(models),
                          "read_from_pypi": sum(1 for pair in lock if pair not in known),
                          "unstated": unstated}, ensure_ascii=False))
        return 0
    problems = check(OUT.read_text(encoding="utf-8") if OUT.is_file() else "", lock, models)
    print(json.dumps({"status": "failed" if problems else "verified", "findings": problems}, ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
