"""حكمُ المالك يُوقَّع بمفتاحه وإلّا فليس حكمَ مالك (ق٤٥)."""
from __future__ import annotations

import copy
import json

import pytest

from core.contracts import Response, Usage
from evaluation.capabilities import _sha, evaluate_suite
from evaluation.human_review import ReviewError, review_binding, summarize_review
from evaluation.verdict_signature import (certify_review, sign_review,
                                          signed_content, verdict_message)

ed25519 = pytest.importorskip(
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    reason="التوقيع يحتاج مكتبة التعمية؛ وغيابها تعذُّرٌ لا فشل (ق٣٩)")


def suite_data():
    return {"schema_version": 1, "suite_id": "verdict-test", "split": "development",
            "description": "بنك صوري علني",
            "cases": [{"case_id": f"case-{i}", "capability": "فهم الطلب",
                       "messages": [{"role": "user", "content": f"أجب بالعدد {i}"}],
                       "reference": str(i), "rubric": ["يطابق المطلوب"],
                       "checks": [{"kind": "exact", "value": str(i)}],
                       "critical": i == 1} for i in (1, 2)]}


class Provider:
    model = "synthetic-test-runtime"
    is_local = True

    def __init__(self):
        self.calls = 0

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        return Response(str(self.calls), Usage(10, 2), "complete", 0,
                        provider="synthetic", model_version="synthetic")


@pytest.fixture
def report(tmp_path):
    return evaluate_suite(suite_data(), Provider(), tmp_path / "runs")


@pytest.fixture
def keys():
    private = ed25519.Ed25519PrivateKey.generate()
    seed = private.private_bytes_raw()
    return seed, private.public_key().public_bytes_raw()


def unsigned(report, verdict="pass"):
    return {**review_binding(report),
            "reviewer": {"identity": "حسين الرابغي", "attestation": True},
            "decisions": [{"case_id": r["case_id"], "answer_sha256": _sha(r["answer"]),
                           "verdict": verdict, "reason": "حكمٌ صوريّ داخل اختبار",
                           "severity": "material" if verdict == "fail" else None,
                           "review_seconds": 40} for r in report["results"]]}


def signed(report, seed, **kwargs):
    review = unsigned(report, **kwargs)
    review["owner_signature"] = sign_review(report, review, private_seed=seed)
    return review


# ————— الطريقُ الوحيد إلى هويةٍ متحقَّقة —————

def test_signed_verdict_is_the_only_path_to_a_verified_identity(report, keys):
    seed, public = keys
    review = signed(report, seed)
    summary = certify_review(report, review, public_key=public)
    assert summary["reviewer_identity_verified"] is True
    assert summary["judgment"] == "passed"
    assert summary["release_ready"] is False
    assert "verdict_correctness_is_not_attested" in summary["limits"]
    # والمسارُ البنيويّ يبقى صريحًا في أنه لا يُثبت هوية
    assert summarize_review(report, review)["reviewer_identity_verified"] is False


def test_attestation_true_alone_does_not_certify(report, keys):
    """الواقعةُ التي أوجبت ق٤٥: إقرارٌ يكتبه كاتبُ الملف باسم المالك."""
    _, public = keys
    review = unsigned(report)
    assert review["reviewer"]["attestation"] is True
    with pytest.raises(ReviewError) as exc:
        certify_review(report, review, public_key=public)
    assert exc.value.code == "owner_signature_missing"


@pytest.mark.parametrize("value,code", [
    ("", "owner_signature_malformed"),
    ("ED25519:", "owner_signature_malformed"),
    ("ED25519:" + "z" * 128, "owner_signature_malformed"),
    ("ED25519:" + "A" * 128, "owner_signature_malformed"),
    ("ED25519:" + "a" * 127, "owner_signature_malformed"),
    ("HMAC-SHA256:" + "a" * 64, "owner_signature_malformed"),
    (12345, "owner_signature_malformed"),
])
def test_malformed_signatures_are_refused_by_name(report, keys, value, code):
    _, public = keys
    review = unsigned(report)
    review["owner_signature"] = value
    with pytest.raises(ReviewError) as exc:
        certify_review(report, review, public_key=public)
    assert exc.value.code == code


def test_another_key_cannot_certify(report, keys):
    seed, _ = keys
    impostor = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    with pytest.raises(ReviewError) as exc:
        certify_review(report, signed(report, seed), public_key=impostor)
    assert exc.value.code == "owner_signature_mismatch"


# ————— ما يغطّيه التوقيع: ما حُكم فيه، لا الشكل —————

def test_changing_a_verdict_after_signing_breaks_it(report, keys):
    seed, public = keys
    review = signed(report, seed)
    review["decisions"][0]["verdict"] = "fail"
    review["decisions"][0]["severity"] = "critical"
    with pytest.raises(ReviewError) as exc:
        certify_review(report, review, public_key=public)
    assert exc.value.code == "owner_signature_mismatch"


def test_changing_the_reviewer_identity_after_signing_breaks_it(report, keys):
    seed, public = keys
    review = signed(report, seed)
    review["reviewer"]["identity"] = "عميلٌ آخر"
    with pytest.raises(ReviewError) as exc:
        certify_review(report, review, public_key=public)
    assert exc.value.code == "owner_signature_mismatch"


def test_dropping_a_decision_after_signing_breaks_it(report, keys):
    seed, public = keys
    review = signed(report, seed)
    review["decisions"].pop()
    with pytest.raises(ReviewError) as exc:
        certify_review(report, review, public_key=public)
    assert exc.value.code == "owner_signature_mismatch"


def test_a_signature_does_not_travel_to_another_run(report, keys, tmp_path):
    """توقيعُ تشغيلٍ لا يُصدِّق تشغيلًا آخر ولو تطابقت الأحكام."""
    seed, public = keys
    review = signed(report, seed)
    other = evaluate_suite(suite_data(), Provider(), tmp_path / "other",
                           run_id="run-other-0000")
    moved = {**unsigned(other), "owner_signature": review["owner_signature"]}
    with pytest.raises(ReviewError) as exc:
        certify_review(other, moved, public_key=public)
    assert exc.value.code == "owner_signature_mismatch"


def test_decision_order_does_not_change_the_message(report, keys):
    seed, public = keys
    review = signed(report, seed)
    review["decisions"].reverse()
    assert certify_review(report, review, public_key=public)["reviewer_identity_verified"]


def test_the_message_carries_its_own_domain(report, keys):
    seed, _ = keys
    review = signed(report, seed)
    message = verdict_message(report, review)
    assert message.startswith(b"diwan-human-verdict-v1\x00")
    content = signed_content(report, review)
    assert content["run_id"] == report["run_id"]
    assert {d["case_id"] for d in content["decisions"]} == {"case-1", "case-2"}
    assert all("answer_sha256" in d for d in content["decisions"])


def test_certification_never_mutates_the_report(report, keys):
    seed, public = keys
    original = copy.deepcopy(report)
    certify_review(report, signed(report, seed), public_key=public)
    assert report == original


def test_missing_public_key_is_a_named_refusal_not_a_pass(report, keys, tmp_path):
    """«تعذّر التحقق» ليس «تحقَّق» — ولا «فشل» (ق٢٥)."""
    seed, _ = keys
    with pytest.raises(ReviewError) as exc:
        certify_review(report, signed(report, seed), root=tmp_path)
    assert exc.value.code == "owner_public_key_unavailable"


# ————— الحكمُ المنشور الفعليّ، مقيسًا لا موصوفًا —————

def test_the_published_m14_verdict_is_not_a_certified_human_verdict():
    """الملفُّ الذي أوجب ق٤٥، مفحوصًا كما هو في المستودع.

    ثلاثُ علاماتٍ مستقلة، كلٌّ منها وحدها كافية للردّ:
    بلا توقيعِ مالك، وبشكلٍ خارج عقد المراجعة، وبرقمِ تشغيلٍ غيرِ الذي
    يحمله التقريرُ المنشور. ويبقى الملفُّ في مكانه شاهدًا — لا يُحذف.
    """
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "docs/probe/m14-human-sample-review.json"
    if not path.is_file():
        pytest.skip("الملف غير موجود في هذه النسخة")
    artifact = json.loads(path.read_text(encoding="utf-8"))
    assert "owner_signature" not in artifact
    assert artifact.get("attestation") is True, "الإقرارُ وحده كان يكفي قبل ق٤٥"
    assert artifact["run_id"] == "run-m14-20260921-144122-99bad2"
    published = (Path(__file__).resolve().parent.parent / "docs/M14-BENCHMARK-REPORT.md"
                 ).read_text(encoding="utf-8")
    assert artifact["run_id"] not in published, "حكمٌ على تشغيلٍ غيرِ المنشور"
    assert "run-m14-20260922-025351" in published


def test_the_signature_scope_does_not_promise_run_uniqueness(report, keys, tmp_path):
    """`run_id` هويةُ إعدادٍ لا هويةُ تشغيل — والاسمُ يجب أن يقول ذلك.

    رصده نقضُ ٢٢ سبتمبر في شيفرتي: تشغيلان بجوابين متناقضين يحملان
    المعرّفَ نفسه، لأنه يُشتقّ من بصمة الإعداد وحدها. والحمايةُ قائمة —
    لكن من `report_sha256` لا من `run_id`.
    """
    seed, public = keys
    summary = certify_review(report, signed(report, seed), public_key=public)
    assert "run_identity" not in summary["signature_scope"]
    assert "config_identity" in summary["signature_scope"]
    assert "run_id_is_config_derived_not_run_unique" in summary["limits"]

    other = evaluate_suite(suite_data(), Provider(), tmp_path / "other")
    assert other["run_id"] == report["run_id"], "المعرّفُ نفسه لإعدادٍ نفسه"
    # وما يحمل الحماية فعلًا هو بصمةُ التقرير، وهي داخل الموقَّع عليه
    assert signed_content(report, signed(report, seed))["report_sha256"] \
        == review_binding(report)["report_sha256"]
