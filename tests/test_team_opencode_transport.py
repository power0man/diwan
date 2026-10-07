"""Completion evidence and subprocess contract for the OpenCode V2 bridge."""
import copy
import json
from pathlib import Path
import subprocess
import sys

from team.adapters._opencode import completion_proof, text_digest
from team.adapters.opencode import OpenCodeAdapter


def packet(tmp_path):
    model = {"providerID": "local-safe", "id": "qwen3.5:9b"}
    event = {"type": "text", "sessionID": "ses_test", "part": {"sessionID": "ses_test", "messageID": "m", "text": "الحكم: صامد"}}
    export = {"info": {"id": "ses_test", "agent": "plan", "model": model, "outcome": "succeeded", "location": {"directory": str(tmp_path.resolve())}},
              "messages": [{"type": "user", "text": "test", "files": []},
                           {"type": "assistant", "id": "m", "agent": "plan", "model": model, "finish": "stop", "rawFinish": "stop",
                            "time": {"created": 1, "completed": 2}, "content": [{"type": "text", "text": "الحكم: صامد"}]}]}
    return export, [event]


def check(data, events, tmp_path):
    return completion_proof(data, events, prompt="test", model="local-safe/qwen3.5:9b", agent="plan", cwd=tmp_path)


def test_export_completion_is_bound_to_the_current_request_and_identity(tmp_path):
    data, events = packet(tmp_path)
    proof = check(data, events, tmp_path)
    assert proof and proof["text_sha256"] == text_digest("الحكم: صامد")
    # A successful previous session or another provider is not this request.
    for key, value in (("id", "ses_old"), ("outcome", "interrupted"), ("agent", "build"),
                       ("model", {"providerID": "other", "id": "qwen3.5:9b"}), ("location", {"directory": "/elsewhere"})):
        bad = copy.deepcopy(data); bad["info"][key] = value
        assert check(bad, events, tmp_path) is None
    bad = copy.deepcopy(events); bad[0]["sessionID"] = "ses_other"
    assert check(data, bad, tmp_path) is None
    for value in ("old prompt", "test\nchanged"):
        bad = copy.deepcopy(data); bad["messages"][0]["text"] = value
        assert check(bad, events, tmp_path) is None
    for extra in (data["messages"][0], data["messages"][1], {"type": "idle", "error": "failed"}):
        bad = copy.deepcopy(data); bad["messages"].append(extra)
        assert check(bad, events, tmp_path) is None
    bad = copy.deepcopy(data); bad["messages"][0]["files"] = [{"url": "private"}]
    assert check(bad, events, tmp_path) is None


def test_export_completion_rejects_truncation_tools_and_stream_mismatch(tmp_path):
    data, events = packet(tmp_path)
    for key, value in (("finish", "length"), ("rawFinish", "length"), ("agent", "build"), ("model", {}),
                       ("time", {"created": 3, "completed": 2}), ("time", {}), ("content", [{"type": "tool"}]),
                       ("content", [{"type": "text", "text": "الحكم: صامد"}, {"type": "tool"}]),
                       ("content", [{"type": "text", "text": "partial"}])):
        bad = copy.deepcopy(data); bad["messages"][1][key] = value
        assert check(bad, events, tmp_path) is None
    for key, value in (("messageID", "old"), ("sessionID", "ses_old"), ("text", "partial")):
        bad = copy.deepcopy(events); bad[0]["part"][key] = value
        assert check(data, bad, tmp_path) is None
    for event in ({"type": "error", "sessionID": "ses_test"}, {"type": "tool_use", "sessionID": "ses_test"},
                  {"type": "step_finish", "sessionID": "ses_test", "part": {"sessionID": "ses_test", "messageID": "m", "reason": "length"}}):
        assert check(data, events + [event], tmp_path) is None


def test_adapter_requires_valid_terminal_proof_and_sticky_failure(tmp_path):
    data, events = packet(tmp_path); proof = check(data, events, tmp_path)
    adapter = OpenCodeAdapter(model="local-safe/qwen3.5:9b")
    encode = lambda rows: "\n".join(map(json.dumps, rows))
    assert adapter.parse_review(0, encode(events + [proof]), "").ok
    assert adapter.parse_review(0, encode(events + [proof]), "").verdict == "pass"
    assert not adapter.parse_work(0, encode(events), "", tmp_path).ok
    for key, value in (("source", "other"), ("model", "other/qwen3.5:9b"), ("sessionID", "ses_old"),
                       ("finish", "length"), ("text_sha256", "bad")):
        bad = dict(proof); bad[key] = value
        assert not adapter.parse_work(0, encode(events + [bad]), "", tmp_path).ok
    for rows in (events + [proof, events[0]], events + [{"type": "error"}, proof],
                 events + [dict(events[0], sessionID="ses_old"), proof],
                 events + [{"type": "step_finish", "part": {"reason": "length"}}, proof]):
        assert not adapter.parse_work(0, encode(rows), "", tmp_path).ok
    assert not adapter.parse_work(1, encode(events + [proof]), "", tmp_path).ok


def test_bridge_uses_private_database_and_checks_native_export(tmp_path):
    data, events = packet(tmp_path)
    binary = tmp_path / "fake-opencode"
    binary.write_text("#!" + sys.executable + "\n" +
        "import json,os,sys\nfrom pathlib import Path\n" +
        "assert 'PWD' not in os.environ\n" +
        "assert Path(os.environ['OPENCODE_DB']).name == 'session.sqlite'\n" +
        "assert '--standalone' in sys.argv and '--auto' not in sys.argv\n" +
        "if sys.argv[1]=='run':\n assert sys.stdin.read()=='test'\n assert sys.argv[sys.argv.index('--model')+1]=='local-safe/qwen3.5:9b'\n print(" + repr(json.dumps(events[0])) + ")\n" +
        "else:\n assert sys.argv[1:3]==['session','export'] and sys.argv[-1]=='ses_test'\n print(" + repr(json.dumps(data)) + ")\n")
    binary.chmod(0o755)
    adapter = OpenCodeAdapter(binary=binary, model="local-safe/qwen3.5:9b")
    rc, out, err = adapter.run_review(adapter.review_argv(tmp_path, "main"), "test", tmp_path, 10)
    assert rc == 0 and adapter.parse_review(rc, out, err).ok
    # Export cannot turn rc=0 plus a truncated response into acceptance.
    binary.write_text(binary.read_text().replace('\"rawFinish\": \"stop\"', '\"rawFinish\": \"length\"'))
    rc, out, err = adapter.run_review(adapter.review_argv(tmp_path, "main"), "test", tmp_path, 10)
    assert rc == 65 and not adapter.parse_review(rc, out, err).ok


def test_review_timeout_stops_the_private_cli_child(tmp_path):
    import os
    import signal
    import time
    binary = tmp_path / "sleeping-opencode"
    pid_path = tmp_path / "native.pid"
    grand_path = tmp_path / "server.pid"
    binary.write_text("#!" + sys.executable + "\nimport os,time,signal\nfrom pathlib import Path\n" +
                      "Path(" + repr(str(pid_path)) + ").write_text(str(os.getpid()))\n" +
                      "if os.fork()==0:\n signal.signal(signal.SIGTERM,signal.SIG_IGN)\n os.close(1);os.close(2)\n Path(" + repr(str(grand_path)) + ").write_text(str(os.getpid()))\n time.sleep(30)\n os._exit(0)\n" +
                      "time.sleep(30)\n")
    binary.chmod(0o755)
    adapter = OpenCodeAdapter(binary=binary, model="local-safe/qwen3.5:9b")
    rc, out, err = adapter.run_review(adapter.review_argv(tmp_path, "main"), "test", tmp_path, 1)
    pids = [int(p.read_text()) for p in (pid_path, grand_path)]
    try:
        assert (rc, out, err) == (124, "", "timeout")
        for pid in pids:
            for _ in range(30):
                state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
                if not state or state.startswith("Z"):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("private process survived timeout")
    finally:
        # Mutation runs must also clean up their deliberately surviving fake server.
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_verified_text_parts_do_not_invent_or_break_verdict_lines(tmp_path):
    data, events = packet(tmp_path)
    adapter = OpenCodeAdapter(model="local-safe/qwen3.5:9b")
    for parts, expected in ((["ملاحظة ", "الحكم: صامد"], "unknown"), (["الحكم:", " صامد"], "pass")):
        data["messages"][1]["content"] = [{"type": "text", "text": text} for text in parts]
        stream = [dict(events[0], part=dict(events[0]["part"], text=text)) for text in parts]
        proof = check(data, stream, tmp_path)
        assert proof is not None
        result = adapter.parse_review(0, "\n".join(map(json.dumps, stream + [proof])), "")
        assert result.ok and result.text == "".join(parts) and result.verdict == expected


def test_named_text_agent_is_explicit_and_cannot_inject_flags(tmp_path):
    import pytest
    from team.adapters.opencode import ModelUnconfigured
    adapter = OpenCodeAdapter(model="local-safe/qwen3.5:9b", agent="diwan-text")
    argv = adapter.review_argv(tmp_path, "main")
    assert argv[-2:] == ["--agent", "diwan-text"]
    assert adapter.spec.capabilities["coding"] is False and adapter.spec.trust["reviewer_eligible"] is False
    for agent in ("--auto", "../agent", "agent name", "x" * 65):
        with pytest.raises(ModelUnconfigured):
            OpenCodeAdapter(model="local-safe/qwen3.5:9b", agent=agent).review_argv(tmp_path, "main")
