#!/usr/bin/env python3
"""Reviewed HMAC-to-Ed25519 conversion, preserving the prior signatures.

Use owning-repository code, with normal writers stopped. Plan/apply are bound to
the root and exact ledger, anchor, old-signature and new-key bytes. `--resign`
authorizes conversion only; it never authorizes accepting an invalid old HMAC.
Each signature replacement is atomic; the collection is resumable, not one
filesystem transaction. Activate the Ed-only policy only after completion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.canonical import canonical_bytes
from core.ledger import Ledger, LedgerCorrupt
from core.seal import write_lock
from core.snapshot import full_entries
from core import signing
from tools.sign_anchors import OPTIONAL_LOCAL, _check_content_before_signing
from tools.migrate_signatures import _regular, _install_bytes

ROOT = Path(__file__).resolve().parent.parent
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_PLAN_KEYS = {"schema_version", "purpose", "root_sha256", "public_key_hex",
              "public_key_sha256", "entries"}
_ENTRY_KEYS = {"path", "ledger_sha256", "anchor_sha256", "signature_sha256",
               "algorithm", "count", "head"}


def _refuse(code):
    raise signing.SigningRefused(code, "رُفض ترحيل التوقيع؛ راجع الخطة والحالة")


def _hash(blob):
    return hashlib.sha256(blob).hexdigest()


def _root_hash():
    return _hash(str(ROOT.resolve()).encode())


def _scopes():
    return sorted(signing.SIGNED_LEDGERS | signing.discovered_stream_scopes(ROOT)
                  | signing.discovered_snapshot_scopes(ROOT))


def _entry(scope: str) -> dict:
    ledger = Ledger(ROOT / scope, create=False)
    for path in (ledger.path, ledger.anchor_path, signing.sig_path(ledger)):
        _regular(path)
    full_entries(ledger.path, generation_check=_check_content_before_signing)
    anchor = ledger.read_anchor()
    raw = signing.sig_path(ledger).read_bytes()
    algorithm, _ = signing.parse_signature(raw)
    return {"path": scope, "ledger_sha256": _hash(ledger.path.read_bytes()),
            "anchor_sha256": _hash(ledger.anchor_path.read_bytes()),
            "signature_sha256": _hash(raw), "algorithm": algorithm,
            "count": anchor["count"], "head": anchor["head"]}


def plan(public: bytes, fingerprint: str) -> dict:
    if len(public) != 32 or not _HEX.fullmatch(fingerprint) or _hash(public) != fingerprint:
        _refuse("migration_public_key_mismatch")
    entries = []
    for scope in _scopes():
        ledger = Ledger(ROOT / scope, create=False)
        paths = (ledger.path, ledger.anchor_path, signing.sig_path(ledger))
        optional = scope in OPTIONAL_LOCAL or signing.is_stream_scope(scope)
        if optional and not any(path.exists() or path.is_symlink() for path in paths):
            continue
        entry = _entry(scope)
        if entry["algorithm"] == signing.ED25519:
            message = signing.signature_message(ledger, ledger.anchor_path.read_bytes(),
                                                algorithm=signing.ED25519, root=ROOT)
            signing.verify_signature_bytes(signing.sig_path(ledger).read_bytes(), message,
                                           public_key=public)
        entries.append(entry)
    return {"schema_version": 1, "purpose": "governing-ed25519-conversion",
            "root_sha256": _root_hash(), "public_key_hex": public.hex(),
            "public_key_sha256": fingerprint, "entries": entries}


def _decode(raw: bytes, digest: str) -> dict:
    if not _HEX.fullmatch(digest) or _hash(raw) != digest:
        _refuse("migration_plan_hash_mismatch")
    try:
        obj = json.loads(raw)
        public = bytes.fromhex(obj.get("public_key_hex", ""))
        if (type(obj) is not dict or set(obj) != _PLAN_KEYS
                or type(obj["schema_version"]) is not int or obj["schema_version"] != 1
                or obj["purpose"] != "governing-ed25519-conversion"
                or obj["root_sha256"] != _root_hash()
                or canonical_bytes(obj) != raw or len(public) != 32
                or obj["public_key_hex"] != public.hex()
                or _hash(public) != obj["public_key_sha256"]
                or type(obj["entries"]) is not list or not obj["entries"]):
            _refuse("migration_plan_invalid")
        paths = []
        for entry in obj["entries"]:
            if (type(entry) is not dict or set(entry) != _ENTRY_KEYS
                    or type(entry["path"]) is not str or entry["path"] not in _scopes()
                    or entry["algorithm"] not in (signing.ALG, signing.ED25519)
                    or type(entry["count"]) is not int or entry["count"] < 0
                    or any(type(entry[k]) is not str or not _HEX.fullmatch(entry[k])
                           for k in ("ledger_sha256", "anchor_sha256", "signature_sha256", "head"))):
                _refuse("migration_plan_invalid")
            paths.append(entry["path"])
        if paths != sorted(set(paths)):
            _refuse("migration_plan_invalid")
    except (ValueError, TypeError, AttributeError):
        _refuse("migration_plan_invalid")
    return obj


def _same_data(entry):
    current = _entry(entry["path"])
    if any(current[k] != entry[k] for k in ("path", "ledger_sha256", "anchor_sha256", "count", "head")):
        _refuse("migration_state_changed")
    if current["signature_sha256"] == entry["signature_sha256"]:
        if current["algorithm"] != entry["algorithm"]:
            _refuse("migration_plan_algorithm_mismatch")
    elif entry["algorithm"] != signing.ALG or current["algorithm"] != signing.ED25519:
        _refuse("migration_signature_changed")
    return current


def _backup_dir(digest: str) -> Path:
    target = ROOT / "var" / "signature-migration" / digest
    for directory in (ROOT / "var", target.parent, target):
        if directory.exists() or directory.is_symlink():
            if directory.is_symlink() or not directory.is_dir():
                _refuse("migration_backup_invalid")
            if directory != ROOT / "var" and stat.S_IMODE(directory.stat().st_mode) & 0o077:
                _refuse("migration_backup_not_private")
        else:
            directory.mkdir(mode=0o700)
    return target


def apply(raw: bytes, digest: str, *, resign: bool = False) -> dict:
    if not resign:
        _refuse("migration_conversion_approval_required")
    obj = _decode(raw, digest)
    public = bytes.fromhex(obj["public_key_hex"])
    actual = []
    for scope in _scopes():
        ledger = Ledger(ROOT / scope, create=False)
        optional = scope in OPTIONAL_LOCAL or signing.is_stream_scope(scope)
        paths = (ledger.path, ledger.anchor_path, signing.sig_path(ledger))
        if not optional or any(p.exists() or p.is_symlink() for p in paths):
            actual.append(scope)
    if [entry["path"] for entry in obj["entries"]] != actual:
        _refuse("migration_scope_changed")
    for entry in obj["entries"]:
        _same_data(entry)
    converted = resumed = 0
    with ExitStack() as stack:
        ledgers = [Ledger(ROOT / entry["path"], create=False) for entry in obj["entries"]]
        for ledger in ledgers:
            _regular(ledger.path.with_suffix(ledger.path.suffix + ".lock"), absent=True)
            stack.enter_context(write_lock(ledger))
        states = [_same_data(entry) for entry in obj["entries"]]
        needs_conversion = any(s["algorithm"] == signing.ALG for s in states)
        old_key = signing.load_key() if needs_conversion else None
        private = signing.load_ed25519_private_key() if needs_conversion else None
        changes = []
        # Validate ALL old signatures and private/public correspondence before
        # creating a backup or replacing the first signature.
        for ledger, entry, state in zip(ledgers, obj["entries"], states):
            anchor = ledger.anchor_path.read_bytes()
            prior = signing.sig_path(ledger).read_bytes()
            message = signing.signature_message(ledger, anchor, algorithm=state["algorithm"], root=ROOT)
            signing.verify_signature_bytes(prior, message, public_key=public, legacy_key=old_key)
            new = (signing.make_signature(ledger, private, algorithm=signing.ED25519,
                                          public_key=public, root=ROOT, anchor_bytes=anchor)
                   if private is not None else prior)
            if state["signature_sha256"] != entry["signature_sha256"] and prior != new:
                _refuse("migration_signature_changed")
            changes.append((ledger, entry, prior, new))
        backup = _backup_dir(digest)
        # Validate all backups needed for completed entries before converting
        # another entry. Resume must not claim preserved history after deletion
        # or replacement of a prior signature backup. Hashes are checked here;
        # no secret is needed to re-read a completed public migration.
        backups_to_create = []
        for ledger, entry, prior, new in changes:
            if entry["algorithm"] != signing.ALG:
                continue
            old_path = backup / (_hash(entry["path"].encode()) + ".sig")
            if old_path.exists() or old_path.is_symlink():
                _regular(old_path)
                original = old_path.read_bytes()
                if (stat.S_IMODE(old_path.stat().st_mode) & 0o077
                        or _hash(original) != entry["signature_sha256"]
                        or signing.parse_signature(original)[0] != signing.ALG):
                    _refuse("migration_backup_mismatch")
            elif prior == new:
                _refuse("migration_backup_missing")
            else:
                backups_to_create.append((old_path, prior))
        for old_path, original in backups_to_create:
            _install_bytes(old_path, original)
        for ledger, entry, prior, new in changes:
            if prior == new:
                resumed += 1
                continue
            # Cooperative writers remain locked. Recheck the exact approved
            # state after durable backup, immediately before replacement.
            if signing.sig_path(ledger).read_bytes() != prior:
                _refuse("migration_signature_changed")
            _same_data(entry)
            signing.atomic_write_signature(signing.sig_path(ledger), new)
            converted += 1
    return {"status": "converted", "plan_sha256": digest, "converted": converted,
            "already_verified": resumed, "public_key_sha256": obj["public_key_sha256"],
            "ledger_and_anchor_bytes_changed": False, "previous_signatures_preserved": True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--public-key", type=Path, required=True)
    p.add_argument("--fingerprint", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = sub.add_parser("apply")
    a.add_argument("--plan", type=Path, required=True)
    a.add_argument("--sha256", required=True)
    a.add_argument("--resign", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            _regular(args.public_key)
            obj = plan(args.public_key.read_bytes(), args.fingerprint)
            raw = canonical_bytes(obj)
            _install_bytes(args.output, raw)
            result = {"status": "review_required", "sha256": _hash(raw),
                      "entries": len(obj["entries"]), "public_key_sha256": obj["public_key_sha256"]}
        else:
            _regular(args.plan)
            result = apply(args.plan.read_bytes(), args.sha256, resign=args.resign)
    except (signing.SigningRefused, LedgerCorrupt, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"status": "refused", "code": getattr(exc, "code", "migration_refused")}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
