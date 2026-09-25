"""المراجعة البشرية إدخال صريح مرتبط بتقرير، وليست حكمًا مولّدًا."""
from __future__ import annotations

import copy
import json
from html.parser import HTMLParser

import re

import pytest

from core.contracts import Response, Usage
from evaluation.capabilities import _sha, evaluate_suite
from evaluation.human_review import (ReviewError, generate_review, load_json,
                                     review_binding, summarize_review,
                                     validate_report, validate_review,
                                     write_review_html)
from tools import review_capabilities


def suite_data():
    return {"schema_version": 1, "suite_id": "review-test", "split": "development",
            "description": "بنك صوري علني",
            "cases": [{"case_id": f"case-{i}", "capability": "فهم الطلب",
                       "messages": [{"role": "user", "content": f"أجب بالعدد {i}"}],
                       "reference": str(i), "rubric": ["يطابق المطلوب"],
                       "checks": [{"kind": "exact", "value": str(i)}],
                       "critical": i == 1} for i in (1, 2)]}


class Provider:
    model = "synthetic-test-runtime"
    is_local = True

    def __init__(self, stop="complete"):
        self.calls = 0
        self.stop = stop

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        return Response(str(self.calls), Usage(10, 2), self.stop, 0,
                        provider="synthetic", model_version="synthetic")


@pytest.fixture
def sample(tmp_path):
    suite = suite_data()
    report = evaluate_suite(suite, Provider(), tmp_path / "runs")
    return suite, report


def decisions(report, verdict="pass"):
    return {**review_binding(report), "reviewer": {"identity": "مراجع تجريبي", "attestation": True},
            "decisions": [{"case_id": r["case_id"], "answer_sha256": _sha(r["answer"]),
                           "verdict": verdict, "reason": "قرار صوري داخل اختبار فقط",
                           "severity": "material" if verdict == "fail" else None,
                           "review_seconds": 7} for r in report["results"]]}


def test_full_success_is_only_local_development_judgment_and_never_readiness(sample):
    _, report = sample
    original = copy.deepcopy(report)
    review = decisions(report)
    assert validate_review(report, review) is review
    result = summarize_review(report, review)
    assert result["judgment"] == "passed"
    assert result["scope"] == "public_development_diagnostic"
    assert result["review_status"] == "complete"
    assert result["counts"] == {"pass": 2, "fail": 0, "unsure": 0}
    assert result["reviewer_identity_verified"] is False
    assert result["release_ready"] is False
    assert report == original
    assert report["manual_review"] == "not_required_q49"


@pytest.mark.parametrize("version", [1, 2, 3])
def test_historical_runner_versions_remain_readable_without_new_claims(sample, version):
    _, report = sample
    config = report["metadata"]["manifest"]["config"]
    config["runner_version"] = version
    del config["execution_backend"]
    if version < 3:
        del config["execution_host"]
    if version == 1:
        del config["quarantine_quoted_material"]
        for result in report["results"]:
            del result["quarantined_directives"]
    report["metadata"]["manifest"]["config_sha256"] = _sha(config)
    report["manual_review"] = report["summary"]["manual_review"] = "pending"
    for result in report["results"]:
        result["manual_review"] = "pending"
    assert validate_report(report) is report
    assert report["release_ready"] is False


@pytest.fixture
def sandbox_report(tmp_path, monkeypatch):
    """Synthetic daemon result: validates schema, not live containment."""
    from types import SimpleNamespace
    from core import sandbox
    from core.execution import ExecutionResult
    receipt = {"image_id": "sha256:" + "a" * 64, "lock_sha256": "b" * 64,
               "python_version": "3.14.7"}
    def run(argv, **_):
        # الخُلفيّةُ المصطنعة تبلغ سطرَ الحكم الذي حمله السكربت (ك١٣) فتُحاكي مدقّقًا أتمّ.
        marker = re.search(r"__DIWAN_VERDICT__ [0-9a-f]{32} PASS", argv[-1]).group(0)
        return ExecutionResult(0, "\n" + marker + "\n", "", "docker:" + "c" * 64)
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", SimpleNamespace(receipt=receipt, run=run))
    suite = suite_data()
    for case in suite["cases"]:
        case["checks"] = [{"kind": "python_sandbox", "value": "assert 1 == 1"}]
    return evaluate_suite(suite, Provider(), tmp_path)


def test_v4_sandbox_contract_is_valid_without_human_requirement(sandbox_report):
    assert validate_report(sandbox_report) is sandbox_report
    assert sandbox_report["manual_review"] == "not_required_q49"


def test_v3_declared_host_report_is_historical_only(sandbox_report):
    config = sandbox_report["metadata"]["manifest"]["config"]
    config["runner_version"] = 3
    del config["execution_backend"]
    config["execution_host"] = "historical-declared-host"
    sandbox_report["metadata"]["manifest"]["config_sha256"] = _sha(config)
    for result in sandbox_report["results"]:
        result["checks"][0]["boundary"] = "historical-declared-host"
    assert validate_report(sandbox_report) is sandbox_report
    assert sandbox_report["release_ready"] is False


@pytest.mark.parametrize("boundary", [None, "historical-declared-host", "docker:" + "c" * 64,
    "docker:sha256:" + "a" * 64 + "@short", "docker:sha256:" + "b" * 64 + "@" + "c" * 64])
def test_v4_rejects_missing_legacy_or_mismatched_boundary(sandbox_report, boundary):
    sandbox_report["results"][0]["checks"][0]["boundary"] = boundary
    with pytest.raises(ReviewError, match="sandbox_boundary_mismatch"):
        validate_report(sandbox_report)


@pytest.mark.parametrize("change,code", [
    ({"execution_backend": None}, "sandbox_configuration_mismatch"),
    ({"execution_host": "owner-env-claim"}, "sandbox_configuration_mismatch"),
    ({"runner_version": 3}, "schema_fields"),
])
def test_v4_rejects_configuration_contradictions(sandbox_report, change, code):
    config = sandbox_report["metadata"]["manifest"]["config"]
    config.update(change)
    sandbox_report["metadata"]["manifest"]["config_sha256"] = _sha(config)
    with pytest.raises(ReviewError, match=code):
        validate_report(sandbox_report)


@pytest.mark.parametrize("change", [{"backend": "host"}, {"image_id": "python:latest"},
                                   {"snapshot_files": ["secret.txt"]}, {"python_version": "2.7.18"}])
def test_v4_rejects_unsafe_backend_metadata(sandbox_report, change):
    config = sandbox_report["metadata"]["manifest"]["config"]
    config["execution_backend"].update(change)
    sandbox_report["metadata"]["manifest"]["config_sha256"] = _sha(config)
    with pytest.raises(ReviewError, match="sandbox_configuration_invalid"):
        validate_report(sandbox_report)


@pytest.mark.parametrize("passed,exit_code,error_code", [
    (True, 7, None),                 # الحكمُ بلغ ثم خرجت العمليةُ بغير صفر لسببٍ جانبيّ: ناجح
    (False, 0, "verdict_missing"),   # خرجت بالصفر قبل بلوغ الحكم: راسب
    (False, 7, "exit_7"),
])
def test_v4_accepts_a_check_whose_error_code_names_its_verdict(sandbox_report, passed, exit_code, error_code):
    result = sandbox_report["results"][0]
    result["checks"][0].update(exit_code=exit_code, passed=passed)
    result["checks"][0].pop("error_code", None)
    if error_code is not None:
        result["checks"][0]["error_code"] = error_code
    if not passed:
        result["automatic_pass"] = False
        sandbox_report["summary"].update(automatic_passes=1, automatic_failures=1)
    assert validate_report(sandbox_report) is sandbox_report


@pytest.mark.parametrize("passed,exit_code,error_code", [
    (True, 0, "verdict_missing"),    # نجاحٌ برمز خطأ
    (True, 7, "exit_7"),
    (False, 0, None),                # رسوبٌ بلا سبب
    (False, 0, "exit_0"),
    (False, 7, "verdict_missing"),   # السببُ لا يطابق الخروج
    (False, 7, None),
    (False, 7, "exit_8"),
])
def test_v4_rejects_a_check_whose_error_code_contradicts_its_verdict(sandbox_report, passed, exit_code, error_code):
    """الاتّساقُ بين الحكم والسبب لا بين الحكم ورمز الخروج (ك١٣)."""
    result = sandbox_report["results"][0]
    result["checks"][0].update(exit_code=exit_code, passed=passed)
    result["checks"][0].pop("error_code", None)
    if error_code is not None:
        result["checks"][0]["error_code"] = error_code
    if not passed:
        result["automatic_pass"] = False
        sandbox_report["summary"].update(automatic_passes=1, automatic_failures=1)
    with pytest.raises(ReviewError, match="sandbox_result_inconsistent"):
        validate_report(sandbox_report)


def test_v4_records_verified_nonzero_exit_as_model_failure(sandbox_report):
    result = sandbox_report["results"][0]
    result["checks"][0].update(exit_code=7, passed=False, error_code="exit_7")
    result["automatic_pass"] = False
    sandbox_report["summary"].update(automatic_passes=1, automatic_failures=1)
    assert validate_report(sandbox_report) is sandbox_report


def test_v4_does_not_accept_measured_check_without_configured_backend(sandbox_report):
    config = sandbox_report["metadata"]["manifest"]["config"]
    config.update(execution_backend=None, execution_host=None)
    sandbox_report["metadata"]["manifest"]["config_sha256"] = _sha(config)
    with pytest.raises(ReviewError, match="sandbox_boundary_mismatch"):
        validate_report(sandbox_report)


def test_v4_requires_backend_field_even_when_unconfigured(sample):
    _, report = sample
    config = report["metadata"]["manifest"]["config"]
    del config["execution_backend"]
    report["metadata"]["manifest"]["config_sha256"] = _sha(config)
    with pytest.raises(ReviewError, match="schema_fields"):
        validate_report(report)


def test_partial_all_passing_and_unsure_never_produce_global_pass(sample):
    _, report = sample
    review = decisions(report)
    review["decisions"].pop()
    summary = summarize_review(report, review)
    assert summary["review_status"] == "partial"
    assert summary["judgment"] == "incomplete"
    review = decisions(report, "unsure")
    assert summarize_review(report, review)["judgment"] == "unresolved"


def test_critical_case_may_pass_but_failed_critical_case_and_critical_severity_are_visible(sample):
    _, report = sample
    review = decisions(report)
    assert summarize_review(report, review)["critical_findings"] == []
    first, second = review["decisions"]
    first.update(verdict="fail", severity="minor")
    second.update(verdict="fail", severity="critical")
    summary = summarize_review(report, review)
    assert summary["judgment"] == "failed"
    assert [(x["case_id"], x["critical_case"], x["severity"]) for x in summary["critical_findings"]] == [
        ("case-1", True, "minor"), ("case-2", False, "critical")]
    assert summary["release_ready"] is False


@pytest.mark.parametrize("field,value", [("schema_version", True), ("kind", "human-approved"),
                                        ("suite_id", "other"), ("run_id", "other"),
                                        ("suite_sha256", "0" * 64), ("config_sha256", "0" * 64),
                                        ("report_sha256", "0" * 64), ("release_ready", 0),
                                        ("release_ready", True)])
def test_review_bindings_are_typed_and_exact(sample, field, value):
    _, report = sample
    review = decisions(report)
    review[field] = value
    with pytest.raises(ReviewError, match="review_binding_mismatch"):
        validate_review(report, review)


@pytest.mark.parametrize("field,value,code", [
    ("verdict", "approved", "verdict_invalid"),
    ("reason", " \n", "text_required"),
    ("review_seconds", True, "integer_invalid"),
    ("review_seconds", 1.0, "integer_invalid"),
    ("review_seconds", 0, "integer_invalid"),
    ("review_seconds", 2**53, "integer_invalid"),
    ("case_id", "not-present", "unknown_case"),
    ("answer_sha256", "0" * 64, "answer_hash_mismatch"),
    ("severity", "critical", "severity_invalid"),
])
def test_invalid_decisions_are_rejected(sample, field, value, code):
    _, report = sample
    review = decisions(report)
    review["decisions"][0][field] = value
    with pytest.raises(ReviewError, match=code):
        validate_review(report, review)


@pytest.mark.parametrize("mutation", ["review", "reviewer", "decision", "missing", "duplicate", "empty", "blank_identity", "unattested", "integer_attestation", "fail_without_severity"])
def test_closed_review_schema_and_explicit_attestation(sample, mutation):
    _, report = sample
    review = decisions(report)
    if mutation == "review":
        review["human_approved"] = True
    elif mutation == "reviewer":
        review["reviewer"]["authenticated"] = True
    elif mutation == "decision":
        review["decisions"][0]["extra"] = "ignored?"
    elif mutation == "missing":
        del review["decisions"][0]["reason"]
    elif mutation == "duplicate":
        review["decisions"][1] = copy.deepcopy(review["decisions"][0])
    elif mutation == "empty":
        review["decisions"] = []
    elif mutation == "blank_identity":
        review["reviewer"]["identity"] = "\t"
    elif mutation == "unattested":
        review["reviewer"]["attestation"] = False
    elif mutation == "integer_attestation":
        review["reviewer"]["attestation"] = 1
    else:
        review["decisions"][0]["verdict"] = "fail"
    with pytest.raises(ReviewError):
        validate_review(report, review)


def test_review_cannot_be_reused_for_changed_answer_or_report(sample):
    _, report = sample
    review = decisions(report)
    changed = copy.deepcopy(report)
    changed["results"][0]["answer"] = "نص معدل"
    with pytest.raises(ReviewError, match="review_binding_mismatch"):
        validate_review(changed, review)
    # Even a rewritten report binding must not mask an old per-answer decision.
    review["report_sha256"] = _sha(changed)
    with pytest.raises(ReviewError, match="answer_hash_mismatch"):
        validate_review(changed, review)
    changed = copy.deepcopy(report)
    changed["results"][0]["elapsed_ms"] += 1
    with pytest.raises(ReviewError, match="review_binding_mismatch"):
        validate_review(changed, decisions(report))


@pytest.mark.parametrize("stop", ["max_output", "error", "deadline", "refused"])
def test_failed_or_truncated_execution_cannot_receive_passing_decision(tmp_path, stop):
    report = evaluate_suite(suite_data(), Provider(stop), tmp_path / "runs")
    review = decisions(report)
    with pytest.raises(ReviewError, match="incomplete_cannot_pass"):
        validate_review(report, review)
    assert summarize_review(report, decisions(report, "fail"))["judgment"] == "failed"


@pytest.mark.parametrize("mutation", ["duplicate_case", "wrong_summary", "bool_count", "wrong_config", "source_ready", "extra", "missing_case"])
def test_corrupt_source_report_is_not_silently_reviewed(sample, mutation):
    _, report = sample
    if mutation == "duplicate_case":
        report["results"][1] = copy.deepcopy(report["results"][0])
    elif mutation == "wrong_summary":
        report["summary"]["automatic_passes"] = 0
    elif mutation == "bool_count":
        report["summary"]["execution_errors"] = False
    elif mutation == "wrong_config":
        report["metadata"]["manifest"]["config"]["max_output"] += 1
    elif mutation == "source_ready":
        report["release_ready"] = True
    elif mutation == "extra":
        report["extra"] = "ignore?"
    else:
        report["results"].pop()
    with pytest.raises(ReviewError):
        validate_report(report)


def test_prepare_checks_suite_reference_rubric_and_dialogue_binding(sample):
    suite, report = sample
    for field in ("reference", "rubric", "messages"):
        changed = copy.deepcopy(suite)
        changed["cases"][0][field] = ({"reference": "معدل", "rubric": ["معدل"],
                                       "messages": [{"role": "user", "content": "معدل"}]}[field])
        with pytest.raises(ReviewError, match="suite_hash_mismatch"):
            generate_review(report, changed)
    report["results"][0]["reference"] = "إجابة مرجعية معدلة"
    with pytest.raises(ReviewError, match="suite_case_mismatch"):
        generate_review(report, suite)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attrs = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend(attrs)


def test_page_escapes_untrusted_html_and_is_offline(tmp_path):
    injection = '</script><script>alert("bad")</script><img src="https://example.invalid/pixel" onerror="bad()">\u2028\u2029&'
    suite = suite_data()
    suite["cases"][0].update(reference=injection, rubric=[injection], capability=injection)
    suite["cases"][0]["messages"][0]["content"] = injection
    report = evaluate_suite(suite, Provider(), tmp_path / "runs")
    report["results"][0]["answer"] = injection
    content = generate_review(report, suite)
    parser = PageParser()
    parser.feed(content)
    assert parser.tags.count("script") == 1
    assert "img" not in parser.tags and "iframe" not in parser.tags
    assert not any(k in ("src", "href", "onerror", "onclick") for k, v in parser.attrs)
    assert "&lt;/script&gt;" in content
    assert "connect-src 'none'" in content
    assert "textContent" in content and "innerHTML" not in content
    assert "لم أراجع بعد" in content
    assert "إقرار محلي غير موثق" in content
    assert "'\\n'" in content
    assert "attestation: true" in content
    assert report["metadata"]["runtime_model"] not in content
    assert "runtime_model_version" not in content and '"metadata"' not in content


def test_script_payload_restricts_html_and_unicode_separators():
    from evaluation.human_review import _script_json
    value = {"text": "</script>&\u2028\u2029"}
    encoded = _script_json(value)
    assert "<" not in encoded and ">" not in encoded and "&" not in encoded
    assert "\u2028" not in encoded and "\u2029" not in encoded
    assert json.loads(encoded) == value


def test_page_output_cannot_escape_follow_symlink_or_overwrite(sample, tmp_path):
    suite, report = sample
    root = tmp_path / "var/reviews"
    with pytest.raises(ReviewError, match="unsafe_output_path"):
        write_review_html(report, suite, tmp_path / "outside", root=root)
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ReviewError, match="unsafe_output_path"):
        write_review_html(report, suite, root / "link/nested", root=root)
    path = write_review_html(report, suite, root / "real", root=root)
    before = path.read_bytes()
    with pytest.raises(ReviewError, match="output_write_failed"):
        write_review_html(report, suite, root / "real", root=root)
    assert path.read_bytes() == before
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '[] broken'])
def test_loader_rejects_ambiguous_or_invalid_json(tmp_path, text):
    path = tmp_path / "review.json"
    path.write_text(text)
    with pytest.raises(ReviewError):
        load_json(path)


def test_cli_prepare_then_import_changes_no_raw_report(sample, tmp_path, monkeypatch, capsys):
    suite, report = sample
    monkeypatch.setattr(review_capabilities, "ROOT", tmp_path)
    source = tmp_path / "report.json"
    bank = tmp_path / "suite.json"
    review_path = tmp_path / "explicit-test-review.json"
    source.write_text(json.dumps(report))
    bank.write_text(json.dumps(suite))
    review_path.write_text(json.dumps(decisions(report)))
    before = source.read_bytes()
    output = tmp_path / "var/reviews/first"
    assert review_capabilities.main(["prepare", "--report", str(source), "--suite", str(bank),
                                     "--output-dir", str(output)]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["manual_review"] == "pending"
    assert prepared["release_ready"] is False
    assert (output / "review.html").is_file()
    assert review_capabilities.main(["import", "--report", str(source), "--review", str(review_path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["judgment"] == "passed" and summary["release_ready"] is False
    assert source.read_bytes() == before
    review_path.write_text('{"schema_version":true}')
    assert review_capabilities.main(["import", "--report", str(source), "--review", str(review_path)]) == 2
    assert json.loads(capsys.readouterr().out)["release_ready"] is False
