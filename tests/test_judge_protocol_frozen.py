"""#288: بروتوكولُ المحكِّم مسجَّلٌ قبل أيّ تشغيل، فلا تتبع العتبةُ النتيجة (ق٦٤، OD3)."""
from __future__ import annotations

import functools
import hashlib
import json

import pytest

from evaluation import judge
from evaluation.judge import JudgeRefused

REGISTERED = "c2425d24938971aadac78f8a33e6be686c88dcda03824bd9cd6056c936441feb"
DATA = json.loads(judge.PROTOCOL.read_text(encoding="utf-8"))


def test_the_protocol_is_registered_before_any_run_and_cannot_change():
    assert hashlib.sha256(judge.PROTOCOL.read_bytes()).hexdigest() == REGISTERED == judge.PROTOCOL_SHA256
    assert DATA["status"] == "registered_not_run" and DATA["protocol_id"] == "judge_v1"
    assert DATA["thresholds"] == {"kappa_min": 0.6, "accuracy_min": 0.85}
    assert set(DATA["families"]["allowed"]) == {"zhipu", "meta", "deepseek", "swiss-ai", "ibm"}
    assert set(DATA["families"]["excluded"]) == {"qwen", "anthropic", "openai", "google", "kimi"}
    assert DATA["calibration"]["split"] == "open_only"
    assert DATA["sealed"]["attempts"] == 120 and DATA["sealed"]["provider"] == "local_only"
    assert DATA["sealed"]["judge"] == {"model": "granite4", "family": "ibm", "local_only": True,
                                       "requires_passing_calibration_evidence": True}
    assert judge.load_protocol() == DATA


def test_a_changed_protocol_is_refused_by_name(tmp_path):
    changed = tmp_path / "judge_v1.json"
    changed.write_text(judge.PROTOCOL.read_text(encoding="utf-8").replace('"kappa_min": 0.6', '"kappa_min": 0.5'),
                       encoding="utf-8")
    with pytest.raises(JudgeRefused) as refused:
        judge.load_protocol(changed)
    assert refused.value.code == "judge_protocol_changed"


@pytest.mark.parametrize("model,family", [
    ("glm-4.6:cloud", "zhipu"), ("meta-llama/Llama-3.3-70B-Instruct:groq", "meta"),
    ("deepseek-v4.1-flash:cloud", "deepseek"), ("swiss-ai/Apertus-70B-Instruct:publicai", "swiss-ai"),
    ("granite4", "ibm"),
])
def test_allowed_judge_families(model, family):
    assert judge.judge_family(model, DATA) == family


@pytest.mark.parametrize("model,code", [
    ("qwen3.5:9b", "judge_family_excluded"), ("claude-opus", "judge_family_excluded"),
    ("gpt-oss:120b-cloud", "judge_family_excluded"), ("gemma3:12b", "judge_family_excluded"),
    ("kimi-k2.6:cloud", "judge_family_excluded"), ("mistral-large-3:675b-cloud", "judge_family_not_allowed"),
    ("unnamed-model", "judge_family_unknown"),
])
def test_engine_developer_and_author_families_never_judge(model, code):
    with pytest.raises(JudgeRefused) as refused:
        judge.judge_family(model, DATA)
    assert refused.value.code == code


def test_the_open_judge_names_a_pinned_hf_provider_or_ollama_com():
    assert judge.open_judge("hf_inference_providers", "meta-llama/Llama-3.3-70B-Instruct:groq", DATA)["family"] == "meta"
    assert judge.open_judge("ollama_com", "glm-4.6:cloud", DATA)["family"] == "zhipu"
    for transport, model, code in (("hf_inference_providers", "meta-llama/Llama-3.3-70B-Instruct",
                                    "judge_provider_unpinned"),
                                   ("openrouter", "glm-4.6", "judge_transport_unsupported")):
        with pytest.raises(JudgeRefused) as refused:
            judge.open_judge(transport, model, DATA)
        assert refused.value.code == code


def test_cohen_kappa_on_known_cases():
    assert judge.cohen_kappa(["correct", "correct", "incorrect", "incorrect"],
                             ["correct", "incorrect", "incorrect", "incorrect"]) == 0.5
    assert judge.cohen_kappa(["correct"] * 4, ["correct"] * 4) is None
    assert judge.cohen_kappa([], []) is None


def _items(agree: int, total: int, source="automatic_checked"):
    truth = ["correct", "incorrect"] * (total // 2)
    return [{"source": source, "split": "open", "label": t,
             "verdict": t if i < agree else ("incorrect" if t == "correct" else "correct")}
            for i, t in enumerate(truth)]


def test_calibration_passes_only_at_the_registered_thresholds():
    passed = judge.calibration_result(_items(18, 20), DATA)
    assert passed["passed"] is True and passed["accuracy"] == 0.9 and passed["kappa"] == 0.8
    failed = judge.calibration_result(_items(16, 20), DATA)
    assert failed["passed"] is False and failed["accuracy"] == 0.8
    assert set(passed["by_source"]) == {"automatic_checked"}


@pytest.mark.parametrize("change,code", [({"split": "sealed"}, "judge_open_only"),
                                         ({"label": None}, "calibration_label_missing"),
                                         ({"verdict": "maybe"}, "calibration_verdict_invalid")])
def test_calibration_is_open_only_and_never_guesses_a_missing_owner_ruling(change, code):
    items = _items(20, 20)
    items[0] = {**items[0], **change}
    with pytest.raises(JudgeRefused) as refused:
        judge.calibration_result(items, DATA)
    assert refused.value.code == code


@functools.cache
def _sample():
    """العيّنةُ تُبنى عند الاختبار لا عند الجمع: فطفرةٌ تكسر بناءَها تُسقط اختباراتها ولا تُسقط الجمعَ كلَّه."""
    return judge.calibration_sample(DATA, REGISTERED)


def _rows(flip: int = 0, **change):
    """صفوفٌ مصطنعةٌ على العيّنة المجمَّدة حالةً حالة؛ والمحكِّمُ يخالف التسمية في أول `flip` منها."""
    rows = [{"source": source, "case": case, "split": "open", "label": ("correct", "incorrect")[i % 2]}
            for source, cases in _sample().items() for i, case in enumerate(cases)]
    for i, row in enumerate(rows):
        row["verdict"] = row["label"] if i >= flip else ("incorrect" if row["label"] == "correct" else "correct")
    rows[0] = {**rows[0], **change}
    return rows


def _evidence(rows=None, **overrides):
    return {"protocol_sha256": REGISTERED, "judge": {"model": "granite4"},
            "rows": _rows() if rows is None else rows, **overrides}


def test_the_calibration_sample_is_frozen_by_the_protocol():
    triage = json.loads(judge.K11_EVIDENCE.read_text(encoding="utf-8"))
    sample = _sample()
    assert sample["k11_owner_ruled"] == sorted(r["id"] for r in triage["real"] + triage["false_positives"])
    assert len(sample["k11_owner_ruled"]) == 23 and len(set(sample["automatic_checked"])) == 100
    assert hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest() == \
        "f69d3c4ef59942e08cddb19d104538a3da820f10266df9d48a627bea262f0bab"


UNCALIBRATED = {
    "missing": lambda: None,
    "scalars_without_rows": lambda: {"protocol_sha256": REGISTERED, "judge": {"model": "granite4"},
                                     "kappa": 0.9, "accuracy": 0.95, "passed": True},
    "infinite_scalars_without_rows": lambda: _evidence([], kappa=float("inf"), accuracy=float("inf"), passed=True),
    "rows_below_threshold_scalars_claim_pass": lambda: _evidence(_rows(flip=30), kappa=0.9, accuracy=0.95,
                                                                 passed=True),
    "one_case_missing": lambda: _evidence(_rows()[1:]),
    "case_not_in_the_sample": lambda: _evidence(_rows(case="tier_x__invented/case_0001")),
    "duplicate_case": lambda: _evidence(_rows(case=_sample()["automatic_checked"][1], source="automatic_checked")),
    "sealed_row": lambda: _evidence(_rows(split="sealed")),
    "owner_ruling_pending": lambda: _evidence(_rows(label=None)),
    "another_model": lambda: _evidence(judge={"model": "granite3"}),
    "another_protocol": lambda: _evidence(protocol_sha256="0" * 64),
}


@pytest.mark.parametrize("case", list(UNCALIBRATED))
def test_a_sealed_judge_needs_its_calibration_rows_on_the_frozen_sample(case):
    """ملاحظتا Codex على #289: دليلٌ بأرقامٍ مكتوبة أو بعيّنةٍ غير المسجَّلة كان يُقبل؛ والأرقامُ تُعاد من الصفوف."""
    with pytest.raises(JudgeRefused) as refused:
        judge.accept_sealed_judge(UNCALIBRATED[case](), "granite4", DATA, REGISTERED, _sample())
    assert refused.value.code == "judge_uncalibrated"


def test_a_calibrated_sealed_judge_is_accepted_from_its_rows_alone():
    assert judge.accept_sealed_judge(_evidence(), "granite4", DATA, REGISTERED, _sample()) == "ibm"
    assert judge.accept_sealed_judge(_evidence(_rows(flip=10)), "granite4", DATA, REGISTERED, _sample()) == "ibm"


@pytest.mark.parametrize("model", ["glm-4.6", "granite4:3b"])
def test_only_the_registered_sealed_judge_may_judge(model):
    with pytest.raises(JudgeRefused) as refused:
        judge.accept_sealed_judge(_evidence(judge={"model": model}), model, DATA, REGISTERED, _sample())
    assert refused.value.code == "judge_not_registered"


def test_attempts_are_allocated_by_tier_size_and_selected_deterministically():
    counts = {"tier_a": 638, "tier_b": 145, "tier_c": 53, "tier_d": 40}
    allocation = judge.allocate(counts, 120)
    assert sum(allocation.values()) == 120
    assert allocation == {"tier_a": 87, "tier_b": 20, "tier_c": 7, "tier_d": 6}
    assert judge.allocate({"tier_a": 3, "tier_b": 2}, 120) == {"tier_a": 3, "tier_b": 2}
    ids = {"tier_a": [f"a{i}" for i in range(10)], "tier_b": [f"b{i}" for i in range(5)]}
    first = judge.select(ids, {"tier_a": 3, "tier_b": 2}, REGISTERED)
    shuffled = {t: list(reversed(v)) for t, v in ids.items()}
    assert judge.select(shuffled, {"tier_a": 3, "tier_b": 2}, REGISTERED) == first
    assert [len(v) for v in first.values()] == [3, 2]


def test_wilson_interval_on_a_known_case_and_a_rate_on_every_attempt():
    rows = ([{"tier": "tier_a", "outcome": "pass"}] * 8 + [{"tier": "tier_a", "outcome": "fail"}]
            + [{"tier": "tier_a", "outcome": "without_checks"}])
    report = judge.tier_report(rows)
    assert report["by_tier"]["tier_a"]["rate"] == 0.8
    assert report["by_tier"]["tier_a"]["wilson95"] == [0.4902, 0.9433]
    assert report["by_tier"]["tier_a"]["without_checks"] == 1
    assert report["overall"] == {"attempted": 10, "passes": 8, "rate": 0.8, "wilson95": [0.4902, 0.9433]}


def test_a_report_carrying_an_identifier_or_a_text_is_refused():
    clean = {"by_tier": {"tier_a": {"rate": 0.5}}}
    judge.assert_clean(clean, ["canary_case_0001"], ["نصٌّ محجوبٌ مصطنع للاختبار"])
    for report in ({**clean, "cases": ["canary_case_0001"]}, {**clean, "note": "نصٌّ محجوبٌ مصطنع للاختبار"}):
        with pytest.raises(JudgeRefused) as refused:
            judge.assert_clean(report, ["canary_case_0001"], ["نصٌّ محجوبٌ مصطنع للاختبار"])
        assert refused.value.code == "sealed_output_leak"
