"""Automatic review of a frozen benchmark, separate from producing the report.

An accepted review covers this development run only, never release readiness.
Review hashes are content integrity, not cryptographic reviewer authentication.
"""
from __future__ import annotations

import math
import re

from evaluation.multi_system_review import (AutomaticReviewError, canonical_bytes,
                                          sha256, validate_review_package)
from evaluation.benchmark_metrics import evaluate_case_response
from evaluation.benchmark_runner import STANDARD_ARMS, summarize_scores

BENCHMARK_RUBRIC = {
    "schema_version": 1,
    "rubric_id": "m14-development-automatic-v1",
    "criteria": [
        {"id": "answers_and_sources", "instruction": "Compare every answer in every arm with its reference case and cited pages. Report factual contradictions, unsupported attribution, misleading refusal or missing reference evidence. Pass only if the report's qualitative claims follow from these outputs; do not reward Diwan by name."},
        {"id": "comparison_limits", "instruction": "Check that arms cover identical cases, references were not passed to the model as answers, limitations are explicit, and lexical metric scores are not presented as entailment or product readiness. If the artifact cannot establish an aspect, say inconclusive and identify the missing evidence."},
    ],
    "deterministic_rules": [
        {"id": "pending_run", "pointer": "/status", "operator": "equals", "expected": "pending_automated_review"},
        {"id": "no_human_claim", "pointer": "/human_review_verified", "operator": "equals", "expected": False},
        {"id": "no_release_claim", "pointer": "/product_readiness", "operator": "equals", "expected": "not_assessed"},
        {"id": "actual_outputs", "pointer": "/results", "operator": "nonempty", "expected": None},
    ],
}


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise AutomaticReviewError(code)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_reference(reference: dict) -> None:
    _require(isinstance(reference, dict) and set(reference) == {
        "case_id", "question", "domain", "ground_truth"}, "benchmark_reference_invalid")
    _require(all(_text(reference[k]) for k in ("case_id", "question", "domain")),
             "benchmark_reference_invalid")
    truth = reference["ground_truth"]
    _require(isinstance(truth, dict) and set(truth) == {
        "source_id", "part", "locus", "literal_text", "golden_facts", "facets", "glossary_terms"},
        "benchmark_reference_invalid")
    _require(all(_text(truth[k]) for k in ("source_id", "part", "locus", "literal_text")),
             "benchmark_reference_invalid")
    for key in ("golden_facts", "facets"):
        _require(isinstance(truth[key], list) and bool(truth[key]) and all(_text(t) for t in truth[key]),
                 "benchmark_reference_invalid")
    glossary = truth["glossary_terms"]
    _require(isinstance(glossary, list), "benchmark_reference_invalid")
    for term in glossary:
        _require(isinstance(term, dict) and set(term) == {"ar", "foreign"}
                 and all(_text(term[k]) for k in ("ar", "foreign")), "benchmark_reference_invalid")


def validate_benchmark_artifact(report: dict) -> None:
    _require(isinstance(report, dict), "benchmark_not_reviewable")
    if (type(report.get("schema_version")) is not int or report.get("schema_version") != 2
            or report.get("status") != "pending_automated_review"
            or report.get("human_review_verified") is not False
            or report.get("product_readiness") != "not_assessed"
            or report.get("review_mode") != "automated_multi_system"):
        raise AutomaticReviewError("benchmark_not_reviewable")
    _require(_text(report.get("run_id")), "benchmark_run_invalid")
    _require(isinstance(report.get("measurement_limits"), list)
             and bool(report["measurement_limits"]) and all(_text(t) for t in report["measurement_limits"]),
             "benchmark_limits_missing")
    results, references = report.get("results"), report.get("reference_cases")
    if (not isinstance(results, list) or not results or not isinstance(references, list)
            or len(results) != len(references)):
        raise AutomaticReviewError("benchmark_cases_invalid")
    for reference in references:
        _validate_reference(reference)
    _require(all(isinstance(case, dict) and set(case) == {"case_id", "question", "arms"}
                 and _text(case["case_id"]) and _text(case["question"]) for case in results),
             "benchmark_cases_invalid")
    ids = [case["case_id"] for case in results]
    ref_ids = [case["case_id"] for case in references]
    if len(set(ids)) != len(ids) or ids != ref_ids:
        raise AutomaticReviewError("benchmark_case_mismatch")
    provenance = report.get("provenance")
    _require(isinstance(provenance, dict), "benchmark_provenance_invalid")
    suite_hash = provenance.get("suite_sha256")
    _require(isinstance(suite_hash, str) and bool(re.fullmatch(r"[0-9a-f]{64}", suite_hash)),
             "benchmark_provenance_invalid")
    _require(provenance.get("reference_cases_sha256") == sha256(references), "benchmark_reference_hash_mismatch")
    _require(isinstance(provenance.get("arms"), list) and len(provenance["arms"]) == len(STANDARD_ARMS)
             and all(isinstance(arm, str) for arm in provenance["arms"])
             and set(provenance["arms"]) == set(STANDARD_ARMS), "benchmark_arms_invalid")
    if not isinstance(report.get("summary"), dict) or set(report["summary"]) != set(STANDARD_ARMS):
        raise AutomaticReviewError("benchmark_arms_invalid")
    recomputed_scores = {arm: [] for arm in STANDARD_ARMS}
    for result, reference in zip(results, references):
        _require(result["question"] == reference["question"], "benchmark_question_mismatch")
        if not isinstance(result["arms"], dict) or set(result["arms"]) != set(STANDARD_ARMS):
            raise AutomaticReviewError("benchmark_arms_invalid")
        for arm, output in result["arms"].items():
            _require(isinstance(output, dict) and set(output) == {
                "answer", "pages_by_ref", "latency_s", "error_code", "abstained", "metrics"},
                "benchmark_output_invalid")
            _require(isinstance(output["answer"], str) and isinstance(output["pages_by_ref"], dict)
                     and isinstance(output["metrics"], dict) and type(output["abstained"]) is bool
                     and (output["error_code"] is None or _text(output["error_code"]))
                     and type(output["latency_s"]) in (int, float)
                     and 0 <= output["latency_s"] <= 2**53 - 1 and math.isfinite(output["latency_s"]),
                     "benchmark_output_invalid")
            pages = {}
            for key, page in output["pages_by_ref"].items():
                _require(isinstance(key, str) and bool(re.fullmatch(r"[1-9][0-9]{0,8}", key))
                         and isinstance(page, dict) and set(page) == {"text", "part", "locus"}
                         and all(isinstance(page[k], str) for k in ("text", "part", "locus")),
                         "benchmark_page_invalid")
                pages[int(key)] = page
            try:
                metrics = evaluate_case_response(output["answer"], reference, pages,
                                                 abstained=output["abstained"], error_code=output["error_code"])
                matches = canonical_bytes(metrics) == canonical_bytes(output["metrics"])
            except Exception as exc:
                raise AutomaticReviewError("benchmark_recompute_failed") from exc
            _require(matches, "benchmark_metrics_mismatch")
            recomputed_scores[arm].append(metrics)
    try:
        expected_summary = summarize_scores(recomputed_scores)
        summary_matches = canonical_bytes(report["summary"]) == canonical_bytes(expected_summary)
    except Exception as exc:
        raise AutomaticReviewError("benchmark_recompute_failed") from exc
    _require(summary_matches, "benchmark_summary_mismatch")


def review_benchmark(report: dict, package: dict) -> dict:
    validate_benchmark_artifact(report)
    checked = validate_review_package(package, report, BENCHMARK_RUBRIC, run_id=report["run_id"])
    # Separate receipt: never mutate the artifact and invalidate its binding.
    return {"schema_version": 1, "kind": "benchmark_automatic_review_receipt",
            "run_id": report["run_id"], "binding": checked["binding"],
            "status": "automated_review_" + checked["status"],
            "human_review": False, "product_readiness": "not_assessed",
            "review_package": checked,
            "scope": "This frozen development benchmark only; not held-out acceptance.",
            "authenticity": "Content bound; local collection provenance must be retained separately."}
