#!/usr/bin/env python3
"""جولتا ج٥ الحيّتان عبر الواجهة نفسها: النموذجُ يستدعي web_search بنفسه، وrun_command يمرّ بالحاوية.

    python3 tools/probe_j5_agent_round.py --model qwen3.5:9b --web-search-url http://127.0.0.1:8080 \\
        --runtime-receipt <إيصال التشغيل الخاص> --out docs/probe/j5-docker-searxng-<التاريخ>.json

- الطريقُ `webui.server.LocalApp.dispatch` بالمزوّدين اللذين تبنيهما `tools/serve_ui.py`، لا نداءً مباشرًا للأداة.
- البحثُ في جلسة «research»، والأمرُ في جلسة «agent» بموافقة المالك (`agent_decide`) كما في الواجهة.
- يُسجَّل ما نادى به النموذجُ وحالُ كل نتيجة، وعناوينُ المصادر، وشكلُ حدّ التنفيذ لا معرّفُه.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.web_search import SearxngBackend  # noqa: E402
from providers.local_chat import LocalChatProvider  # noqa: E402
from providers.local_tools import LocalToolProvider  # noqa: E402
from webui.server import LocalApp  # noqa: E402

SEARCH_QUESTIONS = (
    "ابحث في الويب: ما أحدث إصدارٍ من لغة بايثون، ومتى صدر؟ اذكر المصادر.",
    "ما أبرز أخبار اليوم في السعودية؟ ابحث في الويب واذكر روابط المصادر.",
    "ابحث في الويب عن موقع المشروع الرسمي لـSearXNG، وأعطني رابطه.",
)
COMMAND_REQUEST = 'شغّل الأمر python3 -c "print(2+2)" وأخبرني بالناتج.'


def _digest(model: str, base: str = "http://127.0.0.1:11434") -> str:
    with urllib.request.urlopen(base + "/api/tags", timeout=10) as response:
        models = json.loads(response.read().decode("utf-8"))["models"]
    return next(m["digest"] for m in models if m["name"] == model)


def _tools(result: dict) -> list[dict]:
    return [{"name": r.get("name"), "status": r.get("status")}
            for step in result.get("steps", []) for r in step.get("tool_results", [])]


def _shape(text: str) -> list[str]:
    return sorted(set(re.sub(r"[0-9a-f]{12,}", "<hex>", b) for b in re.findall(r"docker:[\w:@<>]+", text)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--web-search-url", required=True)
    parser.add_argument("--runtime-receipt", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"التقريرُ قائم: {args.out}")
    version = _digest(args.model)
    root = Path(tempfile.mkdtemp(prefix="diwan-j5-ui-")).resolve()
    try:
        app = LocalApp(root, model=args.model, model_version=version,
                       provider_factory=lambda: LocalChatProvider(args.model, version),
                       agent_provider_factory=lambda: LocalToolProvider(args.model, version),
                       runtime_receipt=args.runtime_receipt.resolve(),
                       web_search=SearxngBackend(args.web_search_url),
                       docker_executable=shutil.which("docker") or "/usr/local/bin/docker")
        api = lambda action, **values: app.dispatch({"action": action, **values})
        project = api("create_project", name="ج٥")["id"]

        attempts = []
        for question in SEARCH_QUESTIONS:
            research = api("create_session", project=project, name="بحث", mode="research")["id"]
            searched = api("agent_ask", project=project, session=research, turn=uuid.uuid4().hex,
                           message=question, files=[])
            content = searched.get("content") or ""
            attempts.append({"question": question, "status": searched.get("status"),
                             "steps": len(searched.get("steps", [])), "tool_calls_by_the_model": _tools(searched),
                             "called_web_search": any(t["name"] == "web_search" for t in _tools(searched)),
                             "citations_passed": (searched.get("citations") or {}).get("passed"),
                             "answer_urls": sorted(set(re.findall(r"https?://[^\s\"\\)\]]+", content)))[:10],
                             "answer_chars": len(content)})
            if attempts[-1]["called_web_search"]:
                break

        agent = api("create_session", project=project, name="أمر", mode="agent")["id"]
        turn = uuid.uuid4().hex
        asked = api("agent_ask", project=project, session=agent, turn=turn, message=COMMAND_REQUEST, files=[])
        decided = resumed = None
        pending = [a for a in asked.get("pending", []) if a.get("name") == "run_command"]
        if asked.get("status") == "awaiting_owner" and pending:
            action = pending[0]
            decided = api("agent_decide", project=project, session=agent, action_id=action["action_id"],
                          call_digest=action["call_digest"], expected_revision=action["revision"], approve=True)
            resumed = api("agent_resume", project=project, session=agent, turn=turn)
        final = resumed or asked
        command_text = json.dumps(final, ensure_ascii=False)
        app.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)

    report = {
        "schema_version": 1, "date": datetime.date.today().isoformat(), "task": "ج٥", "issue": "power0man/diwan#23",
        "author": "anthropic/claude-opus-5-5",
        "host": {"machine": "MacBook Pro (Apple silicon)", "os": os.uname().sysname + " " + os.uname().release},
        "engine": {"provider": "ollama-local", "model": args.model, "digest": version},
        "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                          for p in ("core/execution.py", "agent/web_search.py", "webui/server.py")},
        "web_search": {"via": "webui.server.LocalApp.dispatch agent_ask (research session)",
                       "called_web_search": any(a["called_web_search"] for a in attempts),
                       "attempts": attempts, "attempts_until_called": len(attempts)},
        "docker_execution": {"via": "webui.server.LocalApp.dispatch agent_ask → agent_decide(approve) → agent_resume",
                             "request": COMMAND_REQUEST, "first_status": asked.get("status"),
                             "owner_approved": decided is not None, "final_status": final.get("status"),
                             "tool_calls_by_the_model": _tools(final),
                             "boundary_shapes": _shape(command_text),
                             "answer": (final.get("content") or "")[:300]},
        "measurement_limits": [
            "one_live_round_each_on_one_local_model_no_variance_estimate",
            "the_model_decides_whether_to_call_the_tool_every_attempt_is_recorded_including_those_without_a_call",
            "search_results_come_from_public_engines_through_a_local_searxng_and_change_over_time",
            "container_ids_recorded_by_shape_not_value",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"web_search_called": report["web_search"]["called_web_search"],
                      "attempts": [(a["called_web_search"], a["answer_chars"], len(a["answer_urls"]))
                                   for a in attempts],
                      "command": [report["docker_execution"]["first_status"], report["docker_execution"]["final_status"]],
                      "tools": report["docker_execution"]["tool_calls_by_the_model"],
                      "boundary": report["docker_execution"]["boundary_shapes"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
