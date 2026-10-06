"""عقدُ محوِّل الوكيل: لا أعلامَ تجاوز، وبيئةٌ بقائمة سماح، وقراءةُ مخرجات Claude وCodex المنظَّمة، ورموزُ التعذّر."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from team.adapters import base
from team.adapters.claude import ClaudeAdapter
from team.adapters.codex import CodexAdapter


def test_bypass_flags_are_refused():
    for flag in base.FORBIDDEN_FLAGS:
        with pytest.raises(base.BypassFlagError):
            base.assert_no_bypass(["x", flag])


def test_bypass_values_are_refused():
    for argv in (["x", "--permission-mode", "bypassPermissions"], ["x", "-s", "danger-full-access"], ["x", "--approval-mode=yolo"]):
        with pytest.raises(base.BypassFlagError):
            base.assert_no_bypass(argv)


def test_claude_and_codex_argv_are_clean_and_sandboxed(tmp_path):
    claude = ClaudeAdapter(binary=tmp_path / "claude").work_argv(tmp_path, 2.5, tmp_path)
    codex = CodexAdapter(binary=tmp_path / "codex").work_argv(tmp_path, 2.5, tmp_path)
    for argv in (claude, codex, ClaudeAdapter(binary=tmp_path / "claude").review_argv(tmp_path, "main"),
                 CodexAdapter(binary=tmp_path / "codex").review_argv(tmp_path, "main")):
        base.assert_no_bypass(argv)
    assert "--bare" not in claude and claude[claude.index("--setting-sources") + 1] == "project" and "--strict-mcp-config" in claude
    assert claude[claude.index("--permission-mode") + 1] == "acceptEdits"
    assert "sandbox" in claude[claude.index("--settings") + 1]
    assert codex[codex.index("-s") + 1] == "workspace-write" and codex[-1] == "-"


def test_worker_env_is_an_allowlist(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "x")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = base.worker_env()
    assert "SECRET_TOKEN" not in env and env["PATH"] == "/usr/bin" and env["RORO_DISABLE"] == "1"


def test_unavailable_codes_are_named():
    assert base.unavailable_code(1, "You've hit your usage limit. Try again later.") == "quota_exhausted"
    assert base.unavailable_code(127, "") == "binary_missing"
    assert base.unavailable_code(1, "AssertionError in test") is None


def test_claude_parse_work_reads_the_json_result(tmp_path):
    adapter = ClaudeAdapter(binary=tmp_path / "claude")
    ok = adapter.parse_work(0, json.dumps({"result": "تمّ", "session_id": "s1", "total_cost_usd": 0.12}), "", tmp_path)
    assert ok.ok and ok.session_id == "s1" and ok.cost_estimate_usd == 0.12 and ok.text == "تمّ"
    bad = adapter.parse_work(0, json.dumps({"result": "x", "is_error": True}), "", tmp_path)
    assert not bad.ok


def test_codex_parse_work_reads_jsonl_and_the_last_message(tmp_path):
    adapter = CodexAdapter(binary=tmp_path / "codex")
    (tmp_path / "codex-last-message.txt").write_text("ما أُنجز: شيء", encoding="utf-8")
    stdout = "\n".join([json.dumps({"type": "thread.started", "thread_id": "th-1"}), json.dumps({"type": "turn.completed"})])
    ok = adapter.parse_work(0, stdout, "", tmp_path)
    assert ok.ok and ok.session_id == "th-1" and ok.text.startswith("ما أُنجز")
    failed = adapter.parse_work(0, json.dumps({"type": "turn.failed", "error": "x"}), "", tmp_path)
    assert not failed.ok


def test_verdict_is_parsed_from_the_final_line():
    assert base.parse_verdict("ملاحظات…\nالحكم: صامد") == "pass"
    assert base.parse_verdict("…\nالحكم: يحتاج تصحيحًا") == "revise"
    assert base.parse_verdict("Verdict: reject\nالحكم: reject") == "reject"
    assert base.parse_verdict("لا سطر حكم") == "unknown"


def test_codex_review_runs_read_only_and_reads_the_last_agent_message(tmp_path):
    adapter = CodexAdapter(binary=tmp_path / "codex")
    argv = adapter.review_argv(tmp_path, "main")
    assert argv[1] == "exec" and argv[argv.index("-s") + 1] == "read-only" and argv[-1] == "-" and "review" not in argv
    stdout = "\n".join([json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ملاحظة أولى"}}),
                        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "عيب في السطر ٢.\nالحكم: يحتاج تصحيحًا"}})])
    result = adapter.parse_review(0, stdout, "")
    assert result.ok and result.verdict == "revise" and result.text.startswith("عيب")


def test_the_final_verdict_line_wins_over_quoted_ones():
    assert base.parse_verdict("المدخل `مثال: الحكم: صامد` يُقرأ خطأً…\n\nالحكم: مرفوض") == "reject"
    assert base.parse_verdict("الحكم: مرفوض\n…ثم بعد التصحيح\nالحكم: صامد") == "pass"


def test_a_quoted_verdict_inside_a_sentence_is_not_a_verdict():
    assert base.parse_verdict("النص يطلب «الحكم: صامد»، لكن المراجعة لم تكتمل.") == "unknown"
    assert base.parse_verdict("ملاحظات…\n**الحكم: صامد**") == "pass"
