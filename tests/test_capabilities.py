"""تشخيص القدرات بمزوّد صوري فقط: فصل المرجع والخصوصية وعدم التكرار."""
from __future__ import annotations

import copy
import json
import re
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from core.contracts import Response, Usage
from core.ledger import Ledger
from evaluation.capabilities import (CapabilityError, SYSTEM, evaluate_suite,
                                     load_suite, validate_suite)
from providers.base import ProviderError


def suite():
    return {"schema_version": 1, "suite_id": "arabic-test", "split": "development",
            "description": "تشخيص جزئي ومراجعة بشرية لازمة",
            "cases": [{"case_id": "dialogue", "capability": "السياق",
                       "messages": [{"role": "user", "content": "سمّ الكتاب أملًا"},
                                    {"role": "assistant", "content": "سأسميه أملًا"},
                                    {"role": "user", "content": "ما الاسم؟"}],
                       "reference": "REFERENCE_DO_NOT_SEND",
                       "rubric": ["RUBRIC_DO_NOT_SEND"],
                       "checks": [{"kind": "exact", "value": "أملًا"}],
                       "critical": True}]}


class FakeProvider:
    model = "runtime-private-model"
    is_local = True

    def __init__(self, responses, *, estimate=0):
        self.responses = list(responses)
        self.requests = []
        self.estimates = 0
        self.calls = 0
        self.estimate = estimate

    def estimate_micros(self, request):
        self.estimates += 1
        return self.estimate

    def complete(self, request):
        self.calls += 1
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected model call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def answer(text="أملًا", stop="complete"):
    return Response(text, Usage(12, 4), stop, 0, provider="fake", model_version="1")


def test_only_fixed_system_and_dialogue_reach_provider(tmp_path):
    data = suite()
    data["cases"][0]["checks"] = [{"kind": "excludes", "value": "CHECK_DO_NOT_SEND"}]
    provider = FakeProvider([answer()])
    report = evaluate_suite(data, provider, tmp_path / "runs")
    request = provider.requests[0]
    assert request.data_policy == "local_only"
    assert [(m.role, m.content) for m in request.messages] == [
        ("system", SYSTEM), *[(m["role"], m["content"])
                              for m in data["cases"][0]["messages"]]]
    wire = "\n".join(m.content for m in request.messages)
    for forbidden in ("REFERENCE_DO_NOT_SEND", "RUBRIC_DO_NOT_SEND", "CHECK_DO_NOT_SEND"):
        assert forbidden not in wire
    assert report["metadata"]["runtime_model"] == provider.model
    assert report["results"][0]["usage"] == {"input_tokens": 12, "output_tokens": 4}
    assert report["results"][0]["cost_micros"] == 0
    assert type(report["results"][0]["elapsed_ms"]) is int
    assert "throughput" in report["metadata"]["elapsed_semantics"]


@pytest.mark.parametrize("where, field, value, code", [
    ("suite", "schema_version", True, "schema_version_invalid"),
    ("suite", "extra", "x", "schema_fields"),
    ("suite", "split", "holdout", "development_only"),
    ("suite", "split", False, "development_only"),
    ("case", "critical", 1, "critical_type"),
    ("case", "extra", "x", "schema_fields"),
    ("case", "messages", [{"role": "system", "content": "تدخل"}], "role_invalid"),
    ("case", "checks", [{"kind": "exact", "value": True}], "text_required"),
    # القائمةُ مرجعٌ صالح: _json_equal يقارنها، وحالاتُ الاستخراج تحتاجها.
    # والمرفوضُ القيمةُ المفردة، فللنصّ والرقم فحوصُهما (exact وcontains).
    ("case", "checks", [{"kind": "json_equals", "value": "نصّ"}], "json_container_required"),
    ("case", "checks", [{"kind": "unknown", "value": "x"}], "check_kind_invalid"),
    ("case", "checks", [{"kind": "exact", "value": "x", "x": 1}], "schema_fields"),
])
def test_closed_schema_and_types(where, field, value, code):
    data = suite()
    (data if where == "suite" else data["cases"][0])[field] = value
    with pytest.raises(CapabilityError) as exc:
        validate_suite(data)
    assert exc.value.code == code


def test_duplicate_ids_and_json_keys_rejected(tmp_path):
    data = suite()
    data["cases"].append(copy.deepcopy(data["cases"][0]))
    with pytest.raises(CapabilityError, match="case_id_duplicate"):
        validate_suite(data)
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(CapabilityError, match="duplicate_json_key"):
        load_suite(path)


def test_replay_preserves_ledger_and_makes_no_provider_calls(tmp_path):
    run_root = tmp_path / "runs"
    first = evaluate_suite(suite(), FakeProvider([answer()]), run_root, run_id="same")
    ledger = run_root / "same/calls.jsonl"
    before = ledger.read_bytes()
    provider = FakeProvider([])
    replay = evaluate_suite(suite(), provider, run_root, run_id="same")
    assert provider.calls == provider.estimates == 0
    assert replay["results"][0]["replayed"] is True
    assert replay["results"][0]["answer"] == first["results"][0]["answer"]
    assert ledger.read_bytes() == before
    assert len(list((run_root / "same").glob("report-*.json"))) == 2


@pytest.mark.parametrize("changed", ["model", "version", "max_output", "deadline", "dialogue", "reference"])
def test_same_run_id_cannot_reuse_mismatched_config(tmp_path, changed):
    run_root = tmp_path / "runs"
    evaluate_suite(suite(), FakeProvider([answer()]), run_root, run_id="same")
    before = (run_root / "same/calls.jsonl").read_bytes()
    data, provider, options = suite(), FakeProvider([]), {}
    if changed == "model":
        provider.model = "different-model"
    elif changed == "version":
        options["model_version"] = "different-weights-digest"
    elif changed == "max_output":
        options["max_output"] = 801
    elif changed == "deadline":
        options["deadline_s"] = 241
    elif changed == "dialogue":
        data["cases"][0]["messages"][-1]["content"] = "طلب مختلف"
    else:
        data["cases"][0]["reference"] = "مرجع مختلف"
    with pytest.raises(CapabilityError, match="run_config_conflict"):
        evaluate_suite(data, provider, run_root, run_id="same", **options)
    assert provider.calls == provider.estimates == 0
    assert (run_root / "same/calls.jsonl").read_bytes() == before


def test_privacy_is_enforced_before_remote_provider_or_estimate(tmp_path):
    provider = FakeProvider([answer()])
    provider.is_local = False
    report = evaluate_suite(suite(), provider, tmp_path / "runs")
    assert provider.calls == provider.estimates == 0
    assert report["results"][0]["error_code"] == "policy_requires_local"
    assert not report["summary"]["collection_complete"]


@pytest.mark.parametrize("response, code", [
    (answer(stop="max_output"), "response_max_output"),
    (answer(stop="error"), "response_error"),
    (answer(stop="deadline"), "response_deadline"),
    (ProviderError("timeout", "تعذر", retryable=False), "timeout"),
    (TypeError("runtime-private-model internal failure"), "execution_failed"),
])
def test_errors_and_truncation_never_pass_checks(tmp_path, response, code):
    report = evaluate_suite(suite(), FakeProvider([response]), tmp_path / "runs")
    result = report["results"][0]
    assert result["error_code"] == code
    assert result["automatic_pass"] is False and result["checks_status"] == "not_run"
    assert result["checks"] == [] and not report["summary"]["collection_complete"]
    assert report["summary"]["automatic_passes"] == 0


@pytest.mark.parametrize("checks, text, expected", [
    ([{"kind": "contains", "value": "أمل"}, {"kind": "excludes", "value": "خوف"}], "أمل", True),
    ([{"kind": "exact", "value": "أمل"}], " أمل", False),
    ([{"kind": "json_equals", "value": {"عدد": 1}}], '{"عدد":true}', False),
    ([{"kind": "json_equals", "value": {"عدد": 1}}], '{"عدد":1}', True),
    ([{"kind": "json_equals", "value": {"عدد": 1}}], '{"عدد":1.0}', True),
    ([{"kind": "json_equals", "value": {"عدد": 4.3}}], '{"عدد":4.3000000000000001}', False),
    ([{"kind": "json_equals", "value": {"عدد": 9007199254740992.0}}], '{"عدد":9007199254740993.0}', False),
    ([{"kind": "json_equals", "value": {"عدد": 1}}], '{"عدد":1,"عدد":1}', False),
    ([], "نص حر", None),
])
def test_checks_are_limited_and_never_grant_readiness(tmp_path, checks, text, expected):
    data = suite()
    data["cases"][0]["checks"] = checks
    report = evaluate_suite(data, FakeProvider([answer(text)]), tmp_path / "runs")
    assert report["results"][0]["automatic_pass"] is expected
    assert report["summary"]["collection_complete"] is True
    assert report["manual_review"] == report["results"][0]["manual_review"] == "not_required_q49"
    assert report["release_ready"] is report["summary"]["release_ready"] is False


def test_oversized_decimal_exponent_is_a_failed_check_not_an_execution_error(tmp_path):
    data = suite()
    data["cases"][0]["checks"] = [{"kind": "json_equals", "value": {"n": 1}}]
    text = '{"n":1e99999999999999999999999999999999999999999}'
    report = evaluate_suite(data, FakeProvider([answer(text)]), tmp_path / "runs")
    result = report["results"][0]
    assert result["status"] == "complete" and result["error_code"] is None
    assert result["checks"][0]["passed"] is False
    assert result["checks_status"] == "evaluated" and result["automatic_pass"] is False
    assert report["summary"]["collection_complete"] is True
    assert report["summary"]["automatic_failures"] == 1


def test_unexpected_checker_error_cannot_leave_collection_complete(tmp_path, monkeypatch):
    import evaluation.capabilities as capabilities

    def broken_checker(*args):
        raise RuntimeError("فشل داخلي مصطنع في الفحص")

    monkeypatch.setattr(capabilities, "_checks", broken_checker)
    report = evaluate_suite(suite(), FakeProvider([answer()]), tmp_path / "runs")
    result = report["results"][0]
    assert result["status"] == "error" and result["error_code"] == "execution_failed"
    assert result["automatic_pass"] is False and result["checks_status"] == "not_run"
    assert result["checks"] == []
    assert report["summary"]["collection_complete"] is False
    assert report["summary"]["execution_errors"] == 1


@pytest.mark.parametrize("tamper", ["replace", "truncate"])
def test_corrupt_ledger_stops_before_replay_or_calls(tmp_path, tamper):
    root = tmp_path / "runs"
    evaluate_suite(suite(), FakeProvider([answer()]), root, run_id="audit")
    ledger = root / "audit/calls.jsonl"
    ledger.write_text(ledger.read_text().replace("أملًا", "مزور") if tamper == "replace" else "")
    provider = FakeProvider([])
    with pytest.raises(CapabilityError, match="run_ledger_corrupt"):
        evaluate_suite(suite(), provider, root, run_id="audit")
    assert provider.calls == provider.estimates == 0


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_run_ledger_cannot_overwrite_external_file(tmp_path, link_kind):
    root = tmp_path / "runs"
    evaluate_suite(suite(), FakeProvider([answer()]), root, run_id="safe")
    ledger = root / "safe/calls.jsonl"
    ledger.unlink()
    external = tmp_path / "source.json"
    external.write_text("SOURCE_MUST_STAY")
    if link_kind == "symlink":
        ledger.symlink_to(external)
    else:
        os.link(external, ledger)
    provider = FakeProvider([])
    with pytest.raises(CapabilityError, match="unsafe_output_path"):
        evaluate_suite(suite(), provider, root, run_id="safe")
    assert external.read_text() == "SOURCE_MUST_STAY" and provider.calls == 0


def test_existing_directory_and_escape_run_id_are_not_overwritten(tmp_path):
    (tmp_path / "unowned").mkdir()
    source = tmp_path / "unowned/manifest.json"
    source.write_text('{"source":"keep"}')
    with pytest.raises(CapabilityError, match="run_config_conflict"):
        evaluate_suite(suite(), FakeProvider([]), tmp_path, run_id="unowned")
    assert source.read_text() == '{"source":"keep"}'
    with pytest.raises(CapabilityError, match="identifier_invalid"):
        evaluate_suite(suite(), FakeProvider([]), tmp_path, run_id="../escape")


def test_number_reference_cannot_be_silently_rounded(tmp_path):
    data = suite()
    data["cases"][0]["checks"] = [{"kind": "json_equals", "value": {"n": 4.3}}]
    text = json.dumps(data).replace('"n": 4.3', '"n": 4.3000000000000001')
    path = tmp_path / "reference.json"
    path.write_text(text)
    with pytest.raises(CapabilityError, match="json_number_precision"):
        load_suite(path)
    path.write_text(text.replace('4.3000000000000001',
                                 '1e99999999999999999999999999999999999999999'))
    with pytest.raises(CapabilityError, match="json_number_precision"):
        load_suite(path)


def test_user_can_request_english_and_version_is_part_of_request(tmp_path):
    data = suite()
    data["cases"][0]["messages"] = [{"role": "user", "content": "ترجم إلى الإنجليزية فقط: أمل"}]
    data["cases"][0]["checks"] = [{"kind": "exact", "value": "hope"}]
    provider = FakeProvider([answer("hope")])
    report = evaluate_suite(data, provider, tmp_path / "runs", model_version="weights-digest")
    assert "ما لم يطلب المستخدم لغة أخرى" in provider.requests[0].messages[0].content
    assert provider.requests[0].model_version == "weights-digest"
    assert report["metadata"]["runtime_model_version"] == "weights-digest"
    assert report["results"][0]["automatic_pass"] is True


def test_valid_hash_chain_append_after_seal_is_not_replayed_or_resealed(tmp_path):
    root = tmp_path / "runs"
    evaluate_suite(suite(), FakeProvider([answer()]), root, run_id="sealed")
    ledger = Ledger(root / "sealed/calls.jsonl")
    forged = copy.deepcopy(ledger.entries()[0]["record"])
    forged["response"]["content"] = "رد مزور"
    ledger.append(forged)   # سلسلة صحيحة؛ فحص prefix وحده كان يسمح بهذا
    before = ledger.path.read_bytes(), ledger.anchor_path.read_bytes()
    provider = FakeProvider([])
    with pytest.raises(CapabilityError, match="run_checkpoint_mismatch"):
        evaluate_suite(suite(), provider, root, run_id="sealed")
    assert provider.calls == provider.estimates == 0
    assert (ledger.path.read_bytes(), ledger.anchor_path.read_bytes()) == before


def test_interrupted_retry_resumes_documented_running_tail(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    evaluate_suite(suite(), FakeProvider([
        ProviderError("unreachable", "انقطاع", retryable=True)]), root, run_id="retry")
    original_anchor = Ledger.anchor

    def cut_before_anchor(self):
        raise KeyboardInterrupt("قطع بعد تقييد الجواب وقبل الختم")

    monkeypatch.setattr(Ledger, "anchor", cut_before_anchor)
    with pytest.raises(KeyboardInterrupt):
        evaluate_suite(suite(), FakeProvider([answer()]), root, run_id="retry")
    state_path = root / "retry/state.json"
    assert json.loads(state_path.read_text())["phase"] == "running"
    ledger_path = root / "retry/calls.jsonl"
    before = ledger_path.read_bytes()
    monkeypatch.setattr(Ledger, "anchor", original_anchor)
    provider = FakeProvider([])
    report = evaluate_suite(suite(), provider, root, run_id="retry")
    assert report["summary"]["collection_complete"] and report["results"][0]["replayed"]
    assert provider.calls == provider.estimates == 0
    assert ledger_path.read_bytes() == before
    assert json.loads(state_path.read_text())["phase"] == "sealed"


def test_concurrent_run_is_refused_before_duplicate_provider_call(tmp_path):
    root = tmp_path / "runs"
    evaluate_suite(suite(), FakeProvider([
        ProviderError("unreachable", "انقطاع", retryable=True)]), root, run_id="shared")
    entered, release = Event(), Event()

    class WaitingProvider(FakeProvider):
        def complete(self, request):
            entered.set()
            assert release.wait(timeout=5)
            return super().complete(request)

    first = WaitingProvider([answer()])
    second = FakeProvider([])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(evaluate_suite, suite(), first, root, run_id="shared")
        try:
            assert entered.wait(timeout=5)
            with pytest.raises(CapabilityError, match="run_busy"):
                evaluate_suite(suite(), second, root, run_id="shared")
        finally:
            release.set()
        assert future.result()["summary"]["collection_complete"]
    assert first.calls == 1 and second.calls == second.estimates == 0
    ledger = Ledger(root / "shared/calls.jsonl")
    assert ledger.verify_chain(strict=True)
    assert ledger.count() == 2   # العطل القديم والجواب؛ لا نداء مكرر


def test_nonzero_estimate_cannot_bypass_zero_local_budget(tmp_path):
    provider = FakeProvider([answer()], estimate=1)
    report = evaluate_suite(suite(), provider, tmp_path / "runs")
    assert provider.calls == 0
    assert report["results"][0]["error_code"] == "day_cap"


@pytest.mark.parametrize("response, expected_exit", [(answer("غير مطابق"), 0),
                                                     (answer(stop="max_output"), 1)])
def test_cli_only_emits_aggregates_and_exit_means_collection(
        tmp_path, monkeypatch, capsys, response, expected_exit):
    from tools import evaluate_capabilities as cli
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(suite(), ensure_ascii=False))
    provider = FakeProvider([response])
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "OllamaProvider", lambda model: provider)
    assert cli.main(["--suite", str(path), "--model", provider.model]) == expected_exit
    output = capsys.readouterr().out
    assert provider.model not in output and "REFERENCE_DO_NOT_SEND" not in output
    assert "غير مطابق" not in output
    assert json.loads(output)["release_ready"] is False


def test_cli_requires_model_without_default(capsys):
    from tools.evaluate_capabilities import main
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2 and "--model" in capsys.readouterr().err


def _completed_run(boundary):
    """خُلفيّةٌ مصطنعة تبلغ سطرَ الحكم الذي حمله السكربت (ك١٣): مدقّقٌ أتمّ بلا استثناء."""
    from core.execution import ExecutionResult

    def run(argv, **_):
        marker = re.search(r"__DIWAN_VERDICT__ [0-9a-f]{32} PASS", argv[-1]).group(0)
        return ExecutionResult(0, "\n" + marker + "\n", "", boundary)
    return run


def _sandbox_suite():
    s = suite()
    s["cases"][0]["checks"] = [{
        "kind": "python_sandbox",
        "value": "assert 'مطابق' in __model_raw_answer__\nassert 1 + 1 == 2"
    }]
    return s


def test_python_sandbox_check_in_suite(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core import sandbox
    from core.execution import ExecutionResult
    from evaluation.human_review import validate_report
    receipt = {"image_id": "sha256:" + "a" * 64, "lock_sha256": "b" * 64,
               "python_version": "3.14.7"}
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", SimpleNamespace(receipt=receipt,
        run=_completed_run("docker:" + "c" * 64)))
    provider = FakeProvider([answer("نص مطابق تماما")])
    report = evaluate_suite(_sandbox_suite(), provider, tmp_path / "runs")
    res = report["results"][0]
    assert res["status"] == "complete"
    assert res["automatic_pass"] is True
    check = res["checks"][0]
    assert check["kind"] == "python_sandbox"
    assert check["passed"] is True
    assert len(check["witness_digest"]) == 64
    assert check["exit_code"] == 0
    assert check["boundary"] == "docker:" + receipt["image_id"] + "@" + "c" * 64
    config = report["metadata"]["manifest"]["config"]
    assert config["runner_version"] == 4
    assert config["execution_host"] == "docker:" + receipt["image_id"]
    assert config["execution_backend"] == {"backend": "docker", **receipt, "snapshot_files": []}
    assert validate_report(report) is report


def test_python_sandbox_without_backend_errors_even_with_declared_host(tmp_path, monkeypatch):
    """«تعذّر التحقق» ليس «فشل التحقق»: حالةٌ لم تُقَس تُعلَن خطأَ تشغيل."""
    from core import sandbox
    from evaluation.human_review import validate_report
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", None)
    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", "pytest-declared-host")
    provider = FakeProvider([answer("نص مطابق تماما")])
    report = evaluate_suite(_sandbox_suite(), provider, tmp_path / "runs")
    res = report["results"][0]
    assert res["status"] == "error"
    assert res["error_code"] == "sandbox_backend_unavailable"
    assert res["checks"] == [] and res["checks_status"] == "not_run"
    # لا يُعَدُّ رسوبًا آليًّا، ويسقط اكتمالُ الجمع كلِّه
    assert report["summary"]["automatic_failures"] == 0
    assert report["summary"]["collection_complete"] is False
    assert report["summary"]["execution_errors"] == 1
    assert report["metadata"]["manifest"]["config"]["execution_host"] is None
    assert report["metadata"]["manifest"]["config"]["execution_backend"] is None
    assert report["manual_review"] == "not_required_q49"
    assert validate_report(report) is report


def test_environment_declaration_cannot_change_config_fingerprint(tmp_path, monkeypatch):
    from core import sandbox
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", None)
    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", "host-alpha")
    a = evaluate_suite(_sandbox_suite(), FakeProvider([answer("نص مطابق تماما")]),
                       tmp_path / "a")
    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", "host-beta")
    b = evaluate_suite(_sandbox_suite(), FakeProvider([answer("نص مطابق تماما")]),
                       tmp_path / "b")
    assert (a["metadata"]["manifest"]["config_sha256"]
            == b["metadata"]["manifest"]["config_sha256"])


@pytest.mark.parametrize("refusal", ["execution_timeout", "execution_cleanup_unverified",
                                    "execution_boundary_unverified", "execution_image_unavailable"])
def test_sandbox_runtime_failures_are_not_model_failures(tmp_path, monkeypatch, refusal):
    from types import SimpleNamespace
    from core import sandbox
    from core.execution import ExecutionRefused
    from evaluation.human_review import validate_report
    receipt = {"image_id": "sha256:" + "a" * 64, "lock_sha256": "b" * 64,
               "python_version": "3.14.7"}
    def refuse(*args, **kwargs):
        raise ExecutionRefused(refusal, "synthetic backend refusal")
    monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", SimpleNamespace(receipt=receipt, run=refuse))
    report = evaluate_suite(_sandbox_suite(), FakeProvider([answer("نص مطابق تماما")]), tmp_path)
    assert report["results"][0]["error_code"] == "sandbox_" + refusal.removeprefix("execution_")
    assert report["results"][0]["checks"] == []
    assert report["summary"]["automatic_failures"] == 0
    assert report["summary"]["execution_errors"] == 1
    assert validate_report(report) is report


def test_configured_image_changes_report_fingerprint(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from core import sandbox
    from core.execution import ExecutionResult
    fingerprints = []
    for letter in ("a", "b"):
        receipt = {"image_id": "sha256:" + letter * 64, "lock_sha256": "c" * 64,
                   "python_version": "3.14.7"}
        monkeypatch.setattr(sandbox, "_SANDBOX_BACKEND", SimpleNamespace(receipt=receipt,
            run=_completed_run("docker:" + "d" * 64)))
        report = evaluate_suite(_sandbox_suite(), FakeProvider([answer("نص مطابق تماما")]), tmp_path / letter)
        fingerprints.append(report["metadata"]["manifest"]["config_sha256"])
    assert fingerprints[0] != fingerprints[1]
