"""ك٤٢ (#17): معرّفٌ واحد لكل مهمّة في البنك، وكلفةُ التشغيل حقيقيّة.

١. في الشطر المفتوح من بنك Kimi v1 ستّةٌ وعشرون `task_id` مشتركًا بين
   `kimi_agentic_001` و`kimi_agentic_002`، ومحتوى كل زوجٍ مختلف. والتقاريرُ تُقرأ
   خارج حزمها، فالمعرّفُ المقروء هو المؤهَّل `suite_id/task_id`. ويفرض
   `validate_agentic_bank` تفرّدَ `suite_id` الذي يقوم عليه تفرّدُه.
   والبنكُ نفسُه لم يُمسّ، لأن بصماته في البيان المختوم.
٢. `Run.cost_micros` كان يعيد صفرًا ثابتًا. صار مجموعَ ما سُوّي لخطواته،
   والمُعادُ عرضُه صفرٌ لأنه لم يُنفق جديدًا.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.builtin_tools import DEFAULT_TOOLS
from agent.registry import ToolRegistry
from core.budget import Budget
from core.canonical import PayloadRejected
from core.contracts import Response, ToolCall, Usage
from core.ledger import Ledger
from agent.actions import ActionStore
from agent.loop import run_agent
from evaluation.agentic_runner import (qualified_task_id, run_agentic_suite,
                                       validate_agentic_bank)
from tests.test_agent_loop import Scripted as LoopScripted, full, space  # noqa: F401
from tests.test_agentic_runner import FIX_TASK, Scripted, says

ROOT = Path(__file__).resolve().parent.parent
TIER_D = ROOT / "evaluation" / "banks" / "kimi_v1" / "open" / "tier_d"


def _open_suites() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(TIER_D.glob("kimi_agentic_*.json"))
            if not p.name.endswith(".meta.json")]


@pytest.mark.skipif(not TIER_D.is_dir(), reason="الشطر المفتوح غير موضوع")
def test_the_open_bank_has_colliding_bare_ids_and_distinct_qualified_ones():
    suites = _open_suites()
    bare = [task["task_id"] for suite in suites for task in suite["tasks"]]
    qualified = validate_agentic_bank(suites)
    assert len(bare) == len(qualified) == 140
    # الرقمُ المنشور في تقييم ٢٥ سبتمبر: يبقى صادقًا حتى يُصلحه البنك v1.2
    assert len(bare) - len(set(bare)) == 26
    assert len(set(qualified)) == 140


def _suite(suite_id: str) -> dict:
    return {"schema_version": 1, "suite_id": suite_id, "kind": "agentic_tasks",
            "description": "حزمةٌ صوريّة", "tasks": [json.loads(json.dumps(FIX_TASK))]}


def test_two_suites_with_one_suite_id_are_refused():
    assert validate_agentic_bank([_suite("a"), _suite("b")]) == ["a/fix_area", "b/fix_area"]
    with pytest.raises(PayloadRejected) as err:
        validate_agentic_bank([_suite("a"), _suite("a")])
    assert err.value.code == "suite_id_duplicate"


def test_the_qualified_id_names_the_suite():
    assert qualified_task_id("kimi_agentic_002", "agentic_0002") == "kimi_agentic_002/agentic_0002"


def test_every_report_row_carries_its_qualified_id(monkeypatch):
    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", "pytest-attested-host")
    report = run_agentic_suite(_suite("bank_x"), Scripted(says("لا شيء.")),
                               ToolRegistry(*DEFAULT_TOOLS), model="m", model_version="v")
    assert [row["qualified_id"] for row in report["results"]] == ["bank_x/fix_area"]


def _costing(content, cost, *calls):
    return Response(content, Usage(1, 1), "complete", cost, provider="scripted",
                    model_version="v1", tool_calls=tuple(calls))


def _script():
    return LoopScripted(_costing("اقرأ", 7, ToolCall("c1", "read_file", {"path": "app.py"})),
                        _costing("تمّ", 5))


def _common(space, full):  # noqa: F811
    return dict(registry=ToolRegistry(*DEFAULT_TOOLS), context=full,
                ledger=Ledger(space / "shared.jsonl"),
                action_store=ActionStore(space.parent / "actions", space),
                session_id="s", turn_id="t", budget=Budget(1000, 1000),
                model="fixture", model_version="v1", idempotency_prefix="run-a")


def test_the_run_cost_is_the_sum_of_what_its_steps_settled(space, full):  # noqa: F811
    run = run_agent("مهمّة", _script(), **_common(space, full))
    assert run.status == "complete"
    assert [step.cost_micros for step in run.steps] == [7, 5]
    assert run.cost_micros == 12


def test_a_replayed_run_spent_nothing_new(space, full):  # noqa: F811
    common = _common(space, full)
    first = run_agent("مهمّة", _script(), **common)
    second = run_agent("مهمّة", _script(), **common)
    assert first.cost_micros == 12
    assert [s.replayed for s in second.steps] == [True, True]
    assert second.cost_micros == 0
