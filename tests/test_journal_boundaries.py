"""Synthetic filesystem adversaries only; no owner data or Docker."""
import hashlib
import os

import pytest

from agent.journal import JOURNAL_DIR, Journal, JournalRefused


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_journal_alias_never_appends_to_an_outside_file(tmp_path, alias):
    root = tmp_path / "workspace"
    root.mkdir()
    store = root / JOURNAL_DIR
    store.mkdir(mode=0o700)
    outside = tmp_path / "outside.jsonl"
    before = b"OWNER SYNTHETIC FILE\n"
    outside.write_bytes(before)
    if alias == "symlink":
        (store / "journal.jsonl").symlink_to(outside)
    else:
        os.link(outside, store / "journal.jsonl")
    refused = False
    try:
        Journal(root).write_file("new.txt", "candidate")
    except JournalRefused:
        refused = True
    assert outside.read_bytes() == before, "journal append escaped the workspace"
    assert not (root / "new.txt").exists()
    assert refused


def test_corrupt_restore_blob_never_replaces_the_current_file(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("original")
    journal = Journal(tmp_path)
    action = journal.write_file("note.txt", "candidate")
    blob = journal.blobs / action.before_sha256
    blob.write_bytes(b"CORRUPTED RESTORE DATA")
    refused = False
    try:
        journal.revert(action.action_id)
    except JournalRefused:
        refused = True
    assert target.read_text() == "candidate", "unverified blob was restored"
    assert refused


@pytest.mark.parametrize("component", ["store", "blobs", "parent", "root", "root_ancestor"])
def test_directory_links_never_read_or_write_outside(tmp_path, component):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "note.txt"
    marker.write_text("outside")
    target = root / "note.txt"
    target.write_text("original")
    relative = "note.txt"
    supplied_root = root
    if component == "store":
        (root / JOURNAL_DIR).symlink_to(outside, target_is_directory=True)
    elif component == "blobs":
        (root / JOURNAL_DIR).mkdir(mode=0o700)
        (root / JOURNAL_DIR / "blobs").symlink_to(outside, target_is_directory=True)
    elif component == "parent":
        (root / "nested").symlink_to(outside, target_is_directory=True)
        relative = "nested/new/note.txt"
    elif component == "root":
        supplied_root = tmp_path / "linked"
        supplied_root.symlink_to(outside, target_is_directory=True)
    else:
        link = tmp_path / "linked"
        link.symlink_to(tmp_path, target_is_directory=True)
        supplied_root = link / "workspace"
    before = {p.name: p.read_bytes() for p in outside.iterdir()}
    with pytest.raises(JournalRefused):
        Journal(supplied_root).write_file(relative, "candidate")
    assert {p.name: p.read_bytes() for p in outside.iterdir()} == before
    assert target.read_text() == "original"


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
@pytest.mark.parametrize("operation", ["write", "revert"])
def test_blob_aliases_are_refused_without_touching_either_file(tmp_path, alias, operation):
    target = tmp_path / "note.txt"
    target.write_text("original")
    journal = Journal(tmp_path)
    action = journal.write_file("note.txt", "candidate")
    blob = journal.blobs / action.before_sha256
    outside = tmp_path / "external-original.txt"
    outside.write_text("original")
    blob.unlink()
    if alias == "symlink":
        blob.symlink_to(outside)
    else:
        os.link(outside, blob)
    if operation == "write":
        target.write_text("original")
    before = target.read_bytes()
    with pytest.raises(JournalRefused):
        if operation == "write":
            journal.write_file("note.txt", "second candidate")
        else:
            journal.revert(action.action_id)
    assert target.read_bytes() == before
    assert outside.read_text() == "original"


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_target_aliases_cannot_be_overwritten_or_used_as_restore_input(tmp_path, alias):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    target = root / "note.txt"
    if alias == "symlink":
        target.symlink_to(outside)
    else:
        os.link(outside, target)
    with pytest.raises(JournalRefused):
        Journal(root).write_file("note.txt", "candidate")
    assert outside.read_text() == "outside"


def test_root_replacement_is_not_adopted_by_an_existing_journal(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    journal = Journal(root)
    root.rename(tmp_path / "original-root")
    root.mkdir()
    (root / "owner.txt").write_text("keep")
    with pytest.raises(JournalRefused, match="workspace_changed"):
        journal.write_file("new.txt", "candidate")
    assert sorted(p.name for p in root.iterdir()) == ["owner.txt"]


def test_reassigning_root_does_not_retarget_the_journal(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    journal = Journal(root)
    other = tmp_path / "other"
    other.mkdir()
    journal.root = other
    with pytest.raises(JournalRefused, match="workspace_changed"):
        journal.write_file("new.txt", "candidate")
    assert list(other.iterdir()) == []


def test_parent_swapped_after_snapshot_never_redirects_the_write(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    parent = root / "nested"
    parent.mkdir()
    (parent / "note.txt").write_text("original")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "note.txt").write_text("outside")
    journal = Journal(root)
    append = journal._append
    def swap(action):
        append(action)
        parent.rename(root / "old-nested")
        parent.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(journal, "_append", swap)
    with pytest.raises(JournalRefused):
        journal.write_file("nested/note.txt", "candidate")
    assert (outside / "note.txt").read_text() == "outside"
    assert (root / "old-nested/note.txt").read_text() == "original"


@pytest.mark.parametrize("operation", ["write", "revert"])
def test_late_owner_edit_is_preserved_before_atomic_placement(tmp_path, monkeypatch, operation):
    target = tmp_path / "note.txt"
    target.write_text("original")
    journal = Journal(tmp_path)
    action = journal.write_file("note.txt", "candidate")
    place = journal._place
    def edit(path, raw):
        target.write_text("owner edit")
        place(path, raw)
    monkeypatch.setattr(journal, "_place", edit)
    with pytest.raises(JournalRefused, match="changed_since_action"):
        if operation == "write":
            journal.write_file("note.txt", "second candidate")
        else:
            journal.revert(action.action_id)
    assert target.read_text() == "owner edit"


def test_blob_changed_between_snapshot_and_restore_is_detected(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    target.write_text("original")
    journal = Journal(tmp_path)
    action = journal.write_file("note.txt", "candidate")
    blob = journal.blobs / action.before_sha256
    check = journal._blob_directory
    def corrupt(**kwargs):
        fd = check(**kwargs)
        blob.write_text("corrupt")
        return fd
    monkeypatch.setattr(journal, "_blob_directory", corrupt)
    with pytest.raises(JournalRefused, match="blob_corrupt"):
        journal.revert(action.action_id)
    assert target.read_text() == "candidate"


def test_lock_alias_is_refused_before_any_target_change(tmp_path):
    store = tmp_path / JOURNAL_DIR
    store.mkdir(mode=0o700)
    outside = tmp_path / "outside-lock"
    outside.write_text("owner")
    (store / "journal.lock").symlink_to(outside)
    with pytest.raises(JournalRefused):
        Journal(tmp_path).write_file("note.txt", "candidate")
    assert outside.read_text() == "owner"
    assert not (tmp_path / "note.txt").exists()


@pytest.mark.parametrize("canonical,alternate", [("Note.txt", "note.txt"), ("café.txt", "cafe\u0301.txt")])
def test_native_filename_aliases_are_refused(tmp_path, canonical, alternate):
    target = tmp_path / canonical
    target.write_text("original")
    actual = next(p.name for p in tmp_path.iterdir())
    alias = alternate if actual != alternate else canonical
    if not (tmp_path / alias).exists() or not (tmp_path / alias).samefile(target):
        pytest.skip("filesystem does not alias these spellings")
    with pytest.raises(JournalRefused, match="path_alias"):
        Journal(tmp_path).write_file(alias, "candidate")
    assert target.read_text() == "original"


def test_hash_name_is_verified_when_reusing_an_existing_blob(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("original")
    journal = Journal(tmp_path)
    first = journal.write_file("note.txt", "candidate")
    target.write_text("original")
    (journal.blobs / first.before_sha256).write_text("corrupt")
    with pytest.raises(JournalRefused, match="blob_corrupt"):
        journal.write_file("note.txt", "second candidate")
    assert target.read_text() == "original"
    assert len(journal.actions()) == 1


def test_successful_restore_preserves_action_schema_and_exact_binary_bytes(tmp_path):
    target = tmp_path / "note.txt"
    original = b"original\x00\xff\n"
    target.write_bytes(original)
    journal = Journal(tmp_path)
    action = journal.write_file("note.txt", "candidate")
    assert set(action.__dict__) == {"action_id", "kind", "path", "before_sha256", "after_sha256", "at"}
    assert action.before_sha256 == hashlib.sha256(original).hexdigest()
    assert Journal(tmp_path).revert(action.action_id)["status"] == "reverted"
    assert target.read_bytes() == original
    assert journal.revert(action.action_id)["status"] == "already_reverted"


def test_legacy_revert_creates_a_lock_before_mutation_and_excludes_another_writer(tmp_path, monkeypatch):
    target = tmp_path / "note.txt"
    target.write_text("original")
    journal = Journal(tmp_path)
    action = journal.write_file("note.txt", "candidate")
    (journal.dir / "journal.lock").unlink()
    os.chmod(journal.path, 0o644)  # The prior implementation's unchanged Action format.
    original_place = journal._place
    observed = []
    def place(path, raw):
        assert (journal.dir / "journal.lock").is_file()
        with pytest.raises(JournalRefused, match="journal_busy"):
            Journal(tmp_path).write_file("other.txt", "second writer")
        observed.append(True)
        original_place(path, raw)
    monkeypatch.setattr(journal, "_place", place)
    assert journal.revert(action.action_id)["status"] == "reverted"
    assert observed == [True]
    assert target.read_text() == "original"
    assert not (tmp_path / "other.txt").exists()


def test_legacy_read_does_not_create_a_lock_or_change_the_journal(tmp_path):
    journal = Journal(tmp_path)
    action = journal.write_file("note.txt", "candidate")
    (journal.dir / "journal.lock").unlink()
    before = journal.path.read_bytes()
    assert Journal(tmp_path).actions() == [action]
    assert journal.path.read_bytes() == before
    assert not (journal.dir / "journal.lock").exists()


def test_revert_refuses_a_target_hardlink_added_after_the_action(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    journal = Journal(root)
    action = journal.write_file("note.txt", "candidate")
    outside = tmp_path / "outside.txt"
    os.link(root / "note.txt", outside)
    with pytest.raises(JournalRefused, match="unsafe_path"):
        journal.revert(action.action_id)
    assert outside.read_text() == "candidate"
    assert (root / "note.txt").read_text() == "candidate"


def test_owner_edit_before_revert_unlink_is_preserved(tmp_path, monkeypatch):
    journal = Journal(tmp_path)
    action = journal.write_file("note.txt", "candidate")
    target = tmp_path / "note.txt"
    check = journal._check_parent
    def edit(fd, relative):
        check(fd, relative)
        target.write_text("owner edit")
    monkeypatch.setattr(journal, "_check_parent", edit)
    with pytest.raises(JournalRefused, match="changed_since_action"):
        journal.revert(action.action_id)
    assert target.read_text() == "owner edit"
