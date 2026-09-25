import uuid
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from webui.server import LocalApp
import webui.server as ui
from workspace_tools.files import TextWorkspace
from core.contracts import Response, Usage


class Provider:
    is_local = True
    calls = []
    def estimate_micros(self, request):
        return 0
    def complete(self, request):
        self.calls.append(request)
        return Response('جواب محلي', Usage(1, 1), 'complete', 0)


@pytest.fixture
def app(tmp_path):
    value = LocalApp(tmp_path.resolve() / 'ui', model='synthetic',
                     model_version='a' * 64, provider_factory=Provider)
    try:
        yield value
    finally:
        value.close()


def create(app):
    project = app.dispatch({'action': 'create_project', 'name': 'مشروع'})['id']
    session = app.dispatch({'action': 'create_session', 'project': project, 'name': 'جلسة'})['id']
    return project, session


def ask(app, project, session, message='طلب'):
    turn = uuid.uuid4().hex
    result = app.dispatch({'action':'ask','project':project,'session':session,
                          'turn':turn,'message':message,'files':[]})
    return turn, result


def test_apply_existing_default_name_returns_error_not_creation(app):
    project, session = create(app)
    turn, _ = ask(app, project, session)
    def draft():
        return app.dispatch({'action':'propose','project':project,'session':session,
                             'turn':turn,'name':'مسودة.txt','request':uuid.uuid4().hex})
    first, second = draft(), draft()
    assert app.dispatch({'action':'apply','project':project,'proposal':first['proposal_id'],
                         'sha256':first['sha256']})['status'] == 'applied'
    result = app.dispatch({'action':'apply','project':project,'proposal':second['proposal_id'],
                           'sha256':second['sha256']})
    assert result['status'] == 'error' and result['error_code'] == 'target_exists'
    # HTTP maps this closed write failure to 409; the UI must not announce creation.


def test_upload_must_not_claim_success_after_uncertain_creation(app, monkeypatch):
    project, _ = create(app)
    upload = uuid.uuid4().hex
    request = {'action':'upload','project':project,'upload':upload,
               'name':'note.txt','content':'محتوى الرفع الأصلي'}
    save = TextWorkspace._save
    def crash(fd, state):
        if state['proposals'] and state['proposals'][-1]['owner'] is not None:
            raise KeyboardInterrupt()
        save(fd, state)
    monkeypatch.setattr(TextWorkspace, '_save', staticmethod(crash))
    with pytest.raises(KeyboardInterrupt):
        app.dispatch(request)
    monkeypatch.setattr(TextWorkspace, '_save', staticmethod(save))
    # A correct endpoint must expose the closed error or raise, not return success metadata.
    result = app.dispatch(request)
    assert result.get('status') == 'error', result


def test_failed_upload_must_not_be_listed_for_attachment(app, monkeypatch):
    project, _ = create(app)
    request = {'action':'upload','project':project,'upload':uuid.uuid4().hex,
               'name':'note.txt','content':'محتوى الرفع الأصلي'}
    save = TextWorkspace._save
    def crash(fd, state):
        if state['proposals'] and state['proposals'][-1]['owner'] is not None:
            raise KeyboardInterrupt()
        save(fd, state)
    monkeypatch.setattr(TextWorkspace, '_save', staticmethod(crash))
    with pytest.raises(KeyboardInterrupt):
        app.dispatch(request)
    monkeypatch.setattr(TextWorkspace, '_save', staticmethod(save))
    files = app.dispatch({'action':'files','project':project})['files']
    assert files == [], files


def test_interrupted_project_creation_does_not_hide_existing_projects(app, monkeypatch):
    project, _ = create(app)
    write = ui._write_json
    def fail(fd, name, value):
        if name == 'meta.json':
            raise OSError('synthetic metadata interruption')
        write(fd, name, value)
    monkeypatch.setattr(ui, '_write_json', fail)
    with pytest.raises(OSError):
        app.dispatch({'action':'create_project','name':'قطع أثناء الإنشاء'})
    monkeypatch.setattr(ui, '_write_json', write)
    projects = app.dispatch({'action':'projects'})['projects']
    assert project in {p['id'] for p in projects}


def test_old_model_history_and_replay_do_not_construct_provider(app):
    project, session = create(app)
    turn, original = ask(app, project, session)
    app.model = 'changed'
    app.model_version = 'b' * 64
    def forbidden():
        raise AssertionError('provider must not be constructed')
    app.provider_factory = forbidden
    replay = app.dispatch({'action':'replay','project':project,'session':session,'turn':turn})
    history = app.dispatch({'action':'history','project':project,'session':session,'before':None})
    assert replay['content'] == original['content'] and replay['replayed']
    assert history['turns'][0]['content'] == original['content']


def test_unknown_session_does_not_create_chat_files(app):
    project, _ = create(app)
    unknown = uuid.uuid4().hex
    with pytest.raises(Exception):
        app.dispatch({'action':'history','project':project,'session':unknown,'before':None})
    assert not (app.root/'projects'/project/'sessions'/unknown).exists()


def test_project_files_and_preferences_remain_separate(app):
    p1, s1 = create(app)
    p2, s2 = create(app)
    app.dispatch({'action':'set_preference','project':p1,'key':'address_name','value':'الأول','revision':0})
    uploaded = app.dispatch({'action':'upload','project':p1,'upload':uuid.uuid4().hex,
                            'name':'first.txt','content':'بيانات المشروع الأول'})
    assert app.dispatch({'action':'preferences','project':p2})['values'] == {}
    assert app.dispatch({'action':'files','project':p2})['files'] == []
    with pytest.raises(Exception):
        app.dispatch({'action':'ask','project':p2,'session':s2,'turn':uuid.uuid4().hex,
                      'message':'اقرأ','files':[uploaded['path']]})


def test_inflight_duplicate_history_and_other_project_are_bounded(app):
    p1, s1 = create(app)
    p2, s2 = create(app)
    entered, release = threading.Event(), threading.Event()
    calls = []
    class SlowProvider(Provider):
        def complete(self, request):
            calls.append(request)
            entered.set()
            assert release.wait(3)
            return Response('محفوظ', Usage(1, 1), 'complete', 0)
    app.provider_factory = SlowProvider
    request = {'action':'ask','project':p1,'session':s1,'turn':uuid.uuid4().hex,
               'message':'طلب واحد','files':[]}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(app.dispatch, request)
        try:
            assert entered.wait(2)
            assert app.dispatch(request)['status'] == 'running'
            with pytest.raises(ui.UIError, match='turn_conflict'):
                app.dispatch({**request, 'message':'مختلف'})
            assert app.dispatch({'action':'history','project':p1,'session':s1,'before':None})['status'] == 'running'
            assert app.dispatch({'action':'history','project':p2,'session':s2,'before':None})['turns'] == []
            with pytest.raises(ui.UIError, match='generation_busy'):
                app.dispatch({**request,'project':p2,'session':s2,'turn':uuid.uuid4().hex})
        finally:
            release.set()
        assert future.result(2)['status'] == 'complete'
    assert len(calls) == 1
    assert app.dispatch(request)['replayed'] is True
    assert len(calls) == 1 and app.active is None


def test_invalid_attachment_releases_generation_without_provider(app):
    project, session = create(app)
    calls = []
    app.provider_factory = lambda: calls.append('constructor') or Provider()
    with pytest.raises(Exception):
        app.dispatch({'action':'ask','project':project,'session':session,
                      'turn':uuid.uuid4().hex,'message':'طلب','files':['../outside']})
    # Constructor is harmless; no complete request reaches the provider.
    assert app.active is None
    assert app.generation.acquire(blocking=False)
    app.generation.release()
    assert app.dispatch({'action':'history','project':project,'session':session,'before':None})['turns'] == []


@pytest.mark.parametrize('damage', ['changed_bytes', 'same_bytes_new_inode', 'unowned_file'])
def test_uploaded_attachment_requires_matching_owned_receipt(app, damage):
    project, session = create(app)
    uploaded = app.dispatch({'action':'upload','project':project,'upload':uuid.uuid4().hex,
                             'name':'data.txt','content':'frozen upload'})
    target = app.root / 'projects' / project / 'uploads' / uploaded['path']
    if damage == 'changed_bytes':
        target.write_text('changed upload')
    elif damage == 'same_bytes_new_inode':
        old = target.with_name('old-private-file')
        target.rename(old)
        target.write_text('frozen upload')
        target.chmod(0o600)
    else:
        target = target.with_name(uuid.uuid4().hex + '--unowned.txt')
        target.write_text('unowned upload')
        target.chmod(0o600)
    before = {str(p.relative_to(app.root)):(p.read_bytes(),p.stat().st_mtime_ns)
              for p in app.root.rglob('*') if p.is_file()}
    catalog = app.dispatch({'action':'files','project':project})
    assert target.name not in {d['path'] for d in catalog['files']}
    after = {str(p.relative_to(app.root)):(p.read_bytes(),p.stat().st_mtime_ns)
             for p in app.root.rglob('*') if p.is_file()}
    assert after == before
    app.provider_factory = lambda: pytest.fail('invalid attachment must not construct provider')
    with pytest.raises(ui.UIError, match='attachment_unavailable'):
        app.dispatch({'action':'ask','project':project,'session':session,'turn':uuid.uuid4().hex,
                      'message':'read','files':[target.name]})


def test_new_empty_session_binds_original_model_before_first_turn(app):
    project, session = create(app)
    app.model_version = 'b' * 64
    app.provider_factory = lambda: pytest.fail('old model session cannot construct changed provider')
    with pytest.raises(ui.UIError, match='model_changed_new_session'):
        ask(app, project, session)
    assert app.dispatch({'action':'history','project':project,'session':session,'before':None})['turns'] == []


def test_interrupted_session_creation_does_not_hide_existing_sessions(app, monkeypatch):
    project, session = create(app)
    constructor = ui.ChatSession
    def interrupted(*args, **kwargs):
        constructor(*args, **kwargs)
        raise OSError('interrupted before session publication')
    monkeypatch.setattr(ui, 'ChatSession', interrupted)
    with pytest.raises(OSError):
        app.dispatch({'action':'create_session','project':project,'name':'interrupted'})
    monkeypatch.setattr(ui, 'ChatSession', constructor)
    sessions = app.dispatch({'action':'sessions','project':project})['sessions']
    assert [s['id'] for s in sessions] == [session]
