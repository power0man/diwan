"""اختبارات م١: عقود المعرفة وسجل العقد وسجل الأصول.

كل اختبار رفضٍ يفحص **رمز** الرفض لا وقوعه فحسب — فالرفض لسببٍ خاطئ
نجاحٌ زائف.
"""
from __future__ import annotations

import pytest

from core.acquisitions import Acquisition, SourceRegister, validated_acquisition
from core.canonical import PayloadRejected, digest
from core.knowledge import (
    BROADCAST,
    KnowledgeEvent,
    KnowledgeItem,
    NodeManifest,
    validated_event,
    validated_item,
    validated_manifest,
)
from core.registry import NodeRegistry


def item(**over) -> KnowledgeItem:
    base = dict(
        text="تنطبق هذه اللائحة على السفن التجارية.",
        lang="ar",
        domain="maritime",
        use_internal=True,
        use_distribution=False,
        source_id="claude-project-regulations-export",
        locus="المادة 1",
        originality="original",
    )
    base.update(over)
    return KnowledgeItem(**base)


def event(**over) -> KnowledgeEvent:
    base = dict(
        kind="item_published",
        from_node="maritime",
        to=BROADCAST,
        correlation_id="c-1",
        data_policy="internal",
        payload={"item_digest": "a" * 64},
        idempotency_key=None,
    )
    base.update(over)
    return KnowledgeEvent(**base)


def manifest(**over) -> NodeManifest:
    base = dict(
        name="maritime",
        contract_version=1,
        domains=("maritime",),
        accepts=("query",),
        data_policy_ceiling="regulated",
    )
    base.update(over)
    return NodeManifest(**base)


def rejects(fn, arg, code):
    with pytest.raises(PayloadRejected) as e:
        fn(arg)
    assert e.value.code == code, f"رمز الرفض {e.value.code!r} والمتوقع {code!r}"


# — المادة المعرفية —

def test_item_valid_passes():
    assert validated_item(item()) is not None


def test_item_fingerprint_deterministic():
    assert digest(item().fingerprint_payload()) == digest(item().fingerprint_payload())


def test_item_no_rights_rejected():
    rejects(validated_item, item(use_internal=False, use_distribution=False),
            "rights_none")


def test_item_rights_not_bool_rejected():
    rejects(validated_item, item(use_internal=1), "use_internal_type")


def test_item_empty_text_rejected():
    rejects(validated_item, item(text="  "), "text_missing")


def test_item_missing_source_rejected():
    rejects(validated_item, item(source_id=""), "source_id_missing")


def test_item_missing_locus_rejected():
    rejects(validated_item, item(locus=""), "locus_missing")


def test_item_unknown_originality_rejected():
    rejects(validated_item, item(originality="opinion"), "originality_unknown")


def test_translated_without_glossary_rejected():
    rejects(validated_item, item(originality="translated", glossary_ref=""),
            "glossary_required")
    # والمسافة ليست مسردًا ولا فراغًا مشروعًا — تُرفض رفضَ نظافة معرِّف
    rejects(validated_item, item(originality="translated", glossary_ref=" "),
            "glossary_ref_whitespace")


def test_translated_with_glossary_passes():
    assert validated_item(item(originality="translated",
                               glossary_ref="maritime-v1")) is not None


# — الرسالة —

def test_event_valid_passes():
    assert validated_event(event()) is not None


def test_event_unknown_kind_rejected():
    rejects(validated_event, event(kind="gossip"), "kind_unknown")


def test_event_unknown_policy_rejected():
    rejects(validated_event, event(data_policy="secret"), "data_policy_unknown")


def test_event_empty_payload_rejected():
    rejects(validated_event, event(payload={}), "payload_empty")


QUERY_PAYLOAD = {"question": "ما نطاق اللائحة؟", "domain": "maritime"}


def test_query_without_budget_cap_rejected():
    rejects(validated_event,
            event(kind="query", payload=QUERY_PAYLOAD, idempotency_key="q-1"),
            "budget_cap_required")


def test_query_without_idempotency_rejected():
    rejects(validated_event,
            event(kind="query", payload=QUERY_PAYLOAD, budget_cap_micros=100),
            "idempotency_key_required")


def test_query_fully_specified_passes():
    assert validated_event(event(kind="query", payload=QUERY_PAYLOAD,
                                 budget_cap_micros=100,
                                 idempotency_key="q-1")) is not None


def test_event_budget_cap_bool_rejected():
    rejects(validated_event, event(budget_cap_micros=True), "budget_cap_type")


# — عقد العقدة وسجلها —

def test_manifest_valid_passes():
    assert validated_manifest(manifest()) is not None


def test_manifest_reserved_name_rejected():
    rejects(validated_manifest, manifest(name=BROADCAST), "name_reserved")


def test_manifest_unknown_accepts_rejected():
    rejects(validated_manifest, manifest(accepts=("gossip",)), "accepts_unknown")


def test_manifest_unknown_ceiling_rejected():
    rejects(validated_manifest, manifest(data_policy_ceiling="secret"),
            "ceiling_unknown")


def test_registry_register_and_chain(tmp_path):
    reg = NodeRegistry(tmp_path / "nodes.jsonl")
    d1 = reg.register(manifest())
    assert reg.get("maritime")["manifest_digest"] == d1
    assert reg.verify()


def test_registry_identical_reregister_adds_no_entry(tmp_path):
    reg = NodeRegistry(tmp_path / "nodes.jsonl")
    reg.register(manifest())
    n = reg._ledger.count()
    reg.register(manifest())
    assert reg._ledger.count() == n


def test_registry_changed_manifest_appends_version(tmp_path):
    reg = NodeRegistry(tmp_path / "nodes.jsonl")
    reg.register(manifest())
    d2 = reg.register(manifest(contract_version=2))
    assert reg.get("maritime")["manifest_digest"] == d2
    assert len(reg.history("maritime")) == 2
    assert reg.verify()


# — سجل الأصول —

def acq(**over) -> Acquisition:
    base = dict(
        source_id="src-1",
        title="مصدر تجريبي",
        origin="https://example.org",
        verified_date="2026-09-20",
        use_internal=True,
        use_distribution=False,
        license_evidence="دليل تجريبي",
    )
    base.update(over)
    return Acquisition(**base)


def test_acquisition_valid_passes():
    assert validated_acquisition(acq()) is not None


def test_acquisition_bad_date_rejected():
    rejects(validated_acquisition, acq(verified_date="20-09-2026"),
            "verified_date_invalid")


def test_acquisition_empty_evidence_rejected():
    rejects(validated_acquisition, acq(license_evidence=" "),
            "license_evidence_missing")


def test_acquisition_both_false_allowed():
    # توثيق «مصدرٍ فُحص ورُفض» معرفةٌ مشروعة — البوابة على المواد لا القيد
    assert validated_acquisition(
        acq(use_internal=False, use_distribution=False)) is not None


def test_source_register_versioning(tmp_path):
    reg = SourceRegister(tmp_path / "acq.jsonl")
    reg.acquire(acq())
    n = reg._ledger.count()
    reg.acquire(acq())                      # مطابق: لا قيد جديد
    assert reg._ledger.count() == n
    d2 = reg.acquire(acq(use_distribution=True,
                         license_evidence="إذن مكتوب ورد"))
    assert reg.get("src-1")["acquisition_digest"] == d2
    assert reg.verify()
