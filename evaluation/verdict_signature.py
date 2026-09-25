"""حكمُ المالك يُوقَّع بمفتاحه، وإلّا فليس حكمَ مالك (ق٤٥).

الواقعة التي أوجبت هذا الملف: صدر ملفُ تحكيمٍ يحمل
`reviewer: "حسين الرابغي (تحكيم معتمد عبر Gemini…)"` و`attestation: true`،
وكاتبُه `tools/adjudicate_m14_human_sample.py` — أربعةٌ وثمانون سطرًا لا
تستورد إلا `json` و`pathlib`: لا شبكة، ولا نموذج، ولا تحكيم. أحكامُه
مكتوبةٌ في متنه.

و`validate_review` كانت تقبله: لأن `attestation: true` **إقرارٌ يكتبه مَن
يكتب الملف**. فأيُّ عميلٍ يستطيع أن يُقرّ باسم المالك.

فالفرقُ الذي يقيمه هذا الملف: `validate_review` تبقى كما هي — عقدٌ بنيويّ
يقول صراحةً `reviewer_identity_verified: False`. وفوقها `certify_review`،
وهي **الطريقُ الوحيد** إلى `reviewer_identity_verified: True`، ولا تمرّ
إلا بتوقيع Ed25519 يتحقق بالمفتاح العامّ المُثبَّت في المستودع. والمفتاحُ
الخاصّ في سلسلة مفاتيح ماك المالك، لا يملكه عميل.

والموقَّع عليه ليس الملفَّ كلَّه بل **ما حُكم فيه**: بصمةُ التقرير، وبصمةُ
كلِّ جوابٍ مع حكمه، وهويةُ المراجع، ومعها `run_id`. فتبديلُ جوابٍ أو حكمٍ
أو تشغيلٍ يُبطل التوقيع، وإضافةُ حقلٍ شكليّ لا تُبطله.

**ودقّةٌ تلزم**: `run_id` **هويةُ إعدادٍ لا هويةُ تشغيل** — يُشتقّ من بصمة
الإعداد وحدها (`capabilities.py:300`)، فتشغيلان بجوابين متناقضين يحملان
المعرّفَ نفسه. فالذي يمنع نقلَ التوقيع بينهما هو `report_sha256` لا هو.

**الحد المُعلَن**: التوقيع يُثبت أن حاملَ المفتاح أقرّ بهذه الأحكام على
هذه الأجوبة. لا يُثبت أن إنسانًا قرأها، ولا أن الحكم صائب. ومن سلّم مفتاحه
لعميل، سلّم صوتَه.
"""
from __future__ import annotations

from pathlib import Path

from core.signing import (ED25519, SigningRefused, load_trusted_public_key,
                          verify_signature_bytes)
from evaluation.capabilities import _json_bytes
from evaluation.human_review import (ReviewError, _reject, review_binding,
                                     summarize_review, validate_review)

VERDICT_DOMAIN = b"diwan-human-verdict-v1\x00"
SIGNATURE_FIELD = "owner_signature"
_HEX128 = 128


def signed_content(report: dict, review: dict) -> dict:
    """ما يُوقَّع عليه: الهويةُ وما حُكم فيه — لا الحقولُ الشكلية."""
    binding = review_binding(report)
    return {"kind": binding["kind"], "suite_id": binding["suite_id"],
            "run_id": binding["run_id"], "suite_sha256": binding["suite_sha256"],
            "config_sha256": binding["config_sha256"],
            "report_sha256": binding["report_sha256"],
            "reviewer_identity": review["reviewer"]["identity"],
            "decisions": sorted(
                ({"case_id": d["case_id"], "answer_sha256": d["answer_sha256"],
                  "verdict": d["verdict"], "severity": d["severity"]}
                 for d in review["decisions"]),
                key=lambda d: d["case_id"])}


def verdict_message(report: dict, review: dict) -> bytes:
    """رسالةُ التوقيع بمجالٍ مخصوص: لا يُعاد استعمال توقيعِ مرساةٍ هنا."""
    return VERDICT_DOMAIN + _json_bytes(signed_content(report, review))


def _signature_bytes(review: dict) -> bytes:
    raw = review.get(SIGNATURE_FIELD)
    if raw is None:
        _reject("review." + SIGNATURE_FIELD, "owner_signature_missing",
                "حكمٌ بلا توقيع المالك ليس حكمَ مالك [ق٤٥]")
    if not isinstance(raw, str) or not raw.startswith(ED25519 + ":"):
        _reject("review." + SIGNATURE_FIELD, "owner_signature_malformed",
                "توقيعٌ على شكل ED25519:<hex> مطلوب")
    body = raw[len(ED25519) + 1:]
    if len(body) != _HEX128 or any(c not in "0123456789abcdef" for c in body):
        _reject("review." + SIGNATURE_FIELD, "owner_signature_malformed",
                "توقيعٌ سداسيٌّ صغيرُ الحروف بطول ١٢٨ مطلوب")
    return (raw + "\n").encode("ascii")


def certify_review(report: dict, review: dict, *, public_key: bytes | None = None,
                   root: Path | None = None) -> dict:
    """الطريقُ الوحيد إلى `reviewer_identity_verified: True`.

    يرفض رفضًا مسمّى: بلا توقيع، أو بتوقيعٍ لا يطابق ما حُكم فيه، أو بغياب
    مكتبة التعمية — و«تعذّر التحقق» يُميَّز عن «فشل التحقق» (ق٢٥/ق٣٩).
    """
    validate_review(report, review)
    signature = _signature_bytes(review)
    if public_key is None:
        try:
            public_key = load_trusted_public_key(root=root)
        except SigningRefused as exc:
            _reject("trust", "owner_public_key_unavailable", exc.reason)
    try:
        verify_signature_bytes(signature, verdict_message(report, review),
                               public_key=public_key)
    except SigningRefused as exc:
        code = ("owner_signature_mismatch" if exc.code == "signature_mismatch"
                else "owner_signature_unverifiable")
        _reject("review." + SIGNATURE_FIELD, code, exc.reason)
    summary = summarize_review(report, review)
    summary["reviewer_identity_verified"] = True
    # «run_identity» كان اسمًا يَعِد بأكثر مما يفعل — وهو عيبي أنا، رصده
    # نقضُ ٢٢ سبتمبر وأعدتُ إنتاجه: `run_id` يُشتقّ من بصمة الإعداد وحدها
    # (`capabilities.py:300`)، فتشغيلان بجوابين متناقضين يحملان المعرّفَ
    # نفسه. والحمايةُ قائمةٌ فعلًا لكن من حقلٍ آخر: `report_sha256` يتغيّر
    # بتغيّر الأجوبة، فالتوقيعُ يُبطَل. فالاسمُ صار يقول أيَّ هويةٍ يحمل.
    summary["signature_scope"] = ["config_identity", "report_digest",
                                  "per_answer_digests", "per_case_verdicts",
                                  "reviewer_identity"]
    summary["limits"] = ["key_holder_attested_not_human_reading_proven",
                         "verdict_correctness_is_not_attested",
                         "run_id_is_config_derived_not_run_unique"]
    return summary


def sign_review(report: dict, review: dict, *, private_seed: bytes) -> str:
    """يوقّع بالبذرة الخاصة — تُقرأ من سلسلة مفاتيح ماك المالك، لا من ملف."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise ReviewError("signing", "owner_signature_unverifiable",
                          "مكتبة التعمية غير متاحة") from exc
    validate_review(report, review)
    key = Ed25519PrivateKey.from_private_bytes(private_seed)
    return ED25519 + ":" + key.sign(verdict_message(report, review)).hex()
