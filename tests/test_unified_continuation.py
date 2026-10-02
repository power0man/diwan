"""Fresh unified continuation: synthetic providers/data, no network or owner data."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import uuid

import pytest

from agent.registry import Tool, ToolRegistry
from conversation.session import ConversationError
from conversation.unified import successor_id
from core import filelock
from core.canonical import canonical_bytes
from core.contracts import ToolCall
from tests.test_agent_webui import Provider, response
from webui.server import (DEFAULT_PROJECT_ID, DEFAULT_SESSION_ID, UNIFIED_SESSION_ROLE,
                          LocalApp, UIError)
from workspace_tools.backup import BackupError, export_workspace, restore_workspace


def api(app, action, **fields):
    return app.dispatch({"action": action, **fields})


def ctx(app):
    result = api(app, "default_workspace")
    return {"project": result["project"]["id"], "session": result["session"]["id"]}


def ask(app, provider, context, *, mode="agent", answer="تم", message="اذكر الرمز"):
    provider.responses = [replace(response(answer), model_version=app.model_version)]
    return api(app, "agent_ask" if mode == "agent" else "ask", **context,
               turn=uuid.uuid4().hex, message=message, files=[])


def continuation(app, context):
    value = api(app, "continue_unified", **context)
    return {"project": context["project"], "session": value["id"]}


def open_app(root, provider, mode="agent", version="a" * 64):
    return LocalApp(root, model="fixture", model_version=version, provider_factory=lambda: provider,
                    agent_provider_factory=(lambda: provider) if mode == "agent" else None)


@pytest.mark.parametrize("mode", ["agent", "text"])
def test_model_rollover_preserves_scope_history_and_isolation_across_restart(tmp_path, mode):
    provider = Provider()
    root = tmp_path.resolve() / "ui"
    app = open_app(root, provider, mode)
    try:
        old = ctx(app)
        source = api(app, "create_project", name="مصدر")['id']
        api(app, "memory_remember", project=source, text="رمز المصدر ٨٣٩٢")
        assert ask(app, provider, old, mode=mode)["status"] == "complete"
        assert "٨٣٩٢" in str(provider.requests[-1].messages)
        before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("manifest.json")}
        history = api(app, "history", **old, before=None)
    finally:
        app.close()
    app = open_app(root, provider, mode, "b" * 64)
    try:
        assert ctx(app) == old
        with pytest.raises((UIError, ConversationError)) as exc:
            ask(app, provider, old, mode=mode)
        assert exc.value.code == "model_changed_new_session"
        calls = len(provider.requests)
        new = continuation(app, old)
        assert new["session"] == successor_id(old["session"])
        assert continuation(app, old) == new == ctx(app)
        assert len(provider.requests) == calls
        assert api(app, "history", **old, before=None) == history
        assert api(app, "history", **new, before=None)["turns"] == []
        assert all((root / p).read_bytes() == raw for p, raw in before.items())
        assert ask(app, provider, new, mode=mode)["status"] == "complete"
        assert "٨٣٩٢" in str(provider.requests[-1].messages)
        isolated = api(app, "create_session", project=old["project"], name="محصور", mode=mode)
        assert ask(app, provider, {**old, "session": isolated["id"]}, mode=mode)["status"] == "complete"
        assert "٨٣٩٢" not in str(provider.requests[-1].messages)
        assert not api(app, "history", project=old["project"], session=isolated["id"], before=None)["unified"]
    finally:
        app.close()
    app = open_app(root, provider, mode, "b" * 64)
    try:
        assert ctx(app) == new
        assert continuation(app, old) == new
        assert len(api(app, "sessions", project=new["project"])["sessions"]) == 3
    finally:
        app.close()


@pytest.mark.parametrize("change", ["profile", "contract"])
def test_tool_rollover_keeps_historical_contracts_inert_and_forgettable(tmp_path, monkeypatch, change):
    import webui.server as server

    provider = Provider()
    app = open_app(tmp_path.resolve() / "ui", provider)
    try:
        old = ctx(app)
        source = api(app, "create_project", name="مصدر")['id']
        item = api(app, "memory_remember", project=source, text="رمز الأدوات ٨٨١")['item_id']
        ask(app, provider, old, answer="رمز الأدوات ٨٨١")
        before = api(app, "history", **old, before=None)
        manifests = {p: p.read_bytes() for p in app.root.rglob("manifest.json")}
        if change == "profile":
            # Enable a genuinely additional current tool without a service call.
            app.web_search = "http://synthetic.invalid"
        else:
            tools = list(server.DEFAULT_TOOLS)
            tools[0] = Tool(replace(tools[0].spec, description=tools[0].spec.description + " v2"),
                            lambda *a, **kw: pytest.fail("historical tool executed"))
            monkeypatch.setattr(server, "DEFAULT_TOOLS", tuple(tools))
        calls = len(provider.requests)
        assert api(app, "history", **old, before=None) == before
        with pytest.raises(UIError) as exc:
            ask(app, provider, old)
        assert exc.value.code == ("agent_capabilities_changed_new_session" if change == "profile"
                                 else "agent_tool_contract_changed")
        new = continuation(app, old)
        assert len(provider.requests) == calls
        assert ask(app, provider, new)["status"] == "complete"
        assert "٨٨١" in str(provider.requests[-1].messages)
        api(app, "memory_forget", project=source, item_id=item)
        assert all(p.read_bytes() == raw for p, raw in manifests.items())
        assert "٨٨١" not in str(api(app, "history", **old, before=None))
        assert ask(app, provider, new)["status"] == "complete"
        assert "٨٨١" not in str(provider.requests[-1].messages)
    finally:
        app.close()


@pytest.mark.parametrize("capacity", ["turn_limit", "context_limit"])
def test_capacity_exhaustion_has_fresh_explicit_continuation(tmp_path, monkeypatch, capacity):
    from conversation.agent_session import AgentSession

    provider = Provider()
    app = open_app(tmp_path.resolve() / "ui", provider)
    original = AgentSession.__init__
    def small(self, *args, **kwargs):
        if "max_turns" not in kwargs:
            kwargs["max_turns"] = 1 if capacity == "turn_limit" else 128
            kwargs["max_context_chars"] = 6000 if capacity == "context_limit" else 24000
        original(self, *args, **kwargs)
    monkeypatch.setattr(AgentSession, "__init__", small)
    try:
        old = ctx(app)
        assert ask(app, provider, old, answer="س" * 5000)["status"] == "complete"
        with pytest.raises(ConversationError) as exc:
            ask(app, provider, old, message="م" * 1000)
        assert exc.value.code == capacity
        new = continuation(app, old)
        assert ask(app, provider, new, message="مرحبا")["status"] == "complete"
        assert len(api(app, "history", **old, before=None)["turns"]) == 1
    finally:
        app.close()


@pytest.mark.parametrize("field", ["system_role", "continuation_of", "id", "mode"])
def test_http_cannot_select_unified_role_or_successor_identity(tmp_path, field):
    provider = Provider()
    app = open_app(tmp_path.resolve() / "ui", provider)
    try:
        old = ctx(app)
        for request in ({"action": "continue_unified", **old, field: UNIFIED_SESSION_ROLE},
                        {"action": "create_session", "project": old["project"], "name": "مزور",
                         "mode": "agent", field: UNIFIED_SESSION_ROLE}):
            with pytest.raises(UIError):
                app.dispatch(request)
        plain = api(app, "create_session", project=old["project"], name="محصور", mode="agent")
        with pytest.raises(UIError) as exc:
            continuation(app, {**old, "session": plain["id"]})
        assert exc.value.code == "unified_session_required"
        assert provider.requests == []
    finally:
        app.close()


@pytest.mark.parametrize("tamper", ["unknown-role", "random-id", "orphan", "isolated-parent", "extra", "wrong-project"])
def test_metadata_and_backup_refuse_invalid_unified_lineages(tmp_path, tamper):
    from conversation.unified import valid_lineage
    from workspace_tools.backup import _metadata

    provider = Provider()
    app = open_app(tmp_path.resolve() / "ui", provider)
    try:
        old = ctx(app)
        new = continuation(app, old)
        target = app.project(old["project"]) / "sessions" / new["session"] / "meta.json"
        value = json.loads(target.read_bytes())
        project = old["project"]
        if tamper == "unknown-role": value["system_role"] = "unknown"
        elif tamper == "random-id": value["id"] = "0" * 32
        elif tamper == "orphan": value["continuation_of"] = "0" * 32
        elif tamper == "extra": value["scope"] = "all"
        elif tamper == "wrong-project": project = "0" * 32
        elif tamper == "isolated-parent":
            parent = target.parent.parent / DEFAULT_SESSION_ID / "meta.json"
            pvalue = json.loads(parent.read_bytes()); pvalue.pop("system_role")
            parent.write_bytes(canonical_bytes(pvalue))
            assert not valid_lineage(value, {pvalue['id']: pvalue})
        if tamper == "wrong-project":
            with pytest.raises(BackupError):
                _metadata(canonical_bytes(value), new["session"], session=True, project=project)
        else:
            target.write_bytes(canonical_bytes(value))
            with pytest.raises((UIError, FileNotFoundError)):
                app.metadata(target.parent)
        filelock.unlock(app.lease)
        try:
            if tamper != "wrong-project":
                with pytest.raises(BackupError):
                    export_workspace(app.root, tmp_path.resolve() / "bad.json")
        finally:
            filelock.lock(app.lease, blocking=False)
    finally:
        app.close()


@pytest.mark.parametrize("mode", ["agent", "text"])
@pytest.mark.parametrize("phase", ["before-init", "before-publish", "after-publish"])
def test_interrupted_continuation_retries_same_successor_after_restart(tmp_path, monkeypatch, phase, mode):
    import webui.server as server

    provider = Provider()
    root = tmp_path.resolve() / "ui"
    app = open_app(root, provider, mode)
    old = ctx(app)
    sid = successor_id(old["session"])
    original_init, original_rename = app.agent_session, server.os.rename
    original_chat = server.ChatSession
    def chat_initialize(root, session_id, **kwargs):
        if phase == "before-init" and session_id == sid:
            raise OSError("synthetic text initialization interruption")
        return original_chat(root, session_id, **kwargs)
    def initialize(project, session_id, **kwargs):
        if phase == "before-init" and kwargs.get("create") and session_id == sid:
            raise OSError("synthetic interruption before initialization")
        return original_init(project, session_id, **kwargs)
    def rename(src, dst, **kwargs):
        if src == sid and phase == "before-publish": raise OSError("synthetic prepublish interruption")
        result = original_rename(src, dst, **kwargs)
        if src == sid and phase == "after-publish": raise OSError("synthetic lost acknowledgement")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(app, "agent_session", initialize)
        patch.setattr(server, "ChatSession", chat_initialize)
        patch.setattr(server.os, "rename", rename)
        with pytest.raises(OSError):
            continuation(app, old)
    app.close()
    app = open_app(root, provider, mode, "b" * 64)
    try:
        new = continuation(app, old)
        expected = sid if phase == "before-init" else successor_id(sid)
        assert new["session"] == expected and continuation(app, old) == new == ctx(app)
        assert len(api(app, "sessions", project=old["project"])["sessions"]) == (2 if phase == "before-init" else 3)
        assert list((root / "staging").iterdir()) == []
        assert provider.requests == []
        assert ask(app, provider, new, mode=mode)["status"] == "complete"
        assert provider.requests[-1].model_version == "b" * 64
        if phase != "before-init":
            frozen = (app.agent_session(app.project(old["project"]), sid, historical=True)
                      if mode == "agent" else app.session(app.project(old["project"]), sid))
            assert frozen.config["model_version"] == "a" * 64
        assert continuation(app, old) == new
        assert len(provider.requests) == 1
    finally:
        app.close()


@pytest.mark.parametrize("mode", ["agent", "text"])
def test_forget_covers_all_descendants_and_restores_old_backup_with_latest_authority(tmp_path, mode):
    provider = Provider()
    root = tmp_path.resolve() / "ui"
    app = open_app(root, provider, mode)
    try:
        source = api(app, "create_project", name="مصدر")['id']
        secret = "ذاكرة سلسلة مصطنعة ٧٩١١"
        item = api(app, "memory_remember", project=source, text=secret)['item_id']
        keep = api(app, "memory_remember", project=source, text="ذاكرة أخرى باقية")['item_id']
        contexts = [ctx(app)]
        for _ in range(3):
            assert ask(app, provider, contexts[-1], mode=mode, answer=secret)["status"] == "complete"
            contexts.append(continuation(app, contexts[-1]))
        archive = tmp_path.resolve() / "before.json"
        filelock.unlock(app.lease)
        try: receipt = export_workspace(root, archive)
        finally: filelock.lock(app.lease, blocking=False)
        forgotten = api(app, "memory_forget", project=source, item_id=item)["receipt"]
        assert set(forgotten["scrubbed"]) == {f"{mode}:{c['session']}" for c in contexts[:-1]}
        assert len(forgotten["references"]) == 3
        for c in contexts:
            assert secret not in str(api(app, "history", **c, before=None))
        destination = tmp_path.resolve() / "restored"
        restore_workspace(archive, destination, receipt['sha256'], tombstones_from=root)
        restored = open_app(destination, provider, mode)
        try:
            assert ctx(restored) == contexts[-1]
            for c in contexts:
                assert secret not in str(api(restored, "history", **c, before=None))
            assert [i['item_id'] for i in api(restored, "memory", project=source)['items']] == [keep]
            ask(restored, provider, contexts[-1], mode=mode)
            assert secret not in str(provider.requests[-1].messages)
            assert "ذاكرة أخرى باقية" in str(provider.requests[-1].messages)
        finally: restored.close()
    finally: app.close()


def test_pending_action_blocks_continuation_without_execution_or_receipt_change(tmp_path):
    provider = Provider()
    app = open_app(tmp_path.resolve() / "ui", provider)
    try:
        old = ctx(app)
        app.generation.acquire()
        try:
            with pytest.raises(UIError) as busy:
                continuation(app, old)
            assert busy.value.code == "generation_busy"
        finally:
            app.generation.release()
        provider.responses = [response("اقتراح", ToolCall("remember", "propose_memory", {"text": "مصطنع"}))]
        pending = api(app, "agent_ask", **old, turn=uuid.uuid4().hex, message="تذكر", files=[])
        assert pending["status"] == "awaiting_owner"
        before = {p: p.read_bytes() for p in app.root.rglob("*.json")}
        with pytest.raises(UIError) as exc: continuation(app, old)
        assert exc.value.code == "turn_unresolved"
        assert len(provider.requests) == 1
        assert all(p.read_bytes() == raw for p, raw in before.items())
        assert len(api(app, "sessions", project=old["project"])["sessions"]) == 1
    finally: app.close()


def test_historical_manifest_tampering_is_refused_before_read_or_continuation(tmp_path):
    provider = Provider()
    app = open_app(tmp_path.resolve() / "ui", provider)
    try:
        old = ctx(app)
        ask(app, provider, old)
        manifest = app.project(old["project"]) / "agent-control" / old["session"] / "manifest.json"
        value = json.loads(manifest.read_bytes())
        value['tools'][0]['description'] += " tampered"
        manifest.write_bytes(canonical_bytes(value))
        for action, fields in (("history", {"before": None}), ("continue_unified", {})):
            with pytest.raises(ConversationError): api(app, action, **old, **fields)
        assert len(provider.requests) == 1
    finally: app.close()


def test_unified_continuation_dom_contract():
    root = Path(__file__).parents[1]
    result = subprocess.run(['node', 'tests/webui_frontend.cjs', 'webui/static/app.js',
                             'unified_continuation_is_explicit_idempotent_and_navigation_safe'],
                            cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_concurrent_retry_does_not_replay_completed_actions_or_change_receipts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    provider = Provider()
    app = open_app(tmp_path.resolve() / "ui", provider)
    try:
        old = ctx(app)
        provider.responses = [response("كتابة", ToolCall("write", "write_file", {"path":"once.txt", "content":"مرة"})), response("تم")]
        out = api(app, "agent_ask", **old, turn=uuid.uuid4().hex, message="اكتب مرة", files=[])
        assert out["status"] == "complete"
        before = {p: p.read_bytes() for p in app.root.rglob("*.json")}
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: continuation(app, old), range(8)))
        assert all(value == results[0] for value in results)
        assert all(p.read_bytes() == raw for p, raw in before.items())
        assert len(provider.requests) == 2
        assert len(api(app, "sessions", project=old["project"])["sessions"]) == 2
        assert (app.project(old["project"]) / "agent-workspace/once.txt").read_text() == "مرة"
    finally: app.close()


@pytest.mark.parametrize("mode", ["agent", "text"])
def test_descendant_forget_journal_rolls_forward_after_process_restart(tmp_path, monkeypatch, mode):
    import webui.server as server

    provider = Provider()
    root = tmp_path.resolve() / "ui"
    app = open_app(root, provider, mode)
    source = api(app, "create_project", name="مصدر")['id']
    secret = "رمز نسيان منقطع ٩٨٨"
    item = api(app, "memory_remember", project=source, text=secret)['item_id']
    old = ctx(app)
    ask(app, provider, old, mode=mode, answer=secret)
    new = continuation(app, old)
    ask(app, provider, new, mode=mode, answer=secret)
    original = server._replace_private
    def interrupted(path, payload):
        if new['session'] in path.parts and path.name == 'state.json':
            raise OSError("synthetic process interruption at successor")
        return original(path, payload)
    with monkeypatch.context() as patch:
        patch.setattr(server, '_replace_private', interrupted)
        with pytest.raises(UIError) as exc: api(app, "memory_forget", project=source, item_id=item)
        assert exc.value.code == "memory_forget_incomplete"
        assert (app.project(source) / server.MEMORY_FORGET_TRANSACTION).exists()
    app.close()
    app = open_app(root, provider, mode)
    try:
        assert not (app.project(source) / server.MEMORY_FORGET_TRANSACTION).exists()
        assert api(app, 'memory', project=source)['items'] == []
        for context in [old,new]:
            assert secret not in str(api(app,'history',**context,before=None))
        ask(app, provider, new, mode=mode)
        assert secret not in str(provider.requests[-1].messages)
    finally: app.close()


@pytest.mark.parametrize('corrupt', ['staged-role', 'partial-core-init'])
def test_unpublished_corrupt_initialization_is_preserved_and_never_promoted(tmp_path, monkeypatch, corrupt):
    import webui.server as server
    from conversation.agent_session import AgentSession

    provider=Provider();root=tmp_path.resolve()/'ui';app=open_app(root,provider)
    old=ctx(app); sid=successor_id(old['session'])
    original=AgentSession._write
    def interrupt(self,name,value):
        original(self,name,value)
        if self.session_id==sid and name=='manifest.json':
            raise OSError('synthetic interruption inside core initialization')
    with monkeypatch.context() as patch:
        if corrupt=='partial-core-init': patch.setattr(AgentSession,'_write',interrupt)
        else:
            rename=server.os.rename
            def stop(src,dst,**kw):
                if src==sid: raise OSError('synthetic prepublish interruption')
                return rename(src,dst,**kw)
            patch.setattr(server.os,'rename',stop)
        with pytest.raises((OSError, ConversationError)): continuation(app,old)
    if corrupt=='staged-role':
        staged=root/'staging'/sid/'meta.json';value=json.loads(staged.read_bytes())
        value['system_role']='unknown';staged.write_bytes(canonical_bytes(value))
    before={p:p.read_bytes() for p in root.rglob('*.json')}
    app.close();app=open_app(root,provider)
    try:
        with pytest.raises((UIError,ConversationError)): continuation(app,old)
        assert not (root/'projects'/old['project']/'sessions'/sid).exists()
        assert all(p.read_bytes()==raw for p,raw in before.items())
        assert ctx(app)==old and provider.requests==[]
    finally: app.close()


def test_lineage_is_bounded_and_cannot_reference_isolated_or_malformed_records():
    from conversation.unified import valid_lineage, valid_role

    def record(sid,previous=None):
        return {'id':sid,'name':'مصطنع','mode':'agent','system_role':UNIFIED_SESSION_ROLE,
                **({'continuation_of':previous} if previous else {})}
    value=record(DEFAULT_SESSION_ID);records={value['id']:value}
    for _ in range(63):
        value=record(successor_id(value['id']),value['id']);records[value['id']]=value
    assert valid_lineage(value,records)
    next_value=record(successor_id(value['id']),value['id'])
    assert not valid_lineage(next_value,records)
    assert not valid_role(record(DEFAULT_SESSION_ID,DEFAULT_SESSION_ID),DEFAULT_PROJECT_ID)
    assert not valid_role([],DEFAULT_PROJECT_ID)
    broken=record(successor_id(DEFAULT_SESSION_ID),DEFAULT_SESSION_ID)
    for malformed in ([],{}, {'id':DEFAULT_SESSION_ID,'name':'isolated','mode':'agent'}):
        assert not valid_lineage(broken,{DEFAULT_SESSION_ID:malformed})


@pytest.mark.parametrize('mode', ['agent','text'])
def test_restore_backup_from_before_continuation_uses_newer_forget_authority(tmp_path,mode):
    provider=Provider();root=tmp_path.resolve()/'ui';app=open_app(root,provider,mode)
    try:
        old=ctx(app);source=api(app,'create_project',name='مصدر')['id'];secret='رمز سلف قديم ١٩٩'
        item=api(app,'memory_remember',project=source,text=secret)['item_id']
        ask(app,provider,old,mode=mode,answer=secret)
        archive=tmp_path.resolve()/'old.json'
        filelock.unlock(app.lease)
        try: receipt=export_workspace(root,archive)
        finally: filelock.lock(app.lease,blocking=False)
        new=continuation(app,old);ask(app,provider,new,mode=mode,answer=secret)
        api(app,'memory_forget',project=source,item_id=item)
        target=tmp_path.resolve()/'restored';restore_workspace(archive,target,receipt['sha256'],tombstones_from=root)
        restored=open_app(target,provider,mode)
        try:
            assert ctx(restored)==old
            assert secret not in str(api(restored,'history',**old,before=None))
            again=continuation(restored,old);assert again==new
            ask(restored,provider,again,mode=mode)
            assert secret not in str(provider.requests[-1].messages)
        finally: restored.close()
    finally:app.close()


@pytest.mark.parametrize('phase', ['before-publish','after-publish'])
@pytest.mark.parametrize('change', ['profile','contract'])
def test_tool_change_during_interruption_returns_a_usable_current_successor(tmp_path,monkeypatch,phase,change):
    import webui.server as server

    provider=Provider();root=tmp_path.resolve()/'ui';app=open_app(root,provider)
    source=api(app,'create_project',name='مصدر')['id']
    api(app,'memory_remember',project=source,text='رمز الاستعادة ٤٧٢')
    old=ctx(app);ask(app,provider,old)
    sid=successor_id(old['session']);rename=server.os.rename
    def interrupt(src,dst,**kw):
        if src==sid and phase=='before-publish':raise OSError('synthetic prepublication interruption')
        result=rename(src,dst,**kw)
        if src==sid and phase=='after-publish':raise OSError('synthetic lost response')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(server.os,'rename',interrupt)
        with pytest.raises(OSError):continuation(app,old)
    frozen=(root/'projects'/old['project']/'agent-control'/sid/'manifest.json').read_bytes()
    app.close();app=open_app(root,provider)
    try:
        if change=='profile':app.web_search='http://synthetic.invalid'
        else:
            tools=list(server.DEFAULT_TOOLS)
            tools[0]=Tool(replace(tools[0].spec,description=tools[0].spec.description+' changed'),
                          lambda *a,**kw:pytest.fail('historical tool executed'))
            monkeypatch.setattr(server,'DEFAULT_TOOLS',tuple(tools))
        calls=len(provider.requests);new=continuation(app,old)
        assert new['session']==successor_id(sid) and new==ctx(app)==continuation(app,old)
        assert len(provider.requests)==calls
        assert (root/'projects'/old['project']/'agent-control'/sid/'manifest.json').read_bytes()==frozen
        assert len(api(app,'sessions',project=old['project'])['sessions'])==3
        assert api(app,'history',project=old['project'],session=sid,before=None)['turns']==[]
        assert ask(app,provider,new)['status']=='complete'
        assert '٤٧٢' in str(provider.requests[-1].messages)
        assert continuation(app,old)==new and len(provider.requests)==calls+1
    finally:app.close()


def test_stale_intervening_successor_with_unresolved_action_is_never_skipped(tmp_path):
    provider=Provider();app=open_app(tmp_path.resolve()/'ui',provider)
    try:
        old=ctx(app);middle=continuation(app,old)
        provider.responses=[response('اقتراح',ToolCall('remember','propose_memory',{'text':'مصطنع'}))]
        result=api(app,'agent_ask',**middle,turn=uuid.uuid4().hex,message='تذكر',files=[])
        assert result['status']=='awaiting_owner'
        app.model_version='b'*64
        before={p:p.read_bytes() for p in app.root.rglob('*.json')}
        with pytest.raises(UIError) as exc:continuation(app,old)
        assert exc.value.code=='turn_unresolved'
        assert len(api(app,'sessions',project=old['project'])['sessions'])==2
        assert len(provider.requests)==1 and all(p.read_bytes()==raw for p,raw in before.items())
    finally:app.close()


def test_retry_from_older_ancestor_reaches_current_leaf_after_settings_are_reverted(tmp_path):
    provider=Provider();app=open_app(tmp_path.resolve()/'ui',provider)
    try:
        old=ctx(app);version_a=continuation(app,old)
        app.model_version='b'*64
        version_b=continuation(app,old)
        assert version_b['session']==successor_id(version_a['session'])
        app.model_version='a'*64
        returned=continuation(app,old)
        assert returned['session']==successor_id(version_b['session'])
        assert returned==ctx(app)==continuation(app,old)
        assert len(api(app,'sessions',project=old['project'])['sessions'])==4
        assert provider.requests==[]
        assert ask(app,provider,returned)['status']=='complete'
    finally:app.close()
