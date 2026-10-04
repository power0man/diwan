"""#288: بروتوكولُ المحكِّم مسجَّلٌ قبل أيّ تشغيل، فلا تتبع العتبةُ النتيجة (ق٦٤، OD3)."""
from __future__ import annotations

import copy
import functools
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.signing import SigningRefused
from evaluation import judge
from evaluation.judge import JudgeRefused

REGISTERED = "f2ecbb23f78674270bac9845fcb465608a306940a2bcd6243c0c592f1e914f78"
DATA = json.loads(judge.PROTOCOL.read_text(encoding="utf-8"))
# مفتاحُ مالكٍ مصطنعٌ للاختبار وحده؛ ومفتاحُ المالك الحقيقيّ في سلسلة مفاتيح الماك لا في المستودع.
OWNER_SEED = hashlib.sha256(b"diwan-test-owner-calibration").digest()
OTHER_SEED = hashlib.sha256(b"diwan-test-not-the-owner").digest()
# بصمةُ المحرّك المسجَّلة، وبصمةُ محكِّمٍ مصطنعة كما يحلّها Ollama على الماك.
ENGINE_DIGEST = DATA["sealed"]["engine_digest"]
JUDGE_DIGEST = hashlib.sha256(b"synthetic-granite4-weights").hexdigest()
OWNER_PUBLIC = Ed25519PrivateKey.from_private_bytes(OWNER_SEED).public_key().public_bytes(Encoding.Raw,
                                                                                          PublicFormat.Raw)


def test_the_protocol_is_registered_before_any_run_and_cannot_change():
    assert hashlib.sha256(judge.PROTOCOL.read_bytes()).hexdigest() == REGISTERED == judge.PROTOCOL_SHA256
    assert DATA["status"] == "registered_not_run" and DATA["protocol_id"] == "judge_v1"
    assert DATA["thresholds"] == {"kappa_min": 0.6, "accuracy_min": 0.85}
    assert set(DATA["families"]["allowed"]) == {"zhipu", "meta", "deepseek", "swiss-ai", "ibm"}
    assert set(DATA["families"]["excluded"]) == {"qwen", "anthropic", "openai", "google", "kimi"}
    assert DATA["calibration"]["split"] == "open_only"
    assert DATA["calibration"]["open_judges"]["required"] == 2 and DATA["calibration"]["open_judges"]["distinct_families"]
    assert DATA["calibration"]["open_judges"]["families"] == ["zhipu", "meta", "deepseek", "swiss-ai"]
    assert DATA["sealed"]["attempts"] == 120 and DATA["sealed"]["provider"] == "local_only"
    assert DATA["sealed"]["judge"] == {"model": "granite4", "family": "ibm", "local_only": True,
                                       "requires_passing_calibration_evidence": True,
                                       "digest": "the_one_signed_in_the_calibration_evidence"}
    assert DATA["sealed"]["engine_digest"] == "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
    assert DATA["sealed"]["engine_model"] == "qwen3.5:9b"
    assert DATA["sealed"]["runtime"] == {
        "max_output": 800, "deadline_s": 240, "seed": 0, "temperature": 0, "allow_thinking": False,
        "context_tokens": 32768, "ollama_base_url": "http://127.0.0.1:11434", "quarantine_quoted_material": False,
        "system_sha256": "e7eee5f5d1b7f1e9917b1067af75b5f66cda4f26e86462eacdf31657d499fbff",
        "verdict_pattern_sha256": "cc6dd82c3aeb72ea3d92f5d10b8d95dd535c523f4bf852600030195bd5e5a3ea",
        "judge_prompt_sha256": "791f50552e41b6adf17cadf2b95e64a05732c15b16c145eeac11a102abfb7421"}
    manifest = judge.ROOT / DATA["sealed"]["manifest"]
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == DATA["sealed"]["manifest_sha256"]
    assert judge.open_bank_digest() == DATA["calibration"]["open_bank_sha256"]
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


HF_META = ("hf_inference_providers", "meta-llama/Llama-3.3-70B-Instruct:groq")
OLLAMA_GLM = ("ollama_com", "glm-4.6:cloud")


@pytest.mark.parametrize("panel,code", [
    ([HF_META, ("ollama_com", "llama3.3:70b-cloud")], "open_judge_panel_same_family"),
    ([OLLAMA_GLM], "open_judge_panel_size"),
    ([HF_META, OLLAMA_GLM, ("ollama_com", "deepseek-v4.1-flash:cloud")], "open_judge_panel_size"),
    ([HF_META, ("ollama_com", "qwen3.5:9b")], "judge_family_excluded"),
    ([("ollama_com", "granite4"), OLLAMA_GLM], "open_judge_family_not_listed"),
], ids=["same_family", "one_judge", "three_judges", "excluded_family", "sealed_judge_as_open_judge"])
def test_the_open_judges_are_the_registered_number_of_distinct_allowed_families(panel, code):
    """ملاحظة Codex على #289: محكِّما المفتوح (#30) اثنان من عائلتين مسموحتين مختلفتين، كما سجّلهما judge_v1؛ ولا يخصّان
    قبولَ محكِّم المحجوب."""
    assert [j["family"] for j in judge.open_judge_panel([HF_META, OLLAMA_GLM], DATA)] == ["meta", "zhipu"]
    with pytest.raises(JudgeRefused) as refused:
        judge.open_judge_panel(panel, DATA)
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


def test_the_threshold_is_compared_before_rounding():
    """ملاحظة Codex على #289: على ١٢٣ صفًّا، TP=18 وFN=1 وFP=16 وTN=88 تعطي κ = 0.59996 ودقّةً 0.8618؛ والتقريبُ إلى
    0.6 قبل العتبة كان يُنجح محكِّمًا دونها. والمنشورُ يبقى مقرَّبًا."""
    pairs = ([("correct", "correct")] * 18 + [("correct", "incorrect")] * 1
             + [("incorrect", "correct")] * 16 + [("incorrect", "incorrect")] * 88)
    items = [{"source": "automatic_checked", "split": "open", "label": label, "verdict": verdict}
             for label, verdict in pairs]
    result = judge.calibration_result(items, DATA)
    assert result["kappa"] == 0.6 and result["accuracy"] == 0.8618 and result["passed"] is False


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


@functools.cache
def _truth():
    return judge.calibration_truth(DATA, _sample())


def _candidate(checks: list[dict], correct: bool) -> str:
    """جوابٌ تنجح فيه فحوصُ الحالة أو ترسب؛ فالتسميةُ الآليّة تُعاد منه لا تُكتب."""
    if not correct:
        return "—"
    check = checks[0]
    return check["value"] if check["kind"] == "exact" else json.dumps(check["value"], ensure_ascii=False)


def _rows(flip: int = 0, at: int = 0, **change):
    """صفوفٌ مصطنعةٌ على العيّنة المجمَّدة حالةً حالة: جوابُ ك١١ مرجعُه، وجوابُ الآليّة يُطابق تسميتَه بفحوصه؛
    والمحكِّمُ يخالف التسمية في أول `flip` منها، و`change` يُطبَّق على الصفّ `at`."""
    rows = []
    for source, cases in _sample().items():
        for i, case in enumerate(cases):
            label = ("correct", "incorrect")[i % 2]
            ground = _truth()[source][case]
            candidate = ground if source == "k11_owner_ruled" else _candidate(ground, label == "correct")
            rows.append({"source": source, "case": case, "split": "open", "candidate": candidate, "label": label})
    for i, row in enumerate(rows):
        row["verdict"] = row["label"] if i >= flip else ("incorrect" if row["label"] == "correct" else "correct")
    rows[at] = {**rows[at], **change}
    return rows


# صفوفُ ك١١ أولًا بترتيب العيّنة، ثم الآليّة.
FIRST_AUTOMATIC = next(s["cases"] for s in DATA["calibration"]["sources"] if s["name"] == "k11_owner_ruled")


def _evidence(rows=None, *, seed=OWNER_SEED, **overrides):
    """دليلُ معايرةٍ موقَّعٌ بمفتاح المالك المصطنع؛ و`seed=None` يتركه بلا توقيع."""
    evidence = {"protocol_sha256": REGISTERED, "judge": {"model": "granite4", "digest": JUDGE_DIGEST},
                "engine": {"model": "qwen3.5:9b", "digest": ENGINE_DIGEST},
                "rows": _rows() if rows is None else rows, **overrides}
    return evidence if seed is None else judge.sign_calibration(evidence, private_seed=seed)


def _tampered():
    evidence = copy.deepcopy(_evidence())
    evidence["rows"][0]["verdict"] = "incorrect" if evidence["rows"][0]["verdict"] == "correct" else "correct"
    return evidence


def _hand_labelled():
    """سيناريو Codex: صفوفٌ تعدّد العيّنةَ وتُناوب التسمياتِ يدويًّا وتنسخها إلى الحكم، وأجوبتُها كلُّها صحيحة."""
    rows = _rows()
    for row in rows[FIRST_AUTOMATIC:]:
        row["candidate"] = _candidate(_truth()["automatic_checked"][row["case"]], True)
    return _evidence(rows)


def test_the_calibration_sample_is_frozen_by_the_protocol():
    triage = json.loads(judge.K11_EVIDENCE.read_text(encoding="utf-8"))
    sample = _sample()
    assert sample["k11_owner_ruled"] == sorted(r["id"] for r in triage["real"] + triage["false_positives"])
    assert len(sample["k11_owner_ruled"]) == 23 and len(set(sample["automatic_checked"])) == 100
    assert hashlib.sha256(json.dumps(sample, sort_keys=True).encode()).hexdigest() == \
        "3daebc2aef5b9f3aa04b70ebcefd4d1349aa6a0c9c44d14412cde30794876f2b"
    kinds = {check["kind"] for checks in _truth()["automatic_checked"].values() for check in checks}
    assert kinds <= {"exact", "json_equals"} and all(isinstance(r, str) for r in _truth()["k11_owner_ruled"].values())


def test_a_changed_open_bank_is_refused_by_name(tmp_path):
    """ملاحظة Codex على #289: بنكٌ استُبدل (v1.2) لا يُعايَر عليه باسم judge_v1؛ وبايتٌ واحدٌ يكفي ليُردّ."""
    import shutil
    bank = tmp_path / "open"
    shutil.copytree(judge.OPEN_BANK, bank)
    target = sorted(bank.glob("*/*.json"))[0]
    target.write_bytes(target.read_bytes() + b" ")
    for build in (lambda: judge.calibration_sample(DATA, REGISTERED, open_bank=bank),
                  lambda: judge.calibration_truth(DATA, _sample(), open_bank=bank)):
        with pytest.raises(JudgeRefused) as refused:
            build()
        assert refused.value.code == "calibration_bank_changed"


def test_the_owner_signs_a_domain_separated_message_without_its_signature():
    evidence = _evidence()
    assert judge.calibration_message(evidence) == b"diwan-judge-calibration-v1\x00" + json.dumps(
        {k: v for k, v in evidence.items() if k != "owner_signature"}, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")


UNCALIBRATED = {
    "missing": lambda: None,
    "scalars_without_rows": lambda: _evidence([], kappa=0.9, accuracy=0.95, passed=True),
    "infinite_scalars_without_rows": lambda: _evidence([], seed=None, kappa=float("inf"), accuracy=float("inf"),
                                                       passed=True),
    "rows_below_threshold_scalars_claim_pass": lambda: _evidence(_rows(flip=30), kappa=0.9, accuracy=0.95,
                                                                 passed=True),
    "one_case_missing": lambda: _evidence(_rows()[1:]),
    "case_not_in_the_sample": lambda: _evidence(_rows(case="tier_x__invented/case_0001")),
    "duplicate_case": lambda: _evidence(_rows(case=_sample()["automatic_checked"][1], source="automatic_checked")),
    "sealed_row": lambda: _evidence(_rows(split="sealed")),
    "owner_ruling_pending": lambda: _evidence(_rows(label=None)),
    "another_model": lambda: _evidence(judge={"model": "granite3"}),
    "another_protocol": lambda: _evidence(protocol_sha256="0" * 64),
    "judge_tag_repointed_after_calibration": lambda: _evidence(judge={"model": "granite4", "digest": "0" * 64}),
    "calibrated_on_another_engine": lambda: _evidence(engine={"model": "qwen3.5:9b", "digest": "0" * 64}),
    "unsigned": lambda: _evidence(seed=None),
    "signed_by_another_key": lambda: _evidence(seed=OTHER_SEED),
    "changed_after_signing": _tampered,
    "hand_labelled_codex_scenario": _hand_labelled,
    "automatic_label_not_its_checks": lambda: _evidence(_rows(at=FIRST_AUTOMATIC, label="incorrect",
                                                              verdict="incorrect")),
    "automatic_candidate_missing": lambda: _evidence(_rows(at=FIRST_AUTOMATIC + 1, candidate=None)),
    "k11_candidate_not_its_reference": lambda: _evidence(_rows(candidate="مرجعٌ آخر")),
}


@pytest.mark.parametrize("case", list(UNCALIBRATED))
def test_a_sealed_judge_needs_its_calibration_rows_on_the_frozen_sample(case):
    """ملاحظاتُ Codex على #289: دليلٌ بأرقامٍ مكتوبة، أو بعيّنةٍ غير المسجَّلة، أو بتسمياتٍ مكتوبةٍ لا تُعاد من حقيقة
    حالاتها، أو بلا توقيع المالك، كان يُقبل؛ والأرقامُ تُعاد من صفوفٍ مربوطةٍ موقَّعة."""
    with pytest.raises(JudgeRefused) as refused:
        judge.accept_sealed_judge(UNCALIBRATED[case](), "granite4", DATA, REGISTERED, _truth(),
                                  judge_digest=JUDGE_DIGEST, public_key=OWNER_PUBLIC)
    assert refused.value.code == "judge_uncalibrated"


def test_a_calibrated_sealed_judge_is_accepted_from_its_rows_alone():
    for evidence in (_evidence(), _evidence(_rows(flip=10))):
        assert judge.accept_sealed_judge(evidence, "granite4", DATA, REGISTERED, _truth(),
                                         judge_digest=JUDGE_DIGEST, public_key=OWNER_PUBLIC) == "ibm"
    assert _rows()[FIRST_AUTOMATIC]["source"] == "automatic_checked" == _rows()[FIRST_AUTOMATIC + 1]["source"]
    assert _rows()[FIRST_AUTOMATIC + 1]["label"] == "incorrect"


def test_the_trusted_owner_key_is_the_default_and_an_unverifiable_one_is_named(monkeypatch):
    """بلا مفتاحٍ مُمرَّر يُتحقَّق بالمفتاح المُثبَّت في المستودع، فتوقيعُ مفتاحٍ مصطنع لا يمرّ؛ وتعذُّرُ قراءة المفتاح
    «تعذّر» مسمًّى لا «لم يُوقَّع» (ق٣٩)."""
    with pytest.raises(JudgeRefused) as refused:
        judge.accept_sealed_judge(_evidence(), "granite4", DATA, REGISTERED, _truth(), judge_digest=JUDGE_DIGEST)
    assert refused.value.code == "judge_uncalibrated"

    def no_policy():
        raise SigningRefused("signing_policy_missing", "synthetic")
    monkeypatch.setattr(judge, "load_trusted_public_key", no_policy)
    with pytest.raises(JudgeRefused) as refused:
        judge.accept_sealed_judge(_evidence(), "granite4", DATA, REGISTERED, _truth(), judge_digest=JUDGE_DIGEST)
    assert refused.value.code == "calibration_signature_unverifiable"


def test_an_unresolved_judge_digest_never_matches_an_evidence_without_one():
    """بصمةٌ لم تُحلّ (None) لا تطابق دليلًا وُقِّع بلا بصمة: فالدليلُ يسمّي أوزانًا بعينها أو لا يُقبل."""
    evidence = _evidence(judge={"model": "granite4", "digest": None})
    with pytest.raises(JudgeRefused) as refused:
        judge.accept_sealed_judge(evidence, "granite4", DATA, REGISTERED, _truth(), judge_digest=None,
                                  public_key=OWNER_PUBLIC)
    assert refused.value.code == "judge_uncalibrated"


@pytest.mark.parametrize("model", ["glm-4.6", "granite4:3b"])
def test_only_the_registered_sealed_judge_may_judge(model):
    with pytest.raises(JudgeRefused) as refused:
        judge.accept_sealed_judge(_evidence(judge={"model": model}), model, DATA, REGISTERED, _truth(),
                                  judge_digest=JUDGE_DIGEST,
                                  public_key=OWNER_PUBLIC)
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
