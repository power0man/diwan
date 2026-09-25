#!/usr/bin/env python3
"""مشغّل وارد عقدة: يستهلك رسائل query من inbox ويرد answer في outbox.

    python3 tools/run_node.py maritime|linguistics

**قيدُ العبور شرطُ الاستهلاك**: لا تُعالَج رسالةٌ في الوارد إلا وبصمتُها
مقيدة `transit` إليّ في السجل الرئيس المختوم — فالمدسوس مباشرةً في
الوارد (متجاوزًا بوابات الموجّه الثلاث) لا يُخدَم، ويُعَدّ ويُعلَن
(عيب تدقيق م٥: «inbox يكتبه الموجّه حصرًا» كان وصفًا بلا فرض).

كل جوابٍ **مادةٌ** في حمولة الرسالة موسومةٌ بسؤالها (`in_reply_to` =
بصمة الاستعلام) — وهي درءُ التكرار: سؤالٌ مُجابٌ في الصادر لا يُعالَج
ثانية، والإزاحة تُكتب **بعد** المعالجة لا قبلها (انهيارٌ في الفجوة كان
يُسقط الرسالة صمتًا بلا جواب ولا رفض). والعجز/الرفض رسالةُ `refuse`
برمزها — إلا فسادَ سجلٍّ (`LedgerCorrupt`) فيُغلق المشغّل لا يُقنَّع
رمزًا. وسقفُ الرسالة `budget_cap_micros` **نافذ**: حجوز خدمتها تُرَدّ
إن جاوزت سقفَها قبل أن تمس ميزانية العملية (كان حرفًا ميتًا).
"""
from __future__ import annotations

import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core.budget import Budget, BudgetRefused
from core.knowledge import KnowledgeEvent, policy_within_ceiling
from core.ledger import Ledger, LedgerCorrupt
from core.seal import require_seal
from core.streams import Offsets, inbox, outbox


def load_node_module(name: str):
    """استيراد وحدة العقدة بمسارها الصريح — ملفا العقد كلاهما node.py
    والاستيراد بالاسم يتصادم (لغم مُصاد)."""
    import importlib.util
    path = ROOT / "nodes" / name / "node.py"
    spec = importlib.util.spec_from_file_location(f"nodes.{name}.node", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MessageBudget:
    """سقفُ الرسالة فوق ميزانية العملية: الحجز يُرَدّ برمز `message_cap`
    إن جاوز مجموعُ حجوز خدمة الرسالة سقفَها المعلن، ثم يمر إلى ميزانية
    العملية فتحكمه أسقفُها أيضًا — سقفان لا واحد."""

    def __init__(self, process_budget: Budget, cap_micros: int):
        self._b = process_budget
        self._cap = cap_micros
        self._reserved: dict[str, int] = {}
        self._spent = 0

    @property
    def kill_switch(self) -> bool:
        return self._b.kill_switch

    def reserve(self, handle: str, estimate_micros: int) -> int:
        if isinstance(estimate_micros, int) \
                and not isinstance(estimate_micros, bool) \
                and estimate_micros >= 0 \
                and self._spent + estimate_micros > self._cap:
            raise BudgetRefused(
                "message_cap",
                f"التقدير يجاوز سقف الرسالة ({self._cap} مايكرو)")
        n = self._b.reserve(handle, estimate_micros)
        self._reserved[handle] = estimate_micros
        self._spent += estimate_micros
        return n

    def settle(self, handle: str, actual_micros: int) -> int:
        n = self._b.settle(handle, actual_micros)
        est = self._reserved.pop(handle, 0)
        self._spent += actual_micros - est
        return n

    def settle_unknown(self, handle: str) -> int:
        # انقطاعٌ بلا استهلاك مُبلَّغ: يُحسب على الرسالة بمحجوزه كاملًا
        self._reserved.pop(handle, None)
        return self._b.settle_unknown(handle)

    @property
    def reservations(self):
        return self._b.reservations

    @property
    def outstanding_micros(self) -> int:
        return self._b.outstanding_micros


def _answer_policy(query_policy: str, payload: dict) -> str:
    """تصنيف الجواب: تصنيفُ الحوار — إلا مادةً بلا حق توزيعٍ فلا تُوسم
    `public` (أرضية `internal`؛ إسقاطُ الحقوق تصنيفًا كاملًا درزُ م٧)."""
    if payload.get("use_distribution") is False \
            and not policy_within_ceiling("internal", query_policy):
        return "internal"
    return query_policy


def answer_event(node_name: str, ev, ev_digest: str,
                 payload: dict) -> KnowledgeEvent:
    return KnowledgeEvent(
        kind="answer", from_node=node_name, to=ev.from_node,
        correlation_id=ev.correlation_id,
        data_policy=_answer_policy(ev.data_policy, payload),
        payload=payload, idempotency_key=None,
        in_reply_to=ev_digest)


def refuse_event(node_name: str, ev, ev_digest: str, code: str,
                 reason: str) -> KnowledgeEvent:
    return KnowledgeEvent(
        kind="refuse", from_node=node_name, to=ev.from_node,
        correlation_id=ev.correlation_id, data_policy=ev.data_policy,
        payload={"code": code, "reason": reason[:300]},
        idempotency_key=None, in_reply_to=ev_digest)


def refusal_details(exc):
    """Keep domain refusal codes; unexpected failures reveal no exception internals."""
    code, reason = getattr(exc, "code", None), getattr(exc, "reason", None)
    if (isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code)
            and isinstance(reason, str) and reason):
        return code, reason
    return "node_execution_failed", "تعذر تنفيذ الطلب؛ راجع تشخيص العقدة محليًا"


def main() -> int:
    node_name = sys.argv[1]
    var = ROOT / "var"
    var.mkdir(exist_ok=True)
    budget = Budget(10_000_000, 100_000_000)
    ledger = Ledger(var / f"{node_name}-calls.jsonl")

    mod = load_node_module(node_name)
    if node_name == "maritime":
        def handle(ev, msg_budget):
            from providers.ollama import OllamaProvider
            op = mod.MaritimeNode(ROOT, OllamaProvider("qwen3:14b"),
                                  msg_budget, ledger)
            r = op.answer(ev.payload["question"], data_policy=ev.data_policy)
            return r["answer"].fingerprint_payload()
    elif node_name == "linguistics":
        def handle(ev, msg_budget):
            op = mod.LinguisticsNode(ROOT)
            pages = op.lookup(ev.payload["question"])
            return dict(pages[0]["item"])   # صفحة المعجم مادةً أصلًا
    elif node_name == "philosophy":
        def handle(ev, msg_budget):
            from providers.ollama import OllamaProvider
            op = mod.PhilosophyNode(ROOT, OllamaProvider("qwen3:14b"),
                                  msg_budget, ledger)
            major = ev.payload.get("major_premise")
            minor = ev.payload.get("minor_premise")
            if major and minor:
                eval_res = op.evaluate_logic(major, minor)
                from core.knowledge import KnowledgeItem, validated_item
                rec = op.register.get("nodes.philosophy")
                acq = rec["acquisition"] if rec else {"use_internal": True, "use_distribution": False}
                item = op.register.admitted_item(validated_item(KnowledgeItem(
                    text=f"النتيجة الصورية: {eval_res.get('conclusion')} ({eval_res.get('rule')})",
                    lang="ar", domain="philosophy",
                    use_internal=acq["use_internal"], use_distribution=acq["use_distribution"],
                    source_id="nodes.philosophy", locus="logic_engine",
                    originality="derived", part="syllogism"
                )))
                return item.fingerprint_payload()
            item = op.handle_query(ev.payload.get("question", ""), data_policy=ev.data_policy)
            return item.fingerprint_payload()
    else:
        raise SystemExit(f"عقدة غير معروفة: {node_name}")

    # قيد العبور شرط الاستهلاك — من التاريخ الكامل للسجل الرئيس
    # (لقطات + ذيل): تدويرُه لا يجعل مسلَّمةً «مدسوسة» (تدقيق م٧)
    from core.snapshot import full_entries
    transited = {r["event_digest"]
                 for e in full_entries(ROOT / "ledger" / "main.jsonl")
                 if (r := e["record"]).get("kind") == "transit"
                 and r.get("to") == node_name}

    offsets = Offsets(var / f"{node_name}-offsets.json")
    in_stream = inbox(ROOT, node_name)
    out_stream = outbox(ROOT, node_name)
    # درء التكرار من الصادر نفسه: الأسئلة المُجابة/المردودة موسومة
    answered = {ev.in_reply_to for _o, ev, _d in out_stream.read_from(0)
                if ev.in_reply_to} if out_stream.path.exists() else set()
    start = offsets.get("inbox")
    handled = injected = 0
    for next_off, ev, ev_digest in in_stream.read_from(start):
        if ev.kind != "query":
            offsets.set("inbox", next_off)
            continue
        if ev_digest not in transited:
            injected += 1     # مدسوسة خارج الموجّه — تُعلَن ولا تُخدَم
            print(f"⚠ {node_name}: رسالة بلا قيد عبور في السجل الرئيس — "
                  f"دُسَّت خارج الموجّه فلن تُعالَج ({ev_digest[:12]}…)")
            offsets.set("inbox", next_off)
            continue
        if ev_digest in answered:
            offsets.set("inbox", next_off)
            continue          # أُجيبت سلفًا — استئنافٌ لا تكرار
        try:
            payload = handle(ev, MessageBudget(budget, ev.budget_cap_micros))
            out_stream.emit(answer_event(node_name, ev, ev_digest, payload))
        except LedgerCorrupt:
            raise             # فساد سجلٍّ يغلق المشغّل — لا يُقنَّع رمزًا
        except Exception as exc:
            out_stream.emit(refuse_event(node_name, ev, ev_digest, *refusal_details(exc)))
        handled += 1
        offsets.set("inbox", next_off)   # بعد المعالجة لا قبلها
    print(f"{node_name}: عالج {handled} رسالة"
          + (f"، وأعلن {injected} مدسوسة" if injected else "") + ".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
