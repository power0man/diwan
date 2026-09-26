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
from pathlib import Path
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
    "gameable_check_patterns_of_k17_are_counted_only_as_cases_without_checks",
    "agentic_checks_run_only_in_a_disposable_container_otherwise_they_are_reported_unjudged",
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


def _open_ids(root: Path) -> dict[str, set]:
    """كلُّ ملفٍّ بمعرّفاته. والملفُّ الجانبيّ (`.meta.json`) داخلٌ بمفاتيح مهامّه، فحلولُه المرجعية ومصادرُه تُمحى معه."""
    out = {}
    for path in sorted(root.rglob("*.json")):
        data = _json(path)
        if path.name.endswith(".meta.json"):
            tasks = data.get("tasks") if isinstance(data, dict) else None
            out[path.relative_to(root).as_posix()] = set(tasks) if isinstance(tasks, dict) else set()
            continue
        items = (data.get("cases") or data.get("tasks") or []) if isinstance(data, dict) else []
        out[path.relative_to(root).as_posix()] = {
            item.get("case_id") or item.get("task_id") for item in items if isinstance(item, dict)}
    return out


def check_open_replacement(src: Path, current: Path) -> dict:
    """دورةُ المفتوح تستبدل المفتوحَ القائم (`UPDATE=1 OPEN_ONLY=1 place`)، فلا تمرّ إلا بكلِّ ملفٍّ وكلِّ حالةٍ فيه.

    كان تسليمٌ فارغٌ يمرّ: البيانُ يُتخطّى، ولا ملفَّ يسقط في المدقّقات، فيمحو التوزيعُ البنك (ملاحظة Codex على #128).
    والزيادةُ مقبولة: ملفٌّ أو حالةٌ جديدة لا تمحو شيئًا.
    """
    failures: list = []
    expected = _open_ids(current) if current.is_dir() else {}
    if not expected:
        _failure(failures, "open", "current_open_bank_missing")
        return {"files": 0, "failures": failures}
    delivered = _open_ids(src / "open")
    for relative, ids in expected.items():
        if relative not in delivered:
            _failure(failures, f"open/{relative}", "open_file_missing")
        elif not ids <= delivered[relative]:
            _failure(failures, f"open/{relative}", "open_case_missing")
    return {"files": len(expected), "failures": failures}


def _bank_files(src: Path) -> list[tuple[str, Path]]:
    out = []
    for part in ("open", "sealed"):
        for path in sorted((src / part).rglob("*.json")):
            if not path.name.endswith(".meta.json") and path.name != "MANIFEST.json":
                out.append((part, path))
    return out


def check_bank(src: Path) -> dict:
    """المدقّقاتُ الحقيقية على الشطرين، وشروطُ v1.2. والمحجوبُ أعدادٌ ورموزٌ بلا معرّفات."""
    failures: list = []
    counts = {"open": {"files": 0, "cases": 0, "tasks": 0}, "sealed": {"files": 0, "cases": 0, "tasks": 0}}
    without_checks = {"open": 0, "sealed": 0}
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
        for case in suite["cases"]:
            case_ids[case["case_id"]] = case_ids.get(case["case_id"], 0) + 1
    try:
        qualified = validate_agentic_bank([suite for _, _, suite in agentic])
    except PayloadRejected as exc:
        _failure(failures, "agentic", exc.code)
        qualified = []
    task_ids = [q.split("/", 1)[1] for q in qualified]
    for part in ("open", "sealed"):
        if without_checks[part]:
            _failure(failures, part, "cases_without_checks")
    if len(set(task_ids)) != len(task_ids):
        _failure(failures, "agentic", "task_id_not_unique_across_bank")
    if any(n > 1 for n in case_ids.values()):
        _failure(failures, "bank", "case_id_not_unique_across_bank")
    return {"counts": counts, "without_checks": without_checks, "failures": failures}


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


def _judge(task: dict, overlay: dict | None) -> tuple[dict, list[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        materialize(task, root)
        for name, content in (overlay or {}).items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(workspace_bytes(content))
        return evaluate_success(task, root), harness_tampering(task, root)


def check_agentic(src: Path, *, judge=_judge) -> dict:
    """في الحاوية: كلُّ مهمّةٍ تسقط قبل الحلّ، ومهامُّ التطوير تمرّ بحلّها المرجعيّ."""
    failures: list = []
    counts = {"tasks": 0, "fail_before_fix": 0, "pass_before_fix": 0, "unjudged": 0,
              "reference_passes": 0, "reference_fails": 0, "decoy_passes": 0}
    suites = [(path.relative_to(src).as_posix(), _json(path), None) for _, path in _bank_files(src)]
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
            reference, tampered = judge(task, solution.get("reference_solution"))
            if reference["passed"] and not tampered:
                counts["reference_passes"] += 1
            else:
                counts["reference_fails"] += 1
                _failure(failures, relative, "reference_solution_fails")
            if "decoy_solution" in solution and judge(task, solution["decoy_solution"])[0]["passed"]:
                counts["decoy_passes"] += 1
                _failure(failures, relative, "decoy_solution_passes")
    return {"counts": counts, "failures": failures}


def intake(src: Path, *, agentic: bool = False, open_only: bool = False, current: Path | None = None,
           judge=_judge) -> dict:
    structure = check_structure(src, open_only=open_only)
    report = {"schema_version": 1, "kind": "kimi_intake", "source": src.name, "structure": structure,
              "open_only": open_only}
    if structure["missing"]:
        report.update(passed=False, measurement_limits=LIMITS)
        return report
    report["manifest"] = {"files": 0, "failures": [], "skipped": "open_only"} if open_only else check_manifest(src)
    if open_only:
        report["replacement"] = check_open_replacement(src, current or CURRENT_OPEN)
    report["bank"] = check_bank(src)
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
    parser.add_argument("--out", required=True, help="مسارُ التقرير، أو - للطباعة")
    args = parser.parse_args(argv)
    report = intake(args.source.resolve(), agentic=args.agentic, open_only=args.open_only, current=args.current)
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
