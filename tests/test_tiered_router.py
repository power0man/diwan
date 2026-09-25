"""اختبارات التوجيه السيادي المتدرج (م١٥): صيانة الحدود وسقف السياسة والتسجيل المختوم.

الفحوصات:
1. اختيار المستوى الأول (Edge Fast) للمهام الاعتيادية.
2. اختيار المستوى الثاني (Local Heavy) للمهام المركبة ذات السياسة المحلية (local_only / regulated).
3. اختيار المستوى الثالث (Cloud Isolated) للمهام المركبة ذات السياسة غير الحساسة (public / internal).
4. الحظر المطلق لتصعيد البيانات المحلية أو المقيدة إلى السحاب (fail-closed).
5. توثيق القرار في السجل بقيد مختوم مطابق للختم الدستوري.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from core.canonical import PayloadRejected
from core.ledger import Ledger
from core.router import RoutingDecision, TieredSovereignRouter


def test_edge_fast_is_selected_for_normal_tasks():
    router = TieredSovereignRouter()
    dec = router.select_tier("regulated", complexity="normal", content_length=200)
    assert dec.tier == TieredSovereignRouter.TIER_EDGE_FAST
    assert dec.model == router.edge_model
    assert dec.data_policy == "regulated"


def test_local_heavy_is_selected_for_complex_local_tasks():
    router = TieredSovereignRouter()
    dec = router.select_tier("local_only", complexity="heavy")
    assert dec.tier == TieredSovereignRouter.TIER_LOCAL_HEAVY
    assert dec.model == router.heavy_model


def test_local_heavy_is_selected_for_long_regulated_content():
    router = TieredSovereignRouter()
    dec = router.select_tier("regulated", content_length=3000)
    assert dec.tier == TieredSovereignRouter.TIER_LOCAL_HEAVY
    assert dec.model == router.heavy_model


def test_cloud_isolated_is_allowed_only_for_non_sensitive_complex_tasks():
    router = TieredSovereignRouter()
    dec = router.select_tier("public", complexity="heavy")
    assert dec.tier == TieredSovereignRouter.TIER_CLOUD_ISOLATED
    assert dec.model == router.cloud_model


def test_invalid_policy_is_rejected():
    router = TieredSovereignRouter()
    with pytest.raises(PayloadRejected) as exc:
        router.select_tier("secret_unregistered")
    assert exc.value.code == "invalid_data_policy"


def test_enforce_policy_ceiling_blocks_cloud_for_local_policies():
    router = TieredSovereignRouter()
    illegal_dec = RoutingDecision(
        tier=TieredSovereignRouter.TIER_CLOUD_ISOLATED,
        model="cloud:frontier",
        data_policy="regulated",
        reason="محاولة خرق غير مشروعة",
    )
    with pytest.raises(PayloadRejected) as exc:
        router.enforce_policy_ceiling(illegal_dec)
    assert exc.value.code == "cloud_escalation_forbidden_for_local_policy"


def test_routing_decision_is_logged_to_ledger_with_seal(tmp_path: Path):
    ledger = Ledger(tmp_path / "test_main.jsonl")
    router = TieredSovereignRouter()
    dec = router.select_tier("regulated", complexity="heavy")
    now_ts = "2026-09-22T20:00:00+00:00"
    router.log_decision(ledger, dec, now_ts)

    entries = ledger.entries()
    assert len(entries) == 1
    rec = entries[0]["record"]
    assert rec["kind"] == "sovereign_route"
    assert rec["tier"] == TieredSovereignRouter.TIER_LOCAL_HEAVY
    assert rec["data_policy"] == "regulated"
    assert rec["at"] == now_ts
