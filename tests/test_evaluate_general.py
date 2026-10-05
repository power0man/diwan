"""الرقمُ العام (ق٧٣ الخطوة 1.7أ): كاملُ المفتوح عبر ذراع الأساس الوكيل ببذورٍ وWilson، والأعطالُ خارج المقام."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from core.contracts import Response, Usage
from evaluation.ablation import AblationError, arm
from services.agent_workspace import decode_input

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from providers.ollama import DEFAULT_MODEL  # noqa: E402
from tools.evaluate_general import (V11_OPEN_DIGEST, LicenseRefused, registered_license, run_general,  # noqa: E402
                                    summarize)
from tools import probe_spend  # noqa: E402

RUN = {"model": DEFAULT_MODEL, "model_version": "v1", "engine_license": "Apache-2.0", "date": "2026-10-05"}


def _intake(bank, **changes):
    """تقريرُ استلامٍ ناجح لهذا البنك بعينه، كما يكتبه tools/kimi_intake.py."""
    from evaluation.judge import open_bank_digest
    report = {"passed": True, "open_only": True, "replacement": {"failures": [], "baseline_digest": V11_OPEN_DIGEST},
              "bank": {"without_checks": {"open": 0, "sealed": 0}, "open_digest": open_bank_digest(bank)}}
    return {**report, **changes}


def _case(case_id, text, checks):
    return {"case_id": case_id, "capability": "general", "critical": False, "reference": "", "rubric": "",
            "messages": [{"role": "user", "content": text}], "checks": checks}


def _bank(root: Path, tiers: dict[str, list[dict]]) -> Path:
    for tier, cases in tiers.items():
        (root / tier).mkdir(parents=True)
        (root / tier / "s.json").write_text(json.dumps({"schema_version": 1, "suite_id": tier, "split": "development",
                                                        "description": "d", "cases": cases}, ensure_ascii=False))
    return root


class SeedReplay:
    """يجيب بحسب البذرة والسؤال، ويسجّل البذورَ والأدواتَ التي بلغته."""
    name, is_local, model = "replay", True, "replay"

    def __init__(self, answer, *, seed=0, seen=None):
        self.answer, self.seed, self.seen = answer, seed, [] if seen is None else seen

    def with_seed(self, seed):
        return type(self)(self.answer, seed=seed, seen=self.seen)

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        user = decode_input(request.messages[-1].content)["user_request"]
        self.seen.append((self.seed, bool(request.tools)))
        return Response(self.answer(user, self.seed), Usage(3, 2), "complete", 0, provider="replay",
                        model_version="v1")


def test_the_general_number_runs_every_checked_case_through_the_seeded_agentic_baseline(tmp_path):
    rabat = [{"kind": "contains", "value": "الرباط"}]
    bank = _bank(tmp_path / "open", {
        "tier_a": [_case(f"a{i}", "ما عاصمة المغرب؟", rabat) for i in range(4)] + [_case("none", "بلا فحص", [])],
        "tier_b": [_case("b0", "عاصمة المغرب؟", rabat), _case("b1", "سؤالٌ آخر", rabat)],
    })

    def answer(user, seed):                       # a0..a3 تنجح دائمًا؛ b0 تنجح في بذرتين من ثلاث؛ b1 لا تنجح
        if user == "سؤالٌ آخر":
            return "فاس"
        if user == "عاصمة المغرب؟":
            return "الرباط" if seed in (0, 2) else "فاس"
        return "الرباط"

    seen = []
    report = run_general(SeedReplay(answer, seen=seen), bank_open=bank, diagnostic=True,
                         command="python3 tools/evaluate_general.py --model replay", **RUN)
    assert report["tool"] == "tools/evaluate_general.py" and report["command"].startswith("python3 tools/")
    assert report["config"]["seeds"] == [0, 1, 2] and report["config"]["arm"] == arm()
    assert report["config"]["bank"]["cases_measured"] == 6
    assert report["config"]["bank"]["cases_without_automatic_check"] == 1
    assert report["config"]["bank"]["sandbox_cases_excluded"] == 0
    assert {seed for seed, _ in seen} == {0, 1, 2} and all(tools for _, tools in seen)   # الأدواتُ معلنة: الطريقُ الوكيل
    assert report["overall"] == {"offered": 6, "measured": 6, "passes": 5, "error_count": 0,
                                 "pass_rate": round(5 / 6, 4), "wilson95": report["overall"]["wilson95"]}
    low, high = report["overall"]["wilson95"]
    assert 0 < low < 5 / 6 < high <= 1
    assert report["by_tier"]["tier_a"]["passes"] == 4 and report["by_tier"]["tier_b"]["passes"] == 1
    assert any("temperature_0" in limit for limit in report["measurement_limits"])
    # رخصةٌ وإنفاقٌ بشكلَي حارسَيهما، فلا يردّ CI دليلَ ليلة القياس (ملاحظة Codex على #312)
    calls = len(seen)
    assert report["licenses"] == {DEFAULT_MODEL: "Apache-2.0"} and report["config"]["engine_calls"] == calls
    assert report["spend"] == {"cloud_calls": 0, "prompt_tokens": 3 * calls, "completion_tokens": 2 * calls,
                               "cost_usd": 0, "cost_basis": "local_no_charge"}
    assert probe_spend.spend_findings("k2c.json", report["spend"]) == []


def test_errored_cases_leave_the_denominator_and_are_counted_not_failed():
    rows = [{"status": "measured", "passed": True}, {"status": "measured", "passed": False},
            {"status": "error", "code": "seed_run_error"}]
    summary = summarize(rows)
    assert (summary["offered"], summary["measured"], summary["passes"], summary["error_count"]) == (3, 2, 1, 1)
    assert summary["pass_rate"] == 0.5
    # طبقةٌ عَطبت كلُّها لا رقمَ لها ولا فاصل، فلا يُنشر الانقطاعُ رسوبًا تامًّا
    outage = summarize([{"status": "error", "code": "seed_run_error"}] * 2)
    assert (outage["pass_rate"], outage["wilson95"], outage["error_count"]) == (None, None, 2)


def test_an_empty_bank_and_even_seeds_are_refused_by_name(tmp_path):
    empty = _bank(tmp_path / "open", {"tier_a": [_case("none", "بلا فحص", [])]})
    with pytest.raises(AblationError, match="bank_empty"):
        run_general(SeedReplay(lambda u, s: ""), bank_open=empty, diagnostic=True, **RUN)
    with pytest.raises(AblationError, match="seeds_invalid"):
        run_general(SeedReplay(lambda u, s: ""), bank_open=empty, seeds=(0, 1), **RUN)


def test_sandbox_checked_cases_are_not_counted_as_unchecked(tmp_path):
    """بلا حاويةٍ تخرج حالاتُ python_sandbox، وتُعدّ منفصلةً عن الحالات التي لا فحصَ لها أصلًا (ملاحظة Codex على #312)."""
    sandboxed = {**_case("py", "اكتب دالة", []), "checks": [{"kind": "python_sandbox", "code": "assert True"}]}
    bank = _bank(tmp_path / "open", {"tier_a": [_case("a", "ما عاصمة المغرب؟", [{"kind": "contains", "value": "الرباط"}]),
                                                _case("none", "بلا فحص", []), sandboxed]})
    report = run_general(SeedReplay(lambda u, s: "الرباط"), bank_open=bank, sandbox=False, diagnostic=True, **RUN)
    counts = report["config"]["bank"]
    assert (counts["cases_measured"], counts["cases_without_automatic_check"], counts["sandbox_cases_excluded"]) == (1, 1, 1)


def test_a_model_whose_license_is_unread_is_refused_before_any_call(tmp_path):
    registry = tmp_path / "licenses.json"
    registry.write_text(json.dumps({"models": {"qwen3.5:9b": {"pending": "read_with_ollama_show_license_on_the_mac"},
                                               "granite4": {"license": "Apache-2.0", "source": "https://x",
                                                            "read_on": "2026-10-01"}}}), encoding="utf-8")
    with pytest.raises(LicenseRefused, match="license_not_read"):
        registered_license("qwen3.5:9b", registry)
    with pytest.raises(LicenseRefused, match="model_not_in_registry"):
        registered_license("unknown:1b", registry)
    assert registered_license("ollama:granite4", registry) == "Apache-2.0"


def test_a_cloud_model_is_refused_before_any_call(tmp_path):
    """نموذجٌ سحابيٌّ يمرّ عبر Ollama المحليّ إلى السحابة، فلا يُقاس باسم المحليّ ولا يُكتب إنفاقُه صفرًا (ملاحظة Codex على #312)."""
    bank = _bank(tmp_path / "open", {"tier_a": [_case("a", "ما عاصمة المغرب؟", [{"kind": "contains", "value": "الرباط"}])]})
    seen = []
    with pytest.raises(AblationError, match="provider_not_local"):
        run_general(SeedReplay(lambda u, s: "الرباط", seen=seen), bank_open=bank,
                    **{**RUN, "model": "gpt-oss:120b-cloud"})

    class Remote(SeedReplay):
        is_local = False
    with pytest.raises(AblationError, match="provider_not_local"):
        run_general(Remote(lambda u, s: "الرباط", seen=seen), bank_open=bank, **RUN)
    assert seen == []


def test_strict_and_trimmed_exact_readings_are_published_together(tmp_path):
    """ق٥٧: «بليغ.» يسقط في الصارم ويُعدّ في المشذَّب، والرقمُ الصارم هو الرقم؛ ولا يمسّ التشذيبُ غيرَ exact."""
    bank = _bank(tmp_path / "open", {"tier_a": [
        _case("dot", "صف الأسلوب", [{"kind": "exact", "value": "بليغ"}]),
        _case("wrong", "صف الأسلوب", [{"kind": "exact", "value": "ركيك"}]),
        _case("ok", "ما عاصمة المغرب؟", [{"kind": "contains", "value": "الرباط"}])]})
    report = run_general(SeedReplay(lambda u, s: "الرباط" if "المغرب" in u else "بليغ."), bank_open=bank,
                         intake=_intake(bank), **RUN)
    readings = report["exact_readings"]
    assert readings["strict"]["passes"] == report["overall"]["passes"] == 1
    assert readings["lenient"]["passes"] == 2 and readings["lost_to_trailing_punctuation"] == 1
    assert readings["lenient"]["wilson95"] is not None


def test_a_bank_with_unchecked_cases_is_refused_unless_the_run_is_declared_diagnostic(tmp_path):
    """رقمُ م١ على v1.2 بصفر حالةٍ بلا فحص؛ وv1.1 فيه ١٥٠، فلا يُقاس باسمه إلا تشخيصًا موسومًا (ملاحظة Codex على #312)."""
    rabat = [{"kind": "contains", "value": "الرباط"}]
    bank = _bank(tmp_path / "open", {"tier_a": [_case("a", "ما عاصمة المغرب؟", rabat), _case("none", "بلا فحص", [])]})
    seen = []
    with pytest.raises(AblationError, match="bank_has_unchecked_cases"):
        run_general(SeedReplay(lambda u, s: "الرباط", seen=seen), bank_open=bank, **RUN)
    assert seen == []
    report = run_general(SeedReplay(lambda u, s: "الرباط"), bank_open=bank, diagnostic=True, **RUN)
    assert report["kind"] == "general_number_diagnostic"
    assert "diagnostic_run_not_m1_gate_evidence" in report["measurement_limits"]


def test_the_run_is_bound_to_a_passed_intake_of_this_very_bank(tmp_path):
    """بنكٌ بلا حالةٍ بلا فحص لا يكفي: يلزم استلامٌ ناجحٌ بصمتُه بصمةُ البنك، فلا يُقاس بنكٌ جزئيّ أو غريب باسم v1.2."""
    rabat = [{"kind": "contains", "value": "الرباط"}]
    bank = _bank(tmp_path / "open", {"tier_a": [_case("a", "ما عاصمة المغرب؟", rabat)]})
    other = _bank(tmp_path / "other", {"tier_a": [_case("b", "عاصمة المغرب؟", rabat)]})
    seen = []
    replay = SeedReplay(lambda u, s: "الرباط", seen=seen)
    for intake, code in ((None, "intake_missing"), (_intake(bank, passed=False), "intake_not_passed"),
                         (_intake(bank, open_only=False), "intake_not_v12_replacement"),
                         (_intake(bank, replacement={"failures": [{"code": "case_missing"}]}), "intake_not_v12_replacement"),
                         # فحصُ استبدالٍ ناجح على بنكٍ قائمٍ غيرِ v1.1 (`--current` من حالةٍ واحدة) لا يشهد (ملاحظة Codex)
                         (_intake(bank, replacement={"failures": [], "baseline_digest": "0" * 64}),
                          "intake_baseline_not_v11"),
                         (_intake(bank, replacement={"failures": []}), "intake_baseline_not_v11"),
                         (_intake(other), "intake_digest_mismatch")):
        with pytest.raises(AblationError, match=code):
            run_general(replay, bank_open=bank, intake=intake, **RUN)
    assert seen == []
    report = run_general(replay, bank_open=bank, intake=_intake(bank), **RUN)
    assert report["kind"] == "general_number" and report["config"]["open_bank_digest"] == _intake(bank)["bank"]["open_digest"]


def test_a_run_without_the_check_container_is_only_diagnostic(tmp_path):
    """بلا حاويةٍ تخرج حالاتُ python_sandbox، فلا يُسمّى الرقمُ رقمَ البنك (ملاحظة Codex على #312)."""
    bank = _bank(tmp_path / "open", {"tier_a": [_case("a", "ما عاصمة المغرب؟", [{"kind": "contains", "value": "الرباط"}])]})
    with pytest.raises(AblationError, match="sandbox_required"):
        run_general(SeedReplay(lambda u, s: "الرباط"), bank_open=bank, sandbox=False, intake=_intake(bank), **RUN)


def test_the_gate_number_is_on_the_frozen_default_engine_and_any_other_model_is_diagnostic(tmp_path):
    """رقمُ البوابة على المحرّك المجمَّد (ق٥٤)؛ ومحرّكٌ آخر محليٌّ يُقاس تشخيصًا موسومًا لا دليلًا (ملاحظة Codex على #312)."""
    bank = _bank(tmp_path / "open", {"tier_a": [_case("a", "ما عاصمة المغرب؟", [{"kind": "contains", "value": "الرباط"}])]})
    seen = []
    other = {**RUN, "model": "gemma4:e4b"}
    with pytest.raises(AblationError, match="engine_not_frozen_default"):
        run_general(SeedReplay(lambda u, s: "الرباط", seen=seen), bank_open=bank, intake=_intake(bank), **other)
    assert seen == []
    report = run_general(SeedReplay(lambda u, s: "الرباط"), bank_open=bank, diagnostic=True, **other)
    assert report["kind"] == "general_number_diagnostic"
    assert "diagnostic_run_not_m1_gate_evidence" in report["measurement_limits"]
    assert run_general(SeedReplay(lambda u, s: "الرباط"), bank_open=bank, intake=_intake(bank), **RUN)["kind"] == \
        "general_number"
