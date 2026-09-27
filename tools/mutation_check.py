"""فاحصُ الطفرات (ق٦٧-٦، بندُ ECC الثالث): «قُتلت الطفرة» يُثبَت بالآلة لا بالنثر.

القاعدةُ في `AGENTS.md` §٥: كلُّ حارسٍ جديد يُثبَت بالطفرة. كانت الطفرةُ تُذكر في رسالة الإيداع أو الطلب نثرًا لا يُعاد
إنتاجُه. صارت سطرًا في بيانٍ `tests/mutations/<وحدةُ الاختبار>.jsonl` يسمّي الملفَّ والنصَّ القديم والجديد والاختباراتِ
التي يجب أن تسقط، وهذه الأداةُ تطبّقه في شجرة عملٍ مؤقّتة منفصلة، وتحكم بالرمز:

    killed                   سقط اختبارٌ مسمًّى عند الطفرة (وهو المطلوب)
    survived                 لم يسقط اختبارٌ مسمًّى: الحارسُ لا يحرس
    test_missing             اختبارٌ مسمًّى لا يُجمع
    failing_before_mutation  اختبارٌ مسمًّى ساقطٌ قبل الطفرة، فسقوطُه بعدها لا يثبت شيئًا
    stale                    النصُّ القديم ليس في الملف بعدد مرّاته المعلَن
    invalid                  الطفرةُ كسرت الجمعَ نفسَه (خطأُ صياغة): ليست قتلًا
    timeout                  تجاوزت الاختباراتُ مهلتَها
    manifest_missing         ملفُّ اختبارٍ أُضيف في المدى بلا بيانِ طفرات
    manifest_invalid         سطرٌ بلا حقوله أو بمفتاحٍ مجهول (يُرفض قبل أيّ شجرة عمل)
    target_refused           هدفٌ مطلق أو صاعد أو تحت tests/ أو في مسارٍ فيه sealed (يُرفض قبل أيّ شجرة عمل)

الاستعمال:
    python tools/mutation_check.py --range origin/main..HEAD     # بياناتُ المدى وملفّاتُ الاختبار المضافة فيه
    python tools/mutation_check.py --all                          # كلُّ البيانات (التدقيقُ الأسبوعي)
    python tools/mutation_check.py --manifest tests/mutations/x.jsonl

الحدود: الأداةُ تثبت أن الاختبارَ المسمّى يسقط عند الطفرة المسمّاة، لا أن الطفرةَ ذاتُ معنى ولا أن الحارسَ كامل؛ فطفرةٌ
مكافئة تنجو بحقّ، ومعنى الطفرة يحكم عليه مراجعٌ من عائلةٍ أخرى يقرأ البيان في الفرق.
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = "tests/mutations"
REQUIRED = ("file", "old", "new", "tests")
OPTIONAL = ("id", "task", "why", "count", "added")
NODE_ID = re.compile(r"^tests/[A-Za-z0-9_./-]+\.py::[A-Za-z_][A-Za-z0-9_]*(\[.*\])?$")
VERDICTS = {
    "killed": "قُتلت: سقط الاختبارُ المسمّى عند الطفرة",
    "survived": "نجت: لم يسقط اختبارٌ مسمًّى، فالحارسُ لا يحرس هذا",
    "test_missing": "اختبارٌ مسمًّى لا يُجمع",
    "failing_before_mutation": "اختبارٌ مسمًّى ساقطٌ قبل الطفرة، فسقوطُه لا يثبت شيئًا",
    "stale": "النصُّ القديم ليس في الملف بعدد مرّاته المعلَن",
    "invalid": "الطفرةُ كسرت الجمعَ نفسَه؛ ليست قتلًا",
    "timeout": "تجاوزت الاختباراتُ مهلتَها",
}
LIMITS = [
    "the_tool_proves_that_the_named_test_fails_under_the_named_mutation_not_that_the_mutation_is_meaningful_nor_that_the_guard_is_complete",
    "an_equivalent_mutation_survives_rightly_and_a_mutation_that_breaks_collection_is_invalid_not_a_kill",
    "only_manifests_under_tests_mutations_are_applied_guards_older_than_the_manifests_have_no_proof_until_one_is_written",
    "tests_run_with_the_given_python_in_a_detached_worktree_of_the_head_commit_uncommitted_changes_are_not_measured",
    "a_kill_is_judged_by_the_named_tests_failing_another_test_that_fails_is_not_counted",
]


class Refused(Exception):
    """رفضٌ قبل أيّ شجرة عمل: بيانٌ غيرُ صالح أو هدفٌ مرفوض."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def _git(root: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(root), *argv], check=True, capture_output=True, text=True).stdout.strip()


def _refuse_target(file: str) -> None:
    pure = PurePosixPath(file)
    if not file or file != pure.as_posix() or pure.is_absolute() or ".." in pure.parts or file.startswith("/"):
        raise Refused("target_refused", f"{file}: مسارٌ مطلق أو صاعد أو غيرُ معياريّ")
    if pure.parts[0] == "tests":
        raise Refused("target_refused", f"{file}: الطفرةُ في شيفرة الإنتاج لا في الاختبارات")
    if any(part.lower() == "sealed" for part in pure.parts):
        raise Refused("target_refused", f"{file}: المحجوبُ لا يُمسّ")


def load_manifest(path: Path, root: Path) -> list[dict]:
    """سطورُ بيانٍ واحد، مفحوصةً كلُّها قبل أن يُلمس git."""
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise Refused("manifest_invalid", f"{rel}:{number}: ليس JSON: {exc.msg}") from None
        if not isinstance(entry, dict):
            raise Refused("manifest_invalid", f"{rel}:{number}: السطرُ ليس كائنًا")
        unknown = sorted(set(entry) - set(REQUIRED) - set(OPTIONAL))
        missing = [key for key in REQUIRED if key not in entry]
        if unknown or missing:
            raise Refused("manifest_invalid", f"{rel}:{number}: مفاتيحُ ناقصة {missing} أو مجهولة {unknown}")
        if not all(isinstance(entry[key], str) and entry[key] for key in ("file", "old", "new")) or entry["old"] == entry["new"]:
            raise Refused("manifest_invalid", f"{rel}:{number}: file وold وnew نصوصٌ غيرُ فارغة وold ≠ new")
        tests = entry["tests"]
        if not isinstance(tests, list) or not tests or not all(isinstance(t, str) and NODE_ID.match(t) for t in tests):
            raise Refused("manifest_invalid", f"{rel}:{number}: tests قائمةُ معرّفات pytest غيرُ فارغة")
        count = entry.get("count", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise Refused("manifest_invalid", f"{rel}:{number}: count عددٌ صحيح ≥ 1")
        _refuse_target(entry["file"])
        entries.append({**entry, "count": count, "manifest": rel, "line": number})
    return entries


def _range_scope(root: Path, rng: str) -> tuple[list[Path], list[str], list[str]]:
    """(بياناتُ المدى، ملفّاتُ اختبارٍ مضافة بلا بيان، ملفّاتُ اختبارٍ معدَّلة بلا بيان)."""
    base, head = rng.split("..", 1)
    changed = _git(root, "diff", "--name-only", "--diff-filter=AMR", f"{base}...{head}", "--", f"{MANIFESTS}/*.jsonl").split()
    added = _git(root, "diff", "--name-only", "--diff-filter=A", f"{base}...{head}", "--", "tests/test_*.py").split()
    modified = _git(root, "diff", "--name-only", "--diff-filter=M", f"{base}...{head}", "--", "tests/test_*.py").split()
    has_manifest = lambda test: (root / MANIFESTS / (Path(test).stem + ".jsonl")).is_file()
    return ([root / p for p in changed if (root / p).is_file()],
            [t for t in added if not has_manifest(t)], [t for t in modified if not has_manifest(t)])


def _pytest(python: str, cwd: Path, argv: list[str], timeout: int) -> subprocess.CompletedProcess | None:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        return subprocess.run([python, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-o", "addopts=", *argv],
                              cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _failed(result: subprocess.CompletedProcess) -> list[str]:
    return [line.split(" ", 1)[1].split(" - ", 1)[0] for line in result.stdout.splitlines() if line.startswith("FAILED ")]


def _collect_missing(result: subprocess.CompletedProcess, node_ids: list[str]) -> list[str]:
    """pytest يسمّي المفقودَ بمساره المطلق في شجرة العمل، فيُطابَق بذيل السطر لا بنصّه كلِّه."""
    lines = (result.stdout + result.stderr).splitlines()
    return [t for t in node_ids if any("not found: " in line and line.rstrip().endswith(t) for line in lines)]


def run(root: Path, entries: list[dict], head: str, python: str, timeout: int, keep: bool) -> dict:
    """يطبّق كلَّ طفرةٍ في شجرة عملٍ منفصلة عند `head` ويحكم بالرمز؛ الشجرةُ تُزال دائمًا إلا بـkeep."""
    head_sha = _git(root, "rev-parse", "--verify", f"{head}^{{commit}}")
    tmp = Path(tempfile.mkdtemp(prefix="diwan-mutation-", dir=os.environ.get("RUNNER_TEMP") or None))
    worktree = tmp / "worktree"

    def cleanup():
        if worktree.exists() and not keep:
            subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(worktree)], capture_output=True)
            subprocess.run(["git", "-C", str(root), "worktree", "prune"], capture_output=True)
    atexit.register(cleanup)
    results, baseline = [], {"collected": 0, "missing": [], "failing_before_mutation": []}
    try:
        _git(root, "worktree", "add", "--detach", str(worktree), head_sha)
        node_ids = sorted({t for e in entries for t in e["tests"]})
        collected = _pytest(python, worktree, ["--collect-only", *node_ids], timeout)
        if collected is None:
            raise Refused("timeout", "جمعُ الاختبارات تجاوز مهلتَه")
        baseline["missing"] = _collect_missing(collected, node_ids) if collected.returncode else []
        if collected.returncode and not baseline["missing"]:
            raise Refused("collection_failed", (collected.stdout + collected.stderr)[-2000:])
        runnable = [t for t in node_ids if t not in baseline["missing"]]
        baseline["collected"] = len(runnable)
        if runnable:
            before = _pytest(python, worktree, runnable, timeout)
            if before is None:
                raise Refused("timeout", "التشغيلُ الأساسيّ تجاوز مهلتَه")
            baseline["failing_before_mutation"] = _failed(before)
        for entry in entries:
            results.append(_apply(entry, worktree, baseline, python, timeout))
    finally:
        cleanup()
    totals = {code: sum(1 for r in results if r["code"] == code) for code in VERDICTS}
    return {"commit": head_sha, "baseline": baseline, "results": results, "totals": totals,
            "worktree_kept": str(worktree) if keep else None}


def _apply(entry: dict, worktree: Path, baseline: dict, python: str, timeout: int) -> dict:
    record = {k: entry[k] for k in ("manifest", "line", "file", "old", "new", "tests", "count")}
    record.update({k: entry[k] for k in ("id", "task", "why") if k in entry})
    target = worktree / entry["file"]
    missing = [t for t in entry["tests"] if t in baseline["missing"]]
    failing = [t for t in entry["tests"] if t in baseline["failing_before_mutation"]]
    if missing:
        return {**record, "code": "test_missing", "verdict": VERDICTS["test_missing"], "detail": missing}
    if failing:
        return {**record, "code": "failing_before_mutation", "verdict": VERDICTS["failing_before_mutation"], "detail": failing}
    if not target.is_file():
        return {**record, "code": "stale", "verdict": VERDICTS["stale"], "detail": "الملفُّ غيرُ موجود عند الرأس"}
    original = target.read_bytes()
    source = original.decode("utf-8")
    if source.count(entry["old"]) != entry["count"]:
        return {**record, "code": "stale", "verdict": VERDICTS["stale"],
                "detail": f"old يرد {source.count(entry['old'])} مرّة والمعلَن {entry['count']}"}
    try:
        target.write_text(source.replace(entry["old"], entry["new"]), encoding="utf-8")
        result = _pytest(python, worktree, entry["tests"], timeout)
    finally:
        target.write_bytes(original)
    if result is None:
        return {**record, "code": "timeout", "verdict": VERDICTS["timeout"]}
    failed = _failed(result)
    named_failed = [t for t in entry["tests"] if t in failed]
    if result.returncode == 1 and named_failed:
        return {**record, "code": "killed", "verdict": VERDICTS["killed"], "failed_tests": named_failed,
                "named_but_passed": [t for t in entry["tests"] if t not in failed], "pytest_exit": 1}
    if result.returncode == 0:
        return {**record, "code": "survived", "verdict": VERDICTS["survived"], "pytest_exit": 0}
    return {**record, "code": "invalid", "verdict": VERDICTS["invalid"], "pytest_exit": result.returncode,
            "detail": (result.stdout + result.stderr)[-1500:]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--range", help="BASE..HEAD: بياناتُ المدى وملفّاتُ الاختبار المضافة فيه")
    scope.add_argument("--all", action="store_true")
    scope.add_argument("--manifest", type=Path)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--timeout-s", type=int, default=300)
    parser.add_argument("--keep-worktree", action="store_true")
    parser.add_argument("--strict-unmanifested", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report = {"schema_version": 1, "tool": "tools/mutation_check.py", "python": sys.version.split()[0],
              "scope": "range" if args.range else "all" if args.all else "manifest", "measurement_limits": LIMITS}
    try:
        manifest_missing, unmanifested = [], []
        if args.range:
            paths, manifest_missing, unmanifested = _range_scope(root, args.range)
            report["range"] = args.range
        elif args.all:
            paths = sorted((root / MANIFESTS).glob("*.jsonl")) if (root / MANIFESTS).is_dir() else []
        else:
            paths = [args.manifest if args.manifest.is_absolute() else root / args.manifest]
        entries = [e for path in paths for e in load_manifest(path, root)]
        report["manifests"] = [p.resolve().relative_to(root).as_posix() for p in paths]
        report["manifest_missing"] = manifest_missing
        report["unmanifested_changed_tests"] = unmanifested
        if entries:
            report.update(run(root, entries, args.head, args.python, args.timeout_s, args.keep_worktree))
        else:
            report.update({"commit": _git(root, "rev-parse", "--verify", f"{args.head}^{{commit}}"),
                           "baseline": {"collected": 0, "missing": [], "failing_before_mutation": []},
                           "results": [], "totals": {code: 0 for code in VERDICTS}})
        bad = [r for r in report["results"] if r["code"] != "killed"]
        strict_bad = unmanifested if args.strict_unmanifested else []
        report["status"] = "failed" if bad or manifest_missing or strict_bad else "passed"
        report["exit_code"] = 1 if report["status"] == "failed" else 0
    except Refused as exc:
        report.update({"status": "refused", "code": exc.code, "detail": exc.detail, "exit_code": 2})
    except subprocess.CalledProcessError as exc:
        report.update({"status": "refused", "code": "git_failed", "detail": (exc.stderr or "")[-500:], "exit_code": 2})
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
