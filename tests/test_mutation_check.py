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
    ("/etc/passwd", "مطلق"), ("../outside.py", "صاعد"), ("tests/test_guard.py", "تحت tests/"),
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
    # تعديلٌ بلا دالّةٍ جديدة في ملفٍّ بلا بيان: حدٌّ معلَن، ويُسقط بالصرامة وحدها
    base = git("rev-parse", "HEAD")
    (repo / "tests/test_guard.py").write_text((repo / "tests/test_guard.py").read_text() + "\n# تعليق\n")
    (repo / "tests/mutations/test_guard.jsonl").unlink()
    git("add", "-A")
    git("commit", "-qm", "touch without a new guard")
    rng = f"{base}..{git('rev-parse', 'HEAD')}"
    report = _run(repo, "--range", rng, capsys=capsys)
    assert report["unmanifested_changed_tests"] == ["tests/test_guard.py"] and report["unmanifested_new_tests"] == []
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
    for path in paths:
        assert (ROOT / "tests" / (path.stem + ".py")).is_file(), f"{path.name} بلا وحدة اختبارٍ باسمه"
        for entry in mc.load_manifest(path, ROOT):
            target = ROOT / entry["file"]
            assert target.is_file(), entry["file"]
            assert target.read_text(encoding="utf-8").count(entry["old"]) == entry["count"], (path.name, entry.get("id"))
