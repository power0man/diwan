"""Offline synthetic/internal session checkpoints under an explicit private grant.

Separate from the public checkpoint API. The trusted lifecycle owner closes the
app before export/restore and retains expected_scope outside ephemeral storage.
No UI, classifier, inference, network provisioning, or automatic lease recovery.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import ContextManager

from core.canonical import canonical_bytes, digest
from memory.store import MemoryStore
from workspace_tools import backup, checkpoints as cp
from workspace_tools.storage_scope import MANIFEST, StorageScopeError, checkpoint_scope

MAX_RECEIPT_BYTES = 4096


class SyntheticCheckpointStore(cp.CheckpointStore):
    """Binding is durable, branch-wide, and verified against the actual target.

    Both calls require exclusive(). bind_scope may initialize only a pristine,
    unclaimed branch; verify_scope never initializes. Every data call is fenced
    to the verified binding until lease exit. Returned identity is canonical.
    """
    def exclusive(self, *, require_bound=False) -> ContextManager[None]: ...
    def bind_scope(self, scope_raw: bytes) -> dict: ...
    def verify_scope(self, scope_raw: bytes) -> dict: ...


def _scope(raw):
    try:
        return checkpoint_scope(raw)
    except StorageScopeError:
        raise cp.CheckpointError("checkpoint_scope_invalid") from None


def _policy_pairs(pairs):
    # Visit duplicate keys as well as nested records; local_only never becomes
    # uploadable through an outer contract or a later duplicate internal label.
    for key, value in pairs:
        if key == "data_policy":
            cp._need(value == "internal", "checkpoint_policy_refused")
    return dict(pairs)


def _policy_json(raw):
    try:
        json.loads(raw, object_pairs_hook=_policy_pairs)
        return True
    except (json.JSONDecodeError, UnicodeError):
        return False
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, cp.CheckpointError):
            raise
        raise cp.CheckpointError("checkpoint_policy_unverifiable") from None


def _admit(bundle, expected_scope):
    _, data, _, _, shape = backup._guard(backup._validate_bundle)(bundle)
    cp._need(data.get(MANIFEST) == expected_scope, "checkpoint_scope_mismatch")
    # Backup validates every session's internal policy, contract binding,
    # portable schema, request identities and ledgers. No archived/media mode.
    cp._need(not shape[4][1] and all(mode == "text" for _, _, mode in shape[0]),
             "checkpoint_session_policy_refused")
    cp._need(all(json.loads(data[f"{project}/sessions/{sid}/meta.json"]).get("mode") == "agent"
                 for project, sid in shape[4][0]), "checkpoint_session_policy_refused")
    for path, raw in data.items():
        cp._need("/uploads/" not in path, "checkpoint_import_refused")
        if not _policy_json(raw):
            for line in raw.splitlines():
                _policy_json(line)
    tombstones = []
    for memory in shape[3]:
        _, receipts = MemoryStore.check_snapshot(backup._memory_snapshot(data, memory))
        identities = {}
        for receipt in receipts:
            cp._need(receipt["item_id"] not in identities or identities[receipt["item_id"]] == receipt["sha256"],
                     "checkpoint_conflicting_tombstones")
            identities[receipt["item_id"]] = receipt["sha256"]
        tombstones.extend(memory + ":" + digest(receipt) for receipt in receipts)
    return canonical_bytes(bundle), set(tombstones)


def _receipt(raw, scope):
    cp._need(type(raw) is bytes and len(raw) <= MAX_RECEIPT_BYTES, "checkpoint_receipt_invalid")
    try:
        value = json.loads(raw)
        cp._need(type(value) is dict and set(value) == {"schema_version", "kind", "scope",
                 "sequence", "previous", "archive_sha256", "tombstones_sha256"}
                 and type(value["schema_version"]) is int and value["schema_version"] == 1
                 and value["kind"] == "diwan_synthetic_checkpoint"
                 and value["scope"] == scope
                 and type(value["sequence"]) is int and 1 <= value["sequence"] <= cp.MAX_CHECKPOINTS
                 and all(type(value[k]) is str and backup._SHA.fullmatch(value[k])
                         for k in ("archive_sha256", "tombstones_sha256"))
                 and ((value["sequence"] == 1 and value["previous"] is None)
                      or (value["sequence"] > 1 and type(value["previous"]) is str
                          and backup._SHA.fullmatch(value["previous"])))
                 and canonical_bytes(value) == raw, "checkpoint_receipt_invalid")
    except (ValueError, TypeError, RecursionError):
        raise cp.CheckpointError("checkpoint_receipt_invalid") from None
    return value


def _object(store, kind, sha):
    raw = store.get(kind + "/" + sha)
    limit = backup.MAX_ARCHIVE_BYTES if kind == "archives" else MAX_RECEIPT_BYTES
    cp._need(type(raw) is bytes and len(raw) <= limit and cp._sha(raw) == sha,
             "checkpoint_object_corrupt")
    return raw


def _encode_receipt(scope, sequence, previous, archive_sha256, tombstones_sha256):
    raw = canonical_bytes({"schema_version": 1, "kind": "diwan_synthetic_checkpoint",
        "scope": scope, "sequence": sequence, "previous": previous,
        "archive_sha256": archive_sha256, "tombstones_sha256": tombstones_sha256})
    cp._need(len(raw) <= MAX_RECEIPT_BYTES, "checkpoint_receipt_limit")
    return raw


def _archive(store, receipt, expected_scope, temporary, name):
    raw = _object(store, "archives", receipt["archive_sha256"])
    path = temporary / name
    path.write_bytes(raw)
    path.chmod(0o600)
    bundle = backup._guard(backup._read_archive)(path, receipt["archive_sha256"])
    _, tombstones = _admit(bundle, expected_scope)
    cp._need(digest(sorted(tombstones)) == receipt["tombstones_sha256"],
             "checkpoint_tombstones_corrupt")
    return path, tombstones


def commit_synthetic_checkpoint(archive, expected_sha256, expected_scope: bytes,
                                store: SyntheticCheckpointStore):
    """Commit only bytes already admitted under the same durable private grant."""
    scope = _scope(expected_scope)
    bundle = backup._guard(backup._read_archive)(archive, expected_sha256)
    raw, tombstones = _admit(bundle, expected_scope)
    tombstones_sha = digest(sorted(tombstones))
    # The Hub control head accepts at most 4096 bytes. Bound the largest future
    # receipt before even acquiring/binding the store; a missing head is shorter.
    _encode_receipt(scope, cp.MAX_CHECKPOINTS, "0" * 64, expected_sha256, tombstones_sha)
    state = {}
    with cp._storage(state), store.exclusive(), tempfile.TemporaryDirectory(prefix="diwan-synthetic-") as tmp:
        cp._need(store.bind_scope(expected_scope) == scope, "checkpoint_scope_mismatch")
        previous = store.read_head()
        if previous is None:
            cp._need(store.is_pristine(), "checkpoint_head_missing")
        head = None if previous is None else _receipt(previous, scope)
        if head is not None:
            _, prior = _archive(store, head, expected_scope, Path(tmp).resolve(), "previous.json")
            cp._need(prior <= tombstones, "checkpoint_forget_regression")
        sequence = 1 if head is None else head["sequence"] + 1
        cp._need(sequence <= cp.MAX_CHECKPOINTS, "checkpoint_history_limit")
        receipt = _encode_receipt(scope, sequence, None if previous is None else cp._sha(previous),
                                  expected_sha256, tombstones_sha)
        for kind, payload in (("archives", raw), ("receipts", receipt)):
            sha = cp._sha(payload)
            store.put_immutable(kind + "/" + sha, payload)
            cp._need(_object(store, kind, sha) == payload, "checkpoint_write_unverified")
        try:
            state["committing"] = True
            cp._need(store.compare_and_swap_head(previous, receipt), "checkpoint_head_conflict")
            cp._need(store.read_head() == receipt, "checkpoint_head_unverified")
        except Exception:
            raise cp.CheckpointError("checkpoint_commit_uncertain", head_may_have_advanced=True) from None
        result = {"status": "committed", "receipt_sha256": cp._sha(receipt), **json.loads(receipt)}
    # Context exit may fail after CAS: no successful return before release.
    return result


def restore_synthetic_checkpoint(store: SyntheticCheckpointStore, destination,
                                 expected_scope: bytes, *, receipt_sha256=None):
    """Restore only this scope, using the latest committed forget authority."""
    scope = _scope(expected_scope)
    if receipt_sha256 is not None:
        cp._need(type(receipt_sha256) is str and backup._SHA.fullmatch(receipt_sha256),
                 "checkpoint_receipt_invalid")
    with cp._storage(), store.exclusive(require_bound=True), tempfile.TemporaryDirectory(prefix="diwan-synthetic-") as tmp:
        cp._need(store.verify_scope(expected_scope) == scope, "checkpoint_scope_mismatch")
        latest_raw = store.read_head()
        cp._need(latest_raw is not None, "checkpoint_head_missing")
        latest = _receipt(latest_raw, scope)
        current_sha, selected = cp._sha(latest_raw), latest
        if receipt_sha256 is not None:
            while current_sha != receipt_sha256:
                previous = selected["previous"]
                cp._need(previous is not None, "checkpoint_not_ancestor")
                ancestor = _receipt(_object(store, "receipts", previous), scope)
                cp._need(ancestor["sequence"] == selected["sequence"] - 1, "checkpoint_chain_invalid")
                selected, current_sha = ancestor, previous
        temporary = Path(tmp).resolve()
        latest_path, _ = _archive(store, latest, expected_scope, temporary, "latest.json")
        selected_path = latest_path if selected == latest else _archive(
            store, selected, expected_scope, temporary, "selected.json")[0]
        authority = temporary / "forget-authority"
        backup.restore_workspace(latest_path, authority, latest["archive_sha256"], tombstones_from=None)
        restored = backup.restore_workspace(selected_path, destination, selected["archive_sha256"],
                                             tombstones_from=authority)
        result = {"status": "restored", "receipt_sha256": current_sha, "scope": scope,
                  "forget_authority_sha256": cp._sha(latest_raw), "backup": restored}
    return result
