"""Synthetic packet tests only. No deployment or model execution."""
import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.write("bundle/app.py", "product")
        self.write("worker.mjs", "bridge")
        self.write("model.bin", "synthetic weights")
        self.write("logs/turn.txt", "synthetic test log; never live proof")
        files = {"app.py": check.digest(self.root / "bundle/app.py")}
        self.source = {"source_commit": "a" * 40, "model": "qwen3.5:9b",
                       "deployment_mode": "test_only", "files_sha256": files}
        self.write_json("bundle/SOURCE.json", self.source)
        self.policy = {"schema_version": 1, "product_sha": "a" * 40,
                       "run_id": "new-staging-attempt-2", "not_before": "2026-10-02T10:00:00Z",
                       "not_after": "2026-10-02T11:00:00Z",
                       "source_sha256": check.digest(self.root / "bundle/SOURCE.json"),
                       "bundle_sha256": check.canonical(files),
                       "worker_sha256": check.digest(self.root / "worker.mjs"),
                       "destination": {"space": "Hussain091/diwan-cloud-limited",
                                       "upstream": check.UPSTREAM,
                                       "public_origin": "https://diwan.260911765.xyz",
                                       "access_aud": "synthetic-audience"},
                       "model": {"id": "qwen3.5:9b", "sha256": check.digest(self.root / "model.bin")},
                       "capability_limits": {"deployment_mode": "test_only", "storage": "durable"}}
        binding = {k: v for k, v in self.policy.items() if k not in
                   ("schema_version", "not_before", "not_after")}
        self.manifest = {"schema_version": 1, "binding": binding, "model_file": "model.bin", "gates": {}}
        obs = dict.fromkeys(("runtime_access", "owner_allowed", "forged_denied", "mac_off", "complete_turn",
                             "restart_recovered", "session_recovered", "receipts_recovered", "forgotten",
                             "restart_absent", "cross_project_absent", "replayed", "single_effect",
                             "receipt_reused", "verified"), True)
        for name in check.GATES:
            self.write_json("evidence/" + name + ".json", {
                "gate": name, "binding": binding, "kind": "live", "status": "passed",
                "collected_at": "2026-10-02T10:30:00Z", "observations": obs,
                "artifacts": {"logs/turn.txt": check.digest(self.root / "logs/turn.txt")}})
            self.refresh(name)

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)

    def write_json(self, path, value):
        self.write(path, json.dumps(value))

    def refresh(self, name):
        file = "evidence/" + name + ".json"
        self.manifest["gates"][name] = {"status": "passed", "evidence_file": file,
                                        "evidence_sha256": check.digest(self.root / file)}

    def bind_all(self):
        self.manifest["binding"] = {k: copy.deepcopy(v) for k, v in self.policy.items()
                                    if k not in ("schema_version", "not_before", "not_after")}
        for name in check.GATES:
            self.alter(name, "binding", self.manifest["binding"])

    def alter(self, name, key, value):
        file = "evidence/" + name + ".json"
        evidence = check.load(self.root / file)
        evidence[key] = value
        self.write_json(file, evidence)
        self.refresh(name)

    def result(self):
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            return check.validate(self.root, self.policy, self.manifest)

    def assert_rejected(self):
        result = self.result()
        self.assertFalse(result["eligible_for_review"])
        self.assertNotEqual(result["status"], "passed")
        return result

    def test_complete_packet_consistent_but_never_live_certified(self):
        result = self.result()
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["eligible_for_review"])
        self.assertFalse(result["live_certified"])

    def test_selected_head_mismatch(self):
        self.manifest["binding"]["product_sha"] = "b" * 40
        self.assert_rejected()

    def test_current_ephemeral_mode_cannot_claim_durability(self):
        mode = "private_hf_cpu_ephemeral_limited"
        self.source["deployment_mode"] = mode
        self.policy["capability_limits"]["deployment_mode"] = mode
        self.write_json("bundle/SOURCE.json", self.source)
        checksum = check.digest(self.root / "bundle/SOURCE.json")
        self.policy["source_sha256"] = checksum
        self.manifest["binding"]["source_sha256"] = checksum
        for name in check.GATES:
            self.alter(name, "binding", self.manifest["binding"])
        result = self.assert_rejected()
        self.assertEqual(result["gates"]["durability"]["status"], "blocked")

    def test_ephemeral_preserves_observed_states_and_reasons(self):
        mode = "private_hf_cpu_ephemeral_limited"
        self.source["deployment_mode"] = mode
        self.policy["capability_limits"]["deployment_mode"] = mode
        self.write_json("bundle/SOURCE.json", self.source)
        self.policy["source_sha256"] = check.digest(self.root / "bundle/SOURCE.json")
        self.bind_all()
        for status in ("failed", "blocked", "not_run"):
            with self.subTest(status=status):
                self.manifest["gates"]["durability"] = {"status": status, "reason": "restart_lost_existing_receipts"}
                result = self.assert_rejected()
                gate = result["gates"]["durability"]
                self.assertEqual(gate["status"], status)
                self.assertEqual(gate["reason"], "restart_lost_existing_receipts")
                self.assertEqual(gate["blocker"], "source_declares_ephemeral_storage")
        self.manifest["gates"].pop("durability")
        self.assertEqual(self.assert_rejected()["gates"]["durability"]["status"], "not_run")

    def test_invalid_timestamp_is_named_and_other_gates_continue(self):
        for value in (None, [], 123, True, "", "not-a-time", "2026-10-02T10:30:00"):
            with self.subTest(value=value):
                self.alter("live_access", "collected_at", value)
                result = self.assert_rejected()
                gate = result["gates"]["live_access"]
                self.assertEqual(gate["status"], "failed")
                expected = ("timestamp_string_required" if type(value) is not str or not value.strip()
                            else "timestamp_invalid" if value == "not-a-time" else "timezone_required")
                self.assertEqual(gate["reason"], expected)
                for name in check.GATES[1:]:
                    self.assertEqual(result["gates"][name]["status"], "passed")

    def test_ephemeral_storage_blocks_durability_in_every_deployment_mode(self):
        self.policy["capability_limits"]["storage"] = "ephemeral"
        for mode in ("test_only", "future_cloud_mode", "private_hf_cpu_ephemeral_limited"):
            with self.subTest(mode=mode):
                self.source["deployment_mode"] = mode
                self.policy["capability_limits"]["deployment_mode"] = mode
                self.write_json("bundle/SOURCE.json", self.source)
                self.policy["source_sha256"] = check.digest(self.root / "bundle/SOURCE.json")
                self.bind_all()
                result = self.assert_rejected()
                self.assertEqual(result["status"], "blocked")
                expected = ("source_declares_ephemeral_storage" if mode == "private_hf_cpu_ephemeral_limited"
                            else "policy_declares_ephemeral_storage")
                self.assertEqual(result["gates"]["durability"], {
                    "status": "blocked", "reason": expected, "declared_status": "passed"})
                self.assertFalse(result["live_certified"])
                self.assertEqual(result["gates"]["live_access"]["status"], "passed")

    def test_ephemeral_storage_preserves_observed_states_and_reasons(self):
        self.policy["capability_limits"]["storage"] = "ephemeral"
        self.bind_all()
        for status in ("failed", "blocked", "not_run"):
            with self.subTest(status=status):
                self.manifest["gates"]["durability"] = {"status": status, "reason": "observed_restart_loss"}
                result = self.assert_rejected()
                self.assertEqual(result["status"], status)
                self.assertEqual(result["gates"]["durability"], {
                    "status": status, "reason": "observed_restart_loss",
                    "blocker": "policy_declares_ephemeral_storage"})
        self.manifest["gates"].pop("durability")
        self.assertEqual(self.assert_rejected()["gates"]["durability"], {
            "status": "not_run", "reason": "no_passing_evidence",
            "blocker": "policy_declares_ephemeral_storage"})

    def test_unknown_storage_classification_fails_closed(self):
        for storage in ("unknown", "temporary", "persistent", "synthetic", "durable-ish",
                        "DURABLE", "EPHEMERAL", " durable", "ephemeral "):
            with self.subTest(storage=storage):
                self.policy["capability_limits"]["storage"] = storage
                self.bind_all()
                result = self.assert_rejected()
                self.assertEqual(result["errors"], ["storage_classification_required"])
                self.assertEqual(result["gates"]["durability"]["status"], "not_run")

    def test_storage_classification_stays_bound_to_manifest(self):
        self.manifest["binding"] = copy.deepcopy(self.manifest["binding"])
        self.manifest["binding"]["capability_limits"]["storage"] = "ephemeral"
        self.assertIn("binding_mismatch", self.assert_rejected()["errors"])

    def test_storage_classification_stays_bound_to_evidence(self):
        binding = copy.deepcopy(self.manifest["binding"])
        binding["capability_limits"]["storage"] = "ephemeral"
        self.alter("durability", "binding", binding)
        result = self.assert_rejected()
        self.assertEqual(result["gates"]["durability"]["reason"], "evidence_binding_mismatch")
        self.assertEqual(result["gates"]["live_access"]["status"], "passed")

    def test_schema_version_requires_exact_integer(self):
        for target in (self.policy, self.manifest):
            for value in (True, 1.0, "1", None, []):
                with self.subTest(value=value):
                    target["schema_version"] = value
                    self.assertIn("schema_version", self.assert_rejected()["errors"])
                    target["schema_version"] = 1

    def test_typed_binding_matches_manifest_and_evidence(self):
        self.policy["capability_limits"]["execution_enabled"] = False
        self.bind_all()
        self.assertTrue(self.result()["eligible_for_review"])
        original = copy.deepcopy(self.manifest["binding"])
        for value in (0, 0.0):
            with self.subTest(location="manifest", value=value):
                self.manifest["binding"]["capability_limits"]["execution_enabled"] = value
                self.assertIn("binding_mismatch", self.assert_rejected()["errors"])
                self.manifest["binding"] = copy.deepcopy(original)
            with self.subTest(location="evidence", value=value):
                altered = copy.deepcopy(original)
                altered["capability_limits"]["execution_enabled"] = value
                self.alter("live_access", "binding", altered)
                result = self.assert_rejected()
                self.assertEqual(result["gates"]["live_access"]["reason"], "evidence_binding_mismatch")
                self.assertEqual(result["gates"]["csrf"]["status"], "passed")
                self.alter("live_access", "binding", original)

    def test_policy_identity_and_origin_schema(self):
        original = copy.deepcopy(self.policy)
        cases = [("destination", "access_aud", value, "fixed_destination_required")
                 for value in ([], ["not-a-string"], 123, True, "", " ")]
        cases += [("destination", "public_origin", value, "fixed_destination_required")
                  for value in ("https://", "https://host/path", "https://host?x=1", "https://a@host",
                                "https://host:bad", "https://host\\evil", "http://host", 123)]
        cases += [("model", "id", value, "model_fingerprint_required") for value in (123, [], True, "", " ")]
        cases += [("capability_limits", key, value, "capability_limits_required")
                  for key in ("deployment_mode", "storage") for value in (123, [], True, "", " ")]
        for group, key, value, reason in cases:
            with self.subTest(group=group, key=key, value=value):
                self.policy = copy.deepcopy(original)
                self.policy[group][key] = value
                self.bind_all()
                self.assertIn(reason, self.assert_rejected()["errors"])

    def test_policy_capability_boolean_and_invalid_hash(self):
        for value in (0, 1, "false", []):
            self.policy["capability_limits"]["execution_enabled"] = value
            self.bind_all()
            self.assertIn("capability_enabled_boolean_required", self.assert_rejected()["errors"])
        self.policy["capability_limits"].pop("execution_enabled")
        self.policy["source_sha256"] = []
        self.bind_all()
        self.assertIn("invalid_source_sha256", self.assert_rejected()["errors"])

    def test_actual_source_commit_old_even_when_digest_rebound(self):
        self.source["source_commit"] = "9e62b7561e9e8931d16a02cfba65459c25a8501a"
        self.write_json("bundle/SOURCE.json", self.source)
        checksum = check.digest(self.root / "bundle/SOURCE.json")
        self.policy["source_sha256"] = checksum
        self.manifest["binding"]["source_sha256"] = checksum
        self.assertIn("source_commit_mismatch", self.assert_rejected()["errors"])

    def test_destination_mismatch(self):
        self.manifest["binding"]["destination"] = {"upstream": "https://evil.invalid"}
        self.assert_rejected()

    def test_fixed_upstream_cannot_be_rebound(self):
        self.policy["destination"]["upstream"] = "https://evil.invalid"
        self.assertIn("fixed_destination_required", self.assert_rejected()["errors"])

    def test_bundle_tamper_and_extra_file(self):
        for file in ("bundle/app.py", "bundle/extra.py"):
            with self.subTest(file=file):
                self.write(file, "changed")
                self.assertIn("bundle_file_inventory_mismatch", self.assert_rejected()["errors"])

    def test_source_bytes_tamper(self):
        self.write("bundle/SOURCE.json", "{}")
        self.assertIn("source_digest_mismatch", self.assert_rejected()["errors"])

    def test_worker_and_model_tamper(self):
        for file in ("worker.mjs", "model.bin"):
            with self.subTest(file=file):
                original = (self.root / file).read_text()
                self.write(file, "changed")
                self.assert_rejected()
                self.write(file, original)

    def test_synthetic_auth_never_live(self):
        for kind in ("synthetic", "mock", "historical"):
            with self.subTest(kind=kind):
                self.alter("live_access", "kind", kind)
                self.assertEqual(self.assert_rejected()["gates"]["live_access"]["status"], "failed")

    def test_history_and_future_never_new_acceptance(self):
        for time in ("2026-09-27T10:30:00Z", "2026-10-03T10:30:00Z"):
            with self.subTest(time=time):
                self.alter("durability", "collected_at", time)
                self.assert_rejected()

    def test_old_run_id_on_same_product(self):
        binding = copy.deepcopy(self.manifest["binding"])
        binding["run_id"] = "previous-attempt"
        self.alter("forgetting", "binding", binding)
        self.assert_rejected()

    def test_missing_required_gates_preserves_not_run(self):
        for name in ("durability", "forgetting", "effect_idempotency"):
            gate = self.manifest["gates"].pop(name)
            result = self.assert_rejected()
            self.assertEqual(result["gates"][name]["status"], "not_run")
            self.manifest["gates"][name] = gate

    def test_statuses_preserved(self):
        for state in ("failed", "blocked", "not_run"):
            with self.subTest(state=state):
                self.manifest["gates"]["durability"] = {"status": state, "reason": "test"}
                result = self.assert_rejected()
                self.assertEqual(result["gates"]["durability"]["status"], state)
                self.assertEqual(result["status"], state)

    def test_observation_true_only(self):
        for name, field in (("live_access", "runtime_access"), ("durability", "restart_recovered"),
                            ("forgetting", "cross_project_absent"), ("effect_idempotency", "single_effect")):
            for value in (False, "true", 1, None):
                with self.subTest(name=name, value=value):
                    evidence = check.load(self.root / ("evidence/" + name + ".json"))
                    evidence["observations"][field] = value
                    self.alter(name, "observations", evidence["observations"])
                    self.assert_rejected()

    def test_missing_and_tampered_artifact(self):
        self.alter("durability", "artifacts", {})
        self.assert_rejected()
        self.write("logs/turn.txt", "tampered")
        self.assert_rejected()

    def test_evidence_digest_tamper(self):
        self.write("evidence/live_access.json", "{}")
        self.assertEqual(self.assert_rejected()["gates"]["live_access"]["reason"], "evidence_digest_mismatch")

    def test_path_escape_and_symlink(self):
        for path in ("../model.bin", "/tmp/model.bin", "model.bin/../model.bin"):
            self.manifest["model_file"] = path
            self.assert_rejected()
        (self.root / "linked.bin").symlink_to(self.root / "model.bin")
        self.manifest["model_file"] = "linked.bin"
        self.assert_rejected()

    def test_unknown_or_invalid_gate_fails(self):
        self.manifest["gates"]["durability"]["status"] = "success"
        self.assert_rejected()
        self.manifest["gates"]["unexpected"] = {"status": "passed"}
        self.assert_rejected()

    def test_duplicate_json_keys_rejected(self):
        self.write("duplicate.json", '{"status":"failed","status":"passed"}')
        with self.assertRaises(ValueError):
            check.load(self.root / "duplicate.json")

    def test_incomplete_schema_fails_closed(self):
        for key in tuple(self.policy):
            value = self.policy.pop(key)
            self.assert_rejected()
            self.policy[key] = value


if __name__ == "__main__":
    unittest.main()
