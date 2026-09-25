"""Independent HTTP audit. Synthetic provider and temporary roots only.

Run with the project interpreter. The module is intentionally outside checkout.
Tests named regression_* encode desired behavior for discovered defects.
"""
import concurrent.futures
from contextlib import contextmanager
import http.client
import json
from pathlib import Path
import socket
import sys
import subprocess
import threading
import time
import uuid

import pytest

from acceptance_m9 import SyntheticProvider, _answer, SYNTHETIC_MODEL, SYNTHETIC_VERSION
from webui.server import LocalApp, Server, Handler
import webui.server as ui
from workspace_tools.files import TextWorkspace

REPO = Path(__file__).resolve().parents[1]


class CountedProvider(SyntheticProvider):
    def __init__(self):
        super().__init__([_answer('جواب مصطنع مستقل') for _ in range(10)])
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False

    def complete(self, request):
        self.calls += 1
        self.entered.set()
        if self.block:
            assert self.release.wait(8), 'test did not release provider'
        return super().complete(request)


class Running:
    def __init__(self, root):
        self.provider = CountedProvider()
        self.app = LocalApp(root, model=SYNTHETIC_MODEL,
                            model_version=SYNTHETIC_VERSION,
                            provider_factory=lambda: self.provider)
        self.server = Server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.provider.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.app.close()

    def headers(self):
        return {'Host':self.server.origin[7:], 'Origin':self.server.origin,
                'X-Diwan-CSRF':self.server.token, 'Content-Type':'application/json'}

    def request(self, data=None, *, headers=None, raw=None, method='POST', path='/api'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            body = raw if raw is not None else json.dumps(data, ensure_ascii=False).encode()
            connection.request(method, path, body=body, headers=headers if headers is not None else self.headers())
            response = connection.getresponse()
            payload = response.read()
            try:
                payload = json.loads(payload)
            except (ValueError, UnicodeError):
                pass
            return response.status, payload, dict(response.getheaders())
        finally:
            connection.close()

    def api(self, action, **values):
        status, payload, _ = self.request({'action':action, **values})
        assert status == 200, payload
        return payload

    def create(self):
        project = self.api('create_project', name='مشروع التدقيق')['id']
        session = self.api('create_session', project=project, name='جلسة التدقيق')['id']
        return project, session

    def raw(self, data):
        with socket.create_connection(('127.0.0.1',self.server.server_port), timeout=3) as sock:
            sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)
            response = b''
            while True:
                part = sock.recv(65536)
                if not part:
                    break
                response += part
            return response


@pytest.fixture
def live(tmp_path):
    server = Running(tmp_path.resolve() / 'ui')
    try:
        yield server
    finally:
        server.close()


def ask_request(project, session, **changes):
    return {'action':'ask','project':project,'session':session,
            'turn':uuid.uuid4().hex,'message':'طلب التدقيق','files':[], **changes}


def test_static_token_boundary_and_security_headers(live):
    status, body, headers = live.request(method='GET', path='/', raw=b'')
    assert status == 200 and live.server.token.encode() in body
    assert b'__DIWAN_TOKEN__' not in body
    assert headers['Cache-Control'] == 'no-store'
    assert headers['X-Content-Type-Options'] == 'nosniff'
    assert headers['X-Frame-Options'] == 'DENY'
    assert "default-src 'none'" in headers['Content-Security-Policy']


@pytest.mark.parametrize('path', ['/../server.py','/%2e%2e/server.py','/api','/index.html','/?token=x','/app.js?x=1'])
def test_no_generic_file_server(live, path):
    assert live.request(method='GET', path=path)[0] == 403


@pytest.mark.parametrize('name,value', [
    ('Host','localhost:8765'),('Host','127.0.0.1:0'),('Host','example.invalid'),
    ('Origin','null'),('Origin','https://example.invalid'),('Origin','http://localhost:8765'),
    ('X-Diwan-CSRF','wrong'),('Sec-Fetch-Site','cross-site'),('Sec-Fetch-Site','same-site'),
    ('Content-Type','text/plain'),('Content-Encoding','gzip'),('Transfer-Encoding','chunked'),
])
def test_boundary_refuses_without_mutating(live, name, value):
    headers = live.headers(); headers[name] = value
    status, result, _ = live.request({'action':'create_project','name':'مرفوض'},headers=headers)
    assert status == 403 and result['error_code'] == 'http_refused'
    assert live.api('projects')['projects'] == []
    assert live.provider.calls == 0


@pytest.mark.parametrize('missing', ['Origin','X-Diwan-CSRF','Content-Type'])
def test_missing_required_headers(live, missing):
    headers = live.headers(); del headers[missing]
    assert live.request({'action':'projects'},headers=headers)[0] == 403


@pytest.mark.parametrize('duplicate', ['Host','Origin','X-Diwan-CSRF','Content-Type','Content-Length'])
def test_duplicate_security_and_framing_headers(live, duplicate):
    body = b'{"action":"projects"}'
    headers = live.headers(); headers['Content-Length'] = str(len(body))
    lines = [f'{k}: {v}' for k,v in headers.items()]
    lines.append(f'{duplicate}: {headers[duplicate]}')
    packet = ('POST /api HTTP/1.1\r\n'+'\r\n'.join(lines)+'\r\n\r\n').encode()+body
    response = live.raw(packet)
    assert b' 403 ' in response.split(b'\r\n',1)[0]
    assert live.provider.calls == 0


@pytest.mark.parametrize('raw', [b'[]', b'{"action":"projects","action":"projects"}',
    b'{"action":"projects","x":NaN}', b'{"action":"projects","x":1.2}',
    b'{"action":"projects","x":9007199254740992}', b'{"action":"projects","x":"\xff"}',
    b'{"action":'+b'['*2000+b'0'+b']'*2000+b'}'])
def test_closed_json(live, raw):
    status, data, _ = live.request(raw=raw)
    assert status == 409 and data['error_code'] == 'json_invalid'
    assert live.provider.calls == 0


@pytest.mark.parametrize('length,body,expected', [('0',b'',b'body_limit'),
    ('524289',b'',b'body_limit'),('-1',b'',b'http_refused'),
    ('+2',b'{}',b'http_refused'),('2,2',b'{}',b'http_refused'),
    ('30',b'{"action":"projects"}',b'body_incomplete')])
def test_bad_content_length(live, length, body, expected):
    headers = live.headers(); headers['Content-Length'] = length
    packet = ('POST /api HTTP/1.1\r\n'+'\r\n'.join(f'{k}: {v}' for k,v in headers.items())+'\r\n\r\n').encode()+body
    assert expected in live.raw(packet)
    assert live.provider.calls == 0


def test_pipeline_second_request_is_not_executed(live):
    def packet(name):
        body = json.dumps({'action':'create_project','name':name}).encode()
        headers=live.headers(); headers['Content-Length']=str(len(body))
        return ('POST /api HTTP/1.1\r\n'+'\r\n'.join(f'{k}: {v}' for k,v in headers.items())+'\r\n\r\n').encode()+body
    response=live.raw(packet('الأول')+packet('الثاني'))
    assert response.count(b'HTTP/1.0 ') == 1
    assert [p['name'] for p in live.api('projects')['projects']] == ['الأول']


def test_duplicate_active_and_saved_turn_calls_provider_once(live):
    project, session = live.create(); request=ask_request(project,session)
    live.provider.block=True
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        initial=executor.submit(live.request,request)
        assert live.provider.entered.wait(3)
        assert live.request(request)[1]['status']=='running'
        changed={**request,'message':'نص مختلف'}
        assert live.request(changed)[1]['error_code']=='turn_conflict'
        newer={**request,'turn':uuid.uuid4().hex}
        assert live.request(newer)[1]['error_code']=='generation_busy'
        assert live.api('history',project=project,session=session,before=None)['status']=='running'
        live.provider.release.set()
        status, result, _=initial.result(4)
    assert status==200 and result['status']=='complete'
    replay=live.api('replay',project=project,session=session,turn=request['turn'])
    assert replay['replayed'] and replay['content']==result['content']
    assert live.request(request)[1]['replayed']
    assert live.provider.calls==1


def test_disconnected_client_does_not_trigger_regeneration(live):
    project,session=live.create(); request=ask_request(project,session)
    live.provider.block=True
    body=json.dumps(request).encode(); headers=live.headers();headers['Content-Length']=str(len(body))
    packet=('POST /api HTTP/1.1\r\n'+'\r\n'.join(f'{k}: {v}' for k,v in headers.items())+'\r\n\r\n').encode()+body
    sock=socket.create_connection(('127.0.0.1',live.server.server_port),timeout=3)
    sock.sendall(packet); assert live.provider.entered.wait(3);sock.close();live.provider.release.set()
    deadline=time.monotonic()+4
    while live.app.active is not None and time.monotonic()<deadline:
        time.sleep(.01)
    assert live.app.active is None
    replay=live.api('replay',project=project,session=session,turn=request['turn'])
    assert replay['status']=='complete' and replay['replayed']
    assert live.request(request)[1]['replayed']
    assert live.provider.calls==1


def test_replay_uses_frozen_inputs_after_sources_disappear(live):
    project,session=live.create()
    upload=live.api('upload',project=project,upload=uuid.uuid4().hex,name='input.txt',content='نسخة أولى')
    request=ask_request(project,session,files=[upload['path']])
    original=live.request(request)[1]
    (live.app.root/'projects'/project/'uploads'/upload['path']).unlink()
    live.api('set_preference',project=project,key='address_name',value='اسم جديد',revision=0)
    replay=live.request(request)[1]
    assert replay['replayed'] and replay['inputs']==original['inputs']
    inspect=live.api('inspect',project=project,session=session,turn=request['turn'])
    assert inspect['attachments'][0]['content']=='نسخة أولى'
    assert inspect['preferences']['revision']==0
    assert live.provider.calls==1


def test_regression_apply_error_is_not_http_success(live):
    project,session=live.create(); request=ask_request(project,session)
    assert live.request(request)[1]['status']=='complete'
    def proposal():
        return live.api('propose',project=project,session=session,turn=request['turn'],name='draft.txt',request=uuid.uuid4().hex)
    first,second=proposal(),proposal()
    assert live.api('apply',project=project,proposal=first['proposal_id'],sha256=first['sha256'])['status']=='applied'
    status,result,_=live.request({'action':'apply','project':project,'proposal':second['proposal_id'],'sha256':second['sha256']})
    assert result['error_code']=='target_exists'
    assert status>=400, result


def test_regression_upload_closed_result_must_be_exposed(live,monkeypatch):
    project,_=live.create(); upload=uuid.uuid4().hex
    request={'action':'upload','project':project,'upload':upload,'name':'fixture.txt','content':'أصلي'}
    saved=TextWorkspace._save
    def interrupt(fd,state):
        if state['proposals'] and state['proposals'][-1]['owner'] is not None:
            raise KeyboardInterrupt()
        return saved(fd,state)
    monkeypatch.setattr(TextWorkspace,'_save',staticmethod(interrupt))
    with pytest.raises(KeyboardInterrupt): live.app.dispatch(request)
    monkeypatch.setattr(TextWorkspace,'_save',staticmethod(saved))
    status,result,_=live.request(request)
    assert status>=400 and result.get('error_code')=='outcome_uncertain', result


def test_regression_project_creation_failure_does_not_hide_existing(live,monkeypatch):
    project,_=live.create(); original=ui._write_json
    def interrupted(fd,name,value):
        if name=='meta.json': raise OSError('fixture interruption')
        return original(fd,name,value)
    monkeypatch.setattr(ui,'_write_json',interrupted)
    assert live.request({'action':'create_project','name':'انقطاع'})[0]>=400
    monkeypatch.setattr(ui,'_write_json',original)
    status,result,_=live.request({'action':'projects'})
    assert status==200 and project in {p['id'] for p in result['projects']}, result


def test_connection_capacity_is_bounded_and_recovers(live):
    sockets=[]
    try:
        for _ in range(8):
            client=socket.create_connection(('127.0.0.1',live.server.server_port),timeout=2)
            client.sendall(b'GET / HTTP/1.1\r\nX-Incomplete: ')
            sockets.append(client)
        deadline=time.monotonic()+2
        while live.server.slots._value and time.monotonic()<deadline:
            time.sleep(.01)
        assert live.server.slots._value==0
        # Closing the excess connection can race either send() or getresponse().
        with pytest.raises((BrokenPipeError,ConnectionResetError,http.client.RemoteDisconnected)):
            live.request({'action':'projects'})
    finally:
        for client in sockets: client.close()
    deadline=time.monotonic()+2
    while live.server.slots._value!=8 and time.monotonic()<deadline:
        time.sleep(.01)
    assert live.api('projects')['projects']==[]


def test_slow_header_absolute_deadline_probe(live,monkeypatch):
    """Regression for the actual production 12-second probe, scaled for CI."""
    monkeypatch.setattr(Handler,'receive_timeout_s',.15,raising=False)
    sockets=[]
    try:
        for _ in range(8):
            client=socket.create_connection(('127.0.0.1',live.server.server_port),timeout=3)
            client.sendall(b'GET / HTTP/1.1\r\nX-Incomplete: ')
            sockets.append(client)
        started=time.monotonic()
        while time.monotonic()-started<.5:
            for client in sockets:
                try: client.sendall(b'a')
                except OSError: pass
            time.sleep(.04)
        # An absolute input deadline must have released these headers by now.
        try:
            status,_,_=live.request({'action':'projects'})
        except (ConnectionResetError,http.client.RemoteDisconnected):
            status=0
        assert status==200, 'All eight slots remain occupied beyond the absolute input deadline'
    finally:
        for client in sockets: client.close()


def test_slow_body_absolute_deadline_does_not_dispatch(live,monkeypatch):
    monkeypatch.setattr(Handler,'receive_timeout_s',.15,raising=False)
    headers=live.headers();headers['Content-Length']='1000'
    packet=('POST /api HTTP/1.1\r\n'+'\r\n'.join(f'{k}: {v}' for k,v in headers.items())+'\r\n\r\n').encode()+b'{'
    with socket.create_connection(('127.0.0.1',live.server.server_port),timeout=3) as client:
        client.sendall(packet)
        started=time.monotonic()
        while time.monotonic()-started<.5:
            try: client.sendall(b' ')
            except OSError: break
            time.sleep(.04)
        assert live.server.slots._value==8
    assert live.api('projects')['projects']==[] and live.provider.calls==0


def test_receive_deadline_is_cancelled_before_generation(live,monkeypatch):
    project,session=live.create();request=ask_request(project,session)
    monkeypatch.setattr(Handler,'receive_timeout_s',.1,raising=False)
    live.provider.block=True
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future=executor.submit(live.request,request)
        assert live.provider.entered.wait(2)
        time.sleep(.3)
        live.provider.release.set()
        status,result,_=future.result(3)
    assert status==200 and result['status']=='complete' and live.provider.calls==1


def test_restart_replay_never_constructs_provider(tmp_path):
    root=tmp_path.resolve()/'ui'
    original=Running(root)
    try:
        project,session=original.create(); request=ask_request(project,session)
        result=original.request(request)[1]
        assert result['status']=='complete' and original.provider.calls==1
    finally:
        original.close()
    resumed=Running(root)
    try:
        def forbidden(): raise AssertionError('replay constructed provider')
        resumed.app.provider_factory=forbidden
        resumed.app.model='synthetic-reconfigured-fixture'
        resumed.app.model_version='b'*64
        replay=resumed.request(request)[1]
        assert replay['replayed'] and replay['content']==result['content']
        assert resumed.api('replay',project=project,session=session,turn=request['turn'])['content']==result['content']
        assert resumed.provider.calls==0
    finally:
        resumed.close()


def test_abrupt_process_restart_closes_pending_without_provider(tmp_path):
    root=tmp_path.resolve()/'ui'
    first=Running(root)
    try:
        project,session=first.create();request=ask_request(project,session)
    finally:
        first.close()
    script='''
import json,os,sys
sys.path.insert(0,sys.argv[1])
from webui.server import LocalApp
from acceptance_m9 import SyntheticProvider,SYNTHETIC_MODEL,SYNTHETIC_VERSION
import conversation.session as session
session.execute=lambda *args,**kwargs: os._exit(70)
app=LocalApp(sys.argv[2],model=SYNTHETIC_MODEL,model_version=SYNTHETIC_VERSION,
             provider_factory=lambda:SyntheticProvider([]))
app.dispatch(json.loads(sys.argv[3]))
'''
    crashed=subprocess.run([sys.executable,'-c',script,str(REPO),str(root),json.dumps(request)],capture_output=True,timeout=5)
    assert crashed.returncode==70,crashed.stderr.decode()
    resumed=Running(root)
    try:
        def forbidden(): raise AssertionError('uncertain turn constructed provider')
        resumed.app.provider_factory=forbidden
        result=resumed.api('replay',project=project,session=session,turn=request['turn'])
        assert result['status']=='error' and result['error_code']=='outcome_uncertain'
        again=resumed.request(request)[1]
        assert again['replayed'] and again['error_code']=='outcome_uncertain'
        assert resumed.provider.calls==0
    finally:
        resumed.close()


@pytest.mark.parametrize('name',['../escape.txt','/absolute.txt','nested/file.txt','bad\u200fname.txt'])
def test_upload_names_cannot_escape_selected_project(live,name):
    project,_=live.create()
    status,result,_=live.request({'action':'upload','project':project,'upload':uuid.uuid4().hex,'name':name,'content':'محتوى'})
    assert status>=400 and 'error_code' in result
    assert live.api('files',project=project)['files']==[]


def test_preference_cas_survives_simultaneous_http_writes(live):
    project,_=live.create()
    live.api('preferences',project=project)
    def change(value):
        return live.request({'action':'set_preference','project':project,'key':'address_name','value':value,'revision':0})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(change,['أول','ثان']))
    assert sorted(status for status,_,_ in results)==[200,409]
    failed=next(payload for status,payload,_ in results if status==409)
    assert failed['error_code'] in ('preference_revision_conflict','preference_busy')
    snapshot=live.api('preferences',project=project)
    assert snapshot['revision']==1 and snapshot['values']['address_name'] in ('أول','ثان')
    assert live.provider.calls==0
