"""Trusted, read-only Git extraction and offline container execution for the gate.

No candidate Python is imported on the host. This module belongs to the private
installed tool bundle, not to the revision being checked.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import time
import unicodedata
import uuid
from types import SimpleNamespace


class GateError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def clean_env() -> dict[str, str]:
    # Do not forward owner credentials, Python hooks, Git routing, or Docker
    # configuration into subprocesses. No environment is passed to containers.
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
            "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"}


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    executable = shutil.which("git", path=clean_env()["PATH"])
    if not executable:
        raise GateError("git_missing")
    try:
        result = subprocess.run(
            [executable, "-C", str(repo), "-c", "core.hooksPath=/dev/null", *args],
            input=data, capture_output=True, env=clean_env(), timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateError("git_unavailable") from exc
    if result.returncode:
        raise GateError("git_read_failed")
    return result.stdout


def parse_updates(raw: str, oid_size: int, *, allow_delete: bool = False) -> list[dict]:
    updates = []
    seen = set()
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) != 4:
            raise GateError("push_input_invalid")
        local_ref, oid, remote_ref, remote_oid = fields
        if (not re.fullmatch(r"[0-9a-f]{" + str(oid_size) + r"}", oid)
                or not re.fullmatch(r"[0-9a-f]{" + str(oid_size) + r"}", remote_oid)
                or not remote_ref.startswith("refs/") or remote_ref in seen):
            raise GateError("push_input_invalid")
        seen.add(remote_ref)
        if oid == "0" * oid_size and not allow_delete:
            raise GateError("push_delete_refused")
        updates.append({"local_ref": local_ref, "local_oid": oid,
                        "remote_ref": remote_ref, "remote_oid": remote_oid})
    return updates


def commit_oid(repo: Path, oid: str, oid_size: int) -> str:
    if not re.fullmatch(r"[0-9a-f]{" + str(oid_size) + r"}", oid):
        raise GateError("commit_oid_required")
    try:
        value = git(repo, "rev-parse", "--verify", oid + "^{commit}").decode().strip()
    except GateError as exc:
        raise GateError("push_object_not_commit") from exc
    if not re.fullmatch(r"[0-9a-f]{" + str(oid_size) + r"}", value):
        raise GateError("push_object_not_commit")
    return value


def materialize(repo: Path, commit: str, destination: Path) -> None:
    """Copy raw blobs, including export-ignore files, without filters/hardlinks.

    Unsupported symlinks/submodules and filesystem aliases fail closed. Ref names
    are never read here: commit is the already frozen peeled object identifier.
    """
    entries = git(repo, "ls-tree", "-rzl", "--full-tree", commit).split(b"\0")
    parsed = []
    aliases = set()
    directory_aliases = {}
    expected_total = 0
    for entry in filter(None, entries):
        try:
            header, raw_path = entry.split(b"\t", 1)
            mode, kind, oid, size = header.decode("ascii").split()
            name = raw_path.decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise GateError("tree_entry_invalid") from exc
        parts = PurePosixPath(name).parts
        if (not parts or name.startswith("/") or "\\" in name
                or any(p in ("", ".", "..") or p.casefold() == ".git" for p in parts)
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise GateError("tree_path_invalid")
        alias = unicodedata.normalize("NFD", name).casefold()
        if alias in aliases:
            raise GateError("tree_path_alias")
        aliases.add(alias)
        if mode not in ("100644", "100755") or kind != "blob":
            raise GateError("tree_type_unsupported")
        for count in range(1, len(parts)):
            prefix = "/".join(parts[:count])
            key = unicodedata.normalize("NFD", prefix).casefold()
            if key in directory_aliases and directory_aliases[key] != prefix:
                raise GateError("tree_path_alias")
            directory_aliases[key] = prefix
        expected_total += int(size)
        if expected_total > 1024 * 1024 * 1024:
            raise GateError("tree_too_large")
        parsed.append((name, mode, oid))
    if len(parsed) > 100_000:
        raise GateError("tree_too_large")
    # --batch produces raw object bytes; attributes cannot omit or rewrite them.
    raw = git(repo, "cat-file", "--batch", data=("".join(
        oid + "\n" for _, _, oid in parsed)).encode("ascii"))
    offset = 0
    total = 0
    for name, mode, oid in parsed:
        end = raw.find(b"\n", offset)
        if end < 0:
            raise GateError("blob_read_invalid")
        header = raw[offset:end].decode("ascii").split()
        if len(header) != 3 or header[:2] != [oid, "blob"]:
            raise GateError("blob_read_invalid")
        length = int(header[2])
        total += length
        if total > 1024 * 1024 * 1024:
            raise GateError("tree_too_large")
        start = end + 1
        content = raw[start:start + length]
        if len(content) != length or raw[start + length:start + length + 1] != b"\n":
            raise GateError("blob_read_invalid")
        offset = start + length + 1
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as handle:
                handle.write(content)
            target.chmod(0o755 if mode == "100755" else 0o644)
        except OSError as exc:
            raise GateError("tree_path_alias") from exc
    if offset != len(raw):
        raise GateError("blob_read_invalid")


def outside(path: Path, repo: Path) -> None:
    try:
        path.resolve().relative_to(repo.resolve())
    except ValueError:
        return
    raise GateError("trusted_path_inside_candidate")


def private_json(path: Path, repo: Path) -> tuple[dict, bytes]:
    outside(path, repo)
    # Every component below the first private ancestor must be owned and cannot
    # be a symlink; the immediate private directory is an explicit trust boundary.
    absolute = path.absolute()
    if any(p.is_symlink() for p in (absolute, *absolute.parents)):
        raise GateError("trusted_path_symlink")
    try:
        info = absolute.stat()
        parent = absolute.parent.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600
                or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700):
            raise GateError("trusted_path_permissions")
        raw = absolute.read_bytes()
        value = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise GateError("trusted_configuration_invalid") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise GateError("trusted_configuration_invalid")
    return value, raw


def load_configuration(runtime: Path, trust: Path, repo: Path) -> tuple[dict, dict]:
    receipt, _ = private_json(runtime, repo)
    policy, raw = private_json(trust, repo)
    if (not re.fullmatch(r"sha256:[0-9a-f]{64}", str(receipt.get("image_id", "")))
            or not HEX64.fullmatch(str(receipt.get("lock_sha256", "")))
            or receipt.get("trust_sha256") != digest(raw)
            or not re.fullmatch(r"3\.14\.\d+", str(receipt.get("python_version", "")))
            or receipt.get("node_major") != 24):
        raise GateError("runtime_receipt_invalid")
    if (policy.get("algorithm") != "ED25519"
            or not HEX64.fullmatch(str(policy.get("public_key_hex", "")))
            or not HEX64.fullmatch(str(policy.get("policy_sha256", "")))):
        raise GateError("trust_policy_invalid")
    return receipt, policy


class EvidenceStore:
    """Private file-backed diagnostics, outside candidate data and never a verdict.

    Output is not size-capped: RAM remains bounded, while disk retention is an
    owner operational responsibility. Files are never overwritten or followed.
    """
    def __init__(self, path: Path, candidate: Path):
        self.path = Path(path).absolute()
        if ".." in self.path.parts:
            raise GateError("evidence_path_invalid")
        outside(self.path, candidate)
        self.records = []
        fd = self._open_directory(create=True)
        try:
            self._private(fd)
            self.identity = self._identity(os.fstat(fd))
        finally:
            os.close(fd)

    @staticmethod
    def _identity(info):
        return info.st_dev, info.st_ino

    @staticmethod
    def _private(fd):
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise GateError("evidence_permissions_invalid")

    def _open_directory(self, *, create=False):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self.path.anchor, flags)
        try:
            for part in self.path.parts[1:]:
                if part not in os.listdir(fd):
                    try:
                        os.stat(part, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        if not create:
                            raise GateError("evidence_directory_missing")
                        os.mkdir(part, 0o700, dir_fd=fd)
                        os.fsync(fd)
                    else:
                        raise GateError("evidence_path_alias")
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd
        except BaseException as exc:
            os.close(fd)
            if isinstance(exc, OSError):
                raise GateError("evidence_path_unsafe") from exc
            raise

    def _check(self, fd):
        opened = self._open_directory()
        try:
            self._private(opened)
            if (self._identity(os.fstat(opened)) != self.identity
                    or self._identity(os.fstat(fd)) != self.identity):
                raise GateError("evidence_directory_changed")
        finally:
            os.close(opened)

    @contextmanager
    def capture(self, check_name):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", check_name):
            raise GateError("evidence_check_name_invalid")
        parent = self._open_directory()
        stream = None
        record = None
        try:
            self._check(parent)
            name = check_name + "-" + uuid.uuid4().hex + ".log"
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
            stream = os.fdopen(fd, "w+b")
            record = {"check": check_name, "path": str(self.path / name),
                      "status": "capturing", "streams": "stdout_and_stderr", "truncated": False}
            self.records.append(record)
            # Persist the directory entry before candidate code can write output.
            os.fsync(parent)
            try:
                yield stream, record
            finally:
                try:
                    stream.flush()
                    os.fsync(stream.fileno())
                    self._check(parent)
                    before = os.fstat(stream.fileno())
                    entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1
                            or entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o600
                            or self._identity(entry) != self._identity(before)):
                        raise GateError("evidence_file_changed")
                    stream.seek(0)
                    hashed, size = hashlib.sha256(), 0
                    while chunk := stream.read(1024 * 1024):
                        hashed.update(chunk)
                        size += len(chunk)
                    after = os.fstat(stream.fileno())
                    self._check(parent)
                    final_entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (before.st_size != size or before.st_size != after.st_size
                            or before.st_mtime_ns != after.st_mtime_ns
                            or before.st_ctime_ns != after.st_ctime_ns
                            or self._identity(final_entry) != self._identity(after)):
                        raise GateError("evidence_file_changed")
                    record.update(status="retained", bytes=size, sha256=hashed.hexdigest())
                except (OSError, GateError) as exc:
                    record.update(status="failed", code=getattr(exc, "code", "evidence_io_failed"))
        except OSError as exc:
            if record is not None:
                record.update(status="failed", code="evidence_io_failed")
            raise GateError("evidence_open_failed") from exc
        finally:
            if stream is not None:
                stream.close()
            os.close(parent)


class DockerRunner:
    """Only disposable candidate data is mounted; owner files never enter."""

    def __init__(self, receipt: dict, *, evidence: EvidenceStore | None = None):
        self.receipt = receipt
        self.evidence = evidence
        self.executable = shutil.which("docker", path=clean_env()["PATH"])
        if not self.executable:
            raise GateError("runtime_unavailable")
        try:
            r = subprocess.run([self.executable, "image", "inspect", receipt["image_id"]],
                               capture_output=True, env=clean_env(), timeout=20)
            image = json.loads(r.stdout)[0] if r.returncode == 0 else {}
            if (image.get("Id") != receipt["image_id"] or image.get("Config", {}).get(
                    "Labels", {}).get("diwan.lock-sha256") != receipt["lock_sha256"]):
                raise GateError("runtime_image_mismatch")
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as exc:
            raise GateError("runtime_unavailable") from exc
        # A label is an assertion, not evidence that the image contains the
        # expected runtimes. This trusted inline probe has no candidate mount.
        probe = ("import hashlib,importlib.metadata as m,pathlib,re,subprocess,sys\n"
                 f"expected_python={receipt['python_version']!r}\n"
                 f"expected_lock={receipt['lock_sha256']!r}\n"
                 "raw=pathlib.Path('/opt/requirements-ci.lock').read_bytes()\n"
                 "if '.'.join(map(str,sys.version_info[:3])) != expected_python: raise RuntimeError('python')\n"
                 "if hashlib.sha256(raw).hexdigest() != expected_lock: raise RuntimeError('lock')\n"
                 "for name,version in re.findall(r'^([A-Za-z0-9_.-]+)==([^\\s]+)',raw.decode(),re.M):\n"
                 "    if m.version(name) != version: raise RuntimeError('distribution')\n"
                 "major=subprocess.check_output(['/usr/local/bin/node','-p','process.versions.node.split(\".\")[0]'],text=True).strip()\n"
                 "if major != '24': raise RuntimeError('node')\n")
        result = self.run(None, SimpleNamespace(name="runtime-identity", timeout_s=30,
                         argv=("/opt/venv/bin/python", "-I", "-c", probe)))
        if result["exit_code"] != 0:
            raise GateError("runtime_probe_failed")

    def run(self, root: Path | None, check) -> dict:
        evidence = getattr(self, "evidence", None)
        if evidence is not None and root is not None:
            outside(evidence.path, root)
        name = "diwan-gate-" + uuid.uuid4().hex
        command = [self.executable, "run", "--rm", "--pull=never", "--name", name,
                   "--network=none", "--read-only", "--cap-drop=ALL",
                   "--security-opt=no-new-privileges", "--pids-limit=512",
                   "--memory=6g", "--cpus=4",
                   "--user", f"{os.getuid()}:{os.getgid()}",
                   "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=1g,mode=1777"]
        if root is not None:
            command += ["--mount", f"type=bind,src={root},dst=/workspace"]
        command += ["--workdir", "/workspace", self.receipt["image_id"], *check.argv]
        started = time.monotonic()
        captured, process_exit = None, None
        process_code, cleanup_code = "not_started", "not_needed"

        @contextmanager
        def output_sink():
            if evidence is not None:
                with evidence.capture(check.name) as capture:
                    yield capture
            else:
                import tempfile
                with tempfile.TemporaryFile() as output:
                    yield output, None

        try:
            with output_sink() as (output, captured):
                try:
                    r = subprocess.run(command, stdout=output, stderr=output,
                                       env=clean_env(), timeout=check.timeout_s)
                    process_exit = r.returncode
                    process_code = "passed" if process_exit == 0 else "check_failed"
                    code, exit_code = process_code, process_exit
                except subprocess.TimeoutExpired:
                    process_code, code, exit_code = "check_timeout", "check_timeout", 124
                except OSError:
                    process_code, code, exit_code = "runtime_unavailable", "runtime_unavailable", 125
                finally:
                    # Stop/verify the container before finalizing its output file.
                    try:
                        removed = subprocess.run([self.executable, "rm", "--force", name],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=clean_env(), timeout=20)
                        cleanup_code = "passed"
                        if removed.returncode != 0:
                            remaining = subprocess.run([self.executable, "ps", "-a", "--filter", f"name=^/{name}$",
                                "--format", "{{.ID}}"], capture_output=True, text=True,
                                env=clean_env(), timeout=20)
                            if remaining.returncode != 0 or remaining.stdout.strip():
                                cleanup_code = "runtime_cleanup_failed"
                    except (OSError, subprocess.TimeoutExpired):
                        cleanup_code = "runtime_cleanup_failed"
                    if cleanup_code != "passed":
                        code, exit_code = "runtime_cleanup_failed", 125
        except (GateError, OSError) as exc:
            code, exit_code = getattr(exc, "code", "evidence_io_failed"), 125
        if captured is not None and captured["status"] != "retained" and code == "passed":
            code, exit_code = "evidence_capture_failed", 125
        result = {"name": check.name, "code": code, "exit_code": exit_code,
                  "process_exit_code": process_exit, "process_code": process_code,
                  "cleanup_code": cleanup_code, "duration_s": round(time.monotonic() - started, 3)}
        if captured is not None:
            captured.update(check_code=code, exit_code=exit_code, process_exit_code=process_exit,
                            process_code=process_code, cleanup_code=cleanup_code)
            result["evidence"] = captured
        return result
