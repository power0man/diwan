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

MAX_OBJECT = 1024 * 1024  # Deliberately smaller than the archive format's limit.
MAX_METADATA = 1024 * 1024


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

    def _decode(self, raw):
        try:
            state = json.loads(raw)
            _need(type(state) is dict and set(state) == {"schema", "namespace", "epoch", "owner", "head"}
                  and type(state["schema"]) is int and state["schema"] == 1 and state["namespace"] == self.namespace
                  and type(state["epoch"]) is int and 1 <= state["epoch"] <= 2**53
                  and (state["owner"] is None or type(state["owner"]) is str
                       and re.fullmatch(r"[a-f0-9]{32}", state["owner"]))
                  and (state["head"] is None or type(state["head"]) is str and len(state["head"]) <= 4096)
                  and canonical_bytes(state) == raw, "hf_control_invalid")
            return state
        except (TypeError, ValueError, RecursionError):
            raise StoreError("hf_control_invalid") from None

    def _held(self):
        _need(self._owner is not None, "hf_lease_required")
        revision, paths = self.remote.snapshot()
        _need(revision == self._revision, "hf_lease_fenced")
        state = self._decode(self.remote.read(revision, self.control))
        _need(state == self._state and state["owner"] == self._owner, "hf_lease_fenced")
        return paths

    def _publish(self, files, state=None):
        self._held()
        state = self._state if state is None else state
        revision = self.remote.commit(self._revision, {**files, self.control: canonical_bytes(state)})
        self._revision, self._state = revision, state
        self._held()

    @contextmanager
    def exclusive(self):
        _need(self._owner is None, "hf_lease_already_held")
        revision, paths = self.remote.snapshot()
        if self.control in paths:
            state = self._decode(self.remote.read(revision, self.control))
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

    def read_head(self):
        self._held()
        head = self._state["head"]
        return None if head is None else head.encode("utf-8")

    def is_pristine(self):
        paths = self._held()
        return self._state["head"] is None and not any(
            p.startswith(self.prefix) and p != self.control for p in paths)

    def _path(self, key):
        _need(type(key) is str and re.fullmatch(r"(archives|receipts)/[a-f0-9]{64}", key), "hf_key_invalid")
        return self.prefix + key + ".json"

    def get(self, key):
        self._held()
        return self.remote.read(self._revision, self._path(key))

    def put_immutable(self, key, value):
        path = self._path(key)
        _need(type(value) is bytes and len(value) <= MAX_OBJECT
              and hashlib.sha256(value).hexdigest() == key.split("/")[1], "hf_object_invalid")
        if path in self._held():
            _need(self.remote.read(self._revision, path) == value, "hf_immutable_conflict")
        else:
            self._publish({path: value})

    def compare_and_swap_head(self, expected, value):
        _need(type(value) is bytes and len(value) <= 4096, "hf_head_invalid")
        if self.read_head() != expected:
            return False
        self._publish({}, {**self._state, "head": value.decode("utf-8")})
        return True
