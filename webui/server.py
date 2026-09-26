"""HTTP محلي محدود؛ المتصفح لا يختار مسارًا في المضيف ولا ينفذ جوابًا."""
from __future__ import annotations

from contextlib import contextmanager
import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import threading
import time
import unicodedata
import uuid

from conversation import ChatSession
from conversation.agent_session import AgentSession
from conversation.session import ConversationError
from agent.builtin_tools import DEFAULT_TOOLS
from agent.registry import ToolRegistry
from agent.web_search import web_search_tool
from analysis.backend import configure_analysis_backend
from analysis.tool import ANALYZE_DATA
from core import filelock
from core.execution import configure_execution_backend
from core.canonical import canonical_bytes, digest
from core.contracts import Message as ContractMessage, Request as ContractRequest
from core.router_sovereign import PIISanitizer, SovereignRouter, SovereignRoutingError
from core.tools_registry import default_tools_registry
from memory.store import MemoryRefused, MemoryStore
from memory.tool import propose_memory_tool
from multimodal.codec import (MEDIA_PREFIX, MEDIA_SYSTEM, MEDIA_CONTEXT_CHARS,
                              pack_media, decode_request)
from services.media_assistant import MediaAssistant, present as present_media
from services.assistant_workspace import AssistantWorkspace, _present, _decode_context
from services import agent_workspace
from workspace_tools.files import (TextWorkspace, _open_directory, _private,
                                   _read_json, _write_json, _relative)
from workspace_tools.preferences import Preferences
from workspace_tools.backup import restore_pending

STATIC = Path(__file__).parent / "static"
IDENTIFIER = re.compile(r"[a-f0-9]{32}\Z")
MAX_BODY = 512 * 1024


class UIError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def need(condition, code="request_invalid"):
    if not condition:
        raise UIError(code)


def identifier(value):
    need(isinstance(value, str) and IDENTIFIER.fullmatch(value), "id_invalid")
    return value


def label(value):
    need(isinstance(value, str) and 1 <= len(value.strip()) <= 80, "name_invalid")
    need(not any(unicodedata.category(c).startswith("C") for c in value), "name_invalid")
    return value.strip()


def decode(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, "json_invalid")
            out[key] = value
        return out
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
        canonical_bytes(value)
        need(type(value) is dict, "json_invalid")
        return value
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise UIError("json_invalid") from None


class LocalApp:
    """مخزن خاص بعملية واحدة؛ معرفات المشروعات والجلسات مولدة داخليًا.

    ملفات الحالات ليست مشفرة. حساب المضيف المالك ومزوده المحلي موثوقان؛
    ليست الواجهة عزلًا عن مهاجم يملك الحساب ويبدل ملفات المحادثة أثناء عملها.
    """
    def __init__(self, root, *, model, model_version, provider_factory,
                 media_model=None, media_model_version=None, media_provider_factory=None,
                 agent_provider_factory=None, runtime_receipt=None, web_search=None,
                 analysis_receipt=None, docker_executable="/usr/local/bin/docker"):
        self.root = Path(root).absolute()
        self.model, self.model_version = model, model_version
        self.provider_factory = provider_factory
        self.agent_provider_factory = agent_provider_factory
        self.agent_enabled = agent_provider_factory is not None
        self.default_session_mode = "agent" if self.agent_enabled else "text"
        self.runtime_receipt = None if runtime_receipt is None else Path(runtime_receipt).absolute()
        # محرّكُ البحث يضبطه الإقلاعُ الموثوق وحده (ج٢)؛ وبدونه لا تُعلَن الأداة
        self.web_search = web_search
        # صورةُ المحلّل يضبطها الإقلاعُ الموثوق وحده (ج٨)؛ وبدونها لا تُعلَن analyze_data
        self.analysis_receipt = None if analysis_receipt is None else Path(analysis_receipt).absolute()
        self.docker_executable = docker_executable
        self.agent_backends = {}
        self.media_model, self.media_model_version = media_model, media_model_version
        self.media_provider_factory = media_provider_factory
        self.media_enabled = all((media_model, media_model_version, media_provider_factory))
        self.lock = threading.RLock()
        self.generation = threading.Lock()
        self.active = None
        self.active_payload = None
        self.active_agent_session = None
        need(not restore_pending(self.root), "restore_incomplete")
        self.fd = _open_directory(self.root, create=True)
        try:
            _private(os.fstat(self.fd), directory=True)
            need(not restore_pending(self.root) and ".restore-incomplete" not in os.listdir(self.fd),
                 "restore_incomplete")
            self.identity = (os.fstat(self.fd).st_dev, os.fstat(self.fd).st_ino)
            self.lease = os.open("app.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                                 0o600, dir_fd=self.fd)
            _private(os.fstat(self.lease))
            filelock.lock(self.lease, blocking=False)
        except BaseException:
            if hasattr(self, "lease"):
                os.close(self.lease)
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.lease)
        os.close(self.fd)

    def check_root(self):
        fd = _open_directory(self.root)
        try:
            info = os.fstat(fd)
            _private(info, directory=True)
            need((info.st_dev, info.st_ino) == self.identity, "store_changed")
        finally:
            os.close(fd)

    @contextmanager
    def directory(self, path, *, create=False):
        self.check_root()
        fd = _open_directory(path, create=create)
        try:
            _private(os.fstat(fd), directory=True)
            yield fd
        finally:
            os.close(fd)

    def metadata(self, path):
        with self.directory(path) as fd:
            value = _read_json(fd, "meta.json")
        need(type(value) is dict and set(value) in ({"id", "name"}, {"id", "name", "mode"}), "metadata_invalid")
        need(value.get("mode", "text") in ("text", "media", "agent"), "metadata_invalid")
        need(identifier(value["id"]) == path.name, "metadata_invalid")
        label(value["name"])
        return value

    def collection(self, root):
        with self.directory(root, create=True) as fd:
            names = os.listdir(fd)
        need(len(names) <= 64, "collection_limit")
        return sorted([self.metadata(root / identifier(name)) for name in names],
                      key=lambda item: (item["name"], item["id"]))

    def create(self, root, name, *, chat=False, mode="text"):
        name = label(name)
        with self.lock:
            need(len(self.collection(root)) < 64, "collection_limit")
            value = {"id": uuid.uuid4().hex, "name": name}
            if chat:
                need(mode in ("text", "media", "agent"), "session_mode_invalid")
                need(mode != "media" or self.media_enabled, "media_unavailable")
                need(mode != "agent" or self.agent_enabled, "agent_unavailable")
                value["mode"] = mode
            staging = self.root / "staging"
            with self.directory(staging, create=True) as fd:
                need(len(os.listdir(fd)) < 64, "staging_limit")
                os.mkdir(value["id"], 0o700, dir_fd=fd)
                os.fsync(fd)
            staged = staging / value["id"]
            with self.directory(staged) as fd:
                _write_json(fd, "meta.json", value)
            if chat:
                if mode == "agent":
                    self.agent_session(root.parent, value["id"], create=True)
                elif mode == "media":
                    MediaAssistant.open(staged / "chat", value["id"], model=self.media_model,
                        model_version=self.media_model_version, provider=None)
                else:
                    ChatSession(staged / "chat", value["id"], model=self.model,
                                model_version=self.model_version)
            with self.directory(staging) as source, self.directory(root) as target:
                need(value["id"] not in os.listdir(target), "id_conflict")
                os.rename(value["id"], value["id"], src_dir_fd=source, dst_dir_fd=target)
                os.fsync(target)
                os.fsync(source)
            return value

    def project(self, value):
        path = self.root / "projects" / identifier(value)
        self.metadata(path)
        return path

    def session(self, project, value):
        path = project / "sessions" / identifier(value)
        metadata = self.metadata(path)
        manifest = path / "chat" / value / "manifest.json"
        # Published sessions were initialized before atomic publication. Absence is corruption.
        with self.directory(manifest.parent) as fd:
            saved = _read_json(fd, "manifest.json")
        need(type(saved) is dict, "metadata_invalid")
        config = {key: saved[key] for key in ("model", "model_version", "max_output", "max_context_chars")}
        config["deadline_s"] = float(saved["deadline_s"])
        if metadata.get("mode") == "media":
            config["system"] = MEDIA_SYSTEM
        return ChatSession(path / "chat", value, **config)

    def workspace(self, project):
        with self.directory(project / "uploads", create=True):
            pass
        return TextWorkspace(project / "uploads", project / "outputs")

    def agent_workspace(self, project):
        path = project / "agent-workspace"
        with self.directory(path, create=True):
            pass
        if project.name not in self.agent_backends:
            configured = {"execution_enabled": False, "execution_status": "not_configured",
                          "web_search_enabled": self.agent_enabled and self.web_search is not None,
                          "web_search_status": ("configured" if self.web_search is not None
                                                else "not_configured")}
            if self.agent_enabled and self.runtime_receipt is not None:
                try:
                    configure_execution_backend(self.runtime_receipt, path,
                        snapshot_selector=agent_workspace.ordinary_files)
                    configured.update(execution_enabled=True, execution_status="configured")
                except Exception as exc:
                    configured.update(execution_enabled=False, execution_status="unavailable",
                                      error_code=getattr(exc, "code", "execution_configuration_invalid"))
            configured.update(analysis_enabled=False, analysis_status="not_configured")
            if self.agent_enabled and self.analysis_receipt is not None:
                try:
                    configure_analysis_backend(self.analysis_receipt, path,
                                               docker_executable=self.docker_executable)
                    configured.update(analysis_enabled=True, analysis_status="configured")
                except Exception as exc:
                    configured.update(analysis_status="unavailable",
                                      analysis_error_code=getattr(exc, "code", "analysis_configuration_invalid"))
            self.agent_backends[project.name] = configured
        return path

    def agent_registry(self, project):
        self.agent_workspace(project)
        execution = self.agent_backends[project.name]["execution_enabled"]
        tools = [tool for tool in DEFAULT_TOOLS
                 if execution or tool.spec.name not in {"run_command", "run_tests"}]
        if self.web_search is not None:
            tools.append(web_search_tool(self.web_search))
        if self.agent_backends[project.name]["analysis_enabled"]:
            tools.append(ANALYZE_DATA)
        tools.append(self.memory_tool(project))
        return ToolRegistry(*tools)

    @staticmethod
    def memory_tool(project):
        # المخزنُ يُفتح (ويُنشأ) حين يقبل المالكُ نداءً بعينه، لا حين تُعلَن الأداة
        return propose_memory_tool(lambda: MemoryStore(project))

    def memory_store(self, project, *, create=False):
        """مخزنُ ذاكرة المشروع (ك٥٥)، أو لا شيء قبل أول حفظ: فلا ينشأ مجلدٌ لم يطلبه المالك."""
        path = project / "memory"
        if not create and not (path.exists() or path.is_symlink()):
            return None
        try:
            return MemoryStore(project)
        except MemoryRefused as exc:
            raise UIError(exc.code) from None

    def agent_session(self, project, session_id, *, create=False):
        workspace = self.agent_workspace(project)
        root = project / "agent-control"
        if create:
            config = {"model": self.model, "model_version": self.model_version}
            registry = self.agent_registry(project)
        else:
            with self.directory(root / identifier(session_id)) as fd:
                saved = _read_json(fd, "manifest.json")
            # ٢ ما قبل ج١٢ (ربطٌ بالموضع)، و٣ بهويّة المساحة المستقلّة عن الموضع
            need(type(saved) is dict and saved.get("schema_version") in (2, 3), "metadata_invalid")
            need(saved.get("project_id") == project.name and saved.get("session_id") == session_id,
                 "metadata_invalid")
            # كلُّ أداةٍ قد تُعلنها جلسةٌ محفوظة: الافتراضيةُ، والبحثُ والمحلّلُ (يرفضان بالاسم إن لم يُضبطا)، والذاكرة
            available = {tool.spec.name: tool for tool in (*DEFAULT_TOOLS, web_search_tool(self.web_search),
                                                           ANALYZE_DATA, self.memory_tool(project))}
            need(type(saved.get("tools")) is list and all(type(spec) is dict and
                spec.get("name") in available and spec == available[spec["name"]].spec.declared()
                for spec in saved["tools"]), "agent_tool_contract_changed")
            registry = ToolRegistry(*(available[spec["name"]] for spec in saved["tools"]))
            config = {key: saved[key] for key in ("model", "model_version", "max_steps", "max_output",
                "deadline_s", "max_context_chars", "max_turns", "system")}
            config["deadline_s"] = float(saved["deadline_s"])
        return AgentSession(root, session_id, workspace_root=workspace, project_id=project.name,
                            registry=registry, memory=self.memory_store(project), **config)

    @staticmethod
    def present_agent(turn, session=None):
        inputs = agent_workspace.decode_input(turn["text"])
        result = dict(turn["result"])
        if session is not None:
            result["pending"] = [{**view, **agent_workspace.snapshot_preview(
                session.action_store.inputs(view["action_id"]))} for view in result["pending"]]
        return {**result, "user_request": inputs["user_request"],
                "inputs": {"attachments": inputs["attachments"], "preferences": inputs.get("preferences")}, "verification": "unverified",
                "mode": "agent", **({"memory_items": turn["memory_items"]} if turn.get("memory_items") else {})}

    def memory_dispatch(self, request, project):
        """فعلُ المالك في الواجهة (ك٥٥، §٣.٢–٣.٣): «تذكّر هذا» بموافقته، و«انسَ» بإيصالٍ يعدّ ما رآه."""
        action = request["action"]
        try:
            if action == "memory":
                store = self.memory_store(project)
                if store is None:
                    return {"items": [], "receipts": []}
                return {"items": [{key: item[key] for key in ("item_id", "text", "approved_at", "source")}
                                  for item in store.items()], "receipts": store.receipts()}
            if action == "memory_remember":
                need(isinstance(request["text"], str), "memory_text_invalid")
                source = request.get("source", {})
                need(type(source) is dict and set(source) <= {"session", "turn"}
                     and all(identifier(value) for value in source.values()), "memory_source_invalid")
                item_id = self.memory_store(project, create=True).remember(
                    request["text"], consent="owner", source={"via": "owner_ui", **source})
                return {"status": "remembered", "item_id": item_id}
            store = self.memory_store(project)
            need(store is not None and isinstance(request["item_id"], str), "item_unknown")
            item = store.find(request["item_id"])
            if item is None:
                prior = store.receipts(request["item_id"])
                need(bool(prior), "item_unknown")
                return {"status": "forgotten", "receipt": prior[0], "replayed": True}
            # الإيصالُ يعدّ الجولاتِ التي رأى النموذجُ فيها العنصر؛ والجلسةُ لا تُقرأ وهي تعمل
            need(self.generation.acquire(blocking=False), "generation_busy")
            try:
                references = self.memory_references(project, item["sha256"])
            finally:
                self.generation.release()
            return {"status": "forgotten", "receipt": store.forget(item["item_id"], references=references),
                    "replayed": False}
        except MemoryRefused as exc:
            raise UIError(exc.code) from None

    def memory_references(self, project, sha256):
        references = []
        for meta in self.collection(project / "sessions"):
            mode = meta.get("mode", "text")
            if mode == "media":
                continue            # جلساتُ الوسائط لا تحمل ذاكرة
            try:
                session = (self.agent_session(project, meta["id"]) if mode == "agent"
                           else self.session(project, meta["id"]))
                references.extend(session.memory_references(sha256))
            except (ConversationError, UIError, OSError, ValueError):
                # جلسةٌ لا تُقرأ لا تحجب النسيان؛ والإيصالُ يسمّيها فلا يدّعي أنها خلت منه
                references.append(f"{mode}:{meta['id']}/unreadable")
        return sorted(references)

    def agent_dispatch(self, request, project):
        action = request["action"]
        workspace = self.agent_workspace(project)
        if action == "agent_capabilities":
            registry = self.agent_registry(project)
            return {"enabled": self.agent_enabled, "tools": [spec.declared() for spec in registry.specs()]
                    if self.agent_enabled else [], **self.agent_backends[project.name],
                    "limits": {"max_steps": 8, "selected_files": 4, "read_bytes": agent_workspace.MAX_READ_BYTES},
                    "workspace_scope": "project", "execution_success": "not_assessed"}
        if action == "agent_files":
            return agent_workspace.catalog(workspace)
        if action == "agent_read":
            return agent_workspace.read_text(workspace, request["path"])
        key = (project.name, identifier(request["session"]))
        need(self.metadata(project / "sessions" / key[1]).get("mode") == "agent", "session_mode_mismatch")
        if action == "agent_stop":
            turn_id = identifier(request["turn"])
            with self.lock:
                active = self.active is not None and self.active[:2] == key
                if active:
                    need(self.active[2] == turn_id, "turn_conflict")
                    session = self.active_agent_session
                    need(session is not None, "stop_not_ready")
                else:
                    session = None
            # The running session owns its execution lock. A stop request uses
            # the session's separate durable control channel, not generation.
            session = session if session is not None else self.agent_session(project, key[1])
            try:
                return session.request_stop(turn_id)
            except ConversationError as exc:
                if active and exc.code == "turn_unknown":
                    raise UIError("stop_not_ready") from None
                raise
        if action == "agent_ask":
            identifier(request["turn"])
            need(isinstance(request["message"], str) and 1 <= len(request["message"]) <= 24000)
            need(type(request["files"]) is list and len(request["files"]) <= 4
                 and all(isinstance(item, str) for item in request["files"])
                 and len(set(request["files"])) == len(request["files"]), "attachments_invalid")
            need(type(request.get("thinking", False)) is bool, "thinking_invalid")
        elif action == "agent_resume":
            identifier(request["turn"])
        elif action == "agent_decide":
            need(type(request["approve"]) is bool and type(request["expected_revision"]) is int,
                 "decision_invalid")
        elif action == "agent_revert":
            identifier(request["request"])
        fingerprint = digest(request)
        operation = request.get("turn", request.get("action_id"))
        with self.lock:
            if self.active == (*key, operation) and action in {"agent_ask", "agent_resume"}:
                need(self.active_payload == fingerprint, "turn_conflict")
                return {"status": "running", "turn": operation}
            need(self.generation.acquire(blocking=False), "generation_busy")
            self.active, self.active_payload = (*key, operation), fingerprint
        try:
            session = self.agent_session(project, key[1])
            with self.lock:
                self.active_agent_session = session
            if action == "agent_decide":
                return session.decide(request["action_id"], request["call_digest"],
                                      request["approve"], request["expected_revision"])
            if action == "agent_revert":
                return session.revert(request["action_id"], request["request"])
            history = session.history()["turns"]
            old = next((turn for turn in history if turn["turn_id"] == request["turn"]), None)
            if action == "agent_ask" and old is not None:
                prior = agent_workspace.decode_input(old["text"])
                need(prior["user_request"] == request["message"] and
                     [doc["source_path"] for doc in prior["attachments"]] == request["files"]
                     and old.get("thinking", False) == request.get("thinking", False), "turn_conflict")
                return {**self.present_agent(old, session), "replayed": True}
            if action == "agent_resume" and old is None:
                raise UIError("turn_unknown")
            if action == "agent_resume" and old["result"]["status"] in {
                    "complete", "truncated", "timed_out", "failed", "refused", "step_limit", "cancelled"}:
                return {**self.present_agent(old, session), "replayed": True}
            need(self.agent_enabled, "agent_unavailable")
            need(session.config["model"] == self.model and session.config["model_version"] == self.model_version,
                 "model_changed_new_session")
            need([spec.declared() for spec in self.agent_registry(project).specs()] == session.config["tools"],
                 "agent_capabilities_changed_new_session")
            if action == "agent_ask":
                need(all(turn["result"]["status"] not in {"awaiting_owner", "outcome_unknown"}
                         for turn in history), "turn_unresolved")
                available = {"files": []}
                if request["files"]:
                    available = self.dispatch({"action": "files", "project": project.name})
                    need(set(request["files"]) <= {doc["path"] for doc in available["files"]}, "attachment_unavailable")
                docs, blobs = agent_workspace.prepare_selected(project / "uploads",
                    session_id=key[1], turn_id=request["turn"], files=request["files"],
                    expected_digests={doc["path"]: doc["sha256"] for doc in available["files"]})
                preferences_root = project / "preferences"
                preferences = (Preferences(preferences_root).snapshot()
                    if preferences_root.exists() or preferences_root.is_symlink() else None)
                text = agent_workspace.encode_input(request["message"], docs, preferences)
                session.validate_turn(request["turn"], text, request.get("thinking", False))
                provider = self.agent_provider_factory()
                need(getattr(provider, "is_local", None) is True, "policy_requires_local")
                agent_workspace.materialize_selected(workspace, blobs)
                session.start_turn(request["turn"], text, provider, thinking=request.get("thinking", False))
            else:
                session.resume(request["turn"], self.agent_provider_factory())
            saved = next(turn for turn in session.history()["turns"] if turn["turn_id"] == request["turn"])
            return self.present_agent(saved, session)
        finally:
            with self.lock:
                self.active = self.active_payload = None
                self.active_agent_session = None
            self.generation.release()

    @staticmethod
    def present(result):
        # لا نكرر محتويات المرفقات في تاريخ الواجهة. النسخة المجمدة لها عملية مستقلة.
        if result["text"].startswith(MEDIA_PREFIX):
            return present_media(result)
        return {k: v for k, v in _present(result).items() if k != "text"}

    def dispatch(self, request):
        need(type(request) is dict and isinstance(request.get("action"), str))
        self.check_root()
        action = request["action"]
        schemas = {
            "projects": set(), "create_project": {"name"},
            "sessions": {"project"}, "create_session": {"project", "name"},
            "history": {"project", "session", "before"},
            "ask": {"project", "session", "turn", "message", "files"},
            "ask_media": {"project", "session", "turn", "message", "media"},
            "inspect": {"project", "session", "turn"},
            "replay": {"project", "session", "turn"},
            "files": {"project"}, "upload": {"project", "upload", "name", "content"},
            "preferences": {"project"},
            "set_preference": {"project", "key", "value", "revision"},
            "delete_preference": {"project", "key", "revision"},
            "propose": {"project", "session", "turn", "name", "request"},
            "review": {"project", "proposal"},
            "apply": {"project", "proposal", "sha256"},
            "sovereign_tools": set(),
            "sovereign_status": set(),
            "search_regulations": {"query"},
            "analyze_morphology": {"word"},
            "analyze_arabic_morphology": {"word"},
            "mlx_status": set(),
            "evaluate_governance": {"answer"},
            "agent_capabilities": {"project"}, "agent_files": {"project"},
            "agent_read": {"project", "path"},
            "agent_ask": {"project", "session", "turn", "message", "files"},
            "agent_resume": {"project", "session", "turn"},
            "agent_stop": {"project", "session", "turn"},
            "agent_decide": {"project", "session", "action_id", "call_digest", "expected_revision", "approve"},
            "agent_revert": {"project", "session", "action_id", "request"},
            "memory": {"project"},
            "memory_remember": {"project", "text"},
            "memory_forget": {"project", "item_id"},
        }
        need(action in schemas)
        fields = set(request)
        if action == "create_session":
            fields -= {"mode"}
        elif action == "search_regulations":
            fields -= {"limit"}
        elif action == "evaluate_governance":
            fields -= {"pages"}
        elif action in ("ask", "ask_media"):
            fields -= {"tier", "data_policy"}
        elif action == "agent_ask":
            fields -= {"thinking"}      # اختياريّ: طلبُ التفكير في الجولة (ك٤٧)
        elif action == "memory_remember":
            fields -= {"source"}        # اختياريّ: الجولةُ التي جاء منها النصّ (ك٥٥)
        need(fields == schemas[action] | {"action"})
        if action == "projects":
            with self.lock:
                return {"projects": self.collection(self.root / "projects"), "media_enabled": self.media_enabled,
                        "agent_enabled": self.agent_enabled, "default_session_mode": self.default_session_mode}
        if action == "create_project":
            return self.create(self.root / "projects", request["name"])
        if action in ("memory", "memory_remember", "memory_forget"):
            return self.memory_dispatch(request, self.project(request["project"]))
        if action == "mlx_status":
            _, handlers = default_tools_registry()
            res = handlers["check_mlx_hardware"]({})
            return {"status": "ok", **res}
        if action == "evaluate_governance":
            need(isinstance(request["answer"], str) and request["answer"].strip(), "answer_empty")
            pages = request.get("pages", {})
            need(isinstance(pages, dict), "pages_invalid")
            formatted_pages = {}
            for k, v in pages.items():
                try:
                    formatted_pages[int(k)] = v
                except ValueError:
                    formatted_pages[k] = v
            _, handlers = default_tools_registry()
            res = handlers["evaluate_governance"]({"answer": request["answer"].strip(), "pages": formatted_pages})
            return {"status": "ok", **res}
        if action == "sovereign_tools":
            tools, _ = default_tools_registry()
            return {
                "status": "ok",
                "tools": [
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters,
                        "consent": spec.consent,
                        "reversible": spec.reversible,
                    }
                    for spec in tools.values()
                ],
            }
        if action == "search_regulations":
            need(isinstance(request["query"], str) and request["query"].strip(), "query_empty")
            limit = request.get("limit", 5)
            need(isinstance(limit, int) and 1 <= limit <= 50, "limit_invalid")
            _, handlers = default_tools_registry()
            results = handlers["search_regulations"]({"query": request["query"].strip(), "limit": limit})
            return {"status": "ok", "query": request["query"].strip(), "results": results, "count": len(results)}
        if action in ("analyze_morphology", "analyze_arabic_morphology"):
            need(isinstance(request["word"], str) and 1 <= len(request["word"].strip()) <= 100, "word_invalid")
            _, handlers = default_tools_registry()
            res = handlers["analyze_arabic_morphology"]({"word": request["word"].strip()})
            return {"status": "ok", "analysis": res}
        if action == "sovereign_status":
            return {
                "available_tiers": [
                    {"id": "local_edge", "name": "المزوّد المحلي المضبوط", "icon": "shield", "offline": True},
                ],
                "data_policies": ["local_only", "regulated", "internal", "public"],
                "default_tier": "local_edge",
                "default_policy": "local_only",
            }
        project = self.project(request["project"])
        if action.startswith("agent_"):
            return self.agent_dispatch(request, project)
        if action == "sessions":
            with self.lock:
                return {"sessions": self.collection(project / "sessions")}
        if action == "create_session":
            return self.create(project / "sessions", request["name"], chat=True,
                               mode=request.get("mode", self.default_session_mode))
        if action in ("preferences", "set_preference", "delete_preference"):
            preferences = Preferences(project / "preferences")
            if action == "set_preference":
                return preferences.set(request["key"], request["value"], expected_revision=request["revision"])
            if action == "delete_preference":
                return preferences.delete(request["key"], expected_revision=request["revision"])
            return preferences.snapshot()
        if action in ("upload", "files"):
            with self.directory(project / "uploads", create=True):
                pass
            uploads = TextWorkspace(project / "uploads", project / "uploads")
            if action == "upload":
                name = _relative(request["name"], writing=True)
                need("/" not in name and len(name.encode("utf-8")) <= 180, "name_invalid")
                upload = identifier(request["upload"])
                path = upload + "--" + name
                proposal = uploads.propose_write(path, request["content"], upload)
                applied = uploads.apply(proposal["proposal_id"], proposal["sha256"])
                if applied["status"] != "applied":
                    return {"status": "error", "error_code": applied["error_code"], "path": path}
                return {"status": "applied", "path": path, "name": name, "sha256": proposal["sha256"], "size_bytes": proposal["size_bytes"]}
            catalog = uploads.verified_outputs()
            result = [{**doc, "name": doc["path"].split("--", 1)[-1]} for doc in catalog["files"]]
            return {"files": sorted(result, key=lambda doc: doc["path"]), "unavailable": catalog["unavailable"]}
        if action == "review":
            return TextWorkspace(None, project / "outputs").review(request["proposal"])
        if action == "apply":
            return TextWorkspace(None, project / "outputs").apply(request["proposal"], request["sha256"])
        key = (request["project"], identifier(request["session"]))
        mode = self.metadata(project / "sessions" / key[1]).get("mode", "text")
        if action == "history":
            with self.lock:
                if self.active and self.active[:2] == key:
                    return {"status": "running", "turn": self.active[2]}
            agent = self.agent_session(project, key[1]) if mode == "agent" else None
            history = agent.history()["turns"] if agent else self.session(project, key[1]).history()
            before = request["before"]
            need(before is None or (type(before) is int and 0 <= before <= len(history)))
            end = len(history) if before is None else before
            start = max(0, end - 30)
            present = (lambda turn: self.present_agent(turn, agent)) if agent else self.present
            return {"status": "idle", "turns": [present(r) for r in history[start:end]],
                    "before": start, "total": len(history)}
        need(mode != "agent", "session_mode_mismatch")
        identifier(request["turn"])
        if action in ("inspect", "replay", "propose"):
            session = self.session(project, key[1])
            if mode == "media":
                assistant = MediaAssistant(session, None)
                if action == "propose":
                    result = assistant.replay(request["turn"])
                    need(result["status"] == "complete" and result["content"].strip(), "complete_answer_required")
                    return TextWorkspace(None, project / "outputs").propose_write(
                        request["name"], result["content"], identifier(request["request"]))
                return getattr(assistant, action)(request["turn"])
            assistant = AssistantWorkspace(session, None,
                TextWorkspace(None, project / "outputs") if action == "propose" else None)
            if action == "replay":
                return {k: v for k, v in assistant.replay(request["turn"]).items() if k != "text"}
            if action == "propose":
                return assistant.propose_answer(request["turn"], request["name"], identifier(request["request"]))
            for turn in session.history():
                if turn["turn_id"] == request["turn"]:
                    envelope = _decode_context(turn["text"])
                    return {"turn": request["turn"], "attachments": envelope["attachments"],
                            "preferences": envelope["preferences"], "verification": "unverified"}
            raise UIError("workspace_turn_missing")
        need(isinstance(request["message"], str) and 1 <= len(request["message"]) <= 24000)
        need(mode == ("media" if action == "ask_media" else "text"), "session_mode_mismatch")
        if mode == "media":
            need(type(request["media"]) is list and len(request["media"]) <= 1, "media_count")
            media = []
            for item in request["media"]:
                need(type(item) is dict and set(item) == {"name", "data_base64"}, "media_invalid")
                need(isinstance(item["data_base64"], str) and len(item["data_base64"]) <= 349528, "media_size")
                try:
                    raw = base64.b64decode(item["data_base64"], validate=True)
                except (ValueError, UnicodeError):
                    raise UIError("media_encoding") from None
                need(base64.b64encode(raw).decode("ascii") == item["data_base64"], "media_encoding")
                media.append(pack_media(raw, item["name"]))
            explicit = {"message": request["message"], "media": media}
        else:
            need(type(request["files"]) is list and len(request["files"]) <= 4)
            need(all(isinstance(f, str) for f in request["files"]))
            explicit = {"message": request["message"], "files": request["files"]}
        fingerprint = digest(explicit)
        with self.lock:
            if self.active == (*key, request["turn"]):
                need(self.active_payload == fingerprint, "turn_conflict")
                return {"status": "running", "turn": request["turn"]}
            need(self.generation.acquire(blocking=False), "generation_busy")
            self.active = (*key, request["turn"])
            self.active_payload = fingerprint
        try:
            session = self.session(project, key[1])
            for old in session.history():
                if old["turn_id"] == request["turn"]:
                    if mode == "media":
                        old_envelope = decode_request(old["text"])
                        need(old_envelope["user_request"] == request["message"] and old_envelope["media"] == media,
                             "turn_conflict")
                        return present_media(old)
                    old = self.present(old)
                    need(old["user_request"] == request["message"] and
                         [doc["relative_path"] for doc in old["inputs"]["attachments"]] == request["files"], "turn_conflict")
                    return old
            tier = request.get("tier", "local_edge")
            need(tier == "local_edge", "tier_unavailable")
            data_policy = request.get("data_policy", "local_only")
            router = SovereignRouter()
            probe_req = ContractRequest(
                messages=(ContractMessage("user", request["message"]),),
                model=session.config["model"],
                model_version=session.config["model_version"],
                max_output=session.config["max_output"],
                deadline_s=session.deadline_s,
                data_policy=data_policy,
                idempotency_key=None,
            )
            try:
                router.determine_tier(probe_req, target_tier=tier)
            except SovereignRoutingError as exc:
                raise UIError(exc.code) from exc

            message_to_send = request["message"]
            token_map = {}
            if tier == "frontier_zdr":
                message_to_send, token_map = PIISanitizer.sanitize(request["message"])

            if mode == "media":
                need(self.media_enabled, "media_unavailable")
                need(session.config["model"] == self.media_model and
                     session.config["model_version"] == self.media_model_version, "model_changed_new_session")
                assistant = MediaAssistant(session, self.media_provider_factory(), Preferences(project / "preferences"))
                result = assistant.ask(request["turn"], message_to_send, media=tuple(media))
            else:
                need(session.config["model"] == self.model and session.config["model_version"] == self.model_version,
                     "model_changed_new_session")
                if request["files"]:
                    available = self.dispatch({"action": "files", "project": request["project"]})
                    need(set(request["files"]) <= {doc["path"] for doc in available["files"]}, "attachment_unavailable")
                assistant = AssistantWorkspace(session, self.provider_factory(),
                                               self.workspace(project), Preferences(project / "preferences"),
                                               memory=self.memory_store(project))
                result = assistant.ask(request["turn"], message_to_send, files=tuple(request["files"]))

            if token_map and result.get("content"):
                result["content"] = PIISanitizer.desanitize(result["content"], token_map)
            if "tier" in request or "data_policy" in request:
                result["sovereign_tier"] = tier
                result["data_policy"] = "local_only"
                result["requested_data_policy"] = data_policy
                result["sanitized_count"] = len(token_map)
            return {k: v for k, v in result.items() if k != "text"}
        finally:
            with self.lock:
                self.active = None
                self.active_payload = None
            self.generation.release()


class Server(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False

    def __init__(self, app, port=0):
        self.app = app
        self.token = secrets.token_hex(32)
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "DiwanLocal"
    sys_version = ""
    receive_timeout_s = 10

    def setup(self):
        super().setup()
        self.connection.settimeout(self.receive_timeout_s)
        self.receive_guard = threading.Lock()
        self.receiving = True
        self.receive_deadline = time.monotonic() + self.receive_timeout_s
        self.receive_timer = threading.Timer(self.receive_timeout_s, self.expire_receive)
        self.receive_timer.daemon = True
        self.receive_timer.start()

    def expire_receive(self):
        with self.receive_guard:
            if self.receiving:
                self.receiving = False
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def stop_receive(self, *, validate=True):
        with self.receive_guard:
            timely = self.receiving and time.monotonic() <= self.receive_deadline
            self.receiving = False
            self.receive_timer.cancel()
        if validate:
            need(timely, "receive_timeout")

    def finish(self):
        self.stop_receive(validate=False)
        super().finish()

    def log_message(self, *args):
        pass  # لا طلبات المستخدم ولا رمز الجلسة في سجل HTTP.

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {"error_code": "http_refused"})

    def reply(self, status, value, content_type="application/json; charset=utf-8"):
        try:
            self._reply(status, value, content_type)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass  # انتهاء مهلة الاستقبال قد يغلق الاتصال قبل إرسال الرؤوس.

    def _reply(self, status, value, content_type):
        body = canonical_bytes(value) if not isinstance(value, bytes) else value
        self.send_response(status)
        for key, val in {
            "Content-Type": content_type, "Content-Length": str(len(body)),
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'none'; img-src blob:; media-src blob:; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            "Connection": "close",
        }.items():
            self.send_header(key, val)
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass  # تنفيذ الجولة يُحفظ حتى لو أُغلقت نافذة المتصفح.

    def header(self, name):
        values = self.headers.get_all(name, [])
        need(len(values) == 1, "http_refused")
        return values[0]

    def boundary(self):
        need(self.header("Host") == self.server.origin.removeprefix("http://"), "http_refused")
        need(self.headers.get("Sec-Fetch-Site") not in ("cross-site", "same-site"), "http_refused")

    def do_GET(self):
        try:
            self.boundary()
            self.stop_receive()
            routes = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            need(self.path in routes, "not_found")
            filename, mime = routes[self.path]
            body = (STATIC / filename).read_bytes()
            if filename == "index.html":
                body = body.replace(b"__DIWAN_TOKEN__", self.server.token.encode("ascii"))
            self.reply(200, body, mime)
        except UIError:
            self.reply(403, {"error_code": "http_refused"})

    def do_POST(self):
        try:
            self.boundary()
            need(self.path == "/api", "http_refused")
            need(self.header("Origin") == self.server.origin, "http_refused")
            need(hmac.compare_digest(self.header("X-Diwan-CSRF"), self.server.token), "http_refused")
            need(self.header("Content-Type") in ("application/json", "application/json; charset=utf-8"), "http_refused")
            need(not self.headers.get_all("Transfer-Encoding"), "http_refused")
            need(not self.headers.get_all("Content-Encoding"), "http_refused")
            length = self.header("Content-Length")
            need(re.fullmatch(r"[0-9]{1,7}", length), "http_refused")
            need(0 < int(length) <= MAX_BODY, "body_limit")
            raw = self.rfile.read(int(length))
            need(len(raw) == int(length), "body_incomplete")
            request = decode(raw)
            self.stop_receive()
            result = self.server.app.dispatch(request)
            failed_write = request["action"] in ("apply", "upload") and result.get("status") == "error"
            self.reply(409 if failed_write else 200, result)
        except Exception as exc:
            code = getattr(exc, "code", "request_failed")
            # فشل غير متوقع لا يسرّب مسارات أو هوية مزود أو نصوصًا خاصة.
            self.reply(403 if code == "http_refused" else 409,
                       {"error_code": code, "release_ready": False})
