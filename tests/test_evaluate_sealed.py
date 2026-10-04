"""#288: مُشغِّلُ المحجوب على محجوبٍ مصطنع — محليٌّ وحده، وبلا معرّفٍ ولا نصٍّ في تقريره.

لا يقرأ هذا الاختبارُ المحجوبَ الحقيقيّ: يبني بنكًا مصطنعًا وبيانَه في مجلّدٍ مؤقّت، ومزوّدين مصطنعين.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from core.contracts import Response, Usage
from evaluation import judge as judge_rules
from evaluation.judge import JudgeRefused
from tools import evaluate_sealed as sealed
from tools.evaluate_sealed import SealedRefused

CANARY_TEXT = "سؤالٌ محجوبٌ مصطنع لا يخرج"


class Provider:
    def __init__(self, model=sealed.FROZEN_ENGINE, answer="نعم", *, local=True):
        self.model, self.answer, self.is_local, self.calls = model, answer, local, 0

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        return Response(self.answer, Usage(3, 1), "complete", 0, provider="synthetic", model_version="fixture")


@pytest.fixture(autouse=True)
def _owner_mac(monkeypatch):
    """الاختباراتُ على لينكس؛ وفحصُ الجهاز نفسُه يُختبر بإبطال هذا في موضعه."""
    monkeypatch.setattr(sealed, "_on_owner_mac", lambda: True)


def _bank(root, tiers=(("tier_a", 6, True), ("tier_b", 4, False))):
    """بنكٌ مصطنع: حالاتٌ بفحصٍ آليّ (exact «نعم») وأخرى بلا فحص، وبيانٌ مختومٌ ببصماتها."""
    sealed_root, files = root / "diwan-sealed" / "kimi_v1", []
    for tier, count, checked in tiers:
        cases = [{"case_id": f"canary_{tier}_{i:04d}", "capability": "synthetic", "critical": False,
                  "reference": "نعم", "rubric": ["الجوابُ نعم"],
                  "checks": [{"kind": "exact", "value": "نعم"}] if checked else [],
                  "messages": [{"role": "user", "content": f"{CANARY_TEXT} {tier} {i}"}]}
                 for i in range(count)]
        suite = {"schema_version": 1, "suite_id": f"canary_suite_{tier}", "split": "development",
                 "description": "مصطنع", "cases": cases}
        path = sealed_root / tier / f"synthetic_{tier}_sealed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
        files.append({"path": f"sealed/{tier}/{path.name}", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "count": count, "kind": "suite", "capabilities": ["synthetic"]})
    manifest = root / "MANIFEST.json"
    manifest.write_text(json.dumps({"generated_at": "2026-10-04", "bank_version": "test", "files": files}),
                        encoding="utf-8")
    return sealed_root, manifest


def _run(tmp_path, provider, **kwargs):
    sealed_root, manifest = _bank(tmp_path)
    return sealed.run_sealed(sealed_root, provider, run_root=tmp_path / "runs", manifest_path=manifest, **kwargs)


def _evidence(model="granite4"):
    return {"protocol_sha256": judge_rules.PROTOCOL_SHA256, "judge": {"model": model},
            "kappa": 0.7, "accuracy": 0.9, "passed": True}


def test_rates_and_wilson_per_tier_without_identifiers_or_text(tmp_path):
    report = _run(tmp_path, Provider())
    assert report["attempts"] == 10 and report["allocation"] == {"tier_a": 6, "tier_b": 4}
    assert report["by_tier"]["tier_a"] == {"attempted": 6, "passes": 6, "failures": 0, "without_checks": 0,
                                           "judged": 0, "errors": 0, "rate": 1.0, "wilson95": [0.6097, 1.0]}
    assert report["by_tier"]["tier_b"]["without_checks"] == 4 and report["by_tier"]["tier_b"]["rate"] == 0.0
    assert report["overall"]["rate"] == 0.6 and report["judge"] is None
    assert "cases_without_automatic_checks_stay_in_the_denominator_as_not_passed_because_no_calibrated_judge" \
        in report["measurement_limits"]
    text = json.dumps(report, ensure_ascii=False)
    assert "canary" not in text and CANARY_TEXT not in text


@pytest.mark.parametrize("provider", [Provider(local=False), Provider(model="glm-4.6:cloud")],
                         ids=["declared_remote", "cloud_name_declared_local"])
def test_a_non_local_engine_is_refused_before_any_sealed_file_is_read(tmp_path, monkeypatch, provider):
    monkeypatch.setattr(sealed, "verify_manifest", lambda *a, **k: pytest.fail("المحجوب قُرئ قبل الرفض"))
    with pytest.raises(SealedRefused) as refused:
        _run(tmp_path, provider)
    assert refused.value.code == "sealed_requires_local_provider" and provider.calls == 0


def test_a_judge_without_passing_calibration_is_refused_before_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(sealed, "verify_manifest", lambda *a, **k: pytest.fail("المحجوب قُرئ قبل الرفض"))
    with pytest.raises(JudgeRefused) as refused:
        _run(tmp_path, Provider(), judge=Provider(model="granite4"), judge_evidence=None)
    assert refused.value.code == "judge_uncalibrated"
    with pytest.raises(SealedRefused) as remote:
        _run(tmp_path, Provider(), judge=Provider(model="granite4", local=False), judge_evidence=_evidence())
    assert remote.value.code == "sealed_requires_local_provider"


def test_a_calibrated_local_judge_scores_only_cases_without_checks(tmp_path):
    judge = Provider(model="granite4", answer="التعليل مصطنع.\nالحكم: correct")
    report = _run(tmp_path, Provider(), judge=judge, judge_evidence=_evidence())
    assert judge.calls == 4
    assert report["by_tier"]["tier_b"]["passes"] == 4 and report["by_tier"]["tier_b"]["judged"] == 4
    assert report["by_tier"]["tier_a"]["judged"] == 0 and report["overall"]["rate"] == 1.0
    assert report["judge"]["model"] == "granite4"


@pytest.mark.parametrize("answer", ["لا أدري", "الحكم: correct\nلكنّ الجوابَ يقلب المعنى، فلا أحكم"],
                         ids=["no_verdict", "verdict_not_on_the_last_line"])
def test_an_unparsed_verdict_is_an_error_not_a_pass(tmp_path, answer):
    report = _run(tmp_path, Provider(), judge=Provider(model="granite4", answer=answer), judge_evidence=_evidence())
    assert report["by_tier"]["tier_b"]["errors"] == 4 and report["by_tier"]["tier_b"]["passes"] == 0


def test_a_failing_judge_run_counts_its_cases_as_errors_without_crashing(tmp_path, monkeypatch):
    real = sealed.evaluate_suite

    def judge_fails(suite, provider, *args, **kwargs):
        if provider.model == "granite4":
            raise sealed.CapabilityError("judge", "synthetic_judge_failure", "عطلٌ مصطنع في المحكِّم")
        return real(suite, provider, *args, **kwargs)
    monkeypatch.setattr(sealed, "evaluate_suite", judge_fails)
    report = _run(tmp_path, Provider(), judge=Provider(model="granite4"), judge_evidence=_evidence())
    assert report["by_tier"]["tier_b"]["errors"] == 4 and report["by_tier"]["tier_a"]["passes"] == 6


def test_a_file_that_does_not_match_the_manifest_is_refused_without_its_name(tmp_path):
    sealed_root, manifest = _bank(tmp_path)
    target = next(sealed_root.rglob("*_sealed.json"))
    target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(SealedRefused) as refused:
        sealed.run_sealed(sealed_root, Provider(), run_root=tmp_path / "runs", manifest_path=manifest)
    assert refused.value.code == "sealed_manifest_mismatch" and target.name not in str(refused.value)


def test_the_sealed_run_never_writes_inside_the_repository(tmp_path):
    sealed_root, manifest = _bank(tmp_path)
    inside = sealed.ROOT / "var" / "sealed-run-must-not-exist"
    with pytest.raises(SealedRefused) as refused:
        sealed.run_sealed(sealed_root, Provider(), run_root=inside, manifest_path=manifest)
    assert refused.value.code == "sealed_run_root_in_repository" and not inside.exists()


def test_the_cli_refuses_a_cloud_engine_by_name_before_any_sandbox_or_sealed_access(monkeypatch, capsys):
    monkeypatch.setattr(sealed, "configure_sandbox_backend", lambda *a: pytest.fail("أُقلعت الخلفيّة قبل الرفض"))
    monkeypatch.setattr(sealed, "verify_manifest", lambda *a, **k: pytest.fail("المحجوب قُرئ قبل الرفض"))
    assert sealed.main(["--model", "glm-4.6:cloud", "--sandbox-receipt", "receipt.json"]) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "code": "sealed_requires_local_provider"}


def test_a_sealed_run_off_the_owner_mac_is_refused_before_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(sealed, "_on_owner_mac", lambda: False)
    monkeypatch.setattr(sealed, "verify_manifest", lambda *a, **k: pytest.fail("المحجوب قُرئ قبل الرفض"))
    with pytest.raises(SealedRefused) as refused:
        _run(tmp_path, Provider())
    assert refused.value.code == "sealed_requires_owner_mac"


@pytest.mark.parametrize("judge", [None, "granite4"], ids=["engine_alone", "engine_and_judge_of_one_family"])
def test_an_engine_other_than_the_frozen_one_is_refused(tmp_path, monkeypatch, judge):
    """ملاحظة Codex على #289: `--model granite4 --judge granite4` كان يُقبل تشغيلًا لـjudge_v1."""
    monkeypatch.setattr(sealed, "verify_manifest", lambda *a, **k: pytest.fail("المحجوب قُرئ قبل الرفض"))
    with pytest.raises(SealedRefused) as refused:
        _run(tmp_path, Provider(model="granite4"), judge=judge and Provider(model=judge),
             judge_evidence=_evidence() if judge else None)
    assert refused.value.code == "sealed_engine_not_frozen"


def test_the_cli_checks_locality_before_reading_any_judge_evidence(tmp_path, capsys):
    """ملاحظة Codex على #289: الدليلُ كان يُقرأ قبل فحص المحليّة، فدليلٌ غائبٌ يرمي استثناءً خامًا."""
    assert sealed.main(["--judge", "glm-4.6:cloud", "--judge-evidence", str(tmp_path / "missing.json")]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "sealed_requires_local_provider"


def test_judge_evidence_is_never_read_from_the_sealed_root_and_a_bad_one_is_named(tmp_path, capsys):
    sealed_root = tmp_path / "diwan-sealed" / "kimi_v1"
    inside = sealed_root / "calibration.json"
    inside.parent.mkdir(parents=True)
    inside.write_text(json.dumps(_evidence()), encoding="utf-8")
    common = ["--judge", "granite4", "--sealed-root", str(sealed_root), "--run-root", str(tmp_path / "runs")]
    assert sealed.main(common + ["--judge-evidence", str(inside)]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "judge_evidence_in_sealed_root"
    assert sealed.main(common + ["--judge-evidence", str(tmp_path / "missing.json")]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "judge_evidence_unreadable"
