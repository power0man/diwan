#!/usr/bin/env python3
"""Owner-run Ed25519 key creation/export; never puts private bytes in argv/files."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SERVICE = b"diwan-anchor-ed25519"
ACCOUNT = b"diwan"


class KeySetupRefused(RuntimeError):
    pass


def _backend():
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError:
        raise KeySetupRefused("signing_backend_missing") from None
    return Ed25519PrivateKey


def _store_private_key(seed: bytes) -> None:
    if sys.platform != "darwin":
        raise KeySetupRefused("keychain_platform_required")
    # Security.framework receives the seed through memory, never a shell/argv.
    security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    add = security.SecKeychainAddGenericPassword
    add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
                    ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
                    ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    add.restype = ctypes.c_int32
    encoded = bytearray(seed.hex().encode("ascii"))
    secret = (ctypes.c_char * len(encoded)).from_buffer(encoded)
    try:
        status = add(None, len(SERVICE), SERVICE, len(ACCOUNT), ACCOUNT,
                     len(encoded), ctypes.cast(secret, ctypes.c_void_p), None)
    finally:
        encoded[:] = b"\0" * len(encoded)
    if status == -25299:
        raise KeySetupRefused("signing_key_already_exists")
    if status != 0:
        raise KeySetupRefused("keychain_write_refused")


def _check_output(path: Path) -> None:
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink() or not parent.is_dir():
            raise KeySetupRefused("public_output_parent_invalid")
    if path.exists() or path.is_symlink():
        raise KeySetupRefused("public_output_exists")


def _write_public(path: Path, public: bytes) -> None:
    # A crash leaves either no public target or all 32 bytes. Publication is
    # exclusive; an existing public identity is never replaced by this tool.
    from tools.migrate_signatures import _install_bytes
    _install_bytes(path, public)


def create(path: Path) -> dict:
    _check_output(path)
    key = _backend().generate()
    public = key.public_key().public_bytes_raw()
    _store_private_key(key.private_bytes_raw())
    # If this write is interrupted, `export` recovers the public half of the
    # existing key. A retry never silently replaces the Keychain identity.
    _write_public(path, public)
    return {"status": "created", "algorithm": "ED25519",
            "public_key_sha256": hashlib.sha256(public).hexdigest(),
            "private_key_saved_to_file": False}


def export(path: Path) -> dict:
    _check_output(path)
    from core.signing import load_ed25519_private_key
    seed = load_ed25519_private_key()
    public = _backend().from_private_bytes(seed).public_key().public_bytes_raw()
    _write_public(path, public)
    return {"status": "exported", "algorithm": "ED25519",
            "public_key_sha256": hashlib.sha256(public).hexdigest(),
            "private_key_saved_to_file": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("create", "export"))
    parser.add_argument("--public-output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = create(args.public_output.absolute()) if args.mode == "create" else export(args.public_output.absolute())
    except Exception as exc:
        # Key material and OS diagnostics must not reach logs or tracebacks.
        code = str(exc) if isinstance(exc, KeySetupRefused) else getattr(exc, "code", "key_setup_refused")
        print(json.dumps({"status": "refused", "code": code}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
