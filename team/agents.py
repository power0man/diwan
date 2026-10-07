"""Inventory, evidence-based selection and text participation for local/cloud agents.

Text participants receive only explicit stdin. They have no repository reader, shell,
file writer or tool execution. CLI workers use team.dispatch, not this text route.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.quoted import quarantine
from team import team_home
from team.catalog import ROLES, discover, load_evidence, record_evidence, select
from team.transport import TransportError, request_json

MAX_PROMPT_BYTES = 64 * 1024


class AgentError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def text_plan(catalog: dict, agent_id: str, role: str, *, allow_cloud: bool = False) -> dict:
    agent = next((a for a in catalog["agents"] if a["id"] == agent_id), None)
    if agent is None:
        raise AgentError("agent_not_found")
    if role not in agent["roles"]:
        raise AgentError("role_not_supported")
    if agent["transport"] != "ollama":
        raise AgentError("use_dispatch_or_external_kimi_driver")
    if agent["placement"] == "cloud" and not allow_cloud:
        raise AgentError("cloud_egress_not_enabled")
    return {"status": "dry_run", "agent": agent["id"], "model": agent["model"], "role": role,
            "family": agent["family"], "placement": agent["placement"], "tools": [], "repository_access": False}


def ask(catalog: dict, agent_id: str, role: str, prompt: str, *, home: Path, execute: bool = False,
        allow_cloud: bool = False, timeout: int = 120, request=request_json) -> dict:
    plan = text_plan(catalog, agent_id, role, allow_cloud=allow_cloud)
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES or not prompt.strip():
        raise AgentError("prompt_empty_or_too_large")
    if not execute:
        return plan
    agent = next(a for a in catalog["agents"] if a["id"] == agent_id)
    run_id = str(uuid.uuid4())
    path = home / "agents" / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {**plan, "run_id": run_id, "status": "started", "at": datetime.now(timezone.utc).isoformat(),
              "identity": agent["identity"], "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        reply = request(agent["endpoint"], "/api/chat", {
            "model": agent["model"], "stream": False, "keep_alive": 0,
            "messages": [{"role": "system", "content": "You are a text participant. No tools or filesystem access. "
                          "Produce only the requested answer; do not claim actions or external research."},
                         {"role": "user", "content": prompt}],
            "options": {"temperature": 0, "num_predict": 1024},
        }, timeout=timeout)
        message = reply.get("message") or {}
        if reply.get("error") or reply.get("done") is not True or not isinstance(message, dict) or message.get("tool_calls"):
            raise TransportError("incomplete_or_tool_response")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise TransportError("empty_response")
        result = quarantine(text)
        record.update(status="completed", response=result.text,
                      quarantine_codes=sorted({finding.code for finding in result.findings}),
                      tokens={k: reply.get(k) for k in ("prompt_eval_count", "eval_count")},
                      spend_basis="local_no_charge" if agent["placement"] == "local" else "provider_usage_not_a_bill")
    except TransportError as exc:
        record.update(status="outcome_unknown", code=exc.code)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    # Raw response stays outside the repository; terminal output contains only its location.
    return {k: record.get(k) for k in ("status", "agent", "role", "placement", "run_id", "code")} | {"result_path": str(path)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ollama-url")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    pick = sub.add_parser("select")
    pick.add_argument("--role", choices=ROLES, required=True)
    pick.add_argument("--author-family", action="append", default=[])
    record = sub.add_parser("record")
    record.add_argument("--agent", required=True)
    record.add_argument("--role", choices=ROLES, required=True)
    record.add_argument("--score", required=True, type=float)
    record.add_argument("--source", required=True, help="operator's evaluation reference; no self-rating")
    run = sub.add_parser("ask")
    run.add_argument("--agent", required=True, help="catalog ID, or auto (requires task evidence)")
    run.add_argument("--role", choices=ROLES, required=True)
    run.add_argument("--execute", action="store_true")
    run.add_argument("--allow-cloud", action="store_true")
    run.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    home = team_home()
    catalog = discover(endpoint=args.ollama_url, home=home)
    try:
        if args.command == "list":
            out = catalog
        elif args.command == "select":
            out = select(catalog, args.role, load_evidence(home), author_families=args.author_family)
        elif args.command == "record":
            agent = next((a for a in catalog["agents"] if a["id"] == args.agent), None)
            if agent is None:
                raise AgentError("agent_not_found")
            out = record_evidence(home, agent, args.role, args.score, args.source)
        else:
            agent_id = args.agent
            if agent_id == "auto":
                text_catalog = {"agents": [a for a in catalog["agents"] if a["route"] == "text_only"]}
                agent_id = select(text_catalog, args.role, load_evidence(home))["selected"]
                if agent_id is None:
                    raise AgentError("task_evidence_missing")
            if args.execute:
                raw = sys.stdin.buffer.read(MAX_PROMPT_BYTES + 1)
                prompt = raw.decode("utf-8")
            else:
                prompt = "dry-run"
            out = ask(catalog, agent_id, args.role, prompt, home=home, execute=args.execute,
                      allow_cloud=args.allow_cloud, timeout=args.timeout)
    except (AgentError, ValueError, UnicodeError) as exc:
        out = {"status": "refused", "code": getattr(exc, "code", "input_not_utf8")}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 2 if out.get("status") in ("refused", "outcome_unknown") else 0


if __name__ == "__main__":
    sys.exit(main())
