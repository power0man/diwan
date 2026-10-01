"""حراس مقام قياس المحرّكات وإعادة توليد مقارنتَي ك٢ وك١٢ (#186)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from core.contracts import Response, Usage
from evaluation.capabilities import CapabilityError
from providers.base import ProviderError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import compare_engines  # noqa: E402
import measure_engine  # noqa: E402


class FakeProvider:
    model = "synthetic-measure-provider"
    is_local = True

    def __init__(self, responses):
        self.responses = list(responses)

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        if not self.responses:
            raise AssertionError("unexpected synthetic provider call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(text: str) -> Response:
    return Response(text, Usage(3, 1), "complete", 0,
                    provider="synthetic", model_version="fixture")


def _suite(path: Path, suite_id: str, cases: int) -> Path:
    value = {
        "schema_version": 1,
        "suite_id": suite_id,
        "split": "development",
        "description": "بيانات مصطنعة لحارس المقام",
        "cases": [
            {"case_id": f"case_{index}", "capability": "synthetic",
             "messages": [{"role": "user", "content": f"الحالة {index}"}],
             "reference": "صحيح", "rubric": ["يطابق النص"],
             "checks": [{"kind": "exact", "value": "صحيح"}], "critical": False}
            for index in range(cases)
        ],
    }
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_failed_suite_and_execution_error_stay_offered_and_never_become_success(tmp_path, monkeypatch):
    live = _suite(tmp_path / "a-live.json", "sample_tier_a__live", 3)
    fallen = _suite(tmp_path / "b-fallen.json", "sample_tier_a__fallen", 2)
    provider = FakeProvider([
        _response("صحيح"),
        ProviderError("timeout", "انتهت المهلة", retryable=False),
        _response("صحيح."),
    ])
    real_evaluate = measure_engine.evaluate_suite

    def evaluate(suite, *args, **kwargs):
        if suite["suite_id"].endswith("__fallen"):
            raise CapabilityError("suite", "synthetic_suite_failure", "سقوط مصطنع")
        return real_evaluate(suite, *args, **kwargs)

    monkeypatch.setattr(measure_engine, "evaluate_suite", evaluate)
    report = measure_engine.measure(
        [live, fallen], "synthetic", tmp_path / "runs", max_output=32,
        deadline_s=10, model_version="fixture", provider=provider,
    )

    assert report["failures"] == [{"suite_id": "sample_tier_a__fallen",
                                    "tier": "tier_a", "offered": 2,
                                    "code": "synthetic_suite_failure"}]
    assert report["overall"] == {
        "offered": 5, "judged": 2, "passes": 1,
        "pass_rate_offered": 0.2, "pass_rate_judged": 0.5, "coverage": 0.4,
        "without_checks": 0, "execution_errors": 1, "lost_to_failed_suite": 2,
    }
    assert report["exact_readings"]["strict"]["passes"] == 1
    assert report["exact_readings"]["lenient"]["passes"] == 2
    assert report["exact_readings"]["lost_to_trailing_punctuation"] == 1
    for values in [report["overall"], *report["by_tier"].values()]:
        assert values["pass_rate_offered"] <= values["pass_rate_judged"]


def test_require_same_suites_rejects_different_names_and_sizes(tmp_path, monkeypatch, capsys):
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({
        "by_suite": [{"suite_id": "sample_tier_a__same", "offered": 1}],
        "failures": [],
    }), encoding="utf-8")
    monkeypatch.setattr(measure_engine, "measure", lambda *a, **k: {
        "model": "synthetic", "overall": {"offered": 0, "judged": 0, "passes": 0,
        "pass_rate_offered": None, "pass_rate_judged": None, "coverage": None,
        "without_checks": 0, "execution_errors": 0, "lost_to_failed_suite": 0},
        "suites_failed": 0, "elapsed_s": 0, "measurement_limits": [],
    })

    cases = (("sample_tier_a__other", 1, "suite_set_differs"),
             ("sample_tier_a__same", 2, "suite_size_differs"))
    for index, (suite_id, size, expected) in enumerate(cases):
        directory = tmp_path / f"suites-{index}"
        directory.mkdir()
        _suite(directory / "suite.json", suite_id, size)
        code = measure_engine.main([
            str(directory), "--model", "synthetic", "--out", str(tmp_path / f"out-{index}.json"),
            "--require-same-suites", str(prior),
        ])
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert code == 2 and payload["error"] == expected


def test_public_samples_regenerate_both_summaries_without_numeric_drift():
    k2, k12 = compare_engines.build_summaries(ROOT / "docs/probe")
    committed_k2 = json.loads((ROOT / "docs/probe" / compare_engines.K2_OUT).read_text(encoding="utf-8"))
    committed_k12 = json.loads((ROOT / "docs/probe" / compare_engines.K12_OUT).read_text(encoding="utf-8"))
    assert (k2, k12) == (committed_k2, committed_k12)
    assert k2["tool"] == k12["tool"] == "tools/compare_engines.py"
    assert k2["command"] == k12["command"] == compare_engines.COMMAND
    assert {model: values["pass_rate_offered"] for model, values in k2["engines"].items()} == {
        "qwen3:14b": 0.4814, "qwen3.5:9b": 0.5426, "gemma4:latest": 0.4521,
    }
    assert k12["results_offered"] == {
        "qwen3:14b": 0.481, "qwen3.5:9b": 0.543,
        "gemma4": 0.452, "command-r7b-arabic:7b": 0.295,
    }
    expected_exact = {
        "qwen3:14b": (0.519, 0.559, 14),
        "qwen3.5:9b": (0.588, 0.588, 0),
        "gemma4:latest": (0.504, 0.534, 10),
        "command-r7b-arabic:7b": (0.316, 0.368, 18),
    }
    for model, (strict, lenient, lost) in expected_exact.items():
        reading = k12["exact_readings"][model]
        assert (reading["strict"], reading["lenient"], reading["lost_to_punctuation"]) == (strict, lenient, lost)
        if model in k2["engines"]:
            assert k2["engines"][model]["exact_readings"] == reading
