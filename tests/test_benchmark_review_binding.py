"""Synthetic review records test binding, not actual model quality."""
from copy import deepcopy
import json
import pytest

from evaluation.benchmark_review import BENCHMARK_RUBRIC, review_benchmark, validate_benchmark_artifact
from evaluation.benchmark_runner import STANDARD_ARMS, BenchmarkRunner, summarize_scores
from evaluation.benchmark_metrics import evaluate_case_response
from evaluation.multi_system_review import AutomaticReviewError, build_review_package, review_binding, sha256


def reference_case():
    return {"case_id": "c1", "domain": "synthetic", "question": "ما الحمولة؟",
            "ground_truth": {"source_id": "fixture", "part": "synthetic", "locus": "1",
                             "literal_text": "الحمولة 30 طن.", "golden_facts": ["30 طن"],
                             "facets": ["الحمولة"], "glossary_terms": []}}


def synthetic_report():
    reference = reference_case()
    pages = {1: {"text": "الحمولة 30 طن.", "part": "synthetic", "locus": "1"}}
    answer = "الحمولة 30 طن [ش1]."
    metrics = evaluate_case_response(answer, reference, pages)
    outputs = {arm: {"answer": answer, "pages_by_ref": {str(k): v for k, v in pages.items()},
                     "metrics": deepcopy(metrics), "abstained": False, "error_code": None,
                     "latency_s": 0.1} for arm in STANDARD_ARMS}
    return {"schema_version": 2, "run_id": "synthetic-benchmark-1",
            "status": "pending_automated_review", "human_review_verified": False,
            "review_mode": "automated_multi_system", "measurement_limits": ["Synthetic fixture only."],
            "product_readiness": "not_assessed", "reference_cases": [reference],
            "provenance": {"suite_sha256": "a" * 64, "reference_cases_sha256": sha256([reference]),
                           "arms": list(STANDARD_ARMS)},
            "results": [{"case_id": "c1", "question": reference["question"], "arms": outputs}],
            "summary": summarize_scores({arm: [metrics] for arm in STANDARD_ARMS})}


# المحرّكُ المُراجَع من عائلة Qwen (qwen3:14b آنذاك، وqwen3.5:9b منذ ق٥٤؛ AGENTS.md §٤). فلا يكون بين المراجعين
# ولا واحدٌ من عائلته — وكان في هذا المثبِّت قبل ك٣.
ENGINE = {"provider": "ollama", "model": "qwen3:14b", "family": "qwen3",
          "digest": "c" * 64}


def approving_package(report):
    binding = review_binding(report, BENCHMARK_RUBRIC)
    reviews = []
    for family, char in [("gemma", "a"), ("llama", "b")]:
        response = {"binding": binding, "judgments": [{"criterion_id": c["id"], "verdict": "pass",
                    "reason": "Synthetic fixture only", "evidence": ["/results"]} for c in BENCHMARK_RUBRIC["criteria"]]}
        reviews.append({"identity": {"provider": "ollama", "model": family+":test", "family": family, "digest": char*64},
                        "binding": binding, "settings": {"temperature": 0, "seed": 0, "num_ctx": 32768, "num_predict": 4096},
                        "started_at": "2026-09-23T10:00:00+00:00", "elapsed_ms": 1,
                        "raw_output": json.dumps(response), "response": response, "error": None})
    return build_review_package(report, BENCHMARK_RUBRIC, reviews, engine=ENGINE)


@pytest.fixture
def reviewed():
    report = synthetic_report()
    return report, approving_package(report)


def test_review_receipt_does_not_mutate_or_release_report(reviewed):
    report, package = reviewed
    before = deepcopy(report)
    receipt = review_benchmark(report, package)
    assert receipt["status"] == "automated_review_accepted"
    assert receipt["human_review"] is False
    assert receipt["product_readiness"] == "not_assessed"
    assert report == before


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(run_id="new-run"),
    lambda r: r["results"][0]["arms"]["model_alone"].update(answer="changed"),
    lambda r: r["reference_cases"][0].update(golden="changed"),
    lambda r: r["summary"].update(extra={}),
    lambda r: r.update(status="diagnostic_only"),
    lambda r: r.update(human_review_verified=True),
    lambda r: r.update(results=[]),
])
def test_replay_or_mutation_cannot_inherit_review(reviewed, mutation):
    report, package = reviewed
    mutation(report)
    with pytest.raises(AutomaticReviewError):
        review_benchmark(report, package)


def test_legacy_counted_ids_cannot_certify(reviewed):
    report, _ = reviewed
    with pytest.raises(AutomaticReviewError):
        review_benchmark(report, {"attestation": True, "reviews": {"c1": {"verdict": "pass"}}})


def test_failed_reviewer_cannot_be_made_accepted_by_flag(reviewed):
    report, package = reviewed
    response = package["reviews"][0]["response"]
    response["judgments"][0]["verdict"] = "fail"
    package["reviews"][0]["raw_output"] = json.dumps(response)
    with pytest.raises(AutomaticReviewError):
        review_benchmark(report, package)
    checked = build_review_package(report, BENCHMARK_RUBRIC, package["reviews"], engine=ENGINE)
    assert review_benchmark(report, checked)["status"] == "automated_review_inconclusive"


@pytest.mark.parametrize("mutation,code", [
    (lambda r: r["results"][0]["arms"]["model_alone"]["metrics"].update(overall_score=0.999), "benchmark_metrics_mismatch"),
    (lambda r: r["results"][0]["arms"]["diwan_full"]["metrics"]["factuality"].update(score=0.001), "benchmark_metrics_mismatch"),
    (lambda r: r["summary"]["model_alone"].update(total_cases=100), "benchmark_summary_mismatch"),
    (lambda r: r["summary"]["diwan_full"].update(effective_overall=0.999), "benchmark_summary_mismatch"),
    (lambda r: r["summary"]["naive_rag"].update(answered_count=0), "benchmark_summary_mismatch"),
    (lambda r: r["results"][0]["arms"]["model_alone"]["pages_by_ref"]["1"].update(text="شاهد مختلف"), "benchmark_metrics_mismatch"),
])
def test_even_fresh_model_agreement_cannot_override_fabricated_metrics(mutation, code):
    report = synthetic_report()
    mutation(report)
    package = approving_package(report)  # Both models pass this exact altered report.
    assert package["status"] == "accepted"
    with pytest.raises(AutomaticReviewError, match=code):
        review_benchmark(report, package)


@pytest.mark.parametrize("mutation", [
    lambda r: r["reference_cases"].append(None),
    lambda r: r["reference_cases"].__setitem__(0, None),
    lambda r: r["reference_cases"].__setitem__(0, "reference"),
    lambda r: r["reference_cases"][0].update(ground_truth={}),
    lambda r: r["reference_cases"][0]["ground_truth"].update(golden_facts=[None]),
    lambda r: r["reference_cases"][0]["ground_truth"].update(glossary_terms=["term"]),
    lambda r: r["reference_cases"][0]["ground_truth"].update(facets=[]),
    lambda r: r["results"][0].update(question="Different question"),
    lambda r: r["results"].append(deepcopy(r["results"][0])),
    lambda r: r.update(reference_cases=[]),
])
def test_reference_coverage_and_elements_fail_closed(mutation):
    report = synthetic_report()
    mutation(report)
    with pytest.raises(AutomaticReviewError):
        validate_benchmark_artifact(report)


@pytest.mark.parametrize("mutation,code", [
    (lambda r: r["provenance"].update(reference_cases_sha256="b" * 64), "benchmark_reference_hash_mismatch"),
    (lambda r: r["provenance"].update(suite_sha256="short"), "benchmark_provenance_invalid"),
    (lambda r: r["results"][0]["arms"]["model_alone"].update(abstained="false"), "benchmark_output_invalid"),
    (lambda r: r["results"][0]["arms"]["model_alone"].update(error_code={}), "benchmark_output_invalid"),
    (lambda r: r["results"][0]["arms"]["model_alone"].update(latency_s=True), "benchmark_output_invalid"),
    (lambda r: r["results"][0]["arms"]["model_alone"].update(latency_s=float("inf")), "benchmark_output_invalid"),
    (lambda r: r["results"][0]["arms"]["model_alone"].update(latency_s=10**400), "benchmark_output_invalid"),
    (lambda r: r["results"][0]["arms"]["model_alone"]["pages_by_ref"].update({"01": {"text": "x", "part": "x", "locus": "x"}}), "benchmark_page_invalid"),
])
def test_malformed_output_or_provenance_is_a_named_refusal(mutation, code):
    report = synthetic_report()
    mutation(report)
    with pytest.raises(AutomaticReviewError, match=code):
        validate_benchmark_artifact(report)


def test_metric_failure_is_inconclusive_not_a_silent_fallback(monkeypatch):
    from evaluation import benchmark_review
    report = synthetic_report()
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic metric failure")
    monkeypatch.setattr(benchmark_review, "evaluate_case_response", broken)
    with pytest.raises(AutomaticReviewError, match="benchmark_recompute_failed"):
        validate_benchmark_artifact(report)


def test_summary_failure_is_a_named_refusal(monkeypatch):
    from evaluation import benchmark_review
    report = synthetic_report()
    def broken(*args, **kwargs):
        raise ArithmeticError("synthetic aggregate failure")
    monkeypatch.setattr(benchmark_review, "summarize_scores", broken)
    with pytest.raises(AutomaticReviewError, match="benchmark_recompute_failed"):
        validate_benchmark_artifact(report)


def test_suite_identity_is_frozen_before_runtime_file_changes(tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from evaluation import benchmark_runner

    suite_path = tmp_path / "suite.json"
    original = json.dumps({"schema_version": 1, "cases": [reference_case()]}, ensure_ascii=False).encode()
    suite_path.write_bytes(original)
    runner = BenchmarkRunner(tmp_path, SimpleNamespace(name="fixture", model="fixture"),
                             suite_path=suite_path, out_dir=tmp_path / "out")
    # Neither a caller's mutable view nor a rewritten on-disk file can change
    # the cases after their measured identity was fixed.
    runner.cases[0]["question"] = "mutated public view"

    class ChangingArm:
        def __init__(self, *args):
            pass
        def run(self, question, case):
            suite_path.write_text('{"cases":[]}', encoding="utf-8")
            assert question == reference_case()["question"]
            return {"answer": "الحمولة 30 طن.", "pages_by_ref": {}, "latency_s": 0.1,
                    "abstained": False, "error_code": None}

    for name in ("ModelAloneArm", "NaiveRagArm", "DiwanFullArm"):
        monkeypatch.setattr(benchmark_runner, name, ChangingArm)
    report = runner.run_benchmark()
    assert report["provenance"]["suite_sha256"] == hashlib.sha256(original).hexdigest()
    assert report["provenance"]["suite_sha256"] != hashlib.sha256(suite_path.read_bytes()).hexdigest()
    assert report["reference_cases"] == [reference_case()]
    validate_benchmark_artifact(report)


def test_summary_preserves_refusal_denominator_and_recomputes_reference_pages():
    report = synthetic_report()
    output = report["results"][0]["arms"]["naive_rag"]
    output.update(answer="", abstained=True, error_code="synthetic_timeout")
    output["metrics"] = evaluate_case_response("", report["reference_cases"][0], {},
                                                abstained=True, error_code="synthetic_timeout")
    report["summary"] = summarize_scores({arm: [report["results"][0]["arms"][arm]["metrics"]]
                                           for arm in STANDARD_ARMS})
    assert report["summary"]["naive_rag"]["total_cases"] == 1
    assert report["summary"]["naive_rag"]["answered_count"] == 0
    assert report["summary"]["naive_rag"]["abstained_count"] == 1
    validate_benchmark_artifact(report)
