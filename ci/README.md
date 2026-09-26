# Reviewed CI supervisor and disposable workers

The code now keeps **the supervisor** alive and replaces **the worker container**
after every job. `run_persistent_runner.py` retains its old filename for callers;
it no longer requests a persistent runner. `start_isolated_runner.py` and the
image entrypoint refuse the former `--persistent` mode. The entrypoint always
configures `--ephemeral --disableupdate` and refuses existing runner state.

**Implementation is not activation.** These source changes do not stop, replace,
or attest the live runner. The earlier persistent-container arrangement adopted
under ق٤٣ remains a historical deployment fact until the cutover below is completed.
The owner-approved revised ق٤٣ governs the new activation.

## Trust and image preparation

Run bootstrap code only from an owner-reviewed installed copy **outside candidate
checkouts**. Its private state directory, image receipts and public trust receipt
also stay outside candidate-writable locations. The supervisor is privileged
in relation to workers: it can call Docker and obtain repository registration
tokens through the owner's authenticated `gh`. Workers receive neither capability.

1. Review `requirements-ci.lock`, Dockerfiles, bootstrap and public-key identity.
2. Use `prepare_runtime.py` from the trusted copy, with official Python 3.14 and
   Node 24 image references pinned by digest, the hash-locked requirements,
   external `--trust-json`, and fresh `--receipt` / `--runner-receipt` paths.
   Building does not register or launch a runner.
3. The new runner image and receipt must say `ephemeral-only-v1` through
   `diwan.runner-mode` and `runner_mode` respectively. The bootstrap compares
   image ID, runner version, mode and dependency-lock hash. An old receipt/image
   is refused; changing its JSON label cannot change the inspected image label.
4. Keep previous images and receipts during the canary. Avoid broad Docker prune
   or updates to other projects. Runtime image contents have not changed; this
   version requires a new runner image because its entrypoint changed.

**Platforms.** `requirements-ci.lock` carries macOS arm64 and Linux arm64 wheel
hashes only, so pip refuses `cffi` and `cryptography` on Linux x86_64 (Nitro).
Nitro builds the runtime alone with `--platform linux/amd64 --lock
ci/requirements-ci.all-platforms.lock`: the same pins and every hash of
`requirements-ci.lock`, plus one Linux x86_64 hash for each platform wheel
(`tests/test_ci_lock_platforms.py`). The root lock stays unchanged for now,
because `tools/verify_gate.py` matches each candidate's lock against the Mac
receipt. At the next lock change the all-platforms lock replaces it, and the
Mac rebuilds its receipt then. The runner image remains linux/arm64 only.

A receipt is a reviewed local identity record, not an attestation against a
hostile Docker daemon or host owner. Runner v2.337.0 remains pinned with its
reviewed official checksum. Review upstream release requirements before building;
GitHub's runner update window means this is an operated cache, not a permanent freeze.

The supervisor freezes one receipt byte fingerprint for its entire lifetime. Each
checkpoint and child command carries that same fingerprint. Bootstrap verifies it
before inspecting the image or reading registration stdin, and uses the parsed
snapshot throughout that run. Updating a receipt requires a reviewed supervisor
restart; it never silently selects a different image for the next worker. Receipt
leaves must be independent regular files, not symbolic links or hard links.

## Lifecycle and explicit failure states

The supervisor takes an exclusive OS lock for the repository in one designated
private `--state-dir`. Use **the same state directory for every supervisor of that
repository**; deliberately selecting separate state directories bypasses that
single-supervisor guard. Do not run an independent one-shot bootstrap alongside it.
The directory is opened through exact directory entries with no symbolic-link,
case or Unicode spelling aliases. Its open descriptor anchors lock and checkpoint
operations. A changed directory or lock identity stops execution; a replaced
checkpoint is preserved instead of being deleted by the earlier worker. Keep the
directory and its ancestors stable and outside candidate access. This does not
defend against a hostile host owner who can replace the supervisor or its storage.

For each cycle:

1. Obtain a new short-lived registration token through `gh api`. No API response,
   token, credential or token-bearing exception is printed or saved. Failed API
   attempts time out after 30 seconds and produce a closed diagnostic code.
2. Allocate a new unique worker name and fsync an `*.active.json` checkpoint with
   its name, repository and receipt hash. This file contains no token.
3. Send the token once over stdin to the reviewed bootstrap. It travels over stdin
   into Docker, then only the configuration child's transient environment. No
   token is placed in argv, a shell command, Docker environment, or a state file.
4. Run one ephemeral GitHub worker. The bootstrap removes the container on normal
   completion, process failure, deadline, SIGINT or SIGTERM. It first stops and
   reaps the Docker client, then performs the final disposal/absence proof, so a
   delayed client cannot create the container after that proof. An unreaped client
   is an unverified cleanup even if the container currently appears absent. The
   supervisor then independently verifies disposal before accepting the exit status.
5. Remove and fsync the checkpoint only after that cleanup proof. Only then may
   the next registration begin. A successful worker exits before the next starts.

`container_cleanup_unverified` / exit 3 stops the supervisor immediately and keeps
the checkpoint. Unknown child exits do the same. An unclean supervisor restart
sees that checkpoint and refuses with `previous_worker_cleanup_required`; it does
not assume that a vanished process means a vanished container. The lock rejects a
second supervisor with `supervisor_already_running` before it registers anything.

Registration and worker errors have a consecutive failure budget (default 5) with
exponential backoff (5, 10, 20, 40 seconds; configurable cap 60). Success resets it.
Budget exhaustion stops with `supervisor_failure_budget_exhausted`; no endless
rapid registration loop is installed. One second separates successful cycles.
The supervisor **does not request workflow/job reruns** and does not repeat an
external action whose outcome is unknown; GitHub remains the job scheduler.

A worker lifetime deadline includes queue wait (default 7200 seconds, bounded
60–86400). A confirmed disposal after lifetime expiry is logged as status 124 and
rotates the worker without consuming the idle-error budget. This is **not** a
passing-job result: expiry can interrupt a job assigned near the deadline.
GitHub's exact job/run conclusion must be checked, and a rerun needs an explicit
operator decision. Choose the configured lifetime with this limit in view.

## Isolation actually provided

Workers have UID 1000, dropped Linux capabilities, `no-new-privileges`, read-only
root, bounded CPU/memory/processes, and fresh tmpfs storage for runner/workspace.
There are no owner HOME mounts, project-data mounts, Docker socket, HMAC secrets,
private Ed25519 keys or inherited host environment variables. Configuration output
is discarded. GitHub runtime credentials stay in tmpfs until disposal.

One fresh container per job prevents normal filesystem carry-over between jobs.
`--ephemeral` alone does not isolate the host. Docker Desktop's shared VM/kernel
and network remain trusted boundaries; the online runner can reach network
services unless separately restricted. This design does not claim VM-escape
protection or egress isolation. No nested Docker/container actions are available.

## Activation plan on the owner's machine — not performed by this patch

1. Inventory the live runner's name, registration, process and busy state. Preserve
   its current frozen bootstrap, receipt and logs; do not interrupt an active job.
2. Install the reviewed new bootstrap to a new external trusted directory, and
   build the new immutable runner image/receipt. Validate the command below first
   with `--dry-run`: it checks local configuration and executable availability
   only, **not** Docker image presence, GitHub auth, registration or successful CI.
3. Start a bounded canary with its separate label and a private state directory.
   Queue two reviewed test jobs specifically targeting `diwan-isolated-canary`.
   Neither this command nor the label alone creates those jobs. Verify distinct
   runner names, different container identities, cleanup between jobs, and that a
   marker created by the first job is absent in the second.

   ```sh
   /absolute/reviewed/python /absolute/trusted/ci/run_persistent_runner.py \
     --runner-receipt /absolute/trusted/runner-ephemeral.json \
     --repository https://github.com/power0man/diwan-private \
     --state-dir /absolute/trusted/supervisor-canary \
     --label diwan-isolated-canary --max-workers 2
   ```

4. Check token redaction, queue pickup, intentionally failing job reporting, and
   timeout/termination disposal. Match all acceptance results to the tested SHA.
5. After canary acceptance, let the old worker finish, then stop its old supervisor
   and remove its registration by exact recorded identity. No unrelated runner or
   launchd service is changed. Start the new supervisor with `diwan-isolated` and
   one designated production state directory. Never run both production modes
   concurrently. Verify a real production-label job and disposal before closure.
6. Only then register the reviewed supervisor as an owner login service if that
   activation is included in approval. Use a fixed interpreter and installed path,
   private logs, `RunAtLoad`, and no unconditional `KeepAlive`: the supervisor itself
   loops; a failure budget/cleanup refusal must not be bypassed by rapid service
   relaunch. Docker and the owner's login environment must already be available.
   No claim is made that this runs while the Mac sleeps or is powered off.

## Recovery and rollback gates

On a pending checkpoint, inspect only its recorded worker name and repository.
Prove that exact container absent, inspect its GitHub registration, and remove only
that owned registration if still present and safe to remove. Ephemeral registration
normally disappears after its job; abrupt failures can leave an offline registration.
This supervisor does not silently delete registrations. Preserve the checkpoint and
logs as evidence, then archive the checkpoint outside the active filename before
restarting. Never erase the checkpoint merely to obtain a green startup.

Stop/review if cleanup cannot be proved, a job observes previous-job files, a token
appears in output, the image differs from its receipt, or candidate code reaches an
owner resource. Source/receipt rollback does not authorize returning to a persistent
shared workspace. The safe fallback is the reviewed single-use runner under manual
operation, with the same fresh-container guarantees. Re-enable any weaker historical
mode only under a separately explicit decision.

References:

- [GitHub ephemeral runners and update policy](https://docs.github.com/en/actions/reference/runners/self-hosted-runners#ephemeral-runners-for-autoscaling)
- [GitHub self-hosted runner security](https://docs.github.com/en/actions/reference/security/secure-use#hardening-for-self-hosted-runners)
- [Docker Desktop host networking boundary](https://docs.docker.com/desktop/features/networking/networking-how-tos/)
- [Pinned runner release and checksum](https://github.com/actions/runner/releases/tag/v2.337.0)
