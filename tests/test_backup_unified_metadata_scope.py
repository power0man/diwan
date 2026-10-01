"""A user file named meta.json must never select the shared-memory restore scope."""
import pytest

from evaluation.memory_runner import _Wired


@pytest.mark.parametrize("content", [b"ordinary user text", b"[]", b'{"system_role":"unified_all_projects"}'],
                         ids=["text", "json-list", "forged-role"])
def test_backup_metadata_scope_ignores_user_workspace_meta_files(tmp_path, content):
    wired = _Wired(tmp_path.resolve() / "ui")
    try:
        first = wired.project("A")["id"]
        wired.session("A", "agent")
        workspace = wired.app.agent_workspace(wired.app.project(first))
        path = workspace / "meta.json"
        path.write_bytes(content)
        path.chmod(0o600)
        second = wired.project("B")["id"]
        item = wired.api("memory_remember", project=second, text="legacy-note")["item_id"]
        # This project has no reusable history. Its older receipt legitimately
        # has no history-scrub proof and must not be mistaken for a shared chat.
        wired.store("B").forget(item)
        snapshot = wired.backup()
        wired.restore(snapshot)
        restored = wired.app.agent_workspace(wired.app.project(first)) / "meta.json"
        assert restored.read_bytes() == content
        assert len(wired.api("memory", project=second)["receipts"]) == 1
    finally:
        wired.close()
