"""اختبارات عدم تكرار العيوب المُثبَتة — البند ٩ في سجل الخلاف.

كل اختبارٍ هنا يقابل عيبًا **أُثبت بالتشغيل** في النماذج الموروثة. ووجودُ
الاختبار شرطُ قبولٍ للمكوّن المقابل: لا يُقبل مكوّنٌ لم يجتز اختبار عيبه.

ولا يدّعي هذا الملف خلوّ النواة من كل عيب؛ يدّعي **عدم تكرار المُثبَت**.
"""
import math

import pytest

from core.budget import Budget, BudgetRefused
from core.canonical import PayloadRejected, digest
from core.contracts import Message, Request
from core.ledger import Ledger, LedgerCorrupt
from core.run import RouteRefused, execute
from core.validate import validated
from evaluation.disclosure import Case, DisclosureRefused, EvaluationSession
from providers.echo import EchoProvider


def _req(**over):
    base = dict(messages=(Message("user", "نصّ"),), model="echo", model_version="1",
                max_output=16, deadline_s=5.0, data_policy="local_only",
                idempotency_key=None, tools=())
    base.update(over)
    return Request(**base)


# ── العيب: تصنيف البيانات الغائب يفشل مفتوحًا فيسلك مسار السحابة ──

def test_missing_data_policy_is_rejected():
    with pytest.raises(PayloadRejected) as e:
        validated(_req(data_policy=""))
    assert e.value.code == "data_policy_missing"


def test_unknown_data_policy_is_rejected():
    with pytest.raises(PayloadRejected) as e:
        validated(_req(data_policy="privat"))          # خطأ إملائي
    assert e.value.code == "data_policy_unknown"


def test_local_only_never_reaches_a_remote_provider(tmp_path):
    class Remote:
        name, is_local = "remote", False
        def estimate_micros(self, r): return 1
        def complete(self, r): raise AssertionError("ما كان ينبغي أن يُنادى")

    with pytest.raises(RouteRefused) as e:
        execute(_req(data_policy="local_only"), Remote(),
                Budget(10_000, 10_000), Ledger(tmp_path / "l.jsonl"))
    assert e.value.code == "policy_requires_local"


# ── العيب: بوابةٌ تمرّ على غياب — مجموعةُ تقييمٍ فارغة تنجح ──

def test_empty_evaluation_suite_is_refused():
    with pytest.raises(DisclosureRefused) as e:
        EvaluationSession(cases=(), max_attempts=3)
    assert e.value.code == "empty_suite"


def _suite(n=6, max_attempts=2, min_aggregate=5):
    return EvaluationSession(
        cases=tuple(Case(f"c{i}", f"س{i}", f"ج{i}") for i in range(n)),
        max_attempts=max_attempts, min_aggregate=min_aggregate)


def test_aggregate_refuses_when_nothing_scored():
    s = _suite()
    s.begin_attempt()
    with pytest.raises(DisclosureRefused) as e:
        s.aggregate()
    assert e.value.code == "nothing_scored"


def test_aggregate_refuses_a_single_case_result():
    """تجميعُ حالةٍ واحدة كشفٌ لها — فالقناة المجمَّعة لا تصير قناةً فرديّة."""
    s = _suite()
    s.begin_attempt()
    s.record("c0", True)
    with pytest.raises(DisclosureRefused) as e:
        s.aggregate()
    assert e.value.code == "aggregate_too_small"


def test_no_recording_without_a_counted_attempt():
    """العدّاد بوّابة لا بيانٌ تقريريّ: لا تسجيلَ بلا محاولةٍ مفتوحة."""
    s = _suite()
    with pytest.raises(DisclosureRefused) as e:
        s.record("c0", True)
    assert e.value.code == "no_open_attempt"
    with pytest.raises(DisclosureRefused) as e2:
        s.aggregate()
    assert e2.value.code == "no_open_attempt"


# ── العيب: قبولُ دليلٍ بمجرّد كونه غير فارغ («x» يغلق بوابة) ──

def test_truthy_string_is_not_a_model_or_version():
    for field in ("model", "model_version"):
        with pytest.raises(PayloadRejected):
            validated(_req(**{field: "   "}))


def test_attempts_are_declared_and_exhaustible():
    s = _suite(max_attempts=1)
    s.begin_attempt()
    for cid in ("c0", "c1", "c2", "c3", "c4"):
        s.record(cid, True)
    s.aggregate()                         # التجميع يُغلق المحاولة فتُعَدّ
    with pytest.raises(DisclosureRefused) as e:
        s.begin_attempt()
    assert e.value.code == "attempts_exhausted"


# ── العيب: أعدادٌ سالبة وغير منتهية تجتاز فحص ">= 0" ──

def test_negative_estimate_is_refused():
    with pytest.raises(BudgetRefused) as e:
        Budget(1_000, 1_000).reserve("h", -5)
    assert e.value.code == "estimate_negative"


def test_infinite_deadline_is_refused():
    with pytest.raises(PayloadRejected) as e:
        validated(_req(deadline_s=math.inf))
    assert e.value.code == "deadline_not_finite"


def test_non_positive_max_output_is_refused():
    with pytest.raises(PayloadRejected) as e:
        validated(_req(max_output=0))
    assert e.value.code == "max_output_not_positive"


# ── العيب: حمولةٌ واحدة تعطي بصمتين ──

def test_same_payload_same_digest():
    a = {"ب": 1, "أ": [1, 2, {"ج": None}]}
    b = {"أ": [1, 2, {"ج": None}], "ب": 1}          # ترتيبٌ مختلف، محتوًى واحد
    assert digest(a) == digest(b)


def test_float_is_refused_before_hashing():
    with pytest.raises(PayloadRejected) as e:
        digest({"مبلغ": 1.0})
    assert e.value.code == "float_not_allowed"


def test_int_above_2_53_is_refused():
    with pytest.raises(PayloadRejected) as e:
        digest({"id": 9007199254740993})
    assert e.value.code == "int_out_of_safe_range"


def test_deadline_is_not_part_of_the_fingerprint():
    """المهلة بيانات تشغيلٍ عابرة: تشغيلان متكافئان يعطيان بصمة واحدة."""
    assert digest(_req(deadline_s=5.0).fingerprint_payload()) == \
           digest(_req(deadline_s=30.0).fingerprint_payload())


# ── العيب: حارسٌ لا يحرس — تغييرٌ يمرّ بلا كشف ──

def test_ledger_detects_tampering(tmp_path):
    p = tmp_path / "l.jsonl"
    led = Ledger(p)
    led.append({"a": 1})
    led.append({"a": 2})
    assert led.verify_chain()
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text(lines[0] + "\n", encoding="utf-8")          # حذفُ قيد
    assert Ledger(p).verify_chain()                           # الحذف من الذيل لا يكسر
    p.write_text(lines[1] + "\n", encoding="utf-8")           # حذفُ الأوّل
    with pytest.raises(LedgerCorrupt):
        Ledger(p).verify_chain()


def test_ledger_has_no_update_or_delete_api():
    assert not hasattr(Ledger, "update")
    assert not hasattr(Ledger, "delete")


# ── العيب: بوابةُ الخصوصية تُقاس بالصدق المنطقي لا بالهوية ──

class _LocalByMethod:
    name = "cloud"
    def is_local(self):            # دالّةٌ غير مُستدعاة: صادقةٌ دائمًا
        return False
    def estimate_micros(self, r): return 1
    def complete(self, r): raise AssertionError("ما كان ينبغي أن يُنادى")


class _LocalByString:
    name, is_local = "cloud", "false"
    def estimate_micros(self, r): return 1
    def complete(self, r): raise AssertionError("ما كان ينبغي أن يُنادى")


class _LocalByList:
    name, is_local = "cloud", [0]
    def estimate_micros(self, r): return 1
    def complete(self, r): raise AssertionError("ما كان ينبغي أن يُنادى")


class _LocalByOne:
    name, is_local = "cloud", 1
    def estimate_micros(self, r): return 1
    def complete(self, r): raise AssertionError("ما كان ينبغي أن يُنادى")


@pytest.mark.parametrize("prov", [_LocalByMethod, _LocalByString, _LocalByList, _LocalByOne])
@pytest.mark.parametrize("policy", ["local_only", "regulated"])
def test_truthy_is_local_does_not_open_the_privacy_gate(prov, policy, tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    with pytest.raises(RouteRefused) as e:
        execute(_req(data_policy=policy), prov(), Budget(10_000, 10_000), led)
    assert e.value.code == "policy_requires_local"
    assert led.entries()[0]["record"]["error_code"] == "policy_requires_local"


# ── العيب: كلُّ مخرجٍ غير ProviderError يُسرّب الحجز ولا يُقيّد ──

@pytest.mark.parametrize("boom", [TimeoutError, ConnectionError, OSError, RuntimeError])
def test_any_interruption_settles_and_records(boom, tmp_path):
    class Exploding(EchoProvider):
        def complete(self, request):
            raise boom("انقطاع")

    led, bud = Ledger(tmp_path / "l.jsonl"), Budget(10_000, 50_000)
    with pytest.raises(boom):
        execute(_req(idempotency_key="K"), Exploding(), bud, led)
    assert bud.outstanding_micros == 0            # لا حجزَ متسرّبًا
    rec = led.entries()[-1]["record"]
    assert rec["kind"] == "error" and rec["settled_micros"] > 0
    # ومفتاح عدم التكرار صار له أثرٌ يمنع إعادة الفعل
    assert led.find_by_idempotency_key("K") is not None


def test_a_bad_response_does_not_move_money(tmp_path):
    class Bad(EchoProvider):
        def complete(self, request):
            r = super().complete(request)
            return type(r)(r.content, r.usage, r.stop_reason, -5)

    led, bud = Ledger(tmp_path / "l.jsonl"), Budget(10_000, 50_000)
    before = bud.day_remaining_micros
    with pytest.raises(RouteRefused) as e:
        execute(_req(), Bad(), bud, led)
    assert e.value.code == "cost_negative"
    assert bud.outstanding_micros == 0
    assert led.entries()[-1]["record"]["kind"] == "error"


# ── العيب: الرفض يُسمّم مفتاح عدم التكرار إلى الأبد ──

def test_a_refusal_does_not_poison_the_key(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    with pytest.raises(RouteRefused):                       # رفضٌ بمفتاح الإيقاف
        execute(_req(idempotency_key="K"), EchoProvider(),
                Budget(10_000, 50_000, kill_switch=True), led)
    assert led.entries()[0]["record"]["kind"] == "refused"
    out = execute(_req(idempotency_key="K"), EchoProvider(),
                  Budget(10_000, 50_000), led)              # والسقف رُفع
    assert not out.replayed and out.response is not None


# ── العيب: نصٌّ لا يُرمَّز UTF-8 ينفجر بعد الفحص ──

def test_lone_surrogate_is_refused_by_the_checker():
    with pytest.raises(PayloadRejected) as e:
        digest({"t": "\ud800"})
    assert e.value.code == "string_not_encodable"


# ── العيب: تصنيفٌ غير قابل للتجزئة يكسر فحص العضوية ──

@pytest.mark.parametrize("bad", [{"level": "regulated"}, ["local_only"], 7])
def test_non_string_data_policy_is_refused(bad):
    with pytest.raises(PayloadRejected) as e:
        validated(_req(data_policy=bad))
    assert e.value.code == "data_policy_type"


# ── العيب: حقلٌ عُلويّ مدسوسٌ في السجل لا يدخل البصمة ──

def test_ledger_rejects_an_injected_top_level_field(tmp_path):
    p = tmp_path / "l.jsonl"
    led = Ledger(p)
    led.append({"a": 1})
    import json as _json
    e = _json.loads(p.read_text(encoding="utf-8").strip())
    e["note"] = "مدسوس"
    p.write_text(_json.dumps(e, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        Ledger(p).verify_chain()


def test_anchor_detects_a_truncated_tail(tmp_path):
    p = tmp_path / "l.jsonl"
    led = Ledger(p)
    led.append({"a": 1}); led.append({"a": 2}); led.anchor()
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text(lines[0] + "\n", encoding="utf-8")          # قصُّ الذيل
    led2 = Ledger(p)
    assert led2.verify_chain() is True                        # السلسلة وحدها لا تكشف
    with pytest.raises(LedgerCorrupt):
        led2.verify_chain(strict=True)                        # والمرساة تكشف


# ════════════════════════════════════════════════════════════════════
# عيوب تدقيق م١ العدائي — ١٢ عيبًا مؤكدًا (صفر مدحوض) أُصلحت كلها
# ════════════════════════════════════════════════════════════════════

from core.acquisitions import (Acquisition, SourceRegister,
                               validated_acquisition)
from core.knowledge import (KnowledgeEvent, KnowledgeItem, NodeManifest,
                            policy_within_ceiling, validated_event,
                            validated_item, validated_manifest)
from core.registry import NodeRegistry


def _item(**over):
    base = dict(text="نصّ", lang="ar", domain="maritime", use_internal=True,
                use_distribution=False, source_id="src", locus="ص1",
                originality="original")
    base.update(over)
    return KnowledgeItem(**base)


def _ev(**over):
    base = dict(kind="item_published", from_node="maritime", to="broadcast",
                correlation_id="c", data_policy="internal",
                payload={"item_digest": "a" * 64}, idempotency_key=None)
    base.update(over)
    return KnowledgeEvent(**base)


def _acq(**over):
    base = dict(source_id="s", title="ت", origin="https://x",
                verified_date="2026-09-20", use_internal=True,
                use_distribution=False, license_evidence="دليل")
    base.update(over)
    return Acquisition(**base)


def _man(**over):
    base = dict(name="maritime", contract_version=1, domains=("maritime",),
                accepts=("query",), data_policy_ceiling="regulated")
    base.update(over)
    return NodeManifest(**base)


# ── العيب ١/١١: حمولة الرسالة نصّ حرّ يجتاز — «لا نصّ حرّ» وعدًا لا فرضًا ──

def test_free_text_payload_is_rejected():
    with pytest.raises(PayloadRejected) as e:
        validated_event(_ev(payload={"free_text": "قول مرسل"}))
    assert e.value.code == "payload_keys_unknown"


def test_short_item_digest_is_rejected():
    with pytest.raises(PayloadRejected) as e:
        validated_event(_ev(payload={"item_digest": "xyz"}))
    assert e.value.code == "item_digest_invalid"


def test_full_item_payload_revalidated():
    bad = _item(use_internal=False)  # بلا أي إذن — يجب أن يرتد من داخل الحمولة
    with pytest.raises(PayloadRejected) as e:
        validated_event(_ev(payload=bad.fingerprint_payload()))
    assert e.value.code == "rights_none"
    ok = _item()
    assert validated_event(_ev(payload=ok.fingerprint_payload())) is not None


# ── العيب ٢: نفس (الاسم، النسخة) بمحتوى مختلف، والنكوص الصامت ──

def test_same_version_different_content_rejected(tmp_path):
    reg = NodeRegistry(tmp_path / "n.jsonl")
    reg.register(_man())
    with pytest.raises(PayloadRejected) as e:
        reg.register(_man(domains=("maritime", "law")))
    assert e.value.code == "contract_version_reused"


def test_version_downgrade_rejected(tmp_path):
    reg = NodeRegistry(tmp_path / "n.jsonl")
    reg.register(_man(contract_version=3))
    with pytest.raises(PayloadRejected) as e:
        reg.register(_man(contract_version=2))
    assert e.value.code == "contract_version_downgrade"
    # ونسخة قديمة قائمة بمحتوى مغاير تُرفض بوصفها إعادة استعمال
    reg.register(_man(contract_version=4))
    with pytest.raises(PayloadRejected) as e2:
        reg.register(_man(contract_version=3, domains=("x",)))
    assert e2.value.code == "contract_version_reused"


# ── العيب ٤: صيغ ISO غير YYYY-MM-DD كانت تجتاز فتكسر عديمة التكرار ──

def test_compact_and_week_dates_rejected():
    for d in ("20260920", "2026-W38-6"):
        with pytest.raises(PayloadRejected) as e:
            validated_acquisition(_acq(verified_date=d))
        assert e.value.code == "verified_date_invalid", d


# ── العيب ٥: قيد ناقص الحقل المفتاحي كان يفجّر KeyError عاريًا ──

def test_poisoned_record_raises_named_corruption(tmp_path):
    p = tmp_path / "n.jsonl"
    Ledger(p).append({"kind": "node_registered"})
    with pytest.raises(LedgerCorrupt):
        NodeRegistry(p).current()


# ── العيب ٦: حجز البثّ كان حرفيًّا فتفلت «broadcast » وBroadcast ──

def test_broadcast_like_names_rejected():
    for name in ("broadcast ", "Broadcast", " BROADCAST"):
        with pytest.raises(PayloadRejected) as e:
            validated_manifest(_man(name=name))
        assert e.value.code in ("name_reserved", "name_whitespace"), name


def test_broadcast_cannot_send():
    with pytest.raises(PayloadRejected) as e:
        validated_event(_ev(from_node="broadcast"))
    assert e.value.code == "from_node_reserved"


# ── العيب ٧ (القاتل): التزوير بالإلحاق الملتفّ كان بلا أثر ──

def test_forged_acquisition_detected(tmp_path):
    p = tmp_path / "a.jsonl"
    reg = SourceRegister(p)
    reg.acquire(_acq())
    Ledger(p).append({"kind": "acquired", "source_id": "مزوّر",
                      "acquisition": {"use_distribution": True},
                      "acquisition_digest": "deadbeef" * 8})
    with pytest.raises(LedgerCorrupt):
        reg.verify()


def test_forged_manifest_digest_detected(tmp_path):
    p = tmp_path / "n.jsonl"
    reg = NodeRegistry(p)
    reg.register(_man())
    good = _man(name="other").fingerprint_payload()
    Ledger(p).append({"kind": "node_registered", "name": "other",
                      "manifest": good, "manifest_digest": "0" * 64})
    with pytest.raises(LedgerCorrupt):
        reg.verify()


# ── العيب ٨: «الحقوق عند الباب» كانت موعودة بلا بوابة فرض ──

def test_admission_gate_enforces_source_rights(tmp_path):
    reg = SourceRegister(tmp_path / "a.jsonl")
    reg.acquire(_acq(source_id="internal-only"))
    with pytest.raises(PayloadRejected) as e:
        reg.admitted_item(_item(source_id="internal-only", use_distribution=True))
    assert e.value.code == "distribution_exceeds_source"
    with pytest.raises(PayloadRejected) as e2:
        reg.admitted_item(_item(source_id="لا-وجود-له"))
    assert e2.value.code == "source_unacquired"
    reg.acquire(_acq(source_id="rejected-src", use_internal=False,
                     use_distribution=False,
                     license_evidence="فُحص ورُفض"))
    with pytest.raises(PayloadRejected) as e3:
        reg.admitted_item(_item(source_id="rejected-src"))
    assert e3.value.code == "internal_exceeds_source"
    assert reg.admitted_item(_item(source_id="internal-only")) is not None


# ── العيب ٩: كشف قصّ الذيل كان يفشل مفتوحًا (فحص بلا صرامة ثم رسو) ──

def test_tail_truncation_caught_by_seal(tmp_path):
    # ترقية ق٢١: الختم صار يُفحص في **كل** قراءة لا في الصارمة فقط —
    # فقصُّ الذيل يُغلق السجل الحاكم كله فورًا
    p = tmp_path / "a.jsonl"
    reg = SourceRegister(p)
    reg.acquire(_acq(source_id="s1"))
    reg.acquire(_acq(source_id="s2"))
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    p.write_text(lines[0], encoding="utf-8")          # قصّ الذيل
    with pytest.raises(LedgerCorrupt):
        reg.current()                                  # أي قراءة تكشفه
    with pytest.raises(LedgerCorrupt):
        reg.verify(strict=True)


# ── العيب ١٠: قراءة الحقوق النافذة كانت لا تفحص السلسلة ──

def test_reading_tampered_register_fails_closed(tmp_path):
    p = tmp_path / "a.jsonl"
    reg = SourceRegister(p)
    reg.acquire(_acq())
    tampered = p.read_text(encoding="utf-8").replace(
        '"use_distribution":false', '"use_distribution":true')
    assert tampered != p.read_text(encoding="utf-8")
    p.write_text(tampered, encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        reg.get("s")


# ── العيب ١٢: «السقف» كان بلا سُلَّم معرَّف فالبوابة غير محسوبة ──

def test_policy_ladder_is_total_and_computable():
    assert policy_within_ceiling("public", "local_only")
    assert policy_within_ceiling("regulated", "regulated")
    assert not policy_within_ceiling("regulated", "internal")
    assert not policy_within_ceiling("local_only", "regulated")
    with pytest.raises(PayloadRejected):
        policy_within_ceiling("secret", "internal")


# ── عرف م٠: مفتاح عدم التكرار بيانات إعادةٍ لا حمولة — خارج البصمة ──

def test_event_fingerprint_excludes_idempotency_key():
    a = _ev(kind="query", payload={"question": "س؟", "domain": "m"},
            budget_cap_micros=10, idempotency_key="k-1")
    b = _ev(kind="query", payload={"question": "س؟", "domain": "m"},
            budget_cap_micros=10, idempotency_key="k-2")
    assert digest(a.fingerprint_payload()) == digest(b.fingerprint_payload())


# ════════════════════════════════════════════════════════════════════
# عيوب تدقيق م٢ العدائي — ١٢ عيبًا مؤكدًا (صفر مدحوض) أُصلحت كلها
# ════════════════════════════════════════════════════════════════════

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "tools"))

from core.corpus import CorpusCatalog, CorpusFile
from ingest_regulations import ARTICLE_RE, chunk_document as _chunk


def _src(tmp_path):
    reg = SourceRegister(tmp_path / "src" / "a.jsonl")
    reg.acquire(_acq(source_id="src"))
    return reg


def _page(**over):
    return _item(source_id="src", **over)


# ── العيب ٦ (القاتل): فحص الفهرس كان يطابق كل الرؤوس التاريخية ──

def test_catalog_correction_flow_survives_verify(tmp_path):
    reg = _src(tmp_path)
    cf = CorpusFile(tmp_path / "d.jsonl")
    h1 = cf.ingest("doc-1", [_page()], reg)
    cat = CorpusCatalog(tmp_path / "_catalog.jsonl")
    cat.record("doc-1", "d.jsonl", 1, h1)
    cat.anchor()
    assert cat.verify(strict=True, root=tmp_path)
    # تصحيح مشروع: قيد نسخة جديد عبر البوابة نفسها
    h2 = cf.ingest("doc-1", [_page(locus="المادة (1) — نسخة مصححة")], reg)
    cat.record("doc-1", "d.jsonl", 2, h2)
    assert cat.verify(strict=True, root=tmp_path)   # كان يرمي إلى الأبد


# ── العيبان ١ و١١: حمولة مهرّبة المفاتيح/النسخة كانت تجتاز الفحص العميق ──

def test_smuggled_page_payload_detected(tmp_path):
    reg = _src(tmp_path)
    cf = CorpusFile(tmp_path / "d.jsonl")
    cf.ingest("doc-1", [_page()], reg)
    payload = _page().fingerprint_payload()
    payload["smuggled"] = "بيانات مهرّبة"
    payload["schema_version"] = 999
    Ledger(tmp_path / "d.jsonl").append(
        {"kind": "page", "doc_id": "doc-1",
         "item": payload, "item_digest": digest(payload)})
    with pytest.raises(LedgerCorrupt):
        cf.verify(register=reg)


# ── العيب ٣: الوثيقة الصفرية ورأس GENESIS كانا يُقيَّدان صامتَين ──

def test_empty_doc_and_genesis_head_rejected(tmp_path):
    reg = _src(tmp_path)
    cf = CorpusFile(tmp_path / "d.jsonl")
    with pytest.raises(PayloadRejected) as e:
        cf.ingest("doc-1", [], reg)
    assert e.value.code == "doc_empty"
    cat = CorpusCatalog(tmp_path / "_catalog.jsonl")
    with pytest.raises(PayloadRejected) as e2:
        cat.record("doc-1", "d.jsonl", 1, "0" * 64)
    assert e2.value.code == "head_invalid"
    with pytest.raises(PayloadRejected) as e3:
        cat.record("doc-1", "d.jsonl", 0, "a" * 64)
    assert e3.value.code == "pages_not_positive"


# ── العيب ٤: فحص وثيقة مفقودة كان يُنشئ ملفها فارغًا ──

def test_missing_doc_file_detected_without_creating_it(tmp_path):
    reg = _src(tmp_path)
    cf = CorpusFile(tmp_path / "d.jsonl")
    h = cf.ingest("doc-1", [_page()], reg)
    cat = CorpusCatalog(tmp_path / "_catalog.jsonl")
    cat.record("doc-1", "d.jsonl", 1, h)
    (tmp_path / "d.jsonl").unlink()
    with pytest.raises(LedgerCorrupt) as e:
        cat.verify(root=tmp_path)
    assert "مفقود" in str(e.value)
    assert not (tmp_path / "d.jsonl").exists()   # الفحص قراءةٌ لا تُنشئ


# ── العيب ٨: الملف الدخيل في مجلد المخزن كان غير مرئي لأي فحص ──

def test_stray_corpus_file_detected(tmp_path):
    reg = _src(tmp_path)
    cf = CorpusFile(tmp_path / "d.jsonl")
    h = cf.ingest("doc-1", [_page()], reg)
    cat = CorpusCatalog(tmp_path / "_catalog.jsonl")
    cat.record("doc-1", "d.jsonl", 1, h)
    assert cat.verify(root=tmp_path)
    CorpusFile(tmp_path / "999__دخيل.jsonl").ingest("dakhil", [_page()], reg)
    with pytest.raises(LedgerCorrupt) as e:
        cat.verify(root=tmp_path)
    assert "دخيل" in str(e.value)


# ── العيب ١٠: حقل pages كان إسقاطًا مُصدَّقًا بلا فحص ──

def test_catalog_pages_count_verified(tmp_path):
    reg = _src(tmp_path)
    cf = CorpusFile(tmp_path / "d.jsonl")
    h1 = cf.ingest("doc-1", [_page()], reg)
    cat = CorpusCatalog(tmp_path / "_catalog.jsonl")
    cat.record("doc-1", "d.jsonl", 1, h1)
    assert cat.verify(root=tmp_path)
    # قيد ملتفّ بنفس الرأس وعدد كاذب
    Ledger(tmp_path / "_catalog.jsonl").append(
        {"kind": "doc_ingested", "doc_id": "doc-1", "file": "d.jsonl",
         "pages": 0, "head": h1})
    with pytest.raises(LedgerCorrupt):
        cat.verify(root=tmp_path)


# ── العيب ١٢: «المادة الأولى» المكتوبة كانت تسقط إلى تقطيع الفقرات ──

def test_spelled_article_numbers_matched():
    text = ("تمهيد.\n\nالمادة الأولى: التعريفات.\nنص.\n\n"
            "المادة الحادية عشرة: كذا.\nنص.\n\n"
            "المادة الخامسة والعشرون: كذا.\nنص.")
    loci = [l for l, _ in _chunk(text)]
    assert loci == ["التمهيد", "المادة الأولى", "المادة الحادية عشرة",
                    "المادة الخامسة والعشرون"]


def test_article_lexicon_is_closed():
    assert not ARTICLE_RE.match("المادة التالية تنص على كذا")
    assert not ARTICLE_RE.match("المادة الخام المستوردة")


# ── العيب ٢ والطفيف: search كان ينفجر خامًا ويُنشئ فهرسًا فارغًا ──

def test_search_named_rejections(tmp_path, monkeypatch):
    import rebuild_index as ri
    # منذ م٥ يُحسم المسار من سجل المخازن CORPORA لا من INDEX المفرد
    monkeypatch.setitem(ri.CORPORA, "maritime",
                        (tmp_path / "cat.jsonl", tmp_path / "no-index.sqlite"))
    with pytest.raises(PayloadRejected) as e:
        ri.search("السفن")
    assert e.value.code == "index_missing"
    assert not (tmp_path / "no-index.sqlite").exists()
    monkeypatch.setitem(ri.CORPORA, "maritime",
                        (tmp_path / "cat.jsonl", tmp_path / "x.sqlite"))
    (tmp_path / "x.sqlite").touch()
    with pytest.raises(PayloadRejected) as e2:
        ri.search("   ًٌٍ ـ ")
    assert e2.value.code == "query_empty_after_normalization"


# ════════════════════════════════════════════════════════════════════
# عيوب تدقيق م٣ العدائي — ١٠ عيوب مؤكدة أُصلحت (وق٢١: الختم والقفل)
# ════════════════════════════════════════════════════════════════════

from core.corpus import CorpusCatalog as _Cat, CorpusFile as _CF
from core.glossary import Glossary as _G, GlossaryEntry as _GE
from core.rulings import Ruling as _R, Rulings as _Rs
from extract_terms import extract_from_page as _extract


# ── القاتل: تزوير سابقة مالك بعد الختم كان يجتاز الفحص الصارم ──

def test_forged_ruling_after_seal_locks_register(tmp_path):
    rs = _Rs(tmp_path / "r.jsonl")
    rs.rule(_R("سلم-الأصالة", "الأصل يُقدَّم.", "ق٢٠", "2026-09-20"))
    p = _R("سلم-الأصالة", "نقض مزوّر: المستنبَط يُقدَّم.", "مختلق",
           "2026-09-20").fingerprint_payload()
    Ledger(tmp_path / "r.jsonl").append(
        {"kind": "ruling", "topic": "سلم-الأصالة",
         "ruling": p, "ruling_digest": digest(p)})
    with pytest.raises(LedgerCorrupt):      # أي قراءة تُغلق — لا نافذ مزوّر
        rs.get("سلم-الأصالة")
    with pytest.raises(LedgerCorrupt):
        rs.verify(strict=True)


def test_rulings_entries_without_seal_locked(tmp_path):
    p = _R("م", "نص الحكم.", "سند.", "2026-09-20").fingerprint_payload()
    Ledger(tmp_path / "r.jsonl").append(
        {"kind": "ruling", "topic": "م", "ruling": p, "ruling_digest": digest(p)})
    with pytest.raises(LedgerCorrupt):      # قيود بلا ختم = بناء خارج القناة
        _Rs(tmp_path / "r.jsonl").current()


# ── الجسيم: الحقل المدسوس وschema_version المزوّر كانا يمرّان ──

def test_smuggled_glossary_entry_fields_detected(tmp_path):
    g = _G(tmp_path / "g.jsonl")
    EVs = frozenset({"a" * 64})
    g.add(_GE("maritime", "مصطلح", "تعريف كافي الطول هنا.", "a" * 64,
              "approved"), EVs)
    p = _GE("maritime", "دخيل", "تعريف كافي الطول هنا.", "a" * 64,
            "approved").fingerprint_payload()
    led = Ledger(tmp_path / "g.jsonl")
    led.append({"kind": "term", "glossary": "maritime", "term": "دخيل",
                "entry": {**p, "note_injected": "مدسوس"},
                "entry_digest": digest({**p, "note_injected": "مدسوس"})})
    led.anchor()                            # حتى بختمٍ معاد: الحصر يكشفه
    with pytest.raises(LedgerCorrupt):
        g.verify(EVs)


# ── العضوية بعد التصحيح: النافذ للجديد، والتاريخ لصحة الأمس ──

def _mini_corpus(tmp_path):
    reg = SourceRegister(tmp_path / "src" / "a.jsonl")
    reg.acquire(_acq(source_id="src"))
    cf = _CF(tmp_path / "d.jsonl")
    h1 = cf.ingest("doc-1", [_item(source_id="src",
                                   text="نص أول فيه خطأ مطبعي.")], reg)
    cat = _Cat(tmp_path / "_catalog.jsonl")
    cat.record("doc-1", "d.jsonl", 1, h1)
    old_digest = cf.pages()[0]["item_digest"]
    h2 = cf.ingest("doc-1", [_item(source_id="src",
                                   text="نص مصحح بلا خطأ.")], reg)
    cat.record("doc-1", "d.jsonl", 2, h2)
    new_digest = cf.pages()[1]["item_digest"]
    return cat, old_digest, new_digest


def test_superseded_page_not_valid_for_new_evidence(tmp_path):
    cat, old_d, new_d = _mini_corpus(tmp_path)
    cur = cat.page_digests(tmp_path, current_only=True)
    allh = cat.page_digests(tmp_path)
    assert new_d in cur and old_d not in cur     # النافذ للجديد
    assert old_d in allh                          # والتاريخ لصحة الأمس
    g = _G(tmp_path / "g.jsonl")
    with pytest.raises(PayloadRejected) as e:
        g.add(_GE("maritime", "مصطلح", "تعريف كافي الطول هنا.",
                  old_d, "approved"), cur)
    assert e.value.code == "evidence_unknown"


def test_find_page_marks_superseded(tmp_path):
    cat, old_d, new_d = _mini_corpus(tmp_path)
    assert cat.find_page(tmp_path, old_d)["superseded"] is True
    assert cat.find_page(tmp_path, new_d)["superseded"] is False


# ── الاستخلاص: الشريطة الداخلية والسابقة اللاصقة ──

def test_extract_rejects_multicell_and_strips_prefix():
    text = ("في تطبيق أحكام هذه اللائحة يقصد بالمصطلحات التالية:\n"
            "| الرقم | التعريف: هذا التعريف داخل جدول من خليتين |\n"
            "يقصد بالسفينة: كل منشأة عائمة مسجلة تعمل بحرًا.\n")
    got = dict(_extract(text))
    assert "السفينة" in got
    assert not any("|" in t for t in got)


# ════════════════════════════════════════════════════════════════════
# عيوب تدقيق م٤ العدائي — ١٢ عيبًا مؤكدًا أُصلحت (أغلبها مغطى في
# tests/test_node.py؛ هنا ارتدادات المخزن التي كشفها القبول الحي)
# ════════════════════════════════════════════════════════════════════


def test_markdown_headed_articles_are_chunked():
    # لائحة التطقيم كتبت موادها برؤوس Markdown فانهارت مقطعًا واحدًا
    text = ("تمهيد.\n\n# المادة الأولى: تعاريف\nنصها.\n\n"
            "## المادة الثانية: نطاق\nنصها.\n")
    loci = [l for l, _ in _chunk(text)]
    assert loci == ["التمهيد", "# المادة الأولى", "## المادة الثانية"]


def test_structural_correction_versions_file_and_keeps_history(tmp_path):
    reg = _src(tmp_path)
    d = tmp_path / "corpus" / "maritime"
    d.mkdir(parents=True)
    cf1 = _CF(d / "001__x.jsonl")
    h1 = cf1.ingest("001__x", [_page(locus="المقطع 1",
                                     text="نص قديم ببنية فقرات.")], reg)
    cat = _Cat(d / "_catalog.jsonl")
    cat.record("001__x", "corpus/maritime/001__x.jsonl", 1, h1)
    old_digest = cf1.pages()[0]["item_digest"]
    # تصحيح بنيوي: ملف نسخة جديد يشير إليه الفهرس قيدَ نسخة
    cf2 = _CF(d / "001__x__v2.jsonl")
    h2 = cf2.ingest("001__x", [_page(locus="المادة الأولى",
                                     text="نص ببنية مواد صحيحة.")], reg)
    cat.record("001__x", "corpus/maritime/001__x__v2.jsonl", 1, h2)
    root = tmp_path
    assert cat.verify(root=root)          # الملف القديم ليس «دخيلًا»
    allh = cat.page_digests(root)
    cur = cat.page_digests(root, current_only=True)
    assert old_digest in allh and old_digest not in cur
    found = cat.find_page(root, old_digest)
    assert found is not None and found["superseded"] is True
