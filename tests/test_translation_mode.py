"""غ٤ الشطر الثاني: جلسةُ الترجمة في الطريق الموصول، والفحصُ الآليّ يُعرض مع كل ترجمة.

- جلسةُ الترجمة وكيلةٌ بتعليمات الترجمة المسجَّلة، وأداتُها check_translation وحدها.
- المسردُ اختياريّ: ملفُّ glossary.csv المرفوع يبلغ النموذجَ في رسالة الجولة، ويُفحص به الجواب.
  وبلا ملفٍّ لا مسرد.
- الحكمُ يُعرض بالنصّ والمسرد اللذين رآهما النموذج، ويبقى نفسَه في التاريخ وبعد الاستعادة.
- ولا تُسأل بطريق النصّ.
"""
from __future__ import annotations

import json
import uuid

import pytest

from agent.translation import TRANSLATE_SYSTEM, split_request
from core.contracts import Response, ToolCall, Usage
from evaluation.translation_bank import load
from services.agent_workspace import decode_input
from webui.server import LocalApp, UIError
from workspace_tools.backup import export_workspace, restore_workspace

SUITE, META = load()
ITEMS = {item["id"]: item for item in SUITE["items"]}
GLOSSARY = "المصدر,الهدف\n" + "".join(f"{s},{t}\n" for s, t in ITEMS["tr13"]["glossary"])


def _says(content, *calls):
    return Response(content, Usage(1, 1), "complete", 0, provider="replay", model_version="v1", tool_calls=tuple(calls))


class Translator:
    """يفحص ترجمتَه بالأداة ثم يجيب بها، ويحفظ ما رآه."""
    name, is_local = "replay", True

    def __init__(self, answer):
        self.answer, self.seen = answer, []

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        user = [m.content for m in request.messages if m.role == "user"]
        self.seen.append((decode_input(user[0])["user_request"], {m.content for m in request.messages if m.role == "system"}))
        if not any(m.role == "tool" for m in request.messages):
            return _says("", ToolCall("chk", "check_translation", {"source": "x", "translation": self.answer}))
        return _says(self.answer)


def _app(root, provider=None, *, agent=True):
    app = LocalApp(root, model="replay", model_version="v" * 64, provider_factory=lambda: None,
                   **({"agent_provider_factory": lambda: provider} if agent else {}))
    app.api = lambda action, **values: app.dispatch({"action": action, **values})
    return app


@pytest.fixture
def base(tmp_path):
    root = tmp_path.resolve()
    root.chmod(0o700)
    return root


def _session(app):
    project = app.api("create_project", name="أ")["id"]
    return project, app.api("create_session", project=project, name="ترجمة", mode="translate")["id"]


def _ask(app, project, session, message, turn=None):
    return app.api("agent_ask", project=project, session=session, turn=turn or uuid.uuid4().hex,
                   message=message, files=[])


def test_a_translation_session_has_its_instructions_and_the_checker_alone(base):
    app = _app(base / "root", Translator(""))
    project, session = _session(app)
    manifest = json.loads((app.root / "projects" / project / "agent-control" / session / "manifest.json").read_bytes())
    assert manifest["system"] == TRANSLATE_SYSTEM
    assert [tool["name"] for tool in manifest["tools"]] == ["check_translation"]
    app.close()
    plain = _app(base / "plain", agent=False)
    project = plain.api("create_project", name="أ")["id"]
    with pytest.raises(UIError) as err:
        plain.api("create_session", project=project, name="ب", mode="translate")
    assert err.value.code == "translate_unavailable"
    plain.close()


def test_a_faithful_translation_is_shown_with_its_passing_check(base):
    item = ITEMS["tr23"]
    provider = Translator(META["items"]["tr23"]["reference"])
    app = _app(base / "root", provider)
    project, session = _session(app)
    result = _ask(app, project, session, item["source"])
    assert result["status"] == "complete" and result["mode"] == "translate"
    assert result["translation"]["passed"] and result["translation"]["target"] == "en"
    assert result["translation"]["glossary_terms"] == 0
    assert provider.seen[0][1] == {TRANSLATE_SYSTEM}
    history = app.api("history", project=project, session=session, before=None)
    assert history["turns"][0]["translation"] == result["translation"]
    app.close()


def test_a_changed_number_is_named_in_the_check(base):
    app = _app(base / "root", Translator(META["items"]["tr23"]["decoy"]))
    project, session = _session(app)
    result = _ask(app, project, session, ITEMS["tr23"]["source"])
    assert not result["translation"]["passed"] and "number_missing" in result["translation"]["codes"]
    app.close()


def test_an_uploaded_glossary_reaches_the_model_and_judges_the_answer(base):
    item = ITEMS["tr13"]
    provider = Translator(META["items"]["tr13"]["decoy"])
    app = _app(base / "root", provider)
    project, session = _session(app)
    app.api("upload", project=project, upload=uuid.uuid4().hex, name="glossary.csv", content=GLOSSARY)
    turn = uuid.uuid4().hex
    result = _ask(app, project, session, item["source"], turn)
    source, glossary = split_request(provider.seen[0][0])
    assert source == item["source"] and glossary == [tuple(p) for p in item["glossary"]]
    assert result["translation"]["glossary_terms"] == 2 and result["translation"]["codes"] == ["term_missing"]
    assert result["user_request"] == provider.seen[0][0]            # ما رآه النموذج هو ما حُفظ وفُحص به
    replay = _ask(app, project, session, item["source"], turn)
    assert replay["replayed"] is True and replay["translation"] == result["translation"]
    app.close()


def test_the_reference_passes_with_the_same_glossary(base):
    app = _app(base / "root", Translator(META["items"]["tr13"]["reference"]))
    project, session = _session(app)
    app.api("upload", project=project, upload=uuid.uuid4().hex, name="glossary.csv", content=GLOSSARY)
    assert _ask(app, project, session, ITEMS["tr13"]["source"])["translation"]["passed"]
    app.close()


def test_an_oversized_glossary_is_refused_by_name(base):
    app = _app(base / "root", Translator("x"))
    project, session = _session(app)
    rows = "".join(f"term{i},مصطلح{i}\n" for i in range(201))
    app.api("upload", project=project, upload=uuid.uuid4().hex, name="glossary.csv", content=rows)
    with pytest.raises(UIError) as err:
        _ask(app, project, session, "Save changes")
    assert err.value.code == "glossary_too_large"
    app.close()


def test_a_translation_session_is_backed_up_and_shown_the_same_after_restore(base):
    app = _app(base / "root", Translator(META["items"]["tr23"]["reference"]))
    project, session = _session(app)
    before = _ask(app, project, session, ITEMS["tr23"]["source"])["translation"]
    app.close()
    archive = base / "backup.json"
    sha = export_workspace(base / "root", archive)["sha256"]
    assert restore_workspace(archive, base / "restored", sha)["agent_sessions"] == {"sessions": 1, "archived": []}
    restored = _app(base / "restored", Translator("لن يُنادى"))
    turn = restored.api("history", project=project, session=session, before=None)["turns"][0]
    assert turn["translation"] == before and turn["mode"] == "translate"
    restored.close()


def test_a_translation_session_is_not_asked_through_the_text_path(base):
    app = _app(base / "root", Translator("x"))
    project, session = _session(app)
    with pytest.raises(UIError) as err:
        app.api("ask", project=project, session=session, turn=uuid.uuid4().hex, message="نص", files=[])
    assert err.value.code == "session_mode_mismatch"
    app.close()


def test_the_latest_uploaded_glossary_is_the_one_used(base):
    provider = Translator(META["items"]["tr13"]["reference"])
    app = _app(base / "root", provider)
    project, session = _session(app)
    app.api("upload", project=project, upload=uuid.uuid4().hex, name="glossary.csv", content="old,قديم\n")
    app.api("upload", project=project, upload=uuid.uuid4().hex, name="glossary.csv", content=GLOSSARY)
    _ask(app, project, session, ITEMS["tr13"]["source"])
    assert split_request(provider.seen[0][0])[1] == [tuple(p) for p in ITEMS["tr13"]["glossary"]]
    app.close()
