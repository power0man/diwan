#!/usr/bin/env python3
"""Owner-run installation of the reviewed gate; never run by CI or the gate.

The private state directory stores a frozen trusted code bundle. Existing hooks
are retained and chained, and modified/unowned files are never overwritten.
The shared hook discovers its repository from Git's cwd, so linked worktrees
check their own pushed objects. Git --no-verify always bypasses local hooks.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.gate_helpers import EvidenceStore, GateError, digest, load_configuration, outside

MARKER = "# diwan-owned-pre-push-v1"
TOOLS = ("verify_gate.py", "gate_helpers.py", "sign_anchors.py", "verification_checks.py",
         "agent_attribution.py")


def _git(repo: Path, *args: str, optional: bool = False) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            text=True, env=env, timeout=20)
    if result.returncode and not optional:
        raise GateError("repository_invalid")
    return result.stdout.strip() if not result.returncode else ""


def hook_location(repo: Path) -> Path:
    root = Path(_git(repo, "rev-parse", "--show-toplevel"))
    configured = _git(repo, "config", "--path", "--get", "core.hooksPath", optional=True)
    if configured:
        base = Path(configured)
        if not base.is_absolute():
            base = root / base
    else:
        base = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
    if str(base) == os.devnull:
        raise GateError("hooks_disabled")
    if any(p.is_symlink() for p in (base, *base.parents)):
        raise GateError("hook_path_symlink")
    if base.exists() and not base.is_dir():
        raise GateError("hook_directory_invalid")
    return base.absolute() / "pre-push"


def _plain_file(path: Path) -> None:
    if path.is_symlink():
        raise GateError("hook_path_symlink")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise GateError("hook_file_invalid")


def _state_directory(state: Path, repo: Path) -> None:
    # Reject any worktree of this repository, not only the invoking checkout.
    for line in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            outside(state, Path(line.removeprefix("worktree ")))
    if any(p.is_symlink() for p in (state, *state.parents)):
        raise GateError("trusted_path_symlink")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = state.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise GateError("trusted_path_permissions")


def _write(path: Path, raw: bytes, mode: int = 0o600) -> None:
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(mode)


def _copy_bundle(source: Path, bundle: Path) -> dict[str, str]:
    bundle.mkdir(mode=0o700)
    paths = sorted((source / "core").glob("*.py")) + [source / "tools" / n for n in TOOLS]
    if not (source / "core" / "signing.py").is_file():
        raise GateError("trusted_bundle_incomplete")
    hashes = {}
    for src in paths:
        _plain_file(src)
        relative = src.relative_to(source)
        dst = bundle / relative
        dst.parent.mkdir(mode=0o700, exist_ok=True)
        raw = src.read_bytes()
        _write(dst, raw)
        hashes[str(relative)] = digest(raw)
    return hashes


def install(repo: Path, state: Path, runtime: Path, trust: Path, *,
            source: Path = ROOT, python: Path = Path(sys.executable)) -> dict:
    repo, state = repo.resolve(), state.absolute()
    load_configuration(runtime, trust, repo)
    _state_directory(state, repo)
    hook = hook_location(repo)
    manifest_path = state / "installation.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        _plain_file(manifest_path)
        manifest = json.loads(manifest_path.read_bytes())
        if (manifest.get("hook") == str(hook) and hook.is_file() and not hook.is_symlink()
                and digest(hook.read_bytes()) == manifest.get("hook_sha256")):
            return {"status": "installed", "code": "already_installed"}
        raise GateError("installation_state_conflict")
    if hook.exists() or hook.is_symlink():
        _plain_file(hook)
        if MARKER.encode() in hook.read_bytes():
            raise GateError("unowned_diwan_hook")
    if not python.is_absolute() or not python.is_file():
        raise GateError("python_runtime_invalid")
    # Validate the actual interpreter, not a PATH alias named python3.
    probe = subprocess.run([str(python), "-I", "-c", "import sys;print('.'.join(map(str,sys.version_info[:2])))"],
                           capture_output=True, text=True, timeout=20)
    parts = probe.stdout.strip().split(".") if probe.returncode == 0 and "." in probe.stdout else []
    try:
        ver = (int(parts[0]), int(parts[1])) if len(parts) >= 2 else ()
    except ValueError:
        ver = ()
    if not ver or not (3, 11) <= ver < (3, 15):
        raise GateError("python_runtime_invalid")
    evidence = EvidenceStore(state / "evidence", repo)
    bundle = state / ("bundle-" + uuid.uuid4().hex)
    hashes = _copy_bundle(source, bundle)
    _write(bundle / "runtime.json", runtime.read_bytes())
    _write(bundle / "trust.json", trust.read_bytes())
    hook.parent.mkdir(parents=True, exist_ok=True)
    prior = None
    prior_hash = None
    prior_executable = False
    if hook.exists():
        prior = hook.parent / ("pre-push.diwan-previous-" + uuid.uuid4().hex)
        prior_hash = digest(hook.read_bytes())
        prior_executable = os.access(hook, os.X_OK)
    command = [str(python), "-I", str(bundle / "tools/verify_gate.py"),
               "--repo", ".", "--runtime", str(bundle / "runtime.json"),
               "--trust", str(bundle / "trust.json"), "--evidence-dir", str(evidence.path)]
    if prior and prior_executable:
        command += ["--previous-hook", str(prior)]
    script = ("#!/bin/sh\n" + MARKER + "\nexec " + shlex.join(command)
              + ' --pre-push "$@"\n').encode()
    staged = hook.parent / (".pre-push.diwan-" + uuid.uuid4().hex)
    _write(staged, script, 0o700)
    manifest = {"schema_version": 1, "hook": str(hook), "hook_sha256": digest(script),
                "previous": str(prior) if prior else None, "previous_sha256": prior_hash,
                "bundle": str(bundle), "bundle_sha256": hashes,
                "evidence_dir": str(evidence.path)}
    # Write the recovery record before moving either hook. On any failure it is
    # retained; reinstall refuses an ambiguous state instead of overwriting it.
    _write(manifest_path, json.dumps(manifest, sort_keys=True).encode())
    try:
        if prior:
            hook.rename(prior)
        staged.rename(hook)
    except OSError:
        if prior and prior.exists() and not hook.exists():
            prior.rename(hook)
        raise
    return {"status": "installed", "code": "installed", "previous_preserved": bool(prior),
            "limits": ["no_verify_bypasses_hook", "pre_push_is_after_remote_contact"]}


def uninstall(repo: Path, state: Path) -> dict:
    state = state.absolute()
    _state_directory(state, repo)
    manifest_path = state / "installation.json"
    _plain_file(manifest_path)
    manifest = json.loads(manifest_path.read_bytes())
    hook = hook_location(repo)
    if str(hook) != manifest.get("hook"):
        raise GateError("hook_location_changed")
    _plain_file(hook)
    if digest(hook.read_bytes()) != manifest.get("hook_sha256"):
        raise GateError("hook_modified_refused")
    prior = Path(manifest["previous"]) if manifest.get("previous") else None
    if prior:
        if prior.parent != hook.parent or not prior.name.startswith("pre-push.diwan-previous-"):
            raise GateError("previous_hook_invalid")
        _plain_file(prior)
        if digest(prior.read_bytes()) != manifest.get("previous_sha256"):
            raise GateError("previous_hook_modified_refused")
        os.replace(prior, hook)
    else:
        hook.unlink()
    # Keep the reviewed bundle and rename the record for audit/recovery.
    manifest_path.rename(state / ("uninstalled-" + uuid.uuid4().hex + ".json"))
    return {"status": "uninstalled", "code": "previous_restored" if prior else "owned_hook_removed"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--trust", type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.uninstall:
            result = uninstall(args.repo.resolve(), args.state_dir)
        elif args.runtime and args.trust:
            result = install(args.repo, args.state_dir, args.runtime, args.trust, python=args.python)
        else:
            raise GateError("runtime_and_trust_required")
    except (GateError, OSError, ValueError) as exc:
        result = {"status": "failed", "code": exc.code if isinstance(exc, GateError)
                  else "installation_io_or_state_failed"}
    print(json.dumps(result, sort_keys=True))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
