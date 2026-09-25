"""CLI contexts cannot cross tools; old journals are inspected without migration."""
import json

import pytest

from conversation import ChatSession, ConversationError
from core.contracts import Response, Usage
from services.assistant_workspace import AssistantWorkspace
from tools import chat, workspace


class Provider:
    is_local = True
    def __init__(self):
        self.requests = []
    def estimate_micros(self, request):
        return 0
    def complete(self, request):
        self.requests.append(request)
        return Response("answer", Usage(1, 1), "complete", 0)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    provider = Provider()
    monkeypatch.setattr(chat, "CHAT_ROOT", root / "chat")
    monkeypatch.setattr(workspace, "CHAT_ROOT", root / "workspace")
    for cli in (chat, workspace):
        monkeypatch.setattr(cli, "LEGACY_CHAT_ROOT", root / "legacy")
        monkeypatch.setattr(cli, "LocalChatProvider", lambda *args: provider)
    monkeypatch.setattr(workspace, "PREFERENCES_ROOT", root / "prefs")
    monkeypatch.setattr(workspace, "ARTIFACT_ROOT", root / "output")
    args = ["--session", "same", "--model", "synthetic", "--model-version", "v1"]
    return root, provider, args


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_default_cli_roots_are_distinct():
    assert chat.CHAT_ROOT != workspace.CHAT_ROOT
    assert chat.LEGACY_CHAT_ROOT == workspace.LEGACY_CHAT_ROOT


def test_same_session_identifier_has_independent_context_and_contract(configured, capsys):
    root, provider, args = configured
    assert chat.main(args + ["ask", "--turn", "one", "--message", "private chat"]) == 0
    capsys.readouterr()
    assert workspace.main(args + ["ask", "--turn", "one", "--message", "workspace question"]) == 0
    capsys.readouterr()
    assert len(provider.requests) == 2
    assert len(provider.requests[1].messages) == 2
    assert "private chat" not in provider.requests[1].messages[-1].content
    first = json.loads((root / "chat/same/manifest.json").read_text())
    second = json.loads((root / "workspace/same/manifest.json").read_text())
    assert first["purpose"] == "cli-chat" and second["purpose"] == "cli-workspace"
    assert provider.requests[0].idempotency_key != provider.requests[1].idempotency_key


def test_contract_still_refuses_cross_tool_if_roots_are_accidentally_equal(configured, monkeypatch, capsys):
    root, provider, args = configured
    monkeypatch.setattr(workspace, "CHAT_ROOT", chat.CHAT_ROOT)
    assert chat.main(args + ["ask", "--turn", "one", "--message", "private chat"]) == 0
    capsys.readouterr()
    before = snapshot(chat.CHAT_ROOT)
    assert workspace.main(args + ["ask", "--turn", "two", "--message", "workspace question"]) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "config_conflict"
    assert snapshot(chat.CHAT_ROOT) == before and len(provider.requests) == 1


def test_new_contract_cannot_be_reopened_without_purpose(tmp_path):
    root = tmp_path.resolve() / "sessions"
    ChatSession(root, "same", model="synthetic", model_version="v1", purpose="cli-chat")
    with pytest.raises(ConversationError, match="config_conflict"):
        ChatSession(root, "same", model="synthetic", model_version="v1")


def test_legacy_chat_requires_explicit_read_and_preserves_all_files(configured, capsys):
    root, provider, args = configured
    legacy = ChatSession(root / "legacy", "same", model="synthetic", model_version="v1")
    legacy.turn("old", "old question", provider)
    before = snapshot(root / "legacy")
    assert chat.main(args + ["ask", "--turn", "new", "--message", "new question"]) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "legacy_session_requires_explicit_selection"
    assert not (root / "chat").exists()
    assert chat.main(args + ["--legacy-session", "history"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["turns"][0]["text"] == "old question"
    assert snapshot(root / "legacy") == before and len(provider.requests) == 1


def test_legacy_workspace_replays_without_new_store_or_preferences(configured, capsys):
    root, provider, args = configured
    session = ChatSession(root / "legacy", "same", model="synthetic", model_version="v1")
    AssistantWorkspace(session, provider, None).ask("old", "old workspace question")
    before = snapshot(root / "legacy")
    assert workspace.main(args + ["--legacy-session", "replay", "--turn", "old"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["user_request"] == "old workspace question"
    assert result["replayed"] and len(provider.requests) == 1
    assert snapshot(root / "legacy") == before
    assert not any((root / name).exists() for name in ("workspace", "prefs", "output"))


def test_legacy_flag_cannot_resume_generation_or_create_missing_session(configured, capsys):
    root, provider, args = configured
    for cli in (chat, workspace):
        with pytest.raises(SystemExit) as raised:
            cli.main(args + ["--legacy-session", "ask", "--turn", "one", "--message", "question"])
        assert raised.value.code == 2
        capsys.readouterr()
    assert chat.main(args + ["--legacy-session", "history"]) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "legacy_session_missing"
    assert not (root / "legacy").exists() and not provider.requests


def test_pending_legacy_history_does_not_change_checkpoint(configured, monkeypatch, capsys):
    root, provider, args = configured
    session = ChatSession(root / "legacy", "same", model="synthetic", model_version="v1")
    original_save = session._save
    def save_then_interrupt(state):
        original_save(state)
        if state["turns"]:
            raise KeyboardInterrupt
    monkeypatch.setattr(session, "_save", save_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        session.turn("pending", "unexecuted request", provider)
    before = snapshot(root / "legacy")
    assert chat.main(args + ["--legacy-session", "history"]) == 0
    turn = json.loads(capsys.readouterr().out)["turns"][0]
    assert turn["error_code"] == "outcome_uncertain" and turn["status"] == "error"
    assert snapshot(root / "legacy") == before and not provider.requests
