"""Server-issued unified-memory lineage, shared by the UI and offline backup.

These are structural bindings, not signatures against an attacker controlling
owner-only storage. HTTP clients cannot select these fields. A successor has
one deterministic identity and a complete chain to the canonical conversation.
"""
import re

from core.canonical import digest

DEFAULT_PROJECT_ID = digest({"kind": "diwan-default-project", "schema_version": 1})[:32]
DEFAULT_SESSION_ID = digest({"kind": "diwan-default-session", "schema_version": 1})[:32]
UNIFIED_SESSION_ROLE = "unified_all_projects"
_ID = re.compile(r"[a-f0-9]{32}\Z")


def successor_id(predecessor):
    return digest({"kind": "diwan-unified-continuation", "schema_version": 1,
                   "predecessor": predecessor})[:32]


def valid_role(value, project):
    """Validate one metadata record without trusting an arbitrary role/UUID."""
    if type(value) is not dict:
        return False
    if "system_role" not in value:
        return "continuation_of" not in value
    fields = {"id", "name", "mode", "system_role"}
    if (set(value) not in (fields, fields | {"continuation_of"})
            or value["system_role"] != UNIFIED_SESSION_ROLE or project != DEFAULT_PROJECT_ID
            or value.get("mode") not in ("text", "agent")):
        return False
    if value.get("id") == DEFAULT_SESSION_ID:
        return "continuation_of" not in value
    previous = value.get("continuation_of")
    return (isinstance(previous, str) and _ID.fullmatch(previous) is not None
            and value.get("id") == successor_id(previous))


def valid_lineage(value, records):
    """Require every predecessor, with no missing, isolated or cyclic link."""
    seen = set()
    while type(value) is dict and value.get("system_role") == UNIFIED_SESSION_ROLE:
        sid = value.get("id")
        if sid in seen or len(seen) >= 64 or not valid_role(value, DEFAULT_PROJECT_ID):
            return False
        seen.add(sid)
        if sid == DEFAULT_SESSION_ID:
            return True
        value = records.get(value["continuation_of"], {})
    return False
