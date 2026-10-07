"""UI requests through the real HTTP handler; no listening socket or paid model."""
import base64
from email.message import Message
import hashlib
import http.client
import io
import json
from pathlib import Path
import struct
import subprocess
import threading
from types import SimpleNamespace
import uuid
import zlib

import pytest

from core.contracts import ToolCall
from multimodal.codec import MAX_MEDIA_BYTES, MAX_PDF_BYTES
from services import agent_workspace
from tests.test_agent_webui import Provider, response
from webui.server import Handler, LocalApp, MAX_BODY, Server
from workspace_tools.files import WorkspaceError


def png_bytes(seed=338):
    def chunk(kind, raw):
        return struct.pack('>I', len(raw)) + kind + raw + struct.pack('>I', zlib.crc32(kind + raw) & 0xffffffff)
    # Deterministic incompressible pixels exercise the old 64 KiB copy ceiling.
    import random
    rng = random.Random(seed)
    pixels = b''.join(b'\0' + rng.randbytes(256 * 3) for _ in range(128))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 256, 128, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b''))


def pdf_bytes(size=MAX_BODY + 1000):
    raw = b'%PDF-1.4\n'
    offsets = [0]
    for number, body in enumerate([
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 150] >>',
    ], 1):
        offsets.append(len(raw))
        raw += f'{number} 0 obj\n'.encode() + body + b'\nendobj\n'
    start = len(raw)
    raw += b'xref\n0 4\n0000000000 65535 f \n'
    raw += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    raw += b'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n' + str(start).encode() + b'\n'
    return raw + b'%' + b'x' * (size - len(raw) - 8) + b'\n%%EOF\n'


class InMemoryHTTP(Handler):
    def stop_receive(self, **kwargs):
        pass

    def reply(self, status, value, *args, **kwargs):
        self.result = status, value


def post(app, action, *, _binary_headers=None, **values):
    raw = json.dumps({'action': action, **values}).encode()
    handler = object.__new__(InMemoryHTTP)
    handler.server = SimpleNamespace(app=app, origin='http://127.0.0.1:338', origin_host='127.0.0.1:338',
                                     public_origin=None, token='fixture', requires_bootstrap=False)
    handler.headers = Message()
    for key, value in {'Host': handler.server.origin_host, 'Origin': handler.server.origin,
                       'X-Diwan-CSRF': 'fixture', 'Content-Type': 'application/json',
                       'Content-Length': str(len(raw))}.items():
        handler.headers[key] = value
    for value in (_binary_headers if _binary_headers is not None else ['1'] if action == 'upload_binary' else []):
        handler.headers['X-Diwan-Binary-Upload'] = value
    handler.command, handler.path = 'POST', '/api'
    handler.rfile = io.BytesIO(raw)
    handler.do_POST()
    return handler.result


@pytest.fixture
def app(tmp_path):
    provider = Provider()
    app = LocalApp(tmp_path.resolve() / 'ui', model='fixture', model_version='a' * 64,
                   provider_factory=lambda: provider, agent_provider_factory=lambda: provider)
    app.test_provider = provider
    app.test_project = app.dispatch({'action': 'create_project', 'name': 'مرفقات'})['id']
    yield app
    app.close()


def upload(app, name, raw):
    return post(app, 'upload_binary', project=app.test_project, upload=uuid.uuid4().hex,
                name=name, data_base64=base64.b64encode(raw).decode())


@pytest.mark.parametrize('kind', ['png', 'pdf', 'jpeg'])
def test_ui_upload_reaches_ocr_and_session_fingerprint(app, monkeypatch, kind):
    raw = (png_bytes() if kind == 'png' else pdf_bytes() if kind == 'pdf' else
           (Path(__file__).resolve().parents[1] / 'evaluation/media_v1/ocr/o01.jpg').read_bytes())
    name = ('م' * 80 if kind == 'png' else 'مختار') + '.' + ('jpg' if kind == 'jpeg' else kind)
    status, uploaded = upload(app, name, raw)
    assert status == 200, uploaded
    assert uploaded['sha256'] == hashlib.sha256(raw).hexdigest()
    selected = post(app, 'files', project=app.test_project)[1]
    assert uploaded['path'] in [item['path'] for item in selected['files']]
    assert selected['limits'] == {'image_bytes': MAX_MEDIA_BYTES, 'pdf_bytes': MAX_PDF_BYTES, 'text_bytes': 65536}
    session = app.dispatch({'action': 'create_session', 'project': app.test_project, 'name': 'OCR', 'mode': 'agent'})['id']
    captured = []
    if kind == 'pdf':
        from multimodal.codec import find_pdf_renderer
        if find_pdf_renderer() is None:
            # The upload contract is independent of Poppler installation in CI.
            from tests.test_ocr_product_path import _fake_pdftoppm
            monkeypatch.setattr('multimodal.codec.find_pdf_renderer', lambda: 'fixture-pdftoppm')
            monkeypatch.setattr('multimodal.codec.subprocess.run', _fake_pdftoppm)
    monkeypatch.setattr('multimodal.ocr.perform_ocr', lambda doc, **kw: captured.append(doc) or 'نص مستخرج')
    def call_ocr(request):
        envelope = agent_workspace.decode_input(request.messages[-1].content)
        path = envelope['attachments'][0]['path']
        return response('', ToolCall('ocr-call', 'ocr_image', {'path': path}))
    app.test_provider.responses = [call_ocr, response('نص مستخرج')]
    turn = uuid.uuid4().hex
    status, result = post(app, 'agent_ask', project=app.test_project, session=session, turn=turn,
                          message='اقرأ المرفق', files=[uploaded['path']])
    assert status == 200 and result['status'] == 'complete', result
    assert len(captured) == 1
    attachment = result['inputs']['attachments'][0]
    assert attachment['sha256'] == uploaded['sha256'] and attachment['size_bytes'] == len(raw)
    workspace = app.project(app.test_project) / 'agent-workspace'
    assert (workspace / attachment['path']).read_bytes() == raw
    if kind != 'pdf':
        assert base64.b64decode(captured[0]['data_base64']) == raw
    else:
        assert captured[0]['mime'] == 'image/png'
    history = app.agent_session(app.project(app.test_project), session).history()['turns']
    assert agent_workspace.decode_input(history[0]['text'])['attachments'][0] == attachment


@pytest.mark.parametrize('name,raw,code', [
    pytest.param('big.png', b'x' * (MAX_MEDIA_BYTES + 1), 'attachment_too_large', id='image-size'),
    pytest.param('big.pdf', b'%PDF-' + b'x' * (MAX_PDF_BYTES - 4), 'attachment_too_large', id='pdf-size'),
    pytest.param('file.gif', png_bytes(), 'attachment_type_unsupported', id='extension'),
    pytest.param('file.pdf', png_bytes(), 'attachment_type_unsupported', id='pdf-signature'),
    pytest.param('file.jpg', png_bytes(), 'attachment_type_unsupported', id='image-mismatch'),
    pytest.param('file.png', b'not an image', 'media_type_unsupported', id='image-signature'),
])
def test_binary_upload_named_refusal(app, name, raw, code):
    status, error = upload(app, name, raw)
    assert status == 409 and error['error_code'] == code
    assert post(app, 'files', project=app.test_project)[1]['files'] == []


def test_base64_invalid_refuses(app):
    status, error = post(app, 'upload_binary', project=app.test_project, upload=uuid.uuid4().hex,
                         name='file.png', data_base64='%%%')
    assert status == 409 and error['error_code'] == 'attachment_encoding_invalid'


def test_changed_binary_receipt_excluded(app):
    _, doc = upload(app, 'page.png', png_bytes())
    (app.project(app.test_project) / 'uploads' / doc['path']).write_bytes(png_bytes(seed=339))
    status, catalog = post(app, 'files', project=app.test_project)
    assert status == 200 and catalog['files'] == [] and len(catalog['unavailable']) == 1


@pytest.mark.parametrize('kind', ['png', 'pdf'])
def test_binary_limit_is_inclusive_and_copy_idempotent(app, kind):
    raw = pdf_bytes(MAX_PDF_BYTES) if kind == 'pdf' else png_bytes()
    # A PNG cannot be padded without changing its valid structure; constrain the
    # configured byte ceiling to its exact valid size to test inclusive admission.
    if kind == 'png':
        from unittest.mock import patch
        with patch.object(agent_workspace, 'MAX_MEDIA_BYTES', len(raw)):
            status, doc = upload(app, 'page.png', raw)
    else:
        assert len(raw) == MAX_PDF_BYTES
        status, doc = upload(app, 'page.pdf', raw)
    assert status == 200, doc
    project = app.project(app.test_project)
    docs, blobs = agent_workspace.prepare_selected(project / 'uploads', session_id='s', turn_id='t',
                                                   files=[doc['path']], expected_digests={doc['path']: doc['sha256']})
    workspace = app.agent_workspace(project)
    agent_workspace.materialize_selected(workspace, blobs)
    agent_workspace.materialize_selected(workspace, blobs)
    assert (workspace / docs[0]['path']).read_bytes() == raw


def test_binary_materialization_rejects_symlink_and_traversal(app, tmp_path):
    workspace = app.agent_workspace(app.project(app.test_project))
    outside = tmp_path / 'outside'
    outside.mkdir()
    (workspace / 'inputs').symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspaceError) as exc:
        agent_workspace.materialize_selected(workspace, [('inputs/file.pdf', b'%PDF-1.4')])
    assert exc.value.code == 'unsafe_path'
    assert list(outside.iterdir()) == []
    with pytest.raises(WorkspaceError) as exc:
        agent_workspace.materialize_selected(workspace, [('../escape.pdf', b'%PDF-1.4')])
    assert exc.value.code == 'path_invalid'


def test_large_nonbinary_http_request_stays_bounded(app):
    status, error = post(app, 'upload', project=app.test_project, upload=uuid.uuid4().hex,
                         name='text.txt', content='x' * MAX_BODY, _binary_headers=['1'])
    assert status == 409 and error['error_code'] == 'body_limit'


@pytest.mark.parametrize('headers,code', [
    pytest.param([], 'body_limit', id='missing'),
    pytest.param(['yes'], 'http_refused', id='invalid'),
    pytest.param(['1', '1'], 'http_refused', id='duplicate'),
])
def test_binary_body_extension_requires_opt_in(app, headers, code):
    status, error = post(app, 'upload_binary', project=app.test_project, upload=uuid.uuid4().hex,
                         name='valid.pdf', data_base64=base64.b64encode(pdf_bytes()).decode(),
                         _binary_headers=headers)
    assert status == (409 if code == 'body_limit' else 403) and error['error_code'] == code
    assert not (app.project(app.test_project) / 'uploads').exists()


def test_binary_cloud_ingress_remains_disabled(tmp_path):
    from tests.test_cloud_workspace_scope import _create, _app
    scope = _create(tmp_path)
    app = _app(scope.root, synthetic_cloud=True)
    try:
        app.test_project = app.dispatch({'action': 'create_project', 'name': 'مصطنع'})['id']
        status, error = upload(app, 'page.png', png_bytes())
        assert status == 409 and error['error_code'] == 'cloud_file_ingress_disabled'
    finally:
        app.close()


def test_frontend_binary_upload_preserves_bytes():
    result = subprocess.run(['node', 'tests/webui_frontend.cjs', 'webui/static/app.js',
                             'binary_upload_preserves_bytes_and_uses_server_limits'],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('kind', ['png', 'pdf', 'jpeg'])
def test_live_http_upload_preserves_bytes_and_limits(app, kind):
    server = Server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    headers = {'Host': server.origin[7:], 'Origin': server.origin,
               'X-Diwan-CSRF': server.token, 'Content-Type': 'application/json'}

    def request(action, **values):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=20)
        try:
            body = json.dumps({'action': action, 'project': app.test_project, **values}).encode()
            selected_headers = {**headers, **({'X-Diwan-Binary-Upload': '1'} if action == 'upload_binary' else {})}
            connection.request('POST', '/api', body=body, headers=selected_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        raw = (png_bytes() if kind == 'png' else pdf_bytes(MAX_PDF_BYTES) if kind == 'pdf' else
               (Path(__file__).resolve().parents[1] / 'evaluation/media_v1/ocr/o01.jpg').read_bytes())
        name = 'مختار.' + ('jpg' if kind == 'jpeg' else kind)
        status, doc = request('upload_binary', upload=uuid.uuid4().hex, name=name,
                              data_base64=base64.b64encode(raw).decode())
        assert status == 200, doc
        assert doc['sha256'] == hashlib.sha256(raw).hexdigest() and doc['size_bytes'] == len(raw)
        assert (app.project(app.test_project) / 'uploads' / doc['path']).read_bytes() == raw
        status, catalog = request('files')
        assert status == 200 and doc['path'] in [item['path'] for item in catalog['files']]
        limit = MAX_PDF_BYTES if kind == 'pdf' else MAX_MEDIA_BYTES
        status, error = request('upload_binary', upload=uuid.uuid4().hex, name=name,
                                data_base64=base64.b64encode(b'x' * (limit + 1)).decode())
        assert status == 409 and error['error_code'] == 'attachment_too_large'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
