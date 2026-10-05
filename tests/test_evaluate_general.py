"""الرقمُ العام (ق٧٣ الخطوة 1.7أ): كاملُ المفتوح عبر ذراع الأساس الوكيل ببذورٍ وWilson، والأعطالُ خارج المقام."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from core.contracts import Response, Usage
from evaluation.ablation import AblationError, arm
from services.agent_workspace import decode_input

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.evaluate_general import run_general, summarize  # noqa: E402


def _case(case_id, text, checks):
    return {"case_id": case_id, "capability": "general", "critical": False, "reference": "", "rubric": "",
            "messages": [{"role": "user", "content": text}], "checks": checks}


def _bank(root: Path, tiers: dict[str, list[dict]]) -> Path:
    for tier, cases in tiers.items():
        (root / tier).mkdir(parents=True)
        (root / tier / "s.json").write_text(json.dumps({"schema_version": 1, "suite_id": tier, "split": "development",
                                                        "description": "d", "cases": cases}, ensure_ascii=False))
    return root


class SeedReplay:
    """يجيب بحسب البذرة والسؤال، ويسجّل البذورَ والأدواتَ التي بلغته."""
    name, is_local, model = "replay", True, "replay"

    def __init__(self, answer, *, seed=0, seen=None):
        self.answer, self.seed, self.seen = answer, seed, [] if seen is None else seen

    def with_seed(self, seed):
        return type(self)(self.answer, seed=seed, seen=self.seen)

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        user = decode_input(request.messages[-1].content)["user_request"]
        self.seen.append((self.seed, bool(request.tools)))
        return Response(self.answer(user, self.seed), Usage(1, 1), "complete", 0, provider="replay",
                        model_version="v1")


def test_the_general_number_runs_every_checked_case_through_the_seeded_agentic_baseline(tmp_path):
    rabat = [{"kind": "contains", "value": "الرباط"}]
    bank = _bank(tmp_path / "open", {
        "tier_a": [_case(f"a{i}", "ما عاصمة المغرب؟", rabat) for i in range(4)] + [_case("none", "بلا فحص", [])],
        "tier_b": [_case("b0", "عاصمة المغرب؟", rabat), _case("b1", "سؤالٌ آخر", rabat)],
    })

    def answer(user, seed):                       # a0..a3 تنجح دائمًا؛ b0 تنجح في بذرتين من ثلاث؛ b1 لا تنجح
        if user == "سؤالٌ آخر":
            return "فاس"
        if user == "عاصمة المغرب؟":
            return "الرباط" if seed in (0, 2) else "فاس"
        return "الرباط"

    seen = []
    report = run_general(SeedReplay(answer, seen=seen), model="replay", model_version="v1", bank_open=bank,
                         command="python3 tools/evaluate_general.py --model replay")
    assert report["tool"] == "tools/evaluate_general.py" and report["command"].startswith("python3 tools/")
    assert report["config"]["seeds"] == [0, 1, 2] and report["config"]["arm"] == arm()
    assert report["config"]["bank"]["cases_with_automatic_check"] == 6
    assert report["config"]["bank"]["cases_without_automatic_check"] == 1
    assert {seed for seed, _ in seen} == {0, 1, 2} and all(tools for _, tools in seen)   # الأدواتُ معلنة: الطريقُ الوكيل
    assert report["overall"] == {"offered": 6, "measured": 6, "passes": 5, "error_count": 0,
                                 "pass_rate": round(5 / 6, 4), "wilson95": report["overall"]["wilson95"]}
    low, high = report["overall"]["wilson95"]
    assert 0 < low < 5 / 6 < high <= 1
    assert report["by_tier"]["tier_a"]["passes"] == 4 and report["by_tier"]["tier_b"]["passes"] == 1
    assert any("temperature_0" in limit for limit in report["measurement_limits"])


def test_errored_cases_leave_the_denominator_and_are_counted_not_failed():
    rows = [{"status": "measured", "passed": True}, {"status": "measured", "passed": False},
            {"status": "error", "code": "seed_run_error"}]
    summary = summarize(rows)
    assert (summary["offered"], summary["measured"], summary["passes"], summary["error_count"]) == (3, 2, 1, 1)
    assert summary["pass_rate"] == 0.5


def test_an_empty_bank_and_even_seeds_are_refused_by_name(tmp_path):
    empty = _bank(tmp_path / "open", {"tier_a": [_case("none", "بلا فحص", [])]})
    with pytest.raises(AblationError, match="bank_empty"):
        run_general(SeedReplay(lambda u, s: ""), model="replay", model_version="v1", bank_open=empty)
    with pytest.raises(AblationError, match="seeds_invalid"):
        run_general(SeedReplay(lambda u, s: ""), model="replay", model_version="v1", bank_open=empty, seeds=(0, 1))
