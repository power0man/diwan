"""#288: مُشغِّلُ المحجوب على محجوبٍ مصطنع — محليٌّ وحده، وبلا معرّفٍ ولا نصٍّ في تقريره.

لا يقرأ هذا الاختبارُ المحجوبَ الحقيقيّ: يبني بنكًا مصطنعًا وبيانَه في مجلّدٍ مؤقّت، ومزوّدين مصطنعين.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import shutil

import pytest

from core.contracts import Response, Usage
from evaluation import judge as judge_rules
from evaluation.judge import JudgeRefused
from core import filelock, signing
from evaluation import capabilities
from providers import ollama as ollama_provider
from providers.base import ProviderError
from providers.ollama import OllamaProvider
from tests.test_judge_protocol_frozen import (DATA, ENGINE_DIGEST, JUDGE_DIGEST, OTHER_SEED, OWNER_PUBLIC, OWNER_SEED,
                                              _evidence, _truth)
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
REAL_TRUTH = judge_rules.calibration_truth


@pytest.fixture(autouse=True)
def _owner_mac(monkeypatch):
    """الاختباراتُ على لينكس؛ وفحصُ الجهاز نفسُه يُختبر بإبطال هذا في موضعه. ومفتاحُ المالك المصطنع يحلّ محلّ
    المُثبَّت، فدليلُ المعايرة الموقَّع به في الاختبار يمرّ، ولا يمرّ به دليلٌ حقيقيّ."""
    monkeypatch.setattr(sealed, "_on_owner_mac", lambda: True)
    monkeypatch.setattr(judge_rules, "load_trusted_public_key", lambda: OWNER_PUBLIC)
    monkeypatch.setattr(model_digest, "resolve_model_digest", lambda model, base_url=None: DIGESTS.get(model))
    # مراجعُ عيوب ك١١ السبعة من ملفّاتها المراجَعة المصطنعة (تُحسب قبل الإبدال)؛ والأصلُ قبل إصلاح ك١٥ في diwan-private
    truth = _truth()
    monkeypatch.setattr(judge_rules, "calibration_truth", lambda protocol, sample, **_: truth)


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


def test_the_sandbox_receipt_identity_is_published_not_collapsed(tmp_path, monkeypatch):
    """ملاحظة Codex على #289: هويّةُ إيصال الحاوية (الصورة والحدود) تحكم فحوصَ python_sandbox، فتُنشر هي وبصمتُها لا
    «configured»؛ وبلا إيصالٍ تبقى None."""
    # وبلا إيصالٍ يحمل التقريرُ نفسُه حدَّ حالات python_sandbox، لا السطرُ وحده (ملاحظة Codex على #289)
    limit = "python_sandbox_cases_count_as_errors_because_no_sandbox_receipt_was_given"
    bare = _run(tmp_path, Provider())
    assert bare["sandbox"] is None and limit in bare["measurement_limits"]
    receipt = {"backend": "docker", "image_id": "sha256:" + "c" * 64, "lock_sha256": "d" * 64, "snapshot_files": []}
    monkeypatch.setattr(sealed, "sandbox_configuration", lambda: receipt)
    report = _run(tmp_path, Provider())
    assert report["sandbox"] == receipt and limit not in report["measurement_limits"]
    assert report["sandbox_sha256"] == hashlib.sha256(json.dumps(receipt, sort_keys=True).encode()).hexdigest()


OVERRIDDEN = "the_inputs_named_in_overrides_are_not_the_registered_ones_so_this_report_is_not_a_judge_v1_measurement"


def test_any_override_is_named_and_never_called_a_judge_v1_measurement(tmp_path, monkeypatch):
    """ملاحظات Codex على #289: بيانٌ أو بروتوكولٌ أو مُحلِّلُ بصماتٍ أو مزوّدٌ يمرّره المستدعي كان يُقبل والتقريرُ «measured» لـjudge_v1،
    فيُنسب إلى المسجَّل ما لم يُقَس عليه. والآن يُسمّى كلُّ تبديلٍ في التقرير ولا يُسمّى قياسًا؛ والمسجَّلُ كلُّه وحده قياس."""
    overridden = _run(tmp_path / "a", Provider())
    assert overridden["status"] == "not_measured_overridden" and overridden["overrides"] == ["provider", "manifest"]
    assert overridden["manifest_sha256"] == _sha(tmp_path / "a" / "MANIFEST.json")
    assert OVERRIDDEN in overridden["measurement_limits"]
    resolved = _run(tmp_path / "c", Provider(), digest_resolver=DIGESTS.get)
    assert resolved["overrides"] == ["digest_resolver", "provider", "manifest"]
    judged = _run(tmp_path / "d", Provider(), judge=Provider(model="granite4"), judge_evidence=_evidence())
    assert judged["overrides"] == ["provider", "judge_provider", "manifest"]
    # المزوّدُ الموثوق عينُه (OllamaProvider) بجوابٍ مصطنعٍ يُعاد من صنفه في الاختبار وحده، فلا يُطلب Ollama حيّ
    monkeypatch.setattr(OllamaProvider, "complete", lambda self, request: Response(
        "نعم", Usage(3, 1), "complete", 0, provider="ollama", model_version="fixture"))
    # بروتوكولٌ يسجّل البيانَ الاصطناعيَّ: مُمرَّرًا ببصمته تبديلٌ، ومثبَّتًا في الشيفرة (المسجَّلُ كلُّه) قياس
    data = json.loads(judge_rules.PROTOCOL.read_text(encoding="utf-8"))
    sealed_root, manifest = _bank(tmp_path / "b")
    data["sealed"]["manifest_sha256"] = _sha(manifest)
    registered = tmp_path / "judge_v1.json"
    registered.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    passed = sealed.run_sealed(sealed_root, OllamaProvider(FROZEN_ENGINE), run_root=tmp_path / "b" / "runs",
                               manifest_path=manifest, protocol_path=registered, protocol_sha256=_sha(registered))
    assert passed["status"] == "not_measured_overridden" and passed["overrides"] == ["protocol", "provider"]
    monkeypatch.setattr(judge_rules, "PROTOCOL", registered)
    monkeypatch.setattr(judge_rules, "PROTOCOL_SHA256", _sha(registered))
    # ملاحظة Codex على #289: OllamaProvider عينُه ممرَّرًا بدالّةٍ مُبدَلة في نسخته يكتب الجوابَ بنفسه، فهو تبديلٌ مسمًّى
    mutated = OllamaProvider(FROZEN_ENGINE)
    mutated.complete = lambda request: Response("نعم", Usage(3, 1), "complete", 0, provider="x", model_version="x")
    injected = sealed.run_sealed(sealed_root, mutated, run_root=tmp_path / "b" / "runs", manifest_path=manifest)
    assert injected["status"] == "not_measured_overridden" and injected["overrides"] == ["provider"]
    # ولا عَلَمَ ثقةٍ في واجهة run_sealed يُزوَّر به المزوّدُ الممرَّر «مبنيًّا هنا» (ملاحظة Codex على #289)
    assert "_constructed_here" not in inspect.signature(sealed.run_sealed).parameters
    with pytest.raises(TypeError):
        sealed.run_sealed(sealed_root, mutated, run_root=tmp_path / "b" / "runs", manifest_path=manifest,
                          _constructed_here=True)
    # والقياسُ وحده بمزوّدَين يبنيهما measure_sealed من اسميهما
    report = sealed.measure_sealed(sealed_root, FROZEN_ENGINE, agent="anthropic/claude-fable-5-1",
                                   run_root=tmp_path / "b" / "runs", manifest_path=manifest)
    assert report["status"] == "measured" and report["overrides"] == [] and report["manifest_sha256"] == _sha(manifest)
    assert report["agent"] == "anthropic/claude-fable-5-1"
    assert OVERRIDDEN not in report["measurement_limits"]


def test_a_request_resent_without_think_is_counted_and_named_not_called_a_measurement(tmp_path, monkeypatch):
    """ملاحظة Codex على #289: OllamaProvider.complete يعيد الطلبَ بلا حقل think إن ردّه الخادمُ بـ«does not support
    thinking»، وcheck_runtime يقرأ الصفةَ والخيارات لا ما أُرسل؛ فكان القياسُ يُنشر «measured» بإعدادٍ غيرِ المسجَّل. والآن
    يُعدّ كلُّ نداءٍ خرج بلا الحقل، ويُنشر عددُه، ويُسمّى تبديلًا."""
    refusing, sent = set(), []

    def post(self, payload, timeout):
        sent.append((self.model, "think" in payload))
        if self.model in refusing and "think" in payload:
            raise ProviderError("http_400", f'خطأ خادم Ollama: {{"error":"\\"{self.model}\\" does not support thinking"}}',
                                retryable=False)
        return {"model": payload["model"], "message": {"role": "assistant", "content": "نعم"}, "done": True,
                "done_reason": "stop", "prompt_eval_count": 3, "eval_count": 1}
    monkeypatch.setattr(OllamaProvider, "_post", post)
    data = json.loads(judge_rules.PROTOCOL.read_text(encoding="utf-8"))
    sealed_root, manifest = _bank(tmp_path / "b")
    data["sealed"]["manifest_sha256"] = _sha(manifest)
    registered = tmp_path / "judge_v1.json"
    registered.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(judge_rules, "PROTOCOL", registered)
    monkeypatch.setattr(judge_rules, "PROTOCOL_SHA256", _sha(registered))

    def measure(run, **judge):
        return sealed.measure_sealed(sealed_root, FROZEN_ENGINE, agent="anthropic/claude-fable-5-1",
                                     run_root=tmp_path / run, manifest_path=manifest, **judge)
    clean = measure("clean")
    assert clean["status"] == "measured" and clean["overrides"] == []
    assert clean["think_fallbacks"] == {"engine": 0, "judge": None} and all(think for _, think in sent)
    refusing.add(FROZEN_ENGINE)
    resent = measure("resent")
    # ستُّ حالاتٍ بفحصٍ آليّ وأربعٌ بلا فحص: كلُّها تُسأل، فعشرُ إعاداتٍ بلا think
    assert resent["think_fallbacks"] == {"engine": 10, "judge": None}
    assert resent["status"] == "not_measured_overridden" and resent["overrides"] == ["think_fallback"]
    assert OVERRIDDEN in resent["measurement_limits"]
    # ولا تبقى أجوبتُها في الدفتر (ملاحظة Codex على #289): استدعاءٌ تالٍ على المجلّد نفسِه يسأل النموذجَ من جديد، نداءين
    # لكلّ حالة، فلا يعيد عرضَها بعدّادين صفرين ويسمّيها «measured»
    before = len(sent)
    again = measure("resent")
    assert len(sent) - before == 20 and again["think_fallbacks"] == {"engine": 10, "judge": None}
    assert again["status"] == "not_measured_overridden" and again["overrides"] == ["think_fallback"]
    runner = tmp_path / "resent" / f"runner-{sealed.runner_sha256()[:24]}"
    assert {p.name for p in runner.iterdir()} == {model_digest.QUARANTINE_DIR, sealed.RUNNER_LOCK}
    # والمحكِّمُ كذلك: الحالاتُ الأربع بلا فحصٍ تُحكَّم، فأربعُ إعاداتٍ منه وحده
    refusing.clear()
    refusing.add("granite4")
    judged = measure("judged", judge_model="granite4", judge_evidence=_evidence(protocol_sha256=_sha(registered)))
    assert judged["think_fallbacks"] == {"engine": 0, "judge": 4} and judged["overrides"] == ["think_fallback"]
    assert judged["status"] == "not_measured_overridden"


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
    # ملاحظة Codex على #289: أجوبةُ ما بعد الانحراف تُنقل إلى drift-quarantine، فوسمٌ أُعيد لا يعيد عرضَها، ويُسأل النموذجان من جديد
    runner = tmp_path / "runs" / f"runner-{sealed.runner_sha256()[:24]}"
    assert {p.name for p in runner.iterdir()} == {model_digest.QUARANTINE_DIR, sealed.RUNNER_LOCK}
    engine, judge = Provider(), Provider(model="granite4", answer="التعليل مصطنع.\nالحكم: correct")
    _run(tmp_path, engine, judge=judge, judge_evidence=_evidence(), digest_resolver=DIGESTS.get)
    assert engine.calls == 10 and judge.calls == 4


def test_runs_of_an_invocation_killed_before_its_post_check_are_quarantined_not_replayed(tmp_path, monkeypatch):
    """ملاحظة Codex على #289: عمليةٌ قُتلت بعد كتابة أجوبتها وقبل إعادة البصمتين تترك علامتَها، فلا يعيد التشغيلُ التالي
    أجوبتَها ولو أُعيد الوسم؛ وتشغيلٌ مرّ فحصُه يزيل علامتَه فيُعاد عرضُه بلا سؤالٍ جديد."""
    runner = tmp_path / "runs" / f"runner-{sealed.runner_sha256()[:24]}"

    def killed(*args, **kwargs):
        raise KeyboardInterrupt
    with monkeypatch.context() as patch:
        patch.setattr(sealed, "verify_model_digest", killed)
        with pytest.raises(KeyboardInterrupt):
            _run(tmp_path, Provider())
    assert (runner / sealed.POST_CHECK_PENDING).is_file()
    engine = Provider()
    _run(tmp_path, engine)
    assert engine.calls == 10 and (runner / model_digest.QUARANTINE_DIR).is_dir()
    assert not (runner / sealed.POST_CHECK_PENDING).exists()
    replayed = Provider()
    _run(tmp_path, replayed)
    assert replayed.calls == 0


@pytest.mark.parametrize("entry", ["runner", sealed.RUNNER_LOCK, sealed.POST_CHECK_PENDING,
                                   f"{sealed.POST_CHECK_PENDING}.tmp"])
def test_a_symlinked_runner_directory_or_file_is_refused_before_anything_is_written(tmp_path, entry):
    """ملاحظة Codex على #289: الفحصُ يحكم على أب مجلّد المُشغِّل؛ ورابطٌ قائمٌ باسم runner-<بصمة> أو بأحد ملفّاته إلى
    المحجوب يكتب القفلَ والعلامةَ في هدفه. فيُرفض برمزٍ مسمًّى قبل أيّ كتابةٍ أو نداء."""
    sealed_root, _ = _bank(tmp_path)
    target = sealed_root / "tier_a" / "synthetic_tier_a_sealed.json"
    before = _sha(target)
    runner = tmp_path / "runs" / f"runner-{sealed.runner_sha256()[:24]}"
    if entry == "runner":
        runner.parent.mkdir(parents=True)
        runner.symlink_to(sealed_root, target_is_directory=True)
    else:
        runner.mkdir(parents=True)
        (runner / entry).symlink_to(target)
    engine = Provider()
    with pytest.raises(SealedRefused) as refused:
        _run(tmp_path, engine)
    assert refused.value.code == "sealed_run_path_is_a_symlink" and engine.calls == 0
    assert _sha(target) == before
    assert not {sealed.RUNNER_LOCK, sealed.POST_CHECK_PENDING} & {p.name for p in sealed_root.rglob("*")}


@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_a_quarantine_that_is_a_link_or_not_a_directory_is_refused_before_runs_are_moved(tmp_path, kind):
    """ملاحظة Codex على #289: علامةُ استدعاءٍ مقطوع تبلغ الحَجر، وكان رابطٌ رمزيّ مُسبَقٌ باسم drift-quarantine (إلى المستودع
    مثلًا) ينقل إليه الدفاترَ وفيها أسئلةُ المحجوب وأجوبتُه. فيُردّ برمزٍ مسمًّى قبل أن يُنقل شيءٌ أو يُسأل نموذج."""
    runner = tmp_path / "runs" / f"runner-{sealed.runner_sha256()[:24]}"
    run = runner / "run-interrupted"
    run.mkdir(parents=True)
    (run / "ledger.jsonl").write_text("{}\n", encoding="utf-8")
    (runner / sealed.POST_CHECK_PENDING).write_text(json.dumps({"started": 0}), encoding="utf-8")
    elsewhere = tmp_path / "checkout"
    elsewhere.mkdir()
    if kind == "symlink":
        (runner / model_digest.QUARANTINE_DIR).symlink_to(elsewhere, target_is_directory=True)
    else:
        (runner / model_digest.QUARANTINE_DIR).write_text("", encoding="utf-8")
    engine = Provider()
    with pytest.raises(SealedRefused) as refused:
        _run(tmp_path, engine)
    assert refused.value.code == "quarantine_dir_unsafe" and engine.calls == 0
    assert (run / "ledger.jsonl").exists() and not any(elsewhere.iterdir())


@pytest.mark.parametrize("entry", [sealed.RUNNER_LOCK, sealed.POST_CHECK_PENDING])
def test_a_hard_linked_lock_or_marker_is_refused_before_it_is_opened(tmp_path, entry):
    """ملاحظة Codex على #289: العلامةُ رابطٌ صلبٌ إلى ملفٍّ مختوم تمرّ بـ_plain، وكانت تُقرأ منه قبل تحقيق أيِّ محجوب؛
    فالقفلُ والعلامةُ القائمان يُرفضان إن لم يكونا ملفّين عاديّين برابطٍ واحد."""
    sealed_root, _ = _bank(tmp_path)
    target = sealed_root / "tier_a" / "synthetic_tier_a_sealed.json"
    before = _sha(target)
    runner = tmp_path / "runs" / f"runner-{sealed.runner_sha256()[:24]}"
    runner.mkdir(parents=True)
    os.link(target, runner / entry)
    engine = Provider()
    with pytest.raises(SealedRefused) as refused:
        _run(tmp_path, engine)
    assert refused.value.code == "sealed_run_file_is_linked" and engine.calls == 0 and _sha(target) == before


def test_a_hard_linked_marker_temporary_is_replaced_not_truncated(tmp_path):
    """مؤقّتُ العلامة الباقي رابطٌ صلبٌ إلى ملفٍّ مختوم: فتحُه بـO_TRUNC كان يقطع المختوم؛ والآن تُزال مدخلتُه ويُنشأ جديدًا."""
    sealed_root, _ = _bank(tmp_path)
    target = sealed_root / "tier_a" / "synthetic_tier_a_sealed.json"
    before = _sha(target)
    runner = tmp_path / "runs" / f"runner-{sealed.runner_sha256()[:24]}"
    runner.mkdir(parents=True)
    os.link(target, runner / f"{sealed.POST_CHECK_PENDING}.tmp")
    engine = Provider()
    _run(tmp_path, engine)
    assert engine.calls == 10 and _sha(target) == before


@pytest.mark.parametrize("where", ["inside", "symlink", "hardlink"])
def test_a_manifest_in_or_linked_into_the_sealed_root_is_refused_before_it_is_read(tmp_path, monkeypatch, where):
    """ملاحظة Codex على #289: measure_sealed يمرّر manifest_path كما سمّاه المستدعي، وverify_manifest يقرؤه قبل مطابقة
    بصمته؛ فبيانٌ في المحجوب أو رابطٌ إليه يُردّ قبل أن يُفتح."""
    sealed_root, manifest = _bank(tmp_path)
    inside = sealed_root / "MANIFEST.json"
    shutil.copyfile(manifest, inside)
    named = {"inside": inside, "symlink": tmp_path / "linked.json", "hardlink": tmp_path / "hard.json"}[where]
    if where == "symlink":
        named.symlink_to(inside)
    elif where == "hardlink":
        os.link(inside, named)
    monkeypatch.setattr(sealed, "verify_manifest", lambda *a, **k: pytest.fail("قُرئ بيانٌ من المحجوب"))
    engine = Provider()
    with pytest.raises(SealedRefused) as refused:
        sealed.run_sealed(sealed_root, engine, run_root=tmp_path / "runs", manifest_path=named,
                          manifest_sha256=_sha(inside))
    assert refused.value.code == "sealed_manifest_in_sealed_root" and engine.calls == 0


@pytest.mark.parametrize("where", ["inside", "symlink", "hardlink"])
def test_a_protocol_in_or_linked_into_the_sealed_root_is_refused_before_it_is_read(tmp_path, monkeypatch, where):
    """ملاحظة Codex على #289: run_sealed يقرأ protocol_path الممرَّرَ قبل مطابقة بصمته وقبل الفحص المسبق؛ فبروتوكولٌ في
    المحجوب أو رابطٌ إليه يُردّ قبل أن يُفتح، ولو رُدّ التشغيلُ بعده."""
    sealed_root, manifest = _bank(tmp_path)
    inside = sealed_root / "judge_v1.json"
    shutil.copyfile(judge_rules.PROTOCOL, inside)
    named = {"inside": inside, "symlink": tmp_path / "linked.json", "hardlink": tmp_path / "hard.json"}[where]
    if where == "symlink":
        named.symlink_to(inside)
    elif where == "hardlink":
        os.link(inside, named)
    monkeypatch.setattr(judge_rules, "load_protocol", lambda *a, **k: pytest.fail("قُرئ بروتوكولٌ من المحجوب"))
    engine = Provider()
    with pytest.raises(SealedRefused) as refused:
        sealed.run_sealed(sealed_root, engine, run_root=tmp_path / "runs", manifest_path=manifest,
                          protocol_path=named, protocol_sha256=_sha(inside))
    assert refused.value.code == "judge_protocol_in_sealed_root" and engine.calls == 0


def test_a_reviewed_bank_that_contains_the_sealed_root_is_refused(tmp_path):
    """ملاحظة Codex على #289: ملفّاتُ ك١١ السبعة بمساراتٍ ثابتةٍ تحت مجلّدها؛ فمجلّدٌ يحوي المحجوبَ يجعل أحدَها مختومًا."""
    with pytest.raises(SealedRefused) as refused:
        _run(tmp_path, Provider(), judge=Provider(model="granite4"), judge_evidence=_evidence(),
             reviewed_bank=tmp_path / "diwan-sealed")
    assert refused.value.code == "k11_reviewed_bank_in_sealed_root"


def test_the_suites_measured_are_the_bytes_the_manifest_authenticated(tmp_path, monkeypatch):
    """ملاحظة Codex على #289: ملفٌّ مختومٌ يُستبدل بعد مطابقة بصمته وقبل تحميله كان يُقاس محتواه غيرُ الموثَّق وينشر التقريرُ
    بصمةَ البيان؛ والآن تُحلَّل البايتاتُ التي طابقت البصمةَ نفسُها، فلا أثرَ للاستبدال."""
    real = sealed.verify_manifest

    def then_swapped(*args):
        entries = real(*args)
        for entry in entries:
            suite = json.loads(entry["local"].read_text(encoding="utf-8"))
            for case in suite["cases"]:
                case["checks"] = [{"kind": "exact", "value": "لا"}] if case["checks"] else []
            entry["local"].write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
        return entries
    monkeypatch.setattr(sealed, "verify_manifest", then_swapped)
    report = _run(tmp_path, Provider())
    assert report["by_tier"]["tier_a"]["passes"] == 6 and report["by_tier"]["tier_a"]["failures"] == 0


def test_a_second_measurement_in_the_same_runner_directory_is_refused_before_it_writes(tmp_path):
    """ملاحظة Codex على #289: استدعاءان متداخلان يتشاركان علامةَ الفحص المعلَّق، فيزيل الأولُ علامةَ الثاني؛ فالقياسُ
    مسلسَلٌ بقفل مجلّد المُشغِّل، والثاني يُرفض قبل أن يكتب علامةً أو يسأل نموذجًا، ثم يمرّ بعد فكّ القفل."""
    runner = tmp_path / "runs" / f"runner-{sealed.runner_sha256()[:24]}"
    runner.mkdir(parents=True)
    with open(runner / sealed.RUNNER_LOCK, "a+") as held:
        filelock.lock(held)
        second = Provider()
        with pytest.raises(SealedRefused) as refused:
            _run(tmp_path, second)
        assert refused.value.code == "sealed_run_in_progress" and second.calls == 0
        assert not (runner / sealed.POST_CHECK_PENDING).exists()
        filelock.unlock(held)
    assert os.path.isfile(runner / sealed.RUNNER_LOCK)
    after = Provider()
    _run(tmp_path, after)
    assert after.calls == 10


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


def test_sandbox_paths_and_the_run_root_stay_outside_the_sealed_root(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #289: إيصالُ الحاوية كان يُفتح من داخل المحجوب قبل أن يوثّقه البيان، بلا حارسِ دليل المعايرة؛
    والآن كلُّ مسارٍ يسمّيه المستدعي (الإيصالُ ومساحتُه ومجلّدُ التشغيل) يُردّ إن وقع داخله، قبل أن يُقلع شيء."""
    sealed_root = tmp_path / "diwan-sealed" / "kimi_v1"
    sealed_root.mkdir(parents=True)
    monkeypatch.setattr(sealed, "configure_sandbox_backend", lambda *a: pytest.fail("أُقلعت الخلفيّة بمسارٍ في المحجوب"))
    common = ["--sealed-root", str(sealed_root), "--run-root", str(tmp_path / "runs")]
    outside = ["--sandbox-workspace", str(tmp_path / "sandbox")]
    assert sealed.main(common + outside + ["--sandbox-receipt", str(sealed_root / "receipt.json")]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "sandbox_receipt_in_sealed_root"
    assert sealed.main(common + ["--sandbox-receipt", str(tmp_path / "receipt.json"),
                                 "--sandbox-workspace", str(sealed_root / "sandbox")]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "sandbox_workspace_in_sealed_root"
    bank_root, manifest = _bank(tmp_path / "b")
    with pytest.raises(SealedRefused) as refused:
        sealed.run_sealed(bank_root, Provider(), run_root=bank_root / "runs", manifest_path=manifest,
                          manifest_sha256=_sha(manifest))
    assert refused.value.code == "sealed_run_root_in_sealed_root" and not (bank_root / "runs").exists()


def test_the_report_is_never_written_into_the_sealed_root(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #289: --out داخل المحجوب (مباشرةً أو عبر رابطٍ رمزيّ) كان يكتب التقريرَ فوق ملفٍّ مختومٍ بعد
    التقويم؛ والآن يُردّ برمزه قبل أن يبدأ التشغيل."""
    sealed_root = tmp_path / "diwan-sealed" / "kimi_v1"
    sealed_root.mkdir(parents=True)
    (tmp_path / "link").symlink_to(sealed_root)
    monkeypatch.setattr(sealed, "run_sealed", lambda *a, **k: pytest.fail("بدأ التشغيلُ وتقريرُه إلى المحجوب"))
    common = ["--sealed-root", str(sealed_root), "--run-root", str(tmp_path / "runs")]
    for out in (sealed_root / "tier_a" / "suite.json", tmp_path / "link" / "report.json"):
        assert sealed.main(common + ["--out", str(out)]) == 2
        assert json.loads(capsys.readouterr().out) == {"status": "refused", "code": "sealed_out_in_sealed_root"}


def test_a_hard_linked_report_destination_is_replaced_not_written_through(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #289: --out رابطٌ صلبٌ إلى ملفٍّ مختوم يمرّ بفحص المسار (resolve لا يكشفه)، والكتابةُ فيه كانت تقطع
    الملفَّ المختوم؛ والآن يُكتب التقريرُ ملفًّا جديدًا يحلّ محلَّ المدخلة، فيبقى المختومُ كما هو."""
    sealed_root = tmp_path / "diwan-sealed" / "kimi_v1"
    sealed_file = sealed_root / "tier_a" / "suite.json"
    sealed_file.parent.mkdir(parents=True)
    sealed_file.write_text("مختوم", encoding="utf-8")
    out = tmp_path / "report.json"
    os.link(sealed_file, out)
    monkeypatch.setattr(sealed, "measure_sealed", lambda *a, **k: {"status": "measured"})
    assert sealed.main(["--sealed-root", str(sealed_root), "--run-root", str(tmp_path / "runs"),
                        "--agent", "human/hussain-alrabighi", "--out", str(out)]) == 0
    capsys.readouterr()
    assert sealed_file.read_text(encoding="utf-8") == "مختوم" and not out.samefile(sealed_file)
    assert json.loads(out.read_text(encoding="utf-8")) == {"status": "measured"}


@pytest.mark.parametrize("flag,code", [("--judge-evidence", "judge_evidence_in_sealed_root"),
                                       ("--sandbox-receipt", "sandbox_receipt_in_sealed_root")])
def test_a_named_input_hard_linked_into_the_sealed_root_is_refused_before_it_is_read(tmp_path, monkeypatch, capsys,
                                                                                      flag, code):
    """ملاحظة Codex على #289 (الرابطُ الصلب): دليلُ المعايرة وإيصالُ الحاوية رابطٌ صلبٌ إلى ملفٍّ مختوم يمرّ بفحص المسار
    المحلول، فيُفتح المختومُ قبل البيان؛ والآن يُردّ برمز موضعه قبل أن يُقرأ."""
    sealed_root = tmp_path / "diwan-sealed" / "kimi_v1"
    target = sealed_root / "tier_a" / "suite.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    linked = tmp_path / "named.json"
    os.link(target, linked)
    monkeypatch.setattr(sealed, "configure_sandbox_backend", lambda *a: pytest.fail("قُرئ الإيصالُ المربوط"))
    argv = ["--sealed-root", str(sealed_root), "--run-root", str(tmp_path / "runs"), "--judge", "granite4", flag, str(linked)]
    if flag == "--sandbox-receipt":
        argv += ["--sandbox-workspace", str(tmp_path / "sandbox"), "--judge-evidence", str(tmp_path / "evidence.json")]
        (tmp_path / "evidence.json").write_text(json.dumps(_evidence()), encoding="utf-8")
    assert sealed.main(argv) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "code": code}


def test_a_refused_sandbox_receipt_is_a_named_refusal_not_a_traceback(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #289: إيصالٌ غائبٌ أو معطوبٌ أو غيرُ خاصّ يرفع ExecutionRefused من إقلاع الحاوية، وكان يخرج أثرًا
    خامًا؛ والآن رفضٌ مسمًّى برمزه كسائر الرفض."""
    from core.execution import ExecutionRefused

    def refuse(*_):
        raise ExecutionRefused("execution_receipt_untrusted", "synthetic")
    monkeypatch.setattr(sealed, "configure_sandbox_backend", refuse)
    assert sealed.main(["--sealed-root", str(tmp_path / "diwan-sealed" / "kimi_v1"), "--run-root", str(tmp_path / "runs"),
                        "--sandbox-receipt", str(tmp_path / "receipt.json"),
                        "--sandbox-workspace", str(tmp_path / "sandbox")]) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "refused", "code": "execution_receipt_untrusted"}


def test_the_k11_reviewed_bank_reaches_calibration_outside_the_sealed_root(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #289: مراجعُ عيوب ك١١ السبعة تُقرأ من ملفّاتها المراجَعة قبل إصلاح ك١٥، ويُمرَّر مسارُها من السطر
    إلى المعايرة خارجَ المحجوب؛ وبلاه يُردّ المحكِّمُ برمزٍ مسمًّى لا بقبولٍ على مراجعَ مُصلَحة."""
    seen = []
    monkeypatch.setattr(judge_rules, "calibration_truth",
                        lambda protocol, sample, *, reviewed_bank=None, **_: seen.append(reviewed_bank) or _truth())
    reviewed = tmp_path / "pre-k15"
    _run(tmp_path / "a", Provider(), judge=Provider(model="granite4"), judge_evidence=_evidence(), reviewed_bank=reviewed)
    assert seen == [reviewed.resolve()]
    sealed_root, manifest = _bank(tmp_path / "b")
    with pytest.raises(SealedRefused) as refused:
        sealed.run_sealed(sealed_root, Provider(), run_root=tmp_path / "b" / "runs", manifest_path=manifest,
                          manifest_sha256=_sha(manifest), judge=Provider(model="granite4"), judge_evidence=_evidence(),
                          reviewed_bank=sealed_root / "pre-k15")
    assert refused.value.code == "k11_reviewed_bank_in_sealed_root" and len(seen) == 1
    monkeypatch.setattr(judge_rules, "calibration_truth", REAL_TRUTH)
    evidence = tmp_path / "calibration.json"
    evidence.write_text(json.dumps(_evidence()), encoding="utf-8")
    common = ["--judge", "granite4", "--judge-evidence", str(evidence), "--sealed-root", str(sealed_root),
              "--run-root", str(tmp_path / "runs")]
    # ملاحظة Codex على #289: مُشغِّلُ القياس يُسمّى بمعرّفه المسجَّل، وبلا معرّفٍ أو بمعرّفٍ غيرِ مسجَّل يُردّ قبل القياس
    for agent in ([], ["--agent", "someone/unregistered"]):
        assert sealed.main(common + agent) == 2
        assert json.loads(capsys.readouterr().out)["code"] == "agent_unregistered"
    assert sealed.main(common + ["--agent", "human/hussain-alrabighi"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "calibration_reviewed_bank_missing"
    captured = {}
    monkeypatch.setattr(sealed, "run_sealed", lambda *a, **k: captured.update(k) or {"measurement_limits": [], "overrides": []})
    assert sealed.main(common + ["--agent", "human/hussain-alrabighi", "--k11-reviewed-bank", str(reviewed)]) == 0
    assert captured["reviewed_bank"] == reviewed and captured["agent"] == "human/hussain-alrabighi"


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
