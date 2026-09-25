import dataclasses
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from core.canonical import canonical_bytes, digest
from workspace_tools import FileDocument, TextWorkspace, WorkspaceError
import workspace_tools.files as module


@pytest.fixture
def setup(tmp_path):
    root = tmp_path.resolve()
    inputs, outputs = root / "inputs", root / "outputs"
    inputs.mkdir()
    return inputs, outputs, TextWorkspace(inputs, outputs)


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def proposal(workspace, path="answer.txt", content="نص عربي", request_id="request-1"):
    return workspace.propose_write(path, content, request_id)


def test_read_explicit_file_immutable_document_no_input_mutation(setup):
    inputs, outputs, workspace = setup
    path = inputs / "مذكرة.txt"
    path.write_text("سطر أول\nسطر ثان\t\r\n", encoding="utf-8")
    before = snapshot(inputs)
    doc = workspace.read_text("مذكرة.txt")
    assert isinstance(doc, FileDocument)
    assert doc.relative_path == "مذكرة.txt"
    assert doc.size_bytes == len(path.read_bytes())
    assert doc.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert doc.content == path.read_bytes().decode("utf-8")
    with pytest.raises(dataclasses.FrozenInstanceError):
        doc.content = "changed"
    assert snapshot(inputs) == before
    assert not (inputs / ".diwan-tools").exists()


def test_missing_input_opened_only_on_explicit_read_and_never_created(tmp_path):
    root = tmp_path.resolve()
    workspace = TextWorkspace(root / "absent", root / "outputs")
    assert not (root / "absent").exists()
    with pytest.raises(WorkspaceError, match="file_missing"):
        workspace.read_text("doc.txt")
    assert not (root / "absent").exists()


@pytest.mark.parametrize("relative", ["../outside", "/absolute", "a/../b", "a//b", "./a", "", "a\\b", "a\x00b", "\ud800"])
def test_read_and_write_reject_nonrelative_paths(setup, relative):
    _, _, workspace = setup
    with pytest.raises(WorkspaceError, match="path_invalid"):
        workspace.read_text(relative)
    with pytest.raises(WorkspaceError, match="path_invalid"):
        proposal(workspace, path=relative)


@pytest.mark.parametrize("relative", [".git/config", ".GIT/config", "x/.codex/config", ".agents/rules", ".diwan-tools/writes/state.json"])
def test_internal_control_paths_protected(setup, relative):
    _, _, workspace = setup
    with pytest.raises(WorkspaceError, match="path_protected"):
        workspace.read_text(relative)
    with pytest.raises(WorkspaceError, match="path_protected"):
        proposal(workspace, path=relative)


@pytest.mark.parametrize("relative", [".hidden", "nested/.hidden/file"])
def test_hidden_output_rejected(setup, relative):
    with pytest.raises(WorkspaceError, match="path_protected"):
        proposal(setup[2], path=relative)


def test_read_symlink_file_and_parent_rejected(setup, tmp_path):
    inputs, _, workspace = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("private")
    (inputs / "link.txt").symlink_to(outside / "secret.txt")
    (inputs / "linked-directory").symlink_to(outside, target_is_directory=True)
    for path in ("link.txt", "linked-directory/secret.txt"):
        with pytest.raises(WorkspaceError, match="unsafe_path"):
            workspace.read_text(path)


def test_symlink_selected_roots_rejected(tmp_path):
    root = tmp_path.resolve()
    actual = root / "actual"
    actual.mkdir()
    linked = root / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    workspace = TextWorkspace(linked, root / "out")
    with pytest.raises(WorkspaceError, match="unsafe_path"):
        workspace.read_text("doc.txt")
    with pytest.raises(WorkspaceError, match="unsafe_path"):
        TextWorkspace(actual, linked)


def test_hardlinked_input_and_nonregular_input_rejected(setup, tmp_path):
    inputs, _, workspace = setup
    outside = tmp_path / "outside.txt"
    outside.write_text("hidden")
    os.link(outside, inputs / "hardlink.txt")
    (inputs / "directory").mkdir()
    os.mkfifo(inputs / "pipe")
    for path in ("hardlink.txt", "directory", "pipe"):
        with pytest.raises(WorkspaceError, match="unsafe_path"):
            workspace.read_text(path)


@pytest.mark.parametrize("raw", [b"\xff", b"text\x00binary", b"escape\x1b[2J", "نص\u0085".encode()])
def test_binary_invalid_utf8_control_text_rejected(setup, raw):
    inputs, _, workspace = setup
    (inputs / "data.txt").write_bytes(raw)
    with pytest.raises(WorkspaceError, match="text_invalid"):
        workspace.read_text("data.txt")


def test_byte_limit_and_exact_boundary(setup):
    inputs, _, workspace = setup
    (inputs / "exact.txt").write_bytes(b"a" * 65536)
    assert workspace.read_text("exact.txt").size_bytes == 65536
    (inputs / "large.txt").write_bytes(b"a" * 65537)
    with pytest.raises(WorkspaceError, match="file_too_large"):
        workspace.read_text("large.txt")
    with pytest.raises(WorkspaceError, match="file_too_large"):
        proposal(workspace, content="س" * 32769)


@pytest.mark.parametrize("content", [None, 3, "\ud800", "\x00", "\x1b[2J", "\u0085"])
def test_proposal_requires_valid_text(setup, content):
    with pytest.raises(WorkspaceError, match="text_invalid"):
        proposal(setup[2], content=content)


def test_proposal_review_does_not_write_target_or_mutate_state(setup):
    inputs, outputs, workspace = setup
    p = proposal(workspace)
    assert p["status"] == "proposed" and not (outputs / p["path"]).exists()
    before = snapshot(outputs)
    reopened = TextWorkspace(inputs, outputs)
    review = reopened.review(p["proposal_id"])
    assert review == {**p, "replayed": True}
    assert snapshot(outputs) == before
    assert reopened.propose_write("answer.txt", "نص عربي", "request-1") == review
    assert snapshot(outputs) == before


def test_explicit_digest_required_then_apply_replays(setup):
    inputs, outputs, workspace = setup
    p = proposal(workspace)
    before = snapshot(outputs)
    with pytest.raises(WorkspaceError, match="approval_mismatch"):
        workspace.apply(p["proposal_id"], "0" * 64)
    assert snapshot(outputs) == before
    result = workspace.apply(p["proposal_id"], p["sha256"])
    assert result["status"] == "applied" and not result["replayed"]
    assert (outputs / p["path"]).read_text() == p["content"]
    assert stat.S_IMODE((outputs / p["path"]).stat().st_mode) == 0o600
    before = snapshot(outputs)
    replay = TextWorkspace(inputs, outputs).apply(p["proposal_id"], p["sha256"])
    assert replay == {**result, "replayed": True}
    assert snapshot(outputs) == before


@pytest.mark.parametrize("content", ["original", "نص عربي"])
def test_preexisting_file_never_overwritten_or_adopted_even_identical(setup, content):
    inputs, outputs, workspace = setup
    target = outputs / "answer.txt"
    target.write_text(content)
    p = proposal(workspace)
    before = (target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns)
    result = workspace.apply(p["proposal_id"], p["sha256"])
    assert result["status"] == "error" and result["error_code"] == "target_exists"
    assert TextWorkspace(inputs, outputs).apply(p["proposal_id"], p["sha256"])["replayed"]
    assert (target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns) == before


def test_no_overwrite_between_different_proposals(setup):
    _, outputs, workspace = setup
    first = proposal(workspace)
    workspace.apply(first["proposal_id"], first["sha256"])
    second = proposal(workspace, content="different", request_id="second")
    assert workspace.apply(second["proposal_id"], second["sha256"])["error_code"] == "target_exists"
    assert (outputs / "answer.txt").read_text() == "نص عربي"


@pytest.mark.parametrize("change", [{"content": "different"}, {"path": "other.txt"}])
def test_request_id_conflict(setup, change):
    workspace = setup[2]
    proposal(workspace)
    with pytest.raises(WorkspaceError, match="proposal_conflict"):
        proposal(workspace, **change)


def test_nested_existing_output_directory_and_missing_parent(setup):
    _, outputs, workspace = setup
    (outputs / "nested").mkdir()
    p = proposal(workspace, path="nested/answer.txt")
    assert workspace.apply(p["proposal_id"], p["sha256"])["status"] == "applied"
    other = proposal(workspace, path="absent/answer.txt", request_id="second")
    with pytest.raises(WorkspaceError, match="file_missing"):
        workspace.apply(other["proposal_id"], other["sha256"])
    assert not (outputs / "absent").exists()
    assert workspace.review(other["proposal_id"])["status"] == "proposed"


def test_output_symlink_parent_and_preexisting_symlink_target_safe(setup, tmp_path):
    _, outputs, workspace = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    (outputs / "linked").symlink_to(outside, target_is_directory=True)
    p = proposal(workspace, path="linked/answer.txt")
    with pytest.raises(WorkspaceError, match="unsafe_path"):
        workspace.apply(p["proposal_id"], p["sha256"])
    (outputs / "answer.txt").symlink_to(outside / "untouched.txt")
    second = proposal(workspace, request_id="second")
    assert workspace.apply(second["proposal_id"], second["sha256"])["error_code"] == "target_exists"
    assert list(outside.iterdir()) == []


def test_crash_after_full_write_recovers_from_ownership_receipt(setup, monkeypatch):
    inputs, outputs, workspace = setup
    p = proposal(workspace)
    save = workspace._save
    def crash(fd, state):
        if state["proposals"][-1]["status"] == "applied":
            raise KeyboardInterrupt()
        save(fd, state)
    monkeypatch.setattr(workspace, "_save", crash)
    with pytest.raises(KeyboardInterrupt):
        workspace.apply(p["proposal_id"], p["sha256"])
    target = outputs / p["path"]
    before = (target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns)
    reopened = TextWorkspace(inputs, outputs)
    assert reopened.review(p["proposal_id"])["status"] == "pending"
    result = reopened.apply(p["proposal_id"], p["sha256"])
    assert result["status"] == "applied" and result["replayed"]
    assert (target.read_bytes(), target.stat().st_ino, target.stat().st_mtime_ns) == before


@pytest.mark.parametrize("save_owner", [False, True])
def test_crash_before_content_unknown_is_closed_not_repeated(setup, monkeypatch, save_owner):
    inputs, outputs, workspace = setup
    p = proposal(workspace)
    save = workspace._save
    def crash(fd, state):
        if state["proposals"][-1]["owner"] is not None:
            if save_owner:
                save(fd, state)
            raise KeyboardInterrupt()
        save(fd, state)
    monkeypatch.setattr(workspace, "_save", crash)
    with pytest.raises(KeyboardInterrupt):
        workspace.apply(p["proposal_id"], p["sha256"])
    target = outputs / p["path"]
    assert target.read_bytes() == b""
    before = (target.stat().st_ino, target.stat().st_mtime_ns)
    reopened = TextWorkspace(inputs, outputs)
    result = reopened.apply(p["proposal_id"], p["sha256"])
    assert result["status"] == "error" and result["error_code"] == "outcome_uncertain"
    assert target.read_bytes() == b"" and (target.stat().st_ino, target.stat().st_mtime_ns) == before
    assert reopened.apply(p["proposal_id"], p["sha256"])["error_code"] == "outcome_uncertain"


def test_unknown_pending_never_adopts_unowned_identical_file(setup, monkeypatch):
    inputs, outputs, workspace = setup
    p = proposal(workspace)
    actual_open = module.os.open
    def crash(name, flags, *args, **kwargs):
        if name == p["path"] and flags & os.O_CREAT:
            raise KeyboardInterrupt()
        return actual_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(module.os, "open", crash)
    with pytest.raises(KeyboardInterrupt):
        workspace.apply(p["proposal_id"], p["sha256"])
    monkeypatch.setattr(module.os, "open", actual_open)
    target = outputs / p["path"]
    target.write_text(p["content"])
    target.chmod(0o600)
    result = TextWorkspace(inputs, outputs).apply(p["proposal_id"], p["sha256"])
    assert result["error_code"] == "outcome_uncertain"


@pytest.mark.parametrize("change", ["content", "replacement", "deleted", "permissions", "hardlink"])
def test_applied_output_change_refused_without_rewrite(setup, change):
    _, outputs, workspace = setup
    p = proposal(workspace)
    workspace.apply(p["proposal_id"], p["sha256"])
    target = outputs / p["path"]
    if change == "content":
        target.write_text("changed")
    elif change == "replacement":
        temp = outputs / "replacement.txt"
        temp.write_text(p["content"])
        temp.chmod(0o600)
        temp.replace(target)
    elif change == "deleted":
        target.unlink()
    elif change == "permissions":
        target.chmod(0o644)
    else:
        os.link(target, outputs / "second-link.txt")
    before = snapshot(outputs)
    with pytest.raises(WorkspaceError, match="applied_output_changed"):
        workspace.apply(p["proposal_id"], p["sha256"])
    assert snapshot(outputs) == before


def test_input_root_replacement_rejected(setup):
    inputs, _, workspace = setup
    (inputs / "doc.txt").write_text("original")
    workspace.read_text("doc.txt")
    inputs.rename(inputs.with_name("old-inputs"))
    inputs.mkdir()
    (inputs / "doc.txt").write_text("new")
    with pytest.raises(WorkspaceError, match="root_changed"):
        workspace.read_text("doc.txt")


def test_artifact_root_replacement_rejected(setup):
    _, outputs, workspace = setup
    outputs.rename(outputs.with_name("old-outputs"))
    outputs.mkdir()
    with pytest.raises(WorkspaceError, match="root_changed"):
        proposal(workspace)


@pytest.mark.parametrize("filename", ["manifest.json", "state.json", "store.lock"])
def test_private_store_symlink_rejected(setup, filename, tmp_path):
    _, outputs, workspace = setup
    target = outputs / ".diwan-tools/writes" / filename
    other = tmp_path / "other"
    other.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(other)
    with pytest.raises(WorkspaceError):
        proposal(workspace)


@pytest.mark.parametrize("filename", ["manifest.json", "state.json", "store.lock"])
def test_private_store_hardlink_rejected(setup, filename, tmp_path):
    _, outputs, workspace = setup
    os.link(outputs / ".diwan-tools/writes" / filename, tmp_path / "other")
    with pytest.raises(WorkspaceError):
        proposal(workspace)


def test_private_store_permissions(setup):
    _, outputs, workspace = setup
    proposal(workspace)
    for directory in (outputs, outputs / ".diwan-tools", outputs / ".diwan-tools/writes"):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600
               for p in (outputs / ".diwan-tools/writes").iterdir())


def test_nonblocking_store_lock(setup):
    _, outputs, workspace = setup
    with (outputs / ".diwan-tools/writes/store.lock").open("r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(WorkspaceError, match="workspace_busy"):
            proposal(workspace)


@pytest.mark.parametrize("corruption", ["hash", "content", "owner", "status", "duplicate", "deep"])
def test_corrupt_private_state_fails_closed(setup, corruption):
    _, outputs, workspace = setup
    p = proposal(workspace)
    path = outputs / ".diwan-tools/writes/state.json"
    envelope = json.loads(path.read_text())
    entry = envelope["state"]["proposals"][0]
    if corruption == "deep":
        path.write_text("[" * 2000 + "0" + "]" * 2000)
    elif corruption == "duplicate":
        path.write_text('{"state":{},"state":{}}')
    else:
        if corruption == "content":
            entry["content"] = "forged"
        elif corruption == "owner":
            entry["owner"] = {"device": True, "inode": "1"}
        elif corruption == "status":
            entry["status"] = "applied"
        envelope["sha256"] = "0" * 64 if corruption == "hash" else digest(envelope["state"])
        path.write_bytes(canonical_bytes(envelope))
    with pytest.raises(WorkspaceError):
        workspace.apply(p["proposal_id"], p["sha256"])
    assert not (outputs / p["path"]).exists()


def test_empty_text_file_supported(setup):
    inputs, outputs, workspace = setup
    (inputs / "empty.txt").write_bytes(b"")
    assert workspace.read_text("empty.txt").content == ""
    p = proposal(workspace, content="")
    assert workspace.apply(p["proposal_id"], p["sha256"])["status"] == "applied"
    assert (outputs / p["path"]).read_bytes() == b""


@pytest.mark.parametrize("limit", [0, True, 65537, None])
def test_invalid_limits(tmp_path, limit):
    with pytest.raises(WorkspaceError, match="limit_invalid"):
        TextWorkspace(tmp_path.resolve(), tmp_path.resolve() / "outputs", max_bytes=limit)


def test_source_root_independent_of_artifact_store(setup, tmp_path):
    inputs, outputs, workspace = setup
    (inputs / "first.txt").write_text("first")
    assert workspace.read_text("first.txt").content == "first"
    other = tmp_path.resolve() / "other-inputs"
    other.mkdir()
    (other / "second.txt").write_text("second")
    p = proposal(workspace)
    other_workspace = TextWorkspace(other, outputs)
    assert other_workspace.read_text("second.txt").content == "second"
    assert other_workspace.review(p["proposal_id"])["content"] == p["content"]
    assert not (other / ".diwan-tools").exists()


def test_review_and_apply_need_no_source_and_work_after_source_deleted(setup):
    inputs, outputs, workspace = setup
    p = proposal(workspace)
    inputs.rmdir()
    reopened = TextWorkspace(None, outputs)
    with pytest.raises(WorkspaceError, match="read_root_required"):
        reopened.read_text("doc.txt")
    assert reopened.review(p["proposal_id"])["content"] == p["content"]
    assert reopened.apply(p["proposal_id"], p["sha256"])["status"] == "applied"
    fresh = reopened.propose_write("second.txt", "second", "second")
    assert fresh["status"] == "proposed"


@pytest.mark.parametrize("format_char", ["\u200f", "\u200e", "\u200d", "\u200c", "\u202e", "\ue000"])
def test_arabic_format_and_private_use_characters_remain_exact_text(setup, format_char):
    inputs, outputs, workspace = setup
    content = "مرحبا" + format_char + " بالعربية"
    raw = content.encode("utf-8")
    (inputs / "note.txt").write_bytes(raw)
    doc = workspace.read_text("note.txt")
    assert doc.content == content and doc.sha256 == hashlib.sha256(raw).hexdigest()
    p = proposal(workspace, content=content)
    assert workspace.apply(p["proposal_id"], p["sha256"])["status"] == "applied"
    assert (outputs / p["path"]).read_bytes() == raw


def test_largest_allowed_escaped_proposal_collection_remains_readable(setup):
    _, outputs, workspace = setup
    first = proposal(workspace, content="\t" * 65536)
    path = outputs / ".diwan-tools/writes/state.json"
    envelope = json.loads(path.read_text())
    template = envelope["state"]["proposals"][0]
    proposals = []
    for i in range(module.MAX_PROPOSALS):
        request_id = f"request-{i}"
        proposals.append({**template, "request_id": request_id,
                          "proposal_id": workspace._proposal_id(request_id, first["path"], first["sha256"])})
    envelope["state"]["proposals"] = proposals
    envelope["sha256"] = digest(envelope["state"])
    path.write_bytes(canonical_bytes(envelope))
    assert path.stat().st_size > 32 * 1024 * 1024
    assert workspace.review(proposals[-1]["proposal_id"])["content"] == first["content"]
