"""عقد CLI: استئناف بلا نداء، حالة غير مكتملة صريحة، وعرض طرفية مأمون."""
import json
import sys
from io import StringIO

import pytest

from core.contracts import Response, Usage
from tools import chat


class Provider:
    is_local = True
    name = "fixture"
    model = "fixture"

    def __init__(self, *args):
        self.calls = 0
        self.stop = "complete"

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        return Response("جواب\x1b[2J", Usage(1, 1), self.stop, 0)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    provider = Provider()
    monkeypatch.setattr(chat, "CHAT_ROOT", tmp_path / "chat")
    monkeypatch.setattr(chat, "LocalChatProvider", lambda *a: provider)
    args = ["--session", "example", "--model", "fixture", "--model-version", "a" * 64]
    return args, provider


def test_cli_replay_and_history_do_not_call_provider(setup, capsys):
    args, provider = setup
    command = args + ["ask", "--turn", "one", "--message", "مرحبا"]
    assert chat.main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["verification"] == "unverified"
    assert chat.main(command) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["replayed"] is True
    assert second["content"] == first["content"]
    assert chat.main(args + ["history"]) == 0
    history = json.loads(capsys.readouterr().out)
    assert len(history["turns"]) == 1
    assert history["release_ready"] is False
    assert provider.calls == 1


def test_stdin_and_conflicting_turn_named(setup, monkeypatch, capsys):
    args, provider = setup
    monkeypatch.setattr(sys, "stdin", StringIO("السؤال"))
    assert chat.main(args + ["ask", "--turn", "one", "--stdin"]) == 0
    capsys.readouterr()
    assert chat.main(args + ["ask", "--turn", "one", "--message", "مختلف"]) == 2
    assert json.loads(capsys.readouterr().out)["error_code"]
    assert provider.calls == 1


def test_truncated_answer_has_nonzero_exit(setup, capsys):
    args, provider = setup
    provider.stop = "max_output"
    assert chat.main(args + ["ask", "--turn", "one", "--message", "سؤال"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "truncated"


def test_terminal_control_characters_are_visible_not_executed():
    shown = chat.terminal_text("نص\x1b]52;c;secret\x07\r\b\u202e\n\t")
    assert "\x1b" not in shown and "\x07" not in shown
    assert "\r" not in shown and "\b" not in shown and "\u202e" not in shown
    assert shown.endswith("\n\t")


def test_interactive_history_and_exit(setup, monkeypatch, capsys):
    args, provider = setup
    lines = iter(["مرحبا", "/history", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(lines))
    assert chat.main(args + ["chat"]) == 0
    out = capsys.readouterr()
    assert "\x1b" not in out.out
    assert "غير موثق" in out.err
    assert provider.calls == 1


def test_interruption_returns_exit130(setup, monkeypatch, capsys):
    args, _ = setup

    def interrupt(_):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    assert chat.main(args + ["chat"]) == 130
    assert "معرف الجولة" in capsys.readouterr().err
