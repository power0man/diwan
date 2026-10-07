"""Discover installed agents and Ollama models without generating, downloading or reading credentials.

Presence is not connectivity. Task rankings use recent, externally supplied evidence;
an unmeasured model never becomes the 'best' model by name or parameter count.
"""
from __future__ import annotations

import json
import math
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from team.transport import TransportError, request_json

ROLES = ("coding", "planning", "reasoning", "arabic", "drafting", "review", "benchmark_author")
TEXT_ROLES = ("planning", "reasoning", "arabic", "drafting", "review")
CLI_NAMES = ("claude", "codex", "gemini", "antigravity", "opencode", "kimi")
FAMILY_PREFIXES = (
    ("qwen", "qwen"), ("deepseek", "deepseek"), ("kimi", "moonshot"),
    ("gemma", "google"), ("gemini", "google"), ("gpt-oss", "openai"),
    ("llama", "meta"), ("mistral", "mistral"), ("minimax", "minimax"),
    ("minicpm", "openbmb"), ("command-r", "cohere"), ("jais", "inception"),
    ("claude", "anthropic"), ("gpt-", "openai"),
)
MAX_EVIDENCE_DAYS = 30


def model_family(model: str) -> str:
    # Match the model, not its transport (Copilot/OpenCode/Hugging Face are not families).
    name = model.rsplit("/", 1)[-1].lower()
    for prefix, family in FAMILY_PREFIXES:
        if name.startswith(prefix):
            return family
    return "unknown"


def cli_path(name: str) -> Path | None:
    env = os.environ.get(f"DIWAN_TEAM_{name.upper()}_BIN")
    if env:
        return Path(env)
    direct = Path.home() / ".local" / "bin" / name
    if name == "claude" and direct.is_file():
        return direct
    found = shutil.which("agy" if name == "antigravity" else name)
    return Path(found) if found else None


def discover(*, endpoint: str | None = None, request=request_json, which=cli_path, home: Path | None = None) -> dict:
    endpoint = endpoint or os.environ.get("DIWAN_TEAM_OLLAMA_URL", "http://127.0.0.1:11434")
    agents, excluded, findings = [], [], []
    families = {"claude": "anthropic", "codex": "openai", "gemini": "google", "kimi": "moonshot"}
    for name in CLI_NAMES:
        binary = which(name)
        present = binary is not None and binary.is_file()
        explicit_model = name in ("opencode", "antigravity")
        model = os.environ.get(f"DIWAN_TEAM_{name.upper()}_MODEL", "") if explicit_model else ""
        family = model_family(model) if explicit_model else families[name]
        roles = ROLES[:-1] if name != "kimi" else ("benchmark_author",)
        status = "installed_unverified" if present else "binary_missing"
        configured = (family in ("google", "anthropic", "openai") if name == "antigravity"
                      else "/" in model and family not in ("unknown", "moonshot"))
        if explicit_model and not configured and present:
            status = "model_unconfigured"
        agents.append({"id": name, "transport": "cli", "family": family, "model": model or None,
                       "placement": "provider_configured", "roles": list(roles), "status": status,
                       "binary": str(binary) if binary else None, "repository_access": name != "kimi",
                       "route": "external_kimi_driver" if name == "kimi" else "dispatch",
                       "identity": model or name})
    try:
        tags = request(endpoint, "/api/tags")
        models = tags.get("models")
        if not isinstance(models, list):
            raise TransportError("bad_catalog")
    except TransportError as exc:
        findings.append(exc.code)
        models = []
    for tag in models:
        if not isinstance(tag, dict):
            findings.append("bad_catalog_item")
            continue
        name = tag.get("name")
        if not isinstance(name, str) or not name:
            continue
        try:
            info = request(endpoint, "/api/show", {"model": name})
            capabilities = info.get("capabilities", [])
            if not isinstance(capabilities, list):
                raise TransportError("bad_capabilities")
            status = "catalogued_unverified"
        except TransportError as exc:
            excluded.append({"model": name, "reason": exc.code})
            continue
        if "completion" not in capabilities:
            excluded.append({"model": name, "reason": "not_a_completion_model"})
            continue
        cloud = name.endswith((":cloud", "-cloud")) or bool(info.get("remote_host") or tag.get("remote_host"))
        family = model_family(name)
        # Kimi retains independent bank authorship: no automatic repository context.
        roles = ("benchmark_author", "drafting") if family == "moonshot" else TEXT_ROLES
        agents.append({"id": f"ollama:{name}", "transport": "ollama", "model": name, "family": family,
                       "placement": "cloud" if cloud else "local", "roles": list(roles), "status": status,
                       "capabilities": capabilities, "endpoint": endpoint, "repository_access": False,
                       "route": "text_only", "identity": tag.get("digest") or name})
    if home is not None:
        apply_health(agents, home)
    return {"schema_version": 1, "agents": agents, "excluded": excluded, "findings": findings,
            "limits": ["presence_is_not_connectivity_or_quota", "roles_are_routes_not_quality_scores",
                       "cloud_placement_uses_ollama_remote_metadata_and_standard_cloud_tags",
                       "model_tool_capability_does_not_grant_repository_tools"]}


def apply_health(agents: list[dict], home: Path, *, now=None) -> None:
    """Recent text-route failures exclude automatic selection; old failures are not permanent bans."""
    now = now or datetime.now(timezone.utc)
    recent = {}
    for path in (home / "agents").glob("*.json"):
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            row = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(row, dict) or row.get("status") not in ("started", "completed", "outcome_unknown"):
                continue
            stamp = datetime.fromisoformat(row["at"])
            if not timedelta(0) <= now - stamp <= timedelta(minutes=30):
                continue
            key = (row["agent"], row["identity"])
            if key not in recent or recent[key][0] < stamp:
                recent[key] = (stamp, row)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    for agent in agents:
        health = recent.get((agent["id"], agent["identity"]))
        if health:
            row = health[1]
            agent["health"] = {"status": row["status"], "at": row["at"], "code": row.get("code")}
            if row["status"] != "completed":
                agent["status"] = "recently_unavailable"


def select(catalog: dict, role: str, evidence: list[dict], *, author_families=(), now=None) -> dict:
    if role not in ROLES:
        raise ValueError("unknown_role")
    now = now or datetime.now(timezone.utc)
    ranked, blocked = [], []
    for agent in catalog["agents"]:
        reason = None
        if role not in agent["roles"]:
            reason = "role_not_supported"
        elif agent["status"] in ("binary_missing", "model_unconfigured", "recently_unavailable"):
            reason = agent["status"]
        elif role == "review" and (agent["family"] == "unknown" or agent["family"] in author_families):
            reason = "reviewer_not_independent"
        if reason:
            blocked.append({"id": agent["id"], "reason": reason})
            continue
        samples = []
        for row in evidence:
            if not isinstance(row, dict):
                continue
            if row.get("agent") != agent["id"] or row.get("role") != role or row.get("identity") != agent["identity"]:
                continue
            try:
                age = now - datetime.fromisoformat(row["at"])
                score = float(row["score"])
                valid = (timedelta(0) <= age <= timedelta(days=MAX_EVIDENCE_DAYS)
                         and math.isfinite(score) and 0 <= score <= 100
                         and row.get("status") == "passed" and bool(row.get("source")))
            except (ValueError, TypeError, KeyError):
                valid = False
            if valid:
                samples.append(score)
        if samples:
            ranked.append({"id": agent["id"], "family": agent["family"], "placement": agent["placement"],
                           "score": sum(samples) / len(samples), "samples": len(samples)})
        else:
            blocked.append({"id": agent["id"], "reason": "task_evidence_missing_or_stale"})
    ranked.sort(key=lambda row: (-row["score"], row["id"]))
    return {"role": role, "selected": ranked[0]["id"] if ranked else None, "ranking": ranked, "blocked": blocked,
            "basis": "task_evidence" if ranked else "no_measured_candidate",
            "limits": ["best_among_recorded_task_evidence_not_universal_quality",
                       "quality_evidence_is_supplied_by_the_operator_not_by_the_agent",
                       "selection_does_not_grant_spend_or_count_a_project_review"]}


def load_evidence(home: Path) -> list[dict]:
    try:
        data = json.loads((home / "agent-evidence.json").read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def record_evidence(home: Path, agent: dict, role: str, score: float, source: str) -> dict:
    """Operator-provided task evidence, bound to the exact model identity. Not model self-rating."""
    if (role not in agent["roles"] or not math.isfinite(score) or not 0 <= score <= 100
            or not source.strip()):
        raise ValueError("invalid_task_evidence")
    from core.filelock import lock, unlock
    home.mkdir(parents=True, exist_ok=True)
    row = {"agent": agent["id"], "identity": agent["identity"], "role": role, "score": score,
           "source": source, "status": "passed", "at": datetime.now(timezone.utc).isoformat()}
    with (home / "agent-evidence.lock").open("a") as handle:
        lock(handle)
        try:
            rows = [r for r in load_evidence(home) if isinstance(r, dict) and not (r.get("agent") == row["agent"]
                    and r.get("role") == role and r.get("source") == source)]
            rows.append(row)
            path = home / "agent-evidence.json"
            tmp = home / "agent-evidence.json.tmp"
            tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            unlock(handle)
    return row
