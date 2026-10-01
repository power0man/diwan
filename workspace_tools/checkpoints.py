"""Offline public-data checkpoint prototype; no network adapter or UI integration.

The adapter contract is stronger than a mounted object bucket: one exclusive,
fenced lifecycle lease covers publication AND restore, immutable objects have
read-after-write consistency, and the head has durable compare-and-swap. The
test adapter proves this protocol only, not any cloud provider's durability.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile
from typing import ContextManager, Protocol

from core.canonical import canonical_bytes, digest
from memory.store import MemoryStore
from workspace_tools import backup

MAX_CHECKPOINTS = 1024


class CheckpointStore(Protocol):
    """One trusted namespace per workspace; no untrusted keys or bucket mounts.

    exclusive() must serialize all readers/writers of this namespace, remain
    fenced until context exit, and fail if its lease cannot be maintained.
    Methods must be bounded; get raises on absence, never returns stale data.
    Successful puts/CAS acknowledge durable storage, not a background queue.
    """
    def exclusive(self) -> ContextManager[None]: ...
    def read_head(self) -> bytes | None: ...
    def is_pristine(self) -> bool: ...  # Strong empty-namespace check, under the lease.
    def get(self, key: str) -> bytes: ...
    def put_immutable(self, key: str, value: bytes) -> None: ...
    def compare_and_swap_head(self, expected: bytes | None, value: bytes) -> bool: ...


class CheckpointError(ValueError):
    def __init__(self, code, *, head_may_have_advanced=False):
        self.code = code
        self.head_may_have_advanced = head_may_have_advanced
        super().__init__(code)  # Never expose adapter errors, paths, or payloads.


def _need(condition, code):
    if not condition:
        raise CheckpointError(code)


@contextmanager
def _storage(state=None):
    try:
        yield
    except (CheckpointError, backup.BackupError):
        raise
    except Exception:
        if state is not None and state.get("committing"):
            raise CheckpointError("checkpoint_commit_uncertain", head_may_have_advanced=True) from None
        raise CheckpointError("checkpoint_store_failure") from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _policy_pairs(pairs):
    # The decoder visits every nested object, including duplicate keys. A later
    # public label must never hide an earlier local_only value in the same JSON.
    for key, value in pairs:
        if key == "data_policy":
            _need(value == "public", "checkpoint_policy_refused")
    return dict(pairs)


def _policy_json(raw):
    try:
        json.loads(raw, object_pairs_hook=_policy_pairs)
        return True
    except (json.JSONDecodeError, UnicodeError):
        return False
    except CheckpointError:
        raise
    except (ValueError, RecursionError):
        raise CheckpointError("checkpoint_policy_unverifiable") from None


def _content(data, shape):
    # Existing session contracts are local-only or have no cloud classification.
    # Do not reinterpret them, including an empty agent or an archived session.
    _need(not shape[0] and not any(shape[4]), "checkpoint_session_policy_refused")
    for raw in data.values():
        if not _policy_json(raw):
            for line in raw.splitlines():
                _policy_json(line)
    tombstones = []
    for memory in shape[3]:
        _, receipts = MemoryStore.check_snapshot(backup._memory_snapshot(data, memory))
        identities = {}
        for receipt in receipts:
            _need(receipt["item_id"] not in identities or identities[receipt["item_id"]] == receipt["sha256"],
                  "checkpoint_conflicting_tombstones")
            identities[receipt["item_id"]] = receipt["sha256"]
        tombstones.extend(memory + ":" + digest(receipt) for receipt in receipts)
    return set(tombstones)


def _admit(bundle, approval):
    _, data, _, _, shape = backup._guard(backup._validate_bundle)(bundle)
    tombstones = _content(data, shape)
    raw = canonical_bytes(bundle)
    _need(type(approval) is dict and set(approval) == {"archive_sha256", "files"}
          and approval["archive_sha256"] == _sha(raw), "checkpoint_approval_required")
    grants = approval["files"]
    _need(type(grants) is dict and set(grants) == set(data), "checkpoint_approval_required")
    for path, payload in data.items():
        grant = grants[path]
        _need(type(grant) is dict and set(grant) == {"sha256", "data_policy"}
              and grant["sha256"] == _sha(payload), "checkpoint_approval_required")
        _need(grant["data_policy"] == "public", "checkpoint_policy_refused")
    return raw, tombstones


def _receipt(raw):
    _need(type(raw) is bytes and len(raw) <= 4096, "checkpoint_receipt_invalid")
    try:
        value = json.loads(raw)
        _need(type(value) is dict and set(value) == {
            "schema_version", "kind", "sequence", "previous", "archive_sha256", "tombstones_sha256"}
            and type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["kind"] == "diwan_public_checkpoint"
            and type(value["sequence"]) is int and 1 <= value["sequence"] <= MAX_CHECKPOINTS
            and all(type(value[k]) is str and backup._SHA.fullmatch(value[k])
                    for k in ("archive_sha256", "tombstones_sha256"))
            and ((value["sequence"] == 1 and value["previous"] is None)
                 or (value["sequence"] > 1 and type(value["previous"]) is str
                     and backup._SHA.fullmatch(value["previous"])))
            and canonical_bytes(value) == raw, "checkpoint_receipt_invalid")
    except (ValueError, TypeError, RecursionError):
        raise CheckpointError("checkpoint_receipt_invalid") from None
    return value


def _object(store, kind, sha256):
    raw = store.get(kind + "/" + sha256)
    limit = backup.MAX_ARCHIVE_BYTES if kind == "archives" else 4096
    _need(type(raw) is bytes and len(raw) <= limit and _sha(raw) == sha256,
          "checkpoint_object_corrupt")
    return raw


def _archive(store, receipt, temporary, name):
    raw = _object(store, "archives", receipt["archive_sha256"])
    path = temporary / name
    path.write_bytes(raw)
    path.chmod(0o600)
    bundle = backup._guard(backup._read_archive)(path, receipt["archive_sha256"])
    _, data, _, _, shape = backup._guard(backup._validate_bundle)(bundle)
    tombstones = _content(data, shape)
    _need(digest(sorted(tombstones)) == receipt["tombstones_sha256"],
          "checkpoint_tombstones_corrupt")
    return path, tombstones


def commit_checkpoint(archive, expected_sha256, approval, store: CheckpointStore):
    """Commit an already exported, offline archive; default-deny per-file grants.

    No provider method is reached before local-only/unclassified content is
    refused. Approval binds every file AND all archive metadata to exact bytes;
    it is a trusted caller decision, not an automatic data classifier.
    """
    bundle = backup._guard(backup._read_archive)(archive, expected_sha256)
    raw, tombstones = _admit(bundle, approval)
    state = {}
    with _storage(state), store.exclusive(), tempfile.TemporaryDirectory(prefix="diwan-checkpoint-") as tmp:
        previous = store.read_head()
        if previous is None:
            _need(store.is_pristine(), "checkpoint_head_missing")
        head = None if previous is None else _receipt(previous)
        if head is not None:
            _, prior = _archive(store, head, Path(tmp).resolve(), "previous.json")
            _need(prior <= tombstones, "checkpoint_forget_regression")
        sequence = 1 if head is None else head["sequence"] + 1
        _need(sequence <= MAX_CHECKPOINTS, "checkpoint_history_limit")
        receipt = canonical_bytes({"schema_version": 1, "kind": "diwan_public_checkpoint",
            "sequence": sequence, "previous": None if previous is None else _sha(previous),
            "archive_sha256": expected_sha256, "tombstones_sha256": digest(sorted(tombstones))})
        for kind, payload in (("archives", raw), ("receipts", receipt)):
            sha = _sha(payload)
            store.put_immutable(kind + "/" + sha, payload)
            _need(_object(store, kind, sha) == payload, "checkpoint_write_unverified")
        # Any failure once CAS starts has an uncertain outcome, not a false
        # promise that the previous head is intact. Reconcile before retrying.
        try:
            state["committing"] = True
            _need(store.compare_and_swap_head(previous, receipt), "checkpoint_head_conflict")
            _need(store.read_head() == receipt, "checkpoint_head_unverified")
        except Exception:
            raise CheckpointError("checkpoint_commit_uncertain", head_may_have_advanced=True) from None
        return {"status": "committed", "receipt_sha256": _sha(receipt), **json.loads(receipt)}


def restore_checkpoint(store: CheckpointStore, destination, *, receipt_sha256=None):
    """Restore an ancestor using the LATEST committed forget receipts, never None.

    The namespace lease excludes a concurrent forget checkpoint until restore
    finishes. This does not sync a running app: the lifecycle owner must keep
    that app closed and install the restored workspace as its single writer.
    """
    with _storage(), store.exclusive(), tempfile.TemporaryDirectory(prefix="diwan-checkpoint-") as tmp:
        latest_raw = store.read_head()
        _need(latest_raw is not None, "checkpoint_head_missing")
        latest = _receipt(latest_raw)
        current_sha = _sha(latest_raw)
        selected = latest
        if receipt_sha256 is not None:
            _need(type(receipt_sha256) is str and backup._SHA.fullmatch(receipt_sha256),
                  "checkpoint_receipt_invalid")
            while current_sha != receipt_sha256:
                previous = selected["previous"]
                _need(previous is not None, "checkpoint_not_ancestor")
                ancestor = _receipt(_object(store, "receipts", previous))
                _need(ancestor["sequence"] == selected["sequence"] - 1, "checkpoint_chain_invalid")
                selected, current_sha = ancestor, previous
        temporary = Path(tmp).resolve()
        latest_path, _ = _archive(store, latest, temporary, "latest.json")
        selected_path = latest_path if selected == latest else _archive(store, selected, temporary, "selected.json")[0]
        authority = temporary / "forget-authority"
        backup.restore_workspace(latest_path, authority, latest["archive_sha256"], tombstones_from=None)
        result = backup.restore_workspace(selected_path, destination, selected["archive_sha256"],
                                          tombstones_from=authority)
        return {"status": "restored", "receipt_sha256": current_sha,
                "forget_authority_sha256": _sha(latest_raw), "backup": result}
