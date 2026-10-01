"""Bounded, offline checkpoint storage on a private HF Hub branch.

No bucket mount, lease expiry, lock stealing, provisioning, or live app sync.
A killed holder leaves the namespace busy. Recovery requires externally proven
quiescence; this adapter intentionally has no force-unlock operation.
"""
from contextlib import contextmanager
import base64
import hashlib
import json
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

from core.canonical import canonical_bytes
from workspace_tools.storage_scope import decode_scope

MAX_OBJECT = 1024 * 1024  # Deliberately smaller than the archive format's limit.
MAX_METADATA = 1024 * 1024
SCOPE_FILE = ".diwan-checkpoint-scope.json"


class StoreError(ValueError):
    pass


def _need(ok, code):
    if not ok:
        raise StoreError(code)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise StoreError("hf_redirect_refused")


class HubRemote:
    """Small regular Git objects only; explicit timeouts and no HTTP retries.

    Token is supplied by a trusted caller, never put in a URL or exception.
    A separate, already existing private non-main branch is required. This
    class creates no repos, branches, volumes, tokens, or paid resources.
    """
    def __init__(self, repo_type, repo_id, branch, token):
        _need(repo_type in {"space", "dataset"}
              and re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", repo_id)
              and re.fullmatch(r"diwan-checkpoint-[a-z0-9-]{1,80}", branch)
              and isinstance(token, str) and bool(token), "hf_target_invalid")
        self.repo_type, self.repo_id, self.branch = repo_type, repo_id, branch
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect)
        self._api = f"https://huggingface.co/api/{repo_type}s/{repo_id}"
        self._files = f"https://huggingface.co/{repo_type}s/{repo_id}/raw/"

    def _request(self, url, *, body=None, limit=MAX_METADATA):
        headers = {"Authorization": "Bearer " + self._token,
                   "Content-Type": "application/x-ndjson", "Cache-Control": "no-cache"}
        try:
            request = urllib.request.Request(url, data=body, headers=headers)
            with self._opener.open(request, timeout=30) as response:
                data = response.read(limit + 1)
            _need(len(data) <= limit, "hf_response_limit")
            return data
        except urllib.error.HTTPError as exc:
            raise StoreError("hf_head_conflict" if exc.code == 412 else "hf_http_failure") from None
        except (OSError, ValueError) as exc:
            if isinstance(exc, StoreError):
                raise
            raise StoreError("hf_transport_failure") from None

    def snapshot(self):
        try:
            value = json.loads(self._request(self._api + "/revision/" + self.branch))
            _need(type(value) is dict, "hf_metadata_invalid")
            _need(value.get("private") is True, "hf_private_required")
            sha = value["sha"]
            _need(type(sha) is str and re.fullmatch(r"[a-f0-9]{40}", sha), "hf_metadata_invalid")
            paths = [x["rfilename"] for x in value["siblings"]]
            _need(all(type(p) is str for p in paths) and len(paths) == len(set(paths)),
                  "hf_metadata_invalid")
            return sha, set(paths)
        except (KeyError, TypeError, json.JSONDecodeError):
            raise StoreError("hf_metadata_invalid") from None

    def read(self, revision, path):
        _need(re.fullmatch(r"[a-f0-9]{40}", revision), "hf_revision_invalid")
        return self._request(self._files + revision + "/" + urllib.parse.quote(path, safe="/"),
                             limit=MAX_OBJECT)

    def commit(self, revision, files):
        payload = [{"key": "header", "value": {
            "summary": "Publish synthetic Diwan offline checkpoint state",
            "description": "", "parentCommit": revision}}]
        for path, data in files.items():
            _need(type(data) is bytes and len(data) <= MAX_OBJECT, "hf_object_limit")
            payload.append({"key": "file", "value": {"path": path, "encoding": "base64",
                            "content": base64.b64encode(data).decode("ascii")}})
        try:
            raw = self._request(self._api + "/commit/" + self.branch,
                                body=b"\n".join(canonical_bytes(v) for v in payload) + b"\n")
            sha = json.loads(raw)["commitOid"]
            _need(type(sha) is str and re.fullmatch(r"[a-f0-9]{40}", sha), "hf_commit_unverified")
            return sha
        except (KeyError, TypeError, json.JSONDecodeError):
            raise StoreError("hf_commit_unverified") from None


class HubCheckpointStore:
    """Fenced by both a unique owner and the server's atomic Git parent CAS.

    Every method verifies the current private branch revision. A concurrent
    branch edit fences this holder out, including its release. No operation
    retries a commit with a newer parent. A namespace belongs to one workspace.
    """
    def __init__(self, remote, namespace):
        _need(type(namespace) is str and re.fullmatch(r"[a-f0-9]{32}", namespace),
              "hf_namespace_invalid")
        self.remote, self.namespace = remote, namespace
        self.prefix = "checkpoints/" + namespace + "/"
        self.control = self.prefix + "control.json"
        self._revision = self._state = self._owner = None
        self._verified_scope = None

    def _decode(self, raw):
        try:
            state = json.loads(raw)
            fields = {"schema", "namespace", "epoch", "owner", "head"}
            _need(type(state) is dict and set(state) in (fields, fields | {"scope_sha256"})
                  and type(state["schema"]) is int and state["schema"] == 1 and state["namespace"] == self.namespace
                  and type(state["epoch"]) is int and 1 <= state["epoch"] <= 2**53
                  and (state["owner"] is None or type(state["owner"]) is str
                       and re.fullmatch(r"[a-f0-9]{32}", state["owner"]))
                  and (state["head"] is None or type(state["head"]) is str and len(state["head"]) <= 4096)
                  and ("scope_sha256" not in state or type(state["scope_sha256"]) is str
                       and re.fullmatch(r"[a-f0-9]{64}", state["scope_sha256"]))
                  and canonical_bytes(state) == raw, "hf_control_invalid")
            return state
        except (TypeError, ValueError, RecursionError):
            raise StoreError("hf_control_invalid") from None

    def _scope_identity(self, raw):
        try:
            value = decode_scope(raw)
        except ValueError:
            raise StoreError("hf_scope_invalid") from None
        plan = value["record"]["plan"]
        target = {"provider": "hf_hub", "repo_type": getattr(self.remote, "repo_type", None),
                  "repo_id": getattr(self.remote, "repo_id", None),
                  "branch": getattr(self.remote, "branch", None), "namespace": self.namespace}
        _need(plan["target"] == target, "hf_scope_target_mismatch")
        return {"workspace_id": plan["workspace_id"], "contract_sha256": value["sha256"],
                "target": plan["target"]}

    def _scope_link(self, revision, paths, state):
        # The control hash also detects a missing branch binding. Never downgrade
        # a bound control to the legacy public-data protocol after local loss.
        _need((SCOPE_FILE in paths) == ("scope_sha256" in state), "hf_scope_binding_missing")
        if SCOPE_FILE in paths:
            raw = self.remote.read(revision, SCOPE_FILE)
            self._scope_identity(raw)
            _need(hashlib.sha256(raw).hexdigest() == state["scope_sha256"], "hf_scope_binding_corrupt")

    def _data_held(self):
        paths = self._held()
        _need("scope_sha256" not in self._state
              or self._verified_scope == self._state["scope_sha256"], "hf_scope_verification_required")
        return paths

    def bind_scope(self, scope_raw):
        """Bind one fresh branch under its lease, or verify the same binding.

        No adoption of old namespaces/claims, expiry, lock stealing or migration.
        Success is provisional until the surrounding lease exits successfully.
        """
        identity = self._scope_identity(scope_raw)
        paths = self._held()
        if SCOPE_FILE in paths:
            return self.verify_scope(scope_raw)
        _need(self._state["epoch"] == 1 and self._state["head"] is None
              and not any(p.startswith("checkpoints/") and p != self.control for p in paths),
              "hf_branch_not_pristine")
        sha = hashlib.sha256(scope_raw).hexdigest()
        self._publish({SCOPE_FILE: scope_raw}, {**self._state, "scope_sha256": sha})
        self._verified_scope = sha
        return identity

    def verify_scope(self, scope_raw):
        """Check an existing remote binding; never create or replace one."""
        identity = self._scope_identity(scope_raw)
        paths = self._held()
        _need(SCOPE_FILE in paths, "hf_scope_unbound")
        _need(self.remote.read(self._revision, SCOPE_FILE) == scope_raw, "hf_scope_mismatch")
        self._verified_scope = hashlib.sha256(scope_raw).hexdigest()
        return identity

    def _held(self):
        _need(self._owner is not None, "hf_lease_required")
        revision, paths = self.remote.snapshot()
        _need(revision == self._revision, "hf_lease_fenced")
        state = self._decode(self.remote.read(revision, self.control))
        _need(state == self._state and state["owner"] == self._owner, "hf_lease_fenced")
        self._scope_link(revision, paths, state)
        return paths

    def _publish(self, files, state=None):
        self._held()
        state = self._state if state is None else state
        revision = self.remote.commit(self._revision, {**files, self.control: canonical_bytes(state)})
        self._revision, self._state = revision, state
        self._held()

    @contextmanager
    def exclusive(self, *, require_bound=False):
        _need(self._owner is None, "hf_lease_already_held")
        revision, paths = self.remote.snapshot()
        _need(not require_bound or SCOPE_FILE in paths, "hf_scope_unbound")
        if SCOPE_FILE in paths:
            self._scope_identity(self.remote.read(revision, SCOPE_FILE))
            _need(self.control in paths, "hf_scope_binding_missing")
        if self.control in paths:
            state = self._decode(self.remote.read(revision, self.control))
            self._scope_link(revision, paths, state)
            _need(state["owner"] is None, "hf_namespace_busy")
        else:
            _need(not any(p.startswith(self.prefix) for p in paths), "hf_namespace_not_pristine")
            state = {"schema": 1, "namespace": self.namespace, "epoch": 0, "owner": None, "head": None}
        owner = secrets.token_hex(16)
        state = {**state, "owner": owner, "epoch": state["epoch"] + 1}
        self._decode(canonical_bytes(state))
        claimed = self.remote.commit(revision, {self.control: canonical_bytes(state)})
        self._revision, self._state, self._owner = claimed, state, owner
        try:
            self._held()
            yield
        finally:
            try:
                self._held()
                released = canonical_bytes({**self._state, "owner": None})
                revision = self.remote.commit(self._revision, {self.control: released})
                actual, _ = self.remote.snapshot()
                _need(actual == revision and self.remote.read(actual, self.control) == released,
                      "hf_release_unverified")
            finally:
                self._revision = self._state = self._owner = None
                self._verified_scope = None

    def read_head(self):
        self._data_held()
        head = self._state["head"]
        return None if head is None else head.encode("utf-8")

    def is_pristine(self):
        paths = self._data_held()
        return self._state["head"] is None and not any(
            p.startswith(self.prefix) and p != self.control for p in paths)

    def _path(self, key):
        _need(type(key) is str and re.fullmatch(r"(archives|receipts)/[a-f0-9]{64}", key), "hf_key_invalid")
        return self.prefix + key + ".json"

    def get(self, key):
        self._data_held()
        return self.remote.read(self._revision, self._path(key))

    def put_immutable(self, key, value):
        path = self._path(key)
        _need(type(value) is bytes and len(value) <= MAX_OBJECT
              and hashlib.sha256(value).hexdigest() == key.split("/")[1], "hf_object_invalid")
        if path in self._data_held():
            _need(self.remote.read(self._revision, path) == value, "hf_immutable_conflict")
        else:
            self._publish({path: value})

    def compare_and_swap_head(self, expected, value):
        _need(type(value) is bytes and len(value) <= 4096, "hf_head_invalid")
        if self.read_head() != expected:
            return False
        self._publish({}, {**self._state, "head": value.decode("utf-8")})
        return True
