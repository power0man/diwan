"""Explicit, offline quarantine of unpublished staging; never delete or publish it."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re

from core.canonical import canonical_bytes, digest
from workspace_tools.backup import (
    BackupError, MAX_RAW_BYTES, _DIR, _inventory, _lease, _open_directory,
    _path, _private, _read_at, _signature, restore_pending,
)


class RecoveryError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code):
    if not condition:
        raise RecoveryError(code)


@contextmanager
def _workspace(root):
    root = _path(root)
    _need(not restore_pending(root), "restore_incomplete")
    fd = _open_directory(root)
    try:
        _private(os.fstat(fd), directory=True)
        _need(".restore-incomplete" not in os.listdir(fd), "restore_incomplete")
        # Existing lease only: inspection does not initialize a workspace.
        with _lease(fd, "app.lock"):
            yield root, fd
    finally:
        os.close(fd)


def _snapshot(fd):
    before = _inventory(fd)
    records, size, count = [], 0, 0
    for path, (directory, signature, identity) in sorted(before.items()):
        record = {"path": path, "directory": directory, "identity": identity}
        if not directory:
            raw, info = _read_at(fd, path, MAX_RAW_BYTES - size)
            _need(_signature(info) == signature, "recovery_source_changed")
            record.update(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            size += len(raw)
            count += 1
        records.append(record)
    _need(_inventory(fd) == before, "recovery_source_changed")
    return records, {"sha256": digest(records), "files": count,
                     "directories": len(records) - count, "total_bytes": size}


def _entry(value):
    _need(type(value) is str and re.fullmatch(r"[a-f0-9]{32}", value), "recovery_entry_invalid")
    return value


def inspect_staging(root):
    """Return content/identity fingerprints, without user text or initializing stores."""
    with _workspace(root) as (_, root_fd):
        if "staging" not in os.listdir(root_fd):
            return {"status": "inspected", "entries": []}
        stage_fd = os.open("staging", _DIR, dir_fd=root_fd)
        try:
            _private(os.fstat(stage_fd), directory=True)
            names = sorted(os.listdir(stage_fd))
            _need(len(names) <= 64, "recovery_entry_limit")
            entries = []
            for name in names:
                _entry(name)
                fd = os.open(name, _DIR, dir_fd=stage_fd)
                try:
                    _, summary = _snapshot(fd)
                    entries.append({"entry": name, **summary})
                finally:
                    os.close(fd)
            return {"status": "inspected", "entries": entries}
        finally:
            os.close(stage_fd)


def _outside(parent_fd, root_fd):
    target = os.fstat(root_fd)
    fd = os.dup(parent_fd)
    try:
        while True:
            here = os.fstat(fd)
            _need((here.st_dev, here.st_ino) != (target.st_dev, target.st_ino),
                  "recovery_destination_inside_workspace")
            up = os.open("..", _DIR, dir_fd=fd)
            parent = os.fstat(up)
            if (here.st_dev, here.st_ino) == (parent.st_dev, parent.st_ino):
                os.close(up)
                return
            os.close(fd)
            fd = up
    finally:
        os.close(fd)


def _receipt(fd, value):
    handle = os.open("receipt.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=fd)
    with os.fdopen(handle, "wb") as stream:
        stream.write(canonical_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(fd)


def quarantine_staging(root, entry, expected_sha256, destination):
    """Move exactly one reviewed entry to a new private wrapper on the same volume.

    The receipt is durable before the atomic move. After interruption it describes
    the reserved destination; presence of data establishes where the entry is.
    Nothing is deleted, merged, auto-published, or replayed to a provider.
    """
    entry = _entry(entry)
    _need(type(expected_sha256) is str and re.fullmatch(r"[a-f0-9]{64}", expected_sha256),
          "recovery_digest_invalid")
    destination = _path(destination)
    with _workspace(root) as (root, root_fd):
        stage_fd = os.open("staging", _DIR, dir_fd=root_fd)
        parent_fd = None
        source_fd = None
        wrapper_fd = None
        try:
            _private(os.fstat(stage_fd), directory=True)
            source_fd = os.open(entry, _DIR, dir_fd=stage_fd)
            records, summary = _snapshot(source_fd)
            _need(summary["sha256"] == expected_sha256, "recovery_digest_mismatch")
            parent_fd = _open_directory(destination.parent)
            _private(os.fstat(parent_fd), directory=True)
            _outside(parent_fd, root_fd)
            _need(os.fstat(parent_fd).st_dev == os.fstat(source_fd).st_dev,
                  "recovery_destination_volume")
            # Exclusive mkdir reserves a fresh wrapper; existing paths are untouched.
            os.mkdir(destination.name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
            wrapper_fd = os.open(destination.name, _DIR, dir_fd=parent_fd)
            _receipt(wrapper_fd, {"schema_version": 1, "kind": "staging_quarantine",
                                 "source_root": str(root), "entry": entry,
                                 "sha256": expected_sha256, "inventory": records})
            current, _ = _snapshot(source_fd)
            _need(current == records, "recovery_source_changed")
            named = os.stat(entry, dir_fd=stage_fd, follow_symlinks=False)
            opened = os.fstat(source_fd)
            _need((named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino),
                  "recovery_source_changed")
            os.rename(entry, "data", src_dir_fd=stage_fd, dst_dir_fd=wrapper_fd)
            os.fsync(wrapper_fd)
            os.fsync(stage_fd)
            return {"status": "quarantined", "entry": entry, **summary}
        finally:
            for fd in (wrapper_fd, source_fd, parent_fd, stage_fd):
                if fd is not None:
                    os.close(fd)


def error_code(exc):
    if isinstance(exc, (RecoveryError, BackupError)):
        return exc.code
    if isinstance(exc, FileExistsError):
        return "recovery_destination_exists"
    if isinstance(exc, FileNotFoundError):
        return "recovery_path_missing"
    if isinstance(exc, OSError):
        return "recovery_io_error"
    return "recovery_failed"
