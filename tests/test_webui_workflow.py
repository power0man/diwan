import threading
import uuid

import pytest

from acceptance_m9 import (CapturingProvider, SyntheticProvider, SYNTHETIC_MODEL,
                           SYNTHETIC_VERSION, _answer, _snapshot)
from webui.server import LocalApp, UIError, decode


@pytest.fixture
def app(tmp_path):
    provider = CapturingProvider(SyntheticProvider([_answer("جواب محفوظ") for _ in range(50)]))
    app = LocalApp(tmp_path.resolve() / "ui", model=SYNTHETIC_MODEL,
                   model_version=SYNTHETIC_VERSION, provider_factory=lambda: provider)
    app.test_provider = provider
    yield app
    app.close()


def call(app, action, **kwargs):
    return app.dispatch({"action": action, **kwargs})


def context(app, name="مشروع"):
    project = call(app, "create_project", name=name)["id"]
    session = call(app, "create_session", project=project, name="محادثة")["id"]
    return {"project": project, "session": session}


def ask(app, ctx, **kwargs):
    return call(app, "ask", **ctx, turn=uuid.uuid4().hex, message="أجب بإيجاز", files=[], **kwargs)


def test_project_and_session_isolation(app):
    one, two = context(app), context(app)
    answer = ask(app, one)
    assert call(app, "history", **two, before=None)["turns"] == []
    with pytest.raises(Exception):
        call(app, "history", project=two["project"], session=one["session"], before=None)
    assert len(app.test_provider.requests) == 1
    assert call(app, "history", **one, before=None)["turns"][0]["content"] == answer["content"]


def test_frozen_file_preferences_replay_and_explicit_write(app):
    ctx = context(app)
    pref = call(app, "set_preference", project=ctx["project"], key="verbosity", value="concise", revision=0)
    upload = call(app, "upload", project=ctx["project"], upload=uuid.uuid4().hex,
                  name="نص.txt", content="مرفق عربي\u200f\u200d <script>alert(1)</script>")
    turn = uuid.uuid4().hex
    request = {**ctx, "turn": turn, "message": "اقرأ", "files": [upload["path"]]}
    result = call(app, "ask", **request)
    project = app.project(ctx["project"])
    (project / "uploads" / upload["path"]).unlink()
    call(app, "set_preference", project=ctx["project"], key="verbosity", value="detailed", revision=1)
    app.provider_factory = lambda: pytest.fail("Replay must not construct provider")
    before = _snapshot(app.root)
    assert call(app, "ask", **request)["replayed"]
    assert call(app, "replay", **ctx, turn=turn)["content"] == result["content"]
    evidence = call(app, "inspect", **ctx, turn=turn)
    assert evidence["preferences"] == pref
    assert "<script>" in evidence["attachments"][0]["content"]
    assert _snapshot(app.root) == before
    proposal = call(app, "propose", **ctx, turn=turn, name="جواب.txt", request=uuid.uuid4().hex)
    assert not (project / "outputs" / "جواب.txt").exists()
    applied = call(app, "apply", project=ctx["project"], proposal=proposal["proposal_id"], sha256=proposal["sha256"])
    assert applied["status"] == "applied"
    assert (project / "outputs" / "جواب.txt").read_text() == result["content"]
    before = _snapshot(app.root)
    assert call(app, "apply", project=ctx["project"], proposal=proposal["proposal_id"], sha256=proposal["sha256"])["replayed"]
    assert _snapshot(app.root) == before


def test_old_model_history_and_replay_without_provider(app):
    ctx = context(app)
    result = ask(app, ctx)
    app.model_version = "b" * 64
    app.provider_factory = lambda: pytest.fail("Must not call a changed provider")
    assert call(app, "history", **ctx, before=None)["turns"][0]["content"] == result["content"]
    assert call(app, "replay", **ctx, turn=result["turn_id"])["replayed"]
    with pytest.raises(UIError, match="model_changed_new_session"):
        ask(app, ctx)


def test_concurrent_generation_duplicate_and_history(app):
    ctx, other = context(app), context(app)
    entered, release = threading.Event(), threading.Event()
    provider = app.test_provider
    complete = provider.complete
    def held(request):
        entered.set()
        assert release.wait(5)
        return complete(request)
    provider.complete = held
    request = {**ctx, "turn": uuid.uuid4().hex, "message": "أجب", "files": []}
    results = []
    thread = threading.Thread(target=lambda: results.append(call(app, "ask", **request)))
    thread.start()
    try:
        assert entered.wait(5)
        assert call(app, "ask", **request)["status"] == "running"
        assert call(app, "history", **ctx, before=None)["status"] == "running"
        assert call(app, "history", **other, before=None)["status"] == "idle"
        with pytest.raises(UIError, match="turn_conflict"):
            call(app, "ask", **{**request, "message": "طلب آخر"})
        with pytest.raises(UIError, match="generation_busy"):
            ask(app, other)
    finally:
        release.set()
        thread.join(5)
    assert len(results) == len(provider.requests) == 1


@pytest.mark.parametrize("raw", [b'{}', b'{"a":1,"a":2}', b'[]', b'null', b'{"a":NaN}', b'\xff'])
def test_malformed_json_or_request_refused(app, raw):
    with pytest.raises(Exception):
        app.dispatch(decode(raw))
    assert not app.test_provider.requests


@pytest.mark.parametrize("bad", ["../secret", "/etc/passwd", "a/b", "\\evil", "", "a" * 500, ".git", "a\u202eb"])
def test_upload_path_cannot_escape(app, bad):
    ctx = context(app)
    with pytest.raises(Exception):
        call(app, "upload", project=ctx["project"], upload=uuid.uuid4().hex, name=bad, content="text")
    assert not app.test_provider.requests


def test_pagination_preserves_order(app):
    ctx = context(app)
    results = [ask(app, ctx) for _ in range(31)]
    page = call(app, "history", **ctx, before=None)
    assert len(page["turns"]) == 30 and page["before"] == 1 and page["total"] == 31
    first = call(app, "history", **ctx, before=page["before"])
    assert [t["turn_id"] for t in first["turns"] + page["turns"]] == [r["turn_id"] for r in results]
    assert all("text" not in t for t in page["turns"])


def test_root_replacement_refused(app):
    moved = app.root.with_name("moved")
    app.root.rename(moved)
    app.root.mkdir(mode=0o700)
    with pytest.raises(UIError, match="store_changed"):
        call(app, "projects")
