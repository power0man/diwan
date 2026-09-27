"""فاحصُ الطفرات (ق٦٧-٦، بندُ ECC الثالث): «قُتلت الطفرة» يُثبَت بالآلة لا بالنثر.

القاعدةُ في `AGENTS.md` §٥: كلُّ حارسٍ جديد يُثبَت بالطفرة. كانت الطفرةُ تُذكر في رسالة الإيداع أو الطلب نثرًا لا يُعاد
إنتاجُه. صارت سطرًا في بيانٍ `tests/mutations/<وحدةُ الاختبار>.jsonl` يسمّي الملفَّ والنصَّ القديم والجديد والاختباراتِ
التي يجب أن تسقط، وهذه الأداةُ تطبّقه في شجرة عملٍ مؤقّتة منفصلة، وتحكم بالرمز:

    killed                   سقط كلُّ اختبارٍ مسمًّى عند الطفرة (وهو المطلوب)
    partially_killed         سقط بعضُ المسمّى لا كلُّه: ما لم يسقط لا يحرس هذه الطفرة فلا يُدَّعى له ذلك؛ والدالّةُ المسمّاةُ
                             بلا معامل تُبسط إلى حالاتها كما يجمعها pytest فيلزم سقوطُها كلِّها
    survived                 لم يسقط اختبارٌ مسمًّى: الحارسُ لا يحرس
    test_missing             اختبارٌ مسمًّى لا يُجمع
    failing_before_mutation  اختبارٌ مسمًّى ساقطٌ قبل الطفرة، فسقوطُه بعدها لا يثبت شيئًا
    stale                    النصُّ القديم ليس في الملف بعدد مرّاته المعلَن
    invalid                  الطفرةُ كسرت الجمعَ نفسَه (خطأُ صياغة): ليست قتلًا
    timeout                  تجاوزت الاختباراتُ مهلتَها
    manifest_missing         ملفُّ اختبارٍ أُضيف (أو نُقل إليه) في المدى بلا بيانِ طفرات باسمه؛ والوحدةُ في مجلّدٍ فرعيّ
                             tests/a/test_x.py بيانُها tests/mutations/a__test_x.jsonl
    unmanifested_new_tests   اختبارٌ مسّه المدى (ما استجدّ في جمع pytest عند الرأس على أصل الدمج، أو اختبارٌ قائم دخل سطرٌ مضاف في
                             مداه المقروء نحويًّا) ولا يسمّيه بيانٌ بمعرّفه الكامل
    unproved_touched_tests   اختبارٌ ممسوس سمّاه بيانٌ لكنه لم يسقط هو نفسُه تحت أيّ طفرةٍ في المدى (ذكرُه بجانب قاتلٍ لا يثبته)؛
                             والدالّةُ المعلَّمة بـparametrize حالاتٌ كما يجمعها pytest، وكلُّ حالةٍ منها حارسٌ يُثبَت وحده
    orphaned_manifests       بيانٌ حُذف في المدى ووحدتُه باقية (تُعرف الوحدةُ بالاتجاه الأمامي: أيُّ وحدةٍ عند الرأس بيانُها هذا)
    dropped_proofs           سطرٌ حُذف أو ضُيّقت قائمتُه في بيانٍ باقٍ: اختبارٌ (أو حالةٌ) كان يسمّيه البيانُ عند أصل الدمج ولا يسمّيه
                             سطرٌ عند الرأس وهو ما زال يُجمع، ففقد إثباتَه صامتًا؛ يُقبل إن زال الاختبارُ أو سمّاه سطرٌ آخر
    revalidated_tests        اختبارٌ زال في المدى أو فقد سطرًا من مداه: بياناتُه تُعاد في المدى (فحذفُ اختبارٍ أو حالةٍ يسمّيها بيانٌ
                             يُحكم test_missing هنا لا في الأسبوعيّ وحده)، ولا يُطلب إثباتُه من جديد
    manifest_invalid         سطرٌ بلا حقوله أو بمفتاحٍ مجهول، أو يسمّي اختبارًا من وحدةٍ أخرى — بيانُ الوحدة يسمّي اختباراتِها
                             وحدها فلا يعيرها بيانُ غيرها إثباتًا يزول بزواله (يُرفض قبل أيّ شجرة عمل)
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
    "killed": "قُتلت: سقط كلُّ اختبارٍ مسمًّى عند الطفرة",
    "partially_killed": "سقط بعضُ المسمّى لا كلُّه؛ ما لم يسقط لا يحرس هذه الطفرة",
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
    "an_existing_test_is_touched_by_an_added_line_inside_its_span_at_the_head_so_changes_to_fixtures_or_helpers_outside_test_functions_are_not_re_proven_while_a_test_that_vanished_or_lost_a_line_only_has_its_manifests_re_applied",
    "what_is_collected_is_decided_by_pytest_at_the_head_and_at_the_merge_base_a_node_new_at_the_head_is_touched_and_an_existing_node_is_touched_only_when_the_parser_places_an_added_line_in_its_span",
    "the_parser_places_definitions_Test_classes_unittest_subclasses_named_in_the_module_and_same_file_inheritance_so_an_existing_collected_node_it_cannot_place_such_as_an_imported_test_an_alias_or_a_method_of_a_class_whose_base_is_imported_under_another_name_is_not_touched_by_an_added_line",
    "inherited_test_methods_are_placed_through_bases_defined_at_module_level_in_the_same_file_first_base_wins_so_an_added_line_in_a_base_method_touches_every_heir_of_that_file",
    "renames_are_not_detected_in_the_range_a_moved_file_is_its_source_deleted_and_its_destination_added_so_both_sides_are_checked",
    "naming_a_touched_test_in_a_manifest_re_applies_that_manifest_in_the_range_but_the_manifests_themselves_are_read_from_the_working_tree",
    "a_touched_parametrized_test_is_proved_case_by_case_every_case_pytest_collects_for_it_must_fail_a_mutation_since_parsing_cannot_tell_the_added_case_from_the_old_ones",
    "a_manifest_changed_in_the_range_is_compared_with_its_merge_base_version_by_the_exact_names_it_carried_a_test_or_case_still_collected_at_the_head_that_no_line_names_any_more_is_a_dropped_proof_a_bare_function_name_at_the_head_covers_all_its_cases",
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
        foreign = [t for t in tests if manifest_for(t.split("::", 1)[0]) != rel]
        if foreign:
            raise Refused("manifest_invalid", f"{rel}:{number}: يسمّي اختبارًا من وحدةٍ أخرى {foreign[0]}؛ بيانُ الوحدة يسمّي اختباراتِها وحدها")
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


def _test_nodes_at(root: Path, head: str, test_file: str) -> dict[str, list[tuple[int, int]]]:
    """مدياتُ اختبارات الملفّ عند الرأس كما تقرؤها الشجرةُ النحوية (دوالُّ test* في الوحدة وفي أصناف Test* وأصناف unittest
    أيًّا كان اسمُها)، كلٌّ بمعرّفه الكامل: مدى الدالّة من أول مزخرفٍ إلى آخر سطر، والموروثةُ من أصلٍ في الوحدة نفسِها تحمل
    فوقه رأسَ كلِّ وارثٍ في السلسلة (مزخرفاتِه وسطرَ class) فإضافةُ دالّةٍ في الأصل أو تغييرُ أصل الوارث يمسّها. والقراءةُ
    للمديات وحدها؛ أما ما يُجمع فعلًا فيحسمه pytest في `_range_scope` (ملاحظات Codex على #149)."""
    try:
        tree = ast.parse(_show(root, head, test_file))
    except SyntaxError as exc:
        raise Refused("test_file_unparsable", f"{test_file}: {exc.msg} (السطر {exc.lineno})") from None
    nodes: dict[str, list[tuple[int, int]]] = {}
    unittest_classes = _unittest_classes(tree)
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    first = lambda node: min([node.lineno, *(d.lineno for d in node.decorator_list)])

    def methods(cls: ast.ClassDef, seen: tuple[str, ...] = ()) -> dict[str, list[tuple[int, int]]]:
        """دوالُّ test* التي تقرؤها الشجرةُ من الصنف: الموروثةُ من أصوله في الوحدة (الأولُ يغلب كما في MRO) ثم ما يعرّفه هو فيغلب."""
        found: dict[str, list[tuple[int, int]]] = {}
        for base in reversed(cls.bases):
            name = _base_name(base)
            if name in classes and name not in (*seen, cls.name):
                for method, spans in methods(classes[name], (*seen, cls.name)).items():
                    found[method] = [*spans, (first(cls), cls.lineno)]
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                found[node.name] = [(first(node), node.end_lineno or node.lineno)]
        return found

    def visit(body, prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                nodes[f"{test_file}::{prefix}{node.name}"] = [(first(node), node.end_lineno or node.lineno)]
            elif isinstance(node, ast.ClassDef) and (node.name.startswith("Test") or node.name in unittest_classes):
                for method, spans in methods(node).items():
                    nodes[f"{test_file}::{prefix}{node.name}::{method}"] = spans
                visit([n for n in node.body if isinstance(n, ast.ClassDef)], f"{prefix}{node.name}::")

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


def _revalidated_test_nodes(root: Path, base: str, head: str, test_file: str) -> list[str]:
    """اختباراتُ الأساس التي زالت من القراءة أو فقدت سطرًا من مداها: بياناتُها تُعاد في المدى، فحذفُ اختبارٍ أو حالةٍ يسمّيها
    بيانٌ يُحكم test_missing في الطلب لا في التدقيق الأسبوعيّ وحده (ملاحظة Codex على #149)."""
    nodes = _test_nodes_at(root, base, test_file)
    removed, at_head = _removed_lines(root, base, head, test_file), _test_nodes_at(root, head, test_file)
    return sorted(node for node, spans in nodes.items()
                  if node not in at_head or any(start <= n <= end for start, end in spans for n in removed))


def _touched_test_nodes(root: Path, base: str, head: str, test_file: str) -> list[str]:
    """الاختباراتُ القائمة التي مسّها المدى: كلُّ اختبارٍ دخل سطرٌ مضاف في مداه (تأكيدٌ جديد في دالّةٍ قائمة، أو حالةٌ في مزخرفها،
    أو دالّةٌ في أصلٍ يرثه) — ملاحظاتُ Codex على #149؛ أما ما استجدّ فيحسمه جمعُ pytest."""
    nodes = _test_nodes_at(root, head, test_file)
    added = _added_lines(root, base, head, test_file)
    return sorted(node for node, spans in nodes.items() if any(start <= n <= end for start, end in spans for n in added))


def _named_exactly(text: str) -> set[str]:
    """ما يسمّيه نصُّ بيانٍ من اختبارات بمعرّفها كما كُتب، بمعاملاته (قراءةٌ متسامحة؛ الصلاحيةُ في load_manifest)."""
    named: set[str] = set()
    for line in text.splitlines():
        try:
            tests = json.loads(line).get("tests", []) if line.strip() else []
        except (json.JSONDecodeError, AttributeError):
            continue
        named.update(t for t in tests if isinstance(t, str))
    return named


def _manifest_names(root: Path) -> dict[Path, set[str]]:
    """ما يسمّيه كلُّ بيانٍ في المستودع من اختبارات بمعرّفها الكامل بلا معاملات."""
    return {path: {t.split("[", 1)[0] for t in _named_exactly(path.read_text(encoding="utf-8"))}
            for path in (sorted((root / MANIFESTS).glob("*.jsonl")) if (root / MANIFESTS).is_dir() else [])}


def _dropped_candidates(root: Path, base: str, head: str, manifests: list[str], modules_at_head: set[str]) -> tuple[list[str], set[str]]:
    """اختباراتٌ كان يسمّيها بيانٌ عند أصل الدمج ولا يسمّيها سطرٌ في بيان وحدتها عند الرأس (حُذف السطرُ أو ضُيّقت قائمتُه)
    ودالّتُها باقية عند الرأس: مرشّحةٌ لفقد إثباتها صامتةً، فيُرفض إلا إن زالت أو سمّاها سطرٌ آخر بمعرّفها أو باسم دالّتها
    (ملاحظة Codex على #149)؛ ومعها ما يسمّيه بيانُ كلِّ وحدةٍ منها عند الرأس، فالحالةُ المسمّاة بمعاملها تُحسم بجمع pytest في شجرة العمل."""
    candidates, named_at_head, nodes, own = set(), set(), {}, {}
    for manifest in manifests:
        for test in sorted(_named_exactly(_git(root, "show", f"{base}:{manifest}"))):
            function = test.split("[", 1)[0]
            module = function.split("::", 1)[0]
            if module not in modules_at_head:
                continue
            if module not in own:       # ما يسمّيه بيانُ الوحدة نفسِها عند الرأس — وهو وحدَه من يسمّي اختباراتِها
                path = root / manifest_for(module)
                own[module] = _named_exactly(path.read_text(encoding="utf-8")) if path.is_file() else set()
                named_at_head |= own[module]
            if test in own[module] or function in own[module]:
                continue
            if module not in nodes:
                nodes[module] = _test_nodes_at(root, head, module)
            if function in nodes[module]:
                candidates.add(test)
    return sorted(candidates), named_at_head


def _is_test_module(path: str) -> bool:
    """أنماطُ pytest الافتراضية لملفّ الاختبار (python_files): test_*.py و*_test.py."""
    name = PurePosixPath(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def _test_modules(paths: list[str]) -> list[str]:
    """وحداتُ الاختبار تحت tests/ في أيّ عمق وبنمطَي pytest (test_*.py و*_test.py)، فنقلُ الاختبارات إلى مجلّدٍ فرعيّ أو
    تسميتُها بالنمط الثاني لا يُخرجها من المدى (ملاحظتا Codex على #149)."""
    return [p for p in paths if p.startswith("tests/") and _is_test_module(p)]


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


def _range_scope(root: Path, rng: str, python: str = sys.executable, timeout: int = 300) -> dict:
    """مدى الطلب: البياناتُ التي تُطبَّق (المتغيّرةُ ومعها كلُّ بيانٍ يسمّي اختبارًا ممسوسًا)، والاختباراتُ الممسوسة التي يجب
    أن يسقط كلٌّ منها تحت طفرةٍ ما، وما يُسقط المدى: ملفُّ اختبارٍ مضاف أو مُعادُ التسمية بلا بيانٍ باسمه، واختبارٌ ممسوس لا
    يسمّيه بيان، وبيانٌ حُذف ووحدتُه باقية (ملاحظات Codex على #149)."""
    base, head = rng.split("..", 1)
    # الشجرةُ القديمة هي أصلُ الدمج لا رأسُ الأساس: فالفرقُ base...head يُقاس منه، وأسطرُه المُزالة بإحداثياته؛ ورأسُ الأساس
    # الذي تقدّم بأسطرٍ مُدرَجة قبل الاختبار يُزيح مدياتِه فيضيع ما زال (ملاحظة Codex على #149)
    base = _git(root, "merge-base", base, head)

    def changed(status: str, *pathspec: str) -> list[str]:
        # بلا كشفِ إعادة التسمية: المنقولُ محذوفٌ في مصدره ومضافٌ في وجهته فتُفحص الجهتان — ملفُّ اختبارٍ نُقل وجهتُه كالمضاف
        # (كلُّ اختبارٍ فيها ممسوس ويلزمها بيانٌ باسمها)، وبيانٌ نُقل باسم وحدةٍ أخرى يتيمٌ لمصدره (ملاحظتا Codex على #149)
        return _git(root, "diff", "--name-only", "--no-renames", f"--diff-filter={status}", f"{base}...{head}", "--", *pathspec).split()

    added = _test_modules(changed("A", "tests/"))
    modified = _test_modules(changed("M", "tests/"))
    has_manifest = lambda test: (root / manifest_for(test)).is_file()
    names = _manifest_names(root)
    own = lambda node: root / manifest_for(node.split("::", 1)[0])
    # كلُّ اختبارٍ مسّه المدى يسمّيه بيانُ وحدته هو بمعرّفه الكامل (بالصنف الحاوي) — لا بيانُ وحدةٍ أخرى، فإثباتٌ مستعار
    # يزول بزوال معيره (ملاحظة Codex على #149) — والبيانُ الذي يسمّيه يُطبَّق في المدى ولو لم يتغيّر
    deleted = _test_modules(changed("D", "tests/"))
    # ما يُجمع يحسمه pytest عند الرأس وعند أصل الدمج (وارثٌ بلا تعريف، وسمةٌ حاجبة، واسمٌ مستعار، واستيراد — ملاحظات Codex
    # على #149): ما استجدّ عند الرأس ممسوس، وما بقي يُمسّ بسطرٍ مضاف في مداه المقروء نحويًّا؛ وما زال، أو فقد سطرًا من مداه
    # (ومنه الاسمُ القديم لملفٍّ نُقل)، تُعاد بياناتُه فيُحكم ما يسمّيه test_missing هنا لا في الأسبوعيّ وحده
    collected_head = _collected_at(root, head, python, added + modified, timeout)
    collected_base = _collected_at(root, base, python, modified + deleted, timeout)
    grown = [node for test in modified for node in _touched_test_nodes(root, base, head, test)]
    touched = sorted((collected_head - collected_base) | {node for node in grown if node in collected_head})
    unnamed = [node for node in touched if node not in names.get(own(node), set())]
    shrunk = [node for test in modified for node in _revalidated_test_nodes(root, base, head, test)]
    revalidated = sorted((collected_base - collected_head) | {node for node in shrunk if node in collected_base})
    naming = [path for path in dict.fromkeys(own(node) for node in [*touched, *revalidated]) if path in names]
    paths = [root / p for p in changed("AMR", f"{MANIFESTS}/*.jsonl") if (root / p).is_file()]
    paths += [path for path in naming if path not in paths]
    # بيانٌ حُذف ووحدتُه باقية عند الرأس: حرّاسُها تفقد إثباتَها صامتة، فيُرفض الحذفُ إلا مع الوحدة
    owned = _manifests_owned_at(root, head)
    orphaned = [m for m in changed("D", f"{MANIFESTS}/*.jsonl") if m in owned]
    # وسطرٌ حُذف أو ضُيّق في بيانٍ باقٍ: ما سمّاه عند أصل الدمج ولا يسمّيه سطرٌ عند الرأس ودالّتُه باقية يفقد إثباتَه صامتًا
    # (ملاحظة Codex على #149)؛ يُحسم بالجمع في شجرة العمل أيُّ حالاته ما زالت تُجمع
    candidates, named_at_head = _dropped_candidates(root, base, head, changed("M", f"{MANIFESTS}/*.jsonl"), set(owned.values()))
    return {"paths": paths, "touched": touched, "revalidated_tests": revalidated,
            "manifest_missing": [t for t in added if not has_manifest(t)],
            "unmanifested_changed_tests": [t for t in modified if not has_manifest(t)],
            "unmanifested_new_tests": unnamed, "orphaned_manifests": orphaned,
            "dropped_candidates": candidates, "named_at_head": sorted(named_at_head)}


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


def _collected_at(root: Path, sha: str, python: str, modules: list[str], timeout: int) -> set[str]:
    """ما يجمعه pytest فعلًا من هذه الوحدات عند الإيداع، بمعرّفاته بلا معاملات، في شجرة عملٍ مؤقّتة تُزال: هو الحكمُ فيما يُجمع
    (وارثٌ بلا تعريف، وسمةٌ حاجبة، واسمٌ مستعار، واستيراد…) والقراءةُ النحوية للمديات وحدها (ملاحظات Codex على #149)."""
    if not modules:
        return set()
    tmp = Path(tempfile.mkdtemp(prefix="diwan-mutation-scope-", dir=os.environ.get("RUNNER_TEMP") or None))
    worktree = tmp / "worktree"
    try:
        _git(root, "worktree", "add", "--detach", str(worktree), sha)
        result = _pytest(python, worktree, ["--collect-only", *modules], timeout)
        if result is None:
            raise Refused("timeout", f"جمعُ الاختبارات عند {sha[:12]} تجاوز مهلتَه")
        if result.returncode not in (0, 5):        # 5: لا اختبارَ في هذه الوحدات
            raise Refused("collection_failed", f"عند {sha[:12]}: " + (result.stdout + result.stderr)[-2000:])
        return {case.split("[", 1)[0] for case in _listed(result)}
    finally:
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(worktree)], capture_output=True)
        subprocess.run(["git", "-C", str(root), "worktree", "prune"], capture_output=True)


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
        touched: list[str] | None = None, probe: list[str] | None = None) -> dict:
    """يطبّق كلَّ طفرةٍ في شجرة عملٍ منفصلة عند `head` ويحكم بالرمز؛ الشجرةُ تُزال دائمًا إلا بـkeep."""
    head_sha = _git(root, "rev-parse", "--verify", f"{head}^{{commit}}")
    tmp = Path(tempfile.mkdtemp(prefix="diwan-mutation-", dir=os.environ.get("RUNNER_TEMP") or None))
    worktree = tmp / "worktree"

    def cleanup():
        if worktree.exists() and not keep:
            subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(worktree)], capture_output=True)
            subprocess.run(["git", "-C", str(root), "worktree", "prune"], capture_output=True)
    atexit.register(cleanup)
    results, baseline, cases, probed = [], {"collected": 0, "missing": [], "failing_before_mutation": []}, {}, {}
    try:
        _git(root, "worktree", "add", "--detach", str(worktree), head_sha)
        for entry in entries:
            _refuse_escape(worktree, entry["file"])
        cases = _touched_cases(python, worktree, touched or [], timeout)
        probed = _touched_cases(python, worktree, probe or [], timeout)     # حالاتُ ما زال ذكرُه من البيانات، لحسم ما فقد إثباتَه
        node_ids = sorted({t for e in entries for t in e["tests"]})
        # الاسمُ المسمّى بلا معامل يُبسط إلى حالاته المجموعة، فلا يُحكم له بالقتل إلا إذا سقطت كلُّها (ملاحظة Codex على #149)
        selectors = _touched_cases(python, worktree, [t for t in node_ids if "[" not in t], timeout)
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
            results.append(_apply(entry, worktree, baseline, python, timeout, selectors))
    finally:
        cleanup()
    totals = {code: sum(1 for r in results if r["code"] == code) for code in VERDICTS}
    return {"commit": head_sha, "baseline": baseline, "results": results, "totals": totals, "touched_cases": cases,
            "probed_cases": probed, "worktree_kept": str(worktree) if keep else None}


def _apply(entry: dict, worktree: Path, baseline: dict, python: str, timeout: int, selectors: dict | None = None) -> dict:
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
    expected = lambda t: (selectors or {}).get(t) or [t]        # الدالّةُ بلا معامل: حالاتُها كلُّها
    failed_tests = list(dict.fromkeys(c for t in entry["tests"] for c in expected(t) if c in failed))
    named_but_passed = [t for t in entry["tests"] if any(c not in failed for c in expected(t))]
    # القتلُ حكمٌ على كلِّ اسمٍ في السطر: اسمٌ لم يسقط لا يُنسب إليه ما لم يفعل (ملاحظة Codex على #149)
    if result.returncode == 1 and failed_tests and not named_but_passed:
        return {**record, "code": "killed", "verdict": VERDICTS["killed"], "failed_tests": failed_tests,
                "named_but_passed": [], "pytest_exit": 1}
    if result.returncode == 1 and failed_tests:
        return {**record, "code": "partially_killed", "verdict": VERDICTS["partially_killed"], "failed_tests": failed_tests,
                "named_but_passed": named_but_passed, "detail": named_but_passed, "pytest_exit": 1}
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
                 "unmanifested_new_tests": [], "orphaned_manifests": [], "dropped_candidates": [], "named_at_head": []}
        if args.range:
            scope = _range_scope(root, args.range, args.python, args.timeout_s)
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
            report.update(run(root, entries, args.head, args.python, args.timeout_s, args.keep_worktree, scope["touched"],
                              sorted({t.split("[", 1)[0] for t in scope["dropped_candidates"]})))
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
        # وما زال ذكرُه من بيانٍ باقٍ يفقد إثباتَه إن كان ما زال يُجمع: الحالةُ بمعرّفها، والدالّةُ بكلِّ حالةٍ لا يسمّيها سطرٌ عند الرأس
        # (ملاحظة Codex على #149)؛ وما لم يُجمع لغياب شجرة العمل يُعدّ فاقدًا
        probed, named = report.get("probed_cases", {}), set(scope["named_at_head"])
        report["dropped_proofs"] = dropped = sorted({
            case for test in scope["dropped_candidates"] for function in [test.split("[", 1)[0]]
            for case in (probed[function] if function in probed else [test])
            if case == test or (test == function and case not in named)})
        bad = [r for r in report["results"] if r["code"] != "killed"]
        missing, unnamed, orphaned = scope["manifest_missing"], scope["unmanifested_new_tests"], scope["orphaned_manifests"]
        strict_bad = scope["unmanifested_changed_tests"] if args.strict_unmanifested else []
        report["status"] = "failed" if bad or missing or unnamed or unproved or orphaned or dropped or strict_bad else "passed"
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
