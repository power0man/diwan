"""A partially restored tree must never become a usable UI workspace."""
import pytest
from acceptance_m9 import SYNTHETIC_MODEL, SYNTHETIC_VERSION
from webui.server import LocalApp, UIError


def test_local_app_refuses_incomplete_restore_before_creating_lease(tmp_path):
    root = tmp_path.resolve() / "restoring"
    root.mkdir(mode=0o700)
    marker = root / ".restore-incomplete"
    marker.write_bytes(b"incomplete")
    marker.chmod(0o600)
    before = (marker.read_bytes(), marker.stat().st_mtime_ns, root.stat().st_mtime_ns)
    with pytest.raises(UIError, match="restore_incomplete"):
        LocalApp(root, model=SYNTHETIC_MODEL, model_version=SYNTHETIC_VERSION,
                 provider_factory=lambda: pytest.fail("provider construction"))
    assert list(root.iterdir()) == [marker]
    assert before == (marker.read_bytes(), marker.stat().st_mtime_ns, root.stat().st_mtime_ns)


def test_conversation_read_does_not_touch_ledger_mtime(tmp_path):
    from conversation import ChatSession
    session = ChatSession(tmp_path.resolve() / "chat", "test", model=SYNTHETIC_MODEL,
                          model_version=SYNTHETIC_VERSION)
    ledger = session.directory / "calls.jsonl"
    before = ledger.read_bytes(), ledger.stat().st_mtime_ns
    assert session.history() == []
    assert before == (ledger.read_bytes(), ledger.stat().st_mtime_ns)
