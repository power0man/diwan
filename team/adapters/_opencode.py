"""Private-session OpenCode V2 transport; exported completion is evidence, not rc=0.

No credentials/configuration are changed. The fallback deliberately accepts only
one text-only turn; tools, errors, truncation and mismatched transcripts fail closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def completion_proof(export: dict, events: list[dict], *, prompt: str, model: str, agent: str, cwd: Path) -> dict | None:
    """Bind persisted completion to this fresh run and its actual emitted text."""
    info = export.get("info", {})
    messages = export.get("messages", [])
    if not isinstance(info, dict) or not isinstance(messages, list):
        return None
    sessions = {e.get("sessionID") for e in events}
    if len(sessions) != 1 or not info.get("id") or sessions != {info["id"]}:
        return None
    provider, _, model_id = model.partition("/")
    expected = {"providerID": provider, "id": model_id}
    identity = lambda value: isinstance(value, dict) and all(value.get(k) == v for k, v in expected.items())
    location = info.get("location", {})
    if info.get("outcome") != "succeeded" or info.get("agent") != agent or not identity(info.get("model")):
        return None
    if not isinstance(location, dict) or location.get("directory") != str(cwd.resolve()):
        return None
    if any(not isinstance(m, dict) or m.get("error") for m in messages):
        return None
    users = [m for m in messages if m.get("type") == "user"]
    assistants = [m for m in messages if m.get("type") == "assistant"]
    if len(users) != 1 or len(assistants) != 1 or users[0].get("text") != prompt or users[0].get("files"):
        return None
    answer = assistants[0]
    if answer.get("finish") != "stop" or answer.get("rawFinish") != "stop" or not identity(answer.get("model")) or answer.get("agent") != agent:
        return None
    timing = answer.get("time", {})
    if not isinstance(timing, dict) or not isinstance(timing.get("completed"), int) or timing["completed"] < timing.get("created", timing["completed"]):
        return None
    content = answer.get("content", [])
    if not isinstance(content, list) or any(not isinstance(p, dict) or p.get("type") not in ("text", "reasoning") for p in content):
        return None
    emitted = []
    for event in events:
        if event.get("type") not in ("step_start", "text", "reasoning", "step_finish"):
            return None
        part = event.get("part", {})
        if not isinstance(part, dict) or part.get("sessionID") != info["id"] or part.get("messageID") != answer.get("id"):
            return None
        if event.get("type") == "step_finish" and part.get("reason") != "stop":
            return None
        if event.get("type") == "text":
            if not isinstance(part.get("text"), str):
                return None
            emitted.append(part["text"])
    text = "".join(emitted)
    persisted = "".join(p.get("text", "") for p in content if p.get("type") == "text")
    if not text.strip() or text != persisted:
        return None
    return {"type": "diwan_session_complete", "sessionID": info["id"], "source": "opencode.session.export",
            "model": model, "messageID": answer["id"], "finish": "stop", "text_sha256": text_digest(text)}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--agent", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.agent):
        return 64
    prompt = sys.stdin.read()
    if not prompt.strip():
        return 64
    child = None

    def stop(signum, frame):
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with tempfile.TemporaryDirectory(prefix="diwan-opencode-") as directory:
        env = os.environ.copy()
        env.pop("PWD", None)
        env["OPENCODE_DB"] = str(Path(directory) / "session.sqlite")

        def run(command, input_text=""):
            nonlocal child
            child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, env=env)
            out, err = child.communicate(input_text)
            return child.returncode, out, err

        try:
            rc, out, err = run([args.binary, "run", "--standalone", "--format", "json", "--model", args.model, "--agent", args.agent], prompt)
            sys.stdout.write(out)
            sys.stderr.write(err)
            if rc != 0:
                return rc if rc > 0 else 128 - rc
            try:
                events = [json.loads(line) for line in out.splitlines() if line.strip()]
                if not events or any(not isinstance(e, dict) for e in events):
                    return 65
                # Even native stop is checked against the final persisted outcome.
                session = events[0].get("sessionID")
                if not isinstance(session, str) or not session.startswith("ses_"):
                    return 65
                rc, exported, errors = run([args.binary, "session", "export", "--standalone", session])
                if rc != 0:
                    sys.stderr.write(errors)
                    return 65
                data = json.loads(exported)
                proof = completion_proof(data, events, prompt=prompt, model=args.model, agent=args.agent, cwd=Path.cwd()) if isinstance(data, dict) else None
            except (ValueError, TypeError, KeyError):
                return 65
            if proof is None:
                sys.stderr.write("opencode_completion_unverified\n")
                return 65
            sys.stdout.write("\n" + json.dumps(proof, ensure_ascii=False) + "\n")
            return 0
        except FileNotFoundError:
            sys.stderr.write("binary_missing\n")
            return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
