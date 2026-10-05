"""Real local HTTP, synthetic model/container, temporary project data only."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess, threading
import uuid

import pytest

from core.contracts import Response, ToolCall, Usage
from core import execution
from core.canonical import canonical_bytes
from memory.scope import HEADER_ALL, AllProjects
from services.agent_workspace import decode_input
from tests.test_webui_http import Running
from webui.server import (DEFAULT_PROJECT_ID, DEFAULT_SESSION_ID, UNIFIED_SESSION_ROLE,
                          LocalApp, Server)


def response(content="done", *calls):
    return Response(content, Usage(1, 1), "complete", 0, provider="fixture", model_version="a" * 64,
                    tool_calls=tuple(calls))


class Provider:
    name, is_local = "fixture", True
    def __init__(self):
        self.requests = []
        self.responses = []
    def estimate_micros(self, request):
        return 0
    def complete(self, request):
        self.requests.append(request)
        assert self.responses, "unexpected generation"
        result = self.responses.pop(0)
        return result(request) if callable(result) else result


class AgentRunning(Running):
    def __init__(self, root, *, receipt=None, enabled=True):
        self.provider = Provider()
        self.app = LocalApp(root, model="fixture", model_version="a" * 64,
            provider_factory=lambda: self.provider,
            agent_provider_factory=(lambda: self.provider) if enabled else None,
            runtime_receipt=receipt)
        self.server = Server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.app.close()
    def agent(self, project=None):
        project = project or self.api("create_project", name="مشروع")['id']
        session = self.api("create_session", project=project, name="أدوات", mode="agent")['id']
        return {"project": project, "session": session}


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "_BACKENDS", {})
    running = AgentRunning(tmp_path.resolve() / "ui")
    yield running
    running.close()


@pytest.fixture
def commands(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "_BACKENDS", {})
    receipt = tmp_path / "runtime.json"
    receipt.write_text(json.dumps({"schema_version": 1, "image_id": "sha256:" + "a" * 64,
        "lock_sha256": "b" * 64, "python_version": "3.12.1"}))
    receipt.chmod(0o600)
    def forbidden(*args, **kwargs):
        pytest.fail("no real Docker or host command")
    monkeypatch.setattr(execution.subprocess, "Popen", forbidden)
    running = AgentRunning(tmp_path.resolve() / "ui", receipt=receipt)
    yield running
    running.close()


def ask(live, ctx, *, message="اقرأ واكتب", files=(), turn=None):
    return live.api("agent_ask", **ctx, turn=turn or uuid.uuid4().hex, message=message, files=list(files))


def write_script(live, *, path="output.txt", content="أثر حقيقي"):
    live.provider.responses = [response("أكتب", ToolCall("write1", "write_file", {"path": path, "content": content})),
                               response("تمت الكتابة")]


def test_unified_page_keeps_setup_in_details_and_automates_the_general_chat():
    index = (Path(__file__).parents[1] / "webui/static/index.html").read_text()
    script = (Path(__file__).parents[1] / "webui/static/app.js").read_text()
    assert '<details id="details-panel"><summary>التفاصيل</summary><aside>' in index
    assert 'placeholder="اسأل ديوان…"' in index
    assert 'event.key === "Enter" && !event.shiftKey && !event.isComposing' in script
    assert 'api("default_workspace")' in script
    assert 'option.value === defaults.project.id' in script
    assert 'item.dataset.session === defaults.session.id' in script
    assert 'button(`مراجعة: ${toolName(action.name)}`, () => reviewAgentAction' in script
    assert 'button("موافقة", () => decideAgentAction' not in script
    result = subprocess.run(['node', 'tests/webui_frontend.cjs', 'webui/static/app.js'], cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=60); assert result.returncode == 0, result.stdout + result.stderr


def test_disabled_research_and_media_modes_explain_operator_enablement():
    root = Path(__file__).parents[1]
    index = (root / "webui/static/index.html").read_text()
    assert '<select id="session-mode" aria-describedby="session-mode-hints">' in index
    assert 'id="research-option" value="research" aria-describedby="research-mode-hint" disabled' in index
    assert 'id="media-option" value="media" aria-describedby="media-mode-hint" disabled' in index
    assert "البحث المعمّق غير مهيّأ" in index and "--web-search-url" in index and "SearXNG" in index
    assert "الوسائط غير مهيّأة" in index and "DIWAN_MEDIA_MODEL" in index and "DIWAN_MEDIA_DIGEST" in index
    result = subprocess.run(
        ["node", "tests/webui_frontend.cjs", "webui/static/app.js",
         "disabled_research_and_media_explain_and_track_operator_enablement"],
        cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_default_workspace_uses_stable_ids_not_duplicate_display_names(live):
    user_project = live.api("create_project", name="عام")
    user_session = live.api("create_session", project=user_project["id"],
                            name="محادثة عامة", mode="agent")["id"]
    first = live.api("default_workspace")
    assert first["project"] == {"id": DEFAULT_PROJECT_ID, "name": "عام"}
    assert first["project"]["id"] != user_project["id"]
    assert first["session"]["id"] == DEFAULT_SESSION_ID
    duplicate = live.api("create_session", project=DEFAULT_PROJECT_ID,
                         name="محادثة عامة", mode=first["session"]["mode"])
    second = live.api("default_workspace")
    assert second == first
    assert second["session"]["id"] != duplicate["id"]
    default_project = live.app.project(DEFAULT_PROJECT_ID)
    metadata = live.app.metadata(default_project / "sessions" / DEFAULT_SESSION_ID)
    assert metadata["system_role"] == UNIFIED_SESSION_ROLE
    assert live.app.is_unified_session(default_project, first["session"]["id"])
    assert not live.app.is_unified_session(live.app.project(user_project["id"]), user_session)
    assert "#166" in live.app.all_projects_memory.__doc__
    assert "#166" in live.app.is_unified_session.__doc__
    path = default_project / "sessions" / DEFAULT_SESSION_ID / "meta.json"
    path.write_bytes(canonical_bytes({key: value for key, value in metadata.items()
                                      if key != "system_role"}))
    recovered = live.api("default_workspace")
    assert recovered["session"]["system_role"] == UNIFIED_SESSION_ROLE
    assert live.app.is_unified_session(default_project, DEFAULT_SESSION_ID)
    status, error, _ = live.request({"action": "create_session", "project": user_project,
                                     "name": "مزورة", "mode": "agent",
                                     "system_role": UNIFIED_SESSION_ROLE})
    assert status == 409 and error["error_code"] == "request_invalid"


def test_http_reads_only_selected_upload_writes_real_file_and_replays_without_provider(live):
    ctx = live.agent()
    chosen = live.api("upload", project=ctx['project'], upload=uuid.uuid4().hex, name="مختار.txt", content="مختار")
    other = live.api("upload", project=ctx['project'], upload=uuid.uuid4().hex, name="آخر.txt", content="غير مختار")
    def read(request):
        data = decode_input(request.messages[-1].content)
        assert len(data["attachments"]) == 1
        doc = data["attachments"][0]
        assert doc["sha256"] == hashlib.sha256("مختار".encode()).hexdigest()
        return response("أقرأ", ToolCall("read1", "read_file", {"path": doc["path"]}))
    live.provider.responses = [read, response("أكتب", ToolCall("write1", "write_file", {
        "path": "output.txt", "content": "تمّت قراءة المختار"})), response("انتهيت")]
    turn = uuid.uuid4().hex
    result = ask(live, ctx, files=[chosen["path"]], turn=turn)
    assert result["status"] == "complete" and len(result["steps"]) == 3
    assert "مختار" in result["steps"][0]["tool_results"][0]["content"]
    action = result["steps"][1]["tool_results"][0]
    assert action["journal_action_id"].startswith("act-")
    assert live.api("agent_read", project=ctx['project'], path="output.txt")["content"] == "تمّت قراءة المختار"
    paths = live.api("agent_files", project=ctx['project'])["files"]
    assert len(paths) == 2 and not any(other["sha256"] == item["sha256"] for item in paths)
    source = live.app.project(ctx['project']) / "uploads" / chosen["path"]
    assert source.read_text() == "مختار"
    source.unlink()
    live.app.agent_provider_factory = lambda: pytest.fail("completed replay must not construct provider")
    assert ask(live, ctx, files=[chosen["path"]], turn=turn)["replayed"] is True
    assert live.api("agent_resume", **ctx, turn=turn)["replayed"] is True
    assert len(live.provider.requests) == 3
    history = live.api("history", **ctx, before=None)
    assert history["turns"][0]["steps"] == result["steps"]
    assert history["turns"][0]["pending"] == []


def test_http_pending_decision_is_bound_and_resume_uses_frozen_command_inputs(commands, monkeypatch):
    ctx = commands.agent()
    workspace = commands.app.project(ctx['project']) / "agent-workspace"
    (workspace / "program.py").write_text("approved")
    commands.provider.responses = [response("أنفذ", ToolCall("cmd1", "run_command", {"argv": ["python", "program.py"]})),
                                   response("انتهى")]
    pending = ask(commands, ctx)
    assert pending["status"] == "awaiting_owner" and len(commands.provider.requests) == 1
    view = pending["pending"][0]
    assert view["arguments"] == {"argv": ["python", "program.py"]}
    assert view["consent"] == "owner" and view["input_files"][0]["path"] == "program.py"
    assert "data" not in json.dumps(view) and len(view["input_snapshot_sha256"]) == 64
    status, error, _ = commands.request({"action": "agent_decide", **ctx, "action_id": view["action_id"],
        "call_digest": "0" * 64, "expected_revision": view["revision"], "approve": True})
    assert status == 409 and error["error_code"] == "action_binding_conflict"
    commands.api("agent_decide", **ctx, action_id=view["action_id"], call_digest=view["call_digest"],
                 expected_revision=view["revision"], approve=True)
    assert len(commands.provider.requests) == 1  # decision does not resume.
    backend = execution._BACKENDS[workspace]
    monkeypatch.setattr(backend, "_verify_image", lambda: None)
    payloads = []
    def run(driver, payload, *, timeout_s):
        payloads.append(payload)
        return execution.ExecutionResult(0, "fixture", "", "synthetic")
    monkeypatch.setattr(backend, "_run_container", run)
    (workspace / "program.py").write_text("owner edit")
    resumed = commands.api("agent_resume", **ctx, turn=pending["turn_id"])
    assert resumed["status"] == "complete"
    import base64
    assert base64.b64decode(payloads[-1]["files"][0]["data"]) == b"approved"
    commands.api("agent_resume", **ctx, turn=pending["turn_id"])
    assert len(payloads) == 2 and len(commands.provider.requests) == 2


def test_http_changed_turn_payload_is_refused_and_sessions_projects_are_bound(live):
    one, two = live.agent(), live.agent()
    sibling = live.agent(one['project'])
    write_script(live)
    first = ask(live, one)
    for other in (two, sibling):
        assert live.api("history", **other, before=None)["turns"] == []
    assert live.api("agent_files", project=two['project'])["files"] == []
    status, _, _ = live.request({"action": "agent_ask", **one, "turn": first['turn_id'],
                                "message": "حجج جديدة", "files": []})
    assert status == 409 and len(live.provider.requests) == 2
    action = first["steps"][0]["tool_results"][0]["action_id"]
    for other in (two, sibling):
        status, _, _ = live.request({"action": "agent_revert", **other, "action_id": action, "request": uuid.uuid4().hex})
        assert status == 409
    status, _, _ = live.request({"action": "history", "project": two['project'], "session": one['session'], "before": None})
    assert status == 409


def test_http_revert_keeps_owner_changes_and_safe_revert_is_replayed(live):
    ctx = live.agent()
    write_script(live)
    result = ask(live, ctx)
    action = result["steps"][0]["tool_results"][0]["action_id"]
    target = live.app.project(ctx['project']) / "agent-workspace/output.txt"
    target.write_text("تعديل المالك")
    refused = live.api("agent_revert", **ctx, action_id=action, request=uuid.uuid4().hex)
    assert refused["status"] == "refused" and target.read_text() == "تعديل المالك"
    second = live.agent()
    write_script(live)
    written = ask(live, second)
    second_action = written["steps"][0]["tool_results"][0]["action_id"]
    token = uuid.uuid4().hex
    reverted = live.api("agent_revert", **second, action_id=second_action, request=token)
    assert reverted["status"] in {"ok", "reverted"}
    assert not (live.app.project(second['project']) / "agent-workspace/output.txt").exists()
    assert live.api("agent_revert", **second, action_id=second_action, request=token) == reverted


def test_http_capabilities_are_explicit_and_text_mode_remains_available(live):
    ctx = live.agent()
    assert live.api("projects")["agent_enabled"] is True
    capabilities = live.api("agent_capabilities", project=ctx['project'])
    assert capabilities['execution_enabled'] is False and capabilities['execution_status'] == 'not_configured'
    assert {item['name'] for item in capabilities['tools']} == {'read_file', 'write_file', 'edit_file', 'export_document', 'list_files', 'search_files', 'propose_memory', 'ocr_image'}
    legacy = live.api("create_session", project=ctx['project'], name="نص", mode="text")
    assert legacy['mode'] == 'text'
    live.provider.responses = [response("نص قديم")]
    result = live.api("ask", project=ctx['project'], session=legacy['id'], turn=uuid.uuid4().hex, message="أجب", files=[])
    assert result['content'] == 'نص قديم'
    status, _, _ = live.request({'action': 'agent_ask', 'project': ctx['project'], 'session': legacy['id'],
                                'turn': uuid.uuid4().hex, 'message': 'أدوات', 'files': []})
    assert status == 409
    assert [tier['id'] for tier in live.api('sovereign_status')['available_tiers']] == ['local_edge']

    long_name = "م" * 80
    first = live.api("create_project", name=long_name)["id"]
    second = live.api("create_project", name=long_name)["id"]
    empty = live.api("create_project", name="بلا ذاكرة")["id"]
    one = live.api("memory_remember", project=first, text="حقيقة أولى")["item_id"]
    two = live.api("memory_remember", project=second, text="حقيقة ثانية")["item_id"]

    scope = live.app.all_projects_memory()
    assert isinstance(scope, AllProjects)
    expected = {f"{long_name[:45]} — {first}", f"{long_name[:45]} — {second}"}
    assert set(scope.labels) == expected
    assert all(len(project_label) == 80 for project_label in scope.labels)
    found = scope.retrieve("حقيقة", limit=5)
    assert {item["item_id"] for item in found} == {one, two}
    assert {item["project"] for item in found} == expected
    assert not (live.app.project(empty) / "memory").exists()

    source = live.api("create_project", name="المصدر")['id']
    item_id = live.api("memory_remember", project=source, text="الموعد الخميس")['item_id']
    defaults = live.api("default_workspace")
    live.provider.responses = [response("الخميس")]
    turn = uuid.uuid4().hex
    live.api("agent_ask", project=defaults["project"]["id"],
             session=defaults["session"]["id"], turn=turn, message="متى الموعد؟", files=[])
    assert live.provider.requests[-1].messages[-1].content.startswith(HEADER_ALL)
    assert "الموعد الخميس" in live.provider.requests[-1].messages[-1].content
    forgotten = live.api("memory_forget", project=source, item_id=item_id)
    assert forgotten["receipt"]["references"] == [f"agent:{DEFAULT_SESSION_ID}/{turn}"]

    unrelated = live.api("create_project", name="ذاكرة معطوبة")["id"]
    unrelated_root = live.app.project(unrelated)
    (unrelated_root / "memory").symlink_to(unrelated_root / "missing-memory")
    history = live.api("history", project=DEFAULT_PROJECT_ID,
                       session=DEFAULT_SESSION_ID, before=None)
    assert history["status"] == "idle" and history["total"] == 1
    assert history["turns"][0]["turn_id"] == turn
    status, error, _ = live.request({"action": "agent_stop", "project": DEFAULT_PROJECT_ID,
                                     "session": DEFAULT_SESSION_ID, "turn": uuid.uuid4().hex})
    assert status == 409 and error["error_code"] == "turn_unknown"

    unseen = live.api("memory_remember", project=source, text="معلومة مؤقتة")["item_id"]
    malformed = live.api("create_project", name="بعيد")["id"]
    (live.app.root / "projects" / malformed / "meta.json").write_text("{")
    forgotten = live.api("memory_forget", project=source, item_id=unseen)
    assert forgotten["receipt"]["references"] == []


@pytest.mark.parametrize('path', ['../uploads/x', '/etc/passwd', '.diwan-journal/journal.jsonl', 'alias.txt'])
def test_agent_read_refuses_paths_links_and_hidden_state(live, path):
    ctx = live.agent()
    root = live.app.project(ctx['project']) / 'agent-workspace'
    (root / 'alias.txt').symlink_to(live.app.root / 'app.lock')
    status, _, _ = live.request({'action': 'agent_read', 'project': ctx['project'], 'path': path})
    assert status == 409
    assert live.api('agent_files', project=ctx['project'])['files'] == []


def test_http_existing_origin_and_csrf_guards_apply_before_agent_effect(live):
    ctx = live.agent()
    write_script(live)
    request = {'action': 'agent_ask', **ctx, 'turn': uuid.uuid4().hex, 'message': 'اكتب', 'files': []}
    for key, value in [('Origin', 'https://outside.invalid'), ('X-Diwan-CSRF', 'wrong')]:
        headers = live.headers()
        headers[key] = value
        status, _, _ = live.request(request, headers=headers)
        assert status == 403
    assert live.provider.requests == []


def test_missing_agent_provider_cannot_create_agent_session(tmp_path):
    server = AgentRunning(tmp_path.resolve() / 'ui', enabled=False)
    try:
        project = server.api('create_project', name='مشروع')['id']
        assert server.api('projects')['agent_enabled'] is False
        status, error, _ = server.request({'action': 'create_session', 'project': project, 'name': 'أدوات', 'mode': 'agent'})
        assert status == 409 and error['error_code'] == 'agent_unavailable'
        item_id = server.api("memory_remember", project=project, text="رمز الملف ٧٣")["item_id"]
        defaults = server.api("default_workspace")
        assert defaults["session"]["mode"] == "text"
        server.provider.responses = [response("٧٣")]
        turn = uuid.uuid4().hex
        server.api("ask", project=defaults["project"]["id"], session=DEFAULT_SESSION_ID,
                   turn=turn, message="ما رمز الملف؟", files=[])
        request = server.provider.requests[-1]
        assert request.messages[-1].content.startswith(HEADER_ALL)
        assert "رمز الملف ٧٣" in request.messages[-1].content
        forgotten = server.api("memory_forget", project=project, item_id=item_id)
        assert forgotten["receipt"]["references"] == [f"text:{DEFAULT_SESSION_ID}/{turn}"]
    finally:
        server.close()


def test_agent_generation_lock_covers_duplicate_changed_and_other_session_requests(live):
    first, other = live.agent(), live.agent()
    entered, release = threading.Event(), threading.Event()
    def held(request):
        entered.set()
        assert release.wait(8)
        return response("تم")
    live.provider.responses = [held]
    payload = {'action': 'agent_ask', **first, 'turn': uuid.uuid4().hex, 'message': 'أجب', 'files': []}
    outputs = []
    thread = threading.Thread(target=lambda: outputs.append(live.request(payload)))
    thread.start()
    try:
        assert entered.wait(5)
        assert live.request(payload)[1]['status'] == 'running'
        assert live.api('history', **first, before=None)['status'] == 'running'
        assert live.request({**payload, 'message': 'تغيير'})[1]['error_code'] == 'turn_conflict'
        assert live.request({**payload, **other, 'turn': uuid.uuid4().hex})[1]['error_code'] == 'generation_busy'
    finally:
        release.set()
        thread.join(8)
    assert outputs[0][0] == 200 and len(live.provider.requests) == 1


def test_prepared_ui_tests_capture_file_created_by_prior_step(commands, monkeypatch):
    ctx = commands.agent()
    workspace = commands.app.project(ctx['project']) / 'agent-workspace'
    backend = execution._BACKENDS[workspace]
    payloads = []
    monkeypatch.setattr(backend, '_verify_image', lambda: None)
    def run(driver, payload, *, timeout_s):
        payloads.append(payload)
        return execution.ExecutionResult(0, '1 passed (synthetic)', '', 'synthetic')
    monkeypatch.setattr(backend, '_run_container', run)
    commands.provider.responses = [
        response('أكتب اختبارًا', ToolCall('write1', 'write_file', {
            'path': 'tests/test_generated.py', 'content': 'def test_generated(): assert 1 == 1\n'})),
        response('أفحصه', ToolCall('tests1', 'run_tests', {'paths': ['tests/test_generated.py']})),
        response('تم الفحص المصطنع')]
    result = ask(commands, ctx)
    assert result['status'] == 'complete'
    assert result['steps'][1]['tool_results'][0]['passed'] is True
    assert [item['path'] for item in payloads[-1]['files']] == ['tests/test_generated.py']
    assert commands.api('agent_capabilities', project=ctx['project'])['execution_status'] == 'configured'


def test_http_decision_schema_forbids_argument_replacement(commands):
    ctx = commands.agent()
    commands.provider.responses = [response('أمر', ToolCall('c1', 'run_command', {'argv': ['echo', 'ok']}))]
    result = ask(commands, ctx)
    pending = result['pending'][0]
    status, _, _ = commands.request({'action': 'agent_decide', **ctx, 'action_id': pending['action_id'],
        'call_digest': pending['call_digest'], 'expected_revision': pending['revision'], 'approve': True,
        'arguments': {'argv': ['changed']}})
    assert status == 409
    assert commands.api('history', **ctx, before=None)['turns'][0]['pending'][0]['state'] == 'prepared'


def test_rejected_new_turn_does_not_copy_inputs_while_approval_is_pending(commands):
    ctx = commands.agent()
    commands.provider.responses = [response('أمر', ToolCall('c1', 'run_command', {'argv': ['echo', 'ok']}))]
    ask(commands, ctx)
    upload = commands.api('upload', project=ctx['project'], upload=uuid.uuid4().hex,
                          name='new.txt', content='not selected for the pending turn')
    before = commands.api('agent_files', project=ctx['project'])
    status, error, _ = commands.request({'action': 'agent_ask', **ctx, 'turn': uuid.uuid4().hex,
                                        'message': 'جولة لاحقة', 'files': [upload['path']]})
    assert status == 409 and error['error_code'] == 'turn_unresolved'
    assert commands.api('agent_files', project=ctx['project']) == before
    assert len(commands.provider.requests) == 1


def test_context_rejection_does_not_copy_selected_inputs(live):
    ctx = live.agent()
    upload = live.api('upload', project=ctx['project'], upload=uuid.uuid4().hex,
                      name='chosen.txt', content='selected source remains intact')
    before = live.api('agent_files', project=ctx['project'])
    status, error, _ = live.request({'action': 'agent_ask', **ctx, 'turn': uuid.uuid4().hex,
                                    'message': 'س' * 24000, 'files': [upload['path']]})
    assert status == 409 and error['error_code'] == 'context_limit'
    assert live.api('agent_files', project=ctx['project']) == before
    assert live.api('history', **ctx, before=None)['turns'] == []
    assert live.provider.requests == []


def test_discovery_ignores_remote_or_empty_local_aliases(monkeypatch):
    import urllib.request
    from tools.serve_ui import _discover_ollama
    class Reply:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps({'models': [
                {'name': 'qwen3:14b', 'digest': 'a' * 64, 'size': 0},
                {'name': 'qwen2.5:3b', 'digest': 'b' * 64, 'size': 100, 'remote_model': 'remote'},
                {'name': 'llama3.1:8b', 'digest': 'c' * 64, 'size': 100},
            ]}).encode()
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == 'http://127.0.0.1:11434/api/tags'
            return Reply()
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Opener())
    assert _discover_ollama() == ('llama3.1:8b', 'c' * 64, None, None, None)


@pytest.mark.parametrize('provider', ['local', 'mlx'])
def test_serve_ui_bootstrap_tools_only_for_local_with_same_identity(monkeypatch, provider):
    import sys
    from types import SimpleNamespace
    import tools.serve_ui as cli
    captured, constructions = {}, []
    for key in ('DIWAN_MEDIA_MODEL', 'DIWAN_MEDIA_DIGEST'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('DIWAN_CHAT_MODEL', 'fixture')
    monkeypatch.setenv('DIWAN_CHAT_DIGEST', 'a' * 64)
    monkeypatch.setattr(sys, 'argv', ['serve_ui', '--provider', provider, '--port', '0'])
    monkeypatch.setattr(cli, 'LocalChatProvider', lambda m, d, **kw: constructions.append(('text', m, d, kw['base_url'])))
    monkeypatch.setattr(cli, 'LocalToolProvider', lambda m, d, **kw: constructions.append(('tools', m, d, kw['base_url'])))
    monkeypatch.setitem(sys.modules, 'providers.mlx_provider', SimpleNamespace(MLXProvider=lambda **kw: None))
    class App:
        def __init__(self, root, **kwargs):
            captured.update(kwargs)
        def close(self):
            pass
    class FakeServer:
        origin = 'http://127.0.0.1:0'
        def __init__(self, *args, listen):
            assert listen is None
        def serve_forever(self):
            raise KeyboardInterrupt()
        def server_close(self):
            pass
    monkeypatch.setattr(cli, 'LocalApp', App)
    monkeypatch.setattr(cli, 'Server', FakeServer)
    assert cli.main() == 0
    if provider == 'local':
        assert constructions == [('text', 'fixture', 'a' * 64, 'http://127.0.0.1:11434'),
                                 ('tools', 'fixture', 'a' * 64, 'http://127.0.0.1:11434')]
        assert captured['agent_provider_factory'] is not None
    else:
        assert captured['agent_provider_factory'] is None
    assert captured['runtime_receipt'] is None
