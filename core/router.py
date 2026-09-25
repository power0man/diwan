"""الموجّه — يوجّه ولا يفهم (م٥، ق٢٠). سكربتٌ يُستدعى، لا خادم.

    python3 -m core.router [جذر المستودع]

لكل تمريرة (تحت **قفلٍ حصري** — موجّهان متزامنان كانا يسلّمان كل رسالة
مرتين، عيب تدقيق م٥): يقرأ صادرَ كل عقدةٍ مسجلة من إزاحته، يتحقق من
كل رسالة، ويفرض ثلاث بوابات بنيوية بلا فهمٍ للمحتوى: (١) هوية المرسِل
= صاحب الصادر؛ (٢) المستقبِل مسجَّلٌ ويقبل نوعَ الرسالة في عقده؛ (٣)
سلّم السياسات: لا تُمرَّر حمولةٌ تصنيفُها فوق سقف المستقبِل. البثُّ
يُسلَّم لكل عقدةٍ تقبل النوع عدا المرسِل — وبثٌّ لا قابلَ له يُرَدّ
برمزه لا يتبخر.

كلُّ عبورٍ يترك **الأثرين**: نصَّ الرسالة في صندوقي الطرفين، وبصمةَ
عبورٍ في السجل الرئيس المختوم (`ledger/main.jsonl`) — وقيودُ السجل
الرئيس هي **درءُ التكرار**: الإزاحة تُكتب بعد اكتمال معالجة الرسالة
(لا قبلها — انهيارٌ في الفجوة كان يُفقد الرسالة صمتًا، عيب تدقيق م٥)،
وإعادةُ المسح بعد فقد الإزاحة لا تعيد أثرًا مقيَّدًا. صادرٌ فاسد
يُحجَر بقيد `stream_corrupt` وتستمر التمريرة لبقية العقد (قيدٌ مسموم
واحد كان يوقف الشبكة كلها أبدًا). والمردودُ يُقيَّد `transit_refused`
برمزه لا يُسقَط صمتًا، ومع كل تمريرةٍ نبضُ `router_run` — وحارسُ
الشيخوخة لا يقرؤه إلا من سجلٍّ مختومٍ متحقَّقٍ (نبضٌ خامٌ ملحقٌ كان
يخدّره بعمرٍ سالب).
"""
from __future__ import annotations

from dataclasses import dataclass
import sys
import time
from pathlib import Path

from core import filelock
from core.canonical import PayloadRejected
from core.contracts import DATA_POLICIES, LOCAL_ONLY_POLICIES
from core.knowledge import BROADCAST, policy_within_ceiling
from core.ledger import Ledger, LedgerCorrupt
from core.registry import NodeRegistry
from core.seal import require_seal, sealed_append
from core.snapshot import full_entries
from core.streams import Offsets, inbox, node_streams_dir, outbox

_LEDGER_NAME = "السجل الرئيس"


def main_ledger(root: Path) -> Ledger:
    return Ledger(root / "ledger" / "main.jsonl")


def _refusal_key(rec: dict) -> tuple:
    return ("r", rec["event_digest"], rec.get("to") or rec.get("outbox"),
            rec["code"])


def _seen_keys(recs: list[dict]) -> set[tuple]:
    """مفاتيح الدرء من قيود السجل الرئيس — الحقيقة التي تمنع تكرار
    التسليم والرد عند الاستئناف أو فقد الإزاحات."""
    seen = set()
    for r in recs:
        if r.get("kind") == "transit":
            seen.add(("t", r["event_digest"], r["to"]))
        elif r.get("kind") == "transit_refused":
            seen.add(_refusal_key(r))
        elif r.get("kind") == "stream_corrupt":
            seen.add(("q", r["outbox"], r["error"]))
    return seen


def route_once(root: Path, now_ts: str) -> dict:
    """تمريرة واحدة تحت قفل حصري. تعيد {delivered, refused, read,
    quarantined} وتقيّد النبض."""
    lock_dir = root / "ledger"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / "router.lock").open("w") as lock_f:
        filelock.lock(lock_f)
        try:
            return _route_once_locked(root, now_ts)
        finally:
            filelock.unlock(lock_f)


def _route_once_locked(root: Path, now_ts: str) -> dict:
    registry = NodeRegistry(root / "registry" / "nodes.jsonl")
    nodes = registry.current()
    ledger = main_ledger(root)
    # حقيقة الدرء تمتد عبر الأجيال: التاريخ الكامل (لقطات + ذيل) لا
    # الذيل وحده — تدويرُ السجل الرئيس لا يمحو قيد عبورٍ (ق٢٢، تدقيق م٧)
    seen = _seen_keys([e["record"]
                       for e in full_entries(ledger.path)])
    offsets = Offsets(root / "ledger" / "router-offsets.json")

    delivered = refused = read = quarantined = 0

    def refuse(rec: dict) -> None:
        nonlocal refused
        key = _refusal_key(rec)
        if key in seen:
            return          # رُدَّت وقُيِّدت سلفًا — استئنافٌ لا تكرار
        sealed_append(ledger, rec, _LEDGER_NAME)
        seen.add(key)
        refused += 1

    for name in sorted(nodes):
        stream_dir = node_streams_dir(root, name)
        if not (stream_dir / "outbox.jsonl").exists():
            continue
        out_stream = outbox(root, name)
        start = offsets.get(f"outbox:{name}")
        try:
            events = out_stream.read_from(start)
        except LedgerCorrupt as exc:
            # حجرُ الصادر الفاسد وحده — لا يُسقط تمريرة الشبكة كلها
            qkey = ("q", name, str(exc)[:300])
            if qkey not in seen:
                sealed_append(ledger, {
                    "kind": "stream_corrupt", "outbox": name,
                    "error": str(exc)[:300], "at": now_ts,
                }, _LEDGER_NAME)
                seen.add(qkey)
            quarantined += 1
            continue
        for next_off, ev, ev_digest in events:
            read += 1
            if ev.from_node != name:
                refuse({"kind": "transit_refused", "code": "sender_mismatch",
                        "outbox": name, "claimed": ev.from_node,
                        "event_digest": ev_digest, "at": now_ts})
                offsets.set(f"outbox:{name}", next_off)
                continue
            if ev.to == BROADCAST:
                targets = [n for n, rec in nodes.items()
                           if n != name
                           and ev.kind in rec["manifest"]["accepts"]]
                if not targets:   # بثٌّ بلا قابل لا يتبخر — يُرَدّ برمزه
                    refuse({"kind": "transit_refused",
                            "code": "broadcast_no_acceptors",
                            "from": name, "to": BROADCAST,
                            "event_digest": ev_digest, "at": now_ts})
            else:
                targets = [ev.to]
            for target in targets:
                rec = nodes.get(target)
                code = None
                if rec is None:
                    code = "recipient_unknown"
                elif ev.kind not in rec["manifest"]["accepts"]:
                    code = "kind_not_accepted"
                elif not policy_within_ceiling(
                        ev.data_policy, rec["manifest"]["data_policy_ceiling"]):
                    code = "policy_exceeds_ceiling"
                if code:
                    refuse({"kind": "transit_refused", "code": code,
                            "from": name, "to": target,
                            "event_digest": ev_digest, "at": now_ts})
                    continue
                tkey = ("t", ev_digest, target)
                if tkey in seen:
                    continue      # عبورٌ مقيَّد سلفًا — لا تسليم مزدوجًا
                target_in = inbox(root, target)
                if not target_in.contains(ev_digest):
                    target_in.emit(ev)   # انهيارٌ سابق بعد التسليم قبل القيد
                sealed_append(ledger, {
                    "kind": "transit", "from": name, "to": target,
                    "event_digest": ev_digest,
                    "correlation_id": ev.correlation_id,
                    "data_policy": ev.data_policy, "at": now_ts,
                }, _LEDGER_NAME)
                seen.add(tkey)
                delivered += 1
            # الإزاحة بعد اكتمال كل الأهداف — لا قبل المعالجة
            offsets.set(f"outbox:{name}", next_off)
    sealed_append(ledger, {"kind": "router_run", "at": now_ts,
                           "read": read, "delivered": delivered,
                           "refused": refused,
                           "quarantined": quarantined}, _LEDGER_NAME)
    return {"delivered": delivered, "refused": refused, "read": read,
            "quarantined": quarantined}


def last_run_age_s(root: Path, now_epoch: float) -> float | None:
    """عمر آخر نبضة موجّه بالثواني — None إن لم يجرِ قط. الحارس لا
    يصدّق نبضًا خارج الختم: السجل يُتحقق تمامًا قبل القراءة."""
    ledger = main_ledger(root)
    last = None
    for e in full_entries(ledger.path):
        rec = e.get("record", {})
        if rec.get("kind") == "router_run":
            last = rec.get("at")
    if last is None:
        return None
    import datetime
    ts = datetime.datetime.fromisoformat(last).timestamp()
    return now_epoch - ts


@dataclass(frozen=True)
class RoutingDecision:
    """قرار التوجيه المتدرج: المستوى، والنموذج، والسياسة، والعلة."""
    tier: str
    model: str
    data_policy: str
    reason: str

    def payload(self) -> dict:
        return {
            "kind": "sovereign_route",
            "tier": self.tier,
            "model": self.model,
            "data_policy": self.data_policy,
            "reason": self.reason,
        }


class TieredSovereignRouter:
    """التوجيه السيادي المتدرج (م١٥ — خطة الحوكمة والنقلة النوعية).

    يفصل معالجة الطلبات إلى ثلاثة مستويات حتمية تصون الخصوصية:
    1. المستوى الأول (Edge / Local Fast): نماذج حافة سريعة للاستجابة اللحظية.
    2. المستوى الثاني (Local Sovereign Heavy): نماذج محلية أعمق للمهام المركبة.
    3. المستوى الثالث (Cloud Sovereign Isolated): تصعيد مشروط محظور على البيانات الحساسة.
    """
    TIER_EDGE_FAST = "edge_local_fast"
    TIER_LOCAL_HEAVY = "local_sovereign_heavy"
    TIER_CLOUD_ISOLATED = "cloud_sovereign_isolated"

    DEFAULT_EDGE_MODEL = "qwen2.5:3b"
    DEFAULT_HEAVY_MODEL = "qwen2.5:14b"
    DEFAULT_CLOUD_MODEL = "cloud:frontier-v1"

    def __init__(self,
                 edge_model: str = DEFAULT_EDGE_MODEL,
                 heavy_model: str = DEFAULT_HEAVY_MODEL,
                 cloud_model: str = DEFAULT_CLOUD_MODEL):
        self.edge_model = edge_model
        self.heavy_model = heavy_model
        self.cloud_model = cloud_model

    def select_tier(self, data_policy: str,
                    complexity: str = "normal",
                    content_length: int = 0) -> RoutingDecision:
        """اختيار مستوى التوجيه وفق سياسة البيانات وحجم المهمة."""
        if data_policy not in DATA_POLICIES:
            raise PayloadRejected(
                "data_policy", "invalid_data_policy",
                f"تصنيف البيانات غير صالح: {data_policy!r} — المسجل: {sorted(DATA_POLICIES)}")

        is_local_only = data_policy in LOCAL_ONLY_POLICIES

        if complexity == "heavy" or content_length > 2500:
            if is_local_only:
                return RoutingDecision(
                    tier=self.TIER_LOCAL_HEAVY,
                    model=self.heavy_model,
                    data_policy=data_policy,
                    reason="مهمة مركبة/مطولة تتطلب استدلالاً عميقاً تحت سقف السيادة المحلية",
                )
            else:
                return RoutingDecision(
                    tier=self.TIER_CLOUD_ISOLATED,
                    model=self.cloud_model,
                    data_policy=data_policy,
                    reason="مهمة مركبة مسموح بتصعيدها سحابياً لتصنيف البيانات غير الحساس",
                )

        return RoutingDecision(
            tier=self.TIER_EDGE_FAST,
            model=self.edge_model,
            data_policy=data_policy,
            reason="مهمة اعتيادية سريعة على النموذج الحافي ذي الاستجابة الخاطفة",
        )

    def enforce_policy_ceiling(self, decision: RoutingDecision) -> None:
        """فرض الحظر الدستوري الصارم على تسريب البيانات المحلية للسحاب."""
        if decision.data_policy in LOCAL_ONLY_POLICIES and decision.tier == self.TIER_CLOUD_ISOLATED:
            raise PayloadRejected(
                "policy", "cloud_escalation_forbidden_for_local_policy",
                f"يحظر تصعيد البيانات ذات التصنيف {decision.data_policy!r} إلى السحاب"
            )

    def log_decision(self, ledger: Ledger, decision: RoutingDecision, now_ts: str) -> None:
        """توثيق قرار التوجيه في السجل بقيد مختوم."""
        self.enforce_policy_ceiling(decision)
        sealed_append(ledger, {
            **decision.payload(),
            "at": now_ts,
        }, _LEDGER_NAME)


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 \
        else Path(__file__).resolve().parent.parent
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    stats = route_once(root, now.strftime("%Y-%m-%dT%H:%M:%S+00:00"))
    print(f"تمريرة الموجّه: قُرئ {stats['read']}، سُلِّم {stats['delivered']}، "
          f"رُدّ {stats['refused']}، حُجر {stats['quarantined']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
