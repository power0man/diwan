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
    ({**KILL, "tests": []}, "tests فارغة"), ({**KILL, "tests": ["tests/test_guard.py::not a node id"]}, "معرّفٌ غيرُ صالح"),
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
    """ملفُّ اختبارٍ مضاف بلا بيانٍ باسمه يُسمّى في manifest_missing، واختباراتُه بلا بيان وحدتها في unmanifested_new_tests."""
    base = git("rev-parse", "HEAD")
    _manifest(repo, "test_guard", {**KILL, "id": "kill"}, {**KILL, "id": "more", "tests": ["tests/test_guard.py::test_more"]})
    (repo / "tests/test_other.py").write_text("from pkg.guard import positive\n\n\ndef test_other():\n    assert positive(0) is False\n")
    (repo / "tests/test_guard.py").write_text(TESTS + "\n\ndef test_more():\n    assert positive(0) is False\n")
    git("add", "-A")
    git("commit", "-qm", "range")
    head = git("rev-parse", "HEAD")
    report = _run(repo, "--range", f"{base}..{head}", capsys=capsys)
    assert report["manifests"] == ["tests/mutations/test_guard.jsonl"]
    assert report["manifest_missing"] == ["tests/test_other.py"], "ملفُّ اختبارٍ مضاف بلا بيان"
    assert report["unmanifested_changed_tests"] == [] and report["unmanifested_new_tests"] == ["tests/test_other.py::test_other"]
    assert report["totals"]["killed"] == 2 and report["status"] == "failed", "غيابُ بيان الوحدة يُسقط المدى"
    _manifest(repo, "test_other", {**KILL, "id": "other", "tests": ["tests/test_other.py::test_other"]})
    git("add", "-A")
    git("commit", "-qm", "manifest for other")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["manifest_missing"] == [] and report["unmanifested_new_tests"] == []
    assert report["status"] == "passed" and report["totals"]["killed"] == 3
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
    """بيانٌ باسم الملفّ الجديد لا يكفي (ملاحظة Codex على #149 بعد 7b19902): كلُّ اختبارٍ فيه، ولو داخل صنف، يسمّيه بيان
    الوحدة بمعرّفه الكامل، فبيانٌ يسمّي أحدَ اختباري الملفّ لا يغطّي الآخر."""
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_other.py").write_text(
        "from pkg.guard import positive\n\n\ndef test_other_zero():\n    assert positive(0) is False\n\n\n"
        "class TestOther:\n    def test_negative(self):\n        assert positive(-1) is False\n")
    _manifest(repo, "test_other", {**KILL, "id": "one-only", "tests": ["tests/test_other.py::test_other_zero"]})
    git("add", "-A")
    git("commit", "-qm", "new file whose manifest names one of its two tests")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["manifest_missing"] == [] and report["totals"]["killed"] == 1
    assert report["unmanifested_new_tests"] == ["tests/test_other.py::TestOther::test_negative"]
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
    """حالةٌ جديدة في مزخرف parametrize حارسٌ جديد (يستجدّ في الجمع)؛ وقيمةُ حالةٍ قائمة تتغيّر بمعرّفها نفسِه لا تستجدّ في
    الجمع، فيمسّها السطرُ المضاف في المزخرف: مدى الاختبار يبدأ من أول مزخرفٍ لا من سطر def."""
    decorated = TESTS + "\n\nimport pytest\n\n\n@pytest.mark.parametrize(\"x\", [\n    pytest.param(0, id=\"zero\"),\n])\ndef test_not_positive(x):\n    assert positive(x) is False\n"
    (repo / "tests/test_guard.py").write_text(decorated)
    git("add", "-A")
    git("commit", "-qm", "decorated")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(decorated.replace("    pytest.param(0, id=\"zero\"),\n", "    pytest.param(0, id=\"zero\"),\n    pytest.param(-1, id=\"negative\"),\n"))
    git("add", "-A")
    git("commit", "-qm", "a new case")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::test_not_positive"] and report["status"] == "failed"
    git("checkout", "-q", base)
    git("checkout", "-q", "-b", "same-id")
    (repo / "tests/test_guard.py").write_text(decorated.replace("    pytest.param(0, id=\"zero\"),\n", "    pytest.param(-3, id=\"zero\"),\n"))
    git("add", "-A")
    git("commit", "-qm", "the same case id with another value: only the decorator line changed")
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
    assert result["code"] == "partially_killed" and result["named_but_passed"] == ["tests/test_guard.py::test_more"]
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


def test_removing_or_narrowing_a_manifest_row_whose_test_remains_drops_its_proof_and_fails_the_range(repo, git, capsys):
    """حذفُ سطرٍ واحد من بيانٍ باقٍ كان يمرّ لأن المدى يقرأ نسخةَ الرأس وحدها ولا يقارنها بأصل الدمج، فيفقد الحارسُ إثباتَه
    صامتًا (ملاحظة Codex على #149)؛ يُقبل إن سمّاه سطرٌ آخر (ولو باسم دالّته)، أو زالت الحالةُ من الجمع، أو زالت الدالّة."""
    decorated = TESTS + "\n\nimport pytest\n\n\n@pytest.mark.parametrize(\"x\", [\n    pytest.param(0, id=\"zero\"),\n    pytest.param(-1, id=\"negative\"),\n])\ndef test_not_positive(x):\n    assert positive(x) is False\n"
    (repo / "tests/test_guard.py").write_text(decorated)
    negative = {**KILL, "id": "kill-negative", "new": "return x > -5", "tests": ["tests/test_guard.py::test_not_positive[negative]"]}
    _manifest(repo, "test_guard", {**KILL, "id": "kill"}, negative)
    git("add", "-A")
    git("commit", "-qm", "guards and their manifest")
    base = git("rev-parse", "HEAD")
    rng = lambda: f"{base}..{git('rev-parse', 'HEAD')}"
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})                     # السطرُ الثاني حُذف والحالةُ باقية
    git("add", "-A")
    git("commit", "-qm", "drop one row only")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["dropped_proofs"] == ["tests/test_guard.py::test_not_positive[negative]"] and report["status"] == "failed"
    assert report["manifests"] == ["tests/mutations/test_guard.jsonl"] and report["totals"]["killed"] == 1
    _manifest(repo, "test_guard", {**KILL, "id": "kill"}, {**negative, "id": "kill-all", "tests": ["tests/test_guard.py::test_not_positive"]})
    git("add", "-A")
    git("commit", "-qm", "another row names the function, so every case")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["dropped_proofs"] == [] and report["status"] == "passed" and report["totals"]["killed"] == 2
    (repo / "tests/test_guard.py").write_text(decorated.replace("    pytest.param(-1, id=\"negative\"),\n", ""))
    _manifest(repo, "test_guard", {**KILL, "id": "kill"}, {**negative, "id": "kill-zero", "tests": ["tests/test_guard.py::test_not_positive[zero]"]})
    git("add", "-A")
    git("commit", "-qm", "the negative case is gone and the row names the remaining one")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["dropped_proofs"] == [] and report["status"] == "passed"
    assert report["revalidated_tests"] == ["tests/test_guard.py::test_not_positive"] and report["totals"]["killed"] == 2
    (repo / "tests/test_guard.py").write_text((repo / "tests/test_guard.py").read_text().replace(
        "def test_zero_is_not_positive():\n    assert positive(0) is False\n\n\n", ""))
    _manifest(repo, "test_guard", {**negative, "id": "kill-zero", "tests": ["tests/test_guard.py::test_not_positive[zero]"]})
    git("add", "-A")
    git("commit", "-qm", "the function is gone with its row")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["dropped_proofs"] == [] and report["status"] == "passed" and report["totals"]["killed"] == 1
    _clean(repo, git)


def test_an_inherited_test_method_is_touched_when_its_base_grows_and_a_new_heir_is_collected(repo, git, capsys):
    """وارثٌ بلا تعريف (`class TestAgain(TestBase)`) يجمع له pytest `TestAgain::test_value` والقراءةُ كانت ترى جسمَ الوارث وحده
    (ملاحظة Codex على #149): صارت الموروثةُ تُوضع للوارث بمدى تعريفها ورأسِه، فسطرٌ مضاف في دالّة الأصل يمسّها في كلِّ وارثٍ
    قائم؛ والوارثُ الجديد — ولو من أصلٍ مستورد — يُمسّ لأنه استجدّ في جمع pytest."""
    based = TESTS + "\n\nclass TestBase:\n    def test_value(self):\n        assert positive(0) is False\n\n\nclass TestAgain(TestBase):\n    note = \"inherits test_value untouched\"\n"
    (repo / "tests/test_guard.py").write_text(based)
    row = {**KILL, "id": "kill-base", "tests": ["tests/test_guard.py::TestBase::test_value"]}
    _manifest(repo, "test_guard", {**KILL, "id": "kill"}, row)
    git("add", "-A")
    git("commit", "-qm", "a base class guard, an heir, and a manifest naming the base only")
    base = git("rev-parse", "HEAD")
    rng = lambda: f"{base}..{git('rev-parse', 'HEAD')}"
    grown = based.replace("        assert positive(0) is False\n", "        assert positive(0) is False\n        assert positive(-1) is False\n", 1)
    (repo / "tests/test_guard.py").write_text(grown)
    git("add", "-A")
    git("commit", "-qm", "the base method grows, so the heir's inherited guard is touched too")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::TestAgain::test_value"] and report["status"] == "failed"
    assert report["touched_cases"] == {"tests/test_guard.py::TestAgain::test_value": ["tests/test_guard.py::TestAgain::test_value"],
                                       "tests/test_guard.py::TestBase::test_value": ["tests/test_guard.py::TestBase::test_value"]}
    _manifest(repo, "test_guard", {**KILL, "id": "kill"}, {**row, "tests": [*row["tests"], "tests/test_guard.py::TestAgain::test_value"]})
    git("add", "-A")
    git("commit", "-qm", "the heir's inherited guard is named")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 2 and report["unproved_touched_tests"] == []
    (repo / "tests/shared.py").write_text("from pkg.guard import positive\n\n\nclass Shared:\n    def test_shared(self):\n        assert positive(0) is False\n")
    (repo / "tests/test_guard.py").write_text(grown + "\n\nfrom shared import Shared\n\n\nclass TestMore(TestBase):\n    pass\n\n\nclass TestImported(Shared):\n    pass\n")
    git("add", "-A")
    git("commit", "-qm", "two new heirs, one of an imported base: both collected, so both touched")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::TestImported::test_shared", "tests/test_guard.py::TestMore::test_value"]
    assert report["status"] == "failed"
    _clean(repo, git)


def test_a_manifest_renamed_onto_another_module_leaves_its_source_module_an_orphan(repo, git, capsys):
    """بيانٌ نُقل باسم وحدةٍ أخرى وعُدّل لها يصنّفه git إعادةَ تسمية (R) لا حذفًا، فكان خارج فحص اليتيم (D) وفحص فقد الإثبات (M)،
    فيمرّ المدى ومصدرُه باقٍ بحرّاسه بلا بيان (ملاحظة Codex على #149)؛ صار الفرقُ بلا كشف إعادة التسمية فالمصدرُ محذوفٌ يتيم."""
    (repo / "tests/test_other.py").write_text("from pkg.guard import positive\n\n\ndef test_other():\n    assert positive(0) is False\n")
    why = "حارسُ الصفر لا يُخفّف إلى ≥: " + "الصفرُ ليس موجبًا، والطفرةُ تجعله موجبًا فيسقط الحارسُ الذي يشهد بذلك. " * 6
    # نصٌّ طويل و`tests` آخرَ المفاتيح، ليرى git النقلَ المعدَّل إعادةَ تسمية (تشابهُه يُحسب بمقاطع السطر من أوّله)
    row = {"id": "kill", "file": KILL["file"], "old": KILL["old"], "new": KILL["new"], "task": "ك٠", "why": why, "added": "2026-09-27",
           "tests": KILL["tests"]}
    _manifest(repo, "test_guard", row)
    git("add", "-A")
    git("commit", "-qm", "two modules, one manifest")
    base = git("rev-parse", "HEAD")
    git("mv", "tests/mutations/test_guard.jsonl", "tests/mutations/test_other.jsonl")
    _manifest(repo, "test_other", {**row, "tests": ["tests/test_other.py::test_other"]})
    git("add", "-A")
    git("commit", "-qm", "renamed onto the other module and edited for it")
    assert git("diff", "--name-status", f"{base}...HEAD", "--", "tests/mutations/").startswith("R"), "git لا يراها إعادةَ تسمية"
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["orphaned_manifests"] == ["tests/mutations/test_guard.jsonl"] and report["status"] == "failed"
    assert report["manifests"] == ["tests/mutations/test_other.jsonl"] and report["totals"]["killed"] == 1
    _clean(repo, git)


def test_a_shadowing_attribute_is_not_collected_and_an_attribute_alias_is_collected_as_pytest_decides(repo, git, capsys):
    """`test_value = None` في الوارث يحجب الموروثةَ فلا يجمعها pytest، والقراءةُ كانت تخترع `TestChild::test_value` فلا يُرضى
    المدى (ملاحظة Codex على #149)؛ صار جمعُ pytest هو الحكمَ: المحجوبةُ لا تُمسّ، والسمةُ المنسوبة دالّةً تُمسّ لأنها استجدّت."""
    based = TESTS + "\n\nclass TestBase:\n    def test_value(self):\n        assert positive(0) is False\n"
    (repo / "tests/test_guard.py").write_text(based)
    row = {**KILL, "id": "kill-base", "tests": ["tests/test_guard.py::TestBase::test_value"]}
    _manifest(repo, "test_guard", {**KILL, "id": "kill"}, row)
    git("add", "-A")
    git("commit", "-qm", "a base class guard")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(based + "\n\ndef _zero(self):\n    assert positive(0) is False\n\n\n"
                                              "class TestChild(TestBase):\n    test_value = None\n\n\nclass TestAlias(TestBase):\n    test_alias = _zero\n")
    git("add", "-A")
    git("commit", "-qm", "a shadowing heir and an aliasing one")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::TestAlias::test_alias", "tests/test_guard.py::TestAlias::test_value"]
    assert "tests/test_guard.py::TestChild::test_value" not in report["touched_cases"] and report["status"] == "failed"
    _manifest(repo, "test_guard", {**KILL, "id": "kill"},
              {**row, "tests": [*row["tests"], "tests/test_guard.py::TestAlias::test_alias", "tests/test_guard.py::TestAlias::test_value"]})
    git("add", "-A")
    git("commit", "-qm", "named")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 2 and report["unproved_touched_tests"] == []
    _clean(repo, git)


def test_a_module_level_alias_of_a_class_or_a_function_is_collected_and_a_none_assignment_uncollects(repo, git, capsys):
    """`TestAlias = Helper` في مستوى الوحدة يجمع له pytest `TestAlias::test_*` بكلِّ دوالّ Helper، و`test_alias = _zero` يجمعه
    دالّةً، والقراءةُ كانت ترى التعريفات وحدها فلا يُمسّ شيء (ملاحظة Codex على #149)؛ صار جمعُ pytest هو الحكمَ، و`test_x = None`
    بعد تعريفها يُخرجها من الجمع فتُعاد بياناتُها كالمحذوفة."""
    based = TESTS + "\n\nclass Helper:\n    def test_zero_again(self):\n        assert positive(0) is False\n\n\ndef _zero():\n    assert positive(0) is False\n"
    (repo / "tests/test_guard.py").write_text(based)
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    git("add", "-A")
    git("commit", "-qm", "a helper class and a helper function, neither collected")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(based + "\n\nTestAlias = Helper\ntest_alias = _zero\ntest_always_passes = None\n")
    git("add", "-A")
    git("commit", "-qm", "aliases and a shadowing assignment")
    rng = lambda: f"{base}..{git('rev-parse', 'HEAD')}"
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::TestAlias::test_zero_again", "tests/test_guard.py::test_alias"]
    assert report["revalidated_tests"] == ["tests/test_guard.py::test_always_passes"] and report["status"] == "failed"
    _manifest(repo, "test_guard", {**KILL, "id": "kill", "tests": [*KILL["tests"], "tests/test_guard.py::TestAlias::test_zero_again",
                                                                    "tests/test_guard.py::test_alias"]})
    git("add", "-A")
    git("commit", "-qm", "named")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1 and report["unproved_touched_tests"] == []
    _clean(repo, git)


def test_an_imported_test_function_or_class_is_collected_in_the_importing_module(repo, git, capsys):
    """`from pkg.helper import test_guard` و`from shared import TestShared` في وحدة اختبارٍ قائمة يجمعهما pytest تحتها
    (ملاحظة Codex على #149)، والقراءةُ لا ترى الاستيراد؛ صار ما استجدّ في جمع pytest ممسوسًا أيًّا كان مصدرُه."""
    (repo / "pkg/helper.py").write_text("from pkg.guard import positive\n\n\ndef test_guard():\n    assert positive(0) is False\n")
    (repo / "tests/shared.py").write_text("from pkg.guard import positive\n\n\nclass TestShared:\n    def test_shared(self):\n        assert positive(0) is False\n")
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    git("add", "-A")
    git("commit", "-qm", "helpers that are not collected where they live")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(TESTS + "\n\nfrom pkg.helper import test_guard\nfrom shared import TestShared\n")
    git("add", "-A")
    git("commit", "-qm", "imported into the test module, so collected there")
    rng = lambda: f"{base}..{git('rev-parse', 'HEAD')}"
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::TestShared::test_shared", "tests/test_guard.py::test_guard"]
    assert report["status"] == "failed"
    _manifest(repo, "test_guard", {**KILL, "id": "kill", "tests": [*KILL["tests"], "tests/test_guard.py::TestShared::test_shared", "tests/test_guard.py::test_guard"]})
    git("add", "-A")
    git("commit", "-qm", "named")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1 and report["unproved_touched_tests"] == []
    _clean(repo, git)


def test_a_guard_reaching_an_unchanged_module_through_a_changed_helper_is_collected(repo, git, capsys):
    """وحدةُ اختبارٍ لم تتغيّر فيها `from pkg.helpers import *`، والطلبُ يضيف `test_new_guard` إلى المساعد وحده: يجمعه pytest تحت
    الوحدة التي لم تتغيّر، والجمعُ المحصور في الوحدات المتغيّرة لا يراه (ملاحظة Codex على #149)؛ صار الجمعُ للمجموعة كلِّها."""
    (repo / "pkg/helpers.py").write_text("from pkg.guard import positive\n\n\ndef helper():\n    return positive\n")
    (repo / "tests/test_existing.py").write_text("from pkg.helpers import *\n\n\ndef test_existing():\n    assert positive(0) is False\n")
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    _manifest(repo, "test_existing", {**KILL, "id": "kill", "tests": ["tests/test_existing.py::test_existing"]})
    git("add", "-A")
    git("commit", "-qm", "a star-importing module with its own manifest")
    base = git("rev-parse", "HEAD")
    (repo / "pkg/helpers.py").write_text("from pkg.guard import positive\n\n\ndef helper():\n    return positive\n\n\ndef test_new_guard():\n    assert positive(0) is False\n")
    git("add", "-A")
    git("commit", "-qm", "the helper alone gains an exported guard")
    rng = lambda: f"{base}..{git('rev-parse', 'HEAD')}"
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_existing.py::test_new_guard"] and report["status"] == "failed"
    _manifest(repo, "test_existing", {**KILL, "id": "kill", "tests": ["tests/test_existing.py::test_existing", "tests/test_existing.py::test_new_guard"]})
    git("add", "-A")
    git("commit", "-qm", "named in the importing module's manifest")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1 and report["unproved_touched_tests"] == []
    _clean(repo, git)


def test_a_parameter_case_generated_outside_the_module_touches_its_test_and_must_itself_be_proved(repo, git, capsys):
    """حالةٌ تُضاف إلى اختبارٍ قائم من `pytest_generate_tests` في conftest وحده: الوحدةُ لم تتغيّر، والجمعُ المردودُ إلى الدوالّ قبل
    المقارنة لا يراها (ملاحظة Codex على #149)؛ صارت المقارنةُ بالمعرّفات الكاملة فتمسّ الحالةُ الجديدة دالّتَها وتُثبَت هي نفسُها."""
    (repo / "tests/test_guard.py").write_text(TESTS + "\n\ndef test_sign(x):\n    assert positive(x) is False\n")
    (repo / "tests/conftest.py").write_text("def pytest_generate_tests(metafunc):\n    if \"x\" in metafunc.fixturenames:\n        metafunc.parametrize(\"x\", [0], ids=[\"zero\"])\n")
    _manifest(repo, "test_guard", {**KILL, "id": "kill", "tests": [*KILL["tests"], "tests/test_guard.py::test_sign"]})
    git("add", "-A")
    git("commit", "-qm", "a generated single case, proved by the manifest")
    base = git("rev-parse", "HEAD")
    rng = lambda: f"{base}..{git('rev-parse', 'HEAD')}"
    (repo / "tests/conftest.py").write_text("def pytest_generate_tests(metafunc):\n    if \"x\" in metafunc.fixturenames:\n        metafunc.parametrize(\"x\", [0, -1], ids=[\"zero\", \"negative\"])\n")
    git("add", "-A")
    git("commit", "-qm", "conftest alone adds the negative case")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["touched_cases"] == {"tests/test_guard.py::test_sign": ["tests/test_guard.py::test_sign[zero]", "tests/test_guard.py::test_sign[negative]"]}
    assert report["totals"]["partially_killed"] == 1 and report["status"] == "failed"        # [negative] لا تسقط تحت x >= 0
    _manifest(repo, "test_guard", {**KILL, "id": "kill"},
              {**KILL, "id": "kill-sign", "new": "return x > -5", "tests": ["tests/test_guard.py::test_sign"]})
    git("add", "-A")
    git("commit", "-qm", "a mutation every generated case detects")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 2 and report["unproved_touched_tests"] == []
    _clean(repo, git)


def test_an_edit_inside_an_imported_test_implementation_touches_the_importing_module_s_test(repo, git, capsys):
    """اختبارٌ معرَّف في `tests/helpers.py` ومستورَدٌ في وحدةٍ لم تتغيّر: تأكيدٌ يُضاف إلى جسمه هناك لا يغيّر معرّفَه المجموع ولا
    يدخل المساعدُ في الوحدات المعدَّلة (ملاحظة Codex على #149)؛ صار كلُّ اختبارٍ مجموع يُردّ إلى الدالّة التي تعرّفه بملفّها ومداها
    كما يراها pytest، فيمسّه السطرُ المضاف هناك، وحذفُها يُعيد بياناتِه."""
    (repo / "tests/helpers.py").write_text("from pkg.guard import positive\n\n\ndef test_imported():\n    assert positive(0) is False\n")
    (repo / "tests/test_existing.py").write_text("from helpers import test_imported  # noqa: F401\nfrom pkg.guard import positive\n\n\ndef test_existing():\n    assert positive(0) is False\n")
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    _manifest(repo, "test_existing", {**KILL, "id": "kill", "tests": ["tests/test_existing.py::test_existing"]})
    git("add", "-A")
    git("commit", "-qm", "an imported test, named nowhere")
    base = git("rev-parse", "HEAD")
    rng = lambda: f"{base}..{git('rev-parse', 'HEAD')}"
    (repo / "tests/helpers.py").write_text("from pkg.guard import positive\n\n\ndef test_imported():\n    assert positive(0) is False\n    assert positive(-1) is False\n")
    git("add", "-A")
    git("commit", "-qm", "a guard grows inside the helper alone")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_existing.py::test_imported"] and report["status"] == "failed"
    _manifest(repo, "test_existing", {**KILL, "id": "kill", "tests": ["tests/test_existing.py::test_existing", "tests/test_existing.py::test_imported"]})
    git("add", "-A")
    git("commit", "-qm", "named in the importing module's manifest")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1 and report["unproved_touched_tests"] == []
    _clean(repo, git)


def test_a_helper_defining_the_same_test_name_in_two_classes_keeps_every_span(repo, git, capsys):
    """مساعدٌ يعرّف `test_same` في صنفين مستورَدين في وحدةٍ لم تتغيّر: قراءةٌ بالاسم تحتفظ بآخر تعريفٍ وحده لا ترى تأكيدًا يُضاف
    إلى الأول (ملاحظة Codex على #149)؛ والردُّ إلى الأصل يضع كلَّ مجموعٍ في مدى تعريفه هو، فيُمسّ اختبارُ الصنف الأول وحده."""
    (repo / "tests/helpers.py").write_text("from pkg.guard import positive\n\n\nclass TestA:\n    def test_same(self):\n        assert positive(0) is False\n\n\n"
                                          "class TestB:\n    def test_same(self):\n        assert positive(0) is False\n")
    (repo / "tests/test_existing.py").write_text("from helpers import TestA, TestB  # noqa: F401\n")
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    _manifest(repo, "test_existing", {**KILL, "id": "kill", "tests": ["tests/test_existing.py::TestA::test_same", "tests/test_existing.py::TestB::test_same"]})
    git("add", "-A")
    git("commit", "-qm", "two imported classes with the same test name, both named")
    base = git("rev-parse", "HEAD")
    rng = lambda: f"{base}..{git('rev-parse', 'HEAD')}"
    (repo / "tests/helpers.py").write_text("from pkg.guard import positive\n\n\nclass TestA:\n    def test_same(self):\n        assert positive(0) is False\n        assert positive(-1) is False\n\n\n"
                                          "class TestB:\n    def test_same(self):\n        assert positive(0) is False\n")
    _manifest(repo, "test_existing", {**KILL, "id": "kill", "tests": ["tests/test_existing.py::TestB::test_same"]})
    git("add", "-A")
    git("commit", "-qm", "the earlier definition grows; the manifest names only the later class")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_existing.py::TestA::test_same"] and report["status"] == "failed"
    _clean(repo, git)


def test_an_aliased_helper_function_collected_as_a_test_is_traced_to_its_defining_callable(repo, git, capsys):
    """`from helpers import guard as test_guard` في وحدةٍ لم تتغيّر: الدالّةُ لا تبدأ بـtest في مصدرها فلا تراها قراءةٌ بالاسم،
    وتأكيدٌ يُضاف إلى جسمها لا يغيّر معرّفَها المجموع (ملاحظة Codex على #149)؛ صار المجموعُ يُردّ إلى الدالّة التي تعرّفه، بملفّها
    ومداها كما يراها pytest، فيمسّه السطرُ المضاف هناك ويلزمه إثباتٌ باسمه المجموع."""
    (repo / "tests/helpers.py").write_text("from pkg.guard import positive\n\n\ndef guard():\n    assert positive(0) is False\n")
    (repo / "tests/test_alias.py").write_text("from helpers import guard as test_guard  # noqa: F401\nfrom pkg.guard import positive\n\n\ndef test_existing():\n    assert positive(0) is False\n")
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    _manifest(repo, "test_alias", {**KILL, "id": "kill", "tests": ["tests/test_alias.py::test_existing"]})
    git("add", "-A")
    git("commit", "-qm", "an aliased helper collected as a test, named nowhere")
    base = git("rev-parse", "HEAD")
    rng = lambda: f"{base}..{git('rev-parse', 'HEAD')}"
    (repo / "tests/helpers.py").write_text("from pkg.guard import positive\n\n\ndef guard():\n    assert positive(0) is False\n    assert positive(-1) is False\n")
    git("add", "-A")
    git("commit", "-qm", "a guard grows inside the aliased helper alone")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_alias.py::test_guard"] and report["status"] == "failed"
    _manifest(repo, "test_alias", {**KILL, "id": "kill", "tests": ["tests/test_alias.py::test_existing", "tests/test_alias.py::test_guard"]})
    git("add", "-A")
    git("commit", "-qm", "named by its collected id in the importing module's manifest")
    report = _run(repo, "--range", rng(), capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1 and report["unproved_touched_tests"] == []
    _clean(repo, git)


def test_a_test_whose_implementation_moves_to_an_added_helper_is_touched_by_the_lines_added_there(repo, git, capsys):
    """`guard` ينتقل من `tests/helpers_old.py` إلى `tests/helpers_new.py` المضاف ويُحدَّث الاستيرادُ في وحدةٍ تبقي معرّفَه المجموع
    (ملاحظة Codex على #149): المساعدُ الجديد مضافٌ لا معدَّل، فقراءةُ الأصول في المعدَّل وحده لا ترى تأكيدًا يُضاف هناك؛ صار الأصلُ
    يُقرأ في المضاف أيضًا للمعرّفات الباقية، فيُمسّ الاختبارُ ويُطبَّق بيانُه."""
    (repo / "tests/helpers_old.py").write_text("from pkg.guard import positive\n\n\ndef guard():\n    assert positive(0) is False\n")
    (repo / "tests/test_alias.py").write_text("from helpers_old import guard as test_guard  # noqa: F401\n")
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    _manifest(repo, "test_alias", {**KILL, "id": "kill", "tests": ["tests/test_alias.py::test_guard"]})
    git("add", "-A")
    git("commit", "-qm", "an aliased helper, named")
    base = git("rev-parse", "HEAD")
    (repo / "tests/helpers_old.py").unlink()
    (repo / "tests/helpers_new.py").write_text("from pkg.guard import positive\n\n\ndef guard():\n    assert positive(0) is False\n    assert positive(-1) is False\n")
    (repo / "tests/test_alias.py").write_text("from helpers_new import guard as test_guard  # noqa: F401\n")
    git("add", "-A")
    git("commit", "-qm", "the implementation moves to an added helper and grows there")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert "tests/test_alias.py::test_guard" in report["touched_cases"] and report["unmanifested_new_tests"] == []
    assert report["status"] == "passed" and report["totals"]["killed"] == 1 and report["unproved_touched_tests"] == []
    _clean(repo, git)


def test_a_test_wrapped_by_a_decorator_without_wraps_is_traced_to_its_own_definition(repo, git, capsys):
    """مزخرفٌ يلفّ الاختبارَ بلا `functools.wraps`: الدالّةُ الفعلية التي يجمعها pytest غلافُ المزخرف، فمدى الأصل الفعليّ في
    المزخرف لا في جسم الاختبار (ملاحظة Codex على #149)؛ صار التعريفُ النحويّ الذي يسمّيه المعرّفُ في وحدته أصلًا ثانيًا،
    فتأكيدٌ يُضاف في جسمه يمسّه."""
    wrapped = TESTS + "\n\ndef announce(fn):\n    def wrapper(*a, **k):\n        return fn(*a, **k)\n    return wrapper\n\n\n@announce\ndef test_wrapped():\n    assert positive(0) is False\n"
    (repo / "tests/test_guard.py").write_text(wrapped)
    git("add", "-A")
    git("commit", "-qm", "a wrapped test")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(wrapped.replace("def test_wrapped():\n    assert positive(0) is False\n",
                                                            "def test_wrapped():\n    assert positive(0) is False\n    assert positive(-1) is False\n"))
    git("add", "-A")
    git("commit", "-qm", "a guard grows inside the wrapped test")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::test_wrapped"] and report["status"] == "failed"
    _manifest(repo, "test_guard", {**KILL, "id": "kill", "tests": [*KILL["tests"], "tests/test_guard.py::test_wrapped"]})
    git("add", "-A")
    git("commit", "-qm", "named")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1 and report["unproved_touched_tests"] == []
    _clean(repo, git)


def test_a_unicode_test_identifier_is_accepted_in_a_manifest_and_proved(repo, git, capsys):
    """معرّفٌ عربيٌّ صالح في بايثون (`tests/test_عربي.py::test_العربية`) يجمعه pytest؛ كان النمطُ اللاتينيّ يرفض بيانَه
    (ملاحظة Codex على #149)، فصار المعرّفُ يُقبل بأيّ أبجدية ويُثبَت اختبارُه."""
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_عربي.py").write_text("from pkg.guard import positive\n\n\ndef test_العربية():\n    assert positive(0) is False\n")
    _manifest(repo, "test_عربي", {**KILL, "id": "kill", "tests": ["tests/test_عربي.py::test_العربية"]})
    git("add", "-A")
    git("commit", "-qm", "an arabic test identifier, named")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1 and report["unmanifested_new_tests"] == []
    assert report["results"][0]["failed_tests"] == ["tests/test_عربي.py::test_العربية"]
    _clean(repo, git)


def test_an_identifier_with_a_combining_mark_or_a_letterlike_symbol_is_accepted_as_python_accepts_it(repo, git, capsys):
    """`test_اَ` (ألفٌ تتبعها فتحةٌ مركّبة) و`test_℘` معرّفان صالحان في بايثون يجمعهما pytest، ويرفضهما `\\w` لأن العلامةَ المركّبة
    و℘ ليسا أبجديَّين رقميَّين (ملاحظة Codex على #149 بعد 72d1c55)؛ فصار المكوّنُ يُقبل بقاعدة اللغة (`str.isidentifier`) ويُثبَت.
    وما ليس معرّفًا (يبدأ برقم، أو فيه فراغ) يبقى مرفوضًا، ومعاملُ parametrize بأقواسٍ متداخلة مقبول."""
    assert mc.valid_node_id("tests/test_x.py::TestA::test_℘[x[1]]") and mc.valid_node_id("tests/sub/test_x.py::test_اَ")
    assert not mc.valid_node_id("tests/test_x.py::1abc") and not mc.valid_node_id("tests/test_x.py::a b")
    assert not mc.valid_node_id("tests/../core/x.py::test_a") and not mc.valid_node_id("tests/test_x.py")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_marks.py").write_text(
        "from pkg.guard import positive\n\n\ndef test_اَ():\n    assert positive(0) is False\n\n\ndef test_℘():\n    assert positive(0) is False\n")
    _manifest(repo, "test_marks", {**KILL, "id": "kill", "tests": ["tests/test_marks.py::test_اَ", "tests/test_marks.py::test_℘"]})
    git("add", "-A")
    git("commit", "-qm", "identifiers a word class rejects, named")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1 and report["unmanifested_new_tests"] == []
    assert report["results"][0]["failed_tests"] == ["tests/test_marks.py::test_اَ", "tests/test_marks.py::test_℘"]
    _clean(repo, git)


def test_a_run_removes_its_temporary_directory_not_only_the_worktree_inside_it(repo, git, capsys, monkeypatch, tmp_path):
    """التنظيفُ كان يزيل شجرةَ العمل ويترك حاضنَها المؤقّت فارغًا (`diwan-mutation-*`)، فتراكم منه آلافٌ في مجلّد النظام المؤقّت
    بعد يومٍ من التشغيل — إخفاقٌ صامت لا يسمّيه تقرير. صار الحاضنُ يُزال مع الشجرة، و`--keep` وحده يُبقيهما."""
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(runner_temp))
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    git("add", "-A")
    git("commit", "-qm", "a manifest")
    report = _run(repo, "--all", capsys=capsys)
    assert report["status"] == "passed" and report["worktree_kept"] is None
    assert sorted(runner_temp.iterdir()) == [], "بقي حاضنٌ مؤقّت بعد التشغيل"
    report = _run(repo, "--all", "--keep", capsys=capsys)
    kept = Path(report["worktree_kept"])
    assert kept.is_relative_to(runner_temp) and kept.is_dir()
    git("worktree", "remove", "--force", str(kept))
    _clean(repo, git)


def test_a_test_module_path_with_a_space_is_accepted_as_pytest_collects_it(repo, git, capsys):
    """`tests/test_space name.py::test_ok` معرّفٌ يجمعه pytest، وكان نمطُ المسار يرفض الفراغَ فلا يُثبَت اختبارٌ في ملفٍّ كهذا
    بحال (ملاحظة Codex على #149)؛ صار المسارُ يُفحص بنيةً (تحت tests/ وينتهي بـ.py ومقاطعُه سليمة) لا بنمطٍ يستثني الفراغ."""
    assert mc.valid_node_id("tests/test_space name.py::test_ok") and mc.valid_node_id("tests/sub dir/test_x.py::TestA::test_y")
    assert not mc.valid_node_id("tests/test_x.py\n::test_ok") and not mc.valid_node_id("tests//test_x.py::test_ok")
    assert not mc.valid_node_id("tests/./test_x.py::test_ok") and not mc.valid_node_id("core/test_x.py::test_ok")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_space name.py").write_text("from pkg.guard import positive\n\n\ndef test_ok():\n    assert positive(0) is False\n")
    _manifest(repo, "test_space name", {**KILL, "id": "kill", "tests": ["tests/test_space name.py::test_ok"]})
    git("add", "-A")
    git("commit", "-qm", "a test module with a space in its name, named")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1 and report["unmanifested_new_tests"] == []
    assert report["results"][0]["failed_tests"] == ["tests/test_space name.py::test_ok"]
    _clean(repo, git)


def test_the_live_later_definition_of_a_duplicated_test_name_is_its_syntactic_origin(repo, git, capsys):
    """اسمُ اختبارٍ معرَّف مرّتين في الوحدة والأخيرُ (الحيُّ بربط الأسماء) ملفوفٌ بمزخرفٍ بلا wraps: كان التعريفُ النحويّ يقع على
    الأول الميّت والأصلُ الفعليّ على الغلاف، فتأكيدٌ يُضاف في الحيّ لا يمسّ شيئًا ويمرّ المدى بلا إثبات (ملاحظة Codex على #149)؛
    صار الأخيرُ هو المدى."""
    module = TESTS + """

def test_dup():
    assert positive(1) is True


def announce(fn):
    def wrapper(*a, **k):
        return fn(*a, **k)
    return wrapper


@announce
def test_dup():
    assert positive(0) is False
"""
    (repo / "tests/test_guard.py").write_text(module)
    git("add", "-A")
    git("commit", "-qm", "a duplicated test name whose live definition is wrapped")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(module.replace("@announce\ndef test_dup():\n    assert positive(0) is False\n",
                                                           "@announce\ndef test_dup():\n    assert positive(0) is False\n    assert positive(-1) is False\n"))
    git("add", "-A")
    git("commit", "-qm", "a guard grows inside the live definition")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::test_dup"] and report["status"] == "failed"
    _manifest(repo, "test_guard", {**KILL, "id": "kill", "tests": [*KILL["tests"], "tests/test_guard.py::test_dup"]})
    git("add", "-A")
    git("commit", "-qm", "named")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1 and report["unproved_touched_tests"] == []
    _clean(repo, git)


def test_a_deleted_manifest_of_a_module_whose_name_has_a_space_is_still_orphaned(repo, git, capsys):
    """قوائمُ مسارات git كانت تُقرأ بالفراغ فينشطر `tests/test_space name.py` مسارين: لا تُعرف وحدتُه باقيةً ولا يُرى بيانُه
    المحذوف، فيمرّ المدى بلا إثبات (ملاحظة Codex على #149)؛ صارت تُقرأ بفاصل NUL."""
    (repo / "tests/test_space name.py").write_text("from pkg.guard import positive\n\n\ndef test_ok():\n    assert positive(0) is False\n")
    _manifest(repo, "test_space name", {**KILL, "id": "kill", "tests": ["tests/test_space name.py::test_ok"]})
    git("add", "-A")
    git("commit", "-qm", "a spaced module with its manifest")
    base = git("rev-parse", "HEAD")
    (repo / "tests/mutations/test_space name.jsonl").unlink()
    git("add", "-A")
    git("commit", "-qm", "the manifest deleted, the module kept")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["orphaned_manifests"] == ["tests/mutations/test_space name.jsonl"] and report["status"] == "failed"
    _clean(repo, git)


def test_a_unittest_subclass_is_placed_whatever_its_name_so_a_grown_method_touches_it_and_its_heir(repo, git, capsys):
    """صنفٌ يرث unittest.TestCase واسمُه لا يبدأ بـTest كان خارج الأصناف المقروءة (ملاحظة Codex على #149)؛ صار يُقرأ هو ووارثُه في
    الوحدة، فسطرٌ مضاف في دالّته يمسّها فيه وفي الوارث. والصنفُ العاديّ لا يجمعه pytest فلا يُمسّ ولو قُرئ؛ والوارثُ أصلًا
    مستوردًا باسمٍ لا ينتهي بـTestCase لا تضعه القراءةُ النحوية، لكن الردَّ إلى الأصل (الدالّةُ التي تعرّفه كما يراها pytest)
    يضعه، فسطرٌ مضاف في دالّته يمسّه أيضًا — وكان حدًّا معلَنًا فأُغلق."""
    (repo / "tests/base.py").write_text("import unittest\n\n\nclass Base(unittest.TestCase):\n    pass\n")
    module = TESTS + """

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
"""
    (repo / "tests/test_guard.py").write_text(module)
    named = [*KILL["tests"], "tests/test_guard.py::GuardCase::test_case_zero", "tests/test_guard.py::Derived::test_case_zero",
             "tests/test_guard.py::Derived::test_derived_zero", "tests/test_guard.py::FromImport::test_imported_base"]
    _manifest(repo, "test_guard", {**KILL, "id": "kill", "tests": named})
    git("add", "-A")
    git("commit", "-qm", "unittest classes, all named")
    base = git("rev-parse", "HEAD")
    grown = module.replace("    def test_case_zero(self):\n        assert positive(0) is False\n",
                           "    def test_case_zero(self):\n        assert positive(0) is False\n        assert positive(-1) is False\n")
    grown = grown.replace("    def test_never_collected(self):\n        assert False\n", "    def test_never_collected(self):\n        assert False\n        assert False\n")
    grown = grown.replace("    def test_imported_base(self):\n        assert positive(0) is False\n",
                          "    def test_imported_base(self):\n        assert positive(0) is False\n        assert positive(-2) is False\n")
    (repo / "tests/test_guard.py").write_text(grown)
    _manifest(repo, "test_guard", {**KILL, "id": "kill", "tests": KILL["tests"]})
    git("add", "-A")
    git("commit", "-qm", "lines added inside three methods; the manifest names none of them")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::Derived::test_case_zero", "tests/test_guard.py::FromImport::test_imported_base",
                                                "tests/test_guard.py::GuardCase::test_case_zero"]
    assert report["status"] == "failed"
    _manifest(repo, "test_guard", {**KILL, "id": "kill", "tests": named})
    git("add", "-A")
    git("commit", "-qm", "named again")
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


@pytest.mark.parametrize("removal", ["module", "function", "parameter_case"], ids=["module", "function", "parameter_case"])
def test_removing_a_test_its_module_or_a_case_re_applies_the_manifest_that_named_it_so_the_range_fails_test_missing(repo, git, capsys, removal):
    """حذفُ اختبارٍ أو وحدته أو حالةٍ من parametrize بلا مسّ بيانه كان يخرج من المدى (لا سطرَ مضاف)، فيمرّ الطلبُ ثم يسقط
    التدقيقُ الأسبوعيّ بـtest_missing (ملاحظة Codex على #149)؛ صار ما زال أو فقد سطرًا يُعيد بياناتِه في المدى."""
    decorated = TESTS + "\n\nimport pytest\n\n\n@pytest.mark.parametrize(\"x\", [\n    pytest.param(0, id=\"zero\"),\n    pytest.param(-1, id=\"negative\"),\n])\ndef test_not_positive(x):\n    assert positive(x) is False\n"
    (repo / "tests/test_guard.py").write_text(decorated)
    _manifest(repo, "test_guard", {**KILL, "id": "kill"},
              {**KILL, "id": "kill-negative", "new": "return x > -5", "tests": ["tests/test_guard.py::test_not_positive[negative]"]})
    git("add", "-A")
    git("commit", "-qm", "guards and their manifest")
    base = git("rev-parse", "HEAD")
    if removal == "module":
        (repo / "tests/test_guard.py").unlink()
        expected = ["tests/test_guard.py::test_already_failing", "tests/test_guard.py::test_always_passes",
                    "tests/test_guard.py::test_not_positive", "tests/test_guard.py::test_zero_is_not_positive"]
    elif removal == "function":
        (repo / "tests/test_guard.py").write_text(decorated.replace("def test_zero_is_not_positive():\n    assert positive(0) is False\n\n\n", ""))
        expected = ["tests/test_guard.py::test_zero_is_not_positive"]
    else:
        (repo / "tests/test_guard.py").write_text(decorated.replace("    pytest.param(-1, id=\"negative\"),\n", ""))
        expected = ["tests/test_guard.py::test_not_positive"]
    git("add", "-A")
    git("commit", "-qm", f"remove the {removal}")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["revalidated_tests"] == expected and report["manifests"] == ["tests/mutations/test_guard.jsonl"]
    assert report["totals"]["test_missing"] >= 1 and report["status"] == "failed"
    assert report["touched_cases"] == {} and report["unmanifested_new_tests"] == []
    _clean(repo, git)


def test_a_manifest_that_names_a_test_of_another_module_is_refused_by_name(repo, git, capsys):
    """بيانُ الوحدة يسمّي اختباراتِها وحدها: إثباتٌ مستعارٌ من بيان وحدةٍ أخرى يزول بزوال تلك الوحدة صامتًا (ملاحظة Codex على #149)."""
    (repo / "tests/test_other.py").write_text("def test_other():\n    assert True\n")
    _manifest(repo, "test_other", {**KILL, "id": "borrowed"})          # يسمّي tests/test_guard.py::… من بيان test_other
    git("add", "-A")
    git("commit", "-qm", "foreign name")
    report = _run(repo, "--all", capsys=capsys)
    assert report["status"] == "refused" and report["code"] == "manifest_invalid"
    assert "tests/mutations/test_other.jsonl:1" in report["detail"] and "tests/test_guard.py::test_zero_is_not_positive" in report["detail"]
    _clean(repo, git)


def test_a_touched_test_must_be_named_by_its_own_module_s_manifest_not_another_s(repo, git, capsys):
    """حارسٌ جديد في test_guard.py كان يُحسب مسمًّى إن ورد معرّفُه في بيان أيّ وحدة (ملاحظة Codex على #149)؛ صار بيانُ وحدته
    وحدَه ما يسمّيه ويُطبَّق، فالبيانُ الغريب لا يُحمَّل ولا يُعير."""
    (repo / "tests/test_other.py").write_text("from pkg.guard import positive\n\n\ndef test_other():\n    assert positive(0) is False\n")
    _manifest(repo, "test_other", {**KILL, "id": "borrowed", "tests": ["tests/test_guard.py::test_more"]})
    _manifest(repo, "test_guard", {**KILL, "id": "kill"})
    git("add", "-A")
    git("commit", "-qm", "manifests")
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text(TESTS + "\n\ndef test_more():\n    assert positive(0) is False\n")
    git("add", "-A")
    git("commit", "-qm", "a guard named only by another module's manifest")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == ["tests/test_guard.py::test_more"] and report["status"] == "failed"
    assert report["manifests"] == ["tests/mutations/test_guard.jsonl"], "البيانُ الغريب حُمّل"
    _manifest(repo, "test_guard", {**KILL, "id": "kill", "tests": [*KILL["tests"], "tests/test_guard.py::test_more"]})
    _manifest(repo, "test_other", {**KILL, "id": "own", "tests": ["tests/test_other.py::test_other"]})
    git("add", "-A")
    git("commit", "-qm", "each named by its own manifest")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["unmanifested_new_tests"] == [] and report["status"] == "passed"
    _clean(repo, git)


def test_a_module_named_by_the_second_pytest_pattern_is_scanned_too(repo, git, capsys):
    """pytest يجمع *_test.py كما يجمع test_*.py؛ كان المرشّحُ يقبل الأولَ وحده فيمرّ حارسٌ في tests/guard_test.py بلا بيان
    (ملاحظة Codex على #149)."""
    base = git("rev-parse", "HEAD")
    (repo / "tests/guard_test.py").write_text("from pkg.guard import positive\n\n\ndef test_second_pattern():\n    assert positive(0) is False\n")
    git("add", "-A")
    git("commit", "-qm", "second pattern")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["manifest_missing"] == ["tests/guard_test.py"] and report["status"] == "failed"
    assert report["unmanifested_new_tests"] == ["tests/guard_test.py::test_second_pattern"]
    _manifest(repo, "guard_test", {**KILL, "id": "second", "tests": ["tests/guard_test.py::test_second_pattern"]})
    git("add", "-A")
    git("commit", "-qm", "its manifest")
    report = _run(repo, "--range", f"{base}..{git('rev-parse', 'HEAD')}", capsys=capsys)
    assert report["status"] == "passed" and report["totals"]["killed"] == 1
    _clean(repo, git)


def test_removed_lines_are_read_against_the_merge_base_not_the_base_tip(repo, git, capsys):
    """الفرقُ base...head يُقاس من أصل الدمج، فأسطرُه المُزالة بإحداثياته؛ ورأسُ الأساس الذي تقدّم بأسطرٍ قبل الاختبار كان
    يُقرأ منه مدى الاختبار فيُزاح ويضيع حذفُ الحالة (ملاحظة Codex على #149)."""
    decorated = TESTS + "\n\nimport pytest\n\n\n@pytest.mark.parametrize(\"x\", [\n    pytest.param(0, id=\"zero\"),\n    pytest.param(-1, id=\"negative\"),\n])\ndef test_not_positive(x):\n    assert positive(x) is False\n"
    (repo / "tests/test_guard.py").write_text(decorated)
    _manifest(repo, "test_guard", {**KILL, "id": "kill-negative", "new": "return x > -5", "tests": ["tests/test_guard.py::test_not_positive[negative]"]})
    git("add", "-A")
    git("commit", "-qm", "guards and manifest")
    fork = git("rev-parse", "HEAD")
    git("checkout", "-q", "-b", "pr")
    (repo / "tests/test_guard.py").write_text(decorated.replace("    pytest.param(-1, id=\"negative\"),\n", ""))
    git("add", "-A")
    git("commit", "-qm", "remove the negative case")
    head = git("rev-parse", "HEAD")
    git("checkout", "-q", "-")                                     # الأساسُ يتقدّم بأسطرٍ كثيرة قبل الاختبار
    padding = "".join(f"\n\ndef test_padding_{i}():\n    assert True\n" for i in range(12))
    (repo / "tests/test_guard.py").write_text("from pkg.guard import positive\n" + padding + "\n" + decorated.split("\n", 1)[1])
    git("add", "-A")
    git("commit", "-qm", "base advances above the test")
    base_tip = git("rev-parse", "HEAD")
    assert git("merge-base", base_tip, head) == fork
    report = _run(repo, "--range", f"{base_tip}..{head}", "--head", head, capsys=capsys)     # كما يمرّره CI
    assert report["revalidated_tests"] == ["tests/test_guard.py::test_not_positive"], report
    assert report["totals"]["test_missing"] == 1 and report["status"] == "failed"
    git("checkout", "-q", "pr")


def test_an_entry_is_killed_only_when_every_named_test_fails(repo, git, capsys):
    """سطرٌ يسمّي اختبارين ويسقط أحدُهما كان يُحكم killed والآخرُ في named_but_passed للعرض وحده، فيُنسب إلى الحارس ما لم
    يفعل ويمرّ الأسبوعيُّ (ملاحظة Codex على #149)؛ صار partially_killed حكمًا مسمًّى يُسقط المدى."""
    _manifest(repo, "test_guard", {**KILL, "id": "half", "tests": [*KILL["tests"], "tests/test_guard.py::test_always_passes"]})
    git("add", "-A")
    git("commit", "-qm", "half")
    report = _run(repo, "--all", capsys=capsys)
    (result,) = report["results"]
    assert result["code"] == "partially_killed" and result["named_but_passed"] == ["tests/test_guard.py::test_always_passes"]
    assert result["failed_tests"] == KILL["tests"] and report["totals"]["partially_killed"] == 1 and report["status"] == "failed"
    _clean(repo, git)


def test_a_bare_function_selector_is_killed_only_when_every_collected_case_fails(repo, git, capsys):
    """الدالّةُ المعلَّمة المسمّاةُ بلا معامل كانت تُحكم killed متى سقطت حالةٌ واحدة من حالاتها (ملاحظة Codex على #149)؛
    صارت تُبسط إلى حالاتها كما يجمعها pytest فيلزم سقوطُها كلُّها وإلا partially_killed."""
    decorated = TESTS + "\n\nimport pytest\n\n\n@pytest.mark.parametrize(\"x\", [\n    pytest.param(0, id=\"zero\"),\n    pytest.param(-1, id=\"negative\"),\n])\ndef test_not_positive(x):\n    assert positive(x) is False\n"
    (repo / "tests/test_guard.py").write_text(decorated)
    _manifest(repo, "test_guard", {**KILL, "id": "bare", "tests": ["tests/test_guard.py::test_not_positive"]})
    git("add", "-A")
    git("commit", "-qm", "bare selector, x >= 0 catches zero only")
    report = _run(repo, "--all", capsys=capsys)
    (result,) = report["results"]
    assert result["code"] == "partially_killed" and result["named_but_passed"] == ["tests/test_guard.py::test_not_positive"]
    assert result["failed_tests"] == ["tests/test_guard.py::test_not_positive[zero]"] and report["status"] == "failed"
    _manifest(repo, "test_guard", {**KILL, "id": "bare", "new": "return x > -5", "tests": ["tests/test_guard.py::test_not_positive"]})
    git("add", "-A")
    git("commit", "-qm", "x > -5 catches both")
    report = _run(repo, "--all", capsys=capsys)
    (result,) = report["results"]
    assert result["code"] == "killed" and result["failed_tests"] == [
        "tests/test_guard.py::test_not_positive[zero]", "tests/test_guard.py::test_not_positive[negative]"]
    assert report["status"] == "passed"
    _clean(repo, git)


def test_the_mutation_check_is_a_step_of_the_required_verify_check_on_the_prospective_merge_commit():
    """قرارُ المالك في ٢٨ سبتمبر ٢٠٢٦: فاحصُ الطفرات مطلوبٌ على main. حمايةُ main تشترط الفحصَ `verify` (docs/guides/G1.md)،
    فالفاحصُ خطوةٌ في مهمّة verify نفسِها في verify-hosted.yml — مطلوبٌ بالبناء لا بإعدادٍ في الواجهة — على مدى الإسناد نفسِه
    وعلى إيداع الدمج المرتقَب (github.sha) لا رأس الفرع (ملاحظة Codex على #149)، وبلا مدًى البياناتُ كلُّها. وسيرُ
    mutation-check.yml لا يعمل على الطلبات (فلا تشغيلَ مزدوج) بل أسبوعيًّا ويدويًّا بالبيانات كلِّها بلا خيارٍ يعد بمدًى لا أساسَ له."""
    import re
    hosted = (ROOT / ".github/workflows/verify-hosted.yml").read_text(encoding="utf-8")
    jobs = hosted.index("\njobs:\n  verify:\n")
    step = hosted.index('tools/mutation_check.py --range "$RANGE" --head "$HEAD" --report "$report" --timeout-s 1200')
    assert step > jobs and re.findall(r"^  ([\w-]+):\s*$", hosted[jobs:step], re.M) == ["verify"]      # في المهمّة الإلزامية نفسِها
    assert "          HEAD: ${{ github.sha }}" in hosted and "pull_request.head.sha" not in hosted
    assert "RANGE_BASE: ${{ github.event.pull_request.base.sha || github.event.before }}" in hosted   # مدى الإسناد نفسُه
    assert '*..*) .venv/bin/python tools/mutation_check.py --range "$RANGE"' in hosted and '*)    .venv/bin/python tools/mutation_check.py --all --head "$HEAD"' in hosted
    weekly = (ROOT / ".github/workflows/mutation-check.yml").read_text(encoding="utf-8")
    assert "pull_request" not in weekly and "inputs:" not in weekly and "--range" not in weekly
    assert 'tools/mutation_check.py --all --head "$HEAD"' in weekly and "HEAD: ${{ github.sha }}" in weekly


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
    owned = {mc.manifest_for(m.relative_to(ROOT).as_posix()) for m in (ROOT / "tests").rglob("*.py") if mc._is_test_module(m.name)}
    for path in paths:
        assert path.relative_to(ROOT).as_posix() in owned, f"{path.name} بلا وحدة اختبارٍ يؤول بيانُها إليه"
        for entry in mc.load_manifest(path, ROOT):
            target = ROOT / entry["file"]
            assert target.is_file(), entry["file"]
            assert target.read_text(encoding="utf-8").count(entry["old"]) == entry["count"], (path.name, entry.get("id"))
