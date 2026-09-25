#!/usr/bin/env python3
"""Offline pre-push checks of the actual pushed objects, using trusted tooling.

Install the reviewed tool bundle outside the worktree before using this hook.
Git has already contacted the remote when pre-push runs. --no-verify bypasses
the hook entirely and cannot be recorded by it. DIWAN_SKIP_GATE=1 is logged.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

TRUSTED_ROOT = Path(__file__).resolve().parent.parent
if str(TRUSTED_ROOT) not in sys.path:
    sys.path.insert(0, str(TRUSTED_ROOT))

from tools.gate_helpers import (DockerRunner, EvidenceStore, GateError, commit_oid, digest,
                                git, load_configuration, materialize, outside,
                                parse_updates)


def verify_public(root: Path, trust: dict) -> dict:
    """Import only from the installed, reviewed bundle, never from root."""
    try:
        from core.signing import PINNED_POLICY_SHA256
        from tools.sign_anchors import verify_repository
    except ImportError as exc:
        raise GateError("trusted_verifier_unavailable") from exc
    if trust["policy_sha256"] != PINNED_POLICY_SHA256:
        raise GateError("trusted_policy_mismatch")
    try:
        result = verify_repository(root, public_key=bytes.fromhex(trust["public_key_hex"]))
    except Exception as exc:
        raise GateError("candidate_signature_failed") from exc
    if (not isinstance(result, dict) or result.get("failures")
            or type(result.get("verified")) is not int or result["verified"] < 7):
        raise GateError("candidate_signature_failed")
    return {"name": "public-signatures", "code": "passed", "exit_code": 0,
            "verified": result["verified"], "skipped": result.get("skipped", 0)}


def verify_attribution(repo, updates: list[dict]) -> dict:
    """كلُّ دفعةٍ تُدفع تُعلن مُنتِجَها — تُقرأ من نسخة الأداة الموثوقة.

    يُقرأ سجلُّ العملاء من المستودع نفسه عمدًا: تسجيلُ عميلٍ جديد فرقٌ
    مرئيّ يراجعه المالك، والبوابةُ تمنع المجهولية لا تأذن بالهوية.
    """
    try:
        from tools.agent_attribution import (AttributionError, LOG_ARGV,
                                             REGISTRY_PATH, check_commits,
                                             load_registry, parse_log)
    except ImportError as exc:
        raise GateError("trusted_attribution_unavailable") from exc
    try:
        registry = load_registry(git(repo, "show", f"{updates[0]['commit']}:{REGISTRY_PATH}"))
    except (GateError, AttributionError) as exc:
        raise GateError("attribution_registry_unavailable") from exc
    commits = []
    seen = set()
    for update in updates:
        zero = "0" * len(update["commit"])
        span = (update["commit"] if update["remote_oid"] in (None, zero)
                else f"{update['remote_oid']}..{update['commit']}")
        try:
            raw = git(repo, *LOG_ARGV, span).decode("utf-8", "replace")
        except GateError:
            # مرجعٌ بعيدٌ لا نملك كائنَه: يُفحص الرأسُ وحده لا أن يُتخطّى.
            raw = git(repo, *LOG_ARGV, "-1", update["commit"]).decode("utf-8", "replace")
        for commit in parse_log(raw):
            if commit["sha"] not in seen:
                seen.add(commit["sha"])
                commits.append(commit)
    try:
        return check_commits(commits, registry)
    except AttributionError as exc:
        raise GateError("attribution_check_failed") from exc


def shared_checks():
    try:
        from tools.verification_checks import commands
        checks = commands("/opt/venv/bin/python", "/usr/local/bin/node")
    except ImportError as exc:
        raise GateError("trusted_check_list_missing") from exc
    if not checks or sum(c.name == "public-signatures" for c in checks) != 1:
        raise GateError("trusted_check_list_invalid")
    return checks


def evaluate(repo: Path, updates: list[dict], receipt: dict, trust: dict, *,
             signature_verifier=verify_public, runner_factory=DockerRunner,
             checks_factory=shared_checks, attribution_verifier=verify_attribution,
             evidence_dir: Path | None = None) -> dict:
    """Dependency injection is for tests; no CLI flag can replace the verifier."""
    report = {"schema_version": 1, "status": "passed", "code": "verified",
              "bypass": False, "refs": updates, "commits": [],
              "limits": ["pre_push_is_after_remote_contact", "no_verify_bypasses_hook",
                         "no_live_model_checks", "owner_health_is_separate"]}
    if not updates:
        report["code"] = "no_updates"
        return report
    oid_size = 64 if git(repo, "rev-parse", "--show-object-format").strip() == b"sha256" else 40
    commits = []
    # Resolve every object before any candidate runs. Moving branches do not
    # change the frozen local_oid read from Git's stdin.
    for update in updates:
        update["commit"] = commit_oid(repo, update["local_oid"], oid_size)
        if update["commit"] not in commits:
            commits.append(update["commit"])
    # النسبةُ تُفحص مرةً على المدى المدفوع كلِّه، قبل أيِّ تشغيل: الدفعةُ
    # المجهولةُ المُنتِج تُردّ ولو كانت شيفرتُها خضراء. والردُّ تقريرٌ فاشل
    # لا استثناءٌ صاعد، ليصل سببُه إلى المالك كما يصل سببُ أيِّ فحصٍ آخر.
    try:
        report["attribution"] = attribution_verifier(repo, updates)
    except GateError as exc:
        report["attribution"] = {"status": "failed", "code": exc.code, "findings": []}
    if report["attribution"].get("status") != "passed":
        report.update(status="failed", code="unattributed_push")
        return report
    evidence = EvidenceStore(evidence_dir, repo) if evidence_dir is not None else None
    if evidence is not None:
        report["evidence"] = {"directory": str(evidence.path), "logs": evidence.records,
                              "limits": ["private_diagnostics_not_product_certification",
                                         "file_backed_capture_without_disk_quota"]}
    runner = None
    checks = checks_factory()
    for commit in commits:
        entry = {"sha": commit, "status": "passed", "checks": []}
        report["commits"].append(entry)
        try:
            with tempfile.TemporaryDirectory(prefix="diwan-gate-") as temporary:
                snapshot = Path(temporary).resolve()
                materialize(repo, commit, snapshot)
                lock = snapshot / "requirements-ci.lock"
                if not lock.is_file() or digest(lock.read_bytes()) != receipt["lock_sha256"]:
                    raise GateError("candidate_lock_mismatch")
                # Authenticate candidate bytes BEFORE any candidate code executes.
                signature = signature_verifier(snapshot, trust)
                if signature.get("code") != "passed" or signature.get("exit_code") != 0:
                    raise GateError("candidate_signature_failed")
                entry["checks"].append(signature)
                if runner is None:
                    runner = (runner_factory(receipt, evidence=evidence) if evidence is not None
                              else runner_factory(receipt))
                for check in checks:
                    if check.name == "public-signatures":
                        continue
                    result = runner.run(snapshot, check)
                    entry["checks"].append(result)
                    if result["exit_code"] != 0 or result["code"] != "passed":
                        raise GateError(result["code"])
        except GateError as exc:
            entry.update(status="failed", code=exc.code)
            report.update(status="failed", code="candidate_failed")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--pre-push", nargs=2, metavar=("REMOTE", "LOCATION"))
    choice.add_argument("--commit", help="full object ID, never an implicit HEAD")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--trust", type=Path, required=True)
    parser.add_argument("--previous-hook", type=Path)
    parser.add_argument("--evidence-dir", type=Path,
                        help="private directory outside candidate; file-backed raw diagnostics, no disk quota")
    parser.add_argument("--owner-health", type=Path,
                        help="separate optional read-only check requiring 15 signatures")
    args = parser.parse_args(argv)
    raw = sys.stdin.buffer.read() if args.pre_push else b""
    result = {"schema_version": 1, "status": "failed", "bypass": False,
              "refs": [], "commits": []}
    try:
        repo = args.repo.resolve()
        if os.environ.get("DIWAN_SKIP_GATE") == "1":
            result.update(status="bypassed", code="explicit_DIWAN_SKIP_GATE", bypass=True)
            # Retain exact object evidence on a deliberate bypass when valid.
            oid_size = 64 if git(repo, "rev-parse", "--show-object-format").strip() == b"sha256" else 40
            if args.pre_push:
                result["refs"] = parse_updates(raw.decode("utf-8"), oid_size, allow_delete=True)
        else:
            outside(TRUSTED_ROOT, repo)
            receipt, trust = load_configuration(args.runtime, args.trust, repo)
            oid_size = 64 if git(repo, "rev-parse", "--show-object-format").strip() == b"sha256" else 40
            updates = (parse_updates(raw.decode("utf-8"), oid_size) if args.pre_push else
                       [{"local_ref": "explicit_commit", "local_oid": args.commit,
                         "remote_ref": None, "remote_oid": None}])
            result = evaluate(repo, updates, receipt, trust, evidence_dir=args.evidence_dir)
            if args.owner_health:
                try:
                    health = verify_public(args.owner_health.resolve(), trust)
                    if health["verified"] != 15:
                        raise GateError("owner_inventory_mismatch")
                    result["owner_health"] = health
                except GateError as exc:
                    result["owner_health"] = {"code": exc.code, "exit_code": 1}
                    result.update(status="failed", code="owner_health_failed")
    except GateError as exc:
        result.update(status="failed", code=exc.code)
    except (OSError, UnicodeError, ValueError):
        result.update(status="failed", code="gate_input_or_io_failed")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    if result["status"] == "failed":
        return 1
    if args.previous_hook and args.pre_push:
        # Preserve the previous hook's stdin, argv, cwd and environment. Only our
        # gate is skipped by DIWAN_SKIP_GATE; the previous hook still runs.
        try:
            return subprocess.run([str(args.previous_hook), *args.pre_push],
                                  input=raw, cwd=args.repo).returncode
        except OSError:
            print(json.dumps({"status": "failed", "code": "previous_hook_failed"}))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
