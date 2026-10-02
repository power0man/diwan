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
    baseline_failed          التشغيلُ الأساسيّ لم يخرج بصفر؛ تُحفظ أسماءُ الساقط ولا تُطبّق أيُّ طفرة
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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = "tests/mutations"
REQUIRED = ("file", "old", "new", "tests")
OPTIONAL = ("id", "task", "why", "count", "added")
NODE_PARTS = re.compile(r"^([^\[\]]*)(\[.*\])?$")   # مكوّناتُ المعرّف (أصنافٌ ثم الدالّة)، ثم معاملُ parametrize في آخرها إن وُجد


def _valid_test_path(path: str) -> bool:
    """ملفُّ الاختبار بنيةً لا نمطًا: تحت tests/ وينتهي بـ.py، ومقاطعُه غيرُ فارغة وليست . ولا ..، وبلا سطرٍ جديد ولا جدولةٍ (فاصلا
    مخرجات الجمع)؛ والفراغُ في الاسم مقبولٌ كما يقبله pytest، فكان نمطٌ يستثنيه يجعل الاختبارَ الممسوس فيه لا يُثبَت بحال
    (ملاحظة Codex على #149)."""
    parts = path.split("/")
    return (parts[0] == "tests" and len(parts) > 1 and path.endswith(".py")
            and all(part and part not in (".", "..") for part in parts) and not any(c in path for c in "\t\n\r"))


def valid_node_id(node_id: str) -> bool:
    """معرّفُ pytest كامل: ملفٌّ تحت tests/ ثم مكوّناتٌ (دوالُّ الأصناف ومنها) كلٌّ منها معرّفُ بايثون صالح بقاعدة اللغة نفسِها
    (`str.isidentifier`) لا بـ`\\w`: فعلامةُ تشكيلٍ مركّبة (`test_اَ`) و`℘` معرّفاتٌ يجمعها pytest ويرفضها `\\w`، فكان الاختبارُ
    الممسوس بها لا يُثبَت بحال — غيابُه نقصُ إثباتٍ وتسميتُه مرفوضة (ملاحظتا Codex على #149). وأيُّ أبجديةٍ مقبولة."""
    path, sep, rest = node_id.partition("::")
    if not sep or not _valid_test_path(path):
        return False
    parts = NODE_PARTS.match(rest)
    return parts is not None and all(part.isidentifier() for part in parts.group(1).split("::"))
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
    "the_selected_baseline_must_exit_zero_before_any_mutation_is_applied_nonzero_exits_and_timeouts_never_prove_a_kill",
    "what_is_collected_is_decided_by_pytest_over_the_whole_tests_tree_at_the_head_and_at_the_merge_base_compared_by_full_node_ids_with_their_parameters_a_case_new_at_the_head_touches_its_test_wherever_it_appears_and_a_vanished_node_re_applies_its_manifests",
    "an_existing_collected_test_is_touched_when_an_added_line_of_the_range_falls_inside_the_span_of_the_callable_that_defines_it_as_pytest_resolves_it_through___wrapped___file_and_decorator_inclusive_span_from_inspect_wherever_that_callable_lives_a_helper_conftest_or_package_imported_by_its_own_name_or_an_alias_or_inherited_from_another_file_or_inside_the_syntactic_definition_its_node_id_names_in_its_own_module_so_a_wrapper_decorator_without_functools_wraps_hides_nothing_and_a_removed_line_inside_either_span_re_applies_its_manifests",
    "changes_to_fixtures_or_helpers_outside_any_test_callable_s_span_are_not_re_proven_and_a_collected_node_whose_callable_has_no_readable_source_such_as_one_built_by_exec_is_touched_only_when_its_node_id_is_new_and_is_named_with_its_error_in_the_collection_output",
    "origins_place_only_the_node_ids_collected_at_both_ends_of_the_range_new_and_vanished_ids_belong_to_the_collection_diff_an_added_line_in_a_modified_or_added_python_file_touches_a_surviving_test_whose_callable_lives_there_and_a_removed_line_in_a_modified_or_deleted_file_revalidates_it",
    "renames_are_not_detected_in_the_range_a_moved_file_is_its_source_deleted_and_its_destination_added_so_both_sides_are_checked",
    "naming_a_touched_test_in_a_manifest_re_applies_that_manifest_in_the_range_but_the_manifests_themselves_are_read_from_the_working_tree",
    "a_touched_parametrized_test_is_proved_case_by_case_every_case_pytest_collects_for_it_must_fail_a_mutation_since_parsing_cannot_tell_the_added_case_from_the_old_ones",
    "a_manifest_changed_in_the_range_is_compared_with_its_merge_base_version_by_the_exact_names_it_carried_a_test_or_case_still_collected_at_the_head_that_no_line_names_any_more_is_a_dropped_proof_a_bare_function_name_at_the_head_covers_all_its_cases",
]


class Refused(Exception):
    """رفضٌ مسمّى قبل القياس أو عند تعذُّر خطِّ أساسٍ صالح؛ تشخيصُه لا يثبت طفرة."""

    def __init__(self, code: str, detail: str, *, baseline: dict | None = None):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail
        self.baseline = baseline


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
        if not isinstance(tests, list) or not tests or not all(isinstance(t, str) and valid_node_id(t) for t in tests):
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


def _dropped_candidates(root: Path, base: str, head: str, manifests: list[str], modules_at_head: set[str], collected_head: set[str]) -> tuple[list[str], set[str]]:
    """اختباراتٌ كان يسمّيها بيانٌ عند أصل الدمج ولا يسمّيها سطرٌ في بيان وحدتها عند الرأس (حُذف السطرُ أو ضُيّقت قائمتُه)
    ودالّتُها باقية عند الرأس: مرشّحةٌ لفقد إثباتها صامتةً، فيُرفض إلا إن زالت أو سمّاها سطرٌ آخر بمعرّفها أو باسم دالّتها
    (ملاحظة Codex على #149)؛ ومعها ما يسمّيه بيانُ كلِّ وحدةٍ منها عند الرأس، فالحالةُ المسمّاة بمعاملها تُحسم بجمع pytest في شجرة العمل."""
    candidates, named_at_head, own = set(), set(), {}
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
            if function in collected_head:      # ما زالت الدالّةُ تُجمع عند الرأس، فإثباتُها سقط صامتًا
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


def _paths(listing: str) -> list[str]:
    """قوائمُ مسارات git تُقرأ بفاصل NUL (`-z`) لا بالفراغ: مسارٌ فيه فراغٌ كان ينشطر مسارين فيضيع بيانُه اليتيم أو وحدتُه
    (ملاحظة Codex على #149)."""
    return [path for path in listing.split("\0") if path]


def _manifests_owned_at(root: Path, head: str) -> dict[str, str]:
    """بيانُ كلِّ وحدة اختبارٍ عند الرأس بالاتجاه الأمامي (manifest_for على الوحدات الموجودة)، فلا يُعكس الاسمُ — وعكسُه
    ملتبس: test_a__b.jsonl بيانُ tests/test_a__b.py لا tests/test_a/b.py (ملاحظة Codex على #149). وحدتان تؤولان إلى بيانٍ واحد تُرفضان باسمهما."""
    owned: dict[str, str] = {}
    for module in _test_modules(_paths(_git(root, "ls-tree", "-r", "--name-only", "-z", head, "--", "tests/"))):
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
        return _paths(_git(root, "diff", "--name-only", "--no-renames", "-z", f"--diff-filter={status}", f"{base}...{head}", "--", *pathspec))

    added = _test_modules(changed("A", "tests/"))
    modified = _test_modules(changed("M", "tests/"))
    has_manifest = lambda test: (root / manifest_for(test)).is_file()
    names = _manifest_names(root)
    own = lambda node: root / manifest_for(node.split("::", 1)[0])
    # كلُّ اختبارٍ مسّه المدى يسمّيه بيانُ وحدته هو بمعرّفه الكامل (بالصنف الحاوي) — لا بيانُ وحدةٍ أخرى، فإثباتٌ مستعار
    # يزول بزوال معيره (ملاحظة Codex على #149) — والبيانُ الذي يسمّيه يُطبَّق في المدى ولو لم يتغيّر
    deleted = _test_modules(changed("D", "tests/"))
    # ما يُجمع يحسمه pytest عند الرأس وعند أصل الدمج، للمجموعة كلِّها لا للوحدات المتغيّرة وحدها (وارثٌ بلا تعريف، وسمةٌ
    # حاجبة، واسمٌ مستعار، واستيراد، وحارسٌ يبلغ وحدةً لم تتغيّر عبر `import *` من مساعدٍ تغيّر — ملاحظات Codex على #149):
    # ما استجدّ عند الرأس ممسوس، وما بقي يُمسّ بسطرٍ مضاف في مدى الدالّة التي تعرّفه (أدناه)؛ وما زال (ومنه الاسمُ القديم
    # لملفٍّ نُقل)، أو فقد سطرًا من مدى تعريفه، تُعاد بياناتُه فيُحكم ما يسمّيه test_missing هنا لا في الأسبوعيّ وحده
    # والمقارنةُ بالمعرّفات الكاملة بمعاملاتها قبل ردّها إلى دوالّها: حالةٌ تولَّد خارج الوحدة (conftest أو مساعد) لاختبارٍ
    # قائم تمسّه (ملاحظة Codex على #149)
    cases_head, origins_head = _collected_origins_at(root, head, python, ["tests/"], timeout)
    cases_base, origins_base = _collected_origins_at(root, base, python, ["tests/"], timeout)
    functions = lambda cases: {case.split("[", 1)[0] for case in cases}
    collected_head, collected_base = functions(cases_head), functions(cases_base)
    touched = sorted(functions(cases_head - cases_base))
    revalidated = sorted(functions(cases_base - cases_head))
    # وكلُّ اختبارٍ مجموع يُردّ إلى الدالّة التي تعرّفه كما يراها pytest نفسُه (ملفُّها ومداها من inspect)، لا إلى اسمه: مساعدٌ
    # أو conftest أو حزمة، مستورَدٌ باسمه أو باسمٍ مستعار (`from helpers import guard as test_guard`)، أو موروثٌ من ملفٍّ
    # آخر — سطرٌ مضاف في مدى تعريفه يمسّه، وسطرٌ محذوف منه يُعيد بياناتِه؛ وما لا مصدرَ له يُقرأ (يُبنى بـexec) لا يُمسّ إلا
    # باستجداد معرّفه، ويُسمّى بخطئه في خرج الجمع (ملاحظتا Codex على #149)
    # الأصلُ يضع المعرّفاتِ الباقية وحدها (ما استجدّ وما زال لفرق الجمع): سطرٌ مضاف في مدى تعريفٍ باقٍ يمسّه، ولو انتقل
    # التعريفُ إلى ملفٍّ مضاف (`guard` من مساعدٍ قديم إلى جديد مع تحديث الاستيراد، ملاحظة Codex على #149)؛ وسطرٌ محذوف من
    # مدى تعريفه عند الأصل — في ملفٍّ معدَّل أو محذوف — يُعيد بياناتِه
    grown_in = {p: _added_lines(root, base, head, p) for p in changed("AM", ".") if p.endswith(".py")}
    shed_in = {p: _removed_lines(root, base, head, p) for p in changed("MD", ".") if p.endswith(".py")}
    surviving = collected_head & collected_base
    reached = {node for node, spans in origins_head.items()
               if node.split("[", 1)[0] in surviving and any(first <= n <= last for origin, first, last in spans for n in grown_in.get(origin, ()))}
    gone = {node for node, spans in origins_base.items()
            if node.split("[", 1)[0] in surviving and any(first <= n <= last for origin, first, last in spans for n in shed_in.get(origin, ()))}
    touched = sorted({*touched, *functions(reached)})
    revalidated = sorted({*revalidated, *functions(gone)})
    unnamed = [node for node in touched if node not in names.get(own(node), set())]
    naming = [path for path in dict.fromkeys(own(node) for node in [*touched, *revalidated]) if path in names]
    paths = [root / p for p in changed("AMR", f"{MANIFESTS}/*.jsonl") if (root / p).is_file()]
    paths += [path for path in naming if path not in paths]
    # بيانٌ حُذف ووحدتُه باقية عند الرأس: حرّاسُها تفقد إثباتَها صامتة، فيُرفض الحذفُ إلا مع الوحدة
    owned = _manifests_owned_at(root, head)
    orphaned = [m for m in changed("D", f"{MANIFESTS}/*.jsonl") if m in owned]
    # وسطرٌ حُذف أو ضُيّق في بيانٍ باقٍ: ما سمّاه عند أصل الدمج ولا يسمّيه سطرٌ عند الرأس ودالّتُه باقية يفقد إثباتَه صامتًا
    # (ملاحظة Codex على #149)؛ يُحسم بالجمع في شجرة العمل أيُّ حالاته ما زالت تُجمع
    candidates, named_at_head = _dropped_candidates(root, base, head, changed("M", f"{MANIFESTS}/*.jsonl"), set(owned.values()), collected_head)
    return {"paths": paths, "touched": touched, "revalidated_tests": revalidated,
            "manifest_missing": [t for t in added if not has_manifest(t)],
            "unmanifested_changed_tests": [t for t in modified if not has_manifest(t)],
            "unmanifested_new_tests": unnamed, "orphaned_manifests": orphaned,
            "dropped_candidates": candidates, "named_at_head": sorted(named_at_head)}


def _pytest(python: str, cwd: Path, argv: list[str], timeout: int, env_extra: dict | None = None) -> subprocess.CompletedProcess | None:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env_extra or {})}
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


ORIGIN_PLUGIN = '''"""إضافةُ جمعٍ تكتب لكل اختبارٍ مجموع الدالّةَ التي تعرّفه كما يراها pytest: ملفُّها ومداها (بمزخرفاتها) من inspect،
لا اسمَها؛ فالمستورَدُ باسمٍ مستعار والموروثُ من ملفٍّ آخر يُردّان إلى مصدرهما (ملاحظة Codex على #149)."""
import inspect
import os


def pytest_collection_finish(session):
    root = str(session.config.rootpath)
    for item in session.items:
        parts = item.nodeid.split("::")
        names = parts[1:]
        if names:
            names[-1] = getattr(item, "originalname", None) or names[-1].split("[", 1)[0]
        # التعريفُ النحويّ الذي يسمّيه المعرّف في وحدته (صنفٌ فدالّة): يُقرأ بجانب الدالّة الفعلية، فمزخرفٌ يلفّ الاختبارَ
        # بلا functools.wraps يجعل الفعليةَ غلافَه ويُخفي جسمَ التعريف (ملاحظة Codex على #149)
        print("@@syntax", item.nodeid, parts[0], "::".join(names), sep="\\t")
        try:
            func = inspect.unwrap(item.obj)
            file = inspect.getsourcefile(func)
            lines, first = inspect.getsourcelines(func)
        except (AttributeError, TypeError, OSError) as exc:   # بلا مصدرٍ يُقرأ: يُسمّى بخطئه ولا يُوضع
            print("@@origin", item.nodeid, "?", 0, 0, type(exc).__name__, sep="\\t")
            continue
        print("@@origin", item.nodeid, os.path.relpath(file, root), first, first + len(lines) - 1, sep="\\t")
'''


def _def_span(tree: ast.Module, names: list[str]) -> tuple[int, int] | None:
    """مدى التعريف النحويّ الذي تسمّيه أجزاءُ المعرّف في وحدته (صنفٌ فصنفٌ فدالّة)، بمزخرفاته؛ وما لا يُعرَّف هناك (موروثٌ أو
    مستورَد) لا مدى له فيها."""
    node: ast.AST = tree
    for name in names:
        # الاسمُ المكرَّر في الوحدة: التعريفُ الأخير هو الحيّ (ربطُ الأسماء في بايثون)، فهو المدى لا الأولُ الميّت (ملاحظة Codex على #149)
        matches = [n for n in getattr(node, "body", []) if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == name]
        if not matches:
            return None
        node = matches[-1]
    return min([node.lineno, *(d.lineno for d in node.decorator_list)]), node.end_lineno or node.lineno


def _origins(root: Path, sha: str, result: subprocess.CompletedProcess | None) -> dict[str, list[tuple[str, int, int]]]:
    """أصولُ كل اختبارٍ مجموع: الدالّةُ الفعلية كما كتبتها الإضافة (الملفُّ وأولُ سطرٍ وآخرُه)، والتعريفُ النحويّ الذي يسمّيه معرّفُه
    في وحدته عند هذا الإيداع؛ وما بلا مصدرٍ يُقرأ ولا تعريفٍ في وحدته لا أصلَ له."""
    origins: dict[str, list[tuple[str, int, int]]] = {}
    trees: dict[str, ast.Module | None] = {}
    for line in result.stdout.splitlines() if result else []:
        if line.startswith("@@origin\t"):
            _, node, origin, first, last, *_ = line.split("\t")
            if origin != "?":
                origins.setdefault(node, []).append((origin, int(first), int(last)))
        elif line.startswith("@@syntax\t"):
            _, node, path, names = line.split("\t")[:4]
            if path not in trees:
                try:
                    trees[path] = ast.parse(_show(root, sha, path))
                except (SyntaxError, subprocess.CalledProcessError):
                    trees[path] = None
            span = _def_span(trees[path], names.split("::")) if trees[path] is not None and names else None
            if span is not None:
                origins.setdefault(node, []).append((path, *span))
    return origins


def _collected_origins_at(root: Path, sha: str, python: str, modules: list[str], timeout: int) -> tuple[set[str], dict[str, tuple[str, int, int]]]:
    """ما يجمعه pytest فعلًا من هذه المسارات عند الإيداع، بمعرّفاته الكاملة بمعاملاتها، في شجرة عملٍ مؤقّتة تُزال: هو الحكمُ
    فيما يُجمع (وارثٌ بلا تعريف، وسمةٌ حاجبة، واسمٌ مستعار، واستيراد، وحالةٌ تولَّد خارج الوحدة…) والقراءةُ النحوية للمديات
    وحدها؛ ومعه أصلُ كلِّ مجموع: الدالّةُ التي تعرّفه بملفّها ومداها (ملاحظات Codex على #149)."""
    tmp = Path(tempfile.mkdtemp(prefix="diwan-mutation-scope-", dir=os.environ.get("RUNNER_TEMP") or None))
    worktree, plugin = tmp / "worktree", tmp / "plugin"
    try:
        _git(root, "worktree", "add", "--detach", str(worktree), sha)
        present = [m for m in modules if (worktree / m).exists()]      # ما زال منها عند هذا الإيداع لا يُجمع
        if not present:
            return set(), {}
        plugin.mkdir()
        (plugin / "diwan_collect_origins.py").write_text(ORIGIN_PLUGIN, encoding="utf-8")
        path = os.pathsep.join(p for p in (str(plugin), os.environ.get("PYTHONPATH", "")) if p)
        result = _pytest(python, worktree, ["-p", "diwan_collect_origins", "--collect-only", *present], timeout,
                         env_extra={"PYTHONPATH": path})
        if result is None:
            raise Refused("timeout", f"جمعُ الاختبارات عند {sha[:12]} تجاوز مهلتَه")
        if result.returncode not in (0, 5):        # 5: لا اختبارَ في هذه الوحدات
            raise Refused("collection_failed", f"عند {sha[:12]}: " + (result.stdout + result.stderr)[-2000:])
        return set(_listed(result)), _origins(root, sha, result)
    finally:
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(worktree)], capture_output=True)
        subprocess.run(["git", "-C", str(root), "worktree", "prune"], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def _collected_at(root: Path, sha: str, python: str, modules: list[str], timeout: int) -> set[str]:
    """معرّفاتُ ما يُجمع وحدها (انظر _collected_origins_at)."""
    return _collected_origins_at(root, sha, python, modules, timeout)[0]


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
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)   # الحاضنُ المؤقّت نفسُه لا الشجرةُ وحدها: كان يبقى فارغًا بعد كلِّ تشغيلٍ فتتراكم آلافُه
    atexit.register(cleanup)
    results, baseline, cases, probed = [], {"collected": 0, "missing": [], "failing_before_mutation": [],
                                          "status": "not_run", "pytest_exit": None}, {}, {}
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
                baseline["status"] = "timeout"
                raise Refused("timeout", "التشغيلُ الأساسيّ تجاوز مهلتَه", baseline=baseline)
            baseline["pytest_exit"] = before.returncode
            baseline["failing_before_mutation"] = _failed(before)
            baseline["status"] = "passed" if before.returncode == 0 else "failed"
            # أسماءُ الساقط تشخيصٌ فقط؛ غيابُ FAILED لا يعني النجاح (INTERNALERROR مثلًا).
            # لا ننشر stdout/stderr: قد يحملان محتوى الاختبارات أو تفاصيلَ خاصة.
            if before.returncode != 0:
                raise Refused("baseline_failed", f"التشغيلُ الأساسيّ لم ينجح (pytest exit: {before.returncode})", baseline=baseline)
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
                           "baseline": {"collected": 0, "missing": [], "failing_before_mutation": [],
                                        "status": "not_run", "pytest_exit": None},
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
        if exc.baseline is not None:
            report["baseline"] = exc.baseline
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
