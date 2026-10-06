#!/usr/bin/env python3
"""استلامُ تسليم Kimi قبل توزيعه (ك٦): كلُّ ما ألّفه يُفحص بالمدقّقات الحقيقية، ولا يُطبع منه محتوى.

    python3 tools/kimi_intake.py ~/kimi-work/kimi-benchmark --out docs/probe/k6-intake-<التاريخ>.json

والمهامُّ الوكيلة تُحكم في حاويةٍ زائلة، لأن أوامرَ نجاحها ألّفها نموذجٌ خارجيّ (ج٣، ق٤٤):

    docker run --rm --network none -e DIWAN_DISPOSABLE_HOST=intake \\
      -v ~/kimi-work/kimi-benchmark:/src:ro -v "$PWD":/workspace:ro -w /workspace <image> \\
      python tools/kimi_intake.py /src --agentic --out -

- **البنية والبيان:** المجلّداتُ المطلوبة. وكلُّ ملفٍّ محجوب مذكورٌ في البيان المختوم ببصمته وعدده،
  ولا محجوبَ خارجه.
- **المدقّقات:** كلُّ ملفّ قدراتٍ بـ`validate_suite`، وكلُّ حزمةٍ وكيلة بـ`validate_agentic_bank`،
  مفتوحةً ومحجوبة.
- **شروطُ v1.2** (`docs/external/KIMI-NEXT.md`):
  - لا حالةَ بلا فحص.
  - ومعرّفاتُ المهامّ فريدةٌ في البنك كلِّه.
  - وبنكُ العربية العامة للتطوير بقدراته التسع، وكلُّ مهمّةٍ في البنك الوكيل للتطوير لها حلٌّ مرجعيّ
    وملفّاتُ حكمٍ محظورة.
- **`--agentic`:**
  - كلُّ مهمّةٍ تسقط قبل الحلّ.
  - ومهامُّ التطوير تمرّ بحلّها المرجعيّ ولا تمسّ ملفّاتِ الحكم.
  - والحلُّ الخاطئ، إن وُجد، يسقط.
- **ما يُطبع:** أعدادٌ، ورموزُ رفضٍ بمسار الملف. لا نصَّ حالةٍ ولا معرّفَ حالةٍ محجوبة.
  - ويخرج بـ1 إن بقي عيب.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.canonical import PayloadRejected  # noqa: E402
from evaluation.agentic_runner import (evaluate_success, harness_tampering, materialize,  # noqa: E402
                                      validate_agentic_bank, workspace_bytes)
from evaluation.capabilities import validate_suite  # noqa: E402

REQUIRED = ("open", "sealed/MANIFEST.json", "REPORT.md", "disputed.json")
# دورةُ الشطر المفتوح (قرار المالك، ٢٦ سبتمبر): لا يرى Kimi المحجوب، فلا بيانَ ولا sealed/ في تسليمه
REQUIRED_OPEN_ONLY = ("open", "REPORT.md", "disputed.json")
CURRENT_OPEN = ROOT / "evaluation" / "banks" / "kimi_v1" / "open"
# بنكُ العربية العامة ملفّان، لأن المدقّقَ لا يقبل فوق مئة حالةٍ في الملف (`validate_suite`)
DEV_GENERAL = ("arabic_general_v3_1.json", "arabic_general_v3_2.json")
DEV_AGENTIC, DEV_AGENTIC_META = "agentic_v3.json", "agentic_v3.meta.json"
GENERAL_CAPABILITIES = {
    "dialogue_and_instructions", "negation_conditions_exceptions", "arithmetic_and_reasoning",
    "arabic_editing_and_writing", "translation_fidelity", "programming",
    "evidence_honesty_and_quoted_instructions", "arabic_lexicon_and_semantics", "arabic_morphology_and_grammar",
}
GENERAL_MIN_CASES = 150
AGENTIC_MIN_TASKS = 30
LIMITS = [
    "validators_check_schema_and_the_v1_2_conditions_not_whether_a_reference_answer_is_correct",
    "gameable_checks_are_found_by_five_fixed_answers_empty_echo_polarity_spray_both_and_each_contains_value_negated_so_a_subtler_gameable_check_passes",
    "without_sandbox_probes_a_case_whose_other_checks_a_fixed_answer_passes_is_counted_needs_sandbox_not_judged",
    "agentic_checks_run_only_in_a_disposable_container_otherwise_they_are_reported_unjudged",
    "decoys_are_written_by_the_bank_author_so_a_check_is_proven_only_against_the_wrong_answers_written_for_it_and_a_reviewer_samples_whether_they_are_real",
    "tool_decoys_are_seven_fixed_templates_built_from_the_reference_and_question_so_a_check_tuned_to_them_or_a_wrong_answer_of_another_shape_still_passes",
]


def _failure(items: list, path: str, code: str) -> None:
    items.append({"file": path, "code": code})


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None


def check_structure(src: Path, *, open_only: bool = False) -> dict:
    missing = [p for p in (REQUIRED_OPEN_ONLY if open_only else REQUIRED) if not (src / p).exists()]
    if open_only and (src / "sealed").exists():
        missing.append("sealed/ ممنوعٌ في دورة الشطر المفتوح")
    return {"missing": missing, "dev_files": sorted(p for p in (*DEV_GENERAL, DEV_AGENTIC, DEV_AGENTIC_META)
                                                    if (src / p).exists())}


def check_manifest(src: Path) -> dict:
    """البيانُ بشكليه (قائمةٌ فيها path، أو قاموسٌ مفتاحُه المسار) كما يقبله أمرُ التوزيع."""
    failures: list = []
    manifest = _json(src / "sealed" / "MANIFEST.json")
    if not isinstance(manifest, dict) or "files" not in manifest:
        _failure(failures, "sealed/MANIFEST.json", "manifest_unreadable")
        return {"files": 0, "failures": failures}
    files = manifest["files"]
    entries = list(files.items()) if isinstance(files, dict) else [(e.get("path"), e) for e in files]
    for path, meta in entries:
        target = src / str(path)
        if not target.is_file():
            _failure(failures, str(path), "sealed_file_missing")
            continue
        raw = target.read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta.get("sha256"):
            _failure(failures, str(path), "sealed_digest_mismatch")
        declared = meta.get("count", meta.get("cases"))
        if declared is not None and meta.get("kind") not in ("sidecar", "meta"):
            data = _json(target) or {}
            if len(data.get("cases") or data.get("tasks") or []) != declared:
                _failure(failures, str(path), "sealed_count_mismatch")
    listed = {str(path) for path, _ in entries}
    for extra in sorted(src.glob("sealed/**/*.json")):
        relative = extra.relative_to(src).as_posix()
        if extra.name != "MANIFEST.json" and relative not in listed:
            _failure(failures, relative, "sealed_file_unlisted")
    return {"files": len(entries), "failures": failures}


def _open_inventory(root: Path) -> dict[str, dict | None]:
    """لكلِّ ملفٍّ معرّفاتُ حالاته أو مهامّه؛ وللملفّ الجانبيّ (`.meta.json`) مدخلاتُه كاملةً، فمصادرُه وحلولُه تُمحى معه.

    وملفٌّ جانبيٌّ بلا `cases` ولا `tasks` قاموسًا لا يُقرأ: قيمتُه None.
    """
    out: dict[str, dict | None] = {}
    for path in sorted(root.rglob("*.json")):
        data = _json(path)
        relative = path.relative_to(root).as_posix()
        if path.name.endswith(".meta.json"):
            entries = None
            if isinstance(data, dict):
                entries = next((data[k] for k in ("cases", "tasks") if isinstance(data.get(k), dict)), None)
            out[relative] = None if entries is None else {"ids": set(entries), "entries": entries}
            continue
        items = (data.get("cases") or data.get("tasks") or []) if isinstance(data, dict) else []
        out[relative] = {"ids": {item.get("case_id") or item.get("task_id") for item in items if isinstance(item, dict)}}
    return out


def _empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _filled(entry) -> set:
    return {key for key, value in entry.items() if not _empty(value)} if isinstance(entry, dict) else set()


def check_open_replacement(src: Path, current: Path) -> dict:
    """دورةُ المفتوح تستبدل المفتوحَ القائم (`UPDATE=1 OPEN_ONLY=1 place`)، فلا تمرّ إلا بكلِّ ملفٍّ وكلِّ حالةٍ فيه.

    كان تسليمٌ فارغٌ يمرّ: البيانُ يُتخطّى، ولا ملفَّ يسقط في المدقّقات، فيمحو التوزيعُ البنك (ملاحظة Codex على #128).
    - **الزيادة مقبولة:** ملفٌّ جديد، أو حالةٌ جديدة.
    - **المعرّفُ يبقى:** إلا المكرّرَ بين ملفّين. تكليفُ v1.2 يطلب إعادةَ تسميته، فغيابُه يغطّيه معرّفٌ جديدٌ في الملف نفسِه.
    - **الملفُّ الجانبيّ:** كلُّ مدخلٍ يُبقي كلَّ حقلٍ كان فيه غيرَ فارغ. والمدخلُ المُعادُ تسميتُه يحمل ما تشترك فيه المدخلاتُ التي حلّ محلَّها.
    """
    from evaluation.judge import open_bank_digest
    failures: list = []
    expected = _open_inventory(current) if current.is_dir() else {}
    if not expected:
        _failure(failures, "open", "current_open_bank_missing")
        return {"files": 0, "baseline_digest": None, "failures": failures}
    # بصمةُ ما استُبدل: فحصٌ على بنكٍ قائمٍ من حالةٍ واحدة (`--current`) ينجح بلا إخفاق، فلا يشهد باستبدال v1.1 إلا بها
    # (ملاحظة Codex على #312)
    baseline = open_bank_digest(current)
    seen: dict = {}
    for relative, before in expected.items():
        if before is not None and "entries" not in before:
            for identifier in before["ids"]:
                seen[identifier] = seen.get(identifier, 0) + 1
    duplicated = {identifier for identifier, n in seen.items() if n > 1}
    delivered = _open_inventory(src / "open")
    for relative, before in expected.items():
        where = f"open/{relative}"
        if relative not in delivered:
            _failure(failures, where, "open_file_missing")
            continue
        after = delivered[relative]
        if after is None:
            _failure(failures, where, "sidecar_unreadable")
            continue
        if before is None:
            continue
        missing = before["ids"] - after["ids"]
        renamed = missing & duplicated
        added = after["ids"] - before["ids"]
        if missing - duplicated or len(added) < len(renamed):
            _failure(failures, where, "open_case_missing")
        if "entries" not in before:
            continue
        kept = [(before["entries"][i], after["entries"][i]) for i in before["ids"] & after["ids"]]
        replaced = [_filled(before["entries"][i]) for i in renamed]
        shared = set.intersection(*replaced) if replaced else set()
        kept_short = any(not _filled(new) >= _filled(old) for old, new in kept)
        added_short = replaced and any(not _filled(after["entries"][i]) >= shared for i in added)
        if kept_short or added_short:
            _failure(failures, where, "sidecar_entry_incomplete")
    return {"files": len(expected), "baseline_digest": baseline, "failures": failures}


def _bank_files(src: Path) -> list[tuple[str, Path]]:
    out = []
    for part in ("open", "sealed"):
        for path in sorted((src / part).rglob("*.json")):
            if not path.name.endswith(".meta.json") and path.name != "MANIFEST.json":
                out.append((part, path))
    return out


# أجوبةٌ ثابتة لا تعرف الجواب (شروطُ v1.2 في docs/external/KIMI-NEXT.md §١): فارغ، ونسخُ السؤال (فيه خياراتُه كلُّها
# ونصُّه الأصل)، وسردُ الحكمين معًا، والاثنان معًا، ونفيُ قيمة كلِّ contains. فحصٌ يمرّره أحدُها لا يميّز الجوابَ الصحيح من لا جواب.
POLARITY_SPRAY = "يوجد خطأ ولا يوجد خطأ. صحيح وخاطئ وغير صحيح. نعم ولا. يجوز ولا يجوز. يصح ولا يصح."
# صيغُ النفي في docs/external/KIMI-NEXT.md §١ البند ٣، وكلٌّ منها جوابٌ ثابت لكل قيمة contains
NEGATION_FORMS = ("لا يوجد {}.", "ليس فيه {}.", "لا {}.")
# الأجوبةُ الثابتة لا ترى فحصًا يمرّره نفيُ التصحيح أو جوابٌ خاطئٌ يحمل كلماتِ المرجع: مرّ ذلك في ٦ من ٦ حالاتِ مقدّماتٍ
# كاذبة فحصها Codex بعد استلامٍ ناجح (#329). فلكلّ حالةٍ من هذه القدرات شِراكٌ يكتبها المؤلّفُ في ملفّها الجانبيّ
# (`decoys`)، ويجب أن يُسقطها الفحصُ كلَّها وأن يمرّ المرجع.
DECOYS_REQUIRED = {"false_premise_rejection": 2}
# وشِراكُ المؤلّف لا تكفي: فحوصُ الجولة الثالثة أسقطت الشِّراكَ الثمانيةَ والستين كلَّها، ومرّ بها نفيُ التصحيح والجوابُ الخاطئ
# بكلمات المرجع في ٤ من ٦ (مراجعة Codex على #339). فيبني الاستلامُ لكلّ حالةٍ منها شِراكًا من مرجعها وسؤالها لا يكتبها المؤلّف:
# نفيُ كلّ جملةٍ في المرجع بعد إنكار الخطأ، ثم بعد إقراره، وإقرارُ المقدّمة بكلمات المرجع مفرَّقةً.
TOOL_NEGATIONS = ("ليس صحيحًا أن", "لم يثبت أن", "يُشكّ في أن")
_CORRECTION_LEAD = re.compile(r"^[^:\n]{0,40}:\s*")
_CLAUSE_SPLIT = re.compile(r"\s*[.؛;!?؟،,\n]+\s*")
_STOPWORDS = {"لا", "ليس", "ليست", "لم", "لن", "غير", "ما", "في", "من", "على", "عن", "إلى", "الى", "أن", "إن", "أو",
              "ثم", "قد", "هو", "هي", "هذا", "هذه", "ذلك", "التي", "الذي", "بل", "لكن", "وليس", "ولا", "ولم"}


def _premise_probes(case: dict) -> tuple[tuple[str, str], ...]:
    """شِراكُ الاستلام لحالة مقدّمةٍ كاذبة، كلٌّ جوابٌ خاطئ: لا يقرّ الحقيقةَ المصحَّحة، أو يقرّ المقدّمةَ الكاذبة."""
    reference = case.get("reference")
    question = " ".join(m.get("content", "") for m in case.get("messages", [])
                        if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str))
    if not isinstance(reference, str) or not reference.strip():
        return ()
    core = _CORRECTION_LEAD.sub("", reference.strip(), count=1)
    clauses = [c for c in _CLAUSE_SPLIT.split(core) if c]
    probes = []
    for index, negation in enumerate(TOOL_NEGATIONS, 1):
        negated = " ".join(f"{negation} {clause}." for clause in clauses)
        probes.append((f"denies_error_negates_reference_{index}", f"المقدمة ليست خاطئة، وما ذكرتَه صحيح. {negated}"))
        probes.append((f"admits_error_negates_reference_{index}", f"المقدمة خاطئة: {negated}"))
    words = [w for w in re.findall(r"[\u0621-\u064A\u064B-\u0652\u0670-\u06D3A-Za-z0-9]+", core)
             if len(w) > 2 and w not in _STOPWORDS]
    premise = question.strip().rstrip("؟?").strip()
    probes.append(("affirms_premise_with_reference_words",
                   f"نعم، كما ذكرتَ تمامًا، ولا خطأ في السؤال: {premise}. ويتصل بذلك: {'، '.join(words)}."))
    return tuple(probes)


def _probe_answers(case: dict) -> tuple[tuple[str, str], ...]:
    question = "\n".join(m.get("content", "") for m in case.get("messages", [])
                         if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str))
    # نفيُ قيمة الفحص نفسِها بصيغ النفي في KIMI-NEXT §١ البند ٣: «لا يوجد خلل» يمرّ `contains: خلل` وإن لم تكن
    # الكلمةُ في POLARITY_SPRAY (ملاحظة Codex على #312، #314)
    values = [check["value"] for check in case.get("checks") or []
              if check.get("kind") == "contains" and isinstance(check.get("value"), str)]
    # كلُّ صيغةٍ جوابٌ مستقلّ: فحصٌ يستبعد «لا يوجد» وحدها يمرّه «ليس فيه X» (ملاحظة Codex على #315)؛ وكلُّ قيمةٍ تُنفى
    # وحدها والباقي مُثبَت: «ألف. لا يوجد باء.» يمرّ وإن حُمي نفيُ «ألف» (#316)
    negated = tuple((f"negated_value_{index}",
                     "\n".join([*(f"{other}." for other in values if other != value), form.format(value)]))
                    for index, form in enumerate(NEGATION_FORMS, 1) for value in dict.fromkeys(values))
    return (("empty", ""), ("echo", question), ("polarity_spray", POLARITY_SPRAY),
            ("echo_and_spray", question + "\n" + POLARITY_SPRAY)) + negated


def gameable_probe(case: dict, *, sandbox: bool = False) -> str | None:
    """اسمُ أوّل جوابٍ ثابتٍ تمرّره فحوصُ الحالة كلُّها، أو `needs_sandbox`، أو None.

    الفحوصُ خارج الحاوية تُجرَّب أولًا؛ فجوابٌ تُسقطه لا يُشغَّل له شيء. وما مرّ بها في حالةٍ فيها `python_sandbox`
    لا يحكم فيه إلا الحاوية: تُشغَّل بـ`sandbox=True`، وإلا فالحالةُ `needs_sandbox` لا «سليمة» (ملاحظة Codex على #312).
    """
    from evaluation.capabilities import _checks
    checks = case.get("checks") or []
    if not checks:
        return None
    plain = [check for check in checks if check.get("kind") != "python_sandbox"]
    boxed = len(plain) != len(checks)
    for name, answer in _probe_answers(case):
        if not all(result["passed"] for result in _checks(answer, plain)):
            continue
        if not boxed:
            return name
        if not sandbox:
            return "needs_sandbox"
        if all(result["passed"] for result in _checks(answer, checks)):
            return name
    return None


def _passes(answer: str, checks: list, *, sandbox: bool) -> bool | None:
    """هل يمرّ الجوابُ فحوصَ الحالة كلَّها؛ وNone إن لم يحكم فيه إلا الحاوية وهي غائبة."""
    from evaluation.capabilities import _checks
    plain = [check for check in checks if check.get("kind") != "python_sandbox"]
    if not all(result["passed"] for result in _checks(answer, plain)):
        return False
    if len(plain) == len(checks):
        return True
    if not sandbox:
        return None
    return all(result["passed"] for result in _checks(answer, checks))


def check_decoys(case: dict, entry, tally: dict, failures: list, where: str, *, sandbox: bool = False) -> None:
    """شِراكُ الحالة في ملفّها الجانبيّ: كلُّها تسقط، والمرجعُ يمرّ؛ والقدرةُ في DECOYS_REQUIRED لا تُقبل بأقلّ من عددها."""
    raw = entry.get("decoys") if isinstance(entry, dict) else None
    decoys = [d for d in raw if isinstance(d, str) and d.strip()] if isinstance(raw, list) else []
    need = DECOYS_REQUIRED.get(case.get("capability"), 0)
    if need:
        tally["required"] += 1
        if len(decoys) < need:
            tally["missing"] += 1
            _failure(failures, where, "decoys_missing")
    if not decoys:
        return
    checks = case.get("checks") or []
    reference = case.get("reference")
    reference = reference if isinstance(reference, str) else json.dumps(reference, ensure_ascii=False)
    verdict = _passes(reference, checks, sandbox=sandbox)
    if verdict is None:
        tally["unjudged"] += 1
    elif not verdict:
        tally["reference_fails"] += 1
        _failure(failures, where, "reference_fails_checks")
    for decoy in decoys:
        tally["decoys"] += 1
        verdict = _passes(decoy, checks, sandbox=sandbox)
        if verdict is None:
            tally["unjudged"] += 1
        elif verdict:
            tally["passes"] += 1
            _failure(failures, where, "decoy_passes_checks")


def check_tool_decoys(case: dict, tally: dict, failures: list, where: str, *, sandbox: bool = False) -> None:
    """شِراكُ الاستلام (`_premise_probes`) لكلّ حالةٍ في DECOYS_REQUIRED: كلُّها تسقط، ولا يرى المؤلّفُ قوالبَها."""
    if not DECOYS_REQUIRED.get(case.get("capability")):
        return
    checks = case.get("checks") or []
    for _name, answer in _premise_probes(case):
        tally["tool_decoys"] += 1
        verdict = _passes(answer, checks, sandbox=sandbox)
        if verdict is None:
            tally["unjudged"] += 1
        elif verdict:
            tally["tool_passes"] += 1
            _failure(failures, where, "tool_decoy_passes_checks")


def gameable_cases(open_dir: Path) -> list[dict]:
    """كلُّ حالةٍ في شطرٍ مفتوح يمرّرها جوابٌ ثابت، أو لا يحكم فيها إلا الحاوية؛ بملفّها والجواب الذي مرّرها.

    قائمةٌ لـKimi في `current/GAMEABLE.json` (`tools/kimi_drive.sh gameable`): يعرف بها ما يردّه الاستلام قبل أن يسلّم،
    ولا يرى شيفرة المسبار. والحاويةُ لا تُشغَّل هنا، فحالاتُها `needs_sandbox`.
    """
    rows = []
    for path, suite in _capability_files(open_dir):
        for case in suite.get("cases") or []:
            probe = gameable_probe(case) if isinstance(case, dict) else None
            if probe:
                rows.append({"file": path.relative_to(open_dir).as_posix(), "case_id": case.get("case_id"),
                             "capability": case.get("capability"), "probe": probe})
    return rows


def _capability_files(open_dir: Path):
    for path in sorted(open_dir.rglob("*.json")):
        if path.name.endswith(".meta.json") or path.name == "MANIFEST.json":
            continue
        suite = _json(path)
        if isinstance(suite, dict) and suite.get("kind") != "agentic_tasks":
            yield path, suite


def check_bank(src: Path, *, sandbox_probes: bool = False) -> dict:
    """المدقّقاتُ الحقيقية على الشطرين، وشروطُ v1.2. والمحجوبُ أعدادٌ ورموزٌ بلا معرّفات."""
    failures: list = []
    counts = {"open": {"files": 0, "cases": 0, "tasks": 0}, "sealed": {"files": 0, "cases": 0, "tasks": 0}}
    without_checks = {"open": 0, "sealed": 0}
    # الخلفيّةُ التي حكمت في حالات الحاوية بإيصالها، فيُربط بها القياسُ اللاحق (ملاحظة Codex على #312)
    from core.sandbox import sandbox_configuration
    gameable = {"open": 0, "by_probe": {}, "needs_sandbox": 0, "sandbox_probed": sandbox_probes,
                "sandbox_backend": sandbox_configuration() if sandbox_probes else None}
    decoys = {"required": 0, "missing": 0, "decoys": 0, "passes": 0, "reference_fails": 0, "unjudged": 0,
              "tool_decoys": 0, "tool_passes": 0}
    agentic, case_ids = [], {}
    for part, path in _bank_files(src):
        relative = path.relative_to(src).as_posix()
        suite = _json(path)
        counts[part]["files"] += 1
        if not isinstance(suite, dict):
            _failure(failures, relative, "json_unreadable")
            continue
        try:
            if suite.get("kind") == "agentic_tasks":
                agentic.append((part, relative, suite))
                counts[part]["tasks"] += len(suite.get("tasks") or [])
                continue
            validate_suite(suite)
        except PayloadRejected as exc:
            _failure(failures, relative, exc.code)
            continue
        counts[part]["cases"] += len(suite["cases"])
        without_checks[part] += sum(not case["checks"] for case in suite["cases"])
        sidecar = _json(path.with_name(path.name[:-len(".json")] + ".meta.json")) if part == "open" else None
        entries = sidecar.get("cases") if isinstance(sidecar, dict) and isinstance(sidecar.get("cases"), dict) else {}
        for case in suite["cases"]:
            if part == "open":
                try:
                    check_decoys(case, entries.get(case["case_id"]), decoys, failures, relative, sandbox=sandbox_probes)
                    check_tool_decoys(case, decoys, failures, relative, sandbox=sandbox_probes)
                except PayloadRejected as exc:
                    _failure(failures, relative, f"decoy_probe_{exc.code}")
            case_ids[case["case_id"]] = case_ids.get(case["case_id"], 0) + 1
            try:
                probe = gameable_probe(case, sandbox=sandbox_probes) if part == "open" else None
            except PayloadRejected as exc:
                _failure(failures, relative, f"gameable_probe_{exc.code}")
                continue
            if probe == "needs_sandbox":
                gameable["needs_sandbox"] += 1
            elif probe:
                gameable["open"] += 1
                gameable["by_probe"][probe] = gameable["by_probe"].get(probe, 0) + 1
    try:
        qualified = validate_agentic_bank([suite for _, _, suite in agentic])
    except PayloadRejected as exc:
        _failure(failures, "agentic", exc.code)
        qualified = []
    task_ids = [q.split("/", 1)[1] for q in qualified]
    for part in ("open", "sealed"):
        if without_checks[part]:
            _failure(failures, part, "cases_without_checks")
    # فحصٌ يمرّره جوابٌ ثابت قابلٌ للتلاعب، والرقمُ العام لا يُبنى عليه (ملاحظة Codex على #312)
    if gameable["open"]:
        _failure(failures, "open", "gameable_checks")
    # شَرَكٌ أو مرجعٌ لم يحكم فيه إلا الحاوية: الاستلامُ بـ--sandbox-probes وحده يشهد له
    if decoys["unjudged"]:
        _failure(failures, "open", "decoys_need_sandbox")
    if len(set(task_ids)) != len(task_ids):
        _failure(failures, "agentic", "task_id_not_unique_across_bank")
    if any(n > 1 for n in case_ids.values()):
        _failure(failures, "bank", "case_id_not_unique_across_bank")
    # بصمةُ الشطر المفتوح كلِّه كما يقرؤها المحكِّم ومُشغِّلُ الرقم العام، فيُربط القياسُ بهذا الاستلام بعينه (ملاحظة Codex على #312)
    from evaluation.judge import open_bank_digest
    open_digest = open_bank_digest(src / "open") if (src / "open").is_dir() else None
    return {"counts": counts, "without_checks": without_checks, "gameable": gameable, "decoys": decoys,
            "open_digest": open_digest, "failures": failures}


def check_dev(src: Path) -> dict:
    failures: list = []
    report: dict = {}
    present = [name for name in DEV_GENERAL if (src / name).exists()]
    if present:
        cases, valid = [], True
        for name in present:
            suite = _json(src / name)
            try:
                validate_suite(suite if isinstance(suite, dict) else {})
            except PayloadRejected as exc:
                _failure(failures, name, exc.code)
                valid = False
                continue
            cases.extend(suite["cases"])
        if len(present) != len(DEV_GENERAL):
            _failure(failures, "arabic_general_v3", "general_file_missing")
        capabilities = {case["capability"] for case in cases}
        report["general"] = {"files": len(present), "cases": len(cases),
                             "without_checks": sum(not c["checks"] for c in cases),
                             "capabilities_missing": sorted(GENERAL_CAPABILITIES - capabilities)}
        if len({c["case_id"] for c in cases}) != len(cases):
            _failure(failures, "arabic_general_v3", "case_id_not_unique_across_files")
        if report["general"]["without_checks"]:
            _failure(failures, "arabic_general_v3", "cases_without_checks")
        if valid and report["general"]["capabilities_missing"]:
            _failure(failures, "arabic_general_v3", "capabilities_missing")
        if valid and len(cases) < GENERAL_MIN_CASES:
            _failure(failures, "arabic_general_v3", "too_few_cases")
    agentic = _json(src / DEV_AGENTIC) if (src / DEV_AGENTIC).exists() else None
    if agentic is not None:
        meta = _json(src / DEV_AGENTIC_META) or {}
        try:
            validate_agentic_bank([agentic])
            tasks = agentic["tasks"]
            solutions = (meta.get("tasks") or {}) if isinstance(meta, dict) else {}
            report["agentic"] = {"tasks": len(tasks),
                                 "without_reference": sum("reference_solution" not in (solutions.get(t["task_id"]) or {})
                                                          for t in tasks),
                                 "without_forbidden": sum(not t["forbidden"] for t in tasks)}
            if report["agentic"]["without_reference"]:
                _failure(failures, DEV_AGENTIC_META, "reference_solution_missing")
            if report["agentic"]["without_forbidden"]:
                _failure(failures, DEV_AGENTIC, "forbidden_missing")
            if len(tasks) < AGENTIC_MIN_TASKS:
                _failure(failures, DEV_AGENTIC, "too_few_tasks")
        except PayloadRejected as exc:
            _failure(failures, DEV_AGENTIC, exc.code)
    return {**report, "failures": failures}


def _is_file_map(value) -> bool:
    """الحلُّ المرجعيّ (أو الشرَك) خريطةُ «مسارٍ ← محتواه الكامل» بعقد المساحة نفسِه (`workspace_bytes`): نصٌّ،
    أو {"base64": …} لملفٍّ ثنائيّ كـxlsx (#191). فالنثرُ لا يُطبَّق آليًّا.

    و`null` حذفُ الملف: مهمّةٌ تطلب حذفَ ملفٍّ لم يكن لحلّها المرجعيّ طريقٌ إليه، فأُرخي معيارُها ليقبل بقاءه
    (`agentic_0039`، مراجعة Codex على #329). ومسارُ الحذف نسبيٌّ لا يصعد."""
    if not isinstance(value, dict) or not all(isinstance(name, str) for name in value):
        return False
    try:
        for name, content in value.items():
            if content is None:
                if not _relative_inside(name):
                    return False
                continue
            workspace_bytes(content)
    except PayloadRejected:
        return False
    return True


def _relative_inside(name: str) -> bool:
    pure = PurePosixPath(name)
    return bool(name) and not pure.is_absolute() and ".." not in pure.parts and name == pure.as_posix()


def _apply_overlay(root: Path, overlay: dict | None) -> None:
    for name, content in (overlay or {}).items():
        target = root / name
        if content is None:
            if not _relative_inside(name):
                raise PayloadRejected(name, "overlay_delete_outside_workspace", "مسارُ الحذف خارج المساحة")
            if target.is_file() or target.is_symlink():
                target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(workspace_bytes(content))


def _judge(task: dict, overlay: dict | None) -> tuple[dict, list[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        materialize(task, root)
        _apply_overlay(root, overlay)
        return evaluate_success(task, root), harness_tampering(task, root)


def check_agentic(src: Path, *, judge=_judge) -> dict:
    """في الحاوية: كلُّ مهمّةٍ تسقط قبل الحلّ، ومهامُّ التطوير تمرّ بحلّها المرجعيّ."""
    failures: list = []
    counts = {"tasks": 0, "fail_before_fix": 0, "pass_before_fix": 0, "unjudged": 0,
              "reference_passes": 0, "reference_fails": 0, "reference_not_a_file_map": 0, "decoy_passes": 0}
    # حلولُ البنك المرجعية في الملف الجانبيّ المجاور (`kimi_agentic_001.meta.json`)، فتُحكم كحلول التطوير
    suites = []
    for _, path in _bank_files(src):
        sidecar = _json(path.with_name(path.name[:-len(".json")] + ".meta.json"))
        tasks = sidecar.get("tasks") if isinstance(sidecar, dict) else None
        suites.append((path.relative_to(src).as_posix(), _json(path), tasks if isinstance(tasks, dict) else None))
    if (src / DEV_AGENTIC).exists():
        meta = _json(src / DEV_AGENTIC_META) or {}
        suites.append((DEV_AGENTIC, _json(src / DEV_AGENTIC), meta.get("tasks") or {}))
    for relative, suite, solutions in suites:
        if not isinstance(suite, dict) or suite.get("kind") != "agentic_tasks":
            continue
        for task in suite["tasks"]:
            counts["tasks"] += 1
            verdict, _ = judge(task, None)
            if verdict["passed"]:
                counts["pass_before_fix"] += 1
                _failure(failures, relative, "passes_before_fix")
            elif verdict.get("code") in ("success_command_unavailable", "success_command_timeout"):
                counts["unjudged"] += 1
                _failure(failures, relative, verdict["code"])
            else:
                counts["fail_before_fix"] += 1
            if solutions is None:
                continue
            solution = solutions.get(task["task_id"]) or {}
            if not _is_file_map(solution.get("reference_solution")):
                counts["reference_not_a_file_map"] += 1
                _failure(failures, relative, "reference_solution_not_a_file_map")
            else:
                reference, tampered = judge(task, solution.get("reference_solution"))
                if reference["passed"] and not tampered:
                    counts["reference_passes"] += 1
                else:
                    counts["reference_fails"] += 1
                    _failure(failures, relative, "reference_solution_fails")
            # الشرَكُ يُحكم مستقلًّا عن الحلّ المرجعيّ: مرجعٌ نثريّ لا يُسقط فحصَ شرَكٍ يمرّ (#191)
            if "decoy_solution" in solution and not _is_file_map(solution["decoy_solution"]):
                _failure(failures, relative, "decoy_solution_not_a_file_map")
            elif "decoy_solution" in solution and judge(task, solution["decoy_solution"])[0]["passed"]:
                counts["decoy_passes"] += 1
                _failure(failures, relative, "decoy_solution_passes")
    return {"counts": counts, "failures": failures}


def intake(src: Path, *, agentic: bool = False, open_only: bool = False, current: Path | None = None,
           judge=_judge, sandbox_probes: bool = False) -> dict:
    structure = check_structure(src, open_only=open_only)
    report = {"schema_version": 1, "kind": "kimi_intake", "source": src.name, "structure": structure,
              "open_only": open_only}
    if structure["missing"]:
        report.update(passed=False, measurement_limits=LIMITS)
        return report
    report["manifest"] = {"files": 0, "failures": [], "skipped": "open_only"} if open_only else check_manifest(src)
    if open_only:
        report["replacement"] = check_open_replacement(src, current or CURRENT_OPEN)
    report["bank"] = check_bank(src, sandbox_probes=sandbox_probes)
    report["dev"] = check_dev(src)
    if agentic:
        report["agentic"] = check_agentic(src, judge=judge)
    report["passed"] = not any(report[k]["failures"] for k in ("manifest", "replacement", "bank", "dev", "agentic")
                               if k in report)
    report["measurement_limits"] = LIMITS
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path, help="مجلّدُ تسليم Kimi (kimi-benchmark)")
    parser.add_argument("--agentic", action="store_true", help="يحكم على المهامّ الوكيلة؛ في حاويةٍ زائلة وحدها")
    parser.add_argument("--open-only", action="store_true",
                        help="دورةُ الشطر المفتوح: لا بيانَ، ويُرفض تسليمٌ فيه sealed/")
    parser.add_argument("--current", type=Path, default=None,
                        help="المفتوحُ القائم الذي يستبدله التسليم (الافتراضيُّ بنكُ المستودع)")
    parser.add_argument("--sandbox-probes", action="store_true",
                        help="تُجرَّب الأجوبةُ الثابتة على حالات python_sandbox في الحاوية (يلزم --sandbox-receipt)")
    parser.add_argument("--sandbox-receipt", type=Path, help="إيصالُ تشغيلٍ موثوق خارج المستودع (core/sandbox.py)")
    parser.add_argument("--sandbox-workspace", type=Path, default=ROOT / "var/sandbox")
    parser.add_argument("--list-gameable", action="store_true",
                        help="source شطرٌ مفتوح: تُكتب قائمةُ حالاته التي يمرّرها جوابٌ ثابت (current/GAMEABLE.json)")
    parser.add_argument("--out", required=True, help="مسارُ التقرير، أو - للطباعة")
    args = parser.parse_args(argv)
    if args.list_gameable:
        rows = gameable_cases(args.source.resolve())
        probes = ["empty", "echo", "polarity_spray", "echo_and_spray",
                  *(f"negated_value_{index}" for index in range(1, len(NEGATION_FORMS) + 1)), "needs_sandbox"]
        boxed = sum(row["probe"] == "needs_sandbox" for row in rows)
        listing = {"schema_version": 1, "kind": "gameable_cases", "probes": probes, "gameable": len(rows) - boxed,
                   "needs_sandbox": boxed, "cases": rows}
        text = json.dumps(listing, ensure_ascii=False, indent=2) + "\n"
        if args.out == "-":
            sys.stdout.write(text)
        else:
            Path(args.out).write_text(text, encoding="utf-8")
        return 0
    if args.sandbox_probes:
        if not args.sandbox_receipt:
            parser.error("--sandbox-probes يلزمه --sandbox-receipt")
        from core.sandbox import configure_sandbox_backend
        args.sandbox_workspace.mkdir(parents=True, exist_ok=True)
        configure_sandbox_backend(args.sandbox_receipt.resolve(), args.sandbox_workspace.resolve())
    report = intake(args.source.resolve(), agentic=args.agentic, open_only=args.open_only, current=args.current,
                    sandbox_probes=args.sandbox_probes)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
    else:
        out = Path(args.out)
        if out.exists():
            parser.error(f"التقريرُ قائم: {out}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(json.dumps({"passed": report["passed"], "out": str(out)}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
