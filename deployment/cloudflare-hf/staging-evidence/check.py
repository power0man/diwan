"""Offline packet consistency checker; never a live-service certificate."""
import argparse
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

GATES = ("live_access", "csrf", "anonymous_denied", "no_secret_leak",
         "no_cache_reuse", "mac_off_turn", "durability", "forgetting",
         "effect_idempotency", "capability_acceptance")
STATES = ("passed", "failed", "blocked", "not_run")
UPSTREAM = "https://hussain091-diwan-cloud-limited.hf.space"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def local(root, name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("invalid_local_path")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts) or name.startswith("/"):
        raise ValueError("invalid_local_path")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("symlink_refused")
    if not path.is_file():
        raise ValueError("artifact_missing")
    return path


def load(path):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate_json_key")
            out[key] = value
        return out
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def typed_equal(left, right):
    """Exact JSON types matter: false, 0, and 0.0 are different bindings."""
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(typed_equal(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def json_value(value):
    if type(value) is dict:
        return all(type(k) is str and json_value(v) for k, v in value.items())
    if type(value) is list:
        return all(json_value(v) for v in value)
    if type(value) is float:
        return math.isfinite(value)
    return value is None or type(value) in (str, int, bool)


def text(value):
    return type(value) is str and bool(value.strip())


def valid_checksum(value, length=64):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def https_origin(value):
    if not text(value) or "\\" in value or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
        return (parsed.scheme == "https" and bool(parsed.hostname)
                and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                        for label in parsed.hostname.split("."))
                and parsed.username is None and parsed.password is None
                and not parsed.path and not parsed.query and not parsed.fragment
                and (port is None or 1 <= port <= 65535)
                and not value.endswith((":", "?", "#")))
    except ValueError:
        return False


def policy_schema(policy, manifest):
    if type(policy) is not dict or type(manifest) is not dict or not json_value(policy) or not json_value(manifest):
        raise ValueError("json_object_required")
    if (type(policy.get("schema_version")) is not int or policy["schema_version"] != 1
            or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1):
        raise ValueError("schema_version")
    if not valid_checksum(policy.get("product_sha"), 40):
        raise ValueError("full_product_sha_required")
    for key in ("source_sha256", "bundle_sha256", "worker_sha256"):
        if not valid_checksum(policy.get(key)):
            raise ValueError("invalid_" + key)
    if not text(policy.get("run_id")):
        raise ValueError("run_id_required")
    destination = policy.get("destination")
    if (type(destination) is not dict
            or set(destination) != {"upstream", "space", "public_origin", "access_aud"}
            or any(not text(destination.get(k)) for k in destination)
            or destination["upstream"] != UPSTREAM
            or destination["space"] != "Hussain091/diwan-cloud-limited"
            or not https_origin(destination["public_origin"])):
        raise ValueError("fixed_destination_required")
    model = policy.get("model")
    if (type(model) is not dict or set(model) != {"id", "sha256"}
            or not text(model["id"]) or not valid_checksum(model["sha256"])):
        raise ValueError("model_fingerprint_required")
    limits = policy.get("capability_limits")
    if (type(limits) is not dict or not text(limits.get("deployment_mode"))
            or not text(limits.get("storage"))):
        raise ValueError("capability_limits_required")
    for key, value in limits.items():
        if key.endswith("_enabled") and type(value) is not bool:
            raise ValueError("capability_enabled_boolean_required")
    if "disabled" in limits and (type(limits["disabled"]) is not list
                                 or any(not text(item) for item in limits["disabled"])):
        raise ValueError("disabled_capabilities_list_required")


def stamp(value):
    if not text(value):
        raise ValueError("timestamp_string_required")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("timestamp_invalid") from None
    if result.tzinfo is None:
        raise ValueError("timezone_required")
    return result


def validate(root, policy, manifest):
    """Policy is independently supplied by reviewer, never inferred from packet."""
    report = {"status": "blocked", "eligible_for_review": False,
              "live_certified": False,
              "gates": {name: {"status": "not_run"} for name in GATES}, "errors": [],
              "limits": ["offline_consistency_only", "unsigned_collector_claims",
                         "no_network_or_service_execution", "not_merge_or_deploy_permission"]}
    try:
        root = Path(root)
        if root.is_symlink():
            raise ValueError("symlink_refused")
        policy_schema(policy, manifest)
        sha = policy["product_sha"]
        start, end = stamp(policy["not_before"]), stamp(policy["not_after"])
        if start > end:
            raise ValueError("invalid_window")
        binding_keys = ("product_sha", "source_sha256", "bundle_sha256", "worker_sha256",
                        "destination", "model", "capability_limits", "run_id")
        expected = {k: policy[k] for k in binding_keys}
        if not typed_equal(manifest["binding"], expected):
            raise ValueError("binding_mismatch")
        model = policy["model"]
        source_path = local(root, "bundle/SOURCE.json")
        if digest(source_path) != policy["source_sha256"]:
            raise ValueError("source_digest_mismatch")
        source = load(source_path)
        if (type(source) is not dict or not json_value(source) or not valid_checksum(source.get("source_commit"), 40)
                or not text(source.get("model")) or not text(source.get("deployment_mode"))
                or type(source.get("files_sha256")) is not dict
                or any(not text(k) or not valid_checksum(v) for k, v in source["files_sha256"].items())):
            raise ValueError("source_schema_invalid")
        if source["source_commit"] != sha:
            raise ValueError("source_commit_mismatch")
        if source["model"] != model["id"]:
            raise ValueError("source_model_mismatch")
        if source["deployment_mode"] != policy["capability_limits"]["deployment_mode"]:
            raise ValueError("source_mode_mismatch")
        files = {}
        for path in (root / "bundle").rglob("*"):
            if path.is_symlink():
                raise ValueError("symlink_refused")
            if path.is_file() and path != source_path:
                files[path.relative_to(root / "bundle").as_posix()] = digest(path)
        if not files or files != source["files_sha256"]:
            raise ValueError("bundle_file_inventory_mismatch")
        if canonical(files) != policy["bundle_sha256"]:
            raise ValueError("bundle_digest_mismatch")
        if digest(local(root, "worker.mjs")) != policy["worker_sha256"]:
            raise ValueError("worker_digest_mismatch")
        if digest(local(root, manifest["model_file"])) != model["sha256"]:
            raise ValueError("model_digest_mismatch")
        gates = manifest["gates"]
        if not isinstance(gates, dict) or set(gates) - set(GATES):
            raise ValueError("unknown_gate")
        for name in GATES:
            gate = gates.get(name, {"status": "not_run"})
            if type(gate) is not dict or type(gate.get("status")) is not str or gate["status"] not in STATES:
                report["gates"][name] = {"status": "failed", "reason": "invalid_gate_status"}
                continue
            status = gate["status"]
            ephemeral = name == "durability" and source["deployment_mode"] == "private_hf_cpu_ephemeral_limited"
            if status != "passed":
                report["gates"][name] = {"status": status, "reason": gate.get("reason", "no_passing_evidence")}
                if ephemeral:
                    report["gates"][name]["blocker"] = "source_declares_ephemeral_storage"
                continue
            if ephemeral:
                report["gates"][name] = {"status": "blocked", "reason": "source_declares_ephemeral_storage",
                                         "declared_status": status}
                continue
            try:
                evidence_path = local(root, gate["evidence_file"])
                if digest(evidence_path) != gate["evidence_sha256"]:
                    raise ValueError("evidence_digest_mismatch")
                evidence = load(evidence_path)
                if not typed_equal(evidence["binding"], expected) or evidence["gate"] != name:
                    raise ValueError("evidence_binding_mismatch")
                if evidence["status"] != "passed" or evidence["kind"] != "live":
                    raise ValueError("live_evidence_required")
                if not start <= stamp(evidence["collected_at"]) <= end:
                    raise ValueError("historical_or_future_evidence")
                observations = evidence["observations"]
                required = {
                    "live_access": ("runtime_access", "owner_allowed", "forged_denied"),
                    "mac_off_turn": ("mac_off", "complete_turn"),
                    "durability": ("restart_recovered", "session_recovered", "receipts_recovered"),
                    "forgetting": ("forgotten", "restart_absent", "cross_project_absent"),
                    "effect_idempotency": ("replayed", "single_effect", "receipt_reused"),
                }.get(name, ("verified",))
                if not isinstance(observations, dict) or any(observations.get(k) is not True for k in required):
                    raise ValueError("required_observation_missing")
                logs = evidence["artifacts"]
                if not isinstance(logs, dict) or not logs:
                    raise ValueError("evidence_artifacts_required")
                for path, checksum in logs.items():
                    artifact = local(root, path)
                    if artifact.stat().st_size == 0 or digest(artifact) != checksum:
                        raise ValueError("evidence_artifact_mismatch")
                report["gates"][name] = {"status": "passed"}
            except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
                report["gates"][name] = {"status": "failed", "reason": str(error)}
        statuses = [v["status"] for v in report["gates"].values()]
        report["status"] = ("failed" if "failed" in statuses else
                            "blocked" if "blocked" in statuses else
                            "not_run" if "not_run" in statuses else "passed")
        report["eligible_for_review"] = report["status"] == "passed"
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
        report["status"] = "failed"
        report["errors"].append(str(error))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = validate(args.packet, load(args.policy), load(local(args.packet, "manifest.json")))
    except (ValueError, OSError, TypeError) as error:
        report = {"status": "failed", "eligible_for_review": False,
                  "live_certified": False, "errors": [str(error)]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["eligible_for_review"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
