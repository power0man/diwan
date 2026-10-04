"""#165: separate recorded history echoes without accepting a failed scenario."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from core.canonical import PayloadRejected
from evaluation import memory_runner as runner
from tools import evaluate_memory as cli

ROOT = Path(__file__).resolve().parents[1]
BANK = json.loads((ROOT / "evaluation/suites/memory_v1.json").read_text())
NEEDLE = "synthetic-marker-739129"
ECHO = f"2: context holds absent «{NEEDLE}» in the model's own earlier reply"
BLOCK = f"2: context holds absent «{NEEDLE}»"


def _result(failures, category="forget", name="synthetic"):
    return {"id": name, "category": category, "failures": list(failures), "passed": not failures,
            "leaks": 0, "consent_violations": 0, "injection_unquarantined": 0,
            "history_echoes": runner._history_count(failures)}


def _views(monkeypatch, views):
    class Probe:
        probe_resets = 0
        stuck_turns = []

        def __init__(self, *args):
            pass

        def contexts(self, *args):
            return views

        def close(self):
            pass
    monkeypatch.setattr(runner, "_Wired", Probe)
    scenario = {"id": "synthetic", "category": "forget", "steps": [
        {"expect": "context", "project": "A", "question": "ماذا تتذكر؟", "present": [], "absent": [NEEDLE]}]}
    return runner.run_memory_bank({**BANK, "scenarios": [scenario]}, driver="wired")


@pytest.mark.parametrize("kind,rate,echoes", [("echo", 1.0, 2), ("block", 0.0, 0), ("both", 0.0, 1)], ids=["echo", "block", "both"])
def test_wired_echo_and_current_block_remain_distinct(monkeypatch, kind, rate, echoes):
    echo = ("", NEEDLE, NEEDLE, "", "", "")
    block = (NEEDLE, NEEDLE, "", "", "", NEEDLE)
    views = {"echo": [echo, echo], "block": [block], "both": [echo, block]}[kind]
    report = _views(monkeypatch, views)
    result = report["results"][0]
    assert result["history_echoes"] == report["metrics"]["history_echoes"] == echoes
    assert report["metrics"]["forget_rate"] == rate
    assert len(result["failures"]) == len(views) and result["passed"] is False
    assert report["passed"] == 0 and report["meets_thresholds"] is False
    assert report["thresholds"] == {**BANK["thresholds"], "history_echoes": 0}
    assert report["threshold_results"]["history_echoes"] is (echoes == 0)
    assert report["threshold_results"]["forget_rate"] is (rate == 1.0)
    assert report["threshold_results"]["all_scenarios_passed"] is False
    assert sum(f.endswith("in the model's own earlier reply") for f in result["failures"]) == echoes


@pytest.mark.parametrize("history,echoes,rate,where", [
    ((("user", "حسنًا"), ("assistant", f"أتذكّر {NEEDLE}")), 1, 1.0, " in the model's own earlier reply"),
    ((("user", f"تذكّر أن {NEEDLE}"), ("assistant", "حسنًا")), 0, 0.0, " elsewhere in the request as sent to the model"),
    ((("user", f"تذكّر أن {NEEDLE}"), ("assistant", f"أتذكّر {NEEDLE}")), 0, 0.0, " elsewhere in the request as sent to the model"),
], ids=["echo_only", "owner_words_only", "owner_words_and_echo"])
def test_an_echo_exempts_only_a_witness_found_in_the_models_replies_and_nowhere_else(monkeypatch, history, echoes, rate, where):
    """تدقيقٌ لاحقٌ لـe8dcd74: المكانُ الأولُ المطابق كان يُسجَّل وحده، فشاهدٌ بقي في كلام المالك السابق ورددّه النموذجُ أيضًا
    سُمّي صدًى وخرج من مقام `forget_rate`. الصدى الآن ما وقع في ردود النموذج وحدها، كما يقول #165."""
    from core.contracts import Message, Request
    request = Request((*(Message(role, text) for role, text in history), Message("user", "ماذا تتذكر؟")),
                      "m", "0" * 64, 32, 5.0, "local_only", None)
    report = _views(monkeypatch, [runner._memory_view(request)])
    result = report["results"][0]
    assert result["failures"] == [f"0: context holds absent «{NEEDLE}»{where}"]
    assert result["history_echoes"] == report["metrics"]["history_echoes"] == echoes
    assert report["metrics"]["forget_rate"] == rate and result["passed"] is False


def test_the_echo_rule_is_published_with_every_live_report_and_recount():
    assert runner.HISTORY_LOCATION_LIMIT in cli.LIMITS
    report = {"results": [_result([ECHO]) | {"id": s["id"], "category": s["category"]} for s in BANK["scenarios"]],
              "metrics": {"forget_rate": 0.0, "history_echoes": None, "leakage": 0, "consent_violations": 0,
                          "injection_unquarantined": 0},
              "meets_thresholds": False, "measurement_limits": []}
    assert runner.HISTORY_LOCATION_LIMIT in runner.recount_history(report, BANK)["measurement_limits"]


@pytest.mark.parametrize("location", [" in the current question as sent to the model", " in the declared tool schemas",
                                      " elsewhere in the request as sent to the model", ""])
def test_unserved_does_not_mean_history_echo(location):
    failures = [BLOCK + location]
    result = _result(failures)
    result["absent_found"] = [{"step": 2, "witness": 0, "served": False}]
    assert result["history_echoes"] == 0
    assert runner._forget_rate([result]) == 0.0
    assert result["failures"] == failures


@pytest.mark.parametrize("failure", [f"2: retrieve holds absent «{NEEDLE}»", f"2: residue holds «{NEEDLE}»",
                                     "2: raised MemoryRefused memory_store_corrupt", "2: raised BackupError backup_invalid",
                                     "2: raised OSError", "2: receipts 0 != 1", "2: item not exposed in context before forget"])
def test_other_failures_still_lower_forget_rate(failure):
    result = _result([ECHO, failure], category="backup")
    assert result["history_echoes"] == 1
    assert runner._forget_rate([result]) == 0.0
    assert result["failures"] == [ECHO, failure]


def test_forget_denominator_and_echo_sum_cover_the_whole_bank(monkeypatch):
    results = [_result([ECHO, ECHO], name="echo"), _result([BLOCK], "backup", "block"),
               _result([], name="ok"), _result([ECHO], "isolation", "elsewhere")]
    for result in results:
        result["context_exposures"] = 0
    monkeypatch.setattr(runner, "run_scenario", lambda scenario, root: copy.deepcopy(scenario))
    report = runner.run_memory_bank({**BANK, "scenarios": results})
    assert report["metrics"]["forget_rate"] == 0.6667
    assert report["metrics"]["history_echoes"] == 3
    assert report["passed"] == 1 and report["total"] == 4
    assert report["meets_thresholds"] is False
    assert [r["failures"] for r in report["results"]] == [r["failures"] for r in results]
    assert runner._forget_rate([results[-1]]) is None
    assert runner._forget_rate([{**results[2], "passed": False}]) == 0.0


def test_recount_preserves_original_measurement_failures_and_first_from(tmp_path, monkeypatch, capsys):
    original = json.loads((ROOT / "docs/probe/memory-live-20260928.json").read_text())
    # Restore the recorded pre-#165 counters, regardless of the checked-in recount.
    original["metrics"].pop("history_echoes", None)
    original["metrics"]["forget_rate"] = 0.3333
    original["recount"] = {"leakage": original["recount"]["leakage"]}
    for result in original["results"]:
        result.pop("history_echoes", None)
    original["measurement_limits"] = ["original_bound"]
    path = tmp_path / "historical.json"
    path.write_text(json.dumps(original, ensure_ascii=False))
    monkeypatch.setattr(cli, "_digest", lambda *args: pytest.fail("recount must not query Ollama"))
    monkeypatch.setattr(cli, "run_memory_bank", lambda *args, **kw: pytest.fail("recount must not remeasure"))
    assert cli.main(["--recount", str(path)]) == 0
    first = json.loads(path.read_text())
    assert json.loads(capsys.readouterr().out)["history_echoes"]["to"] == 19
    assert first["metrics"]["forget_rate"] == 1.0 and first["metrics"]["history_echoes"] == 19
    assert first["passed"] == original["passed"] == 21 and first["total"] == original["total"] == 30
    assert first["meets_thresholds"] is False
    assert sum(r["history_echoes"] > 0 for r in first["results"]) == 9
    assert first["threshold_results"]["forget_rate"] is True
    assert first["threshold_results"]["history_echoes"] is False
    assert first["recount"]["forget_rate"]["from"] == 0.3333
    assert first["recount"]["history_echoes"]["from"] is None
    assert first["recount"]["meets_thresholds"]["from"] is False
    for key in ("forget_rate", "history_echoes", "meets_thresholds"):
        assert first["recount"][key]["rule"] == runner.HISTORY_RULE and first["recount"][key]["date"]
    for before, after in zip(original["results"], first["results"]):
        assert {k: v for k, v in after.items() if k != "history_echoes"} == before
    for key in ("suite_sha256", "date", "engine", "agent", "probe_sessions_reset", "stuck_probe_turns"):
        assert first[key] == original[key]
    assert first["suite_sha256"] == hashlib.sha256(cli.DEFAULT_SUITE.read_bytes()).hexdigest()
    assert first["measurement_limits"][0] == "original_bound"
    assert runner.HISTORY_LIMIT in first["measurement_limits"] and runner.HISTORY_LIMIT in cli.LIMITS
    assert runner.HISTORY_RECOUNT_LIMIT in first["measurement_limits"]
    assert cli.main(["--recount", str(path)]) == 0
    second = json.loads(path.read_text())
    assert second == first
    other = tmp_path / "same-bank-different-bytes.json"
    other.write_bytes(cli.DEFAULT_SUITE.read_bytes() + b"\n")
    before = path.read_bytes()
    assert cli.main(["--recount", str(path), "--suite", str(other)]) == 2
    assert path.read_bytes() == before
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["code"] == "suite_digest_mismatch"


@pytest.mark.parametrize("alter", ["missing", "duplicate", "category"])
def test_recount_refuses_results_not_matching_the_frozen_bank(alter):
    report = json.loads((ROOT / "docs/probe/memory-live-20260928.json").read_text())
    if alter == "missing":
        report["results"].pop()
    elif alter == "duplicate":
        report["results"][-1] = copy.deepcopy(report["results"][0])
    else:
        report["results"][0]["category"] = "isolation"
    with pytest.raises(PayloadRejected) as exc:
        runner.recount_history(report, BANK)
    assert exc.value.code == "recount_scenarios_mismatch"


def test_nonmetric_failure_still_prevents_acceptance_when_counters_meet():
    results = [_result([], name="forget_ok"), _result(["2: raised OSError"], "isolation", "errored")]
    metrics = {**BANK["thresholds"], "history_echoes": 0}
    acceptance = runner._acceptance(metrics, results, BANK["thresholds"])
    assert all(v for k, v in acceptance["threshold_results"].items() if k != "all_scenarios_passed")
    assert acceptance["threshold_results"]["all_scenarios_passed"] is False
    assert acceptance["meets_thresholds"] is False
