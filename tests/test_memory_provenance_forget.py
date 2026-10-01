"""Forget derived model/tool context by recorded exposure, without a lexical guess."""
import json
import uuid
from unittest.mock import patch

from memory.store import MemoryStore

import pytest

from core.contracts import Response, ToolCall, Usage
from core.validate import validated
from evaluation.memory_runner import _Wired
from tests.test_memory_wired import _ask
from workspace_tools.backup import export_workspace, restore_workspace


class Derived:
    name, is_local = "synthetic-derived", True

    def __init__(self, tool=False):
        self.requests, self.tool = [], tool

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.requests.append(request)
        if self.tool and request.tools and request.messages[-1].role != "tool":
            call = ToolCall("derived-" + uuid.uuid4().hex, "list_files", {})
            return Response("", Usage(1, 1), "complete", 0, provider=self.name,
                            model_version="0" * 64, tool_calls=(call,))
        return Response("blue triangle" if any("Azure polygon record 75109" in m.content or "blue triangle" in m.content
                                              for m in request.messages) else "unrelated answer",
                        Usage(1, 1), "complete", 0, provider=self.name, model_version="0" * 64)


@pytest.fixture
def wired(tmp_path):
    running = _Wired(tmp_path.resolve() / "ui")
    running.provider = Derived()
    try:
        yield running
    finally:
        running.close()


def prepare(wired, kind, *, tool=False):
    ids = wired.project("provenance")
    before, _ = _ask(wired, "provenance", kind, "A harmless earlier owner input")
    item = wired.api("memory_remember", project=ids["id"], text="Azure polygon record 75109")["item_id"]
    wired.provider.tool = tool
    if tool:
        exposed = wired.api("agent_ask", project=ids["id"], session=wired.session("provenance", kind),
                            turn=uuid.uuid4().hex, message="Azure polygon record", files=[])
        assert exposed["status"] == "complete"
    else:
        exposed, _ = _ask(wired, "provenance", kind, "Azure polygon record")
    wired.provider.tool = False
    # Even without retrieval of the item, this actual request contains the earlier answer.
    with patch.object(MemoryStore, "context", return_value=("", [])):
        dependent, request = _ask(wired, "provenance", kind, "A distinct later owner input")
    assert "75109" not in request.messages[-1].content
    assert any("blue triangle" in m.content for m in request.messages)
    session = wired.app.agent_session(wired.app.project(ids["id"]), ids[kind]) if kind == "agent" else wired.app.session(wired.app.project(ids["id"]), ids[kind])
    return ids, item, before, exposed, dependent, session


@pytest.mark.parametrize("kind", ["agent", "text"])
def test_provenance_blocks_derived_context_and_keeps_unrelated_owner_input(wired, kind):
    ids, item, before, exposed, dependent, session = prepare(wired, kind)
    project = wired.app.project(ids["id"])
    ledger_files = sorted(project.rglob("calls.jsonl"))
    ledgers = {p: p.read_bytes() for p in ledger_files}
    receipt = wired.api("memory_forget", project=ids["id"], item_id=item)["receipt"]
    prefix = f"{kind}:{ids[kind]}/"
    assert receipt["context_withheld_turns"] == sorted(prefix + t["turn_id"] for t in (exposed, dependent))
    assert all(p.read_bytes() == contents for p, contents in ledgers.items())
    _, later = _ask(wired, "provenance", kind, "A fresh request after forget")
    validated(later)
    contents = [m.content for m in later.messages]
    assert not any("blue triangle" in c for c in contents)
    assert "unrelated answer" in contents
    assert all(any(value in c for c in contents) for value in
               ("A harmless earlier owner input", "A distinct later owner input"))
    # Reopening validates optional state metadata and never resurrects old output.
    wired.close()
    wired._open()
    _, reopened = _ask(wired, "provenance", kind, "Continue after reopening")
    assert all("blue triangle" not in m.content for m in reopened.messages)


def test_provenance_withholds_tool_pairs_without_touching_action_receipts(wired):
    ids, item, _, _, _, _ = prepare(wired, "agent", tool=True)
    root = wired.app.project(ids["id"])
    paths = [p for p in root.rglob("*") if p.is_file() and
             ("actions" in p.parts or p.name == "calls.jsonl")]
    assert paths
    audit = {p: p.read_bytes() for p in paths}
    wired.api("memory_forget", project=ids["id"], item_id=item)
    assert all(p.read_bytes() == raw for p, raw in audit.items())
    _, later = _ask(wired, "provenance", "agent", "Continue without old tools")
    validated(later)
    assert all(not m.tool_calls and m.tool_call_id is None for m in later.messages)
    assert all("blue triangle" not in m.content for m in later.messages)


@pytest.mark.parametrize("kind", ["agent", "text"])
def test_restore_uses_exposure_provenance_for_partial_and_paraphrased_echoes(wired, tmp_path, kind):
    ids, item, _, _, _, _ = prepare(wired, kind)
    backup = wired.backup()
    wired.api("memory_forget", project=ids["id"], item_id=item)
    wired.restore(backup)
    _, later = _ask(wired, "provenance", kind, "Continue restored history")
    assert all("blue triangle" not in m.content for m in later.messages)
    assert any("unrelated answer" in m.content for m in later.messages)


@pytest.mark.parametrize("kind", ["agent", "text"])
def test_interrupted_forget_recovers_provenance_before_another_request(wired, monkeypatch, kind):
    from pathlib import Path
    import webui.server as server
    ids, item, _, _, _, _ = prepare(wired, kind)
    original = server._replace_private

    def fail_state(path, payload):
        if Path(path).name == "state.json":
            raise OSError("synthetic state-write interruption")
        return original(path, payload)

    monkeypatch.setattr(server, "_replace_private", fail_state)
    with pytest.raises(server.UIError) as failure:
        wired.api("memory_forget", project=ids["id"], item_id=item)
    assert failure.value.code == "memory_forget_incomplete"
    monkeypatch.setattr(server, "_replace_private", original)
    wired.close()
    wired._open()
    _, later = _ask(wired, "provenance", kind, "Continue after recovery")
    assert all("blue triangle" not in m.content for m in later.messages)
    assert wired.api("memory", project=ids["id"])["items"] == []


@pytest.mark.parametrize("kind", ["agent", "text"])
def test_previous_forget_does_not_taint_new_unexposed_turns(wired, kind):
    ids, item, _, _, _, _ = prepare(wired, kind)
    wired.api("memory_forget", project=ids["id"], item_id=item)
    new_item = wired.api("memory_remember", project=ids["id"], text="Azure polygon record 75109")["item_id"]
    with patch.object(MemoryStore, "context", return_value=("", [])):
        fresh, _ = _ask(wired, "provenance", kind, "Unexposed new question")
    assert fresh["content"] == "unrelated answer"
    receipt = wired.api("memory_forget", project=ids["id"], item_id=new_item)["receipt"]
    assert receipt["context_withheld_turns"] == []
    _, later = _ask(wired, "provenance", kind, "Keep the unexposed response")
    assert later.messages[-2].content == "unrelated answer"
