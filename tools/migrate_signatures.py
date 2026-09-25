#!/usr/bin/env python3
"""First signatures for the publication manifest and all local node streams.

Run on the owning repository with writers stopped:
    python tools/migrate_signatures.py plan --output /private/review.json
    python tools/migrate_signatures.py apply --plan /private/review.json --sha256 HASH

For a newly created stream file with exactly zero bytes and no anchor, plan
with --initialize-empty-streams. This explicitly records a genesis checkpoint;
it cannot repair a nonempty unanchored stream or replace an existing anchor.

The plan is evidence for owner review, not provenance authentication. Compare its
checkpoint with an independently trusted record before applying. Application
never repairs a chain/anchor and never overwrites a signature. It takes existing
writer locks and validates the whole plan again before loading the key. A crash
may leave some signatures installed; an unchanged plan can resume only after
verifying each existing signature. No remote host state or Keychain is changed.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.canonical import canonical_bytes
from core.ledger import GENESIS, Ledger, LedgerCorrupt
from core.seal import write_lock
from core.signing import (ALG, INITIAL_SIGNATURE_MIGRATION,
                          SigningRefused, _mac, load_key, sig_path,
                          verify_anchor_signature, discovered_stream_scopes,
                          initial_signature_scope, is_stream_scope, prepare_signer)
from tools.sign_anchors import _check_content_before_signing

ROOT = Path(__file__).resolve().parent.parent
_PLAN_KEYS = {"schema_version", "purpose", "root_sha256", "entries"}
_ENTRY_KEYS = {"path", "ledger_sha256", "anchor_sha256", "count", "head", "initialize_empty_anchor"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_EMPTY_ANCHOR = canonical_bytes({"count": 0, "head": GENESIS})


def _refuse(code: str, reason: str):
    raise SigningRefused(code, reason)


def _hash(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _root_hash() -> str:
    return _hash(str(ROOT.resolve()).encode("utf-8"))


def _regular(path: Path, *, absent: bool = False) -> bool:
    """Reject aliases in every component; absence is explicit, never repair."""
    missing = False
    for component in reversed((path, *path.parents)):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            if absent:
                missing = True
                continue
            _refuse("migration_path_missing", "ملف أو مجلد مطلوب غائب")
        if stat.S_ISLNK(mode):
            _refuse("migration_path_invalid", "روابط رمزية غير مسموحة")
        if component == path:
            if not stat.S_ISREG(mode) or component.stat().st_nlink != 1:
                _refuse("migration_path_invalid", "المدخل ليس ملفًا عاديًا مستقلًا")
        elif not stat.S_ISDIR(mode):
            _refuse("migration_path_invalid", "أحد الآباء ليس مجلدًا")
    return not missing


def _entry(rel: str, *, initialize_empty_anchor: bool = False) -> dict:
    ledger = Ledger(ROOT / rel, create=False)
    _regular(ledger.path)
    anchored = _regular(ledger.anchor_path, absent=initialize_empty_anchor)
    signed = _regular(sig_path(ledger), absent=True)
    if initialize_empty_anchor:
        if (not is_stream_scope(rel) or ledger.path.read_bytes() != b""
                or (signed and not anchored)
                or (anchored and ledger.anchor_path.read_bytes() != _EMPTY_ANCHOR)):
            _refuse("migration_empty_stream_invalid", "تهيئة المرساة تخص تيارًا خاليًا تمامًا بلا آثار مخالفة")
        return {"path": rel, "ledger_sha256": _hash(b""),
                "anchor_sha256": _hash(_EMPTY_ANCHOR), "count": 0,
                "head": GENESIS, "initialize_empty_anchor": True}
    # Chain verification and exact typed checkpoint are independent of signature
    # presence. In particular, strict mode is never temporarily disabled.
    _check_content_before_signing(ledger)
    anchor = ledger.read_anchor()
    return {"path": rel, "ledger_sha256": _hash(ledger.path.read_bytes()),
            "anchor_sha256": _hash(ledger.anchor_path.read_bytes()),
            "count": anchor["count"], "head": anchor["head"],
            "initialize_empty_anchor": False}


def plan(*, initialize_empty_streams: bool = False) -> dict:
    entries = []
    for rel in sorted(INITIAL_SIGNATURE_MIGRATION | discovered_stream_scopes(ROOT)):
        ledger = Ledger(ROOT / rel, create=False)
        present = [_regular(p, absent=True) for p in
                   (ledger.path, ledger.anchor_path, sig_path(ledger))]
        if not any(present) and is_stream_scope(rel):
            continue
        # A present signature is not a first-signature migration candidate.
        # Verify/renew it using the ordinary strict command instead.
        current = _entry(rel, initialize_empty_anchor=(
            initialize_empty_streams and is_stream_scope(rel) and not present[1]))
        if not present[2]:
            entries.append(current)
    return {"schema_version": 1, "purpose": "initial-governing-signatures",
            "root_sha256": _root_hash(), "entries": entries}


def _decode_plan(raw: bytes, expected_sha256: str) -> dict:
    if not _HEX.fullmatch(expected_sha256) or _hash(raw) != expected_sha256:
        _refuse("migration_plan_hash_mismatch", "بصمة الخطة لا تطابق النسخة المراجعة")
    try:
        obj = json.loads(raw)
    except (ValueError, UnicodeError):
        _refuse("migration_plan_invalid", "خطة مشوهة")
    if (type(obj) is not dict or set(obj) != _PLAN_KEYS
            or type(obj["schema_version"]) is not int or obj["schema_version"] != 1
            or obj["purpose"] != "initial-governing-signatures"
            or obj["root_sha256"] != _root_hash()
            or type(obj["entries"]) is not list
            or canonical_bytes(obj) != raw):
        _refuse("migration_plan_invalid", "خطة غير معيارية أو لجذر آخر")
    paths = []
    for entry in obj["entries"]:
        if (type(entry) is not dict or set(entry) != _ENTRY_KEYS
                or type(entry["path"]) is not str
                or not initial_signature_scope(entry["path"])
                or type(entry["count"]) is not int or entry["count"] < 0
                or type(entry["initialize_empty_anchor"]) is not bool
                or any(type(entry[k]) is not str or not _HEX.fullmatch(entry[k])
                       for k in ("ledger_sha256", "anchor_sha256", "head"))):
            _refuse("migration_plan_invalid", "قيد خطة خارج العقد")
        paths.append(entry["path"])
    if paths != sorted(set(paths)):
        _refuse("migration_plan_invalid", "نطاق مكرر أو ترتيب غير معياري")
    return obj


def _publish_exclusive(source: Path, destination: Path) -> None:
    """Atomic rename without replacing a raced-in target (Mac/Linux only)."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = getattr(libc, "renamex_np", None)
        if rename is None:
            _refuse("migration_atomic_publish_unavailable", "النظام لا يدعم النشر الحصري")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        # RENAME_EXCL from the macOS SDK <sys/stdio.h>.
        result = rename(os.fsencode(source), os.fsencode(destination), 0x00000004)
    elif sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            _refuse("migration_atomic_publish_unavailable", "النظام لا يدعم النشر الحصري")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                           ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        # AT_FDCWD=-100, RENAME_NOREPLACE=1 on Linux.
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    else:
        _refuse("migration_atomic_publish_unavailable", "النظام لا يدعم النشر الحصري")
    if result != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def _install_bytes(target: Path, blob: bytes) -> None:
    """An interruption leaves either no final file or a fully written file.

    Random temporary files from a hard crash are ignored, never trusted or
    promoted on retry. They contain only public anchor/signature data, no key.
    """
    fd, name = tempfile.mkstemp(prefix=".diwan-signing-", suffix=".tmp", dir=target.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(blob)
            stream.flush()
            os.fsync(stream.fileno())
        _publish_exclusive(temp, target)
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temp.unlink(missing_ok=True)


def apply(raw: bytes, expected_sha256: str) -> dict:
    obj = _decode_plan(raw, expected_sha256)
    # Nothing, including a lock file or key lookup, happens on stale/bad input.
    for entry in obj["entries"]:
        if _entry(entry["path"], initialize_empty_anchor=entry["initialize_empty_anchor"]) != entry:
            _refuse("migration_state_changed", "تغير السجل أو ختمه بعد إعداد الخطة")
    signed = resumed = anchors_created = 0
    with ExitStack() as stack:
        ledgers = [Ledger(ROOT / e["path"], create=False) for e in obj["entries"]]
        for ledger in ledgers:
            lock = ledger.path.with_suffix(ledger.path.suffix + ".lock")
            _regular(lock, absent=True)
            stack.enter_context(write_lock(ledger))
        for entry in obj["entries"]:
            if _entry(entry["path"], initialize_empty_anchor=entry["initialize_empty_anchor"]) != entry:
                _refuse("migration_state_changed", "تغير السجل أثناء اكتساب الأقفال")
        # Public Ed verification of already completed entries requires no
        # private key. A pending first signature follows the active policy,
        # never a hard-coded legacy algorithm or legacy-key fallback.
        for ledger in ledgers:
            if sig_path(ledger).exists():
                verify_anchor_signature(ledger)
        pending = [ledger for ledger in ledgers if not sig_path(ledger).exists()]
        signer = prepare_signer(pending[0]) if pending else None
        # Validate the private/public identity and compute every signature
        # BEFORE creating even a planned genesis anchor. One policy governs all
        # these repository scopes; reuse the prepared signer without key lookup.
        signatures = {str(ledger.path): signer.signature_bytes(
            ledger, root=ROOT, anchor_bytes=(ledger.anchor_path.read_bytes()
                    if ledger.anchor_path.exists() else _EMPTY_ANCHOR))
            for ledger in pending}
        for ledger in ledgers:
            target = sig_path(ledger)
            if target.exists():
                resumed += 1
                continue
            if not ledger.anchor_path.exists():
                _install_bytes(ledger.anchor_path, _EMPTY_ANCHOR)
                anchors_created += 1
            _install_bytes(target, signatures[str(ledger.path)])
            signed += 1
    return {"status": "signed", "plan_sha256": expected_sha256,
            "signed": signed, "already_verified": resumed,
            "empty_anchors_created": anchors_created,
            "existing_ledger_and_anchor_bytes_changed": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    planner = sub.add_parser("plan")
    planner.add_argument("--output", type=Path, required=True)
    planner.add_argument("--initialize-empty-streams", action="store_true",
                         help="تخطيط مرساة genesis لتيار موجود خال من كل البايتات فقط")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--plan", type=Path, required=True)
    apply_parser.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.mode == "plan":
            raw = canonical_bytes(plan(initialize_empty_streams=args.initialize_empty_streams))
            with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                   | os.O_NOFOLLOW, 0o600), "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            result = {"status": "review_required", "sha256": _hash(raw),
                      "entries": len(json.loads(raw)["entries"])}
        else:
            _regular(args.plan)
            result = apply(args.plan.read_bytes(), args.sha256)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (SigningRefused, LedgerCorrupt, OSError, ValueError, TypeError) as exc:
        code = exc.code if isinstance(exc, SigningRefused) else "migration_refused"
        print(json.dumps({"status": "refused", "code": code}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
