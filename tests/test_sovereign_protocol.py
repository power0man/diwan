"""غ٥: بروتوكولُ الناقل السياديّ مسجَّلٌ قبل أيّ ناقل، والافتراضيُّ محليّ، وموجّهُ السياسات يطابق سقوفَه.

- الملفُّ مسجَّلٌ ببصمته: تعديلُه بعد ظهور نتيجةٍ يظهر هنا.
- لا ناقلَ مفعَّل، وغيرُ المحليّ يُرفض بالاسم في الواجهة وسطر الأوامر (tests/test_webui_sovereign_integration.py،
  tests/test_chat_sovereign.py).
- سقوفُ السياسات هي ما يفعله `core/router_sovereign.py` بعينه، وما لا يغادر الجهازَ فيها هو `LOCAL_ONLY_POLICIES` في العقد.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.contracts import DATA_POLICIES, LOCAL_ONLY_POLICIES, Message, Request
from core.router_sovereign import SovereignRouter, SovereignRoutingError

PROTOCOL = Path(__file__).resolve().parents[1] / "evaluation" / "protocols" / "sovereign_v1.json"
DATA = json.loads(PROTOCOL.read_text(encoding="utf-8"))
TIERS = ("local_edge", "sovereign_cloud", "frontier_zdr")


def test_the_protocol_is_registered_by_digest():
    assert hashlib.sha256(PROTOCOL.read_bytes()).hexdigest() == \
        "52d32ce649442e1d2d117f77e1ec1665db23f34e1082f7bd50371a3ef8ddc4f1"


def test_the_default_stays_local_and_no_carrier_is_enabled():
    assert DATA["status"] == "registered_not_run"
    assert DATA["default"] == {"tier": "local_edge", "carriers_enabled": [], "non_local_tier_refusal": "tier_unavailable"}
    assert DATA["leakage_gate"]["max_leaks"] == 0 and DATA["receipts"]["coverage"] == 1.0


def _request(policy):
    return Request(messages=(Message("user", "نص"),), model="m", model_version="v", max_output=10,
                   deadline_s=1.0, data_policy=policy, idempotency_key=None)


@pytest.mark.parametrize("policy", sorted(DATA_POLICIES))
def test_the_router_allows_exactly_the_registered_ceiling(policy):
    router = SovereignRouter()
    allowed = []
    for tier in TIERS:
        try:
            router.determine_tier(_request(policy), target_tier=tier)
            allowed.append(tier)
        except SovereignRoutingError:
            pass
    assert allowed == DATA["policy_ceilings"][policy]


def test_what_never_leaves_the_device_is_the_kernel_contract():
    local = {policy for policy, tiers in DATA["policy_ceilings"].items() if tiers == ["local_edge"]}
    assert local == set(LOCAL_ONLY_POLICIES) and set(DATA["policy_ceilings"]) == set(DATA_POLICIES)
