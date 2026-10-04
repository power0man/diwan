"""#288: مُشغِّلُ المحجوب على محجوبٍ مصطنع — محليٌّ وحده، وبلا معرّفٍ ولا نصٍّ في تقريره.

لا يقرأ هذا الاختبارُ المحجوبَ الحقيقيّ: يبني بنكًا مصطنعًا وبيانَه في مجلّدٍ مؤقّت، ومزوّدين مصطنعين.
"""
from __future__ import annotations

import hashlib
import json
import re

import pytest

from core.contracts import Response, Usage
from evaluation import judge as judge_rules
from evaluation.judge import JudgeRefused
from core import signing
from evaluation import capabilities
from providers import ollama as ollama_provider
from providers.ollama import OllamaProvider
from tests.test_judge_protocol_frozen import (DATA, ENGINE_DIGEST, JUDGE_DIGEST, OTHER_SEED, OWNER_PUBLIC, OWNER_SEED,
                                              _evidence)
from tools import evaluate_sealed as sealed
from tools import model_digest
from tools.evaluate_sealed import SealedRefused

CANARY_TEXT = "سؤالٌ محجوبٌ مصطنع لا يخرج"
FROZEN_ENGINE = DATA["sealed"]["engine_model"]
# ما يحلّه Ollama على الماك: المحرّكُ ببصمته المسجَّلة، والمحكِّمُ ببصمته الموقَّعة في الدليل المصطنع.
DIGESTS = {FROZEN_ENGINE: ENGINE_DIGEST, "granite4": JUDGE_DIGEST}


ENDPOINT = DATA["sealed"]["runtime"]["ollama_base_url"]


class Provider:
    def __init__(self, model=FROZEN_ENGINE, answer="نعم", *, local=True, seed=0, allow_thinking=False,
                 temperature=0, base_url=ENDPOINT):
        self.model, self.answer, self.is_local, self.calls = model, answer, local, 0
        self.seed, self.allow_thinking, self.temperature, self.base_url = seed, allow_thinking, temperature, base_url

    def payload(self, request):
        """كما يبني OllamaProvider خياراته؛ فالفحصُ يقرأ ما يُرسل لا ما يُظنّ."""
        return {"options": {"num_predict": request.max_output, "temperature": self.temperature,
                            "num_ctx": ollama_provider.CONTEXT_TOKENS, "seed": self.seed}}

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        return Response(self.answer, Usage(3, 1), "complete", 0, provider="synthetic", model_version="fixture")


REAL_ON_OWNER_MAC = sealed._on_owner_mac


@pytest.fixture(autouse=True)
def _owner_mac(monkeypatch):
    """الاختباراتُ على لينكس؛ وفحصُ الجهاز نفسُه يُختبر بإبطال هذا في موضعه. ومفتاحُ المالك المصطنع يحلّ محلّ
    المُثبَّت، فدليلُ المعايرة الموقَّع به في الاختبار يمرّ، ولا يمرّ به دليلٌ حقيقيّ."""
    monkeypatch.setattr(sealed, "_on_owner_mac", lambda: True)
    monkeypatch.setattr(judge_rules, "load_trusted_public_key", lambda: OWNER_PUBLIC)
    monkeypatch.setattr(model_digest, "resolve_model_digest", lambda model, base_url=None: DIGESTS.get(model))


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


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(tmp_path, provider, **kwargs):
    """البيانُ المصطنع يُمرَّر ببصمته بدل المسجَّلة في judge_v1؛ ورفضُ غيرِ المسجَّل يُختبر بلا هذا التمرير."""
    sealed_root, manifest = _bank(tmp_path)
    return sealed.run_sealed(sealed_root, provider, run_root=tmp_path / "runs", manifest_path=manifest,
                             manifest_sha256=_sha(manifest), **kwargs)


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
    assert report["engine"]["digest"] == ENGINE_DIGEST and report["judge"]["digest"] == JUDGE_DIGEST
    assert report["runtime"] == DATA["sealed"]["runtime"]
    assert report["runner_sha256"] == sealed.runner_sha256() and len(report["runner_sha256"]) == 64


@pytest.mark.parametrize("resolved,code", [("0" * 64, "model_version_mismatch"), (None, "model_digest_unresolved")],
                         ids=["repointed", "unresolved"])
def test_an_engine_tag_off_its_registered_digest_is_refused_before_reading(tmp_path, resolved, code):
    """ملاحظة Codex على #289: وسمُ qwen3.5:9b يُعاد توجيهُه قبل التشغيل؛ فالمحرّكُ ببصمته المسجَّلة في البروتوكول."""
    provider = Provider()
    with pytest.raises(SealedRefused) as refused:
        _run(tmp_path, provider, digest_resolver={**DIGESTS, FROZEN_ENGINE: resolved}.get)
    assert refused.value.code == code and provider.calls == 0


def test_a_judge_tag_repointed_after_calibration_is_refused_before_reading(tmp_path):
    provider = Provider()
    with pytest.raises(JudgeRefused) as refused:
        _run(tmp_path, provider, judge=Provider(model="granite4"), judge_evidence=_evidence(),
             digest_resolver={**DIGESTS, "granite4": "0" * 64}.get)
    assert refused.value.code == "judge_uncalibrated" and provider.calls == 0


def test_a_reused_run_root_never_replays_verdicts_of_earlier_judge_weights(tmp_path):
    """ملاحظة Codex على #289: بصمةُ المحكِّم في هويّة تشغيله، فأوزانٌ أُعيدت معايرتُها في مجلّد التشغيل نفسِه تحكم من جديد."""
    first = Provider(model="granite4", answer="الحكم: correct")
    assert _run(tmp_path, Provider(), judge=first, judge_evidence=_evidence())["by_tier"]["tier_b"]["passes"] == 4
    recalibrated = "e" * 64
    second = Provider(model="granite4", answer="الحكم: incorrect")
    report = _run(tmp_path, Provider(), judge=second,
                  judge_evidence=_evidence(judge={"model": "granite4", "digest": recalibrated}),
                  digest_resolver={**DIGESTS, "granite4": recalibrated}.get)
    assert second.calls == 4 and report["by_tier"]["tier_b"]["failures"] == 4
    assert report["judge"]["digest"] == recalibrated


def test_a_reused_run_root_never_replays_answers_of_earlier_runner_code(tmp_path, monkeypatch):
    """ملاحظة Codex على #289: مجلّدُ التشغيل معزولٌ ببصمة المُشغِّل، فشيفرةٌ تغيّرت تسأل المحرّكَ من جديد ولا تعيد أجوبةَ
    دفتر الشيفرة السابقة وتنسبها إلى بصمتها."""
    monkeypatch.setattr(sealed, "runner_sha256", lambda: "a" * 64)
    assert _run(tmp_path, Provider(answer="نعم"))["by_tier"]["tier_a"]["passes"] == 6
    monkeypatch.setattr(sealed, "runner_sha256", lambda: "b" * 64)
    engine = Provider(answer="لا")
    report = _run(tmp_path, engine)
    assert engine.calls > 0 and report["by_tier"]["tier_a"]["passes"] == 0 and report["runner_sha256"] == "b" * 64


def test_the_runner_digest_covers_whole_imported_packages_not_a_hand_list():
    """ملاحظة Codex على #289: القائمةُ اليدوية أغفلت ollama_codec وcore/run وretrieval_general؛ والآن الحزمُ المستورَدة
    كلُّها، فيدخل ما يُستورد كسولًا داخلها."""
    files = {path.as_posix() for path in sealed.runner_files()}
    assert {"providers/ollama_codec.py", "core/run.py", "evaluation/retrieval_general.py",
            "tools/evaluate_sealed.py", "tools/model_digest.py"} <= files
    package = {p.relative_to(sealed.ROOT).as_posix() for p in (sealed.ROOT / "evaluation").rglob("*.py")}
    assert package <= files and not any(path.startswith("tests/") for path in files)


@pytest.mark.parametrize("model", [FROZEN_ENGINE, "granite4"], ids=["engine", "judge"])
def test_a_digest_that_changes_during_the_run_refuses_the_report(tmp_path, model):
    """البصمتان تُعادان بعد التشغيل؛ ونموذجٌ تبدّلت أوزانُه أثناءه لا يُكتب له تقرير."""
    seen = []

    def resolver(name):
        seen.append(name)
        return "f" * 64 if name == model and seen.count(name) > 1 else DIGESTS.get(name)
    with pytest.raises(SealedRefused) as refused:
        _run(tmp_path, Provider(), judge=Provider(model="granite4"), judge_evidence=_evidence(),
             digest_resolver=resolver)
    assert refused.value.code == "model_digest_drifted"


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
        sealed.run_sealed(sealed_root, Provider(), run_root=tmp_path / "runs", manifest_path=manifest,
                          manifest_sha256=_sha(manifest))
    assert refused.value.code == "sealed_manifest_mismatch" and target.name not in str(refused.value)


def test_a_manifest_other_than_the_registered_one_is_refused_before_reading(tmp_path):
    """ملاحظة Codex على #289: بيانُ بنكٍ آخر (v1.2) لا يُقاس باسم judge_v1؛ والمسجَّلُ بصمةُ بيان v1.1."""
    sealed_root, manifest = _bank(tmp_path)
    provider = Provider()
    with pytest.raises(SealedRefused) as refused:
        sealed.run_sealed(sealed_root, provider, run_root=tmp_path / "runs", manifest_path=manifest)
    assert refused.value.code == "sealed_manifest_changed" and provider.calls == 0


def test_the_frozen_engine_is_named_by_the_protocol_not_the_product_default(tmp_path):
    """ملاحظة Codex على #289: اسمُ المحرّك من judge_v1 نفسِه؛ فبروتوكولٌ يسمّي غيرَه يردّ qwen3.5:9b ولو بقي هو الافتراضيّ."""
    data = json.loads(judge_rules.PROTOCOL.read_text(encoding="utf-8"))
    data["sealed"]["engine_model"] = "llama-frozen:1b"
    other = tmp_path / "judge_other.json"
    other.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SealedRefused) as refused:
        _run(tmp_path, Provider(), protocol_path=other, protocol_sha256=_sha(other))
    assert refused.value.code == "sealed_engine_not_frozen"


def test_the_sealed_run_never_writes_inside_the_repository(tmp_path):
    sealed_root, manifest = _bank(tmp_path)
    inside = sealed.ROOT / "var" / "sealed-run-must-not-exist"
    with pytest.raises(SealedRefused) as refused:
        sealed.run_sealed(sealed_root, Provider(), run_root=inside, manifest_path=manifest,
                          manifest_sha256=_sha(manifest))
    assert refused.value.code == "sealed_run_root_in_repository" and not inside.exists()


def test_the_cli_refuses_a_cloud_engine_by_name_before_any_sandbox_or_sealed_access(monkeypatch, capsys):
    monkeypatch.setattr(sealed, "configure_sandbox_backend", lambda *a: pytest.fail("أُقلعت الخلفيّة قبل الرفض"))
    monkeypatch.setattr(sealed, "verify_manifest", lambda *a, **k: pytest.fail("المحجوب قُرئ قبل الرفض"))
    assert sealed.main(["--model", "glm-4.6:cloud", "--sandbox-receipt", "receipt.json"]) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "code": "sealed_requires_local_provider"}


def _refuse_key():
    raise signing.SigningRefused("signing_keychain_item_missing", "synthetic")


@pytest.mark.parametrize("system,seed,expected", [
    ("Darwin", lambda: OWNER_SEED, True), ("Darwin", lambda: OTHER_SEED, False),
    ("Darwin", _refuse_key, False), ("Linux", lambda: OWNER_SEED, False),
], ids=["owner_key", "another_key", "no_owner_key", "linux_with_owner_key"])
def test_the_owner_mac_is_attested_by_the_owner_key_not_the_os_name(monkeypatch, system, seed, expected):
    """ملاحظة Codex على #289: Darwin وحده كان يُقبل، فماكٌ آخر نُسخ إليه المحجوبُ يقرؤه؛ والآن يلزم مفتاحُ المالك في سلسلة
    مفاتيح الجهاز يطابق المفتاحَ العامّ المُثبَّت."""
    monkeypatch.setattr(sealed.platform, "system", lambda: system)
    monkeypatch.setattr(signing, "load_ed25519_private_key", seed)
    monkeypatch.setattr(signing, "load_trusted_public_key", lambda: OWNER_PUBLIC)
    assert REAL_ON_OWNER_MAC() is expected


@pytest.mark.parametrize("change", ["engine_seed", "judge_seed", "thinking", "context", "judge_prompt", "temperature",
                                    "endpoint", "system_prompt", "verdict_pattern"])
def test_a_runtime_other_than_the_registered_one_is_refused_before_reading(tmp_path, monkeypatch, change):
    """ملاحظة Codex على #289: إعدادُ التشغيل (البذرة، والتفكير، والسياق، وتعليماتُ المحكِّم، والسقفان) مسجَّلٌ في judge_v1؛
    فتشغيلٌ بغيره لا يُنشر قياسًا بالاسم نفسِه."""
    engine, judge = Provider(), Provider(model="granite4")
    if change == "engine_seed":
        engine = Provider(seed=7)
    elif change == "judge_seed":
        judge = Provider(model="granite4", seed=1)
    elif change == "thinking":
        engine = Provider(allow_thinking=True)
    elif change == "context":
        monkeypatch.setattr(ollama_provider, "CONTEXT_TOKENS", 8192)
    elif change == "temperature":
        engine = Provider(temperature=0.7)
    elif change == "endpoint":
        judge = Provider(model="granite4", base_url="http://127.0.0.1:11500")
    elif change == "system_prompt":
        monkeypatch.setattr(capabilities, "SYSTEM", capabilities.SYSTEM + " ")
    elif change == "verdict_pattern":
        monkeypatch.setattr(sealed, "_VERDICT", re.compile(r"(correct|incorrect)", re.IGNORECASE))
    else:
        monkeypatch.setattr(sealed, "JUDGE_PROMPT", sealed.JUDGE_PROMPT + " ")
    with pytest.raises(SealedRefused) as refused:
        _run(tmp_path, engine, judge=judge, judge_evidence=_evidence())
    assert refused.value.code == "sealed_runtime_changed" and engine.calls == 0 and judge.calls == 0


def test_the_registered_runtime_matches_the_real_ollama_provider():
    """الإعدادُ المسجَّل هو ما يرسله OllamaProvider فعلًا للمحرّك والمحكِّم، فلا يُردّ التشغيلُ الحقيقيّ ولا يُقبل غيرُه."""
    engine, judge = OllamaProvider(FROZEN_ENGINE), OllamaProvider("granite4")
    assert sealed.check_runtime(DATA, engine, judge) == DATA["sealed"]["runtime"]
    with pytest.raises(SealedRefused):
        sealed.check_runtime(DATA, OllamaProvider(FROZEN_ENGINE, seed=3), judge)


def test_digests_resolve_from_the_registered_endpoint_and_quoted_material_follows_the_protocol(tmp_path, monkeypatch):
    """ملاحظة Codex على #289: البصمتان تُحلّان من نقطة Ollama المسجَّلة التي يُجاب منها، لا من نقطةٍ ثابتةٍ أخرى؛ وحَجرُ
    المقتبس يُمرَّر من البروتوكول لا من افتراضٍ قد يتغيّر."""
    endpoints, quarantine = [], []
    monkeypatch.setattr(model_digest, "resolve_model_digest",
                        lambda model, base_url=None: endpoints.append(base_url) or DIGESTS.get(model))
    real = sealed.evaluate_suite

    def recording(*args, **kwargs):
        quarantine.append(kwargs.get("quarantine_quoted_material", "absent"))
        return real(*args, **kwargs)
    monkeypatch.setattr(sealed, "evaluate_suite", recording)
    _run(tmp_path, Provider(), judge=Provider(model="granite4", answer="الحكم: correct"), judge_evidence=_evidence())
    assert endpoints and set(endpoints) == {ENDPOINT}
    assert quarantine and set(quarantine) == {DATA["sealed"]["runtime"]["quarantine_quoted_material"]}


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


def test_a_corrupted_protocol_is_refused_by_name_not_at_import(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #289: كان المُشغِّل يقرأ البروتوكول عند استيراده بلا فحص بصمته، فعطبُه يخرج استثناءً خامًا قبل
    main؛ والآن لا يُقرأ إلا بعد فحصها، فيُردّ برمزه."""
    import importlib.util
    corrupted = tmp_path / "judge_v1.json"
    corrupted.write_text("{ ليس JSON", encoding="utf-8")
    monkeypatch.setattr(judge_rules, "PROTOCOL", corrupted)
    spec = importlib.util.spec_from_file_location("evaluate_sealed_fresh", sealed.__file__)
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    monkeypatch.setattr(fresh, "_on_owner_mac", lambda: True)
    assert fresh.main(["--sealed-root", str(tmp_path / "sealed")]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "judge_protocol_changed"


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
