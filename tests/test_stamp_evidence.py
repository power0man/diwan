"""ختمُ الأدلّة بكتلتَي `licenses` و`spend` من مصادرهما (`tools/stamp_evidence.py`، #301 و#295)."""
from __future__ import annotations

import json

import pytest

from tools import model_licenses as ml
from tools import probe_spend
from tools import stamp_evidence as se

MODELS = {
    "qwen3.5:9b": {"license": "apache-2.0", "source": "https://ollama.com/library/qwen3.5", "read_on": "2026-10-05"},
    "deepseek-v4.1-flash:cloud": {"license": "mit", "source": "https://ollama.com/library/x", "read_on": "2026-10-05"},
    "deepseek-ai/DeepSeek-V3-0324": {"license": "mit", "source": "https://huggingface.co/x", "read_on": "2026-10-05"},
    "gemma3:12b": {"pending": "read_with_ollama_show_license_on_the_mac"},
}
LOCAL = {"cloud_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0, "cost_basis": "local_no_charge"}


def _row(provider, model, *, cloud=True, sent=True, usage=(10, 5), cost=None, status="not_reported", kind=None):
    row = {"provider": provider, "model": model, "request_sent": sent, "cost_usd": cost, "cost_status": status,
           "usage": None if usage is None else {"prompt_tokens": usage[0], "completion_tokens": usage[1]}}
    if provider == "ollama":
        row["cloud"] = cloud
    if kind:
        row["kind"] = kind
    return row


def test_a_local_run_is_stamped_with_its_license_and_no_cloud_spend_and_then_passes_both_guards():
    payload = {"date": "2026-10-06", "config": {"model": "qwen3.5:9b"}, "result": {"pass": 3}}
    stamped, problems = se.stamp(payload, MODELS)
    assert problems == []
    assert stamped["licenses"] == {"qwen3.5:9b": "apache-2.0"} and stamped["spend"] == LOCAL
    assert ml.evidence_findings("new.json", stamped, MODELS, "2026-10-06", set()) == []
    assert probe_spend.findings({"historical_evidence": [], "enforced_from": "2026-10-06"},
                                {"new.json": stamped}) == []


@pytest.mark.parametrize("model, code", [
    pytest.param("gemma3:12b", "license_pending:gemma3:12b", id="pending"),
    pytest.param("phi9:1b", "license_unknown:phi9:1b", id="unknown"),
])
def test_a_license_not_read_is_named_and_not_stamped(model, code):
    stamped, problems = se.stamp({"model": model}, MODELS)
    assert problems == [code]
    assert "licenses" not in stamped, "لا رخصةَ تُخمَّن لنموذجٍ لم تُقرأ رخصتُه"


def test_a_written_license_that_disagrees_with_the_registry_is_named_not_replaced():
    stamped, problems = se.stamp({"model": "qwen3.5:9b", "licenses": {"qwen3.5:9b": "mit"}}, MODELS)
    assert problems == ["license_disagrees:qwen3.5:9b"]
    assert stamped["licenses"] == {"qwen3.5:9b": "mit"}


@pytest.mark.parametrize("payload", [
    pytest.param({"model": "deepseek-v4.1-flash:cloud"}, id="ollama_cloud"),
    pytest.param({"model": "deepseek-ai/DeepSeek-V3-0324"}, id="remote_provider_id"),
])
def test_a_cloud_model_without_a_call_ledger_needs_its_spend_given(payload):
    stamped, problems = se.stamp(payload, MODELS)
    assert problems == ["spend_basis_required"] and "spend" not in stamped
    given = {"cloud_calls": 3, "prompt_tokens": 30, "completion_tokens": 9, "cost_usd": 0,
             "cost_basis": "subscription_flat"}
    stamped, problems = se.stamp(payload, MODELS, given)
    assert problems == [] and stamped["spend"] == given


def test_a_given_or_written_spend_is_checked_not_trusted():
    bad = {"cloud_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 1, "cost_basis": "local_no_charge"}
    stamped, problems = se.stamp({"model": "qwen3.5:9b", "spend": bad}, MODELS)
    assert problems == ["spend_local_with_cloud"]


@pytest.mark.parametrize("rows, spend", [
    pytest.param([_row("ollama", "deepseek-v4.1-flash:cloud"), _row("ollama", "qwen3.5:9b", cloud=False),
                  _row("ollama", "deepseek-v4.1-flash:cloud", sent=False)],
                 {"cloud_calls": 1, "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0,
                  "cost_basis": "subscription_flat"}, id="ollama_subscription"),
    pytest.param([_row("openrouter", "m:free", cost="0", status="reported"), _row("openrouter", None, kind="catalog")],
                 {"cloud_calls": 1, "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0,
                  "cost_basis": "free_tier"}, id="free_backend"),
    pytest.param([_row("hf-router", "a/b", cost="0.0002", status="estimated_from_prices"),
                  _row("hf-router", "a/b", cost="0.0004", status="reserved_upper_bound")],
                 {"cloud_calls": 2, "prompt_tokens": 20, "completion_tokens": 10, "cost_usd": 0.0006,
                  "cost_basis": "estimated_from_prices"}, id="hf_router_estimated"),
    pytest.param([_row("hf-router", "a/b", cost="0.0005", status="reported")],
                 {"cloud_calls": 1, "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0.0005,
                  "cost_basis": "reported_by_provider"}, id="hf_router_reported"),
    pytest.param([_row("hf-router", "a/b", cost="0.0002", status="estimated_from_prices"), _row("hf-router", "a/b")],
                 {"cloud_calls": 2, "prompt_tokens": 20, "completion_tokens": 10, "cost_usd": None,
                  "cost_basis": "unpriced"}, id="hf_router_one_unpriced"),
    pytest.param([_row("ollama", "qwen3.5:9b", cloud=False)], LOCAL, id="only_local_calls"),
])
def test_spend_is_derived_from_the_call_ledger(rows, spend):
    derived, problems = se.spend_from_usage(rows)
    assert problems == [] and derived == spend
    assert probe_spend.spend_findings("f", derived) == []


def test_a_ledger_of_two_providers_is_not_summed_into_one_basis():
    derived, problems = se.spend_from_usage([_row("ollama", "x:cloud"), _row("groq", "y")])
    assert derived is None and problems == ["spend_mixed_providers"]


def test_evidence_naming_no_model_is_left_as_it_is():
    payload = {"date": "2026-10-06", "counts": {"a": 1}}
    assert se.stamp(payload, MODELS) == (payload, [])


def test_the_cli_stamps_in_place_and_check_writes_nothing(tmp_path, capsys):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"models": MODELS}), encoding="utf-8")
    ok, held = tmp_path / "ok.json", tmp_path / "held.json"
    ok.write_text(json.dumps({"model": "qwen3.5:9b"}), encoding="utf-8")
    held.write_text(json.dumps({"model": "gemma3:12b"}), encoding="utf-8")
    assert se.main(["--check", "--registry", str(registry), str(ok)]) == 0
    assert json.loads(ok.read_text(encoding="utf-8")) == {"model": "qwen3.5:9b"}, "--check لا يكتب"
    capsys.readouterr()
    assert se.main(["--registry", str(registry), str(ok), str(held)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["findings"] == {str(ok): [], str(held): ["license_pending:gemma3:12b"]}
    assert json.loads(ok.read_text(encoding="utf-8"))["spend"] == LOCAL
    assert list(tmp_path.glob(".*.tmp")) == []


def test_a_review_with_its_call_ledger_is_stamped_from_the_ledger():
    payload = {"reviewers": ["deepseek-v4.1-flash:cloud"],
               "provider_usage": [_row("ollama", "deepseek-v4.1-flash:cloud", usage=(120, 40))]}
    stamped, problems = se.stamp(payload, MODELS)
    assert problems == []
    assert stamped["licenses"] == {"deepseek-v4.1-flash:cloud": "mit"}
    assert stamped["spend"] == {"cloud_calls": 1, "prompt_tokens": 120, "completion_tokens": 40, "cost_usd": 0,
                                "cost_basis": "subscription_flat"}
