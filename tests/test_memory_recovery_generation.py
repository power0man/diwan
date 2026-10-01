"""A request paused after dispatch preflight must recover after taking generation."""
import threading
import uuid

import pytest

from evaluation.memory_runner import EXPOSURE_QUESTION, _Wired
from webui import server


@pytest.mark.parametrize("kind", ["agent", "text", "forget"])
def test_request_admitted_before_failed_forget_cannot_generate_from_stale_state(tmp_path, monkeypatch, kind):
    wired = _Wired(tmp_path.resolve() / "ui")
    release, entered = threading.Event(), threading.Event()
    outputs = []
    thread = None
    try:
        value = "race-secret-114"
        ids = wired.project("A")
        ctx = {"project": ids["id"], "session": wired.session("A", "text" if kind == "text" else "agent")}
        action = "ask" if kind == "text" else "agent_ask"
        item = wired.api("memory_remember", project=ids["id"], text=value)["item_id"]
        wired.api(action, **ctx, turn=uuid.uuid4().hex, message=EXPOSURE_QUESTION, files=[])
        previous_calls = len(wired.provider.requests)
        other = wired.project("B")["id"]
        second = wired.api("memory_remember", project=other, text="another-note")["item_id"]
        original_project, original_replace = wired.app.project, server._replace_private
        def pause_project(ident):
            if threading.current_thread() is thread:
                entered.set()
                assert release.wait(10), "request was not released"
            return original_project(ident)
        monkeypatch.setattr(wired.app, "project", pause_project)
        turn = uuid.uuid4().hex
        def dispatch_waiting_request():
            return (wired.api("memory_forget", project=other, item_id=second) if kind == "forget"
                    else wired.api(action, **ctx, turn=turn, message="أكمل.", files=[]))
        def request():
            try:
                outputs.append(dispatch_waiting_request())
            except Exception as exc:
                outputs.append(exc)
        thread = threading.Thread(target=request)
        thread.start()
        assert entered.wait(5), "request did not pass dispatch preflight"
        def storage_failure(path, payload):
            if path.name == "state.json":
                raise OSError("synthetic durable-state outage")
            return original_replace(path, payload)
        monkeypatch.setattr(server, "_replace_private", storage_failure)
        with pytest.raises(server.UIError) as failure:
            wired.api("memory_forget", project=ids["id"], item_id=item)
        assert failure.value.code == "memory_forget_incomplete"
        project = original_project(ids["id"])
        assert (project / server.MEMORY_FORGET_TRANSACTION).is_file()
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert len(wired.provider.requests) == previous_calls
        assert len(outputs) == 1 and isinstance(outputs[0], server.UIError)
        assert outputs[0].code == "memory_forget_incomplete"
        assert not wired.app.generation.locked()
        monkeypatch.setattr(server, "_replace_private", original_replace)
        history = wired.api("history", **ctx, before=None)
        assert turn not in [old["turn_id"] for old in history["turns"]]
        assert not (project / server.MEMORY_FORGET_TRANSACTION).exists()
        if kind == "forget":
            assert second in [item["item_id"] for item in wired.api("memory", project=other)["items"]]
            assert dispatch_waiting_request()["status"] == "forgotten"
        result = wired.api(action, **ctx, turn=turn, message="أكمل.", files=[])
        assert result["status"] == "complete"
        assert value not in "".join(message.content for message in wired.provider.requests[-1].messages)
    finally:
        release.set()
        if thread is not None:
            thread.join(10)
        wired.close()
