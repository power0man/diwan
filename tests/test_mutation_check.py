"""حارسُ فاحص الطفرات (ق٦٧-٦) على مستودعٍ مصطنع: كلُّ رمزٍ من أحكامه يُنتَج بطفرةٍ معلومة، والمستودعُ المصدر لا يُمسّ،
ولا تبقى شجرةُ عملٍ بعد التشغيل، والرفضُ يسبق أيَّ شجرة عمل."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import mutation_check as mc  # noqa: E402

GUARD = "def positive(x):\n    return x > 0\n"
TESTS = """from pkg.guard import positive


def test_zero_is_not_positive():
    assert positive(0) is False


def test_always_passes():
    assert True


def test_already_failing():
    assert False, "ساقطٌ قبل أيّ طفرة"
"""
KILL = {"file": "pkg/guard.py", "old": "return x > 0", "new": "return x >= 0",
        "tests": ["tests/test_guard.py::test_zero_is_not_positive"]}


@pytest.fixture
def git(tmp_path):
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-09-27T00:00:00Z", "GIT_COMMITTER_DATE": "2026-09-27T00:00:00Z"}

    def run(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, env=env, check=True, capture_output=True, text=True).stdout.strip()

    return run


@pytest.fixture
def repo(tmp_path, git):
    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("")
    (tmp_path / "pkg/guard.py").write_text(GUARD)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_guard.py").write_text(TESTS)
    (tmp_path / "tests/mutations").mkdir()
    git("add", "-A")
    git("commit", "-qm", "base")
    return tmp_path


def _manifest(repo: Path, name: str, *entries: dict) -> Path:
    path = repo / "tests/mutations" / f"{name}.jsonl"
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8")
    return path


def _run(repo: Path, *argv: str, capsys) -> dict:
    code = mc.main([*argv, "--root", str(repo), "--report", str(repo.parent / "report.json")])
    report = json.loads((repo.parent / "report.json").read_text(encoding="utf-8"))
    assert report["exit_code"] == code
    capsys.readouterr()
    return report


def _clean(repo: Path, git) -> None:
    assert git("status", "--porcelain") == "", "المستودعُ المصدر مُسّ"
    assert git("worktree", "list", "--porcelain").count("worktree ") == 1, "شجرةُ عملٍ بقيت"
    assert not any((repo / ".git/worktrees").glob("*")), "بقايا شجرة عمل"


def test_every_verdict_code_comes_from_its_own_mutation(repo, capsys, git):
    git("rm", "-q", "--cached", "tests/test_guard.py")     # نُبقيه في الشجرة لكنّ الطفرات تُقاس على الرأس
    git("add", "tests/test_guard.py")
    _manifest(repo, "test_guard",
              {**KILL, "id": "kill"},
              {**KILL, "id": "survive", "tests": ["tests/test_guard.py::test_always_passes"]},
              {**KILL, "id": "missing", "tests": ["tests/test_guard.py::test_no_such_test"]},
              {**KILL, "id": "stale", "old": "return x < 0"},
              {**KILL, "id": "count", "count": 2},
              {**KILL, "id": "invalid", "new": "return x > 0 ("},
              {**KILL, "id": "failing-before", "tests": ["tests/test_guard.py::test_already_failing"]})
    git("add", "-A")
    git("commit", "-qm", "manifest")
    report = _run(repo, "--all", capsys=capsys)
    by_id = {r["id"]: r["code"] for r in report["results"]}
    assert by_id == {"kill": "killed", "survive": "survived", "missing": "test_missing", "stale": "stale",
                     "count": "stale", "invalid": "invalid", "failing-before": "failing_before_mutation"}
    assert report["status"] == "failed" and report["exit_code"] == 1
    assert report["baseline"]["failing_before_mutation"] == ["tests/test_guard.py::test_already_failing"]
    assert report["baseline"]["missing"] == ["tests/test_guard.py::test_no_such_test"]
    killed = next(r for r in report["results"] if r["id"] == "kill")
    assert killed["failed_tests"] == KILL["tests"] and killed["verdict"] == mc.VERDICTS["killed"]
    assert "measurement_limits" in report and report["commit"] == git("rev-parse", "HEAD")
    _clean(repo, git)


def test_a_clean_manifest_passes_and_the_worktree_is_removed_even_when_kept_is_off(repo, capsys, git):
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    git("add", "-A")
    git("commit", "-qm", "manifest")
    report = _run(repo, "--all", capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1 and report["exit_code"] == 0
    assert report["worktree_kept"] is None
    _clean(repo, git)


@pytest.mark.parametrize("target, reason", [
    ("/no/such/guard.py", "مطلق"), ("../outside.py", "صاعد"), ("tests/test_guard.py", "تحت tests/"),
    ("evaluation/banks/x/sealed/case.json", "محجوب"), ("evaluation/banks/x/Sealed/case.json", "محجوب بحرفٍ كبير"),
], ids=["absolute", "parent", "tests_dir", "sealed", "sealed_capitalised"])
def test_a_refused_target_is_refused_before_any_worktree_exists(repo, target, reason, capsys, git):
    _manifest(repo, "test_guard", {**KILL, "file": target})
    report = _run(repo, "--all", capsys=capsys)
    assert (report["status"], report["code"], report["exit_code"]) == ("refused", "target_refused", 2), reason
    assert "results" not in report and not (repo / ".git/worktrees").exists()


@pytest.mark.parametrize("entry, reason", [
    ({**KILL, "extra": 1}, "مفتاحٌ مجهول"), ({k: v for k, v in KILL.items() if k != "tests"}, "بلا tests"),
    ({**KILL, "tests": []}, "tests فارغة"), ({**KILL, "tests": ["not a node id"]}, "معرّفٌ غيرُ صالح"),
    ({**KILL, "new": KILL["old"]}, "old = new"), ({**KILL, "count": 0}, "count صفر"), ({**KILL, "count": True}, "count منطقيّ"),
], ids=["unknown_key", "no_tests", "empty_tests", "bad_node_id", "old_equals_new", "count_zero", "count_bool"])
def test_an_invalid_manifest_line_is_refused_before_any_worktree_exists(repo, entry, reason, capsys, git):
    _manifest(repo, "test_guard", entry)
    report = _run(repo, "--all", capsys=capsys)
    assert (report["status"], report["code"], report["exit_code"]) == ("refused", "manifest_invalid", 2), reason
    assert not (repo / ".git/worktrees").exists()


def test_a_line_that_is_not_json_is_refused_by_name(repo, capsys, git):
    (repo / "tests/mutations/test_guard.jsonl").write_text('{"file": ', encoding="utf-8")
    report = _run(repo, "--all", capsys=capsys)
    assert report["code"] == "manifest_invalid" and "JSON" in report["detail"]


def test_range_mode_applies_the_changed_manifests_and_names_an_added_test_file_without_one(repo, capsys, git):
    base = git("rev-parse", "HEAD")
    _manifest(repo, "test_guard", {**KILL, "id": "kill"}, {**KILL, "id": "more", "tests": ["tests/test_guard.py::test_more"]},
              {**KILL, "id": "other-named-elsewhere", "tests": ["tests/test_other.py::test_other"]})
    (repo / "tests/test_other.py").write_text("from pkg.guard import positive\n\n\ndef test_other():\n    assert positive(0) is False\n")
    (repo / "tests/test_guard.py").write_text(TESTS + "\n\ndef test_more():\n    assert positive(0) is False\n")
    git("add", "-A")
    git("commit", "-qm", "range")
    head = git("rev-parse", "HEAD")
    report = _run(repo, "--range", f"{base}..{head}", capsys=capsys)
    assert report["manifests"] == ["tests/mutations/test_guard.jsonl"]
    assert report["manifest_missing"] == ["tests/test_other.py"], "ملفُّ اختبارٍ مضاف بلا بيان"
    assert report["unmanifested_changed_tests"] == [] and report["unmanifested_new_tests"] == [] and report["totals"]["killed"] == 3
    assert report["status"] == "failed", "غيابُ البيان باسم الملفّ يُسقط المدى ولو سمّى اختباراتِه بيانٌ آخر"
    _manifest(repo, "test_other", {**KILL, "id": "other", "tests": ["tests/test_other.py::test_other"]})
    git("add", "-A")
    git("commit", "-qm", "manifest for other")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["manifest_missing"] == [] and report["status"] == "passed" and report["totals"]["killed"] == 4
    _clean(repo, git)


def test_a_new_test_in_an_existing_file_must_be_named_by_a_manifest(repo, git, capsys):
    """الحالةُ الشائعة: حارسٌ جديد في ملفٍّ قائم (ملاحظة Codex على #149) — يُسقط المدى حتى يسمّيه بيان؛ وتعديلُ ملفٍّ
    بلا دالّةٍ جديدة حدٌّ معلَن لا يُسقط إلا بـ--strict-unmanifested."""
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(TESTS + "\n\ndef test_more():\n    assert positive(0) is False\n")
    git("add", "-A")
    git("commit", "-qm", "add a guard to an existing file")
    rng = f"{base}..{git('rev-parse', 'HEAD')}"
    report = _run(repo, "--range", rng, capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::test_more"] and report["status"] == "failed"
    _manifest(repo, "test_guard", {**KILL, "id": "more", "tests": ["tests/test_guard.py::test_more"]})
    git("add", "-A")
    git("commit", "-qm", "its manifest")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == [] and report["status"] == "passed" and report["totals"]["killed"] == 1
    # تعديلٌ بلا دالّةٍ جديدة في ملفٍّ بلا بيان (أُضيف خارج المدى): حدٌّ معلَن، ويُسقط بالصرامة وحدها
    (repo / "tests/test_other.py").write_text("def test_other():\n    assert True\n")
    git("add", "-A")
    git("commit", "-qm", "a module without a manifest, outside the range")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_other.py").write_text("def test_other():\n    assert True\n\n# تعليق\n")
    git("add", "-A")
    git("commit", "-qm", "touch without a new guard")
    rng = f"{base}..{git('rev-parse', 'HEAD')}"
    report = _run(repo, "--range", rng, capsys=capsys)
    assert report["unmanifested_changed_tests"] == ["tests/test_other.py"] and report["unmanifested_new_tests"] == []
    assert report["status"] == "passed"
    assert _run(repo, "--range", rng, "--strict-unmanifested", capsys=capsys)["status"] == "failed"


def test_an_empty_manifest_does_not_satisfy_the_gate(repo, git, capsys):
    """بيانٌ فارغ كان يُحسب موجودًا فيمرّ ملفُّ اختبارٍ جديد بلا طفرة (ملاحظة Codex على #149)."""
    (repo / "tests/mutations/test_guard.jsonl").write_text("\n", encoding="utf-8")
    report = _run(repo, "--all", capsys=capsys)
    assert (report["status"], report["code"]) == ("refused", "manifest_invalid") and "بلا طفرة" in report["detail"]


def test_a_class_method_node_id_is_accepted_and_killed(repo, git, capsys):
    """حارسٌ داخل صنف اختبار (`file::Class::test`) يُقبل معرّفُه ويُقتل به (ملاحظة Codex على #149)."""
    (repo / "tests/test_guard.py").write_text(TESTS + "\n\nclass TestGuard:\n    def test_zero_in_class(self):\n        assert positive(0) is False\n")
    _manifest(repo, "test_guard", {**KILL, "id": "in-class", "tests": ["tests/test_guard.py::TestGuard::test_zero_in_class"]})
    git("add", "-A")
    git("commit", "-qm", "class guard")
    report = _run(repo, "--all", capsys=capsys)
    assert report["status"] == "passed" and report["results"][0]["code"] == "killed"


GROWN = TESTS.replace("    assert positive(0) is False\n", "    assert positive(0) is False\n    assert positive(-1) is False\n")


def test_a_guard_added_inside_an_existing_test_body_must_be_named_by_a_manifest(repo, git, capsys):
    """تأكيدٌ جديد داخل دالّةٍ قائمة حارسٌ جديد بلا `def` جديد (ملاحظة Codex على #149 بعد 7b19902): الاختبارُ الذي دخل
    سطرٌ مضاف في مداه يلزمه بيانٌ يسمّيه، ولا يُرجأ إلى --strict-unmanifested."""
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(GROWN)
    git("add", "-A")
    git("commit", "-qm", "grow a guard in place")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::test_zero_is_not_positive"] and report["status"] == "failed"
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    git("add", "-A")
    git("commit", "-qm", "its manifest")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == [] and report["status"] == "passed" and report["totals"]["killed"] == 1
    _clean(repo, git)


def test_the_manifest_that_names_a_touched_test_is_applied_in_the_range_even_when_it_did_not_change(repo, git, capsys):
    """الإثباتُ يُعاد في الطلب نفسِه: البيانُ الذي يسمّي اختبارًا مسّه المدى يُطبَّق ولو لم يتغيّر، فلا يُرجأ إلى الأسبوعيّ."""
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    git("add", "-A")
    git("commit", "-qm", "manifest first")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(GROWN)
    git("add", "-A")
    git("commit", "-qm", "grow the guard; the manifest is untouched")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["manifests"] == ["tests/mutations/test_guard.jsonl"] and report["totals"]["killed"] == 1
    assert report["status"] == "passed"
    _clean(repo, git)


def test_every_test_in_a_new_file_must_be_named_not_only_its_manifest_file(repo, git, capsys):
    """بيانٌ باسم الملفّ الجديد لا يكفي (ملاحظة Codex على #149 بعد 7b19902): كلُّ اختبارٍ فيه، ولو داخل صنف، يسمّيه بيان."""
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_other.py").write_text(
        "from pkg.guard import positive\n\n\ndef test_other_zero():\n    assert positive(0) is False\n\n\n"
        "class TestOther:\n    def test_negative(self):\n        assert positive(-1) is False\n")
    _manifest(repo, "test_other", {**KILL, "id": "old-only", "tests": ["tests/test_guard.py::test_zero_is_not_positive"]})
    git("add", "-A")
    git("commit", "-qm", "new file whose manifest names an old test only")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["manifest_missing"] == [] and report["totals"]["killed"] == 1
    assert report["unmanifested_new_tests"] == ["tests/test_other.py::TestOther::test_negative", "tests/test_other.py::test_other_zero"]
    assert report["status"] == "failed"
    _manifest(repo, "test_other", {**KILL, "id": "both", "new": "return x > -5",
                                   "tests": ["tests/test_other.py::test_other_zero", "tests/test_other.py::TestOther::test_negative"]})
    git("add", "-A")
    git("commit", "-qm", "name them")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == [] and report["status"] == "passed" and report["totals"]["killed"] == 1
    assert report["results"][0]["failed_tests"] == ["tests/test_other.py::test_other_zero", "tests/test_other.py::TestOther::test_negative"]
    _clean(repo, git)


def test_a_new_method_whose_name_collides_across_classes_is_not_covered_by_the_other_class_s_manifest(repo, git, capsys):
    """`TestA::test_same` مسمًّى؛ إضافةُ `TestB::test_same` لا تُغطّى به (ملاحظة Codex على #149 بعد 7b19902)."""
    (repo / "tests/test_guard.py").write_text(TESTS + "\n\nclass TestA:\n    def test_same(self):\n        assert positive(0) is False\n")
    _manifest(repo, "test_guard", {**KILL, "id": "a", "tests": ["tests/test_guard.py::TestA::test_same"]})
    git("add", "-A")
    git("commit", "-qm", "class A")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text((repo / "tests/test_guard.py").read_text()
                                              + "\n\nclass TestB:\n    def test_same(self):\n        assert positive(-1) is False\n")
    git("add", "-A")
    git("commit", "-qm", "class B with the same method name")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::TestB::test_same"] and report["status"] == "failed"
    _clean(repo, git)


def test_a_case_added_to_a_parametrize_decorator_touches_its_test(repo, git, capsys):
    """حالةٌ جديدة في مزخرف parametrize حارسٌ جديد: مدى الاختبار يبدأ من أول مزخرفٍ لا من سطر def."""
    decorated = TESTS + "\n\nimport pytest\n\n\n@pytest.mark.parametrize(\"x\", [\n    0,\n])\ndef test_not_positive(x):\n    assert positive(x) is False\n"
    (repo / "tests/test_guard.py").write_text(decorated)
    git("add", "-A")
    git("commit", "-qm", "decorated")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(decorated.replace("    0,\n", "    0,\n    -1,\n"))
    git("add", "-A")
    git("commit", "-qm", "a new case")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::test_not_positive"] and report["status"] == "failed"
    _clean(repo, git)


def test_a_case_added_to_a_parametrized_test_must_itself_fail_a_mutation_not_hide_behind_an_older_case(repo, git, capsys):
    """بيانٌ يسمّي `test[zero]` وحدها كان يُثبت الدالّةَ كلَّها بعد حذف المعامل، فتمرّ حالةُ `negative` المضافة وهي لم تسقط
    (ملاحظة Codex على #149)؛ صارت حالاتُ الدالّة الممسوسة كما يجمعها pytest تُثبَت حالةً حالة. وتسميةُ الدالّة بلا معامل
    تُشغّل حالاتِها كلَّها وتسجّل ما سقط منها بمعرّفه (كانت تُحكم invalid)."""
    decorated = TESTS + "\n\nimport pytest\n\n\n@pytest.mark.parametrize(\"x\", [\n    0,\n], ids=[\"zero\"])\ndef test_not_positive(x):\n    assert positive(x) is False\n"
    (repo / "tests/test_guard.py").write_text(decorated)
    _manifest(repo, "test_guard", {**KILL, "id": "kill", "tests": ["tests/test_guard.py::test_not_positive[zero]"]})
    git("add", "-A")
    git("commit", "-qm", "decorated and proved for zero")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(decorated.replace("    0,\n], ids=[\"zero\"]", "    0,\n    -1,\n], ids=[\"zero\", \"negative\"]"))
    git("add", "-A")
    git("commit", "-qm", "a new case that x >= 0 does not catch")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    (result,) = report["results"]
    assert result["code"] == "killed" and result["failed_tests"] == ["tests/test_guard.py::test_not_positive[zero]"]
    assert report["touched_cases"] == {"tests/test_guard.py::test_not_positive": [
        "tests/test_guard.py::test_not_positive[zero]", "tests/test_guard.py::test_not_positive[negative]"]}
    assert report["unproved_touched_tests"] == ["tests/test_guard.py::test_not_positive[negative]"] and report["status"] == "failed"
    _manifest(repo, "test_guard", {**KILL, "id": "kill", "tests": ["tests/test_guard.py::test_not_positive[zero]"]},
              {**KILL, "id": "kill-negative", "new": "return x > -5", "tests": ["tests/test_guard.py::test_not_positive"]})
    git("add", "-A")
    git("commit", "-qm", "a mutation the negative case catches, named without a parameter")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    second = report["results"][1]
    assert second["code"] == "killed" and second["failed_tests"] == [
        "tests/test_guard.py::test_not_positive[zero]", "tests/test_guard.py::test_not_positive[negative]"]
    assert report["unproved_touched_tests"] == [] and report["status"] == "passed"
    _clean(repo, git)


def test_a_symlink_target_is_refused_before_any_mutation(repo, git, capsys):
    """وصلةٌ رمزية باسم شيفرة إنتاج إلى اختبارٍ كانت تُتبع فتُطفَّر الاختبارُ نفسُه وتُحسب قتلًا (ملاحظة Codex على #149)."""
    (repo / "tools").mkdir()
    (repo / "tools/link.py").symlink_to("../tests/test_guard.py")
    _manifest(repo, "test_guard", {**KILL, "file": "tools/link.py", "old": "assert True", "new": "assert False",
                                   "tests": ["tests/test_guard.py::test_always_passes"]})
    git("add", "-A")
    git("commit", "-qm", "link")
    report = _run(repo, "--all", capsys=capsys)
    assert (report["status"], report["code"], report["exit_code"]) == ("refused", "target_refused", 2)
    assert "وصلة" in report["detail"] and "results" not in report
    _clean(repo, git)


def test_a_renamed_test_file_is_inspected_like_an_added_one(repo, git, capsys):
    """إعادةُ تسمية ملفّ اختبارٍ مع إضافة حارسٍ كانت تخرج من المدى كلِّه (ملاحظة Codex على #149): الوجهةُ تُعامل كالمضاف."""
    base = git("rev-parse", "HEAD")
    git("mv", "tests/test_guard.py", "tests/test_guardian.py")
    (repo / "tests/test_guardian.py").write_text(TESTS + "\n\ndef test_more():\n    assert positive(0) is False\n")
    git("add", "-A")
    git("commit", "-qm", "rename and add a guard")
    assert git("diff", "--name-status", "--diff-filter=R", f"{base}..HEAD").startswith("R"), "ليست إعادةَ تسمية عند git"
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["manifest_missing"] == ["tests/test_guardian.py"] and report["status"] == "failed"
    assert report["unmanifested_new_tests"] == [f"tests/test_guardian.py::{name}" for name in
                                                ("test_already_failing", "test_always_passes", "test_more", "test_zero_is_not_positive")]


def test_a_touched_test_must_itself_fail_a_mutation_not_merely_be_listed_beside_a_killer(repo, git, capsys):
    """ذكرُ الاختبار الجديد بجانب اختبارٍ قاتل في السطر نفسِه كان يجعله «مسمًّى» ويمرّ وهو لم يسقط قطّ (ملاحظة Codex على #149)."""
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(TESTS + "\n\ndef test_more():\n    assert True\n")
    _manifest(repo, "test_guard", {**KILL, "id": "kill-and-tag-along", "tests": [*KILL["tests"], "tests/test_guard.py::test_more"]})
    git("add", "-A")
    git("commit", "-qm", "tag along")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    (result,) = report["results"]
    assert result["code"] == "killed" and result["named_but_passed"] == ["tests/test_guard.py::test_more"]
    assert report["unmanifested_new_tests"] == [] and report["unproved_touched_tests"] == ["tests/test_guard.py::test_more"]
    assert report["status"] == "failed"
    (repo / "tests/test_guard.py").write_text(TESTS + "\n\ndef test_more():\n    assert positive(0) is False\n")
    git("add", "-A")
    git("commit", "-qm", "a real guard")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unproved_touched_tests"] == [] and report["status"] == "passed"
    _clean(repo, git)


def test_a_test_module_in_a_nested_directory_is_scanned_and_its_manifest_is_named_by_its_path(repo, git, capsys):
    """tests/unit/test_x.py كان خارج مرشّح tests/test_*.py فيمرّ بلا بيان (ملاحظة Codex على #149)."""
    base = git("rev-parse", "HEAD")
    (repo / "tests/unit").mkdir()
    (repo / "tests/unit/test_nested.py").write_text("from pkg.guard import positive\n\n\ndef test_nested_zero():\n    assert positive(0) is False\n")
    git("add", "-A")
    git("commit", "-qm", "nested")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["manifest_missing"] == ["tests/unit/test_nested.py"] and report["status"] == "failed"
    assert report["unmanifested_new_tests"] == ["tests/unit/test_nested.py::test_nested_zero"]
    assert mc.manifest_for("tests/unit/test_nested.py") == "tests/mutations/unit__test_nested.jsonl"
    _manifest(repo, "unit__test_nested", {**KILL, "id": "nested", "tests": ["tests/unit/test_nested.py::test_nested_zero"]})
    git("add", "-A")
    git("commit", "-qm", "its manifest")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1
    _clean(repo, git)


def test_deleting_a_manifest_whose_module_remains_fails_the_range(repo, git, capsys):
    """حذفُ البيان وحده كان يخرج من المدى فتفقد الحرّاسُ إثباتَها صامتة (ملاحظة Codex على #149)؛ يُقبل مع حذف الوحدة."""
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    git("add", "-A")
    git("commit", "-qm", "manifest")
    base = git("rev-parse", "HEAD")
    (repo / "tests/mutations/test_guard.jsonl").unlink()
    git("add", "-A")
    git("commit", "-qm", "drop the manifest only")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["orphaned_manifests"] == ["tests/mutations/test_guard.jsonl"] and report["status"] == "failed"
    (repo / "tests/test_guard.py").unlink()
    git("add", "-A")
    git("commit", "-qm", "drop the module too")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["orphaned_manifests"] == [] and report["status"] == "passed"


def test_a_unittest_subclass_is_collected_whatever_its_name_and_a_base_imported_under_another_name_is_a_declared_limit(repo, git, capsys):
    """صنفٌ يرث unittest.TestCase واسمُه لا يبدأ بـTest كان خارج الأصناف المقروءة فتمرّ حرّاسُه بلا بيانٍ ولا إثبات (ملاحظة Codex
    على #149)؛ والوارثُ منه في الوحدة نفسِها مثلُه. والصنفُ العاديّ لا يجمعه pytest ولا تراه الأداة؛ والوارثُ أصلًا مستوردًا
    باسمٍ لا ينتهي بـTestCase يجمعه pytest ولا تراه الأداة — حدٌّ معلَن باسمه."""
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    git("add", "-A")
    git("commit", "-qm", "manifest")
    base = git("rev-parse", "HEAD")
    (repo / "tests/base.py").write_text("import unittest\n\n\nclass Base(unittest.TestCase):\n    pass\n")
    (repo / "tests/test_guard.py").write_text(TESTS + """

import unittest

from base import Base


class GuardCase(unittest.TestCase):
    def test_case_zero(self):
        assert positive(0) is False


class Derived(GuardCase):
    def test_derived_zero(self):
        assert positive(0) is False


class Helper:
    def test_never_collected(self):
        assert False


class FromImport(Base):
    def test_imported_base(self):
        assert positive(0) is False
""")
    git("add", "-A")
    git("commit", "-qm", "unittest classes")
    head = git("rev-parse", "HEAD")
    report = _run(repo, "--range", f"{base}..{head}", capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::Derived::test_derived_zero",
                                                "tests/test_guard.py::GuardCase::test_case_zero"]
    assert report["status"] == "failed"
    assert "tests/test_guard.py::FromImport::test_imported_base" not in report["unmanifested_new_tests"], "الحدُّ المعلَن أُغلق: حدِّث اسمه"
    named = [*KILL["tests"], "tests/test_guard.py::GuardCase::test_case_zero", "tests/test_guard.py::Derived::test_derived_zero"]
    _manifest(repo, "test_guard", {**KILL, "id": "kill", "tests": named})
    git("add", "-A")
    git("commit", "-qm", "named")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    (result,) = report["results"]
    assert result["code"] == "killed" and result["failed_tests"] == named
    assert report["unproved_touched_tests"] == [] and report["status"] == "passed"
    _clean(repo, git)


def test_a_manifest_is_matched_to_its_module_forward_so_double_underscores_in_a_module_name_cannot_hide_an_orphan(repo, git, capsys):
    """عكسُ الاسم (__ → /) كان يحوّل test_a__b.jsonl إلى tests/test_a/b.py فلا يُرى حذفُ بيان tests/test_a__b.py يتيمًا
    (ملاحظة Codex على #149)؛ صار البيانُ يُنسب إلى وحدته بالاتجاه الأمامي على الوحدات الموجودة عند الرأس."""
    (repo / "tests/test_a__b.py").write_text("from pkg.guard import positive\n\n\ndef test_ab_zero():\n    assert positive(0) is False\n")
    _manifest(repo, "test_a__b", {**KILL, "id": "ab", "tests": ["tests/test_a__b.py::test_ab_zero"]})
    git("add", "-A")
    git("commit", "-qm", "module and manifest")
    base = git("rev-parse", "HEAD")
    (repo / "tests/mutations/test_a__b.jsonl").unlink()
    git("add", "-A")
    git("commit", "-qm", "drop the manifest only")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["orphaned_manifests"] == ["tests/mutations/test_a__b.jsonl"] and report["status"] == "failed"
    _clean(repo, git)


def test_two_modules_whose_manifest_names_collide_are_refused_before_any_worktree(repo, git, capsys):
    """tests/test_unit/test_x.py وtests/test_unit__test_x.py يؤولان إلى بيانٍ واحد (test_unit__test_x.jsonl) فيدّعي أحدُهما
    إثباتَ الآخر؛ يُرفضان باسمهما قبل أيّ شجرة عمل."""
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_unit").mkdir()
    (repo / "tests/test_unit/test_x.py").write_text("def test_x():\n    assert True\n")
    (repo / "tests/test_unit__test_x.py").write_text("def test_y():\n    assert True\n")
    git("add", "-A")
    git("commit", "-qm", "colliding modules")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["status"] == "refused" and report["code"] == "manifest_name_collision"
    assert "tests/test_unit/test_x.py" in report["detail"] and "tests/test_unit__test_x.py" in report["detail"]
    _clean(repo, git)


def test_a_collection_error_is_invalid_not_a_kill(repo, capsys, git):
    _manifest(repo, "test_guard", {**KILL, "id": "broken", "new": "return x > 0 ("})
    git("add", "-A")
    git("commit", "-qm", "manifest")
    report = _run(repo, "--all", capsys=capsys)
    (result,) = report["results"]
    assert result["code"] == "invalid" and result["pytest_exit"] not in (0, 1)
    _clean(repo, git)


def test_uncommitted_changes_are_not_measured_only_the_head(repo, capsys, git):
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    git("add", "-A")
    git("commit", "-qm", "manifest")
    (repo / "pkg/guard.py").write_text("def positive(x):\n    return x > 1\n")      # تغييرٌ غيرُ مودَع لا يُرى
    report = _run(repo, "--all", capsys=capsys)
    assert report["results"][0]["code"] == "killed"
    assert (repo / "pkg/guard.py").read_text() == "def positive(x):\n    return x > 1\n", "الأداةُ مسّت شجرةَ العمل"


def test_the_repository_manifests_are_valid_and_target_only_production_code():
    """بياناتُ المستودع نفسِه تُحمَّل بلا رفض، وكلُّ هدفٍ فيها شيفرةُ إنتاجٍ موجودة."""
    paths = sorted((ROOT / mc.MANIFESTS).glob("*.jsonl"))
    assert paths, "لا بياناتَ في المستودع"
    owned = {mc.manifest_for(m.relative_to(ROOT).as_posix()) for m in (ROOT / "tests").rglob("test_*.py")}
    for path in paths:
        assert path.relative_to(ROOT).as_posix() in owned, f"{path.name} بلا وحدة اختبارٍ يؤول بيانُها إليه"
        for entry in mc.load_manifest(path, ROOT):
            target = ROOT / entry["file"]
            assert target.is_file(), entry["file"]
            assert target.read_text(encoding="utf-8").count(entry["old"]) == entry["count"], (path.name, entry.get("id"))
