#!/usr/bin/env python3
"""التسليمُ بين المسارات فحصٌ آليّ لا ذاكرة (ك٣٠).

`AGENTS.md` §٢: المسارُ ملكٌ لصاحبه وليس سورًا، ومن يعدّل مسارَ غيره يكتب في إيداعه سطرًا
يبدأ بـ«تسليم:». وملفُّ CODEOWNERS لا يخدم هذا لأن العملاء كلَّهم يدفعون بحساب GitHub
واحد؛ فالعميلُ هو ذيلُ `Diwan-Agent` لا مؤلّفُ git. تقرأ هذه الأداة كلَّ إيداعٍ في المدى:
عائلةَ عميله (ما قبل «/» في المعرّف) والمساراتِ التي مسّها، فإن مسّ مسارًا تملكه عائلةٌ
أخرى بلا سطر «تسليم:» سُجّل اعتراضٌ مسمًّى ورُدّ الإيداع. والمالكُ (`human/*`) حَكَمٌ لا
يُسأل تسليمًا، والمساراتُ غيرُ المدرجة في `registry/lanes.json` بلا مالك.

    python tools/lane_handoff.py --range origin/main..HEAD

**الحدُّ المعلَن:** الإقرارُ ذاتيٌّ كالنسبة (`tools/agent_attribution.py`): الأداةُ تمنع
تعديلًا **صامتًا** لمسار الغير، ولا تحكم على صحة ما كُتب بعد «تسليم:». والإيداعاتُ
السابقةُ لوقت التفعيل مُعفاة، فالماضي يُسجَّل ولا يُزوَّر.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_attribution import (LOG_ARGV, REGISTRY_PATH, AttributionError,  # noqa: E402
                               load_registry, parse_log, parse_trailers)

ROOT = Path(__file__).resolve().parent.parent
LANES_PATH = "registry/lanes.json"


class LaneError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


def load_lanes(raw: str | bytes) -> dict:
    """يقرأ الخريطة ويرفض شكلًا غيرَ مُعلَن."""
    try:
        lanes = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise LaneError("lanes_unreadable") from exc
    required = {"schema_version", "handoff_marker", "enforced_from", "lanes", "limits"}
    if not isinstance(lanes, dict) or lanes.keys() != required or lanes["schema_version"] != 1:
        raise LaneError("lanes_schema_invalid")
    if not isinstance(lanes["handoff_marker"], str) or not lanes["handoff_marker"].strip():
        raise LaneError("lanes_schema_invalid")
    try:
        moment = datetime.fromisoformat(lanes["enforced_from"].replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise LaneError("lanes_schema_invalid") from exc
    if moment.tzinfo is None:
        raise LaneError("lanes_schema_invalid")
    lanes["enforced_from_epoch"] = int(moment.timestamp())
    table = lanes["lanes"]
    if not isinstance(table, dict) or not table:
        raise LaneError("lanes_schema_invalid")
    for family, prefixes in table.items():
        if not isinstance(family, str) or "/" in family or not family:
            raise LaneError("lanes_schema_invalid", family)
        if not isinstance(prefixes, list) or not all(isinstance(p, str) and p for p in prefixes):
            raise LaneError("lanes_schema_invalid", family)
    return lanes


def lane_of(path: str, lanes: dict) -> str | None:
    """العائلةُ المالكة للمسار: أطولُ بادئةٍ مطابقة (الدليلُ ينتهي بـ«/»، والملفُّ بمساره)."""
    best: tuple[int, str] | None = None
    for family, prefixes in lanes["lanes"].items():
        for prefix in prefixes:
            hit = path.startswith(prefix) if prefix.endswith("/") else path == prefix
            if hit and (best is None or len(prefix) > best[0]):
                best = (len(prefix), family)
    return best[1] if best else None


def family_of(agent_id: str) -> str:
    return agent_id.split("/", 1)[0]


def declares_handoff(message: str, marker: str) -> bool:
    return any(line.strip().startswith(marker) for line in message.splitlines())


def inspect_commit(commit: dict, lanes: dict, registry: dict) -> dict | None:
    """اعتراضٌ مسمًّى، أو None إن كان الإيداع في مساره أو أعلن تسليمه."""
    if commit["committed_at"] < lanes["enforced_from_epoch"]:
        return None
    values = parse_trailers(commit["message"]).get(registry["trailer"], [])
    if len(values) != 1:
        return None                       # غيابُ النسبة شأنُ أداة النسبة، لا هذه
    agent = values[0]
    family = family_of(agent)
    if family == "human":
        return None
    if declares_handoff(commit["message"], lanes["handoff_marker"]):
        return None
    foreign = sorted({(path, owner) for path in commit["paths"]
                      if (owner := lane_of(path, lanes)) is not None and owner != family})
    if not foreign:
        return None
    return {"sha": commit["sha"], "subject": commit["message"].splitlines()[0][:72],
            "agent": agent, "code": "lane_handoff_missing",
            "paths": [{"path": path, "lane": owner} for path, owner in foreign],
            "detail": f"مسّ مسارَ غيره بلا سطر «{lanes['handoff_marker']}»"}


def check_commits(commits: list[dict], lanes: dict, registry: dict) -> dict:
    findings = [f for f in (inspect_commit(c, lanes, registry) for c in commits) if f]
    return {"schema_version": 1, "status": "failed" if findings else "passed",
            "code": "handoff_missing" if findings else "lanes_respected",
            "checked": len(commits), "enforced_from": lanes["enforced_from"],
            "findings": findings, "limits": lanes["limits"]}


def _git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True,
                          text=True).stdout


def commits_in_range(repo: Path, rev_range: str) -> list[dict]:
    """إيداعاتُ المدى بلا دمج، كلٌّ بمساراته."""
    commits = parse_log(_git(repo, *LOG_ARGV, rev_range))
    for commit in commits:
        names = _git(repo, "show", "--name-only", "--format=", "--no-renames", commit["sha"])
        commit["paths"] = [line.strip() for line in names.splitlines() if line.strip()]
    return commits


def check_range(repo: Path, rev_range: str, *, lanes: dict, registry: dict) -> dict:
    return check_commits(commits_in_range(repo, rev_range), lanes, registry)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--range", required=True, help="مدى git، مثل origin/main..HEAD")
    parser.add_argument("--repo", default=str(ROOT))
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    try:
        lanes = load_lanes((repo / LANES_PATH).read_bytes())
        registry = load_registry((repo / REGISTRY_PATH).read_bytes())
        report = check_range(repo, args.range, lanes=lanes, registry=registry)
    except (LaneError, AttributionError, OSError, subprocess.CalledProcessError) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        print(json.dumps({"status": "error", "code": code, "detail": str(exc)[:200]}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
