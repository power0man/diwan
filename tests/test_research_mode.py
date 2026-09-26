"""ك٥٣ (#49) الشطر الثاني: جلسةُ البحث المعمّق في الطريق الموصول، والإسنادُ يُفحص عند العرض.

- لا تُعرض الجلسة إلا حيث ضُبط محرّكُ بحث. وهي جلسةٌ وكيلة بتعليمات البحث المسجَّلة، وأداتُها الوحيدة web_search.
- الجوابُ المسنَد يُعرض بحكمه ومصادره. والمختلَق يُعرض بحكمه ورموزه، ولا يُعرض مصدرُه رابطًا (لا javascript:).
- الجلسةُ تُنسخ وتُستعاد، وتُعرض بعد الاستعادة بحكم إسنادها نفسِه.
- ولا يُسأل فيها بطريق النصّ.
"""
from __future__ import annotations

import json
import uuid

import pytest

from agent.research import RESEARCH_SYSTEM
from core.contracts import Response, ToolCall, Usage
from evaluation.research_bank import FixtureSearch, load
from webui.server import LocalApp, UIError
from workspace_tools.backup import export_workspace, restore_workspace

SUITE, CORPUS, META = load()
ITEM = SUITE["questions"][0]                        # two_hop: سلافة ← زبرجد ← 1412
REF = META["references"][ITEM["id"]]


def _says(content, *calls):
    return Response(content, Usage(1, 1), "complete", 0, provider="replay", model_version="v1",
                    tool_calls=tuple(calls))


class Replay:
    name, is_local = "replay", True

    def __init__(self, answer):
        self.answer = answer

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        done = sum(m.role == "tool" for m in request.messages if m.role == "tool")
        if done < len(REF["queries"]):
            return _says("", ToolCall(f"call-{done}", "web_search", {"query": REF["queries"][done]}))
        return _says(self.answer)


def _app(root, provider=None, search=True):
    app = LocalApp(root, model="replay", model_version="v" * 64, provider_factory=lambda: None,
                   agent_provider_factory=lambda: provider,
                   web_search=FixtureSearch(CORPUS) if search else None)
    app.api = lambda action, **values: app.dispatch({"action": action, **values})
    return app


@pytest.fixture
def base(tmp_path):
    root = tmp_path.resolve()
    root.chmod(0o700)
    return root


def _ask(app, project, session, message=ITEM["question"]):
    return app.api("agent_ask", project=project, session=session, turn=uuid.uuid4().hex, message=message, files=[])


def test_research_is_offered_only_where_a_search_engine_is_configured(base):
    plain = _app(base / "plain", search=False)
    assert plain.api("projects")["research_enabled"] is False
    project = plain.api("create_project", name="أ")["id"]
    with pytest.raises(UIError) as err:
        plain.api("create_session", project=project, name="بحث", mode="research")
    assert err.value.code == "research_unavailable"
    plain.close()
    assert _app(base / "wired").api("projects")["research_enabled"] is True


def test_a_research_session_is_an_agent_session_with_the_registered_instructions_and_search_alone(base):
    app = _app(base / "root", Replay(REF["answer"]))
    project = app.api("create_project", name="أ")["id"]
    session = app.api("create_session", project=project, name="بحث", mode="research")["id"]
    manifest = json.loads((base / "root/projects" / project / "agent-control" / session / "manifest.json").read_bytes())
    assert manifest["system"] == RESEARCH_SYSTEM
    assert [tool["name"] for tool in manifest["tools"]] == ["web_search"]
    app.close()


def test_a_cited_answer_is_presented_with_its_verdict_and_sources(base):
    app = _app(base / "root", Replay(REF["answer"]))
    project = app.api("create_project", name="أ")["id"]
    session = app.api("create_session", project=project, name="بحث", mode="research")["id"]
    result = _ask(app, project, session)
    assert result["status"] == "complete" and result["mode"] == "research"
    assert result["citations"]["passed"] and result["citations"]["codes"] == []
    assert set(result["citations"]["sources"].values()) == {u for f in ITEM["facts"] for u in f["sources"]}
    history = app.api("history", project=project, session=session, before=None)
    assert history["turns"][0]["citations"] == result["citations"]
    app.close()


def test_a_fabricated_source_is_named_and_never_offered_as_a_link(base):
    forged = "تأسّست زبرجد عام 1412 [1].\n\nالمصادر:\n[1] javascript:alert(1)"
    app = _app(base / "root", Replay(forged))
    project = app.api("create_project", name="أ")["id"]
    session = app.api("create_session", project=project, name="بحث", mode="research")["id"]
    result = _ask(app, project, session)
    assert not result["citations"]["passed"] and "source_not_returned" in result["citations"]["codes"]
    assert result["citations"]["sources"] == {}
    app.close()


def test_a_research_session_is_backed_up_restored_and_presented_the_same(base):
    app = _app(base / "root", Replay(REF["answer"]))
    project = app.api("create_project", name="أ")["id"]
    session = app.api("create_session", project=project, name="بحث", mode="research")["id"]
    before = _ask(app, project, session)["citations"]
    app.close()
    archive = base / "backup.json"
    sha = export_workspace(base / "root", archive)["sha256"]
    out = restore_workspace(archive, base / "restored", sha)
    assert out["agent_sessions"] == {"sessions": 1, "archived": []}
    restored = _app(base / "restored", Replay("لن يُنادى"))
    history = restored.api("history", project=project, session=session, before=None)
    assert history["turns"][0]["citations"] == before and history["turns"][0]["mode"] == "research"
    restored.close()


def test_a_research_session_is_not_asked_through_the_text_path(base):
    app = _app(base / "root", Replay(REF["answer"]))
    project = app.api("create_project", name="أ")["id"]
    session = app.api("create_session", project=project, name="بحث", mode="research")["id"]
    ask = {"message": "سؤال", "files": []}
    for action, extra in (("ask", ask), ("replay", {}), ("inspect", {})):
        with pytest.raises(UIError) as err:
            app.api(action, project=project, session=session, turn=uuid.uuid4().hex, **extra)
        assert err.value.code == "session_mode_mismatch", action
    app.close()


def test_an_agent_session_is_not_presented_as_research(base):
    app = _app(base / "root", Replay(REF["answer"]))
    project = app.api("create_project", name="أ")["id"]
    session = app.api("create_session", project=project, name="عمل", mode="agent")["id"]
    result = _ask(app, project, session)
    turn = app.api("history", project=project, session=session, before=None)["turns"][0]
    for shown in (result, turn):
        assert shown["mode"] == "agent" and "citations" not in shown
    app.close()


def test_a_research_session_is_refused_by_name_once_its_search_engine_is_gone(base):
    app = _app(base / "root", Replay(REF["answer"]))
    project = app.api("create_project", name="أ")["id"]
    session = app.api("create_session", project=project, name="بحث", mode="research")["id"]
    _ask(app, project, session)
    app.close()
    unwired = _app(base / "root", Replay(REF["answer"]), search=False)
    with pytest.raises(UIError) as err:
        _ask(unwired, project, session)
    assert err.value.code == "research_unavailable"
    assert len(unwired.api("history", project=project, session=session, before=None)["turns"]) == 1
    unwired.close()


class Endless(Replay):
    """يبحث ولا يجيب: جولةٌ لا تكتمل."""

    def complete(self, request):
        done = sum(m.role == "tool" for m in request.messages)
        return _says("", ToolCall(f"call-{done}", "web_search", {"query": f"{REF['queries'][0]} {done}"}))


def test_an_unfinished_research_turn_carries_no_citation_verdict(base):
    app = _app(base / "root", Endless(""))
    project = app.api("create_project", name="أ")["id"]
    session = app.api("create_session", project=project, name="بحث", mode="research")["id"]
    result = _ask(app, project, session)
    assert result["status"] == "step_limit" and result["mode"] == "research"
    assert "citations" not in result
    app.close()


def test_the_forget_receipt_names_a_research_turn_that_saw_the_item(base):
    app = _app(base / "root", Replay(REF["answer"]))
    project = app.api("create_project", name="أ")["id"]
    session = app.api("create_session", project=project, name="بحث", mode="research")["id"]
    item = app.api("memory_remember", project=project, text="المشروع عن مدن الجواهر")["item_id"]
    result = _ask(app, project, session)
    assert result.get("memory_items") == 1
    out = app.api("memory_forget", project=project, item_id=item)
    assert out["receipt"]["references"] == [f"agent:{session}/{result['turn_id']}"]
    app.close()
