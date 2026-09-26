"""خزنةُ Obsidian للمالك (جديد-obsidian-vault، ق٦٤): مرآةٌ خارج المستودع لا تمسّ المحجوبَ ولا ملاحظاتِ المالك.

كلُّ حارسٍ هنا مُثبَتٌ بالطفرة: كُسر في `tools/obsidian_vault.py` فسقط اختبارُه، ثم أُعيد.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("obsidian_vault", ROOT / "tools/obsidian_vault.py")
ov = importlib.util.module_from_spec(spec)
sys.modules["obsidian_vault"] = ov
spec.loader.exec_module(ov)

PLAN = {
    "title": "خطة تجريبية", "thesis": "أطروحة",
    "phases": [{"id": "م٠", "name": "الأولى", "start": "2026-09-28", "end": "2026-10-04", "goal": "هدف", "gate": ["بند"], "task_ids": ["ك١", "جديد-x"]}],
    "tasks": [
        {"id": "ك١", "title": "مهمّة", "assignee": "claude-cloud", "block": "ب٩", "phase": "م٠", "effort": "S", "depends_on": [],
         "description": "وصف", "deliverable": "مخرج", "acceptance_evidence": "دليل", "unblocks": ["جديد-x"], "guide_id": "G1"},
        {"id": "جديد-x", "title": "ثانية", "assignee": "owner", "block": "ب٩", "phase": "م٠", "effort": "S", "depends_on": ["ك١"],
         "description": "وصف", "deliverable": "مخرج", "acceptance_evidence": "دليل"},
    ],
    "owner_steps": [{"order": 1, "guide_id": "G1", "title": "خطوة", "time": "٥ دقائق", "cost": "$0"}],
}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "docs/guides").mkdir(parents=True)
    for f in ov.MIRROR_FILES:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_text(f"# {f}\n", encoding="utf-8")
    (root / "docs/guides/G1.md").write_text("# G1\n", encoding="utf-8")
    (root / "docs/secret.md").write_text("لا يُنسخ\n", encoding="utf-8")
    (root / ov.PLAN).write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    return root


@pytest.fixture
def vault(tmp_path):
    return tmp_path / "Diwan-Vault"


def test_build_writes_mirror_board_tasks_steps_and_inbox(repo, vault):
    report = ov.build(vault, root=repo)
    out = vault / "Diwan"
    assert (out / "الوثائق/STATUS.md").read_text(encoding="utf-8").startswith("> **مرآةٌ للقراءة**")
    assert (out / "الأدلة/G1.md").is_file()
    assert "[[المهام/ك١|ك١]]" in (out / "لوحة المراحل.md").read_text(encoding="utf-8")
    assert "[[المهام/ك١|ك١]]" in (out / "المهام/جديد-x.md").read_text(encoding="utf-8")
    assert "- [ ] **1. خطوة** · [[الأدلة/G1|G1]]" in (out / "خطواتي.md").read_text(encoding="utf-8")
    assert (vault / "Inbox/اقرأني.md").is_file() and (vault / "Inbox/منجز").is_dir()
    assert report["kept_edited"] == [] and ov.check(vault, root=repo) == []


def test_vault_inside_the_repository_is_refused(repo):
    with pytest.raises(ov.VaultError) as e:
        ov.build(repo / "vault", root=repo)
    assert e.value.code == "vault_inside_repository"
    with pytest.raises(ov.VaultError):
        ov.build(repo, root=repo)


@pytest.mark.parametrize("name", ["diwan-sealed", "Sealed", "x_SEALED_y"])
def test_a_sealed_path_is_refused_for_the_vault(tmp_path, repo, name):
    with pytest.raises(ov.VaultError) as e:
        ov.build(tmp_path / name / "vault", root=repo)
    assert e.value.code == "sealed_path_refused"


def test_only_the_named_files_are_mirrored(repo, vault):
    ov.build(vault, root=repo)
    names = {p.name for p in (vault / "Diwan").rglob("*.md")}
    assert "secret.md" not in names
    (repo / "docs/guides/sealed-notes.md").write_text("x", encoding="utf-8")
    ov.build(vault, root=repo)
    assert not any("sealed" in p.name for p in (vault / "Diwan").rglob("*"))


def test_a_sealed_source_in_the_list_raises(repo, vault, monkeypatch):
    (repo / "docs/Sealed").mkdir()
    (repo / "docs/Sealed/x.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(ov, "MIRROR_FILES", ov.MIRROR_FILES + ("docs/Sealed/x.md",))
    with pytest.raises(ov.VaultError) as e:
        ov.build(vault, root=repo)
    assert e.value.code == "sealed_path_refused"


def test_an_owner_edit_is_kept_unless_forced(repo, vault):
    ov.build(vault, root=repo)
    board = vault / "Diwan/لوحة المراحل.md"
    board.write_text("ملاحظتي", encoding="utf-8")
    (repo / ov.PLAN).write_text(json.dumps({**PLAN, "title": "جديد"}, ensure_ascii=False), encoding="utf-8")
    report = ov.build(vault, root=repo)
    assert "لوحة المراحل.md" in report["kept_edited"]
    assert board.read_text(encoding="utf-8") == "ملاحظتي"
    ov.build(vault, root=repo, force=True)
    assert board.read_text(encoding="utf-8") != "ملاحظتي"


def test_the_steps_note_is_seeded_once_and_then_belongs_to_the_owner(repo, vault):
    ov.build(vault, root=repo)
    steps = vault / "Diwan/خطواتي.md"
    steps.write_text(steps.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    ov.build(vault, root=repo, force=True)
    assert "- [x]" in steps.read_text(encoding="utf-8")


def test_inbox_notes_survive_rebuilds_and_only_written_files_are_removed(repo, vault):
    ov.build(vault, root=repo)
    note = vault / "Inbox/طلب.md"
    note.write_text("# شغّل القياس الليلة\n", encoding="utf-8")
    mine = vault / "Diwan/ملاحظتي.md"
    mine.write_text("لي", encoding="utf-8")
    plan = {**PLAN, "tasks": PLAN["tasks"][:1], "phases": [{**PLAN["phases"][0], "task_ids": ["ك١"]}]}
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    report = ov.build(vault, root=repo)
    assert "المهام/جديد-x.md" in report["removed"]
    assert note.is_file() and mine.is_file()


def test_list_inbox_and_mark_done(repo, vault):
    ov.build(vault, root=repo)
    (vault / "Inbox/طلب.md").write_text("# شغّل القياس الليلة\nتفاصيل", encoding="utf-8")
    notes = ov.list_inbox(vault, root=repo)
    assert [n["title"] for n in notes] == ["شغّل القياس الليلة"]
    dest = ov.mark_done(vault, "Inbox/طلب.md", root=repo)
    assert dest.parent.name == "منجز" and ov.list_inbox(vault, root=repo) == []
    with pytest.raises(ov.VaultError):
        ov.mark_done(vault, "Diwan/لوحة المراحل.md", root=repo)
    with pytest.raises(ov.VaultError):
        ov.mark_done(vault, "Inbox/اقرأني.md", root=repo)


def test_check_reports_drift(repo, vault):
    ov.build(vault, root=repo)
    (repo / "docs/STATUS.md").write_text("# تغيّر\n", encoding="utf-8")
    assert ov.check(vault, root=repo) == ["stale الوثائق/STATUS.md"]


def test_the_real_repository_builds(tmp_path):
    report = ov.build(tmp_path / "vault", root=ROOT)
    assert report["written"] and (tmp_path / "vault/Diwan/لوحة المراحل.md").is_file()
    plan = json.loads((ROOT / ov.PLAN).read_text(encoding="utf-8"))
    assert len(list((tmp_path / "vault/Diwan/المهام").glob("*.md"))) == len(plan["tasks"])


def test_a_written_file_the_owner_edited_is_not_removed_when_it_leaves_the_plan(repo, vault):
    ov.build(vault, root=repo)
    task = vault / "Diwan/المهام/جديد-x.md"
    task.write_text("ملاحظتي على المهمّة", encoding="utf-8")
    plan = {**PLAN, "tasks": PLAN["tasks"][:1], "phases": [{**PLAN["phases"][0], "task_ids": ["ك١"]}]}
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    report = ov.build(vault, root=repo)
    assert "المهام/جديد-x.md" not in report["removed"] and task.read_text(encoding="utf-8") == "ملاحظتي على المهمّة"


def test_mark_done_never_overwrites_an_earlier_completed_note(repo, vault):
    ov.build(vault, root=repo)
    for body in ("# الأول\n", "# الثاني\n"):
        (vault / "Inbox/طلب.md").write_text(body, encoding="utf-8")
        ov.mark_done(vault, "Inbox/طلب.md", root=repo)
    done = sorted(p.read_text(encoding="utf-8") for p in (vault / "Inbox/منجز").glob("*.md"))
    assert done == ["# الأول\n", "# الثاني\n"]


def test_check_reports_an_obsolete_managed_note(repo, vault):
    ov.build(vault, root=repo)
    plan = {**PLAN, "tasks": PLAN["tasks"][:1], "phases": [{**PLAN["phases"][0], "task_ids": ["ك١"]}]}
    (repo / ov.PLAN).write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    assert "obsolete المهام/جديد-x.md" in ov.check(vault, root=repo)
    ov.build(vault, root=repo)
    assert ov.check(vault, root=repo) == []
