"""Candidate execution in an explicitly configured, disposable Docker container.

The trusted application bootstrap supplies a private receipt outside the candidate
workspace and an explicit list of approved input files. Neither tool arguments nor
environment declarations can configure a backend. Configuration is not proof of
isolation: image identity and the created container are checked for every call.
No workspace bind mount, Docker socket, host environment, or result file is shared.
The snapshot allowlist excludes common credential paths; its contents still require
the operator's review. This is a container boundary, not a separate physical host.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import stat
import subprocess
import time
import unicodedata
import uuid

from core.canonical import PayloadRejected

IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_TIMEOUT_S = 900
MEMORY_BYTES = 1024 * 1024 * 1024
TMPFS = {
    "/workspace": "rw,exec,nosuid,nodev,size=128m,uid=1000,gid=1000,mode=700",
    "/tmp": "rw,exec,nosuid,nodev,size=64m,uid=1000,gid=1000,mode=700",
}
_BACKENDS: dict[Path, "DockerExecutionBackend"] = {}
_FROZEN_INPUTS: ContextVar[dict | None] = ContextVar("diwan_frozen_execution_inputs", default=None)


class ExecutionRefused(PayloadRejected):
    def __init__(self, code: str, reason: str):
        super().__init__("execution", code, reason)


def _refuse(code: str, reason: str):
    raise ExecutionRefused(code, reason)


def _clean_env() -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def _identity(fd: int) -> tuple[int, int]:
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _open_directory(path: Path, *, forbidden_identity=None) -> int:
    """Pin a directory by traversing all absolute components without symlinks."""
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            if part == "..":
                raise OSError("parent traversal refused")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            if forbidden_identity is not None and _identity(fd) == forbidden_identity:
                _refuse("execution_receipt_untrusted", "إيصال التشغيل داخل مساحة المرشح")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _root(value) -> Path:
    try:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("absolute root required")
        fd = _open_directory(path)
        os.close(fd)
        return path
    except (TypeError, ValueError, OSError, RuntimeError):
        _refuse("execution_workspace_invalid", "مساحة تنفيذ مطلقة موجودة مطلوبة")


def _snapshot_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        _refuse("execution_snapshot_invalid", "اسم ملف غير صالح في لقطة التنفيذ")
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or str(path) != name or any(part.startswith(".") for part in path.parts):
        _refuse("execution_snapshot_invalid", "لا مسارات مطلقة أو مخفية أو رجوع في لقطة التنفيذ")
    folded = [part.casefold() for part in path.parts]
    if (any(part in {"secrets", "credentials", "keychain", "private"} for part in folded)
            or path.suffix.casefold() in {".key", ".pem", ".p12", ".pfx", ".kdbx", ".db", ".sqlite", ".sqlite3"}):
        _refuse("execution_snapshot_sensitive", "مسار اعتماد أو مخزن خاص مرفوض في لقطة التنفيذ")
    return name


def _snapshot_files(value) -> tuple[str, ...]:
    if not isinstance(value, tuple) or len(value) > 1024:
        _refuse("execution_snapshot_invalid", "قائمة ملفات موافق عليها مطلوبة")
    names = tuple(_snapshot_name(name) for name in value)
    aliases = [unicodedata.normalize("NFC", name).casefold() for name in names]
    if len(set(aliases)) != len(aliases):
        _refuse("execution_snapshot_invalid", "تكرار أو التباس في أسماء ملفات اللقطة")
    return names


def _read_snapshot_file(root: Path, name: str, *, root_fd: int | None = None) -> bytes:
    """Open every component without following links, including intermediate dirs."""
    fd = _open_directory(root) if root_fd is None else os.dup(root_fd)
    try:
        parts = PurePosixPath(name).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(child, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
                _refuse("execution_snapshot_invalid", "لقطة التنفيذ تقبل ملفات عادية محدودة الحجم فقط")
            raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                _refuse("execution_snapshot_too_large", "ملف لقطة التنفيذ تجاوز سقف الحجم")
            return raw
    except OSError as exc:
        raise ExecutionRefused("execution_snapshot_invalid", "تعذر فتح ملف عادي بلا روابط رمزية") from exc
    finally:
        os.close(fd)


# This trusted probe runs in its own inspected container before any candidate
# bytes are sent. Only its daemon-attested exit and cleanup authorize the next
# container. Candidate stdout cannot forge or interfere with this phase.
_RUNTIME_PREFLIGHT = r'''
import hashlib, importlib.metadata, json, pathlib, re, sys
payload = json.loads(sys.stdin.buffer.readline(16384))
receipt = payload["runtime"]
raw_lock = pathlib.Path(payload.get("lock", "/opt/requirements-ci.lock")).read_bytes()
if (".".join(map(str, sys.version_info[:3])) != receipt["python_version"]
    or hashlib.sha256(raw_lock).hexdigest() != receipt["lock_sha256"]):
    raise RuntimeError("execution_runtime_mismatch")
for name, version in re.findall(r"^([A-Za-z0-9_.-]+)==([^\s]+)", raw_lock.decode(), re.M):
    if importlib.metadata.version(name) != version:
        raise RuntimeError("execution_runtime_mismatch")
'''

# This code runs only in the inspected candidate container, using the same
# immutable image ID that passed the separate runtime probe. Candidate source
# is data until execve; /workspace and /tmp are disposable tmpfs, never mounts.
# No control result is parsed from stdout: the daemon supplies State.ExitCode.
_CONTAINER_DRIVER = r'''
import os
try:
    import base64, json, pathlib, resource, sys
    payload = json.loads(sys.stdin.buffer.readline(100 * 1024 * 1024))
    for item in payload["files"]:
        rel = pathlib.PurePosixPath(item["path"])
        if rel.is_absolute() or any(p in ("..", ".") for p in rel.parts):
            raise RuntimeError("snapshot_path_invalid")
        path = pathlib.Path("/workspace") / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(base64.b64decode(item["data"], validate=True))
    pathlib.Path("/tmp/home").mkdir()
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    os.chdir("/workspace")
    with open(os.devnull, "rb") as null:
        os.dup2(null.fileno(), 0)
    os.execvpe(payload["argv"][0], payload["argv"],
        {"PATH":"/opt/venv/bin:/usr/local/bin:/usr/bin:/bin", "HOME":"/tmp/home",
         "LANG":"C.UTF-8", "LC_ALL":"C.UTF-8", "PYTHONDONTWRITEBYTECODE":"1"})
except BaseException:
    os._exit(125)
'''


def _bounded_process(argv: list[str], *, data: bytes = b"", timeout: float = 20,
                     limit: int = MAX_OUTPUT_BYTES) -> tuple[int, bytes, bytes]:
    """Drain both pipes while feeding stdin; abort before unbounded host output."""
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=_clean_env())
    except OSError as exc:
        raise ExecutionRefused("execution_runtime_unavailable", "محرك الحاوية غير متاح") from exc
    output = {"stdout": bytearray(), "stderr": bytearray()}
    position = 0
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, label)
            if data:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()
            while selector.get_map():
                if time.monotonic() >= deadline:
                    _refuse("execution_timeout", "انقضت مهلة التنفيذ المعزول")
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    if key.data == "stdin":
                        try:
                            position += os.write(key.fd, data[position:position + 65536])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            position = len(data)
                        if position == len(data):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                    else:
                        try:
                            raw = os.read(key.fd, 65536)
                        except BlockingIOError:
                            continue
                        if not raw:
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        else:
                            output[key.data].extend(raw)
                            if sum(map(len, output.values())) > limit:
                                _refuse("execution_output_limit", "تجاوز خرج الحاوية الحد المسموح")
            try:
                code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                raise ExecutionRefused("execution_timeout", "انقضت مهلة محرك الحاوية") from exc
            return code, bytes(output["stdout"]), bytes(output["stderr"])
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    boundary: str
    output_truncated: bool = False
    timed_out: bool = False


class DockerExecutionBackend:
    # The image's pinned lock, which the preflight re-checks against the receipt.
    lock_path = "/opt/requirements-ci.lock"

    def __init__(self, receipt_path: Path, workspace_root: Path, snapshot_files: tuple[str, ...],
                 *, docker_executable: str = "/usr/local/bin/docker", snapshot_selector=None):
        self.root = _root(workspace_root)
        root_fd = _open_directory(self.root)
        try:
            self.root_identity = _identity(root_fd)
        finally:
            os.close(root_fd)
        path = Path(receipt_path)
        if not path.is_absolute() or path.is_relative_to(self.root):
            _refuse("execution_receipt_untrusted", "إيصال التشغيل يلزم أن يكون خاصًا وخارج مساحة المرشح")
        try:
            parent_fd = _open_directory(path.parent, forbidden_identity=self.root_identity)
            try:
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                        or info.st_mode & 0o077 or info.st_size > 16384):
                    _refuse("execution_receipt_untrusted", "إيصال التشغيل ليس ملفًا خاصًا مملوكًا للمشغّل")
                raw = stream.read(16385)
                if len(raw) > 16384:
                    _refuse("execution_receipt_untrusted", "إيصال التشغيل تجاوز سقف الحجم")
                receipt = json.loads(raw)
        except ExecutionRefused:
            raise
        except (OSError, ValueError) as exc:
            raise ExecutionRefused("execution_receipt_invalid", "إيصال التشغيل غير صالح") from exc
        if (not isinstance(receipt, dict) or receipt.get("schema_version") != 1
                or not IMAGE_ID.fullmatch(str(receipt.get("image_id", "")))
                or not HEX64.fullmatch(str(receipt.get("lock_sha256", "")))
                or not re.fullmatch(r"3\.(?:1[2-9]|[2-9][0-9])\.\d+", str(receipt.get("python_version", "")))):
            _refuse("execution_receipt_invalid", "هوية صورة التشغيل أو نسختها غير صالحة")
        if not Path(docker_executable).is_absolute():
            _refuse("execution_runtime_invalid", "مسار Docker الموثوق يجب أن يكون مطلقًا")
        names = _snapshot_files(snapshot_files)
        if snapshot_selector is not None and not callable(snapshot_selector):
            _refuse("execution_snapshot_invalid", "منتقي ملفات bootstrap يجب أن يكون دالة موثوقة")
        self.receipt = {key: receipt[key] for key in ("image_id", "lock_sha256", "python_version")}
        self.files = names
        self.docker = docker_executable
        self.snapshot_selector = snapshot_selector

    def _docker(self, *args: str, data=b"", timeout=20, limit=MAX_OUTPUT_BYTES):
        return _bounded_process([self.docker, *args], data=data, timeout=timeout, limit=limit)

    def _selected_files(self):
        return _snapshot_files(self.files if self.snapshot_selector is None
                               else self.snapshot_selector(self.root))

    def _snapshot(self, *, selected=None) -> list[dict]:
        files, size = [], 0
        names = self._selected_files() if selected is None else _snapshot_files(selected)
        try:
            fd = _open_directory(self.root)
        except OSError as exc:
            raise ExecutionRefused("execution_workspace_invalid", "تغير مسار مساحة التنفيذ") from exc
        try:
            if _identity(fd) != self.root_identity:
                _refuse("execution_workspace_changed", "تغيرت هوية مساحة التنفيذ منذ ضبط المنفذ")
            for name in names:
                raw = _read_snapshot_file(self.root, name, root_fd=fd)
                size += len(raw)
                if size > MAX_SNAPSHOT_BYTES:
                    _refuse("execution_snapshot_too_large", "لقطة التنفيذ تجاوزت سقف الحجم")
                files.append({"path": name, "data": base64.b64encode(raw).decode("ascii")})
        finally:
            os.close(fd)
        return files

    def _verify_image(self):
        code, raw, _ = self._docker("image", "inspect", self.receipt["image_id"])
        try:
            image = json.loads(raw)[0] if code == 0 else {}
            config = image.get("Config") or {}
            if (image.get("Id") != self.receipt["image_id"] or image.get("Os") != "linux"
                    or (config.get("Labels") or {}).get("diwan.lock-sha256") != self.receipt["lock_sha256"]
                    or config.get("Volumes")):
                raise ValueError("image mismatch")
        except (ValueError, IndexError, TypeError, AttributeError):
            _refuse("execution_runtime_mismatch", "الصورة المحلية لا تطابق إيصال التشغيل المعتمد")

    def _create_args(self, name: str, driver: str) -> list[str]:
        args = ["create", "--pull=never", "--name", name, "--interactive", "--network=none",
                "--read-only", "--user=1000:1000", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--pids-limit=128", f"--memory={MEMORY_BYTES}", f"--memory-swap={MEMORY_BYTES}",
                "--cpus=2", "--ipc=private", "--cgroupns=private", "--log-driver=none", "--workdir=/workspace",
                "--entrypoint=/opt/venv/bin/python"]
        for path, options in TMPFS.items():
            args.extend(("--tmpfs", f"{path}:{options}"))
        return [*args, self.receipt["image_id"], "-I", "-c", driver]

    def _verify_container(self, container_id: str, name: str, driver: str):
        code, raw, _ = self._docker("inspect", container_id)
        try:
            info = json.loads(raw)[0] if code == 0 else {}
            host, config = info.get("HostConfig") or {}, info.get("Config") or {}
            safe = (info.get("Id") == container_id and info.get("Name") == "/" + name
                and info.get("Image") == self.receipt["image_id"] and not (info.get("State") or {}).get("Running")
                and config.get("User") == "1000:1000" and host.get("NetworkMode") == "none"
                and config.get("Entrypoint") == ["/opt/venv/bin/python"]
                and config.get("Cmd") == ["-I", "-c", driver] and config.get("WorkingDir") == "/workspace"
                and host.get("ReadonlyRootfs") is True and host.get("Privileged") is False
                and set(host.get("CapDrop") or ()) == {"ALL"} and not host.get("CapAdd")
                and set(host.get("SecurityOpt") or ()) == {"no-new-privileges"}
                and host.get("PidsLimit") == 128 and host.get("Memory") == MEMORY_BYTES
                and host.get("MemorySwap") == MEMORY_BYTES and host.get("NanoCpus") == 2_000_000_000
                and host.get("IpcMode") == "private" and not host.get("PidMode") and not host.get("UTSMode")
                and host.get("CgroupnsMode") == "private" and not host.get("UsernsMode")
                and (host.get("LogConfig") or {}).get("Type") == "none"
                and host.get("Tmpfs") == TMPFS and not host.get("Binds") and not host.get("Mounts")
                and not host.get("Devices") and not host.get("DeviceRequests") and not host.get("VolumesFrom")
                and not host.get("DeviceCgroupRules") and not host.get("PortBindings")
                and not config.get("Volumes")
                and all(m.get("Type") == "tmpfs" and m.get("Destination") in TMPFS for m in info.get("Mounts", [])))
            if not safe:
                raise ValueError("container boundary mismatch")
        except (ValueError, IndexError, TypeError, AttributeError):
            _refuse("execution_boundary_unverified", "لم تثبت ضوابط الحاوية؛ لم يُرسل كود المرشح")

    def _dispose(self, name: str) -> bool:
        try:
            code, _, _ = self._docker("rm", "--force", name)
            if code == 0:
                return True
            code, raw, _ = self._docker("ps", "--all", "--filter", f"name=^/{name}$", "--format", "{{.ID}}")
            return code == 0 and not raw.strip()
        except ExecutionRefused:
            return False

    def _exit_code(self, container_id: str, name: str) -> int:
        code, raw, _ = self._docker("inspect", container_id)
        try:
            info = json.loads(raw)[0] if code == 0 else {}
            state = info.get("State") or {}
            exit_code = state.get("ExitCode")
            if (info.get("Id") != container_id or info.get("Name") != "/" + name
                    or state.get("Running") is not False or state.get("Status") != "exited"
                    or type(exit_code) is not int or not 0 <= exit_code <= 255
                    or state.get("Error") or state.get("OOMKilled")):
                raise ValueError("container exit unverified")
            return exit_code
        except (ValueError, IndexError, TypeError, AttributeError):
            _refuse("execution_exit_unverified", "لم تثبت حالة خروج الحاوية من محرك Docker")

    def _run_container_raw(self, driver: str, payload: dict, *, timeout_s: float,
                           output_limit: int | None = None) -> tuple[int, bytes, bytes, str]:
        """Run one fixed bootstrap and always verify disposal before returning.

        Returns the daemon-attested exit code, the raw output and the boundary.
        """
        name = "diwan-exec-" + uuid.uuid4().hex
        try:
            code, raw, _ = self._docker(*self._create_args(name, driver))
            container_id = raw.decode("ascii", "replace").strip()
            if code != 0 or not HEX64.fullmatch(container_id):
                _refuse("execution_container_unavailable", "تعذر إنشاء حاوية تنفيذ زائلة")
            self._verify_container(container_id, name, driver)
            data = json.dumps(payload, ensure_ascii=True).encode() + b"\n"
            start = ("start", "--attach", "--interactive", container_id)
            _, stdout, stderr = (self._docker(*start, data=data, timeout=timeout_s) if output_limit is None
                                 else self._docker(*start, data=data, timeout=timeout_s, limit=output_limit))
            # stdout is candidate-controlled. Only the daemon attests process exit.
            return self._exit_code(container_id, name), stdout, stderr, "docker:" + container_id
        finally:
            if not self._dispose(name):
                _refuse("execution_cleanup_unverified", "تعذر إثبات إزالة حاوية التنفيذ")

    def _run_container(self, driver: str, payload: dict, *, timeout_s: float) -> ExecutionResult:
        exit_code, stdout, stderr, boundary = self._run_container_raw(driver, payload, timeout_s=timeout_s)
        return ExecutionResult(exit_code, stdout[-16384:].decode("utf-8", "replace"),
            stderr[-16384:].decode("utf-8", "replace"), boundary,
            output_truncated=len(stdout) > 16384 or len(stderr) > 16384)

    def _preflight(self):
        """The image and its pinned lock, checked before any candidate bytes are sent."""
        self._verify_image()
        payload = {"runtime": self.receipt}
        if self.lock_path != DockerExecutionBackend.lock_path:
            payload["lock"] = self.lock_path
        preflight = self._run_container(_RUNTIME_PREFLIGHT, payload, timeout_s=20)
        if preflight.exit_code != 0:
            _refuse("execution_runtime_mismatch", "لم تجتز صورة التشغيل تحقق Python والقفل والاعتماديات")

    def run(self, argv: tuple[str, ...], *, timeout_s: float) -> ExecutionResult:
        if (not isinstance(argv, tuple) or not 1 <= len(argv) <= 64
                or any(not isinstance(arg, str) or "\x00" in arg or len(arg) > 32768 for arg in argv)
                or not argv[0] or type(timeout_s) not in (float, int)
                or not 0 < timeout_s <= MAX_TIMEOUT_S):
            _refuse("execution_arguments_invalid", "أمر أو مهلة تنفيذ غير صالحة")
        frozen = _FROZEN_INPUTS.get()
        files = self._snapshot() if frozen is None else _validated_frozen(self, frozen)
        self._preflight()
        result = self._run_container(_CONTAINER_DRIVER, {"argv": argv, "files": files}, timeout_s=timeout_s)
        # 125 is intentionally ambiguous: a bootstrap error or an explicit
        # candidate exit. It never claims a measured model failure or success.
        # This is conservative classification, not an authenticated stage signal.
        if result.exit_code == 125:
            _refuse("execution_outcome_unverified", "خروج ملتبس: تعذر إثبات بدء المرشح بعد تهيئة الحاوية")
        return result


def configure_execution_backend(receipt_path: Path, workspace_root: Path,
                                snapshot_files: tuple[str, ...] = (), *, snapshot_selector=None,
                                docker_executable: str | None = None) -> DockerExecutionBackend:
    """Trusted startup only; never expose this function as a model tool."""
    options = {} if docker_executable is None else {"docker_executable": docker_executable}
    backend = DockerExecutionBackend(receipt_path, workspace_root, snapshot_files,
                                     snapshot_selector=snapshot_selector, **options)
    _BACKENDS[backend.root] = backend
    return backend


def release_execution_backend(workspace_root: Path) -> None:
    """Trusted teardown only: the workspace loses its container executor (a disposable benchmark task)."""
    _BACKENDS.pop(Path(workspace_root), None)


def execute_candidate(argv: tuple[str, ...], workspace_root, *, timeout_s: float = 30) -> ExecutionResult:
    if not _BACKENDS:
        _refuse("execution_backend_unavailable", "لم يُضبط منفذ حاوية موثوق؛ لا تنفيذ على المضيف")
    backend = _BACKENDS.get(_root(workspace_root))
    if backend is None:
        _refuse("execution_backend_unavailable", "لا منفذ حاوية موثوق لهذه المساحة")
    return backend.run(argv, timeout_s=timeout_s)


def _configuration(backend):
    return {"workspace": str(backend.root),
            "identity": [str(value) for value in backend.root_identity],
            "receipt": dict(backend.receipt), "snapshot_files": list(backend.files),
            "docker_executable": backend.docker}


def freeze_execution_inputs(workspace_root) -> dict:
    """Trusted action preparation: copy approved bytes without running Docker."""
    backend = _BACKENDS.get(_root(workspace_root))
    if backend is None:
        _refuse("execution_backend_unavailable", "لا منفذ حاوية موثوق لهذه المساحة")
    selected = backend._selected_files()
    return {"configuration": _configuration(backend), "approved_paths": list(selected),
            "files": backend._snapshot(selected=selected)}


def _validated_frozen(backend, frozen):
    if (not isinstance(frozen, dict) or set(frozen) != {"configuration", "approved_paths", "files"}
            or frozen["configuration"] != _configuration(backend)
            or not isinstance(frozen["approved_paths"], list)
            or not isinstance(frozen["files"], list)
            or len(frozen["files"]) != len(frozen["approved_paths"])):
        _refuse("execution_prepared_mismatch", "تغير إعداد التنفيذ منذ تثبيت مدخلات الفعل")
    approved = _snapshot_files(tuple(frozen["approved_paths"]))
    size = 0
    for item, expected in zip(frozen["files"], approved):
        if (not isinstance(item, dict) or set(item) != {"path", "data"}
                or item["path"] != expected or not isinstance(item["data"], str)):
            _refuse("execution_prepared_mismatch", "لقطة الفعل لا تطابق ملفات التنفيذ المعلنة")
        try:
            raw = base64.b64decode(item["data"], validate=True)
        except ValueError:
            _refuse("execution_prepared_mismatch", "بايتات لقطة الفعل غير صالحة")
        size += len(raw)
        if len(raw) > MAX_FILE_BYTES or size > MAX_SNAPSHOT_BYTES:
            _refuse("execution_snapshot_too_large", "لقطة التنفيذ المثبتة تجاوزت الحد")
    return frozen["files"]


@contextmanager
def frozen_execution_inputs(workspace_root, frozen):
    """A prepared call consumes exactly its stored bytes, never a fresh snapshot.

    This trusted Python API is not a tool and does not grant owner consent.
    ContextVar keeps concurrent sessions from sharing an input override.
    """
    backend = _BACKENDS.get(_root(workspace_root))
    if backend is None:
        _refuse("execution_backend_unavailable", "لا منفذ حاوية موثوق لهذه المساحة")
    copied = json.loads(json.dumps(frozen, ensure_ascii=True, allow_nan=False))
    _validated_frozen(backend, copied)
    token = _FROZEN_INPUTS.set(copied)
    try:
        yield
    finally:
        _FROZEN_INPUTS.reset(token)
