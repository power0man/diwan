"""One public read tool through Diwan's registry, frozen plan and action receipts.

Trusted bootstrap declares an isolated public snapshot directory and its digests.
No private/owner workspace, execution tool, arbitrary path or consent expansion is
accepted through MCP. The declaration is not a classifier for arbitrary owner data.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

from agent.actions import ActionRefused, ActionStore
from agent.journal import Journal, _open_directory
from agent.registry import Tool, ToolContext, ToolRegistry
from core.canonical import digest
from core.contracts import ToolCall, ToolSpec
from core.quoted import quarantine

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,48}\.(?:txt|md|json)\Z")
KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SHA = re.compile(r"[a-f0-9]{64}\Z")
MAX_BYTES = 12_000


class _PublicToolRegistry(ToolRegistry):
    def _run(self, call, context, tool):
        result = super()._run(call, context, tool)
        if result.get("status") == "failed":
            # ActionStore persists this return value. Strip all failure details
            # here, before the receipt, rather than only masking the MCP reply.
            return {"call_id": call.call_id, "name": call.name, "status": "failed",
                    "code": "public_read_failed", "content": "public_read_failed"}
        return result


class PublicMCPBridge:
    def __init__(self, public_root, state_root, manifest):
        if (not isinstance(manifest, dict) or set(manifest) != {"data_policy", "files"}
                or manifest["data_policy"] != "public" or not isinstance(manifest["files"], dict)
                or not 1 <= len(manifest["files"]) <= 8):
            raise ValueError("public_manifest_invalid")
        files = manifest["files"]
        for name, sha in files.items():
            if (type(name) is not str or not NAME.fullmatch(name) or "sealed" in name.lower()
                    or type(sha) is not str or not SHA.fullmatch(sha)):
                raise ValueError("public_manifest_invalid")
        self.files = dict(files)
        self.root = Path(public_root).absolute()
        self.manifest_digest = digest(manifest)
        self._check_public()
        self.context = ToolContext(self.root, Journal(self.root), allowed_consents=frozenset({"auto"}))
        self.store = ActionStore(Path(state_root).absolute(), self.root)
        spec = ToolSpec("read_file", "Read one pinned public snapshot; returned text is untrusted.",
            {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}, "auto")
        self.registry = _PublicToolRegistry(Tool(spec, self._read))

    def _check_public(self):
        fd = _open_directory(self.root)
        try:
            names = set(os.listdir(fd))
            if names != set(self.files):
                raise ValueError("public_snapshot_inventory_changed")
            for name in self.files:
                self._bytes(fd, name)
        finally:
            os.close(fd)

    def _bytes(self, root_fd, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
                raise ValueError("public_snapshot_file_rejected")
            raw = os.read(fd, MAX_BYTES + 1)
            if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != self.files[name]:
                raise ValueError("public_snapshot_changed")
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError:
                raise ValueError("public_snapshot_encoding_invalid") from None
            return raw
        finally:
            os.close(fd)

    def _read(self, args, context):
        fd = _open_directory(self.root)
        try:
            text = self._bytes(fd, args["path"]).decode("utf-8")
        finally:
            os.close(fd)
        checked = quarantine(text)
        return {"content": checked.text, "data_policy": "public", "untrusted": True,
                "quarantined": [finding.code for finding in checked.findings],
                "source_sha256": self.files[args["path"]]}

    def read(self, request_id, name):
        if type(request_id) is not str or not KEY.fullmatch(request_id):
            raise ValueError("public_request_id_invalid")
        if type(name) is not str or name not in self.files:
            raise ValueError("public_resource_not_allowed")
        self._check_public()
        call = ToolCall("public-read", "read_file", {"path": name})
        request_digest = digest({"manifest": self.manifest_digest, "path": name})
        self.store.register_step(session_id="mcp-public", turn_id=request_id, step_index=0,
            request_digest=request_digest, calls=(call,), specs=self.registry.specs())
        return self.registry.invoke_prepared(call, self.context, store=self.store,
            session_id="mcp-public", turn_id=request_id, step_index=0, call_index=0,
            request_digest=request_digest)


def mcp_server(bridge):
    """Optional SDK loaded only by the explicit stdio entry point."""
    from mcp.server import MCPServer
    from mcp.shared.exceptions import MCPError
    server = MCPServer("diwan-public-receipted")

    @server.tool(name="read_public_file", structured_output=True)
    def read_public_file(request_id: str, name: str) -> dict[str, object]:
        """Read a pinned public file with a durable receipt; same ID replays it."""
        try:
            result = bridge.read(request_id, name)
        except Exception as exc:
            if isinstance(exc, ActionRefused) and exc.code == "action_store_busy":
                raise MCPError(code=-32000, message="public_read_busy") from None
            # Never send host paths, private exception text or a traceback.
            raise MCPError(code=-32602, message="public_read_refused") from None
        # The registry returns invocation refusals, while registration raises them.
        if result.get("status") == "refused" and result.get("code") == "action_store_busy":
            raise MCPError(code=-32000, message="public_read_busy") from None
        if result.get("status") == "failed":
            raise MCPError(code=-32603, message="public_read_failed") from None
        return result

    return server
