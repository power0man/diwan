"""Bounded offline backup of one private daily workspace; no provider or apply calls.

The archive is plaintext. Hashes detect corruption relative to the explicitly
supplied archive digest; they do not authenticate a hostile account owner.
Existing receipt checks remain intact. Only files created by this restore get
new inode receipts, after their original archived receipt was verified.

Agent sessions (ج١٢، ق٦٣) travel with their control state and project workspace.
A session bound to its workspace identity (manifest schema 3) is opened and verified
in place after restore. A session from before that identity (schema 2) is bound to
path and inode, so it cannot open at a new path: it is restored only as a read-only
archive under ``agent-archive/`` when the caller asks for that explicitly.
"""
from __future__ import annotations

import base64
import copy
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import unicodedata

from agent.actions import ActionRefused
from agent.registry import Tool, ToolRegistry
from conversation import ChatSession
from conversation.agent_session import AgentSession
from conversation.session import SYSTEM, ConversationError
from core import filelock
from core.canonical import canonical_bytes, digest
from core.contracts import ToolSpec
from core.ledger import GENESIS
from memory.store import LOCK_NAME as MEMORY_LOCK, MemoryRefused, MemoryStore
from multimodal.codec import MEDIA_SYSTEM, decode_request
from services.agent_workspace import decode_input as _decode_agent_input
from services.assistant_workspace import _decode_context
from services.media_assistant import MediaAssistant
from workspace_tools.files import TextWorkspace, WorkspaceError, _relative
from workspace_tools.preferences import Preferences

MAX_RAW_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 4096
MAX_DEPTH = 12
INCOMPLETE = ".restore-incomplete"
PROVENANCE = "restore-provenance.json"
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_ID = re.compile(r"[a-f0-9]{32}\Z")
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_MEMORY_ITEM = re.compile(r"items/[0-9a-f]{16}\.json\Z")
# قفلُ الذاكرة قبل ك٥٥ (الجزء ٢) كان `.lock`؛ يُقبل في النسخة ولا يُستعاد
_MEMORY_LOCKS = {MEMORY_LOCK, ".lock"}
_UNSET = object()
# الجلسات الوكيلة (ج١٢): ما في دليل تحكّم الجلسة، وما في مساحة المشروع غيرَ ملفّاتها العادية
_CONTROL_FILES = {"manifest.json", "state.json", "calls.jsonl", "session.lock",
                  "actions/manifest.json", "actions/store.lock"}
_CONTROL_RECORD = re.compile(r"(stop-[a-f0-9]{64}\.json|actions/(step|action)-[a-f0-9]{64}\.json)\Z")
_WORKSPACE_DIRS = {".diwan-journal", ".diwan-journal/blobs", ".diwan-workspace"}
_WORKSPACE_FILES = {".diwan-journal/journal.jsonl", ".diwan-journal/journal.lock",
                    ".diwan-workspace/identity.json"}
_JOURNAL_BLOB = re.compile(r"\.diwan-journal/blobs/[a-f0-9]{64}\Z")
_LOCKS = {"session.lock", "preferences.lock", "store.lock", "journal.lock"}


class BackupError(ValueError):
    def __init__(self, code, reason):
        self.code, self.reason = code, reason
        super().__init__(f"{reason} [{code}]")


def _fail(code, reason="تعذر التحقق من النسخة المحلية"):
    raise BackupError(code, reason)


def _need(condition, code="backup_invalid"):
    if not condition:
        _fail(code)


def _path(value):
    path = Path(value).expanduser().absolute()
    _need(".." not in path.parts, "backup_unsafe_path")
    return path


def restore_intent_name(root):
    """A durable reservation outside the destination covers crashes before its first file."""
    return ".diwan-restore-" + hashlib.sha256(unicodedata.normalize("NFC", _path(root).name).casefold().encode("utf-8")).hexdigest() + ".pending"


def restore_pending(root):
    root = _path(root)
    try:
        parent = _open_directory(root.parent)
    except FileNotFoundError:
        return False
    try:
        try:
            os.stat(restore_intent_name(root), dir_fd=parent, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False
    finally:
        os.close(parent)


def _private(info, *, directory=False):
    _need((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
          and info.st_uid == os.getuid() and not info.st_mode & 0o077
          and (directory or info.st_nlink == 1), "backup_unsafe_path")


def _identity(info):
    return {"device": str(info.st_dev), "inode": str(info.st_ino)}


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_directory(path):
    fd = os.open(path.anchor, _DIR)
    try:
        for part in path.parts[1:]:
            child = os.open(part, _DIR, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _parent(root_fd, relative):
    parts = relative.split("/")
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, _DIR, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


def _read_at(root_fd, relative, limit):
    with _parent(root_fd, relative) as (parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            _private(before)
            _need(before.st_size <= limit, "backup_limit")
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            _need(len(raw) == before.st_size and _signature(before) == _signature(after),
                  "backup_source_changed")
            _need(len(raw) <= limit, "backup_limit")
            return raw, after


def _relative_path(value, *, root=False):
    _need(type(value) is str, "backup_unsafe_path")
    if value == "" and root:
        return value
    parts = value.split("/")
    _need(value and not value.startswith("/") and "\\" not in value
          and len(parts) <= MAX_DEPTH and all(p not in ("", ".", "..") for p in parts)
          and len(value.encode("utf-8")) <= 4096
          and not any(unicodedata.category(c).startswith("C") for c in value),
          "backup_unsafe_path")
    return value


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _need(key not in result, "backup_invalid")
            result[key] = value
        return result
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    canonical_bytes(value)
    return value


def _inventory(root_fd):
    result = {}
    def walk(fd, path):
        info = os.fstat(fd)
        _private(info, directory=True)
        result[path] = (True, _signature(info), _identity(info))
        _need(len(result) <= MAX_ENTRIES, "backup_limit")
        for name in sorted(os.listdir(fd)):
            child_path = name if not path else path + "/" + name
            _relative_path(child_path)
            item = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(item.st_mode):
                child = os.open(name, _DIR, dir_fd=fd)
                try:
                    _need(_identity(os.fstat(child)) == _identity(item), "backup_source_changed")
                    walk(child, child_path)
                finally:
                    os.close(child)
            else:
                _private(item)
                result[child_path] = (False, _signature(item), _identity(item))
                _need(len(result) <= MAX_ENTRIES, "backup_limit")
    walk(root_fd, "")
    return result


@contextmanager
def _lease(root_fd, path):
    with _parent(root_fd, path) as (parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        _private(os.fstat(fd))
        try:
            filelock.lock(fd, blocking=False)
        except BlockingIOError:
            _fail("backup_busy", "أغلق مساحة العمل قبل النسخ")
        yield
    finally:
        os.close(fd)


def _take_snapshot(root_fd, root):
    with ExitStack() as stack:
        stack.enter_context(_lease(root_fd, "app.lock"))
        before = _inventory(root_fd)
        for path, (directory, _, _) in sorted(before.items()):
            if not directory and path != "app.lock" and (path.rsplit("/", 1)[-1] in _LOCKS
                    or _memory_part(path) in _MEMORY_LOCKS):
                stack.enter_context(_lease(root_fd, path))
        _need(_inventory(root_fd) == before, "backup_source_changed")
        directories, files, total = [], [], 0
        for path, (directory, signature, identity) in sorted(before.items()):
            if directory:
                directories.append({"path": path, "identity": identity})
                continue
            raw, info = _read_at(root_fd, path, MAX_RAW_BYTES - total)
            _need(_signature(info) == signature, "backup_source_changed")
            total += len(raw)
            files.append({"path": path, "identity": identity, "size_bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "data_base64": base64.b64encode(raw).decode("ascii")})
        _need(_inventory(root_fd) == before, "backup_source_changed")
        return {"schema_version": 1, "kind": "diwan_daily_workspace_backup",
                "source_root": str(root), "directories": directories, "files": files}


def _valid_identity(value):
    return (type(value) is dict and set(value) == {"device", "inode"}
            and all(type(x) is str and re.fullmatch(r"[0-9]{1,32}", x) for x in value.values()))


def _unpack(bundle):
    _need(type(bundle) is dict and set(bundle) == {
        "schema_version", "kind", "source_root", "directories", "files"})
    _need(type(bundle["schema_version"]) is int and bundle["schema_version"] == 1
          and bundle["kind"] == "diwan_daily_workspace_backup")
    source = bundle["source_root"]
    _need(type(source) is str and source.startswith("/") and str(_path(source)) == source
          and len(source.encode("utf-8")) <= 4096)
    directories, files = bundle["directories"], bundle["files"]
    _need(type(directories) is list and type(files) is list
          and 1 <= len(directories) + len(files) <= MAX_ENTRIES, "backup_limit")
    seen, folded, dirs, data, identities = set(), set(), {}, {}, {}
    total = 0
    for directory, entries in ((True, directories), (False, files)):
        previous = None
        for item in entries:
            keys = {"path", "identity"} if directory else {
                "path", "identity", "size_bytes", "sha256", "data_base64"}
            _need(type(item) is dict and set(item) == keys)
            path = _relative_path(item["path"], root=directory)
            collision = unicodedata.normalize("NFC", path).casefold()
            _need(path not in seen and collision not in folded and
                  (previous is None or previous < path), "backup_unsafe_path")
            previous = path
            seen.add(path); folded.add(collision)
            _need(_valid_identity(item["identity"]))
            identities[path] = item["identity"]
            if directory:
                dirs[path] = item["identity"]
                continue
            size, encoded = item["size_bytes"], item["data_base64"]
            _need(type(size) is int and 0 <= size <= MAX_RAW_BYTES - total, "backup_limit")
            _need(type(encoded) is str and len(encoded) == 4 * ((size + 2) // 3))
            raw = base64.b64decode(encoded, validate=True)
            _need(len(raw) == size and base64.b64encode(raw).decode("ascii") == encoded
                  and type(item["sha256"]) is str
                  and hashlib.sha256(raw).hexdigest() == item["sha256"])
            data[path] = raw
            total += size
    _need("" in dirs and "app.lock" in data, "backup_incomplete")
    for path in seen - {""}:
        _need(path.rsplit("/", 1)[0] in dirs if "/" in path else "" in dirs,
              "backup_unsafe_path")
    return dirs, data, identities, total


def _memory_part(path):
    """ما بعد `projects/<id>/memory/` في المسار، أو None إن لم يكن من ذاكرة مشروع."""
    parts = path.split("/")
    if len(parts) >= 4 and parts[0] == "projects" and parts[2] == "memory":
        return "/".join(parts[3:])
    return None


def _memory_snapshot(data, memory):
    """لقطةُ مخزن الذاكرة كما يقبلها `MemoryStore.restore`: العناصرُ والإيصالات، بلا القفل."""
    prefix = memory + "/"
    return {path[len(prefix):]: raw for path, raw in data.items()
            if path.startswith(prefix) and path[len(prefix):] not in _MEMORY_LOCKS}


def _metadata(raw, expected, *, session=False):
    value = _json(raw)
    fields = {"id", "name"}
    _need(type(value) is dict and set(value) in
          ((fields, fields | {"mode"}) if session else (fields,)))
    name = value["name"]
    _need(value["id"] == expected and type(name) is str and 1 <= len(name.strip()) <= 80
          and not any(unicodedata.category(c).startswith("C") for c in name))
    mode = value.get("mode", "text")
    _need(mode in ("text", "media", "agent"))
    return mode


def _ordinary(relative):
    try:
        _relative(relative, writing=True)
        return True
    except WorkspaceError:
        return False


def _control(prefix, dirs, data, allowed_dirs, allowed_files):
    """دليلُ تحكّم جلسةٍ وكيلة: ملفّاتُه المعروفة حاضرةً كلُّها، ولا غيرُها."""
    _need(prefix in dirs and prefix + "/actions" in dirs
          and {prefix + "/" + name for name in _CONTROL_FILES} <= data.keys(), "backup_incomplete")
    allowed_dirs.update({prefix, prefix + "/actions"})
    for path in data:
        if path.startswith(prefix + "/"):
            rest = path[len(prefix) + 1:]
            if rest in _CONTROL_FILES or _CONTROL_RECORD.fullmatch(rest):
                allowed_files.add(path)


def _shape(dirs, data):
    allowed_dirs = {"", "projects", "staging"}
    allowed_files = {"app.lock", PROVENANCE}
    _need(INCOMPLETE not in data, "backup_incomplete")
    _need(not any(p.startswith("staging/") for p in set(dirs) | set(data)),
          "backup_staging_not_empty")
    projects = sorted(p for p in dirs if p.startswith("projects/") and p.count("/") == 1)
    _need(len(projects) <= 64, "backup_limit")
    sessions, stores, preferences, memories = [], [], [], []
    agents, archived = [], []
    for project in projects:
        pid = project.split("/")[-1]
        _need(_ID.fullmatch(pid), "backup_invalid")
        allowed_dirs.update({project, project + "/sessions"})
        _metadata(data[project + "/meta.json"], pid)
        allowed_files.add(project + "/meta.json")
        pref = project + "/preferences"
        if pref in dirs:
            allowed_dirs.add(pref)
            allowed_files.update({pref + "/state.json", pref + "/preferences.lock"})
            _need(all(p in data for p in (pref + "/state.json", pref + "/preferences.lock")),
                  "backup_incomplete")
            preferences.append(pref)
        memory = project + "/memory"
        if memory in dirs:
            # الذاكرةُ المحكومة (ك٥٥): عناصرُها وإيصالاتُها، وتُفحص بصمةً بصمة في `_validate_tree`
            _need(memory + "/items" in dirs, "backup_incomplete")
            allowed_dirs.update({memory, memory + "/items"})
            for path in data:
                part = _memory_part(path) if path.startswith(memory + "/") else None
                if part is not None and (part in _MEMORY_LOCKS or part == "receipts.jsonl"
                                         or _MEMORY_ITEM.match(part)):
                    allowed_files.add(path)
            memories.append(memory)
        current = sorted(p for p in dirs if p.startswith(project + "/sessions/") and p.count("/") == 3)
        _need(len(current) <= 64, "backup_limit")
        for session in current:
            sid = session.split("/")[-1]
            _need(_ID.fullmatch(sid), "backup_invalid")
            mode = _metadata(data[session + "/meta.json"], sid, session=True)
            if mode == "agent":
                # الجلسةُ الوكيلة (ج١٢): بيانُها هنا، وتحكّمُها في agent-control، ومساحتُها للمشروع كلِّه
                allowed_dirs.add(session)
                allowed_files.add(session + "/meta.json")
                _control(project + "/agent-control/" + sid, dirs, data, allowed_dirs, allowed_files)
                agents.append((project, sid))
                continue
            chat = session + "/chat/" + sid
            allowed_dirs.update({session, session + "/chat", chat})
            expected = {session + "/meta.json"} | {chat + "/" + name for name in (
                "manifest.json", "state.json", "calls.jsonl", "session.lock")}
            _need(expected <= data.keys() and chat in dirs, "backup_incomplete")
            allowed_files.update(expected)
            sessions.append((chat, sid, mode))
        if project + "/agent-control" in dirs:
            allowed_dirs.add(project + "/agent-control")   # وأبناؤه جلساتٌ ذاتُ بيانٍ فقط
        work = project + "/agent-workspace"
        if work in dirs:
            allowed_dirs.add(work)
            for path in set(dirs) | set(data):
                if not path.startswith(work + "/"):
                    continue
                rest, directory = path[len(work) + 1:], path in dirs
                if (rest in (_WORKSPACE_DIRS if directory else _WORKSPACE_FILES) or _ordinary(rest)
                        or (not directory and _JOURNAL_BLOB.fullmatch(rest))):
                    (allowed_dirs if directory else allowed_files).add(path)
        shelf = project + "/agent-archive"
        if shelf in dirs:
            # جلساتٌ من قبل ج١٢ استُعيدت أرشيفًا للقراءة: بيانُها وتحكّمُها كما كانا، ولا تُفتح
            allowed_dirs.add(shelf)
            for entry in sorted(p for p in dirs if p.startswith(shelf + "/") and p.count("/") == 3):
                sid = entry.split("/")[-1]
                _need(_ID.fullmatch(sid), "backup_invalid")
                _need(_metadata(data[entry + "/meta.json"], sid, session=True) == "agent", "backup_invalid")
                allowed_dirs.add(entry)
                allowed_files.add(entry + "/meta.json")
                _control(entry + "/control", dirs, data, allowed_dirs, allowed_files)
                archived.append(entry + "/control")
        for category in ("uploads", "outputs"):
            artifact = project + "/" + category
            if artifact not in dirs:
                continue
            allowed_dirs.add(artifact)
            internal = artifact + "/.diwan-tools"
            internal_files = {internal + "/writes/" + name for name in (
                "manifest.json", "state.json", "store.lock")}
            if internal in dirs:
                _need(internal + "/writes" in dirs and internal_files <= data.keys(),
                      "backup_incomplete")
                allowed_dirs.update({internal, internal + "/writes"})
                allowed_files.update(internal_files)
                stores.append(artifact)
            for p in set(dirs) | set(data):
                if not p.startswith(artifact + "/") or p.startswith(internal + "/") or p == internal:
                    continue
                _relative(p[len(artifact) + 1:], writing=True)
                (allowed_dirs if p in dirs else allowed_files).add(p)
    _need(set(dirs) <= allowed_dirs and set(data) <= allowed_files, "backup_tree_invalid")
    if PROVENANCE in data:
        previous = _json(data[PROVENANCE])
        _need(type(previous) is dict and previous.get("schema_version") == 1
              and type(previous.get("archive_sha256")) is str
              and _SHA.fullmatch(previous["archive_sha256"]), "backup_invalid")
    return sessions, stores, preferences, memories, (agents, archived)


def _write_file(root_fd, path, raw, *, replace=False):
    with _parent(root_fd, path) as (parent, name):
        flags = os.O_WRONLY | os.O_NOFOLLOW | (os.O_TRUNC if replace else os.O_CREAT | os.O_EXCL)
        fd = os.open(name, flags, 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as stream:
            _private(os.fstat(stream.fileno()))
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)


def _materialize(root, dirs, data):
    root_fd = _open_directory(root)
    try:
        for path in sorted(set(dirs) - {""}, key=lambda p: (p.count("/"), p)):
            with _parent(root_fd, path) as (parent, name):
                os.mkdir(name, 0o700, dir_fd=parent)
                os.fsync(parent)
        for path, raw in data.items():
            _write_file(root_fd, path, raw)
    finally:
        os.close(root_fd)


def _schema(data, project, sid):
    config = _json(data[f"{project}/agent-control/{sid}/manifest.json"])
    _need(type(config) is dict, "backup_agent_session_invalid")
    return config.get("schema_version")


def _check_legacy_control(data, prefix):
    """جلسةٌ من قبل ج١٢ لا تُفتح في غير موضعها، فتُفحص بنيةً وبصمات: بيانُها، وحالتُها المربوطة
    به، وسلسلةُ نداءاتها ورأسُها المحفوظ، وبصمةُ كلِّ إيصال فعل."""
    config = _json(data[prefix + "/manifest.json"])
    _need(type(config) is dict and config.get("schema_version") == 2 and type(config.get("workspace")) is dict
          and set(config["workspace"]) == {"path", "device", "inode"}, "backup_agent_session_invalid")
    envelope = _json(data[prefix + "/state.json"])
    state = envelope.get("state") if type(envelope) is dict else None
    _need(type(state) is dict and set(envelope) == {"state", "sha256"} and envelope["sha256"] == digest(state)
          and state.get("config_digest") == digest(config), "backup_agent_session_invalid")
    previous, heads = GENESIS, []
    for index, line in enumerate(line for line in data[prefix + "/calls.jsonl"].splitlines() if line.strip()):
        entry = _json(line)
        _need(type(entry) is dict and set(entry) == {"prev", "seq", "record", "digest"}
              and entry["prev"] == previous and entry["seq"] == index
              and digest({"prev": entry["prev"], "seq": entry["seq"], "record": entry["record"]}) == entry["digest"],
              "backup_agent_session_invalid")
        previous = entry["digest"]
        heads.append(previous)
    ledger = state.get("ledger")
    _need(type(ledger) is dict and type(ledger.get("count")) is int and 0 <= ledger["count"] <= len(heads)
          and ledger.get("head") == (heads[ledger["count"] - 1] if ledger["count"] else GENESIS),
          "backup_agent_session_invalid")
    for path, raw in data.items():
        if path.startswith(prefix + "/actions/") and not path.endswith("/store.lock"):
            record = _json(raw)
            _need(type(record) is dict and set(record) == {"record", "sha256"}
                  and digest(record["record"]) == record["sha256"], "backup_agent_session_invalid")


def _refuse_tool(arguments, context):
    raise RuntimeError("التحقّق من النسخة لا ينفّذ أداة")


def _open_agent(root, project, sid, config):
    """الجلسةُ بهويّة مساحتها تُفتح في موضعها الجديد: البناءُ يفحص البيان والحالة والسجلّ وكلَّ إيصال فعل،
    بسجلّ أدواتٍ مجمَّدٍ من عقدها لا ينفّذ شيئًا."""
    try:
        registry = ToolRegistry(*(Tool(ToolSpec(**spec), _refuse_tool) for spec in config["tools"]))
        AgentSession(root / project / "agent-control", sid, workspace_root=root / project / "agent-workspace",
                     project_id=project.split("/")[-1], registry=registry, model=config["model"],
                     model_version=config["model_version"], max_steps=config["max_steps"],
                     max_output=config["max_output"], deadline_s=float(config["deadline_s"]),
                     max_context_chars=config["max_context_chars"], max_turns=config["max_turns"],
                     system=config["system"])
    except (ConversationError, ActionRefused):
        _fail("backup_agent_session_invalid", "جلسةٌ وكيلة في النسخة لا تطابق حالتها أو إيصالاتها")


def _validate_tree(root, bundle, dirs, data, identities, shape):
    sessions, stores, preferences, memories, (agents, archived) = shape
    migrations = []
    fd = _open_directory(root)
    try:
        for artifact in stores:
            prefix = artifact + "/.diwan-tools/writes/"
            config = _json(data[prefix + "manifest.json"])
            envelope = _json(data[prefix + "state.json"])
            _need(type(config) is dict and set(config) == {
                "schema_version", "max_bytes", "artifact_root", "artifact_identity"})
            _need(type(config["schema_version"]) is int and config["schema_version"] == 1
                  and type(config["max_bytes"]) is int and 1 <= config["max_bytes"] <= 65536
                  and config["artifact_root"] == str(Path(bundle["source_root"]) / artifact)
                  and config["artifact_identity"] == dirs[artifact])
            _need(type(envelope) is dict and set(envelope) == {"state", "sha256"})
            state = envelope["state"]
            _need(type(state) is dict and set(state) == {"config_sha256", "proposals"}
                  and envelope["sha256"] == digest(state) and state["config_sha256"] == digest(config)
                  and type(state["proposals"]) is list)
            migrated = copy.deepcopy(state)
            changed = []
            for proposal in migrated["proposals"]:
                _need(type(proposal) is dict)
                _need(proposal.get("status") != "pending", "backup_pending")
                if proposal.get("status") != "applied":
                    continue
                path = artifact + "/" + _relative(proposal["path"], writing=True)
                _need(path in data and proposal["owner"] == identities[path]
                      and proposal["size_bytes"] == len(data[path])
                      and proposal["sha256"] == hashlib.sha256(data[path]).hexdigest(),
                      "backup_applied_changed")
                with _parent(fd, path) as (parent, name):
                    new_owner = _identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                changed.append({"proposal_id": proposal["proposal_id"],
                                "old_owner": proposal["owner"], "new_owner": new_owner})
                proposal["owner"] = new_owner
            artifact_fd = _open_directory(root / artifact)
            try:
                new_config = {**config, "artifact_root": str(root / artifact),
                              "artifact_identity": _identity(os.fstat(artifact_fd))}
            finally:
                os.close(artifact_fd)
            migrated["config_sha256"] = digest(new_config)
            _write_file(fd, prefix + "manifest.json", canonical_bytes(new_config), replace=True)
            _write_file(fd, prefix + "state.json",
                        canonical_bytes({"state": migrated, "sha256": digest(migrated)}), replace=True)
            workspace = TextWorkspace(None, root / artifact, max_bytes=config["max_bytes"])
            _need(not any(item["error_code"] == "applied_output_changed"
                          for item in workspace.verified_outputs()["unavailable"]), "backup_applied_changed")
            migrations.append({"path": artifact, "old_config_sha256": digest(config),
                               "new_config_sha256": digest(new_config), "owners": changed})
        for path in preferences:
            Preferences(root / path).snapshot()
        for memory in memories:
            try:
                MemoryStore.check_snapshot(_memory_snapshot(data, memory))
            except MemoryRefused:
                _fail("backup_memory_invalid", "ذاكرةُ مشروعٍ في النسخة لا تطابق بصماتها")
        for chat, sid, mode in sessions:
            config = _json(data[chat + "/manifest.json"])
            envelope = _json(data[chat + "/state.json"])
            _need(type(config) is dict and type(envelope) is dict)
            turns = envelope["state"]["turns"]
            _need(type(turns) is list)
            _need(all(type(turn) is dict and turn.get("result") is not None for turn in turns),
                  "backup_pending")
            session = ChatSession(root / Path(chat).parent, sid,
                model=config["model"], model_version=config["model_version"],
                max_output=config["max_output"], deadline_s=float(config["deadline_s"]),
                max_context_chars=config["max_context_chars"], system=MEDIA_SYSTEM if mode == "media" else SYSTEM)
            if mode == "media":
                MediaAssistant(session, None)  # Enforce the fixed media execution profile.
            # Constructor checks manifests, request fingerprints, state and ledger.
            # It may touch only this copied staging ledger; never the source.
            for turn in turns:
                (decode_request if mode == "media" else _decode_context)(turn["text"])
        for project, sid in agents:
            prefix = f"{project}/agent-control/{sid}"
            envelope = _json(data[prefix + "/state.json"])
            _need(type(envelope) is dict and type(envelope.get("state")) is dict)
            turns = envelope["state"].get("turns")
            _need(type(turns) is list and all(type(turn) is dict for turn in turns))
            # جولةٌ جارية أو تنتظر قرار المالك تُحسم قبل النسخ؛ والمجهولةُ النتيجة تُنسخ كما هي ولا تُعاد
            _need(all(type(turn.get("result")) is dict and turn["result"].get("status") != "awaiting_owner"
                      for turn in turns), "backup_pending")
            for turn in turns:
                _decode_agent_input(turn["text"])
            if _schema(data, project, sid) == 3:
                _open_agent(root, project, sid, _json(data[prefix + "/manifest.json"]))
            else:
                _check_legacy_control(data, prefix)
        for prefix in archived:
            _check_legacy_control(data, prefix)
    finally:
        os.close(fd)
    return migrations


def _validate_bundle(bundle):
    dirs, data, identities, total = _unpack(bundle)
    shape = _shape(dirs, data)
    with tempfile.TemporaryDirectory(prefix="diwan-backup-check-") as temporary:
        root = Path(temporary).resolve()
        _materialize(root, dirs, data)
        _validate_tree(root, bundle, dirs, data, identities, shape)
    return dirs, data, identities, total, shape


def _report(status, sha256, dirs, data, total):
    return {"status": status, "sha256": sha256, "file_count": len(data),
            "directory_count": len(dirs), "total_bytes": total}


def _read_archive(archive, expected_sha256):
    _need(type(expected_sha256) is str and _SHA.fullmatch(expected_sha256), "backup_digest_invalid")
    archive = _path(archive)
    fd = _open_directory(archive.parent)
    try:
        raw, _ = _read_at(fd, archive.name, MAX_ARCHIVE_BYTES)
    finally:
        os.close(fd)
    _need(hashlib.sha256(raw).hexdigest() == expected_sha256, "backup_digest_mismatch")
    bundle = _json(raw)
    _need(canonical_bytes(bundle) == raw, "backup_invalid")
    return bundle


def _guard(function):
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except BackupError:
            raise
        except MemoryRefused:
            _fail("backup_memory_invalid", "ذاكرةُ مشروعٍ لا تُستعاد بصمتها")
        except FileExistsError:
            _fail("backup_destination_exists", "الوجهة موجودة؛ لا استبدال")
        except (OSError, ValueError, TypeError, KeyError, RecursionError, AttributeError):
            _fail("backup_invalid")
    return guarded


@_guard
def export_workspace(root, archive):
    root, archive = _path(root), _path(archive)
    _need(not restore_pending(root), "backup_incomplete")
    _need(not archive.is_relative_to(root), "backup_unsafe_path")
    root_fd = _open_directory(root)
    try:
        _private(os.fstat(root_fd), directory=True)
        bundle = _take_snapshot(root_fd, root)
    finally:
        os.close(root_fd)
    dirs, data, _, total, _ = _validate_bundle(bundle)
    raw = canonical_bytes(bundle)
    _need(len(raw) <= MAX_ARCHIVE_BYTES, "backup_limit")
    parent = _open_directory(archive.parent)
    try:
        _private(os.fstat(parent), directory=True)
        _write_file(parent, archive.name, raw)
    finally:
        os.close(parent)
    return _report("exported", hashlib.sha256(raw).hexdigest(), dirs, data, total)


@_guard
def inspect_archive(archive, expected_sha256):
    bundle = _read_archive(archive, expected_sha256)
    dirs, data, _, total, _ = _validate_bundle(bundle)
    return _report("verified", expected_sha256, dirs, data, total)


def _live_receipts(live_root, memory):
    """إيصالاتُ النسيان في المساحة الحيّة لهذا المشروع، أو لا شيء إن لم يكن له مخزنٌ فيها."""
    fd = _open_directory(live_root)
    try:
        try:
            raw, _ = _read_at(fd, memory + "/receipts.jsonl", MAX_RAW_BYTES)
        except FileNotFoundError:
            return None
    finally:
        os.close(fd)
    try:
        MemoryStore.check_snapshot({"receipts.jsonl": raw})
    except MemoryRefused:
        _fail("backup_memory_invalid", "إيصالاتُ النسيان في المساحة الحيّة غير صالحة")
    return raw


def _restore_memory(destination, data, memories, live_root):
    """الذاكرةُ تُستعاد عبر `MemoryStore.restore` وحده (§٣.٣): الإيصالاتُ كلُّها، القائمةُ في
    المساحة الحيّة ثم ما في النسخة، تُطبَّق قبل أن يُكتب عنصرٌ واحد. فالمنسيُّ بعد النسخة لا يعود."""
    report = {"projects": len(memories), "items_in_archive": 0, "items_restored": 0,
              "live_receipts_applied": live_root is not None}
    for memory in memories:
        snapshot = _memory_snapshot(data, memory)
        store = MemoryStore(destination / Path(memory).parent)
        if live_root is not None:
            live = _live_receipts(live_root, memory)
            if live is not None:
                store.restore({"receipts.jsonl": live})
        store.restore(snapshot)
        report["items_in_archive"] += sum(name.startswith("items/") for name in snapshot)
        report["items_restored"] += len(store.items())
    return report


def _archive_moves(dirs, data, legacy):
    """جلساتُ ما قبل ج١٢ تنتقل إلى agent-archive: بيانُها ودليلُ تحكّمها كما هما، ولا تُفتح جلسةً حيّة."""
    moves = {}
    for project, sid in legacy:
        sources = {f"{project}/sessions/{sid}": f"{project}/agent-archive/{sid}",
                   f"{project}/agent-control/{sid}": f"{project}/agent-archive/{sid}/control"}
        for path in set(dirs) | set(data):
            for old, new in sources.items():
                if path == old or path.startswith(old + "/"):
                    moves[path] = new + path[len(old):]
    return moves


@_guard
def restore_workspace(archive, destination, expected_sha256, *, tombstones_from=_UNSET,
                      legacy_agent_sessions=_UNSET):
    """`tombstones_from` جذرُ المساحة الحيّة إن بقيت، فتُطبَّق إيصالاتُ نسيانها على ذاكرة النسخة؛
    أو None صراحةً إن فُقدت. ونسخةٌ فيها ذاكرة لا تُستعاد بلا هذا الاختيار: فاستعادةُ نسخةٍ أقدم
    من نسيانٍ بلا إيصالاته تُعيد المنسيّ.

    `legacy_agent_sessions="archive"` اختيارٌ صريح لجلساتٍ وكيلةٍ من قبل ج١٢: مربوطةٌ بالمسار وinode
    فلا تُفتح في موضعٍ جديد، فتُستعاد أرشيفًا للقراءة في `agent-archive/`. ونسخةٌ فيها منها لا تُستعاد
    بلا هذا الاختيار (`backup_agent_session_not_portable`)."""
    bundle = _read_archive(archive, expected_sha256)
    dirs, data, identities, total, shape = _validate_bundle(bundle)
    memories = shape[3]
    agents = shape[4][0]
    legacy = [(project, sid) for project, sid in agents if _schema(data, project, sid) != 3]
    _need(legacy_agent_sessions in (_UNSET, "archive"), "backup_invalid")
    if legacy:
        _need(legacy_agent_sessions == "archive", "backup_agent_session_not_portable")
    moves = _archive_moves(dirs, data, legacy)
    if memories:
        _need(tombstones_from is not _UNSET, "backup_tombstones_required")
    live_root = None if tombstones_from in (_UNSET, None) else _path(tombstones_from)
    if live_root is not None:
        fd = _open_directory(live_root)
        try:
            _private(os.fstat(fd), directory=True)
        finally:
            os.close(fd)
    memory_paths = {path for path in set(dirs) | set(data)
                    if any(path == m or path.startswith(m + "/") for m in memories)}
    destination = _path(destination)
    parent = _open_directory(destination.parent)
    intent = restore_intent_name(destination)
    try:
        _private(os.fstat(parent), directory=True)
        try:
            os.stat(destination.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _fail("backup_destination_exists", "الوجهة موجودة؛ لا استبدال")
        _write_file(parent, intent, b"diwan restore reserved; incomplete\n")
        intent_owner = _identity(os.stat(intent, dir_fd=parent, follow_symlinks=False))
        try:
            os.mkdir(destination.name, 0o700, dir_fd=parent)
        except FileExistsError:
            # Remove only our reservation; the concurrently created destination is untouched.
            _need(_identity(os.stat(intent, dir_fd=parent, follow_symlinks=False)) == intent_owner,
                  "backup_source_changed")
            os.unlink(intent, dir_fd=parent)
            os.fsync(parent)
            raise
        os.fsync(parent)
        fd = _open_directory(destination)
        try:
            _write_file(fd, INCOMPLETE, b"diwan restore incomplete\n")
            live_dirs = {moves.get(k, k): v for k, v in dirs.items() if k not in memory_paths}
            live_dirs.update({f"{project}/agent-archive": dirs[project] for project, _ in legacy})
            _materialize(destination, live_dirs,
                         {moves.get(k, k): v for k, v in data.items() if k not in memory_paths})
            migrations = _validate_tree(destination, bundle, dirs, data, identities, shape)
            memory_report = _restore_memory(destination, data, memories, live_root)
            agent_report = {"sessions": len(agents) - len(legacy),
                            "archived": [f"{project}/agent-archive/{sid}" for project, sid in legacy]}
            provenance = {"schema_version": 1, "archive_sha256": expected_sha256,
                          "workspace_migrations": migrations,
                          **({"memory": memory_report} if memories else {}),
                          **({"agent_sessions": agent_report} if agents else {})}
            _write_file(fd, PROVENANCE, canonical_bytes(provenance), replace=PROVENANCE in data)
            os.unlink(INCOMPLETE, dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(fd)
        _need(_identity(os.stat(intent, dir_fd=parent, follow_symlinks=False)) == intent_owner,
              "backup_source_changed")
        os.unlink(intent, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
    report = _report("restored", expected_sha256, dirs, data, total)
    return {**report, **({"memory": memory_report} if memories else {}),
            **({"agent_sessions": agent_report} if agents else {})}
