"""كتلةُ الإنفاق في كل دليلٍ جديد يسمّي نموذجًا (جديد-spend-ledger، البند ٤ من #295؛ ق٦٧-٣)."""
from __future__ import annotations

import json
import sys

import pytest

from tools import model_licenses as ml
from tools import probe_spend as ps

sys.path.insert(0, str(ml.ROOT / "tools"))
import probe_evidence as pe  # noqa: E402

LOCAL = {"cloud_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0, "cost_basis": "local_no_charge"}
CLOUD = {"cloud_calls": 3, "prompt_tokens": 900, "completion_tokens": 40, "cost_usd": 0, "cost_basis": "subscription_flat"}


def _registry(historical=()) -> dict:
    return {"enforced_from": "2026-10-06", "historical_evidence": list(historical), "models": {}}


def test_the_published_evidence_passes():
    registry = json.loads(ml.REGISTRY.read_text(encoding="utf-8"))
    assert ps.findings(registry, ml.load_evidence()) == []


def test_new_model_evidence_without_a_spend_block_is_named():
    evidence = {"n.json": {"date": "2026-10-06", "config": {"model": "qwen3.5:9b"}}}
    assert ps.findings(_registry(), evidence) == ["spend_missing:n.json"]


def test_historical_and_model_free_evidence_need_no_spend_block():
    old = {"date": "2026-09-30", "model": "qwen3.5:9b"}
    assert ps.findings(_registry(["old.json"]), {"old.json": old}) == []
    assert ps.findings(_registry(), {"n.json": {"date": "2026-10-06", "summary": "بلا نموذج"}}) == []
    dated = {"date": "2026-10-06", "model": "qwen3.5:9b"}
    assert ps.findings(_registry(["old.json"]), {"old.json": dated}) == ["spend_missing:old.json"]


@pytest.mark.parametrize("spend", [pytest.param(LOCAL, id="local"), pytest.param(CLOUD, id="subscription"),
                                   pytest.param({**CLOUD, "cost_usd": 0.0123, "cost_basis": "reported_by_provider"},
                                                id="reported"),
                                   pytest.param({**CLOUD, "cost_usd": None, "cost_basis": "unpriced"}, id="unpriced")])
def test_a_well_formed_spend_block_passes(spend):
    assert ps.spend_findings("n.json", spend) == []


@pytest.mark.parametrize("spend, code", [
    pytest.param({**LOCAL, "extra": 1}, "spend_keys:n.json", id="extra_key"),
    pytest.param({k: v for k, v in LOCAL.items() if k != "cost_basis"}, "spend_keys:n.json", id="missing_key"),
    pytest.param({**CLOUD, "prompt_tokens": -1}, "spend_count:n.json:prompt_tokens", id="negative_count"),
    pytest.param({**CLOUD, "cloud_calls": True}, "spend_count:n.json:cloud_calls", id="boolean_count"),
    pytest.param({**CLOUD, "completion_tokens": 4.0}, "spend_count:n.json:completion_tokens", id="float_count"),
    pytest.param({**CLOUD, "cost_basis": "cheap"}, "spend_basis_unknown:n.json", id="unknown_basis"),
    pytest.param({**CLOUD, "cost_basis": "unpriced"}, "spend_unpriced_with_cost:n.json", id="unpriced_with_cost"),
    pytest.param({**CLOUD, "cost_usd": None}, "spend_cost:n.json", id="priced_without_cost"),
    pytest.param({**CLOUD, "cost_usd": -0.5, "cost_basis": "reported_by_provider"}, "spend_cost:n.json",
                 id="negative_cost"),
    pytest.param({**CLOUD, "cost_usd": True, "cost_basis": "reported_by_provider"}, "spend_cost:n.json",
                 id="boolean_cost"),
    pytest.param({**CLOUD, "cost_usd": float("inf"), "cost_basis": "reported_by_provider"}, "spend_cost:n.json",
                 id="infinite_cost"),
    pytest.param({**CLOUD, "cost_usd": float("nan"), "cost_basis": "reported_by_provider"}, "spend_cost:n.json",
                 id="nan_cost"),
    pytest.param({**LOCAL, "cloud_calls": 2}, "spend_local_with_cloud:n.json", id="local_with_calls"),
    pytest.param({**LOCAL, "cost_usd": 0.01}, "spend_local_with_cloud:n.json", id="local_with_cost"),
    pytest.param({**CLOUD, "cloud_calls": 0}, "spend_cloud_basis_without_calls:n.json", id="cloud_basis_no_calls"),
    pytest.param({**CLOUD, "cost_usd": 0.4}, "spend_free_basis_with_cost:n.json", id="subscription_with_cost"),
    pytest.param("free", "spend_missing:n.json", id="not_an_object"),
])
def test_a_malformed_spend_block_is_named(spend, code):
    assert ps.spend_findings("n.json", spend) == [code]


def test_the_spend_block_is_not_private_operational_metadata():
    """ق٦٧-٣ يحكم بنشر الإنفاق؛ وكتلتُه بأسماءٍ غير ما يحجبه حارسُ الخصوصية (#188)، فلا يتعارضان."""
    payload = {"agent": "anthropic/claude-opus-5-5", "date": "2026-10-06", "measurement_limits": ["x"],
               "model": "qwen3.5:9b", "spend": CLOUD}
    assert pe.privacy_findings(payload) == set()
    assert pe.privacy_findings({**payload, "spend": {**CLOUD, "input_tokens": 1}}) == {"private_operational_metadata"}


def test_a_malformed_registry_is_refused_by_name():
    assert ps.findings({"enforced_from": "2026-10-06"}, {}) == ["registry_malformed"]
    assert ps.findings({"historical_evidence": []}, {}) == ["registry_malformed"]


def test_the_cli_fails_on_a_finding(tmp_path, capsys):
    registry = tmp_path / "r.json"
    registry.write_text(json.dumps(_registry()), encoding="utf-8")
    probe = tmp_path / "probe"
    probe.mkdir()
    (probe / "x.json").write_text(json.dumps({"date": "2026-10-06", "model": "a/b"}), encoding="utf-8")
    assert ps.main(["--registry", str(registry), "--probe", str(probe)]) == 1
    assert json.loads(capsys.readouterr().out)["findings"] == ["spend_missing:x.json"]
    (probe / "x.json").write_text(json.dumps({"date": "2026-10-06", "model": "a/b", "spend": LOCAL}), encoding="utf-8")
    assert ps.main(["--registry", str(registry), "--probe", str(probe)]) == 0
