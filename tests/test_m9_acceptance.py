"""دليل م٩ يثبت آلية الحوار ويترك جودة العربية والمراجعة الآلية معلقتين."""
from __future__ import annotations

from dataclasses import replace
import json
import stat

import pytest

import acceptance_m9 as acceptance
from core.contracts import Message, Response, Usage
from providers.base import ProviderError


def test_synthetic_acceptance_covers_guards_and_does_not_claim_quality(tmp_path):
    report = acceptance.run_synthetic(tmp_path / "synthetic")
    assert report["scope"] == "synthetic_mechanism_only"
    assert report["passed"] == report["total"] == 14
    assert all(report["checks"].values())
    assert report["initial_provider_calls"] == 3
    assert report["counter_scope"] == "dialogue_only"
    assert report["replay_provider_calls"] == 0
    assert report["quality_pending"] is True
    assert report["human_review"] == "not_required_q49"
    assert report["automated_review"] == "pending"
    assert report["independent_bank"] == "not_provided"
    assert report["m8b_complete"] is False
    assert report["release_ready"] is False


def _sample(tmp_path):
    provider = acceptance.CapturingProvider(acceptance.SyntheticProvider(
        [acceptance._answer(text) for text in acceptance.ANSWERS]))
    _, results, _, _, _ = acceptance._dialogue(
        tmp_path / "sample", provider, model=acceptance.SYNTHETIC_MODEL,
        model_version=acceptance.SYNTHETIC_VERSION)
    return provider.requests, results


@pytest.mark.parametrize("mutation", ["dropped_context", "reordered", "fabricated_answer", "duplicate_system"])
def test_context_check_detects_structural_regressions(tmp_path, mutation):
    requests, results = _sample(tmp_path)
    requests = list(requests)
    messages = list(requests[2].messages)
    if mutation == "dropped_context":
        messages = [messages[0], messages[-1]]
    elif mutation == "reordered":
        messages[1], messages[3] = messages[3], messages[1]
    elif mutation == "fabricated_answer":
        messages[2] = Message("assistant", "جواب لم يقله المزوّد")
    else:
        messages.insert(1, Message("system", "تعليمات زائدة"))
    requests[2] = replace(requests[2], messages=tuple(messages))
    assert acceptance._context_checks(requests, results)["ordered_multi_turn_context"] is False


def test_request_identity_check_rejects_reused_context_identity(tmp_path):
    requests, results = _sample(tmp_path)
    results[2]["context_sha256"] = results[1]["context_sha256"]
    assert acceptance._context_checks(requests, results)["context_changes_request_identity"] is False


class CountedStub:
    name = "private-provider"
    is_local = True

    def __init__(self, model, version, *, fail=False):
        self.model = model
        self.version = version
        self.chat_calls = 0
        self.metadata_calls = 0
        self.fail = fail

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.chat_calls += 1
        self.metadata_calls += 2
        if self.fail:
            raise ProviderError("synthetic_failure", "fixture", retryable=False)
        return Response(acceptance.ANSWERS[self.chat_calls - 1], Usage(11, 9),
                        "complete", 0, provider=self.name, model_version=self.version)


def test_live_mode_evidence_is_private_and_summary_omits_identity_and_answers(tmp_path):
    model, version = "runtime-secret-identifier", "f" * 64
    public = acceptance.run_live(tmp_path / "runs", model=model, model_version=version,
                                 run_id="local-sample", provider_factory=CountedStub)
    assert public["passed"] == public["total"] == 11
    assert public["initial_chat_calls"] == 3
    assert public["initial_metadata_calls"] == 6
    assert public["replay_chat_calls"] == public["replay_metadata_calls"] == 0
    assert public["scope"] == "live_development_dialogue_mechanism"
    wire = json.dumps(public, ensure_ascii=False)
    assert model not in wire and version not in wire
    assert all(answer not in wire for answer in acceptance.ANSWERS)
    path = tmp_path / "runs/local-sample/report.json"
    private = json.loads(path.read_text())
    assert private["runtime_model"] == model and private["model_version"] == version
    assert private["prompts"] == list(acceptance.PROMPTS)
    assert [r["content"] for r in private["results"]] == list(acceptance.ANSWERS)
    assert private["quality_pending"] is True and private["release_ready"] is False
    assert private["human_review"] == public["human_review"] == "not_required_q49"
    assert private["automated_review"] == public["automated_review"] == "pending"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_existing_live_sample_is_never_relabelled_as_a_fresh_run(tmp_path):
    arguments = dict(model="runtime", model_version="b" * 64, run_id="once", provider_factory=CountedStub)
    acceptance.run_live(tmp_path / "runs", **arguments)
    before = {str(p): p.read_bytes() for p in (tmp_path / "runs").rglob("*") if p.is_file()}
    arguments["provider_factory"] = lambda *_: pytest.fail("must reject before provider construction")
    with pytest.raises(acceptance.AcceptanceError, match="run_already_exists"):
        acceptance.run_live(tmp_path / "runs", **arguments)
    assert before == {str(p): p.read_bytes() for p in (tmp_path / "runs").rglob("*") if p.is_file()}


def test_failed_live_turn_is_saved_without_further_model_calls_or_success_claim(tmp_path):
    public = acceptance.run_live(tmp_path / "runs", model="runtime", model_version="b" * 64,
                                 run_id="failed", provider_factory=lambda m, v: CountedStub(m, v, fail=True))
    assert public["initial_provider_calls"] == public["initial_chat_calls"] == 1
    assert public["replay_provider_calls"] == public["replay_chat_calls"] == 0
    assert public["checks"]["three_complete_turns"] is False
    assert public["checks"]["three_initial_chat_requests"] is False
    assert public["passed"] < public["total"]
    report = json.loads((tmp_path / "runs/failed/report.json").read_text())
    assert len(report["results"]) == 1
    assert report["results"][0]["error_code"] == "synthetic_failure"
    assert report["release_ready"] is False


@pytest.mark.parametrize("run_id", ["../escape", "name/child", ".", "a" * 81, ""])
def test_live_run_id_cannot_escape_private_run_folder(tmp_path, run_id):
    with pytest.raises(acceptance.AcceptanceError, match="run_id_invalid"):
        acceptance.run_live(tmp_path / "runs", model="runtime", model_version="b" * 64,
                             run_id=run_id, provider_factory=lambda *_: pytest.fail("no provider expected"))
    assert not (tmp_path / "runs").exists()


def test_live_root_symlink_rejected_before_provider(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(acceptance.AcceptanceError, match="root_symlink"):
        acceptance.run_live(link, model="runtime", model_version="b" * 64, run_id="sample",
                             provider_factory=lambda *_: pytest.fail("no provider expected"))
    assert not list(target.iterdir())


@pytest.mark.parametrize("arguments", [["--model", "runtime"], ["--live"], ["--root", "somewhere"]])
def test_cli_requires_explicit_complete_live_configuration(arguments):
    with pytest.raises(SystemExit) as exc:
        acceptance.main(arguments)
    assert exc.value.code == 2


def test_default_cli_runs_synthetic_only_and_uses_non_quality_exit_code(capsys):
    assert acceptance.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["scope"] == "synthetic_mechanism_only"
    assert report["quality_pending"] is True and report["release_ready"] is False


def test_main_failure_returns_nonzero_and_does_not_print_private_exception(monkeypatch, capsys):
    def fail(_):
        raise acceptance.ConversationError("synthetic_error", "PRIVATE CONTENT")
    monkeypatch.setattr(acceptance, "run_synthetic", fail)
    assert acceptance.main([]) == 1
    output = capsys.readouterr().out
    assert "PRIVATE CONTENT" not in output
    assert json.loads(output)["error_code"] == "synthetic_error"


def test_invalid_live_provider_is_named_error_without_traceback(tmp_path, capsys):
    result = acceptance.main(["--live", "--model", "example-cloud", "--model-version", "a" * 64,
                              "--run-id", "invalid-provider", "--root", str(tmp_path / "runs")])
    assert result == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["error_code"] == "local_chat_remote_model"
    assert "example-cloud" not in output.out


def test_live_filesystem_failure_is_named_without_private_path(tmp_path, capsys):
    root = tmp_path / "private-path"
    root.write_text("occupied")
    result = acceptance.main(["--live", "--model", "fixture", "--model-version", "a" * 64,
                              "--run-id", "filesystem", "--root", str(root)])
    assert result == 1
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["error_code"] == "filesystem_error"
    assert "private-path" not in output.out
