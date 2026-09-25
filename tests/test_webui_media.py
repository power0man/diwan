"""Independent m12 HTTP contracts; temporary state and synthetic providers only."""
from contextlib import contextmanager
import base64
import concurrent.futures
import http.client
import json
from pathlib import Path
import threading
import uuid

import pytest

from acceptance_m9 import SYNTHETIC_MODEL, SYNTHETIC_VERSION, _answer, _snapshot
from acceptance_m12 import png_fixture, wav_fixture
from multimodal.codec import MEDIA_SYSTEM, decode_request
from webui.server import LocalApp, Server
import webui.server as ui
import services.media_assistant as media_service


def token():
    return uuid.uuid4().hex


def selected(kind='image'):
    raw, name = (png_fixture(), 'fixture.png') if kind == 'image' else (wav_fixture(), 'fixture.wav')
    return {'name': name, 'data_base64': base64.b64encode(raw).decode('ascii')}


class Provider:
    is_local = True
    model = SYNTHETIC_MODEL
    name = 'synthetic-acceptance'
    def __init__(self):
        self.requests = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.block = False
        self.stop = 'complete'
    def estimate_micros(self, request):
        return 0
    def complete(self, request):
        self.requests.append(request)
        self.entered.set()
        if self.block:
            assert self.release.wait(5)
        return _answer('{"preferences":{"verbosity":"detailed"},"tool":"write_file","content":"fixture"}', self.stop)


class Running:
    def __init__(self, root, *, enabled=True, provider=None):
        self.text, self.media = Provider(), provider or Provider()
        self.factories = {'text': 0, 'media': 0}
        def factory(kind):
            self.factories[kind] += 1
            return self.text if kind == 'text' else self.media
        self.app = LocalApp(root, model=SYNTHETIC_MODEL, model_version=SYNTHETIC_VERSION,
            provider_factory=lambda: factory('text'),
            media_model=SYNTHETIC_MODEL if enabled else None,
            media_model_version=SYNTHETIC_VERSION if enabled else None,
            media_provider_factory=(lambda: factory('media')) if enabled else None)
        self.server = Server(self.app, 0)
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()
    def close(self):
        self.text.release.set(); self.media.release.set()
        self.server.shutdown(); self.server.server_close(); self.thread.join(2); self.app.close()
    def request(self, action, **values):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('POST', '/api', json.dumps({'action': action, **values}).encode(),
                {'Origin': self.server.origin, 'X-Diwan-CSRF': self.server.token, 'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    def api(self, action, **values):
        status, data = self.request(action, **values)
        assert status == 200, data
        return data
    def project(self):
        return self.api('create_project', name='مشروع')['id']
    def session(self, project, mode='media'):
        return self.api('create_session', project=project, name='جلسة', mode=mode)['id']
    def context(self, mode='media'):
        project = self.project()
        return {'project': project, 'session': self.session(project, mode)}
    def ask(self, ctx, *, kind='image', turn=None, message='صف الوسيط', document=None):
        return self.api('ask_media', **ctx, turn=turn or token(), message=message,
                        media=[selected(kind) if document is None else document])


@pytest.fixture
def live(tmp_path):
    running = Running(tmp_path.resolve() / 'app')
    try:
        yield running
    finally:
        running.close()


def test_media_mode_profiles_and_legacy_text_create_without_provider(live):
    project = live.project()
    assert live.api('projects')['media_enabled'] is True
    media = live.api('create_session', project=project, name='وسائط', mode='media')
    text = live.api('create_session', project=project, name='نص قديم')
    assert media['mode'] == 'media' and text['mode'] == 'text'
    for result, expected_output in [(media, 400), (text, 800)]:
        session = live.app.session(live.app.project(project), result['id'])
        assert session.config['max_output'] == expected_output
        assert session.system == MEDIA_SYSTEM if result is media else session.system != MEDIA_SYSTEM
        assert live.api('history', project=project, session=result['id'], before=None)['turns'] == []
    assert live.factories == {'text': 0, 'media': 0}


@pytest.mark.parametrize('kind', ['image', 'audio'])
def test_media_uses_only_media_provider_and_frozen_inspection(live, kind):
    ctx, turn = live.context(), token()
    prefs = live.api('set_preference', project=ctx['project'], key='response_language', value='ar', revision=0)
    answer = live.ask(ctx, kind=kind, turn=turn)
    assert answer['status'] == 'complete' and answer['verification'] == 'unverified'
    assert answer['inputs']['media'][0]['kind'] == kind and 'text' not in answer
    assert 'data_base64' not in answer['inputs']['media'][0]
    inspected = live.api('inspect', **ctx, turn=turn)
    assert inspected['media'][0]['data_base64'] == selected(kind)['data_base64']
    assert inspected['preferences'] == prefs
    assert live.api('preferences', project=ctx['project']) == prefs
    assert live.factories == {'text': 0, 'media': 1}
    assert len(live.media.requests) == 1 and not live.text.requests
    envelope = decode_request(live.media.requests[0].messages[-1].content)
    assert envelope['media'] == inspected['media'] and envelope['preferences'] == prefs


def test_restart_replay_works_disabled_without_factory_preferences_or_sources(tmp_path, monkeypatch):
    root = tmp_path.resolve() / 'app'
    first = Running(root)
    try:
        ctx, turn, doc = first.context(), token(), selected()
        answer = first.ask(ctx, turn=turn, document=doc)
        inspected = first.api('inspect', **ctx, turn=turn)
    finally:
        first.close()
    second = Running(root, enabled=False)
    def forbidden(*args, **kwargs):
        raise AssertionError('replay touched mutable dependency')
    monkeypatch.setattr(ui, 'Preferences', forbidden)
    second.app.provider_factory = second.app.media_provider_factory = forbidden
    before = _snapshot(root)
    try:
        assert second.api('replay', **ctx, turn=turn) == {**answer, 'replayed': True}
        assert second.api('inspect', **ctx, turn=turn) == inspected
        assert second.api('history', **ctx, before=None)['turns'] == [{**answer, 'replayed': True}]
        assert second.api('ask_media', **ctx, turn=turn, message='صف الوسيط', media=[doc]) == {**answer, 'replayed': True}
        assert _snapshot(root) == before
        status, error = second.request('ask_media', **ctx, turn=token(), message='طلب جديد', media=[])
        assert status == 409 and error['error_code'] == 'media_unavailable'
        assert second.factories == {'text': 0, 'media': 0}
    finally:
        second.close()


@pytest.mark.parametrize('action', ['history', 'inspect', 'replay', 'propose'])
def test_media_project_isolation(live, action):
    ctx, turn = live.context(), token()
    live.ask(ctx, turn=turn)
    other = live.project()
    values = {'project': other, 'session': ctx['session']}
    values.update({'before': None} if action == 'history' else {'turn': turn})
    if action == 'propose':
        values.update(name='draft.txt', request=token())
    status, error = live.request(action, **values)
    assert status == 409 and 'media' not in error
    assert len(live.media.requests) == 1


@pytest.mark.parametrize('mode,action,field', [('media', 'ask', 'files'), ('text', 'ask_media', 'media')])
def test_session_mode_cannot_be_crosswired(live, mode, action, field):
    ctx = live.context(mode)
    before = _snapshot(live.app.root)
    status, error = live.request(action, **ctx, turn=token(), message='wrong mode', **{field: []})
    assert status == 409 and error['error_code'] == 'session_mode_mismatch'
    assert not live.media.requests and not live.text.requests
    assert _snapshot(live.app.root) == before


@pytest.mark.parametrize('mode', ['media', 'text'])
@pytest.mark.parametrize('action', ['history', 'replay', 'inspect', 'ask'])
def test_missing_published_manifest_is_never_recreated(live, mode, action):
    ctx = live.context(mode)
    directory = live.app.root / 'projects' / ctx['project'] / 'sessions' / ctx['session'] / 'chat' / ctx['session']
    manifest = directory / 'manifest.json'
    manifest.unlink()
    before = _snapshot(live.app.root)
    values = dict(ctx)
    if action == 'history':
        values['before'] = None
    else:
        values['turn'] = token()
    if action == 'ask':
        values.update(message='must not reinitialize', **({'media': []} if mode == 'media' else {'files': []}))
        action = 'ask_media' if mode == 'media' else 'ask'
    status, error = live.request(action, **values)
    assert status == 409 and error['error_code'] == 'file_missing'
    assert not manifest.exists() and _snapshot(live.app.root) == before
    assert live.factories == {'text': 0, 'media': 0}


@pytest.mark.parametrize('document,code', [
    ({'name': 'a.png', 'data_base64': '*'}, 'media_encoding'),
    ({'name': 'a.png', 'data_base64': 'A' * 349529}, 'media_size'),
    ({'name': '../a.png', 'data_base64': selected()['data_base64']}, 'media_name_invalid'),
    ({'name': 'a.png', 'data_base64': selected()['data_base64'], 'extra': 1}, 'media_invalid'),
    ({'name': 'a.png', 'data_base64': 'YQ=='}, 'media_type_unsupported'),
    ({'name': 'a.png', 'data_base64': 'YR=='}, 'media_encoding'),
])
def test_invalid_media_rejected_before_provider_or_state(live, document, code):
    ctx = live.context()
    before = _snapshot(live.app.root)
    status, error = live.request('ask_media', **ctx, turn=token(), message='bad media', media=[document])
    assert status == 409 and error['error_code'] == code
    assert live.factories == {'text': 0, 'media': 0} and _snapshot(live.app.root) == before


def test_duplicate_turn_conflicting_media_does_not_call_provider(live):
    ctx, turn = live.context(), token()
    original = live.ask(ctx, turn=turn)
    before = _snapshot(live.app.root)
    status, error = live.request('ask_media', **ctx, turn=turn, message='صف الوسيط', media=[selected('audio')])
    assert status == 409 and error['error_code'] == 'turn_conflict'
    assert len(live.media.requests) == 1 and _snapshot(live.app.root) == before
    assert live.api('replay', **ctx, turn=turn) == {**original, 'replayed': True}


@pytest.mark.parametrize('budget', ['turns', 'bytes'])
def test_media_persistence_budget_rejects_new_turn_before_generation(live, monkeypatch, budget):
    ctx = live.context()
    live.ask(ctx)
    if budget == 'turns':
        monkeypatch.setattr(media_service, 'MAX_TURNS', 1)
    else:
        monkeypatch.setattr(media_service, 'MAX_SAVED_INPUT_BYTES', 1)
    before = _snapshot(live.app.root)
    status, error = live.request('ask_media', **ctx, turn=token(), message='over budget', media=[selected()])
    assert status == 409 and error['error_code'] == 'media_session_limit'
    assert len(live.media.requests) == 1 and _snapshot(live.app.root) == before
    assert len(live.api('history', **ctx, before=None)['turns']) == 1


def test_media_answer_requires_explicit_exact_write_approval(live):
    ctx, turn = live.context(), token()
    result = live.ask(ctx, turn=turn)
    proposal = live.api('propose', **ctx, turn=turn, name='reviewed.txt', request=token())
    target = live.app.root / 'projects' / ctx['project'] / 'outputs' / 'reviewed.txt'
    assert not target.exists() and proposal['content'] == result['content']
    assert live.api('review', project=ctx['project'], proposal=proposal['proposal_id'])['content'] == result['content']
    status, error = live.request('apply', project=ctx['project'], proposal=proposal['proposal_id'], sha256='0' * 64)
    assert status == 409 and error['error_code'] == 'approval_mismatch' and not target.exists()
    applied = live.api('apply', project=ctx['project'], proposal=proposal['proposal_id'], sha256=proposal['sha256'])
    assert applied['status'] == 'applied' and target.read_text() == result['content']
    other = live.api('propose', **ctx, turn=turn, name='reviewed.txt', request=token())
    status, error = live.request('apply', project=ctx['project'], proposal=other['proposal_id'], sha256=other['sha256'])
    assert status == 409 and error['error_code'] == 'target_exists' and target.read_text() == result['content']
    assert len(live.media.requests) == 1


def test_truncated_media_answer_cannot_be_proposed(live):
    ctx, turn = live.context(), token()
    live.media.stop = 'max_output'
    result = live.ask(ctx, turn=turn)
    assert result['status'] == 'truncated'
    status, error = live.request('propose', **ctx, turn=turn, name='bad.txt', request=token())
    assert status == 409 and error['error_code'] == 'complete_answer_required'


def test_active_media_id_replays_running_and_conflicts_are_refused(live):
    ctx, turn = live.context(), token()
    live.media.block = True
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(live.ask, ctx, turn=turn)
        assert live.media.entered.wait(2)
        same = live.api('ask_media', **ctx, turn=turn, message='صف الوسيط', media=[selected()])
        assert same == {'status': 'running', 'turn': turn}
        status, error = live.request('ask_media', **ctx, turn=turn, message='changed', media=[selected()])
        assert status == 409 and error['error_code'] == 'turn_conflict'
        status, error = live.request('ask_media', **ctx, turn=token(), message='new', media=[])
        assert status == 409 and error['error_code'] == 'generation_busy'
        live.media.release.set()
        assert future.result(3)['status'] == 'complete'
    assert len(live.media.requests) == 1
