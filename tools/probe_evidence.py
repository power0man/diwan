#!/usr/bin/env python3
"""Validate explicitly supplied public probe evidence without disclosing its contents."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

REDACTION_MARKER = "redacted_existing_public_sensitive_metadata"
PUBLIC_REPOSITORY = "power0man/diwan"
SAFE_SUCCESSOR = re.compile(r"^[A-Za-z0-9._-]+\.json$")
INDEX_ROW = re.compile(
    r"^\| \[`(?P<name>[A-Za-z0-9._-]+\.json)`\]"
    r"\((?P<link>[A-Za-z0-9._-]+\.json)\) \| (?P<judgment>[^|\r\n]+) \|$"
)
LOCAL_OPERATION_TEXT = re.compile(
    r"(?i)(?:localhost|127\.0\.0\.1|host\.docker\.internal|local[-_ ]host)"
)
REMOTE_OPERATION_TEXT = re.compile(
    r"(?i)(?:cloud[-_ ]?(?:job|runtime)|remote[-_ ]?job)"
)
PAYMENT_RESPONSE_TEXT = re.compile(
    r"(?is)\A(?=.*(?:http|status|response|server))(?=.*\b402\b)"
)
ACCESS_VALUE_PATTERNS = (
    re.compile(r"(?i)(?:github_pat_|gh[pousr]_)[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(?:sk-|gsk_|hf_|xox[baprs]-)[A-Za-z0-9_-]{28,}"),
    re.compile(r"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
)
SENSITIVE_FIELD = re.compile(
    r"^(?:github_)?secret_(?:exists|name|names)$|"
    r"^(?:credential|access_token|api_key|private_key)(?:_exists|_name|_names)?$",
    re.IGNORECASE,
)
OPERATIONAL_FIELDS = frozenset(
    {
        "account",
        "base_url",
        "billed_usd",
        "credits",
        "estimate_usd",
        "host",
        "hostname",
        "input_tokens",
        "job_id",
        "local_url",
        "machine",
        "os",
        "output_tokens",
        "port",
        "sandbox_id",
    }
)
TIMING_FIELD = re.compile(
    r"(?i)(?:attempt|elapsed|duration|seconds|minutes|started|finished|timeout|latency)"
)
ACCESS_QUERY_NAMES = frozenset(
    {"access_key", "access_token", "api_key", "credential", "signature", "token"}
)


def _valid_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _contains_operation_scope(value: object) -> bool:
    if value == REDACTION_MARKER:
        return False
    if isinstance(value, dict):
        return any(
            key.lower() in OPERATIONAL_FIELDS
            or _contains_operation_scope(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_operation_scope(child) for child in value)
    return isinstance(value, str) and bool(
        LOCAL_OPERATION_TEXT.search(value)
        or REMOTE_OPERATION_TEXT.search(value)
        or PAYMENT_RESPONSE_TEXT.search(value)
    )


def _unsafe_string(value: str) -> bool:
    if any(pattern.search(value) for pattern in ACCESS_VALUE_PATTERNS):
        return True
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.username or parsed.password:
        return True
    if any(name.lower() in ACCESS_QUERY_NAMES for name, _ in parse_qsl(parsed.query)):
        return True
    if hostname in {"github.com", "www.github.com"} and len(parts) >= 2:
        repository = "/".join(parts[:2])
        if "actions" in parts and repository != PUBLIC_REPOSITORY:
            return True
        if "settings" in parts or "secrets" in parts:
            return True
    if hostname == "api.github.com" and len(parts) >= 3 and parts[0] == "repos":
        return "/".join(parts[1:3]) != PUBLIC_REPOSITORY
    return False


def privacy_findings(payload: object) -> set[str]:
    """Return category codes only; never return paths, names, or captured values."""
    findings: set[str] = set()
    operation_scope = _contains_operation_scope(payload)

    def walk(value: object) -> None:
        if value == REDACTION_MARKER:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                if child == REDACTION_MARKER:
                    continue
                lowered = key.lower()
                if SENSITIVE_FIELD.fullmatch(key):
                    findings.add("private_access_metadata")
                    continue
                if lowered in OPERATIONAL_FIELDS:
                    findings.add("private_operational_metadata")
                    continue
                if operation_scope and TIMING_FIELD.search(key):
                    findings.add("private_operational_timing")
                    continue
                if operation_scope and lowered in {"provider", "transport"}:
                    findings.add("private_operational_provider")
                    continue
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, str):
            if _unsafe_string(value):
                findings.add("private_access_metadata")
            if (
                LOCAL_OPERATION_TEXT.search(value)
                or REMOTE_OPERATION_TEXT.search(value)
                or PAYMENT_RESPONSE_TEXT.search(value)
            ):
                findings.add("private_operational_metadata")

    walk(payload)
    return findings


def validate_payload(
    payload: object, *, available_names: set[str] | None = None
) -> list[str]:
    """Return stable error codes without echoing evidence metadata."""
    if not isinstance(payload, dict):
        return ["root_not_object"]
    errors: list[str] = []
    if payload.get("historical") is True:
        successor = payload.get("superseded_by")
        if not isinstance(successor, str) or SAFE_SUCCESSOR.fullmatch(successor) is None:
            errors.append("invalid_superseded_by")
        elif available_names is not None and successor not in available_names:
            errors.append("superseded_by_missing")
    else:
        if not isinstance(payload.get("agent"), str) or not payload["agent"].strip():
            errors.append("missing_agent")
        if not _valid_date(payload.get("date")):
            errors.append("invalid_date")
        limits = payload.get("measurement_limits")
        if not isinstance(limits, list) or not limits or not all(
            isinstance(item, str) and item.strip() for item in limits
        ):
            errors.append("missing_measurement_limits")
    errors.extend(sorted(privacy_findings(payload)))
    return errors


def validate_index(path: Path, *, available_names: set[str]) -> Counter[str]:
    """Validate an explicit Markdown index without returning any recorded name."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return Counter({"index_unreadable": 1})

    errors: Counter[str] = Counter()
    rows: set[str] = set()
    for line in lines:
        if not line.startswith("| ["):
            continue
        match = INDEX_ROW.fullmatch(line)
        if match is None:
            errors["malformed_index_row"] += 1
            continue
        name, link, judgment = match.group("name", "link", "judgment")
        if name != link:
            errors["index_link_mismatch"] += 1
        if name in rows:
            errors["duplicate_index_row"] += 1
        rows.add(name)
        if not judgment.strip():
            errors["missing_index_judgment"] += 1
    errors["index_missing_rows"] += len(available_names - rows)
    errors["index_extra_rows"] += len(rows - available_names)
    return +errors


def validate_files(
    paths: list[Path], *, index_path: Path | None = None
) -> tuple[int, Counter[str]]:
    errors: Counter[str] = Counter()
    available_names = {path.name for path in paths}
    errors["duplicate_evidence_name"] += len(paths) - len(available_names)
    for path in paths:
        if SAFE_SUCCESSOR.fullmatch(path.name) is None:
            errors["unsafe_evidence_name"] += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            errors["unreadable_or_invalid_json"] += 1
            continue
        errors.update(validate_payload(payload, available_names=available_names))
    if index_path is not None:
        errors.update(validate_index(index_path, available_names=available_names))
    return len(paths), +errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument(
        "--index",
        type=Path,
        help="فهرس Markdown صريح للملفات الممرّرة فقط؛ لا اكتشاف للمستودع",
    )
    args = parser.parse_args(argv)
    checked, errors = validate_files(args.files, index_path=args.index)
    report = {
        "schema_version": 1,
        "status": "failed" if errors else "passed",
        "checked": checked,
        "error_counts": dict(sorted(errors.items())),
        "measurement_limits": [
            "explicit_files_only_no_repository_discovery_or_inventory",
            "explicit_index_only_no_repository_discovery_or_inventory",
            "aggregate_error_codes_only_no_paths_names_or_values",
        ],
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
