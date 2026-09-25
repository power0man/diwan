"""الشطرُ المحجوب من بنوك القياس المستقلّة لا يدخل المستودع — والبيانُ وحدَه يُنشر.

قيمةُ المحجوب كلُّها في أن عملاءَ التطوير لم يروه. فلو دخل ملفٌّ منه
الشجرةَ مرّةً واحدة قرأه كلُّ عميلٍ يفتح المستودع، وبطل الرقمُ الذي يُقاس
عليه — بلا أثرٍ يدلّ على البطلان. و.gitignore وحدَه لا يكفي: `git add -f`
يتجاوزه. فهذا الاختبارُ يفحص ما **في الشجرة فعلًا**.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BANKS = ROOT / "evaluation" / "banks"
SHA = re.compile(r"[0-9a-f]{64}")


def _disk_files(prefix: str) -> list[str]:
    """Enumerate names without following links; enumeration errors propagate."""
    def visit(directory):
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    yield from visit(path)
                else:
                    yield path.relative_to(ROOT).as_posix()
    return sorted(visit(ROOT / prefix))


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    # Hooks may export a different repository or index. Never inherit those
    # selectors, and never discover a parent repository for an invalid .git.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = str(root.parent.resolve())
    return subprocess.run(["git", *args], cwd=root, env=env,
                          capture_output=True, text=True, check=True)


def _candidate_files(prefix: str) -> list[str]:
    # The trusted gate materializes all commit blobs without Git metadata.
    # A checkout still uses its index, including entries absent from disk.
    if not os.path.lexists(ROOT / ".git"):
        return _disk_files(prefix)
    out = _git(ROOT, "ls-files", "-z", "--", prefix)
    return [name for name in out.stdout.split("\0") if name]


def _is_manifest(path: str) -> bool:
    parts = path.split("/")
    return (len(parts) == 5 and parts[:2] == ["evaluation", "banks"]
            and parts[3:] == ["sealed", "MANIFEST.json"])


def _sealed_leaks(paths: list[str]) -> list[str]:
    """اسمٌ يشي بالمحجوب — في أيّ موضعٍ من المرشَّح، وبلا اعتبارٍ لحالة الأحرف.

    كان الحارس يطابق مكوّنَ مسارٍ يساوي `sealed` تحت `evaluation/banks` وحده،
    فمرّ `Sealed/` و`docs/sealed/` و`open/x_sealed.json` (قِيس ٢٥ سبتمبر ٢٠٢٦، ك٢٢).
    فصار: مجلّدٌ في اسمه «sealed» في أيّ موضع، أو ملفٌّ في اسمه «sealed» تحت
    `evaluation/banks` — والبيانُ وحده مستثنًى. والاسمُ قرينةٌ لا برهان؛ البرهانُ
    بالبصمة في `_digest_leaks`.
    """
    out = []
    for path in paths:
        if _is_manifest(path):
            continue
        *dirs, name = path.split("/")
        if any("sealed" in part.lower() for part in dirs):
            out.append(path)
        elif path.startswith("evaluation/banks/") and "sealed" in name.lower():
            out.append(path)
    return out


def _manifests() -> list[Path]:
    return sorted((ROOT / "evaluation" / "banks").glob("*/sealed/MANIFEST.json"))


# ————— الحارسُ بالبصمة: المحتوى لا الاسم —————

SKIP_DIRS = {".git", ".venv", "var", "__pycache__", ".pytest_cache", "node_modules"}


def _disk_files_everywhere() -> list[str]:
    """كلُّ ملفٍّ في شجرة المرشَّح بلا اتّباع روابط، وبلا مجلّدات البيئة والعمل."""
    def visit(directory):
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name in SKIP_DIRS:
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    yield from visit(path)
                else:
                    yield path.relative_to(ROOT).as_posix()
    return sorted(visit(ROOT))


def _all_candidate_files() -> list[str]:
    """التسريبُ لا يلتزم مجلّدًا، فالمرشَّحُ كلُّه يُفحص لا `evaluation/banks` وحده."""
    if not os.path.lexists(ROOT / ".git"):
        return _disk_files_everywhere()
    out = _git(ROOT, "ls-files", "-z")
    return [name for name in out.stdout.split("\0") if name]


def _sealed_digests() -> dict[str, str]:
    """بصمةُ كلِّ ملفٍّ محجوب كما يعلنها البيان ← مسارُه المحجوب."""
    digests: dict[str, str] = {}
    for manifest in _manifests():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for path, entry in _entries(data["files"]):
            digests[entry["sha256"]] = f"{manifest.parent.parent.name}/{path}"
    return digests


def _content(path: str) -> bytes | None:
    """محتوى المرشَّح: من القرص، أو من الفهرس إن حُذف من القرص بعد إضافته."""
    target = ROOT / path
    try:
        return target.read_bytes()
    except OSError:
        pass
    if os.path.lexists(ROOT / ".git"):
        shown = _git(ROOT, "show", f":{path}")
        return shown.stdout.encode("utf-8")
    return None


def _digest_leaks(paths: list[str]) -> list[tuple[str, str]]:
    """ملفٌّ محتواه محتوى ملفٍّ محجوب — مهما سُمّي وأينما وُضع.

    الاسمُ يُغيَّر بلا كلفة؛ البصمةُ لا. فكلُّ ملفٍّ في المرشَّح يُبصَم ويُقارن
    ببصمات البيان. والحدُّ معلَن: نسخةٌ معدَّلةٌ ولو بمسافةٍ واحدة تفلت من البصمة،
    ولا يصيدها إلا الاسمُ إن أبقاه المهرِّب.
    """
    sealed = _sealed_digests()
    leaks = []
    for path in paths:
        if _is_manifest(path):
            continue
        data = _content(path)
        if data is None:
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest in sealed:
            leaks.append((path, sealed[digest]))
    return leaks


def test_only_the_manifest_of_a_sealed_split_is_in_the_candidate():
    leaked = _sealed_leaks(_candidate_files("evaluation/banks"))
    assert leaked == [], f"محتوًى محجوبٌ دخل المستودع: {leaked}"


def test_only_the_manifest_of_a_sealed_split_exists_on_disk():
    """ولو لم يُدفَع بعد: وجودُه في شجرة العمل يعني أن عميلًا قد يقرؤه."""
    leaked = _sealed_leaks(_disk_files("evaluation/banks"))
    assert leaked == [], f"ملفٌّ محجوبٌ في شجرة العمل: {leaked}"


# مفاتيحُ البيان مغلقةٌ بقصد: أيُّ حقلٍ حرٍّ زائد موضعٌ تُهرَّب فيه حالةٌ محجوبة
# إلى المستودع تحت اسم «بيان». والشكلان مقبولان لأن التكليف اشترط محتوى
# البيان لا شكلَه: قائمةٌ فيها path (v1)، أو قاموسٌ مفتاحُه المسار (v1.1).
MANIFEST_KEYS_REQUIRED = {"generated_at", "files"}
MANIFEST_KEYS_OPTIONAL = {"authored_by", "manifest_version", "bank_version",
                          "note", "sealed_totals"}
ENTRY_KEYS_OPTIONAL = {"path", "kind", "count", "cases", "capabilities"}
NOTE_MAX = 500


def _entries(files):
    """(مسار، وصف) من الشكلين جميعًا."""
    if isinstance(files, dict):
        return [(path, meta) for path, meta in files.items()]
    return [(entry.get("path"), entry) for entry in files]


@pytest.mark.parametrize("manifest", _manifests(), ids=lambda p: p.parent.parent.name)
def test_every_manifest_commits_to_hashes_and_counts_without_content(manifest):
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert MANIFEST_KEYS_REQUIRED <= set(data), set(data)
    unknown = set(data) - MANIFEST_KEYS_REQUIRED - MANIFEST_KEYS_OPTIONAL
    assert not unknown, f"حقلٌ لا يعرفه الحارس قد يحمل محتوىً: {unknown}"
    assert len(str(data.get("note", ""))) <= NOTE_MAX, "ملاحظةٌ أطولُ من أن تكون وصفًا"
    assert data["files"], "بيانٌ بلا ملفّات لا يُثبت شيئًا"
    seen = set()
    for path, entry in _entries(data["files"]):
        unknown = set(entry) - {"sha256"} - ENTRY_KEYS_OPTIONAL
        assert not unknown, f"{path}: حقلٌ لا يعرفه الحارس: {unknown}"
        assert path and path.startswith("sealed/") and path not in seen, path
        seen.add(path)
        assert SHA.fullmatch(entry["sha256"]), path
        count = entry.get("count", entry.get("cases"))
        if entry.get("kind") == "sidecar":
            # الجانبيّ وصفٌ لا حالات، فيكفيه أن تُعلن بصمتُه
            continue
        assert type(count) is int and count >= 1, path
        assert entry["capabilities"] and all(isinstance(c, str) for c in entry["capabilities"])


def test_there_is_at_least_one_manifest():
    """حارسٌ على الحارس: لو نُقل المجلّد لمرّ الاختبارُ السابق فارغًا."""
    assert _manifests(), "لا بيانَ محجوبٍ في evaluation/banks"


@pytest.mark.parametrize("checkout", [False, True], ids=["snapshot", "checkout"])
def test_guard_accepts_only_the_regular_manifest(tmp_path, monkeypatch, checkout):
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    path = tmp_path / "evaluation/banks/fixture/sealed/MANIFEST.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    if checkout:
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "add", "--", "evaluation/banks")
    test_only_the_manifest_of_a_sealed_split_is_in_the_candidate()
    test_only_the_manifest_of_a_sealed_split_exists_on_disk()


def test_index_guard_detects_a_staged_leak_even_after_disk_removal(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    path = tmp_path / "evaluation/banks/fixture/sealed/answers.json"
    path.parent.mkdir(parents=True)
    path.write_text("synthetic fixture")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "--", "evaluation/banks")
    path.unlink()
    with pytest.raises(AssertionError, match="محتوًى محجوب"):
        test_only_the_manifest_of_a_sealed_split_is_in_the_candidate()
    test_only_the_manifest_of_a_sealed_split_exists_on_disk()


@pytest.mark.parametrize("checkout", [False, True], ids=["snapshot", "checkout"])
@pytest.mark.parametrize("relative", ["sealed/answers.json", "sealed/nested/MANIFEST.json",
                                      "sealed/nested/sealed/MANIFEST.json"])
def test_guard_detects_synthetic_leak_in_each_candidate_form(tmp_path, monkeypatch, checkout, relative):
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    path = tmp_path / "evaluation/banks/fixture" / relative
    path.parent.mkdir(parents=True)
    path.write_text("synthetic fixture, not bank content")
    if checkout:
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "add", "--", "evaluation/banks")
    with pytest.raises(AssertionError, match="محتوًى محجوب"):
        test_only_the_manifest_of_a_sealed_split_is_in_the_candidate()
    with pytest.raises(AssertionError, match="ملفٌّ محجوب"):
        test_only_the_manifest_of_a_sealed_split_exists_on_disk()


def test_checkout_git_failure_is_not_reclassified_as_snapshot(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    inner = tmp_path / "snapshot"
    inner.mkdir()
    monkeypatch.setitem(globals(), "ROOT", inner)
    (inner / ".git").symlink_to(inner / "missing-git")
    with pytest.raises(subprocess.CalledProcessError):
        _candidate_files("evaluation/banks")


def test_snapshot_listing_error_is_not_an_empty_candidate(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        _candidate_files("evaluation/banks")


def test_snapshot_does_not_invoke_git_or_follow_links(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    root = tmp_path / "evaluation/banks/fixture/sealed"
    root.mkdir(parents=True)
    (root / "MANIFEST.json").write_text("{}")
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    def forbidden(*args, **kwargs):
        raise AssertionError("snapshot must not invoke Git")
    monkeypatch.setattr(subprocess, "run", forbidden)
    assert _candidate_files("evaluation/banks") == [
        "evaluation/banks/fixture/sealed/MANIFEST.json",
        "evaluation/banks/fixture/sealed/link"]
    assert _sealed_leaks(_candidate_files("evaluation/banks")) == [
        "evaluation/banks/fixture/sealed/link"]


def test_foreign_git_environment_cannot_select_or_modify_another_index(tmp_path, monkeypatch):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _git(foreign, "init", "-q")
    root = tmp_path / "candidate"
    root.mkdir()
    monkeypatch.setitem(globals(), "ROOT", root)
    path = root / "evaluation/banks/fixture/sealed/answers.json"
    path.parent.mkdir(parents=True)
    path.write_text("synthetic fixture")
    foreign_index = foreign / "unexpected-index"
    monkeypatch.setenv("GIT_DIR", str(foreign / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(foreign))
    monkeypatch.setenv("GIT_INDEX_FILE", str(foreign_index))
    _git(root, "init", "-q")
    _git(root, "add", "--", "evaluation/banks")
    assert _candidate_files("evaluation/banks") == [
        "evaluation/banks/fixture/sealed/answers.json"]
    assert _git(foreign, "ls-files", "-z").stdout == ""
    assert not foreign_index.exists()


# ————— ك٢٢: الحارسُ بالبصمة، والاسمُ في كل موضع —————

def test_no_candidate_file_anywhere_carries_a_sealed_digest():
    leaked = _digest_leaks(_all_candidate_files())
    assert leaked == [], f"محتوًى محجوبٌ بعينه في المرشَّح تحت اسمٍ آخر: {leaked}"


def test_no_file_on_disk_anywhere_carries_a_sealed_digest():
    leaked = _digest_leaks(_disk_files_everywhere())
    assert leaked == [], f"محتوًى محجوبٌ بعينه في شجرة العمل: {leaked}"


def test_no_sealed_named_path_anywhere_outside_the_manifest():
    leaked = _sealed_leaks(_all_candidate_files())
    assert leaked == [], f"اسمٌ يشي بالمحجوب خارج البيان: {leaked}"


SYNTHETIC_SEALED = b'{"cases": ["synthetic sealed fixture, not bank content"]}\n'


def _synthetic_manifest(root: Path) -> None:
    manifest = root / "evaluation/banks/fixture/sealed/MANIFEST.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "generated_at": "2026-09-25",
        "files": [{"path": "sealed/tier_x/x_sealed.json",
                   "sha256": hashlib.sha256(SYNTHETIC_SEALED).hexdigest(),
                   "count": 1, "kind": "suite", "capabilities": ["synthetic"]}],
    }), encoding="utf-8")


@pytest.mark.parametrize("checkout", [False, True], ids=["snapshot", "checkout"])
@pytest.mark.parametrize("relative", ["docs/notes/harmless.json",
                                      "evaluation/banks/fixture/open/tier_a/kimi_x_a_001.json",
                                      "tests/fixtures/sample.txt"])
def test_sealed_content_under_an_innocent_name_is_caught_by_digest(tmp_path, monkeypatch,
                                                                    checkout, relative):
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    _synthetic_manifest(tmp_path)
    leak = tmp_path / relative
    leak.parent.mkdir(parents=True, exist_ok=True)
    leak.write_bytes(SYNTHETIC_SEALED)
    if checkout:
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "add", "--", ".")
    assert _sealed_leaks(_all_candidate_files()) == [], "الاسمُ بريء عمدًا: لا يصيده إلا البصمة"
    assert _digest_leaks(_all_candidate_files()) == [(relative, "fixture/sealed/tier_x/x_sealed.json")]
    assert _digest_leaks(_disk_files_everywhere()) == [(relative, "fixture/sealed/tier_x/x_sealed.json")]
    with pytest.raises(AssertionError, match="محتوًى محجوبٌ بعينه"):
        test_no_candidate_file_anywhere_carries_a_sealed_digest()


def test_sealed_content_staged_then_removed_from_disk_is_still_caught(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    _synthetic_manifest(tmp_path)
    leak = tmp_path / "docs/notes/harmless.json"
    leak.parent.mkdir(parents=True)
    leak.write_bytes(SYNTHETIC_SEALED)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "--", ".")
    leak.unlink()
    assert _digest_leaks(_all_candidate_files()) == [
        ("docs/notes/harmless.json", "fixture/sealed/tier_x/x_sealed.json")]


def test_a_modified_copy_escapes_the_digest_and_that_limit_is_declared(tmp_path, monkeypatch):
    """حدٌّ معلَن لا سلوكٌ مقصود: مسافةٌ واحدة تغيّر البصمة. يبقى الاسمُ الخطَّ الثاني."""
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    _synthetic_manifest(tmp_path)
    leak = tmp_path / "docs/notes/harmless.json"
    leak.parent.mkdir(parents=True)
    leak.write_bytes(SYNTHETIC_SEALED + b" ")
    assert _digest_leaks(_all_candidate_files()) == []


@pytest.mark.parametrize("relative", [
    "evaluation/banks/fixture/Sealed/answers.json",
    "docs/sealed/answers.json",
    "evaluation/sealed/answers.json",
    "evaluation/banks/fixture/reviews/sealed_copy/answers.json",
    "evaluation/banks/fixture/sealed.bak/answers.json",
    "evaluation/banks/fixture/open/tier_a/kimi_arabic_a_001_sealed.json",
    "evaluation/banks/fixture/open/tier_a/kimi_arabic_a_001.sealed.json",
    "evaluation/banks/fixture/open/tier_a/SEALED_answers.json",
])
def test_sealed_named_paths_are_caught_anywhere_and_case_insensitively(tmp_path, monkeypatch, relative):
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    leak = tmp_path / relative
    leak.parent.mkdir(parents=True)
    leak.write_text("synthetic fixture, not bank content", encoding="utf-8")
    assert _sealed_leaks(_all_candidate_files()) == [relative]


@pytest.mark.parametrize("relative", [
    "tests/test_sealed_banks_stay_sealed.py",
    "docs/probe/sealed-snapshot-20260924.json",
    "docs/external/KIMI-MANIFEST-FIX.md",
])
def test_ordinary_files_that_merely_mention_sealed_are_not_flagged(tmp_path, monkeypatch, relative):
    """الاسمُ خارج `evaluation/banks` لا يُدان بكلمة: الحارسُ والوثائقُ تُسمّي المحجوبَ باسمه."""
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text("about the sealed split", encoding="utf-8")
    assert _sealed_leaks(_all_candidate_files()) == []


def test_repo_wide_snapshot_walk_skips_environment_directories(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    for skipped in (".git", ".venv", "var", "__pycache__"):
        (tmp_path / skipped / "sealed").mkdir(parents=True)
        (tmp_path / skipped / "sealed" / "x.json").write_bytes(SYNTHETIC_SEALED)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("x", encoding="utf-8")
    assert _disk_files_everywhere() == ["docs/a.md"]
