#!/usr/bin/env python3
"""رخصةُ كلِّ نموذجٍ تسمّيه أدلّةُ docs/probe مقروءةٌ من مصدرها، لا مخمَّنة (جديد-license-tagging، #301).

السجلّ `registry/model_licenses.json` يحمل لكل معرّفٍ معياريّ إحدى حالتين:
- **محلولة:** الرخصةُ ومصدرُها وتاريخُ قراءتها.
- **منتظِرة:** سببٌ مسمًّى، كأن تُقرأ بـ`ollama show --license` على الماك.

والأدلّةُ التاريخيّة لا تُعدَّل، وأسماؤها مقيّدةٌ في السجلّ (`historical_evidence`) ومع كلٍّ بصمةُ محتواه، فما عُدِّل منها بعد
التجميد جديدٌ يُفحص كاملًا (ملاحظة Codex على #307). فالحارسُ يقرن كلَّ دليلٍ بالسجلّ ويرفض:
- نموذجًا بلا قيد، وقيدًا محلولًا بلا مصدرٍ أو تاريخ.
- رخصةً في دليلٍ تخالف السجلّ.
- محرّكًا افتراضيًّا برخصةٍ غير تجاريّة (ق٦٢-٨).

والدليلُ الجديد (ليس تاريخيًّا مجمَّدًا، أو مؤرَّخٌ من تاريخ الإنفاذ) أشدّ (ملاحظتا Codex على #302):
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
import hashlib
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
# وبصمةُ نصّ رخصة الوزن لازمةٌ لكلّ وزن، فلا يُقبل نصٌّ آخر في مصدر رخصته بلا أن يُرى (ملاحظة Codex على #307)
# وبصمةُ الأصل المنزَّل كلِّه (الأرشيف أو الملفّ) مقيَّدةٌ كذلك، فلا يعلن الدليلُ للأصل بصمةً لا يقابلها شيء (ملاحظة Codex على #307)
WEIGHT_FIELDS = ("file", "sha256", "origin", "origin_sha256", "license", "license_source", "read_on", "license_text_sha256")
WEIGHT_DIGESTS = ("sha256", "origin_sha256", "license_text_sha256")
# سجلُّ المصدر في الدليل: ما نزّلته `tools/weight_provenance.py` من الأصل المعلن فطابقت بصمتُه (ملاحظة Codex على #307)
PROVENANCE_KEY = "weight_provenance"
# والرخصةُ المعلنة وتاريخُ قراءتها جزءٌ من الربط، فلا يتغيّر وسمُها ولا تاريخُها في السجلّ دون دليلٍ جديد يقيس نصَّها
# في ذلك اليوم (ملاحظتا Codex على #307)
PROVENANCE_FIELDS = ("file", "sha256", "origin", "origin_sha256", "license_source", "license", "read_on")
# ونصُّ رخصة النموذج كما قيس من مصدره، فلا تُقيَّد بصمةُ نصٍّ لم يُقرأ (ملاحظة Codex على #307)
LICENSE_PROVENANCE_KEY = "license_provenance"
LICENSE_PROVENANCE_FIELDS = ("source", "license_text_sha256", "license", "read_on")
LICENSE_FILE_READ = "upstream_license_file_at_the_release_tag_the_probes_name"
# رخصٌ يحمل نصُّها إشعارَ حقوق نشرٍ يُشترط نشرُه مع الوزن، فإسنادُه لازمٌ لا اختياريّ (ملاحظة Codex على #307)
NOTICE_LICENSES = frozenset({"mit"})
UNOWNED = ("",)
# أنواعُ البصمات التي يكتبها المستودع لبياناتٍ لا لبايتات نموذج (`<نوع>_sha256`). وما سواها في دليلٍ جديد أثرٌ يُطالَب بقيده،
# فلا يمرّ `checkpoint_sha256` أو `model_artifact_sha256` بلا أصلٍ ولا رخصة (ملاحظة Codex على #307). ومنها ما تكتبه أدواتُ
# المراجعة للمراجَع: `artifact` (`evaluation/multi_system_review.py`)، و`file` (`evaluation/external_review.py`)، و`review_artifact`،
# وملفّا الأساس والمرشَّح (`baseline_file`، `candidate_file`)؛ وبصمةٌ منها بجانب مسار وزنٍ تُقيَّد باسم ملفّه (ملاحظة Codex على #307)
NON_ARTIFACT_KINDS = frozenset({
    "after_report", "anchor", "answer", "artifact", "audio", "bank", "bank_manifest", "baseline", "baseline_file",
    "baseline_raw_report", "baseline_spec", "before_report", "binary", "brief", "bundle", "calibration", "candidate",
    "candidate_file", "candidate_spec", "canonical", "case_ids", "comparison", "config", "context", "contract", "corpus",
    "dialogue", "execution", "file", "forget_authority", "gate_report",
    "harness", "hook", "input_manifest", "input_snapshot", "judge_prompt", "ledger", "legacy", "license_text", "live_report",
    "loaded_source", "lock", "log", "manifest", "measured_and_integrated", "meta", "model_version", "observed", "open_bank",
    "origin", "original_trace", "output", "package_manifest", "plan", "policy", "preserved_payload", "previous", "probe",
    "prompt", "protocol", "provenance", "provider_evidence", "public_key", "raw", "raw_log", "raw_provenance", "raw_report",
    "raw_response", "raw_summary", "raw_trace", "receipt", "reference_cases", "report", "request", "response", "review_artifact",
    "resumed_trace", "root", "rubric", "runner", "runtime_lock", "runtime_receipt", "sandbox", "signature", "source",
    "source_report", "state", "stdout", "suite", "system", "test_log", "thresholds_file", "trust", "verdict_pattern", "worker",
})
# خرائطُ البصمات (`<نوع>_sha256: {اسم: بصمة}`) التي يكتبها المستودع لملفّات التشغيل ومصدرِه ومخرجاتِه، فيحكم على كلِّ ملفٍّ
# فيها `is_weight_file`. وكلُّ خريطةٍ سواها خريطةُ أثرٍ (`models_sha256`، `checkpoint_sha256`) تعلن أنّ ملفّاتها بايتاتُ نموذج،
# فتُطالَب بقيدها أيًّا كانت لاحقتُها، كما تُطالَب بصمةُ نوعها وحدها (ملاحظتا Codex على #307)
DATA_MAPS = NON_ARTIFACT_KINDS | {"artifacts", "measured_source", "private_evidence"}
# أنواعُ الأوزان التي تُقبل بصمتُها بحقل `<نوع>_sha256` بلا اسم ملفّ
WEIGHT_KINDS = frozenset({"pth", "pt", "bin", "safetensors", "gguf", "onnx", "ckpt", "h5", "hdf5", "keras", "pb", "tflite",
                          "mlmodel", "traineddata"})
# ما يُعرف أنه بياناتٌ لا أوزان (صورُ البنك، ونصوصُه، وسجلّاتُه)؛ وكلُّ لاحقةٍ سواه وزنٌ يُطالَب بقيده في الدليل الجديد، فلا
# يمرّ نوعٌ لم يُسمَّ (`.keras` و`.pb` و`.model`…) بايتاتٍ بلا رخصة (ملاحظات Codex على #307)
DATA_KINDS = frozenset({"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "svg", "pdf", "json", "jsonl", "txt",
                        "md", "csv", "tsv", "html", "xml", "yaml", "yml", "toml", "ini", "cfg", "log", "lock", "py",
                        "sh", "wav", "mp3", "flac", "ogg", "mp4"})
# أغلفةٌ تُنزع قبل قراءة النوع: `checkpoint.pth.tar` وزنُ `pth` في أرشيف (ملاحظة Codex على #307)
WRAPPERS = frozenset({"tar", "gz", "tgz", "zip", "bz2", "xz", "zst"})
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
    # نموذجٌ قُرئت رخصتُه من ملفّها أو قُيّدت له أوزانٌ يُقيَّد نصُّ رخصته ببصمته، فيطالبه `provenance_findings` بدليلٍ قاسه؛
    # ولا يُعطَّل ذلك بحذف البصمة (ملاحظة Codex على #307)
    if (entry.get("read_via") == LICENSE_FILE_READ or entry.get("weights")) \
            and not (isinstance(entry.get("license_text_sha256"), str) and SHA256.match(entry["license_text_sha256"])):
        problems.append(f"license_text_unmeasured:{name}")
    return problems


def evidence_digest(payload: object) -> str:
    """بصمةُ الدليل المقروء بصيغةٍ قانونية: لا يغيّرها تنسيقُ الملفّ، ويغيّرها كلُّ تعديلٍ في محتواه."""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def frozen_evidence(historical: object, evidence: dict[str, object]) -> set[str]:
    """الأدلّةُ التاريخيّةُ كما جُمِّدت: اسمُها في السجلّ وبصمةُ محتواها هي المقيَّدةُ معه. فدليلٌ تاريخيٌّ عُدِّل بعد التجميد جديدٌ
    يُفحص كاملًا، ولا يمرّ باسمه وتاريخه المعلَن (ملاحظة Codex على #307)."""
    frozen = historical if isinstance(historical, dict) else {}
    return {file for file, payload in evidence.items() if frozen.get(file) == evidence_digest(payload)}


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


def measured_weights(payload: object, provenance: dict[str, set[tuple[str, ...]]] | None = None,
                     licenses: dict[str, set[tuple[str, ...]]] | None = None,
                     malformed: dict[str, set[tuple[str, str]]] | None = None,
                     declared: dict[str, set[str]] | None = None,
                     ) -> dict[str, tuple[dict[str, set[str]], dict[str, set[str]]]]:
    """البصماتُ كما سجّلها الدليل، لكل نموذجٍ من شجرته وحدها (ملاحظتا Codex على #307). فالقاموسُ الذي يسمّي نموذجًا
    (`{"model": …}` أو `"engine": {"name": …}`) يملك ما تحته، فلا تُنسب بصمةُ نموذجٍ في الدليل نفسِه إلى غيره. والبصمةُ
    مربوطةٌ بملفّها: مفتاحٌ اسمُه اسمُ ملفّ (`arabic.pth`) قيمتُه بصمة، أو مفتاحٌ `<نوع>_sha256` (`traineddata_sha256`)
    لوزنٍ وحيدٍ من نوعه. وما لا مالكَ له على طريقه لا يُنسب إلى أحد. وسجلّاتُ المصدر (`weight_provenance`) تُجمع في
    `provenance`، ونصوصُ الرخص المقيسة (`license_provenance`) في `licenses`، وما شُوِّه في `malformed` برمزه: بصمةُ أثرٍ ليست
    64 محرفًا ستّ عشريًّا صغيرًا، أو سجلُّ مصدرٍ ناقص؛ بالملكيّة نفسِها."""
    out: dict[str, tuple[dict[str, set[str]], dict[str, set[str]]]] = {}

    # وما لا مالكَ له على طريقه يُجمع تحت المالك "" فيربطه `weight_findings` بالنموذج الوحيد الذي يسمّيه الدليلُ الجديد أو
    # يسمّيه بلا مالك، ولا يُسقط صامتًا (ملاحظة Codex على #307)
    def flag(owners: tuple[str, ...], code: str, key: str) -> None:
        for owner in owners or UNOWNED:
            (malformed if malformed is not None else {}).setdefault(owner, set()).add((code, key))

    def record(owners: tuple[str, ...], key: str, digest: str) -> None:
        for owner in owners or UNOWNED:
            by_file, by_kind = out.setdefault(owner, ({}, {}))
            if "." in key:
                by_file.setdefault(key, set()).add(digest)
            elif key.endswith("_sha256"):
                by_kind.setdefault(key.removesuffix("_sha256"), set()).add(digest)

    def visit(value: object, owners: tuple[str, ...]) -> None:
        if isinstance(value, list):
            for child in value:
                visit(child, owners)
            return
        if not isinstance(value, dict):
            return
        # كلُّ اسمٍ يقرؤه `all_named_models` هنا يملك ما بجانبه: نصًّا، أو قاموسًا (`"engine": {"name": …}`)، أو في قائمةٍ
        # أو خريطة (`"models": ["asr"]`). فإن سمّى الدليلُ أكثرَ من نموذجٍ طولب كلٌّ منهم بقيد البصمة، فلا تمرّ بصمةٌ مبهمةُ
        # المالك (ملاحظتا Codex على #307)
        here = tuple(dict.fromkeys(canonical(name) for k, v in value.items() if k in MODEL_KEYS for name in _names_under(k, v)))
        owners = here or owners
        for key, child in value.items():
            path = _digest_path(value, key) if isinstance(key, str) and key.endswith("_sha256") and not isinstance(child, dict) else None
            if path is not None:
                # بصمةٌ بجانب مسارها بصمةُ ذلك الملفّ: تُقيَّد باسمه لا بنوعها، فملفُّ البيانات لا يُطالَب وملفُّ الوزن يُطالَب
                # (ملاحظة Codex على #307)
                if isinstance(child, str) and SHA256.match(child):
                    for owner in owners or UNOWNED:
                        out.setdefault(owner, ({}, {}))[0].setdefault(path, set()).add(child)
                elif is_weight_file(path):
                    flag(owners, "weight_digest_malformed", path)
                continue
            if isinstance(key, str) and isinstance(child, str) and SHA256.match(child):
                record(owners, key, child)
            elif isinstance(key, str) and "." in key and weight_kind(key) in WEIGHT_KINDS:
                # اسمُ ملفّ وزنٍ قيمتُه بصمتُه، أو قاموسٌ فيه `sha256`؛ وما سواهما (null أو نصٌّ أو رقمٌ أو قائمة أو قاموسٌ بلا
                # بصمة) يُسمّى ولا يُسقط صامتًا. ويُقصر على أنواع الأوزان المعروفة، فاسمُ نموذجٍ مفتاحًا (`qwen3.5-9b`) ليس ملفًّا
                # (ملاحظة Codex على #307)
                digest = child.get("sha256") if isinstance(child, dict) else child
                if isinstance(digest, str) and SHA256.match(digest):
                    record(owners, key, digest)
                else:
                    flag(owners, "weight_digest_malformed", key)
                visit(child, owners)
            elif isinstance(key, str) and key.endswith("_sha256") and not isinstance(child, dict):
                # بصمةُ أثرٍ مشوَّهة (نصٌّ على غير صيغتها، أو null أو رقمٌ أو قائمة) تُسمّى ولا تُسقط صامتةً، فلا يمرّ أثرٌ بلا قيد
                # (ملاحظتا Codex على #307)
                if key.removesuffix("_sha256") not in NON_ARTIFACT_KINDS:
                    flag(owners, "weight_digest_malformed", key.removesuffix("_sha256"))
                visit(child, owners)
            elif isinstance(key, str) and key.endswith("_sha256") and isinstance(child, dict):
                # خريطةُ بصماتٍ بأسماء الملفّات: كلُّ مفتاحٍ فيها اسمُ ملفّ ولو بلا لاحقة (`checkpoint`). يحكم عليه
                # `is_weight_file` في خرائط البيانات (`DATA_MAPS`)، وما سواها خريطةُ أثرٍ تعلن أنّ ملفّاتها بايتاتُ نموذج، فتُطالَب
                # أيًّا كانت لاحقتُها (ملاحظات Codex على #307)
                declares = key.removesuffix("_sha256") not in DATA_MAPS
                for name, digest in child.items():
                    if isinstance(name, str) and isinstance(digest, str) and SHA256.match(digest):
                        for owner in owners or UNOWNED:
                            out.setdefault(owner, ({}, {}))[0].setdefault(name, set()).add(digest)
                            if declares:
                                (declared if declared is not None else {}).setdefault(owner, set()).add(name)
                    elif isinstance(name, str) and (declares or is_weight_file(name)):
                        flag(owners, "weight_digest_malformed", name)
                visit(child, owners)
            elif key == PROVENANCE_KEY and isinstance(child, list):
                for index, item in enumerate(child):
                    if isinstance(item, dict) and all(isinstance(item.get(f), str) for f in PROVENANCE_FIELDS):
                        for owner in owners or UNOWNED:
                            (provenance if provenance is not None else {}).setdefault(owner, set()).add(
                                (*(item[f] for f in PROVENANCE_FIELDS), item.get("license_text_sha256"), item.get("attribution")))
                            if SHA256.match(item["sha256"]):
                                # سجلُّ المصدر قياسٌ لملفّه، فيُطالَب بقيده كما تُطالَب خريطةُ البصمات (ملاحظة Codex على #307)
                                out.setdefault(owner, ({}, {}))[0].setdefault(item["file"], set()).add(item["sha256"])
                                # وملفُّه وزنٌ بإعلان السجلّ نفسه، فيُطالَب بقيده أيًّا كانت لاحقتُه (ملاحظة Codex على #307)
                                (declared if declared is not None else {}).setdefault(owner, set()).add(item["file"])
                        if not SHA256.match(item["sha256"]):
                            flag(owners, "weight_digest_malformed", item["file"])
                        if not SHA256.match(item["origin_sha256"]):
                            # بصمةُ الأصل المنزَّل في سجلّ المصدر بصمةٌ لا نصٌّ يُكتب (ملاحظة Codex على #307)
                            flag(owners, "weight_digest_malformed", item["file"])
                        text = item.get("license_text_sha256")
                        if text is not None and not (isinstance(text, str) and SHA256.match(text)):
                            # بصمةُ نصّ الرخصة في سجلّ المصدر بصمةٌ لا نصٌّ يُكتب (ملاحظة Codex على #307)
                            flag(owners, "weight_digest_malformed", item["file"])
                    else:
                        # سجلُّ مصدرٍ ناقصٌ يُسمّى ولا يُسقط صامتًا، فلا يمرّ أثرٌ يعلنه الدليلُ بلا أصلٍ أو رخصة (ملاحظة Codex على #307)
                        named = item.get("file") if isinstance(item, dict) else None
                        flag(owners, "weight_provenance_malformed", named if isinstance(named, str) else f"#{index}")
            elif key == PROVENANCE_KEY:
                flag(owners, "weight_provenance_malformed", PROVENANCE_KEY)
            elif key == LICENSE_PROVENANCE_KEY and isinstance(child, dict):
                if all(isinstance(child.get(f), str) for f in LICENSE_PROVENANCE_FIELDS):
                    for owner in owners:
                        (licenses if licenses is not None else {}).setdefault(owner, set()).add(
                            tuple(child[f] for f in LICENSE_PROVENANCE_FIELDS))
            elif key in MODEL_MAPS and isinstance(child, dict) and not any(k in child for k in ("repo", "model", "name")):
                for name, sub in child.items():
                    visit(sub, (canonical(name),) if isinstance(name, str) else owners)
            elif key in MODEL_KEYS and isinstance(child, list):
                # قائمةُ نماذج: كلُّ عنصرٍ يملكه ما يسمّيه (`name` أو `model` أو `repo`) كما يقرؤه `all_named_models`
                # (ملاحظة Codex على #307)
                for item in child:
                    visit(item, tuple(canonical(name) for name in _named(item)) or owners)
            elif key in MODEL_KEYS and isinstance(child, dict):
                visit(child, tuple(canonical(name) for name in _named(child)) or owners)
            else:
                visit(child, owners)

    visit(payload, ())
    return out


def weight_kind(name: str) -> str:
    """نوعُ الملفّ من لاحقته بعد نزع الأغلفة: `checkpoint.pth.tar` نوعُه `pth` في التعرّف والربط معًا (ملاحظتا Codex على #307)."""
    parts = [part.lower() for part in name.split(".")[1:]]
    while parts and parts[-1] in WRAPPERS:
        parts.pop()
    return parts[-1] if parts else ""


def is_weight_file(name: str) -> bool:
    """كلُّ ملفٍّ ليس نوعُه بياناتٍ معروفة وزنٌ: `checkpoint.pth.tar` و`spm.model` و`model.keras` و`bundle.tar` أوزان، و`o01.jpg`
    و`data.model.json` ليست أوزانًا. يُغلق عند الشكّ (ملاحظات Codex على #307)."""
    return weight_kind(name) not in DATA_KINDS


def _name_hashes(names: list[str]) -> set[str]:
    """بصماتُ أسماء النموذج نصًّا ونصَّ JSON، كما يكتبها `evaluation/capabilities.py` في `model_sha256`: هويّةٌ لا بايتات."""
    return {hashlib.sha256(text.encode("utf-8")).hexdigest()
            for name in names for text in (name, json.dumps(name, ensure_ascii=False))}


def _digest_path(value: dict, key: str) -> str | None:
    """اسمُ ملفّ المسار المقابل لبصمة `<نوع>_sha256`: `<نوع>_path`، أو `path` لبصمة `file_sha256`. فبصمتُه بصمةُ ذلك الملفّ:
    ملفُّ بياناتٍ (`bank: {path: …json, file_sha256: …}`) ليس وزنًا، وملفُّ وزنٍ يُقيَّد باسمه. ولا يُصنَّف بالمسار غيرُ بصمته
    (ملاحظات Codex على #307)."""
    kind = key.removesuffix("_sha256")
    path = value.get(f"{kind}_path", value.get("path") if kind == "file" else None)
    return path.rsplit("/", 1)[-1] if isinstance(path, str) else None


def unregistered_weights(file: str, name: str, entry: dict, by_file: dict, by_kind: dict,
                         aliases: list[str] | None = None, declared: set[str] = frozenset()) -> list[str]:
    """دليلٌ جديد يسجّل لنموذجٍ في السجلّ وزنًا (ملفًّا بامتداد وزنٍ أو بصمةً بنوعه) ليس في قيوده ببصمته، فرخصةُ النموذج لا
    تُلحق به بلا قيدٍ له (ملاحظة Codex على #307). و`model_sha256` يُقبل ببصمة أيِّ وزنٍ مقيَّدٍ للنموذج، أو ببصمة اسمه."""
    weights = [w for w in entry.get("weights", []) if isinstance(w, dict)] if isinstance(entry.get("weights"), list) else []
    registered = {(w.get("file"), w.get("sha256")) for w in weights}
    by_extension = {(weight_kind(str(w.get("file"))), w.get("sha256")) for w in weights}
    by_model = {w.get("sha256") for w in weights} | _name_hashes([name, *(aliases or [])])
    problems = [f"weight_not_registered:{file}:{name}:{weight}" for weight, digests in sorted(by_file.items())
                if (is_weight_file(weight) or weight in declared) and any((weight, d) not in registered for d in digests)]
    problems += [f"weight_not_registered:{file}:{name}:{kind}" for kind, digests in sorted(by_kind.items())
                 if kind not in NON_ARTIFACT_KINDS
                 and any(d not in by_model if _untyped_artifact(kind) else (kind, d) not in by_extension for d in digests)]
    return problems


def _sole_of_its_kind(weights: list, file: str) -> bool:
    """بصمةُ النوع بلا اسم ملفّ تربط وزنًا وحيدًا من نوعه. ووزنان من نوعٍ واحد يُطلب لكلٍّ منهما ملفُّه، فلا تتبادل رخصتاهما
    البايتات (ملاحظة Codex على #307)."""
    kind = weight_kind(file)
    return sum(1 for w in weights if isinstance(w, dict) and weight_kind(str(w.get("file"))) == kind) == 1


def _bound_digests(weights: list, file: str, by_file: dict, by_kind: dict) -> set[str]:
    """بصماتُ الوزن في دليل: باسم ملفّه، وإلّا بنوعه إن كان وحيدَ نوعه، وبـ`model_sha256` أو بأثرٍ غير معروف النوع
    (`checkpoint_sha256`) إن كان وزنَ النموذج الوحيد."""
    if file in by_file:
        return by_file[file]
    found = set(by_kind.get(weight_kind(file), set())) if _sole_of_its_kind(weights, file) else set()
    if len(weights) == 1:
        found |= {digest for kind, digests in by_kind.items() if _untyped_artifact(kind) for digest in digests}
    return found


def _untyped_artifact(kind: str) -> bool:
    """بصمةٌ لا يسمّي نوعُها ملفًّا: نوعٌ ليس وزنًا معروفًا ولا بياناتٍ معروفة. ومنها `model_sha256`، يكتبه مسارُ ASR لملفّ
    الوزن ويكتبه `evaluation/capabilities.py` لاسم النموذج، فتُقبل بصمتُه بصمةَ وزنٍ مقيَّدٍ أو بصمةَ الاسم (ملاحظتا Codex على #307)."""
    return kind not in WEIGHT_KINDS and kind not in NON_ARTIFACT_KINDS


def unmeasured_weights(file: str, payload: object, models: dict, measured: dict) -> list[str]:
    """دليلٌ جديد يسمّي نموذجًا له أوزانٌ في السجلّ يسجّل بصمةَ كلِّ وزنٍ منها لملفّه أو نوعه في شجرة نموذجه، فلا تمرّ بايتاتٌ
    مستبدَلةٌ بغياب البصمة اتّكالًا على دليلٍ أقدم (ملاحظة Codex على #307)."""
    problems = []
    for name in dict.fromkeys(canonical(raw) for raw in all_named_models(payload)):
        entry = models.get(name)
        weights = entry.get("weights") if isinstance(entry, dict) else None
        by_file, by_kind = measured.get(name, ({}, {}))
        for weight in weights if isinstance(weights, list) else []:
            if not isinstance(weight, dict) or not isinstance(weight.get("file"), str):
                continue
            if weight.get("sha256") not in _bound_digests(weights, weight["file"], by_file, by_kind):
                problems.append(f"weight_not_measured_in_new_evidence:{file}:{name}:{weight['file']}")
    return problems


def _bind_unowned(file: str, payload: object, measured: dict, malformed: dict, unowned: tuple[dict, dict],
                  flags: set[tuple[str, str]], declared: dict[str, set[str]], unowned_declared: set[str]) -> list[str]:
    """بصماتُ الآثار التي لا مالكَ لها على طريقها (`{"config": {"model": …}, "checkpoint_sha256": …}`) تُنسب إلى النموذج الوحيد
    الذي يسمّيه الدليلُ كلُّه، فيُطالَب بقيدها؛ وإن سمّى غيرَ نموذجٍ واحد سُمّيت بلا مالك (ملاحظة Codex على #307)."""
    by_file, by_kind = unowned
    keys = sorted({name for name in by_file if is_weight_file(name) or name in unowned_declared}
                  | {kind for kind in by_kind if kind not in NON_ARTIFACT_KINDS} | {key for _, key in flags})
    if not keys:
        return []
    names = list(dict.fromkeys(canonical(raw) for raw in all_named_models(payload)))
    if len(names) != 1:
        return [f"weight_owner_unknown:{file}:{key}" for key in keys]
    owned_file, owned_kind = measured.setdefault(names[0], ({}, {}))
    for target, found in ((owned_file, by_file), (owned_kind, by_kind)):
        for key, digests in found.items():
            target.setdefault(key, set()).update(digests)
    malformed.setdefault(names[0], set()).update(flags)
    declared.setdefault(names[0], set()).update(unowned_declared)
    return []


def weight_findings(models: dict, evidence: dict[str, object], new_files: frozenset[str] = frozenset()) -> list[str]:
    """كلُّ وزنٍ في السجلّ بهويّته كاملةً: ملفُّه وبصمتُه وأصلُه ورخصتُه بمصدرها وتاريخ قراءتها. وبصمتُه هي التي سجّلها لملفّه
    دليلٌ يسمّي نموذجَه، فلا تُلصق رخصةٌ ببايتاتٍ غيرِ التي قيست (ملاحظتا Codex على #307)."""
    files: dict[str, dict[str, set[str]]] = {}
    kinds: dict[str, dict[str, set[str]]] = {}
    problems = []
    for file, payload in sorted(evidence.items()):
        malformed: dict[str, set[tuple[str, str]]] = {}
        declared: dict[str, set[str]] = {}
        records: dict[str, set[tuple[str, ...]]] = {}
        texts: dict[str, set[tuple[str, ...]]] = {}
        measured = measured_weights(payload, records, texts, malformed=malformed, declared=declared)
        unowned, unowned_flags = measured.pop(UNOWNED[0], ({}, {})), malformed.pop(UNOWNED[0], set())
        unowned_declared = declared.pop(UNOWNED[0], set())
        if file in new_files:
            problems += _bind_unowned(file, payload, measured, malformed, unowned, unowned_flags, declared, unowned_declared)
        for name, (by_file, by_kind) in measured.items():
            for target, found in ((files, by_file), (kinds, by_kind)):
                for key, digests in found.items():
                    target.setdefault(name, {}).setdefault(key, set()).update(digests)
            if file in new_files and isinstance(models.get(name), dict):
                aliases = [raw for raw in all_named_models(payload) if canonical(raw) == name]
                problems += unregistered_weights(file, name, models[name], by_file, by_kind, aliases, declared.get(name, set()))
        if file in new_files:
            problems += unmeasured_weights(file, payload, models, measured)
            problems += conflicting_records(file, models, records, texts)
            problems += [f"{code}:{file}:{name}:{key}" for name, keys in sorted(malformed.items())
                         if isinstance(models.get(name), dict) for code, key in sorted(keys)]
    for name, entry in sorted(models.items()):
        weights = entry.get("weights", []) if isinstance(entry, dict) else []
        if not isinstance(weights, list) or not all(isinstance(weight, dict) for weight in weights):
            problems.append(f"weights_malformed:{name}")
            continue
        for weight in weights:
            label = f"{name}:{weight.get('file')}"
            missing = [field for field in WEIGHT_FIELDS if not isinstance(weight.get(field), str) or not weight[field]]
            problems += [f"weight_field_missing:{label}:{field}" for field in missing]
            # بصمةُ الوزن وبصمةُ نصّ رخصته بصمتان لا نصّان يُكتبان، فلا يطابق `"x"` في السجلّ `"x"` في الدليل (ملاحظة Codex على #307)
            malformed_digests = [field for field in WEIGHT_DIGESTS
                                 if field not in missing and not SHA256.match(weight[field])]
            problems += [f"weight_field_malformed:{label}:{field}" for field in malformed_digests]
            if weight.get("license") in NOTICE_LICENSES \
                    and not (isinstance(weight.get("attribution"), str) and weight["attribution"].strip()):
                problems.append(f"attribution_missing:{label}")
            if missing or malformed_digests:
                continue
            if weight["sha256"] not in _bound_digests(weights, weight["file"], files.get(name, {}), kinds.get(name, {})):
                problems.append(f"weight_digest_not_in_evidence:{label}")
            problems += [f"weight_source_not_https:{label}:{key}" for key in ("origin", "license_source")
                         if not HTTPS_SOURCE.match(weight[key])]
            if not _valid_day(weight["read_on"]):
                problems.append(f"weight_read_on_invalid:{label}")
    return problems


def provenance_findings(models: dict, evidence: dict[str, object]) -> list[str]:
    """كلُّ وزنٍ مسجَّل يطابقه في دليلٍ سجلُّ مصدرٍ لنموذجه بملفّه وبصمته وأصله ومصدر رخصته معًا: ما نزّلته
    `tools/weight_provenance.py` من الأصل المعلن فطابقت بصمتُه. فلا يمرّ أصلٌ أو مصدرُ رخصةٍ صحيحُ الصيغة لا علاقة له
    بالبايتات المقيسة (ملاحظة Codex على #307)."""
    provenance: dict[str, set[tuple[str, ...]]] = {}
    licenses: dict[str, set[tuple[str, ...]]] = {}
    for payload in evidence.values():
        measured_weights(payload, provenance, licenses)
    problems = []
    for name, entry in sorted(models.items()):
        # بصمةُ نصّ الرخصة المقيَّدة للنموذج نصٌّ قيس من مصدره المقيَّد، لا 64 محرفًا تُكتب (ملاحظة Codex على #307)
        if isinstance(entry, dict) and "license_text_sha256" in entry \
                and tuple(entry.get(f) for f in LICENSE_PROVENANCE_FIELDS) not in licenses.get(name, set()):
            problems.append(f"license_text_not_in_evidence:{name}")
        weights = entry.get("weights") if isinstance(entry, dict) else None
        for weight in weights if isinstance(weights, list) else []:
            if isinstance(weight, dict) and all(isinstance(weight.get(f), str) for f in PROVENANCE_FIELDS) \
                    and not _provenance_recorded(weight, provenance.get(name, set())):
                problems.append(f"weight_provenance_not_in_evidence:{name}:{weight['file']}")
    return problems


def _provenance_recorded(weight: dict, records: set[tuple[str, ...]]) -> bool:
    """سجلٌّ بملفّ الوزن وبصمته وأصله ومصدر رخصته ورخصته المعلنة وتاريخ قراءتها وبصمة نصّ رخصته وإسناده معًا. فالإسنادُ
    المنشور في THIRD-PARTY.md سطرٌ قيس في نصّ الرخصة، لا نصٌّ يُكتب (ملاحظة Codex على #307)."""
    return _record_of(weight) in records


def _record_of(weight: dict) -> tuple:
    """قيدُ الوزن بصورة سجلّ المصدر كما يجمعه `measured_weights`."""
    return (*(weight[f] for f in PROVENANCE_FIELDS), weight.get("license_text_sha256"), weight.get("attribution"))


def _undated(record: tuple, read_on: int) -> tuple:
    return record[:read_on] + record[read_on + 1:]


def conflicting_records(file: str, models: dict, records: dict[str, set[tuple]], texts: dict[str, set[tuple]]) -> list[str]:
    """دليلٌ جديد يعلن لوزنٍ مقيَّد (بملفّه وبصمته) سجلَّ مصدرٍ يخالف قيدَه، أو لنموذجٍ مقيَّدٍ نصُّ رخصته سجلَّ نصٍّ يخالفه، يُسمّى؛
    فلا يُنشر بجانب السجلّ الصحيح أصلٌ أو مصدرُ رخصةٍ أو رخصةٌ أو نصٌّ أو إسنادٌ كاذبٌ على البايتات نفسها (ملاحظة Codex على
    #307). وتاريخُ القراءة لا يُقارن، فقراءتان مؤرَّختان لشيءٍ واحد قياسان صحيحان. وسجلُّ المصدر الذي لا مالكَ له على طريقه
    يُقارن بكلّ وزنٍ مقيَّدٍ بملفّه وبصمته."""
    problems = set()
    for owner, found in records.items():
        for name in [owner] if owner else list(models):
            entry = models.get(name)
            weights = entry.get("weights") if isinstance(entry, dict) else None
            for weight in weights if isinstance(weights, list) else []:
                if not isinstance(weight, dict) or not all(isinstance(weight.get(f), str) for f in PROVENANCE_FIELDS):
                    continue
                expected = _undated(_record_of(weight), PROVENANCE_FIELDS.index("read_on"))
                if any(record[:2] == expected[:2] and _undated(record, PROVENANCE_FIELDS.index("read_on")) != expected
                       for record in found):
                    problems.add(f"weight_provenance_conflicts:{file}:{name}:{weight['file']}")
    for name, found in texts.items():
        entry = models.get(name)
        if isinstance(entry, dict) and "license_text_sha256" in entry:
            expected = _undated(tuple(entry.get(f) for f in LICENSE_PROVENANCE_FIELDS), LICENSE_PROVENANCE_FIELDS.index("read_on"))
            if any(_undated(record, LICENSE_PROVENANCE_FIELDS.index("read_on")) != expected for record in found):
                problems.add(f"license_provenance_conflicts:{file}:{name}")
    return sorted(problems)


def default_engine(source: Path = DEFAULT_ENGINE_SOURCE) -> str | None:
    match = DEFAULT_MODEL.search(source.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def findings(registry: dict, evidence: dict[str, object], engine: str | None) -> list[str]:
    """كلُّ مخالفةٍ باسمها، مرتّبة. `evidence` اسمُ الملفّ وقيمتُه المقروءة."""
    problems: list[str] = []
    models = registry.get("models")
    enforced_from = registry.get("enforced_from")
    historical = registry.get("historical_evidence")
    if not isinstance(models, dict) or not _valid_day(enforced_from) or not isinstance(historical, dict) \
            or not all(isinstance(digest, str) and SHA256.match(digest) for digest in historical.values()):
        return ["registry_malformed"]
    frozen = frozen_evidence(historical, evidence)
    for name, entry in models.items():
        problems += entry_findings(name, entry)
    for file, payload in sorted(evidence.items()):
        if isinstance(payload, dict):
            problems += evidence_findings(file, payload, models, enforced_from, frozen)
    dicts = {file: payload for file, payload in evidence.items() if isinstance(payload, dict)}
    new_files = frozenset(file for file, payload in dicts.items() if file not in frozen
                          or (isinstance(payload.get("date"), str) and payload["date"][:10] >= enforced_from))
    problems += weight_findings(models, dicts, new_files)
    problems += provenance_findings(models, dicts)
    if engine is not None:
        entry = models.get(canonical(engine))
        if entry is None:
            problems.append(f"default_engine_not_in_registry:{engine}")
        elif isinstance(entry, dict):
            if "license" in entry and license_class(entry["license"]) == "non_commercial":
                problems.append(f"default_engine_non_commercial:{engine}")
            # وزنٌ مسجَّلٌ للمحرّك برخصةٍ غير تجاريّة داخلُ المنتج أيضًا، ولو كانت رخصةُ غلافه مفتوحة (ملاحظة Codex على #307)
            weights = entry.get("weights") if isinstance(entry.get("weights"), list) else []
            problems += [f"default_engine_weight_non_commercial:{engine}:{w.get('file')}" for w in weights
                         if isinstance(w, dict) and isinstance(w.get("license"), str)
                         and license_class(w["license"]) == "non_commercial"]
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
