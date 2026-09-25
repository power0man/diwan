"""اختبارات التوجيه السيادي ثلاثي الطبقات وتعقيم البيانات الحساسة (م١٧)."""
import pytest

from core.contracts import Message, Request, Response, Usage
from core.router_sovereign import (
    PIISanitizer,
    SovereignRouter,
    SovereignRoutingError,
    TierEndpoint,
)


def test_pii_sanitizer_saudi_id():
    text = "رقم الهوية الوطنية 1083928172 ورقم الإقامة 2491827364 يرجى التحقق."
    sanitized, token_map = PIISanitizer.sanitize(text)

    assert "1083928172" not in sanitized
    assert "2491827364" not in sanitized
    assert "[هوية_محجوبة_هوية_1]" in sanitized
    assert "[هوية_محجوبة_هوية_2]" in sanitized
    assert token_map["[هوية_محجوبة_هوية_1]"] == "1083928172"
    assert token_map["[هوية_محجوبة_هوية_2]"] == "2491827364"

    # التحقق من استرجاع النص الأصلي
    restored = PIISanitizer.desanitize(sanitized, token_map)
    assert restored == text


def test_pii_sanitizer_phone_and_email():
    text = "للتواصل عبر الجوال 0501234567 أو الدولي +966551234567 والبريد contact@example.sa"
    sanitized, token_map = PIISanitizer.sanitize(text)

    assert "0501234567" not in sanitized
    assert "+966551234567" not in sanitized
    assert "contact@example.sa" not in sanitized

    restored = PIISanitizer.desanitize(sanitized, token_map)
    assert restored == text


def test_pii_sanitizer_no_pii():
    text = "هذا نص لغوي خالص لا يحتوي على أي أرقام هوية أو هواتف."
    sanitized, token_map = PIISanitizer.sanitize(text)
    assert sanitized == text
    assert token_map == {}
    assert PIISanitizer.desanitize(sanitized, token_map) == text


def test_sovereign_router_default_tiers():
    router = SovereignRouter()

    req_local = Request(
        messages=(Message("user", "مرحبا"),),
        model="local-model",
        model_version="1.0",
        max_output=100,
        deadline_s=10.0,
        data_policy="local_only",
        idempotency_key=None,
    )
    assert router.determine_tier(req_local) == "local_edge"

    req_regulated = Request(
        messages=(Message("user", "بيانات نظامية"),),
        model="sovereign-model",
        model_version="1.0",
        max_output=100,
        deadline_s=10.0,
        data_policy="regulated",
        idempotency_key=None,
    )
    assert router.determine_tier(req_regulated) == "local_edge"

    req_internal = Request(
        messages=(Message("user", "بيانات داخلية"),),
        model="sovereign-model",
        model_version="1.0",
        max_output=100,
        deadline_s=10.0,
        data_policy="internal",
        idempotency_key=None,
    )
    assert router.determine_tier(req_internal) == "sovereign_cloud"

    req_public = Request(
        messages=(Message("user", "نص عام"),),
        model="frontier-model",
        model_version="1.0",
        max_output=100,
        deadline_s=10.0,
        data_policy="public",
        idempotency_key=None,
    )
    assert router.determine_tier(req_public) == "frontier_zdr"


def test_sovereign_router_policy_violations():
    router = SovereignRouter()

    # محاولة توجيه local_only إلى السحابة
    req_local = Request(
        messages=(Message("user", "سري للغاية"),),
        model="frontier-model",
        model_version="1.0",
        max_output=100,
        deadline_s=10.0,
        data_policy="local_only",
        idempotency_key=None,
    )
    with pytest.raises(SovereignRoutingError) as exc_info:
        router.determine_tier(req_local, target_tier="sovereign_cloud")
    assert exc_info.value.code == "policy_violation_local_only"

    # محاولة توجيه regulated لخارج المملكة
    with pytest.raises(SovereignRoutingError) as exc_info2:
        router.determine_tier(req_local, target_tier="frontier_zdr")
    assert exc_info2.value.code == "policy_violation_local_only"

    req_reg = Request(
        messages=(Message("user", "سجلات حكومية"),),
        model="frontier-model",
        model_version="1.0",
        max_output=100,
        deadline_s=10.0,
        data_policy="regulated",
        idempotency_key=None,
    )
    with pytest.raises(SovereignRoutingError) as exc_info3:
        router.determine_tier(req_reg, target_tier="frontier_zdr")
    assert exc_info3.value.code == "policy_violation_regulated"


def test_sovereign_router_zdr_enforcement():
    # إنشاء طرف طليعي بلا اتفاقية Zero Data Retention
    endpoints = {
        "frontier_zdr": TierEndpoint("frontier_zdr", "مزود بلا ZDR", True, False, False),
    }
    router = SovereignRouter(endpoints=endpoints)
    req = Request(
        messages=(Message("user", "طلب عام"),),
        model="external-model",
        model_version="1.0",
        max_output=100,
        deadline_s=10.0,
        data_policy="public",
        idempotency_key=None,
    )
    with pytest.raises(SovereignRoutingError) as exc:
        router.determine_tier(req, target_tier="frontier_zdr")
    assert exc.value.code == "policy_violation_retention"


def test_sovereign_router_prepare_and_restore_roundtrip():
    router = SovereignRouter()

    req = Request(
        messages=(
            Message("system", "أنت مساعد ذكي."),
            Message("user", "المواطن ذو الهوية 1098765432 وهاتفه 0555555555 استفسر عن المعاملة."),
        ),
        model="frontier-model",
        model_version="1.0",
        max_output=200,
        deadline_s=10.0,
        data_policy="public",
        idempotency_key=None,
    )

    sanitized_req, token_map, tier = router.prepare_request(req, target_tier="frontier_zdr")
    assert tier == "frontier_zdr"
    assert "1098765432" not in sanitized_req.messages[1].content
    assert "0555555555" not in sanitized_req.messages[1].content
    assert len(token_map) == 2

    # محاكاة رد النموذج الخارجي باستخدام الرموز المستعارة
    mock_llm_response = Response(
        content=f"تم استلام طلب المواطن {list(token_map.keys())[0]} ورقم الاتصال {list(token_map.keys())[1]}.",
        usage=Usage(20, 15),
        stop_reason="complete",
        cost_micros=100,
        provider="mock_frontier",
        model_version="1.0",
    )

    final_response = router.restore_response(mock_llm_response, token_map)
    assert "1098765432" in final_response.content
    assert "0555555555" in final_response.content
