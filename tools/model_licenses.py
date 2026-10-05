#!/usr/bin/env python3
"""رخصةُ كلِّ نموذجٍ تسمّيه أدلّةُ docs/probe مقروءةٌ من مصدرها، لا مخمَّنة (جديد-license-tagging، #301).

السجلّ `registry/model_licenses.json` يحمل لكل معرّفٍ معياريّ إحدى حالتين:
- **محلولة:** الرخصةُ ومصدرُها وتاريخُ قراءتها.
- **منتظِرة:** سببٌ مسمًّى، كأن تُقرأ بـ`ollama show --license` على الماك.

والأدلّةُ التاريخيّة لا تُعدَّل، وأسماؤها مقيّدةٌ في السجلّ (`historical_evidence`). فالحارسُ يقرن كلَّ دليلٍ بالسجلّ ويرفض:
- نموذجًا بلا قيد، وقيدًا محلولًا بلا مصدرٍ أو تاريخ.
- رخصةً في دليلٍ تخالف السجلّ.
- محرّكًا افتراضيًّا برخصةٍ غير تجاريّة (ق٦٢-٨).

والدليلُ الجديد (ليس في القائمة التاريخيّة، أو مؤرَّخٌ من تاريخ الإنفاذ) أشدّ (ملاحظتا Codex على #302):
- يُقرأ كلُّ حقلِ نموذجٍ فيه على أيّ عمق: `config.model`، و`providers[].models[].model`، وما أشبه.
- ولا يسمّي نموذجًا لم تُقرأ رخصتُه.
- ويعلن رخصةَ كلِّ نموذجٍ يسمّيه في حقلٍ أعلى `licenses` (معرّفٌ معياريّ ← رخصة) تطابق السجلّ.

تصنيفُ الرخصة آليٌّ من اسمها لا تفسير:
- `osi`: من قائمةٍ مغلقة.
- `non_commercial`: ما فيه `-nc`.
- `custom_terms`: ما سواهما، وتفسيرُه قرارُ المالك.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "registry" / "model_licenses.json"
PROBE = ROOT / "docs" / "probe"
DEFAULT_ENGINE_SOURCE = ROOT / "providers" / "ollama.py"

# حقولُ الدليل التي تسمّي نموذجًا. وما يردّه المزوّدُ صدًى (model_returned) لا يُعدّ، لأن المطلوبَ هو المسمّى
MODEL_FIELDS = ("engine", "model", "local_model")
MODEL_MAPS = ("models", "reviewers")
MODEL_KEYS = frozenset(MODEL_FIELDS + MODEL_MAPS + ("model_requested",))
OSI = frozenset({"apache-2.0", "mit", "bsd-2-clause", "bsd-3-clause", "mpl-2.0"})
PENDING_REASONS = frozenset({
    "read_with_ollama_show_license_on_the_mac",
    "read_from_the_upstream_license_file",
})
HTTPS_SOURCE = re.compile(r"^https://[^\s]+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
WEIGHT_FIELDS = ("file", "sha256", "origin", "license", "license_source", "read_on")
HF_PREFIX = re.compile(r"^(?:https://)?(?:huggingface\.co|hf\.co)/")
DEFAULT_MODEL = re.compile(r'^DEFAULT_MODEL\s*=\s*"([^"]+)"\s*$', re.MULTILINE)


def canonical(identifier: str) -> str:
    """المعرّفُ كما يُقيَّد في السجلّ:
    - بلا بادئة `ollama:`.
    - مستودعُ Hugging Face بلا بادئة نطاقه ولا لاحقة تكميمه (`:Q4_K_M`).
    - وبلا لاحقة مزوّد التوجيه (`:novita`)، فالأوزانُ نفسُها والرخصةُ رخصتُها."""
    name = identifier.strip()
    if name.startswith("ollama:"):
        name = name[len("ollama:"):]
    name = HF_PREFIX.sub("", name)
    if "/" in name and ":" in name.split("/", 1)[1]:
        name = name.rsplit(":", 1)[0]
    return name


def _named(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        # المستودعُ أولًا: الطلبُ قد يسمّي اسمًا يحوّله Hugging Face إلى مستودعٍ آخر، والرخصةُ رخصةُ ما سُحب
        for key in ("repo", "model", "name"):
            if isinstance(value.get(key), str):
                return [value[key]]
    return []


def named_models(payload: dict) -> list[str]:
    """المعرّفاتُ الخامُ التي يسمّيها الدليلُ في حقوله المعروفة، بترتيب ورودها بلا تكرار."""
    found: list[str] = []
    for key in MODEL_FIELDS:
        if key in payload:
            found += _named(payload[key])
    for key in MODEL_MAPS:
        value = payload.get(key)
        if isinstance(value, dict):
            found += [name for name in value if isinstance(name, str)]
        elif isinstance(value, list):
            found += [name for item in value for name in _named(item)]
    if isinstance(payload.get("model_requested"), str):
        found.append(payload["model_requested"])
    return list(dict.fromkeys(found))


def _names_under(key: str, value: object) -> list[str]:
    if isinstance(value, list):
        return [name for item in value for name in _named(item)]
    if isinstance(value, dict) and key in MODEL_MAPS and not any(k in value for k in ("repo", "model", "name")):
        return [name for name in value if isinstance(name, str)]
    return _named(value)


def all_named_models(payload: object) -> list[str]:
    """كلُّ معرّفٍ تحت حقلٍ يسمّي نموذجًا، على أيّ عمق، بلا تكرار. وما كان نصًّا حرًّا تحت حقل نموذجٍ يُعدّ اسمًا، فلا يُقبل
    إلا إن كان في السجلّ: يُغلق عند الشكّ."""
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in MODEL_KEYS:
                    found.extend(_names_under(key, child))
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return list(dict.fromkeys(found))


def recorded_license(payload: dict) -> str | None:
    """رخصةٌ سجّلها الدليلُ نفسُه لمحرّكه، إن سجّلها."""
    for key in ("engine", "model"):
        value = payload.get(key)
        if isinstance(value, dict) and isinstance(value.get("license"), str):
            return value["license"]
    return payload["license"] if isinstance(payload.get("license"), str) else None


def license_class(name: str) -> str:
    key = name.strip().lower()
    if key in OSI:
        return "osi"
    if re.search(r"(?:^|-)nc(?:-|$)", key):
        return "non_commercial"
    return "custom_terms"


def _valid_day(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return len(value) == 10


def entry_findings(name: str, entry: object) -> list[str]:
    """قيدُ السجلّ إمّا محلولٌ بالرخصة ومصدرٍ https وتاريخ قراءة، وإمّا منتظِرٌ بسببٍ مسمًّى. ولا يجمع الحالتين."""
    if not isinstance(entry, dict):
        return [f"entry_not_an_object:{name}"]
    if "pending" in entry:
        if set(entry) != {"pending"} or entry["pending"] not in PENDING_REASONS:
            return [f"pending_without_a_named_reason:{name}"]
        return []
    problems = []
    if not isinstance(entry.get("license"), str) or not entry["license"].strip():
        problems.append(f"license_missing:{name}")
    if not isinstance(entry.get("source"), str) or not HTTPS_SOURCE.match(entry["source"]):
        problems.append(f"source_missing:{name}")
    if not _valid_day(entry.get("read_on")):
        problems.append(f"read_on_missing:{name}")
    return problems


def evidence_findings(file: str, payload: dict, models: dict, enforced_from: str, historical: set[str]) -> list[str]:
    problems = []
    day = payload.get("date")
    enforced = file not in historical or (isinstance(day, str) and day[:10] >= enforced_from)
    names = list(dict.fromkeys(canonical(raw) for raw in (all_named_models(payload) if enforced else named_models(payload))))
    stated = payload.get("licenses") if isinstance(payload.get("licenses"), dict) else {}
    for name in names:
        entry = models.get(name)
        if entry is None:
            problems.append(f"model_not_in_registry:{file}:{name}")
        elif "pending" in entry and enforced:
            problems.append(f"license_not_read_before_new_evidence:{file}:{name}")
        elif enforced and not isinstance(stated.get(name), str):
            problems.append(f"license_not_stated_in_new_evidence:{file}:{name}")
        elif enforced and stated[name].lower() != entry["license"].lower():
            problems.append(f"recorded_license_disagrees:{file}:{name}")
    recorded = recorded_license(payload)
    if not enforced and recorded is not None and len(names) == 1:
        entry = models.get(names[0]) or {}
        if "license" in entry and entry["license"].lower() != recorded.lower():
            problems.append(f"recorded_license_disagrees:{file}:{names[0]}")
    return problems


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return [text for child in children for text in _strings(child)]


def weight_findings(models: dict, evidence: dict[str, object]) -> list[str]:
    """كلُّ وزنٍ في السجلّ بهويّته كاملةً: ملفُّه وبصمتُه وأصلُه ورخصتُه بمصدرها وتاريخ قراءتها. وبصمتُه مكتوبةٌ في دليلٍ يسمّي
    نموذجَه، فلا تُلصق رخصةٌ ببايتاتٍ غيرِ التي قيست (ملاحظة Codex على #307)."""
    measured: dict[str, set[str]] = {}
    for payload in evidence.values():
        digests = {text for text in _strings(payload) if SHA256.match(text)}
        for name in all_named_models(payload):
            measured.setdefault(canonical(name), set()).update(digests)
    problems = []
    for name, entry in sorted(models.items()):
        weights = entry.get("weights", []) if isinstance(entry, dict) else []
        if not isinstance(weights, list) or not all(isinstance(weight, dict) for weight in weights):
            problems.append(f"weights_malformed:{name}")
            continue
        for weight in weights:
            label = f"{name}:{weight.get('file')}"
            missing = [field for field in WEIGHT_FIELDS if not isinstance(weight.get(field), str) or not weight[field]]
            problems += [f"weight_field_missing:{label}:{field}" for field in missing]
            if missing:
                continue
            if weight["sha256"] not in measured.get(name, set()):
                problems.append(f"weight_digest_not_in_evidence:{label}")
            problems += [f"weight_source_not_https:{label}:{key}" for key in ("origin", "license_source")
                         if not HTTPS_SOURCE.match(weight[key])]
            if not _valid_day(weight["read_on"]):
                problems.append(f"weight_read_on_invalid:{label}")
    return problems


def default_engine(source: Path = DEFAULT_ENGINE_SOURCE) -> str | None:
    match = DEFAULT_MODEL.search(source.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def findings(registry: dict, evidence: dict[str, object], engine: str | None) -> list[str]:
    """كلُّ مخالفةٍ باسمها، مرتّبة. `evidence` اسمُ الملفّ وقيمتُه المقروءة."""
    problems: list[str] = []
    models = registry.get("models")
    enforced_from = registry.get("enforced_from")
    historical = registry.get("historical_evidence")
    if not isinstance(models, dict) or not _valid_day(enforced_from) or not isinstance(historical, list) \
            or not all(isinstance(name, str) for name in historical):
        return ["registry_malformed"]
    for name, entry in models.items():
        problems += entry_findings(name, entry)
    for file, payload in sorted(evidence.items()):
        if isinstance(payload, dict):
            problems += evidence_findings(file, payload, models, enforced_from, set(historical))
    problems += weight_findings(models, {file: payload for file, payload in evidence.items() if isinstance(payload, dict)})
    if engine is not None:
        entry = models.get(canonical(engine))
        if entry is None:
            problems.append(f"default_engine_not_in_registry:{engine}")
        elif "license" in entry and license_class(entry["license"]) == "non_commercial":
            problems.append(f"default_engine_non_commercial:{engine}")
    return sorted(set(problems))


def load_evidence(probe: Path = PROBE) -> dict[str, object]:
    return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in sorted(probe.glob("*.json"))}


def summary(registry: dict) -> dict:
    models = registry.get("models", {})
    pending = sorted(name for name, entry in models.items() if isinstance(entry, dict) and "pending" in entry)
    classes: dict[str, list[str]] = {}
    for name, entry in sorted(models.items()):
        if isinstance(entry, dict) and isinstance(entry.get("license"), str):
            classes.setdefault(license_class(entry["license"]), []).append(name)
    return {"pending": pending, "by_class": classes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    parser.add_argument("--probe", type=Path, default=PROBE)
    args = parser.parse_args(argv)
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    problems = findings(registry, load_evidence(args.probe), default_engine())
    print(json.dumps({"schema_version": 1, "status": "failed" if problems else "passed",
                      "findings": problems, **summary(registry),
                      "measurement_limits": registry.get("limits", [])}, ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
