"""Operator-only lifecycle proof for synthetic cloud workspaces.

This is not wired to Server or browser input. The coordinator exclusively owns
its LocalApp; callers supply trusted provider configuration, never an existing
app or a custom committer. Private attributes are not a security boundary against
code running as the operator. No owner-content consent or credential is added.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading

from webui.server import LocalApp
from workspace_tools import backup, storage_scope as ss, synthetic_checkpoints as sc
from workspace_tools.files import _canonical_root


class LifecycleError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(condition, code):
    if not condition:
        raise LifecycleError(code)


def _options(value):
    required = {"model", "model_version", "provider_factory"}
    allowed = required | {"agent_provider_factory"}
    _need(type(value) is dict and required <= set(value) <= allowed,
          "cloud_bootstrap_invalid")
    _need(all(isinstance(value[key], str) and value[key] for key in ("model", "model_version"))
          and callable(value["provider_factory"]) and
          ("agent_provider_factory" not in value or callable(value["agent_provider_factory"])),
          "cloud_bootstrap_invalid")
    return dict(value)


class SyntheticLifecycle:
    """READY -> SAVING -> READY; any persistence failure -> BLOCKED.

    CLOSED and BLOCKED never dispatch or reopen themselves. Only explicit restore
    creates a new coordinator, at a previously absent destination. All mutations
    must enter through dispatch; do not retain or publish the privately owned app.
    Factory configuration and storage target belong to the trusted operator.
    """
    def __init__(self):
        raise TypeError("use create or restore")

    @classmethod
    def _bootstrap(cls, root, expected_scope, store, options, last_receipt=None):
        self = object.__new__(cls)
        self._root = _canonical_root(root)
        self._scope = expected_scope
        self._store = store
        self._options = options
        self._gate = threading.Lock()
        self._state = "closed"
        self._app = None
        self._last_receipt = last_receipt
        self._open()
        self._state = "ready"
        return self

    @classmethod
    def create(cls, destination, plan, *, approval, claims_root, store, app_options):
        """Create a new approved synthetic workspace; never adopt an old root."""
        options = _options(app_options)
        scope = ss.create_cloud_workspace(destination, plan, approval=approval, claims_root=claims_root)
        return cls._bootstrap(scope.root, scope.raw, store, options)

    @classmethod
    def restore(cls, destination, expected_scope, *, store, app_options, receipt_sha256=None):
        """Explicit cold restore; the store supplies the latest forget authority."""
        options = _options(app_options)
        ss.checkpoint_scope(expected_scope)
        result = sc.restore_synthetic_checkpoint(store, destination, expected_scope,
                                                 receipt_sha256=receipt_sha256)
        _need(result.get("status") == "restored", "cloud_restore_unconfirmed")
        return cls._bootstrap(destination, expected_scope, store, options,
                              result["forget_authority_sha256"])

    @property
    def state(self):
        return self._state

    def _scope_matches(self):
        current = ss.read_storage_scope(self._root)
        _need(current is not None and current.raw == self._scope, "cloud_scope_changed")
        ss.checkpoint_scope(self._scope)

    def _open(self):
        self._scope_matches()
        app = LocalApp(self._root, synthetic_cloud=True, **self._options)
        try:
            _need(app.root == self._root and app.storage_scope is not None
                  and app.storage_scope.raw == self._scope, "cloud_bootstrap_invalid")
            app.check_root()
        except BaseException:
            app.close()
            raise
        self._app = app

    @contextmanager
    def _request(self):
        _need(self._gate.acquire(blocking=False), "cloud_lifecycle_busy")
        try:
            _need(self._state == "ready" and self._app is not None, "cloud_lifecycle_closed")
            try:
                self._scope_matches()
                self._app.check_root()
            except Exception:
                self._block()
                raise LifecycleError("cloud_scope_changed") from None
            yield
        finally:
            self._gate.release()

    @contextmanager
    def _idle(self):
        app = self._app
        with app.lock:
            _need(app.active is None and app.active_agent_session is None,
                  "cloud_generation_busy")
            _need(app.generation.acquire(blocking=False), "cloud_generation_busy")
        try:
            yield app
        finally:
            app.generation.release()

    def _block(self):
        self._state = "blocked"
        # A future asynchronous provider must not have its files closed below a
        # live generation. Keep the app private and refuse every further call.
        if self._app is not None:
            try:
                with self._idle() as app:
                    self._app = None
                    app.close()
            except Exception:
                pass

    def _persist(self):
        with self._idle() as app:
            self._state = "saving"
            self._app = None
            try:
                app.close()
                with tempfile.TemporaryDirectory(prefix="diwan-synthetic-save-") as temporary:
                    archive = Path(temporary).resolve() / "checkpoint.json"
                    exported = backup.export_workspace(self._root, archive)
                    committed = sc.commit_synthetic_checkpoint(
                        archive, exported["sha256"], self._scope, self._store)
                _need(committed.get("status") == "committed", "cloud_checkpoint_unconfirmed")
                self._last_receipt = committed["receipt_sha256"]
                self._open()
                self._state = "ready"
                return {"status": "saved", "receipt_sha256": self._last_receipt}
            except BaseException as exc:
                self._block()
                if not isinstance(exc, Exception):
                    raise
                raise LifecycleError("cloud_checkpoint_unconfirmed") from None

    def dispatch(self, request):
        """Serialize app calls; a forget result cannot escape before durability."""
        with self._request():
            _need(type(request) is dict and isinstance(request.get("action"), str),
                  "cloud_request_invalid")
            # Do not enter the app when a generation is still using its files,
            # including one whose dispatch returned before background work ended.
            with self._idle():
                pass
            if request["action"] != "memory_forget":
                return self._app.dispatch(request)
            try:
                result = self._app.dispatch(request)
                _need(result.get("status") == "forgotten", "cloud_forget_unconfirmed")
                durable = self._persist()
                return {**result, "durability": durable}
            except BaseException as exc:
                self._block()
                if not isinstance(exc, Exception):
                    raise
                raise LifecycleError("cloud_forget_unconfirmed") from None

    def save(self):
        with self._request():
            return self._persist()

    def close(self):
        _need(self._gate.acquire(blocking=False), "cloud_lifecycle_busy")
        try:
            if self._state != "blocked":
                self._state = "closed"
            if self._app is not None:
                with self._idle() as app:
                    self._app = None
                    app.close()
        except BaseException:
            self._state = "blocked"
            raise
        finally:
            self._gate.release()
