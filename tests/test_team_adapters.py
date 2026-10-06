"""عقدُ محوِّل الوكيل: لا أعلامَ تجاوز، وبيئةٌ بقائمة سماح، وقراءةُ مخرجات Claude وCodex المنظَّمة، ورموزُ التعذّر."""
from __future__ import annotations

import errno
import os
import time

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
    assert base.parse_verdict("لم تكتمل المراجعة، وهذا مجرّد مثالٍ منقول: الحكم: صامد") == "unknown"
    assert base.parse_verdict("ملاحظات…\n**الحكم: صامد**") == "pass"


def test_the_wrapper_records_the_child_pid_and_exit_code_without_the_dispatcher(tmp_path):
    adapter = ClaudeAdapter(binary=tmp_path / "claude")
    exit_path = tmp_path / "raw" / "exit"
    proc = adapter.start(["/bin/sh", "-c", "echo $$ > \"$0\"; exit 3", str(tmp_path / "raw" / "self_pid")], "", tmp_path,
                         tmp_path / "raw" / "out", tmp_path / "raw" / "err", exit_path=exit_path)
    assert proc.wait(timeout=60) == 3 and exit_path.read_text() == "3"
    child = (tmp_path / "raw" / "child_pid").read_text().strip()
    assert child == (tmp_path / "raw" / "self_pid").read_text().strip() and child != str(proc.pid)


def test_the_verdict_must_be_the_final_unquoted_line():
    assert base.parse_verdict("الحكم: صامد\nلم أتمكن من إكمال المراجعة") == "unknown"
    assert base.parse_verdict("> الحكم: صامد") == "unknown"


def test_a_claude_review_with_a_login_error_is_unavailability(tmp_path):
    adapter = ClaudeAdapter(binary=tmp_path / "claude")
    result = adapter.parse_review(0, json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}), "")
    assert not result.ok and result.unavailable == "auth_required"


def test_a_login_error_with_a_zero_exit_is_unavailability_not_failure(tmp_path):
    adapter = ClaudeAdapter(binary=tmp_path / "claude")
    result = adapter.parse_work(0, json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}), "", tmp_path)
    assert not result.ok and result.unavailable == "auth_required"


def _reap(pid: int) -> int:
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def test_the_wrapper_holds_the_agent_until_its_pid_is_saved(tmp_path):
    """الابنُ لا ينفّذ شيئًا قبل الإذن؛ وإغلاقُ الأنبوب بلا إذن (موتُ الغلاف قبل كتابة المعرّف) يُخرجه بلا تنفيذ؛ وفشلُ exec مسمًّى."""
    from team.adapters import _wrap

    marker = tmp_path / "ran"
    command = ["/bin/sh", "-c", f"echo ran > '{marker}'"]
    pid, go_w, err_r = _wrap.fork_agent(command)
    time.sleep(0.3)
    assert not marker.exists(), "الوكيلُ نُفّذ قبل الإذن"
    os.close(go_w)                                                      # موتُ الغلاف قبل الإذن
    assert _reap(pid) == _wrap.NOT_RELEASED and not marker.exists()
    os.close(err_r)
    pid, go_w, err_r = _wrap.fork_agent(command)
    _wrap.release(go_w)
    assert os.read(err_r, 64) == b""                                    # exec نجح
    os.close(err_r)
    assert _reap(pid) == 0 and marker.read_text().strip() == "ran"
    pid, go_w, err_r = _wrap.fork_agent([str(tmp_path / "nonexistent-agent")])
    _wrap.release(go_w)
    assert os.read(err_r, 64) == str(errno.ENOENT).encode("ascii")
    os.close(err_r)
    assert _reap(pid) == _wrap.LAUNCH_FAILED


def test_the_wrapper_marks_a_failed_exec_as_launch_failed(tmp_path):
    adapter = ClaudeAdapter(binary=tmp_path / "claude")
    raw = tmp_path / "raw"
    proc = adapter.start([str(tmp_path / "nonexistent-agent")], "", tmp_path, raw / "out", raw / "err", exit_path=raw / "exit")
    assert proc.wait(timeout=60) == 127 and (raw / "exit").read_text() == "127"
    assert (raw / "launch_failed").read_text().strip() == str(errno.ENOENT) and (raw / "child_pid").exists()


def test_the_wrapper_records_its_own_pid_first_and_refuses_to_release_after_a_takeover(tmp_path):
    """غلافٌ تأخّر حتى استُحوذ على المحاولة لا يطلق وكيلًا: علامةُ `taken_over` قبل الإذن تُخرج الابنَ بلا تنفيذ (125)؛
    ومعرّفُ الغلاف نفسِه يُكتب أوّلًا فيُفحص حيًّا عند الاستحواذ (ملاحظة Codex على #347)."""
    adapter = ClaudeAdapter(binary=tmp_path / "claude")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "taken_over").write_text("2026-10-07T00:00:00+00:00", encoding="utf-8")
    marker = tmp_path / "ran"
    proc = adapter.start(["/bin/sh", "-c", f"echo ran > '{marker}'"], "", tmp_path, raw / "out", raw / "err", exit_path=raw / "exit")
    assert proc.wait(timeout=60) == 125 and (raw / "exit").read_text() == "125" and not marker.exists()
    assert (raw / "wrapper_pid").read_text().strip() == str(proc.pid) and (raw / "child_pid").exists()
