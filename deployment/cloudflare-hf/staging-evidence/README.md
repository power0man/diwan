# Offline staging evidence consistency

This standalone standard-library checker detects stale or incomplete evidence
packets. It performs no network calls, deployment, resource creation, model
inference, or credential handling. It never changes `prepare.py` or deployment
settings. `passed` means the supplied packet is internally consistent with a
separately supplied reviewer policy; it is **not** live certification, service
acceptance, merge approval, or permission to stop the Mac.

At integration base `fbea5fa52d01a82756faf2ae91308b89614f9a7a`, `prepare.py`
exports product SHA `9e62b7561e9e8931d16a02cfba65459c25a8501a`. A deployment
pin may differ from the current `main`; inspect the actual prepared source.
The actual `SOURCE.json` must match the selected full product SHA. A policy
declaring `storage: "ephemeral"` blocks a claimed durability pass in every
deployment mode. The known `private_hf_cpu_ephemeral_limited` source mode also
blocks that claim even if the policy incorrectly declares durable storage. Explicit
`failed`, `blocked`, and `not_run` observations retain their status and reason;
the storage limitation is recorded separately as `blocker`. A missing durability
record remains `not_run`, also with that blocker. This task does not repair storage.

## Invocation

From this directory, using the selected Python interpreter:

```sh
python check.py /path/to/packet --policy /separate/reviewer-policy.json > report.json
python -m unittest discover -s . -v
python mutate.py
```

Exit code 0 means `eligible_for_review: true`; every other packet exits 1.
Output preserves each gate's `passed`, `failed`, `blocked`, or `not_run` status.
Missing gates are `not_run`; inconsistent claimed passes become `failed`.
Passed is assigned only after validation. Malformed timestamps fail their own
gate with a named error while the other gates continue to be checked.
Structural rejection leaves unchecked gates `not_run`. Aggregate priority is
failed, blocked, not_run, passed. `live_certified` is always false.

## Reviewer policy (schema version 1)

Supply this independently of the packet. Do not generate expectations from an
untrusted manifest or silently choose its SHA, audience, run ID, or time window.
Before each acceptance attempt, select a **new** run ID and bounded timezone-aware
window. Reusing an old policy permits rechecking old evidence; it does not create
a new acceptance attempt. The checker deliberately does not query GitHub or guess
what the current product head should be.

Required policy keys:

| Key | Meaning |
| --- | --- |
| `schema_version` | Exact JSON integer `1`; Boolean `true` and float `1.0` are rejected |
| `product_sha` | Selected full 40-character lowercase commit SHA |
| `source_sha256` | Hash of exact bytes of actual `bundle/SOURCE.json` |
| `bundle_sha256` | Canonical hash of complete bundle file/hash inventory excluding `SOURCE.json` |
| `worker_sha256` | Hash of actual `worker.mjs` deployment candidate bytes |
| `destination` | Object with `space`, `upstream`, `public_origin`, `access_aud` |
| `model` | Object with explicit `id` and SHA-256 of supplied model weight artifact |
| `capability_limits` | Object with nonempty string `deployment_mode` and exact `storage` class `durable` or `ephemeral`, plus actual capability boundaries |
| `run_id` | Fresh reviewer-selected attempt identifier |
| `not_before`, `not_after` | ISO-8601 times with timezone, bounding this attempt |

The fixed Space is `Hussain091/diwan-cloud-limited`; its only accepted upstream is
`https://hussain091-diwan-cloud-limited.hf.space`. Origin and audience must exactly
match the independent policy; the audience must be nonempty. No alternative
upstream can be accepted by rewriting policy and packet together.

Identity, audience, model ID, and deployment/storage modes must be nonempty
strings; fingerprints must be lowercase hexadecimal SHA values of the stated
length. Origin must be an HTTPS DNS origin with a host and optional valid port,
without credentials, path, query, fragment, whitespace, or backslashes. Destination
has exactly the four stated fields; model has exactly `id` and `sha256`.
Optional capability fields ending in `_enabled` require JSON Booleans; optional
`disabled` is a list of nonempty capability names. Other boundary details may be
finite JSON data, and matching bindings preserve exact types recursively:
`false`, `0`, and `0.0` are different. These fields declare boundaries; they do
not measure or prove that the service enforces them.

`storage` is a closed, case-sensitive classification: only `durable` and
`ephemeral` are accepted. Unknown values, aliases such as `persistent` or
`temporary`, and labels such as `synthetic` fail closed with
`storage_classification_required`; malformed/empty values remain schema errors.
The class describes the claimed storage boundary, not the provenance of the
test data. A synthetic test packet can declare durable storage to exercise the
positive consistency path, but that declaration never proves persistence.
`durable` permits a durability gate to be checked; it cannot by itself pass the
gate or override a source mode known to be ephemeral. An ephemeral policy adds
`policy_declares_ephemeral_storage`; the known ephemeral source takes precedence
with `source_declares_ephemeral_storage`. Explicit non-passing states and reasons
are preserved, with the storage constraint recorded separately as `blocker`.

Canonical inventory hashing is SHA-256 of UTF-8 JSON with sorted keys,
`separators=(",", ":")`, and `ensure_ascii=False` (see `canonical`). File hashes
cover actual bytes, including extra-file detection. `SOURCE.json` uses the
existing prepare format: `source_commit`, `model`, `deployment_mode`, and
`files_sha256`. Its model and deployment mode must also match policy.

## Packet layout and records

```text
manifest.json
bundle/SOURCE.json
bundle/...             # exact exported product and deployment files
worker.mjs
model.bin              # actual chosen weight artifact; no downloading here
evidence/<gate>.json
logs/...               # nonempty, redacted collector artifacts
```

`manifest.json` contains `schema_version: 1`, a `binding` object with all policy
keys **except** `schema_version`, `not_before`, and `not_after`, `model_file`
(a relative local path), and a `gates` object. A gate record contains `status`
and optional `reason`. A claimed pass must also contain `evidence_file` and
`evidence_sha256`. Unknown gates and statuses fail closed. Duplicate JSON keys,
path traversal, absolute artifact paths, and symlink artifacts are rejected.

Every passed gate's JSON evidence must contain its exact `gate` name, the same
`binding`, `status: "passed"`, `kind: "live"`, timezone-aware `collected_at`,
`observations`, and a nonempty `artifacts` map of local path to actual SHA-256.
`synthetic`, `mock`, and `historical` kinds never qualify. A historical timestamp
or previous run ID never becomes a new attempt merely because the SHA is equal.

Required gates and exact boolean `true` observations:

| Gate | Observations |
| --- | --- |
| `live_access` | `runtime_access`, `owner_allowed`, `forged_denied` |
| `csrf`, `anonymous_denied`, `no_secret_leak`, `no_cache_reuse` | `verified` for each gate |
| `mac_off_turn` | `mac_off`, `complete_turn` |
| `durability` | `restart_recovered`, `session_recovered`, `receipts_recovered` |
| `forgetting` | `forgotten`, `restart_absent`, `cross_project_absent` |
| `effect_idempotency` | `replayed`, `single_effect`, `receipt_reused` |
| `capability_acceptance` | `verified`, with collector artifacts describing the selected capability boundaries |

Collector records and logs are unsigned assertions. Matching hashes and a `live`
label cannot establish that a collector told the truth, authenticated through
Cloudflare, ran the selected model, or performed a real restart. The checker does
not interpret arbitrary logs, verify identity, inspect remote state, measure
quality, or certify current availability. A reviewer must inspect authentic,
redacted live records before operational acceptance. Tests explicitly show that
an entirely synthetic but consistently labeled packet passes consistency while
`live_certified` stays false. Never include credentials or private conversations.

## Tests and repository integration

The checker, standalone tests, and standalone mutation records live here. `mutate.py`
copies only the checker and tests to disposable temporary directories, verifies
the baseline, applies one syntactically valid guard mutation, and requires the
named test to fail. It never modifies the real checkout under test.

`tests/test_staging_evidence.py` collects the same 32 test methods in the
repository's standard pytest suite without retaining a generic `check` import.
Its pytest adapter fails a method on the first failed subcase so the existing
mutation runner can attribute a normal `FAILED` node; standalone unittest runs
retain their subtest diagnostics. All subcases still run when they pass.
`tests/mutations/test_staging_evidence.jsonl` maps the 40 standalone mutations
to canonical pytest node IDs, including a fail-closed mutation for missing
schema fields. The required verification pipeline discovers these through its
existing pytest and mutation-check steps; workflow and review requirements are
unchanged. From the repository root, run:

```sh
python -m pytest tests/test_staging_evidence.py
python tools/mutation_check.py --manifest tests/mutations/test_staging_evidence.jsonl
```

The canonical mutation runner checks committed content in detached worktrees.
Generated repository counts include this adapter and the standalone sources.
No Mac proof, deployment settings, or deployment pin is changed by this checker.
