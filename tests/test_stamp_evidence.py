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
FREE = "catalog_free_suffix_and_all_pricing_zero"
EVIDENCE = {"m:free": {"proof": FREE, "catalog": "https://openrouter.ai/api/v1/models",
                       "observed_at": "2026-10-05T07:00:00Z", "pricing": {"completion": "0", "prompt": "0", "request": "0"}}}
LOCAL = {"cloud_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0, "cost_basis": "local_no_charge"}


def _row(provider, model, *, cloud=True, sent=True, usage=(10, 5), cost=None, status="not_reported", kind=None,
         proof=None):
    row = {"provider": provider, "model": model, "request_sent": sent, "cost_usd": cost, "cost_status": status,
           "usage": None if usage is None else {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
           "zero_spend_proof": proof}
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
    pytest.param({"model": "llama-3.3-70b-versatile"}, id="bare_remote_id"),
    pytest.param({"model": "meta-llama/llama-3.3-70b-instruct:free"}, id="remote_id_with_a_tag"),
])
def test_a_cloud_model_without_a_call_ledger_needs_its_spend_given(payload):
    registered = {**MODELS, ml.canonical(payload["model"]): MODELS["deepseek-ai/DeepSeek-V3-0324"]}
    stamped, problems = se.stamp(payload, registered)
    assert problems == ["spend_basis_required"] and "spend" not in stamped
    given = {"cloud_calls": 3, "prompt_tokens": 30, "completion_tokens": 9, "cost_usd": 0,
             "cost_basis": "subscription_flat"}
    stamped, problems = se.stamp(payload, registered, given)
    assert problems == [] and stamped["spend"] == given


@pytest.mark.parametrize("name", [
    pytest.param("qwen3.5:9b", id="ollama_tag"),
    pytest.param("hf.co/org/model-GGUF:Q4_K_M", id="hf_weights_pulled_to_ollama"),
    pytest.param("tesseract", id="in_process_engine"),
])
def test_only_a_positively_local_name_is_stamped_local(name):
    """ملاحظة Codex على #310: الاسمُ العاري بلا وسمٍ مبهم (Groq وGitHub Models)، فالمحليُّ ما عُرف محليًّا."""
    assert se.stamp_spend({"model": name}, None) == (LOCAL, [])


def test_the_derived_block_is_checked_by_the_spend_guard_itself(monkeypatch):
    """ملاحظة Codex على #310: الكتلةُ المشتقّة تمرّ مدقّقَ الحارس نفسَه، فما يردّه الحارسُ لا يُكتب ولو فات فحوصَ الاشتقاق."""
    monkeypatch.setattr(se.probe_spend, "spend_findings", lambda file, spend: [f"spend_guard_refused:{file}"])
    assert se.spend_from_usage([_row("ollama", "deepseek-v4.1-flash:cloud")]) == (None, ["spend_guard_refused"])


def test_a_given_or_written_spend_is_checked_not_trusted():
    bad = {"cloud_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 1, "cost_basis": "local_no_charge"}
    stamped, problems = se.stamp({"model": "qwen3.5:9b", "spend": bad}, MODELS)
    assert problems == ["spend_local_with_cloud"]


@pytest.mark.parametrize("licenses", [pytest.param(None, id="null"), pytest.param("apache-2.0", id="text"),
                                      pytest.param(["apache-2.0"], id="list")])
def test_a_malformed_license_block_is_named_not_replaced(licenses):
    """ملاحظة Codex على #310: كتلةُ `licenses` بغير شكل كائنٍ لا تُستبدل بقيم السجلّ فيمرّ الدليلُ الفاسد."""
    payload = {"model": "qwen3.5:9b", "spend": LOCAL, "licenses": licenses}
    stamped, problems = se.stamp(payload, MODELS)
    assert problems == ["licenses_malformed"]
    assert stamped["licenses"] == licenses, "الكتلةُ الفاسدة باقيةٌ كما هي"


@pytest.mark.parametrize("payload, code", [
    pytest.param({"model": "qwen3.5:9b", "spend": "corrupt"}, "spend_missing", id="written_spend_not_an_object"),
    pytest.param({"model": "qwen3.5:9b", "spend": None}, "spend_missing", id="written_spend_null"),
    pytest.param({"model": "deepseek-v4.1-flash:cloud", "provider_usage": [None]}, "spend_ledger_malformed",
                 id="ledger_row_not_an_object"),
    pytest.param({"model": "deepseek-v4.1-flash:cloud", "provider_usage": [_row("ollama", "deepseek-v4.1-flash:cloud"), 5]},
                 "spend_ledger_malformed", id="one_ledger_row_not_an_object"),
    pytest.param({"model": "qwen3.5:9b", "provider_usage": "corrupt"}, "spend_ledger_malformed",
                 id="ledger_not_a_list"),
    pytest.param({"model": "deepseek-v4.1-flash:cloud",
                  "provider_usage": [{k: v for k, v in _row("ollama", "deepseek-v4.1-flash:cloud").items()
                                      if k != "request_sent"}]},
                 "spend_ledger_malformed", id="row_without_request_sent"),
    pytest.param({"model": "deepseek-v4.1-flash:cloud",
                  "provider_usage": [{**_row("ollama", "deepseek-v4.1-flash:cloud"), "request_sent": "yes"}]},
                 "spend_ledger_malformed", id="request_sent_not_a_boolean"),
    pytest.param({"model": "deepseek-v4.1-flash:cloud",
                  "provider_usage": [{k: v for k, v in _row("ollama", "deepseek-v4.1-flash:cloud").items() if k != "cloud"}]},
                 "spend_ledger_malformed", id="ollama_row_without_cloud"),
    pytest.param({"model": "deepseek-v4.1-flash:cloud",
                  "provider_usage": [{**_row("ollama", "deepseek-v4.1-flash:cloud"), "cloud": None}]},
                 "spend_ledger_malformed", id="ollama_cloud_not_a_boolean"),
])
def test_a_malformed_spend_or_ledger_is_named_not_replaced(payload, code):
    """ملاحظتا Codex على #310: كتلةٌ أو سجلٌّ فاسدٌ لا يُصفّى إلى كتلةٍ صفريّةٍ صالحة، بل يُسمّى ولا يُختم."""
    stamped, problems = se.stamp(payload, MODELS)
    assert problems == [code]
    assert stamped.get("spend") == payload.get("spend"), "لا تُكتب كتلةٌ بدل الفاسدة"


@pytest.mark.parametrize("rows, spend", [
    pytest.param([_row("ollama", "deepseek-v4.1-flash:cloud"), _row("ollama", "qwen3.5:9b", cloud=False),
                  _row("ollama", "deepseek-v4.1-flash:cloud", sent=False)],
                 {"cloud_calls": 1, "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0,
                  "cost_basis": "subscription_flat"}, id="ollama_subscription"),
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


def test_a_free_call_is_stamped_free_on_its_saved_catalog_evidence():
    rows = [_row("openrouter", "m:free", cost="0", status="reported", proof=FREE), _row("openrouter", None, kind="catalog")]
    derived, problems = se.spend_from_usage(rows, EVIDENCE)
    assert problems == [] and derived == {"cloud_calls": 1, "prompt_tokens": 10, "completion_tokens": 5, "cost_usd": 0,
                                          "cost_basis": "free_tier"}
    assert probe_spend.spend_findings("f", derived) == []
    groq = [_row("groq", "llama", proof=se.GROQ_PROOF)]
    assert se.spend_from_usage(groq)[0]["cost_basis"] == "free_tier"


def _entry(**changes):
    return {"m:free": {**EVIDENCE["m:free"], **changes}}


@pytest.mark.parametrize("row, evidence", [
    pytest.param(_row("openrouter", "m:free", proof=FREE), None, id="proof_without_evidence"),
    pytest.param(_row("openrouter", "m:free", proof=FREE), {"n:free": EVIDENCE["m:free"]}, id="evidence_of_another_model"),
    pytest.param(_row("openrouter", "m:free", proof=FREE), _entry(proof="x"), id="evidence_proof_differs"),
    pytest.param(_row("openrouter", "m:free", proof=FREE), _entry(catalog=""), id="evidence_without_catalog"),
    pytest.param(_row("openrouter", "m:free", proof=FREE), _entry(observed_at=None), id="evidence_without_observed_at"),
    pytest.param(_row("openrouter", "m:free", proof=FREE), _entry(pricing={"prompt": "0.000001", "completion": "0"}),
                 id="evidence_priced"),
    pytest.param(_row("openrouter", "m:free", proof=FREE), _entry(pricing={"prompt": "0"}), id="evidence_one_price_missing"),
    pytest.param(_row("openrouter", "m", proof=FREE), {"m": EVIDENCE["m:free"]}, id="not_a_free_model"),
    pytest.param(_row("openrouter", "m:free", proof="made_up"), _entry(proof="made_up"), id="proof_not_the_validators"),
    pytest.param(_row("github-models", "m:free", proof=FREE), EVIDENCE, id="another_provider"),
    pytest.param(_row("groq", "llama", proof=FREE), None, id="groq_without_operator_confirmation"),
])
def test_a_free_claim_without_its_validated_evidence_is_unpriced(row, evidence):
    derived, problems = se.spend_from_usage([row], evidence)
    assert problems == [] and derived["cost_basis"] == "unpriced" and derived["cost_usd"] is None


def test_the_stamp_reads_the_free_evidence_saved_beside_the_ledger():
    payload = {"reviewers": ["deepseek-v4.1-flash:cloud"], "zero_spend_evidence": EVIDENCE,
               "provider_usage": [_row("openrouter", "m:free", proof=FREE)]}
    stamped, problems = se.stamp(payload, {**MODELS, "m:free": MODELS["deepseek-v4.1-flash:cloud"]})
    assert problems == [] and stamped["spend"]["cost_basis"] == "free_tier"


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
    assert se.main(["--check", "--registry", str(registry), str(ok)]) == 1
    assert json.loads(ok.read_text(encoding="utf-8")) == {"model": "qwen3.5:9b"}, "--check لا يكتب"
    capsys.readouterr()
    assert se.main(["--registry", str(registry), str(ok), str(held)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["findings"] == {str(ok): [], str(held): ["license_pending:gemma3:12b"]}
    assert json.loads(ok.read_text(encoding="utf-8"))["spend"] == LOCAL
    assert list(tmp_path.glob(".*.tmp")) == []
    capsys.readouterr()
    assert se.main(["--check", "--registry", str(registry), str(ok)]) == 0, "الملفُّ المختوم يمرّ الفحص"


def test_a_review_with_its_call_ledger_is_stamped_from_the_ledger():
    payload = {"reviewers": ["deepseek-v4.1-flash:cloud"],
               "provider_usage": [_row("ollama", "deepseek-v4.1-flash:cloud", usage=(120, 40))]}
    stamped, problems = se.stamp(payload, MODELS)
    assert problems == []
    assert stamped["licenses"] == {"deepseek-v4.1-flash:cloud": "mit"}
    assert stamped["spend"] == {"cloud_calls": 1, "prompt_tokens": 120, "completion_tokens": 40, "cost_usd": 0,
                                "cost_basis": "subscription_flat"}


@pytest.mark.parametrize("rows, code", [
    pytest.param([_row("ollama", "x:cloud", usage=None)], "spend_usage_incomplete", id="usage_missing"),
    pytest.param([{**_row("hf-router", "a/b", cost="0.1", status="reported"), "usage": {"prompt_tokens": 3}}],
                 "spend_usage_incomplete", id="one_counter_missing"),
    pytest.param([_row("hf-router", "a/b", usage=(-5, 10)), _row("hf-router", "a/b", usage=(10, 1))],
                 "spend_usage_incomplete", id="negative_counter_hidden_by_the_sum"),
    pytest.param([_row("hf-router", "a/b", usage=(True, 1))], "spend_usage_incomplete", id="bool_counter"),
    pytest.param([_row("hf-router", "a/b", cost="-1", status="reported")], "spend_cost_invalid", id="negative_cost"),
    pytest.param([_row("hf-router", "a/b", cost="abc", status="reported")], "spend_cost_invalid", id="unreadable_cost"),
    pytest.param([_row("hf-router", "a/b", cost="NaN", status="reported")], "spend_cost_invalid", id="nan_cost"),
    pytest.param([_row("hf-router", "a/b", cost="1e10000", status="reported")], "spend_cost_invalid",
                 id="cost_overflows_a_float"),
    pytest.param([_row("hf-router", "a/b", cost="0.1234567890123456789", status="reported")], "spend_cost_invalid",
                 id="cost_rounded_by_a_float"),
    pytest.param([_row("hf-router", "a/b", cost="9007199254740993", status="reported")], "spend_cost_invalid",
                 id="integer_cost_rounded_by_a_float"),
    pytest.param([_row("ollama", "deepseek-v4.1-flash:cloud", cost="3.75", status="reported")],
                 "spend_subscription_with_cost", id="ollama_row_with_a_cost"),
    pytest.param([_row("hf-router", "a/b", cost="1e-10000", status="reported")], "spend_cost_invalid",
                 id="cost_underflows_to_zero"),
    pytest.param([_row("hf-router", "a/b", cost="0.25")], "spend_cost_status_invalid", id="cost_without_a_reported_status"),
    pytest.param([_row("hf-router", "a/b", cost="0.25", status="guessed")], "spend_cost_status_invalid",
                 id="unknown_cost_status"),
    pytest.param([_row("hf-router", "a/b", status=None)], "spend_cost_status_invalid", id="cost_status_missing"),
])
def test_a_sent_call_without_valid_counts_or_cost_is_not_stamped(rows, code):
    assert se.spend_from_usage(rows) == (None, [code])


def test_a_free_backend_without_a_zero_spend_proof_is_unpriced_not_free():
    derived, problems = se.spend_from_usage([_row("openrouter", "m:free")])
    assert problems == [] and derived["cost_basis"] == "unpriced" and derived["cost_usd"] is None


def test_one_given_spend_block_is_refused_for_several_files(tmp_path):
    """ملاحظة Codex على #310: لكل دليلٍ إنفاقُه، فلا تُكتب كتلةٌ واحدة في ملفّين."""
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"models": MODELS}), encoding="utf-8")
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    for path in (first, second):
        path.write_text(json.dumps({"model": "deepseek-v4.1-flash:cloud"}), encoding="utf-8")
    given = json.dumps({"cloud_calls": 1, "prompt_tokens": 3, "completion_tokens": 1, "cost_usd": 0,
                        "cost_basis": "subscription_flat"})
    with pytest.raises(SystemExit) as refused:
        se.main(["--registry", str(registry), "--spend", given, str(first), str(second)])
    assert refused.value.code == 2
    assert json.loads(first.read_text(encoding="utf-8")) == {"model": "deepseek-v4.1-flash:cloud"}
    assert se.main(["--registry", str(registry), "--spend", given, str(first)]) == 0
    assert json.loads(first.read_text(encoding="utf-8"))["spend"]["cloud_calls"] == 1


def test_historical_evidence_is_neither_stamped_nor_checked(tmp_path, capsys):
    """ملاحظة Codex على #310: الدليلُ التاريخيّ بمقياس الحارسين نفسِه (القائمةُ وتاريخُ الإنفاذ) لا يُختم ولا يُفحص."""
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"models": MODELS, "historical_evidence": ["old.json", "dated.json"],
                                    "enforced_from": "2026-10-06"}), encoding="utf-8")
    old, dated, new = tmp_path / "old.json", tmp_path / "dated.json", tmp_path / "new.json"
    old.write_text(json.dumps({"model": "gemma3:12b"}), encoding="utf-8")
    dated.write_text(json.dumps({"date": "2026-10-07", "model": "gemma3:12b"}), encoding="utf-8")
    new.write_text(json.dumps({"model": "qwen3.5:9b"}), encoding="utf-8")
    assert se.main(["--registry", str(registry), str(old), str(dated), str(new)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["historical"] == [str(old)]
    assert report["findings"] == {str(dated): ["license_pending:gemma3:12b"], str(new): []}
    assert json.loads(old.read_text(encoding="utf-8")) == {"model": "gemma3:12b"}, "التاريخيُّ لا يُعاد ختمُه"
    assert json.loads(new.read_text(encoding="utf-8"))["spend"] == LOCAL


def test_the_documented_check_passes_on_the_published_evidence(capsys):
    """الأمرُ الموثَّق `--check docs/probe/*.json` يمرّ على الأدلّة المنشورة، ولا يكتب شيئًا."""
    files = sorted(str(path) for path in ml.PROBE.glob("*.json"))
    assert se.main(["--check", *files]) == 0, json.loads(capsys.readouterr().out)["findings"]


def test_check_mode_fails_a_file_that_still_needs_its_stamp(tmp_path, capsys):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"models": MODELS}), encoding="utf-8")
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps({"model": "qwen3.5:9b"}), encoding="utf-8")
    assert se.main(["--check", "--registry", str(registry), str(bare)]) == 1
    assert json.loads(capsys.readouterr().out)["findings"] == {str(bare): ["stamp_required"]}
    assert json.loads(bare.read_text(encoding="utf-8")) == {"model": "qwen3.5:9b"}
