"""Bounded local Ollama gateway requests; no retries, proxies, redirects or credentials.

A cloud-tagged model on a local gateway still sends data to its cloud provider.
Transport failure after generation starts is outcome_unknown, not permission to retry.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class TransportError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_endpoint(endpoint: str) -> None:
    try:
        u = urllib.parse.urlsplit(endpoint)
        host = u.hostname
        loopback = host == "localhost" or bool(host and ipaddress.ip_address(host).is_loopback)
        valid = (u.scheme == "http" and loopback and not u.username and not u.password
                 and u.path in ("", "/") and not u.query and not u.fragment and bool(u.port))
    except ValueError:
        valid = False
    if not valid:
        raise TransportError("non_loopback_gateway_refused")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise TransportError("redirect_refused")


def request_json(endpoint: str, path: str, body: dict | None = None, *, timeout: int = 15) -> dict:
    validate_endpoint(endpoint)
    if path not in ("/api/tags", "/api/show", "/api/chat"):
        raise TransportError("endpoint_path_refused")
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint.rstrip("/") + path, data=data, headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise TransportError("response_too_large")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise TransportError("response_not_object")
        return result
    except urllib.error.HTTPError as exc:
        code = "auth_required" if exc.code in (401, 403) else "quota_exhausted" if exc.code == 429 else f"http_{exc.code}"
        raise TransportError(code) from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise TransportError("gateway_unavailable_or_timeout") from exc
    except (ValueError, UnicodeError) as exc:
        raise TransportError("bad_response_json") from exc
