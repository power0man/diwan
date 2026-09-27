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
    manifest_missing         ملفُّ اختبارٍ أُضيف (أو أُعيدت تسميتُه) في المدى بلا بيانِ طفرات باسمه؛ والوحدةُ في مجلّدٍ فرعيّ
                             tests/a/test_x.py بيانُها tests/mutations/a__test_x.jsonl
    unmanifested_new_tests   اختبارٌ مسّه المدى (كلُّ اختبارٍ في ملفٍّ مضاف، أو اختبارٌ دخل سطرٌ مضاف في مداه) ولا يسمّيه بيانٌ بمعرّفه الكامل
    unproved_touched_tests   اختبارٌ ممسوس سمّاه بيانٌ لكنه لم يسقط هو نفسُه تحت أيّ طفرةٍ في المدى (ذكرُه بجانب قاتلٍ لا يثبته)؛
                             والدالّةُ المعلَّمة بـparametrize حالاتٌ كما يجمعها pytest، وكلُّ حالةٍ منها حارسٌ يُثبَت وحده
    orphaned_manifests       بيانٌ حُذف في المدى ووحدتُه باقية (تُعرف الوحدةُ بالاتجاه الأمامي: أيُّ وحدةٍ عند الرأس بيانُها هذا)
    revalidated_tests        اختبارٌ زال في المدى أو فقد سطرًا من مداه: بياناتُه تُعاد في المدى (فحذفُ اختبارٍ أو حالةٍ يسمّيها بيانٌ
                             يُحكم test_missing هنا لا في الأسبوعيّ وحده)، ولا يُطلب إثباتُه من جديد
    manifest_invalid         سطرٌ بلا حقوله أو بمفتاحٍ مجهول (يُرفض قبل أيّ شجرة عمل)
    manifest_name_collision  وحدتان عند الرأس تؤولان إلى بيانٍ واحد (tests/test_a/test_x.py وtests/test_a__test_x.py)؛ يُرفض قبل أيّ شجرة عمل
    target_refused           هدفٌ مطلق أو صاعد أو تحت tests/ أو في مسارٍ فيه sealed (يُرفض قبل أيّ شجرة عمل)، أو يمرّ
                             بوصلةٍ رمزية في شجرة العمل (يُرفض قبل أيّ طفرة)

الاستعمال:
    python tools/mutation_check.py --range origin/main..HEAD     # بياناتُ المدى، وكلُّ بيانٍ يسمّي اختبارًا مسّه المدى
    python tools/mutation_check.py --all                          # كلُّ البيانات (التدقيقُ الأسبوعي)
    python tools/mutation_check.py --manifest tests/mutations/x.jsonl

الحدود: الأداةُ تثبت أن الاختبارَ المسمّى يسقط عند الطفرة المسمّاة، لا أن الطفرةَ ذاتُ معنى ولا أن الحارسَ كامل؛ فطفرةٌ
مكافئة تنجو بحقّ، ومعنى الطفرة يحكم عليه مراجعٌ من عائلةٍ أخرى يقرأ البيان في الفرق.
"""
from __future__ import annotations

import argparse
import ast
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
NODE_ID = re.compile(r"^tests/[A-Za-z0-9_./-]+\.py(::[A-Za-z_][A-Za-z0-9_]*)+(\[.*\])?$")   # ومنه دوالُّ الأصناف
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
    "a_touched_test_is_one_with_an_added_line_inside_its_span_at_the_head_so_changes_to_fixtures_or_helpers_outside_test_functions_are_not_re_proven_while_a_test_that_vanished_or_lost_a_line_only_has_its_manifests_re_applied",
    "tests_are_found_by_parsing_the_head_file_for_the_default_pytest_names_test_functions_Test_classes_and_unittest_TestCase_subclasses_named_in_the_module_not_by_collecting_with_pytest",
    "a_class_whose_base_is_imported_under_a_name_that_does_not_end_in_TestCase_is_not_seen_as_a_unittest_class_so_its_methods_are_not_touched_tests",
    "naming_a_touched_test_in_a_manifest_re_applies_that_manifest_in_the_range_but_the_manifests_themselves_are_read_from_the_working_tree",
    "a_touched_parametrized_test_is_proved_case_by_case_every_case_pytest_collects_for_it_must_fail_a_mutation_since_parsing_cannot_tell_the_added_case_from_the_old_ones",
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


def _refuse_escape(worktree: Path, file: str) -> None:
    """الهدفُ في شجرة العمل ملفٌّ لا يمرّ بوصلةٍ رمزية: وصلةٌ إلى tests/ أو إلى خارج الشجرة تجعل الطفرةَ تمسّ غيرَ ما
    سُمّي فتُحسب قتلًا زائفًا (ملاحظة Codex على #149)."""
    target = worktree / file
    if not target.exists():
        return                                  # غيابُه يُحكم عليه stale في موضعه
    root = Path(os.path.realpath(worktree))
    try:
        real = Path(os.path.realpath(target)).relative_to(root).as_posix()
    except ValueError:
        raise Refused("target_refused", f"{file}: يمرّ بوصلةٍ رمزية إلى خارج شجرة العمل") from None
    if real != file:
        raise Refused("target_refused", f"{file}: وصلةٌ رمزية إلى {real}")


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
    if not entries:
        raise Refused("manifest_invalid", f"{rel}: بيانٌ بلا طفرة؛ لا يُغني عن البيان")
    return entries


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
HUNK_BASE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+")


def _show(root: Path, head: str, path: str) -> str:
    """نصُّ الملفّ عند الرأس كما هو، بلا قصٍّ يحرّك أرقامَ الأسطر."""
    return subprocess.run(["git", "-C", str(root), "show", f"{head}:{path}"], check=True, capture_output=True, text=True).stdout


def _base_name(expr: ast.expr) -> str | None:
    """آخرُ مقطعٍ من اسم الأصل: unittest.TestCase → TestCase، وBase → Base؛ وما ليس اسمًا لا يُعرف."""
    return expr.id if isinstance(expr, ast.Name) else expr.attr if isinstance(expr, ast.Attribute) else None


def _unittest_classes(tree: ast.Module) -> set[str]:
    """أصنافُ الوحدة التي يجمعها pytest أيًّا كان اسمُها لأنها ترث unittest.TestCase (اسمُ الأصل ينتهي بـTestCase) أو ترث
    صنفًا من الوحدة نفسِها يرثه — ملاحظةُ Codex على #149. والأصلُ المستورد باسمٍ آخر حدٌّ معلَن."""
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    found: set[str] = set()
    while True:
        more = {c.name for c in classes if c.name not in found
                and any((base := _base_name(b)) is not None and (base.endswith("TestCase") or base in found) for b in c.bases)}
        if not more:
            return found
        found |= more


def _test_nodes_at(root: Path, head: str, test_file: str) -> dict[str, tuple[int, int]]:
    """اختباراتُ الملفّ عند الرأس بأسماء pytest الافتراضية (دوالُّ test* في الوحدة وفي أصناف Test* وفي أصناف unittest أيًّا
    كان اسمُها)، كلٌّ بمعرّفه الكامل بالصنف الحاوي ومدى أسطره من أول مزخرفٍ إلى آخر سطر."""
    try:
        tree = ast.parse(_show(root, head, test_file))
    except SyntaxError as exc:
        raise Refused("test_file_unparsable", f"{test_file}: {exc.msg} (السطر {exc.lineno})") from None
    nodes: dict[str, tuple[int, int]] = {}
    unittest_classes = _unittest_classes(tree)

    def visit(body, prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
                nodes[f"{test_file}::{prefix}{node.name}"] = (start, node.end_lineno or node.lineno)
            elif isinstance(node, ast.ClassDef) and (node.name.startswith("Test") or node.name in unittest_classes):
                visit(node.body, f"{prefix}{node.name}::")

    visit(tree.body, "")
    return nodes


def _added_lines(root: Path, base: str, head: str, test_file: str) -> set[int]:
    """أرقامُ الأسطر المضافة في الرأس، من رؤوس مقاطع الفرق."""
    lines: set[int] = set()
    for line in _git(root, "diff", "--unified=0", f"{base}...{head}", "--", test_file).splitlines():
        if m := HUNK.match(line):
            start, count = int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 1
            lines.update(range(start, start + count))
    return lines


def _removed_lines(root: Path, base: str, head: str, test_file: str) -> set[int]:
    """أرقامُ أسطر الأساس التي أُزيلت، من رؤوس مقاطع الفرق."""
    lines: set[int] = set()
    for line in _git(root, "diff", "--unified=0", f"{base}...{head}", "--", test_file).splitlines():
        if m := HUNK_BASE.match(line):
            start, count = int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 1
            lines.update(range(start, start + count))
    return lines


def _revalidated_test_nodes(root: Path, base: str, head: str, test_file: str, deleted: bool) -> list[str]:
    """اختباراتُ الأساس التي زالت أو فقدت سطرًا من مداها: بياناتُها تُعاد في المدى، فحذفُ اختبارٍ أو حالةٍ يسمّيها بيانٌ يُحكم
    test_missing في الطلب لا في التدقيق الأسبوعيّ وحده (ملاحظة Codex على #149)."""
    nodes = _test_nodes_at(root, base, test_file)
    if deleted:
        return sorted(nodes)
    removed, at_head = _removed_lines(root, base, head, test_file), _test_nodes_at(root, head, test_file)
    return sorted(node for node, (start, end) in nodes.items() if node not in at_head or any(start <= n <= end for n in removed))


def _touched_test_nodes(root: Path, base: str, head: str, test_file: str, new_file: bool) -> list[str]:
    """الاختباراتُ التي مسّها المدى: كلُّ اختبارٍ في ملفٍّ مضاف، وفي الملفّ القائم كلُّ اختبارٍ دخل سطرٌ مضاف في مداه
    (دالّةٌ جديدة، أو تأكيدٌ جديد في دالّةٍ قائمة، أو حالةٌ في مزخرفها) — ملاحظاتُ Codex على #149."""
    nodes = _test_nodes_at(root, head, test_file)
    if new_file:
        return sorted(nodes)
    added = _added_lines(root, base, head, test_file)
    return sorted(node for node, (start, end) in nodes.items() if any(start <= n <= end for n in added))


def _manifest_names(root: Path) -> dict[Path, set[str]]:
    """ما يسمّيه كلُّ بيانٍ في المستودع من اختبارات بمعرّفها الكامل بلا معاملات (قراءةٌ متسامحة؛ الصلاحيةُ في load_manifest)."""
    names: dict[Path, set[str]] = {}
    for path in sorted((root / MANIFESTS).glob("*.jsonl")) if (root / MANIFESTS).is_dir() else []:
        named: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                tests = json.loads(line).get("tests", []) if line.strip() else []
            except (json.JSONDecodeError, AttributeError):
                continue
            named.update(t.split("[", 1)[0] for t in tests if isinstance(t, str))
        names[path] = named
    return names


def _test_modules(paths: list[str]) -> list[str]:
    """وحداتُ الاختبار تحت tests/ في أيّ عمق (test_*.py)، فنقلُ الاختبارات إلى مجلّدٍ فرعيّ لا يُخرجها من المدى."""
    return [p for p in paths if p.startswith("tests/") and p.endswith(".py") and PurePosixPath(p).name.startswith("test_")]


def manifest_for(test: str) -> str:
    """بيانُ وحدة اختبار: tests/unit/test_x.py → tests/mutations/unit__test_x.jsonl (المجلّداتُ تُوصل بـ__)."""
    return f"{MANIFESTS}/{'__'.join(PurePosixPath(test).with_suffix('').parts[1:])}.jsonl"


def _manifests_owned_at(root: Path, head: str) -> dict[str, str]:
    """بيانُ كلِّ وحدة اختبارٍ عند الرأس بالاتجاه الأمامي (manifest_for على الوحدات الموجودة)، فلا يُعكس الاسمُ — وعكسُه
    ملتبس: test_a__b.jsonl بيانُ tests/test_a__b.py لا tests/test_a/b.py (ملاحظة Codex على #149). وحدتان تؤولان إلى بيانٍ واحد تُرفضان باسمهما."""
    owned: dict[str, str] = {}
    for module in _test_modules(_git(root, "ls-tree", "-r", "--name-only", head, "--", "tests/").split()):
        manifest = manifest_for(module)
        if manifest in owned:
            raise Refused("manifest_name_collision", f"{owned[manifest]} و{module} كلاهما بيانُه {manifest}")
        owned[manifest] = module
    return owned


def _range_scope(root: Path, rng: str) -> dict:
    """مدى الطلب: البياناتُ التي تُطبَّق (المتغيّرةُ ومعها كلُّ بيانٍ يسمّي اختبارًا ممسوسًا)، والاختباراتُ الممسوسة التي يجب
    أن يسقط كلٌّ منها تحت طفرةٍ ما، وما يُسقط المدى: ملفُّ اختبارٍ مضاف أو مُعادُ التسمية بلا بيانٍ باسمه، واختبارٌ ممسوس لا
    يسمّيه بيان، وبيانٌ حُذف ووحدتُه باقية (ملاحظات Codex على #149)."""
    base, head = rng.split("..", 1)

    def changed(status: str, *pathspec: str) -> list[str]:
        return _git(root, "diff", "--name-only", f"--diff-filter={status}", f"{base}...{head}", "--", *pathspec).split()

    # الملفُّ المعادُ تسميتُه يُعامل كالمضاف: كلُّ اختبارٍ فيه ممسوس ويلزمه بيانٌ باسمه الجديد
    added = _test_modules(changed("A", "tests/")) + _test_modules(changed("R", "tests/"))
    modified = _test_modules(changed("M", "tests/"))
    has_manifest = lambda test: (root / manifest_for(test)).is_file()
    names = _manifest_names(root)
    # كلُّ اختبارٍ مسّه المدى يسمّيه بيانٌ بمعرّفه الكامل (بالصنف الحاوي)، والبيانُ الذي يسمّيه يُطبَّق في المدى ولو لم يتغيّر
    touched = [node for test in added for node in _touched_test_nodes(root, base, head, test, True)]
    touched += [node for test in modified for node in _touched_test_nodes(root, base, head, test, False)]
    unnamed = [node for node in touched if not any(node in named for named in names.values())]
    # والاختبارُ الذي زال (ومنه الاسمُ القديم لملفٍّ أُعيدت تسميتُه) أو فقد سطرًا: بياناتُه تُعاد فيُحكم ما يسمّيه test_missing هنا
    deleted = _test_modules(_git(root, "diff", "--name-only", "--diff-filter=D", "--no-renames", f"{base}...{head}", "--", "tests/").split())
    revalidated = [node for test in deleted for node in _revalidated_test_nodes(root, base, head, test, True)]
    revalidated += [node for test in modified for node in _revalidated_test_nodes(root, base, head, test, False)]
    naming = [path for path, named in names.items() if named & (set(touched) | set(revalidated))]
    paths = [root / p for p in changed("AMR", f"{MANIFESTS}/*.jsonl") if (root / p).is_file()]
    paths += [path for path in naming if path not in paths]
    # بيانٌ حُذف ووحدتُه باقية عند الرأس: حرّاسُها تفقد إثباتَها صامتة، فيُرفض الحذفُ إلا مع الوحدة
    owned = _manifests_owned_at(root, head)
    orphaned = [m for m in changed("D", f"{MANIFESTS}/*.jsonl") if m in owned]
    return {"paths": paths, "touched": touched, "revalidated_tests": revalidated,
            "manifest_missing": [t for t in added if not has_manifest(t)],
            "unmanifested_changed_tests": [t for t in modified if not has_manifest(t)],
            "unmanifested_new_tests": unnamed, "orphaned_manifests": orphaned}


def _pytest(python: str, cwd: Path, argv: list[str], timeout: int) -> subprocess.CompletedProcess | None:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        return subprocess.run([python, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-o", "addopts=", *argv],
                              cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _failed(result: subprocess.CompletedProcess) -> list[str]:
    return [line.split(" ", 1)[1].split(" - ", 1)[0] for line in result.stdout.splitlines() if line.startswith("FAILED ")]


def _listed(result: subprocess.CompletedProcess | None) -> list[str]:
    """معرّفاتُ ما جمعه pytest بترتيبه كما يطبعها --collect-only -q (لا يطبعها متى فُقد أيُّ اسمٍ مسمًّى)."""
    return [l.strip() for l in result.stdout.splitlines() if l.strip().startswith("tests/")] if result and result.returncode == 0 else []


def _collect_missing(node_ids: list[str], python: str, worktree: Path, timeout: int) -> list[str]:
    """المفقودُ ما سُمّي ولم يُجمع. متى أخفق الجمعُ الجامع كتم pytest قائمةَ المجموع وسمّى أولَ مفقودٍ وحده، ويسمّي
    الحالةَ المفقودة من parametrize بدالّتها بلا معامل؛ فتُجمع كلُّ دالّةٍ مسمّاة وحدها، فيُعرف ما فُقد من دوالَّ وحالات —
    والدالّةُ المسمّاةُ بلا معامل مجموعةٌ بحالاتها. وعطبُ جمعٍ ليس فقدانًا (خطأُ صياغة) يُرفض باسمه."""
    function = lambda t: t.split("[", 1)[0]
    missing: list[str] = []
    for name in sorted({function(t) for t in node_ids}):
        again = _pytest(python, worktree, ["--collect-only", name], timeout)
        if again is None:
            raise Refused("timeout", "جمعُ الاختبارات تجاوز مهلتَه")
        if again.returncode and "not found" not in again.stdout + again.stderr:
            raise Refused("collection_failed", (again.stdout + again.stderr)[-2000:])
        present = _listed(again)
        missing += [t for t in node_ids if function(t) == name and t not in present and not any(c.startswith(t + "[") for c in present)]
    return missing


def _touched_cases(python: str, worktree: Path, touched: list[str], timeout: int) -> dict[str, list[str]]:
    """حالاتُ كلِّ اختبارٍ ممسوس كما يجمعها pytest عند الرأس: الدالّةُ المعلَّمة بـparametrize حالاتٌ عدّة بمعرّفاتها، وكلُّ
    حالةٍ حارسٌ يُثبَت وحده لأن القراءة لا تميّز الحالةَ المضافة من القديمة (ملاحظة Codex على #149)."""
    if not touched:
        return {}
    collected = _pytest(python, worktree, ["--collect-only", *touched], timeout)
    if collected is None:
        raise Refused("timeout", "جمعُ الاختبارات الممسوسة تجاوز مهلتَه")
    # متى لم يُجمع اسمٌ واحد كتم pytest القائمةَ كلَّها، فيُجمع كلُّ ممسوسٍ وحده حتى لا تضيع حالاتُ الباقين صامتةً
    listed = _listed(collected) if collected.returncode == 0 else [c for node in touched for c in _listed(_pytest(python, worktree, ["--collect-only", node], timeout))]
    cases: dict[str, list[str]] = {node: [] for node in touched}
    for case in dict.fromkeys(listed):
        function = case.split("[", 1)[0]
        if function in cases:
            cases[function].append(case)
    return cases


def _covers(name: str, failed: list[str]) -> list[str]:
    """ما سقط مما يسمّيه الاسم: هو نفسُه، أو حالاتُه إن سُمّيت الدالّةُ المعلَّمة بلا معامل."""
    return [f for f in failed if f == name or f.startswith(name + "[")]


def run(root: Path, entries: list[dict], head: str, python: str, timeout: int, keep: bool,
        touched: list[str] | None = None) -> dict:
    """يطبّق كلَّ طفرةٍ في شجرة عملٍ منفصلة عند `head` ويحكم بالرمز؛ الشجرةُ تُزال دائمًا إلا بـkeep."""
    head_sha = _git(root, "rev-parse", "--verify", f"{head}^{{commit}}")
    tmp = Path(tempfile.mkdtemp(prefix="diwan-mutation-", dir=os.environ.get("RUNNER_TEMP") or None))
    worktree = tmp / "worktree"

    def cleanup():
        if worktree.exists() and not keep:
            subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(worktree)], capture_output=True)
            subprocess.run(["git", "-C", str(root), "worktree", "prune"], capture_output=True)
    atexit.register(cleanup)
    results, baseline, cases = [], {"collected": 0, "missing": [], "failing_before_mutation": []}, {}
    try:
        _git(root, "worktree", "add", "--detach", str(worktree), head_sha)
        for entry in entries:
            _refuse_escape(worktree, entry["file"])
        cases = _touched_cases(python, worktree, touched or [], timeout)
        node_ids = sorted({t for e in entries for t in e["tests"]})
        collected = _pytest(python, worktree, ["--collect-only", *node_ids], timeout)
        if collected is None:
            raise Refused("timeout", "جمعُ الاختبارات تجاوز مهلتَه")
        baseline["missing"] = _collect_missing(node_ids, python, worktree, timeout) if collected.returncode else []
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
    return {"commit": head_sha, "baseline": baseline, "results": results, "totals": totals, "touched_cases": cases,
            "worktree_kept": str(worktree) if keep else None}


def _apply(entry: dict, worktree: Path, baseline: dict, python: str, timeout: int) -> dict:
    record = {k: entry[k] for k in ("manifest", "line", "file", "old", "new", "tests", "count")}
    record.update({k: entry[k] for k in ("id", "task", "why") if k in entry})
    target = worktree / entry["file"]
    missing = [t for t in entry["tests"] if t in baseline["missing"]]
    failing = [t for t in entry["tests"] if _covers(t, baseline["failing_before_mutation"])]
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
    # ما سقط يُسجَّل بمعرّفه الكامل بالمعامل، فالدالّةُ المسمّاةُ بلا معامل تُثبَت حالةً حالة
    failed_tests = list(dict.fromkeys(f for t in entry["tests"] for f in _covers(t, failed)))
    if result.returncode == 1 and failed_tests:
        return {**record, "code": "killed", "verdict": VERDICTS["killed"], "failed_tests": failed_tests,
                "named_but_passed": [t for t in entry["tests"] if not _covers(t, failed)], "pytest_exit": 1}
    if result.returncode == 0:
        return {**record, "code": "survived", "verdict": VERDICTS["survived"], "pytest_exit": 0}
    return {**record, "code": "invalid", "verdict": VERDICTS["invalid"], "pytest_exit": result.returncode,
            "detail": (result.stdout + result.stderr)[-1500:]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--range", help="BASE..HEAD: بياناتُ المدى، وكلُّ بيانٍ يسمّي اختبارًا مسّه المدى")
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
        scope = {"touched": [], "revalidated_tests": [], "manifest_missing": [], "unmanifested_changed_tests": [],
                 "unmanifested_new_tests": [], "orphaned_manifests": []}
        if args.range:
            scope = _range_scope(root, args.range)
            paths = scope["paths"]
            report["range"] = args.range
        elif args.all:
            paths = sorted((root / MANIFESTS).glob("*.jsonl")) if (root / MANIFESTS).is_dir() else []
        else:
            paths = [args.manifest if args.manifest.is_absolute() else root / args.manifest]
        entries = [e for path in paths for e in load_manifest(path, root)]
        report["manifests"] = [p.resolve().relative_to(root).as_posix() for p in paths]
        for key in ("manifest_missing", "unmanifested_changed_tests", "unmanifested_new_tests", "orphaned_manifests", "revalidated_tests"):
            report[key] = scope[key]
        if entries:
            report.update(run(root, entries, args.head, args.python, args.timeout_s, args.keep_worktree, scope["touched"]))
        else:
            report.update({"commit": _git(root, "rev-parse", "--verify", f"{args.head}^{{commit}}"),
                           "baseline": {"collected": 0, "missing": [], "failing_before_mutation": []},
                           "results": [], "totals": {code: 0 for code in VERDICTS}})
        # الاختبارُ الممسوس يجب أن يسقط هو نفسُه تحت طفرةٍ ما في المدى؛ فذكرُه بجانب اختبارٍ قاتل لا يثبته، وحالاتُ الدالّة
        # المعلَّمة تُثبَت حالةً حالة بمعرّفها (ملاحظتا Codex على #149)
        proved = {t for r in report["results"] if r["code"] == "killed" for t in r["failed_tests"]}
        cases = report.get("touched_cases", {})
        unproved = [case for node in scope["touched"] if node not in scope["unmanifested_new_tests"]
                    for case in (cases.get(node) or [node]) if case not in proved]
        report["unproved_touched_tests"] = unproved
        bad = [r for r in report["results"] if r["code"] != "killed"]
        missing, unnamed, orphaned = scope["manifest_missing"], scope["unmanifested_new_tests"], scope["orphaned_manifests"]
        strict_bad = scope["unmanifested_changed_tests"] if args.strict_unmanifested else []
        report["status"] = "failed" if bad or missing or unnamed or unproved or orphaned or strict_bad else "passed"
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
