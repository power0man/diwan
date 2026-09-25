"""اختبارات م٥: التيارات والموجّه وبوابة المسرد الصرفية + لغما القبول
المُصادان حيًّا (تصادم اسم الوحدة، الصرامة الزائفة في فرض المصطلح)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core.knowledge import KnowledgeEvent, NodeManifest
from core.ledger import LedgerCorrupt
from core.registry import NodeRegistry
from core.router import last_run_age_s, main_ledger, route_once
from core.streams import Stream, inbox, outbox
from run_node import load_node_module


def _q(frm, to, policy="internal", corr="c1", key="k1"):
    return KnowledgeEvent(
        kind="query", from_node=frm, to=to, correlation_id=corr,
        data_policy=policy, payload={"question": "س", "domain": "d"},
        idempotency_key=key, budget_cap_micros=1)


def _seed_registry(root, ceilings):
    reg = NodeRegistry(root / "registry" / "nodes.jsonl")
    for name, ceiling in ceilings.items():
        reg.register(NodeManifest(
            name=name, contract_version=1, domains=("d",),
            accepts=("query",), data_policy_ceiling=ceiling))
    reg.anchor()
    return reg


# ── لغم القبول ١: ملفا العقدتين كلاهما node.py — الاستيراد بالاسم تصادم ──

def test_node_modules_load_by_path_without_collision():
    ling = load_node_module("linguistics")
    mar = load_node_module("maritime")
    phi = load_node_module("philosophy")
    assert hasattr(ling, "LinguisticsNode") and hasattr(mar, "MaritimeNode") and hasattr(phi, "PhilosophyNode")
    assert not hasattr(ling, "MaritimeNode") and not hasattr(phi, "MaritimeNode")
    assert len({ling.__file__, mar.__file__, phi.__file__}) == 3


# ── لغم القبول ٢: فرض المسرد فرضُ معجمة لا صيغة ──

@pytest.fixture(scope="module")
def term_in_text():
    spec = importlib.util.spec_from_file_location(
        "m5_test_ling", ROOT / "nodes" / "linguistics" / "node.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.term_in_text


def test_term_accepts_sound_inflection(term_in_text):
    assert term_in_text("الرحلة الدولية", "في كل رحلة دولية يلتزم")
    assert term_in_text("السفينة", "ربان أي سفينة ملزم")
    assert term_in_text("السفينة", "وللسفينة وثائقها")
    assert term_in_text("مياه الصابورة", "بمياه صابورة نظيفة")


def test_term_accepts_diacritics_and_hamza(term_in_text):
    # تدقيق م٥: التشكيل والتنوين الوسيط وتنويعات الهمزة عربية أمينة
    assert term_in_text("الربان", "يلتزم الربّان بالخطة")
    assert term_in_text("الرحلة الدولية", "في كل رحلةٍ دوليةٍ")
    assert term_in_text("الإدارة", "وافقت الادارة على الخطة")
    assert term_in_text("السفينة", "وَللسَّفينةِ وثائقُها")


def test_term_rejects_substitution_and_partial(term_in_text):
    assert not term_in_text("الميناء", "دخل المرفأ صباحًا")
    assert not term_in_text("السفينة", "سفينتها راسية")   # لصق الضمير يغيّر البنية
    assert not term_in_text("الرحلة الدولية", "الرحلة المحلية الدولة")


# ── التيارات: المُدسّ لا يصل مستهلكًا ──

def test_stream_rejects_tampered_event(tmp_path):
    s = Stream(tmp_path / "outbox.jsonl")
    s.emit(_q("a", "b"))
    lines = (tmp_path / "outbox.jsonl").read_text().splitlines()
    rec = json.loads(lines[0])
    rec["record"]["event"]["payload"]["question"] = "بُدّل"
    (tmp_path / "outbox.jsonl").write_text(
        json.dumps(rec, ensure_ascii=False) + "\n")
    with pytest.raises(LedgerCorrupt):
        Stream(tmp_path / "outbox.jsonl").read_from(0)


def test_stream_rejects_appended_without_reseal(tmp_path):
    s = Stream(tmp_path / "outbox.jsonl")
    s.emit(_q("a", "b"))
    with (tmp_path / "outbox.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"دس": 1}\n')
    with pytest.raises(LedgerCorrupt):
        Stream(tmp_path / "outbox.jsonl").read_from(0)


# ── بوابات الموجّه الثلاث + الأثران + النبض ──

def test_router_gates_and_dual_trace(tmp_path):
    _seed_registry(tmp_path, {"a": "regulated", "b": "internal"})
    outbox(tmp_path, "a").emit(_q("a", "b", policy="internal"))     # يمر
    outbox(tmp_path, "a").emit(_q("x", "b", key="k2"))              # مزوّر الهوية
    outbox(tmp_path, "a").emit(_q("a", "ghost", key="k3"))          # مستقبل مجهول
    outbox(tmp_path, "a").emit(_q("a", "b", policy="regulated",
                                  key="k4"))                        # فوق السقف
    stats = route_once(tmp_path, "2026-09-20T12:00:00+00:00")
    assert stats == {"delivered": 1, "refused": 3, "read": 4,
                     "quarantined": 0}
    got = inbox(tmp_path, "b").read_from(0)
    assert len(got) == 1 and got[0][1].from_node == "a"
    led = main_ledger(tmp_path)
    assert led.verify_chain(strict=True)
    recs = [e["record"] for e in led.entries()]
    codes = sorted(r["code"] for r in recs if r["kind"] == "transit_refused")
    assert codes == ["policy_exceeds_ceiling", "recipient_unknown",
                     "sender_mismatch"]
    assert sum(1 for r in recs if r["kind"] == "transit") == 1
    assert recs[-1]["kind"] == "router_run"
    age = last_run_age_s(tmp_path, 1789000000.0)
    assert age is not None


def test_router_offset_survives_rerun_no_double_delivery(tmp_path):
    _seed_registry(tmp_path, {"a": "regulated", "b": "regulated"})
    outbox(tmp_path, "a").emit(_q("a", "b"))
    route_once(tmp_path, "2026-09-20T12:00:00+00:00")
    stats2 = route_once(tmp_path, "2026-09-20T12:00:01+00:00")
    assert stats2["delivered"] == 0 and stats2["read"] == 0
    assert len(inbox(tmp_path, "b").read_from(0)) == 1


# ── تدقيق م٥: الإزاحة إسقاطٌ حقًّا — فقدُها لا يعيد أثرًا مقيَّدًا ──

def test_lost_offsets_do_not_redeliver(tmp_path):
    _seed_registry(tmp_path, {"a": "regulated", "b": "regulated"})
    outbox(tmp_path, "a").emit(_q("a", "b"))
    outbox(tmp_path, "a").emit(_q("x", "b", key="k2"))   # مردودة
    route_once(tmp_path, "2026-09-20T12:00:00+00:00")
    (tmp_path / "ledger" / "router-offsets.json").unlink()
    stats2 = route_once(tmp_path, "2026-09-20T12:00:01+00:00")
    # يعاد المسح (read=2) لكن لا تسليم ولا رد مكررين — الدرء بالسجل
    assert stats2["read"] == 2
    assert stats2["delivered"] == 0 and stats2["refused"] == 0
    assert len(inbox(tmp_path, "b").read_from(0)) == 1
    recs = [e["record"] for e in main_ledger(tmp_path).entries()]
    assert sum(1 for r in recs if r["kind"] == "transit") == 1
    assert sum(1 for r in recs if r["kind"] == "transit_refused") == 1


# ── تدقيق م٥: صادرٌ مسموم يُحجَر ولا يوقف الشبكة ──

def test_poisoned_outbox_quarantined_others_delivered(tmp_path):
    _seed_registry(tmp_path, {"a": "regulated", "b": "regulated",
                              "c": "regulated"})
    outbox(tmp_path, "a").emit(_q("a", "b"))          # سيُفسد تياره
    outbox(tmp_path, "c").emit(_q("c", "b", key="k9"))  # سليم
    with (tmp_path / "streams" / "a" / "outbox.jsonl").open(
            "a", encoding="utf-8") as f:
        f.write('{"دس": 1}\n')
    stats = route_once(tmp_path, "2026-09-20T12:00:00+00:00")
    assert stats["quarantined"] == 1
    assert stats["delivered"] == 1          # رسالة c وصلت رغم فساد a
    recs = [e["record"] for e in main_ledger(tmp_path).entries()]
    assert any(r["kind"] == "stream_corrupt" and r["outbox"] == "a"
               for r in recs)
    assert recs[-1]["kind"] == "router_run"   # النبض لم يُحرم
    # الحجر لا يتضاعف قيدًا مع كل تمريرة
    route_once(tmp_path, "2026-09-20T12:00:01+00:00")
    recs2 = [e["record"] for e in main_ledger(tmp_path).entries()]
    assert sum(1 for r in recs2 if r["kind"] == "stream_corrupt") == 1


# ── تدقيق م٥: حارس الشيخوخة لا يصدّق نبضًا خارج الختم ──

def test_forged_heartbeat_rejected(tmp_path):
    _seed_registry(tmp_path, {"a": "regulated"})
    outbox(tmp_path, "a").emit(_q("a", "a"))
    route_once(tmp_path, "2026-09-20T12:00:00+00:00")
    with (tmp_path / "ledger" / "main.jsonl").open("a",
                                                   encoding="utf-8") as f:
        f.write('{"record": {"kind": "router_run", '
                '"at": "2099-01-01T00:00:00+00:00"}}\n')
    with pytest.raises(LedgerCorrupt):
        last_run_age_s(tmp_path, 1789000000.0)


# ── تدقيق م٥: سقف الرسالة نافذ ──

def test_message_budget_cap_enforced():
    from core.budget import Budget as B, BudgetRefused
    from run_node import MessageBudget
    mb = MessageBudget(B(1_000_000, 1_000_000), cap_micros=5)
    assert mb.reserve("h1", 3) == 3
    with pytest.raises(BudgetRefused) as e:
        mb.reserve("h2", 4)          # 3+4 > 5
    assert e.value.code == "message_cap"
    mb.settle("h1", 1)               # يُرَدّ الفرق للرسالة أيضًا
    assert mb.reserve("h3", 3) == 3  # 1+3 ≤ 5


# ── تدقيق م٥: العطل القابل للإعادة لا يسمم المفتاح عبر التشغيلات ──

def test_retryable_error_not_replayed_across_runs(tmp_path):
    from core.contracts import Message, Request, Response, Usage
    from core.budget import Budget as B
    from core.ledger import Ledger
    from core.run import execute
    from providers.base import ProviderError

    class FlakyProvider:
        model = "m"
        is_local = True
        def __init__(self): self.calls = 0
        def estimate_micros(self, req): return 0
        def complete(self, req):
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("unreachable", "انقطاع", retryable=True)
            return Response(content="جواب", usage=Usage(3, 2),
                            stop_reason="complete", cost_micros=0,
                            provider="t", model_version="t")

    led = Ledger(tmp_path / "calls.jsonl")
    p = FlakyProvider()
    req = Request(messages=(Message("user", "س"),), model="m",
                  model_version="t", max_output=10, deadline_s=5.0,
                  data_policy="internal", idempotency_key="K1")
    o1 = execute(req, p, B(1000, 1000), led)
    assert o1.response is None and o1.error_code == "unreachable"
    # «تشغيلة» تالية بالمفتاح نفسه: يُنادى حيًّا لا يُعاد عرض الأمس
    o2 = execute(req, p, B(1000, 1000), led)
    assert o2.response is not None and o2.response.content == "جواب"
    # والنجاح المقيد يُستهلك لا يُعاد نداؤه
    o3 = execute(req, p, B(1000, 1000), led)
    assert o3.replayed and o3.response.content == "جواب"
    assert p.calls == 2


# ── تدقيق م٥: حدود كلمات المكافئ الأجنبي ──

def test_foreign_equiv_word_boundaries():
    spec = importlib.util.spec_from_file_location(
        "m5_test_ling2", ROOT / "nodes" / "linguistics" / "node.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pat = mod.LinguisticsNode._foreign_pattern
    assert not pat("ship").search("the shipment and ownership")
    assert not pat("crew").search("a box of screws")
    assert pat("ship").search("the master of a ship shall")
    assert pat("ballast water").search("manage ballast water now")
