from acceptance_m11 import run_case, SyntheticProvider, SYNTHETIC_MODEL, SYNTHETIC_VERSION
from providers.base import ProviderError


def test_synthetic_acceptance_is_mechanism_not_human_judgment(tmp_path):
    report, evidence = run_case(tmp_path.resolve() / "app", model=SYNTHETIC_MODEL,
        model_version=SYNTHETIC_VERSION, provider_factory=SyntheticProvider)
    assert report["passed"] == report["total"] == 22
    assert report["initial_provider_calls"] == report["initial_provider_instances"] == 1
    assert report["replay_provider_calls"] == report["replay_provider_instances"] == 0
    assert report["human_review"] == "not_required_q49" and report["quality_pending"] is True
    assert report["automated_review"] == "pending"
    assert report["browser_rendering"] == "not_tested" and report["release_ready"] is False
    assert evidence["result"]["verification"] == "unverified"


def test_provider_failure_cannot_be_counted_as_success(tmp_path):
    class Broken(SyntheticProvider):
        def complete(self, request):
            raise ProviderError("synthetic_offline", "fixture", False)
    report, evidence = run_case(tmp_path.resolve() / "app", model=SYNTHETIC_MODEL,
        model_version=SYNTHETIC_VERSION, provider_factory=Broken)
    assert report["passed"] < report["total"]
    assert report["checks"]["one_fresh_unverified_answer"] is False
    assert report["release_ready"] is False
    assert evidence["result"]["status"] == "error"
    assert "applied" not in evidence
