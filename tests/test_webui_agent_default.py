"""J1 coauthored contracts: real loopback HTTP, synthetic providers and temp files."""
from contextlib import contextmanager
import json
import threading
import uuid

import pytest

from agent.registry import Tool
from core.contracts import ToolCall, ToolSpec
from tests.test_agent_webui import Provider, response
from tests.test_webui_http import Running
import webui.server as ui


class RoutedRunning(Running):
    """Separate providers make an accidental text/agent/media fallback observable."""

    def __init__(self, root, *, agent_enabled=True, web_search=None):
        self.providers = {mode: Provider() for mode in ("text", "agent", "media")}
        self.factories = {mode: 0 for mode in self.providers}

        def provider(mode):
            self.factories[mode] += 1
            return self.providers[mode]

        self.app = ui.LocalApp(root, model="fixture", model_version="a" * 64,
            provider_factory=lambda: provider("text"),
            agent_provider_factory=(lambda: provider("agent")) if agent_enabled else None,
            media_model="fixture", media_model_version="a" * 64,
            media_provider_factory=lambda: provider("media"), web_search=web_search)
        try:
            self.server = ui.Server(self.app, 0)
        except BaseException:
            self.app.close()
            raise
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.app.close()
        assert not self.thread.is_alive()


@contextmanager
def serving(root, **kwargs):
    running = RoutedRunning(root, **kwargs)
    try:
        yield running
    finally:
        running.close()


def new_session(running, **options):
    project = running.api("create_project", name="مشروع افتراضي")['id']
    session = running.api("create_session", project=project, name="جلسة", **options)
    return {"project": project, "session": session['id']}, session


def ask_payload(context, action="ask", **options):
    return {"action": action, **context, "turn": uuid.uuid4().hex,
            "message": "طلب مصطنع", "files": [], **options}


@pytest.mark.parametrize("enabled,expected", [(True, "agent"), (False, "text")])
def test_bootstrap_and_omitted_mode_agree_with_available_provider(tmp_path, enabled, expected):
    with serving(tmp_path.resolve() / "ui", agent_enabled=enabled) as running:
        bootstrap = running.api("projects")
        assert bootstrap['agent_enabled'] is enabled
        assert bootstrap['default_session_mode'] == expected
        context, created = new_session(running)
        assert created['mode'] == expected
        assert running.api("sessions", project=context['project'])['sessions'] == [created]
        assert running.api("history", **context, before=None)['turns'] == []
        assert running.factories == {"text": 0, "agent": 0, "media": 0}


def test_default_agent_routes_tools_and_persists_the_actual_file_receipt(tmp_path):
    with serving(tmp_path.resolve() / "ui") as running:
        context, _ = new_session(running)
        running.providers['agent'].responses = [
            response("أكتب", ToolCall("write-default", "write_file", {
                "path": "result.txt", "content": "أثر الجلسة الافتراضية"})),
            response("تمت الكتابة")]
        status, refused, _ = running.request(ask_payload(context))
        assert status == 409 and refused['error_code'] == 'session_mode_mismatch'
        assert running.factories == {"text": 0, "agent": 0, "media": 0}
        payload = ask_payload(context, "agent_ask")
        status, result, _ = running.request(payload)
        assert status == 200 and result['status'] == 'complete' and result['mode'] == 'agent'
        action = result['steps'][0]['tool_results'][0]
        assert action['status'] == 'ok' and action['journal_action_id'].startswith('act-')
        target = running.app.project(context['project']) / 'agent-workspace/result.txt'
        assert target.read_text() == "أثر الجلسة الافتراضية"
        assert len(running.providers['agent'].requests) == 2
        assert not running.providers['text'].requests and not running.providers['media'].requests
        replay = running.request(payload)[1]
        assert replay['replayed'] is True and replay['steps'] == result['steps']
        assert len(running.providers['agent'].requests) == 2


def test_explicit_text_still_uses_only_the_text_provider_when_agent_is_default(tmp_path):
    with serving(tmp_path.resolve() / "ui") as running:
        context, created = new_session(running, mode="text")
        assert created['mode'] == 'text'
        running.providers['text'].responses = [response("جواب نصي")]
        status, result, _ = running.request(ask_payload(context))
        assert status == 200 and result['content'] == 'جواب نصي'
        status, refused, _ = running.request(ask_payload(context, "agent_ask"))
        assert status == 409 and refused['error_code'] == 'session_mode_mismatch'
        assert running.factories == {"text": 1, "agent": 0, "media": 0}


def test_explicit_media_still_uses_only_the_media_provider_when_agent_is_default(tmp_path):
    with serving(tmp_path.resolve() / "ui") as running:
        context, created = new_session(running, mode="media")
        assert created['mode'] == 'media'
        running.providers['media'].responses = [response("جواب وسائط")]
        result = running.api("ask_media", **context, turn=uuid.uuid4().hex,
                             message="صف الطلب", media=[])
        assert result['status'] == 'complete' and result['content'] == 'جواب وسائط'
        status, refused, _ = running.request(ask_payload(context, "agent_ask"))
        assert status == 409 and refused['error_code'] == 'session_mode_mismatch'
        assert running.factories == {"text": 0, "agent": 0, "media": 1}


def test_saved_legacy_metadata_without_mode_stays_text_after_agent_capable_restart(tmp_path):
    root = tmp_path.resolve() / "ui"
    with serving(root, agent_enabled=False) as first:
        context, created = new_session(first)
        first.providers['text'].responses = [response("جواب محفوظ")]
        old_payload = ask_payload(context)
        assert first.request(old_payload)[1]['content'] == 'جواب محفوظ'
        metadata = first.app.project(context['project']) / 'sessions' / context['session'] / 'meta.json'
    # Reproduce the saved schema that predates session modes, not merely explicit text.
    legacy = {key: created[key] for key in ('id', 'name')}
    metadata.write_text(json.dumps(legacy, ensure_ascii=False))
    original = metadata.read_bytes()
    with serving(root) as reopened:
        assert reopened.api('projects')['default_session_mode'] == 'agent'
        assert reopened.api('sessions', project=context['project'])['sessions'] == [legacy]
        history = reopened.api('history', **context, before=None)
        assert history['turns'][0]['content'] == 'جواب محفوظ'
        assert reopened.request(old_payload)[1]['replayed'] is True
        assert reopened.factories == {"text": 0, "agent": 0, "media": 0}
        reopened.providers['text'].responses = [response("متابعة نصية")]
        assert reopened.request(ask_payload(context))[1]['content'] == 'متابعة نصية'
        status, refused, _ = reopened.request(ask_payload(context, "agent_ask"))
        assert status == 409 and refused['error_code'] == 'session_mode_mismatch'
        assert reopened.factories == {"text": 1, "agent": 0, "media": 0}
        assert metadata.read_bytes() == original
        assert not (reopened.app.project(context['project']) / 'agent-control').exists()


def test_unavailable_agent_is_refused_without_silently_creating_text(tmp_path):
    with serving(tmp_path.resolve() / "ui", agent_enabled=False) as running:
        context, created = new_session(running)
        running.providers['text'].responses = [response("المسار النصي متاح")]
        assert running.request(ask_payload(context))[1]['content'] == 'المسار النصي متاح'
        before = running.api('sessions', project=context['project'])
        status, refused, _ = running.request({"action": "create_session", "project": context['project'],
                                             "name": "وكيل غير متاح", "mode": "agent"})
        assert status == 409 and refused['error_code'] == 'agent_unavailable'
        assert running.api('sessions', project=context['project']) == before == {'sessions': [created]}
        assert running.factories == {"text": 1, "agent": 0, "media": 0}


def test_default_agent_owner_action_waits_for_bound_approval_and_replays_once(tmp_path, monkeypatch):
    effects = []

    def write_after_approval(arguments, context):
        target = context.root / 'approved.txt'
        target.write_text(arguments['content'])
        effects.append(arguments.copy())
        return {'content': 'أثر مصطنع بعد الموافقة'}

    tool = Tool(ToolSpec('owner_fixture', 'أثر مصطنع ينتظر المالك', {
        'type': 'object', 'properties': {'content': {'type': 'string'}},
        'required': ['content'], 'additionalProperties': False}, consent='owner'), write_after_approval)
    monkeypatch.setattr(ui, 'DEFAULT_TOOLS', (*ui.DEFAULT_TOOLS, tool))
    with serving(tmp_path.resolve() / "ui") as running:
        context, _ = new_session(running)
        running.providers['agent'].responses = [
            response('أطلب الموافقة', ToolCall('owner-default', 'owner_fixture', {'content': 'موافق'})),
            response('اكتمل الأثر')]
        payload = ask_payload(context, 'agent_ask')
        status, pending, _ = running.request(payload)
        assert status == 200 and pending['status'] == 'awaiting_owner'
        view = pending['pending'][0]
        target = running.app.project(context['project']) / 'agent-workspace/approved.txt'
        assert view['consent'] == 'owner' and not target.exists() and effects == []
        decision = {'action': 'agent_decide', **context, 'action_id': view['action_id'],
                    'call_digest': view['call_digest'], 'expected_revision': view['revision'], 'approve': True}
        status, refused, _ = running.request({**decision, 'call_digest': '0' * 64})
        assert status == 409 and refused['error_code'] == 'action_binding_conflict'
        assert running.request(decision)[0] == 200
        assert not target.exists() and effects == []
        assert len(running.providers['agent'].requests) == 1
        completed = running.api('agent_resume', **context, turn=payload['turn'])
        assert completed['status'] == 'complete' and target.read_text() == 'موافق'
        assert effects == [{'content': 'موافق'}]
        action = completed['steps'][0]['tool_results'][0]
        assert action['action_id'] == view['action_id'] and action['status'] == 'ok'
        replay = running.api('agent_resume', **context, turn=payload['turn'])
        assert replay['replayed'] is True and effects == [{'content': 'موافق'}]
        assert len(running.providers['agent'].requests) == 2
        assert running.factories['text'] == running.factories['media'] == 0
