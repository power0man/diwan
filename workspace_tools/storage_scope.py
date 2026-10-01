"""Explicit creation of synthetic private-cloud workspaces; no remote IO.

The trusted operator owns the creation authority and must use the same authority
for every workspace targeting a store. A backend must additionally refuse a
non-pristine remote namespace. These receipts are not cloud durability evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import uuid

from core.canonical import canonical_bytes, digest
from workspace_tools.files import (_canonical_root, _open_directory, _private,
                                   _read_file, _write_json)

MANIFEST = ".cloud-workspace.json"
MAX_MANIFEST_BYTES = 8192
_ID = re.compile(r"[a-f0-9]{32}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+\Z")
_BRANCH = re.compile(r"diwan-checkpoint-[a-z0-9-]{1,80}\Z")


class StorageScopeError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code="cloud_scope_invalid"):
    if not condition:
        raise StorageScopeError(code)


def _exists(fd, name):
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _target(value, *, namespace):
    fields = {"provider", "repo_type", "repo_id", "branch"}
    _need(type(value) is dict and set(value) == fields | ({"namespace"} if namespace else set()))
    _need(value["provider"] == "hf_hub" and value["repo_type"] in ("dataset", "space"))
    _need(type(value["repo_id"]) is str and _REPOSITORY.fullmatch(value["repo_id"]))
    _need(type(value["branch"]) is str and _BRANCH.fullmatch(value["branch"]))
    if namespace:
        _need(type(value["namespace"]) is str and _ID.fullmatch(value["namespace"]))


def _plan(plan):
    _need(type(plan) is dict and set(plan) == {"schema_version", "kind", "workspace_id",
          "data_policy", "inference_policy", "content_scope", "target"})
    _need(type(plan["schema_version"]) is int and plan["schema_version"] == 1
          and plan["kind"] == "new_private_cloud_workspace"
          and plan["data_policy"] == "internal" and plan["inference_policy"] == "local_only"
          and plan["content_scope"] == "synthetic_only"
          and type(plan["workspace_id"]) is str and _ID.fullmatch(plan["workspace_id"]))
    _target(plan["target"], namespace=True)
    return plan


def plan_cloud_workspace(target):
    """Return a fresh proposal. Approval is a separate trusted operator action."""
    _target(target, namespace=False)
    return {"schema_version": 1, "kind": "new_private_cloud_workspace",
            "workspace_id": uuid.uuid4().hex, "data_policy": "internal",
            "inference_policy": "local_only", "content_scope": "synthetic_only",
            "target": {**target, "namespace": uuid.uuid4().hex}}


def _approved(plan, approval):
    _plan(plan)
    _need(type(approval) is dict and set(approval) == {"owner_approved", "plan_sha256"}
          and approval["owner_approved"] is True
          and approval["plan_sha256"] == digest(plan), "cloud_approval_required")
    return {"plan": plan, "approval": approval}


def decode_scope(raw):
    """Strict byte contract also used by offline archive validation."""
    _need(type(raw) is bytes and len(raw) <= MAX_MANIFEST_BYTES)
    try:
        value = json.loads(raw)
        _need(type(value) is dict and set(value) == {"record", "sha256"})
        record = value["record"]
        _need(type(record) is dict and set(record) == {"plan", "approval"})
        _approved(record["plan"], record["approval"])
        _need(value["sha256"] == digest(record) and canonical_bytes(value) == raw)
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, StorageScopeError):
            raise
        raise StorageScopeError("cloud_scope_invalid") from None
    return value


@dataclass(frozen=True)
class StorageScope:
    root: Path
    raw: bytes

    def binding(self, path):
        # Every use rechecks the immutable approval; losing it never downgrades
        # a live cloud session to a legacy local session.
        current = read_storage_scope(self.root)
        _need(current is not None and current.raw == self.raw, "cloud_scope_changed")
        path = Path(path).absolute()
        _need(".." not in path.parts and self.root in path.parents, "cloud_scope_path_conflict")
        value = decode_scope(self.raw)
        return {"workspace_id": value["record"]["plan"]["workspace_id"],
                "contract_sha256": value["sha256"]}


def read_storage_scope(root):
    """Missing means legacy/local; malformed or unsafe means refusal, never consent."""
    root = _canonical_root(root)
    parent = _open_directory(root.parent)
    try:
        _need(not _exists(parent, _pending_name(root)), "cloud_creation_incomplete")
    finally:
        os.close(parent)
    fd = _open_directory(root)
    try:
        _private(os.fstat(fd), directory=True)
        if not _exists(fd, MANIFEST):
            return None
        found = _read_file(fd, MANIFEST, MAX_MANIFEST_BYTES, private=True)
        raw = found[0]
        decode_scope(raw)
        return StorageScope(root, raw)
    finally:
        os.close(fd)


def _pending_name(destination):
    return ".cloud-create-" + digest(destination.name) + ".pending"


def create_cloud_workspace(destination, plan, *, approval, claims_root):
    """Create only a fresh child of a private creation authority.

    claims_root is operator-owned and shared across all creations for a target.
    Claims survive a failed creation or deletion of its workspace. No rollback
    removes them: reconciling a failed creation is an explicit future operation.
    """
    record = _approved(plan, approval)
    destination, authority = _canonical_root(destination), _canonical_root(claims_root)
    _need(destination.parent == authority and not destination.name.startswith("."),
          "cloud_creation_authority_required")
    fd = _open_directory(authority)
    try:
        _private(os.fstat(fd), directory=True)
        _need(not _exists(fd, destination.name), "cloud_destination_exists")
        # Hub CAS fences the whole branch: a second namespace on the same branch
        # is not a second independent workspace. Reserve the branch as well.
        branch_target = {key: value for key, value in plan["target"].items() if key != "namespace"}
        name = ".cloud-claim-" + digest(branch_target) + ".json"
        try:
            claim_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                               0o600, dir_fd=fd)
        except FileExistsError:
            raise StorageScopeError("cloud_namespace_used") from None
        with os.fdopen(claim_fd, "wb") as stream:
            stream.write(canonical_bytes({"plan_sha256": digest(plan), "destination": destination.name}))
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
        pending = os.open(_pending_name(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o600, dir_fd=fd)
        os.fsync(pending)
        os.close(pending)
        os.fsync(fd)
        try:
            os.mkdir(destination.name, 0o700, dir_fd=fd)
        except FileExistsError:
            raise StorageScopeError("cloud_destination_exists") from None
        os.fsync(fd)
        child = _open_directory(destination)
        try:
            _write_json(child, MANIFEST, {"record": record, "sha256": digest(record)})
        finally:
            os.close(child)
        os.unlink(_pending_name(destination), dir_fd=fd)
        os.fsync(fd)
    finally:
        os.close(fd)
    return read_storage_scope(destination)


def session_storage(scope, path):
    if scope is None:
        return {}
    _need(type(scope) is StorageScope, "cloud_scope_invalid")
    return {"data_policy": "internal", "storage": scope.binding(path)}
