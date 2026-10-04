"""#194: only complete research turns reach scoring; ablation errors keep their arm."""
from __future__ import annotations

import pytest

from agent.loop import Run
from core.contracts import Response, ToolCall, Usage
from evaluation import research_runner
from evaluation.ablation import compare, judge
from evaluation.research_bank import FixtureSearch, load, summarize
from tools.evaluate_ablation import run_component

SUITE, CORPUS, META = load()
ITEM = next(item for item in SUITE["questions"] if item["category"] == "unanswerable")


class StoppingProvider:
    name, is_local = "replay", True

    def __init__(self, stop_reason="complete", *, search_forever=False):
        self.stop_reason, self.search_forever = stop_reason, search_forever
        self.calls = 0

    def with_seed(self, seed):
        return type(self)(self.stop_reason, search_forever=self.search_forever)

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        calls = (ToolCall(f"search-{self.calls}", "web_search", {"query": "مدينة لجين"}),) \
            if self.search_forever else ()
        return Response("لم أجد ذلك في المصادر.", Usage(1, 1), self.stop_reason, 0,
                        provider=self.name, model_version="v1", tool_calls=calls)


def _run(provider, **options):
    return research_runner.run_item(ITEM, provider, FixtureSearch(CORPUS),
                                    model="replay", model_version="v1", **options)


def _forbid_scoring(monkeypatch):
    def unexpected_score(*args):
        pytest.fail("an unfinished answer reached the scorer")
    monkeypatch.setattr(research_runner, "score_item", unexpected_score)


@pytest.mark.parametrize("stop_reason, status", [
    ("max_output", "truncated"), ("deadline", "timed_out"),
    ("refused", "refused"), ("error", "failed"),
])
def test_provider_stops_are_named_errors_without_scoring(monkeypatch, stop_reason, status):
    _forbid_scoring(monkeypatch)
    provider = StoppingProvider(stop_reason)
    row = _run(provider)
    assert provider.calls == 1
    assert row == {"id": ITEM["id"], "category": ITEM["category"], "status": "error",
                   "code": "response_" + stop_reason, "loop_status": status}
    summary = summarize([row], META["thresholds"])
    assert summary["measured"] == 0 and summary["errors"] == 1 and not summary["meets"]
    assert summary["errors_by_code"] == {"response_" + stop_reason: 1}           # الرمزُ في الملخّص لا العددُ وحده (#285)


def test_exhausting_search_steps_never_scores_the_intermediate_answer(monkeypatch):
    _forbid_scoring(monkeypatch)
    provider = StoppingProvider(search_forever=True)
    row = _run(provider, max_steps=2)
    assert provider.calls == 2
    assert row == {"id": ITEM["id"], "category": ITEM["category"], "status": "error",
                   "code": "max_steps_exhausted", "loop_status": "step_limit"}


def test_a_new_unfinished_status_falls_back_to_its_name_without_scoring(monkeypatch):
    _forbid_scoring(monkeypatch)
    monkeypatch.setattr(research_runner, "run_agent", lambda *args, **kwargs: Run("interrupted", None, "partial"))
    row = _run(StoppingProvider())
    assert row["status"] == "error" and row["code"] == row["loop_status"] == "interrupted"


def test_complete_reports_and_search_ablation_identify_research_runner_v2(tmp_path):
    report = research_runner.run_bank(StoppingProvider(), model="replay", model_version="v1")
    assert report["config"]["runner_version"] == 2
    assert report["summary"]["measured"] == 30 and report["summary"]["errors"] == 0
    assert all(row["loop_status"] == "complete" for row in report["results"])
    ablation = run_component("search", StoppingProvider("max_output"), model="replay", model_version="v1",
                             bank_open=tmp_path)
    assert ablation["config"]["research_runner_version"] == 2
    assert ablation["judgment"]["overall"]["errors_by_arm"] == {"on": 30, "off": 30}
    assert ablation["judgment"]["overall"]["errors"] == 30
    assert ablation["judgment"]["overall"]["pairs"] == 0


def _row(item_id, *, error=False, category="selected", passed=True):
    return {"id": item_id, "category": category, "status": "error" if error else "measured",
            **({"code": "response_max_output"} if error else {"passed": passed})}


def test_guard_ablation_keeps_general_and_benefit_arm_errors_separate():
    on = [_row("benefit", error=True, category="prompt_injection_resistance"), _row("general")]
    off = [_row("benefit", category="prompt_injection_resistance"), _row("general", error=True)]
    result = judge("quarantine", on, off)
    assert result["overall"]["errors_by_arm"] == {"on": 0, "off": 1}
    assert result["benefit_subset"]["errors_by_arm"] == {"on": 1, "off": 0}


@pytest.mark.parametrize("include_pair", [False, True], ids=["no-measured-pairs", "with-measured-pair"])
def test_error_counts_distinguish_arms_and_respect_the_category_filter(include_pair):
    on = [_row("both", error=True), _row("on", error=True), _row("off"),
          _row("outside", error=True, category="outside")]
    off = [_row("both", error=True), _row("on"), _row("off", error=True),
           _row("outside", category="outside")]
    if include_pair:
        on.append(_row("pair"))
        off.append(_row("pair", passed=False))
    result = compare(on, off, categories={"selected"}, include_arm_errors=True)
    assert result["pairs"] == int(include_pair) and result["errors"] == 3
    assert result["errors_by_arm"] == {"on": 2, "off": 2}
    assert compare(off, on, categories={"selected"}, include_arm_errors=True)["errors_by_arm"] == {"on": 2, "off": 2}
    assert compare(on, off, include_arm_errors=True)["errors_by_arm"] == {"on": 3, "off": 2}
    legacy = compare(on, off, categories={"selected"})
    assert legacy == {key: value for key, value in result.items() if key != "errors_by_arm"}
    assert result["on_rate"] == (1.0 if include_pair else None)
    assert result["off_rate"] == (0.0 if include_pair else None)
