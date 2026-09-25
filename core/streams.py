"""تيارات الرسائل بين العقد — صناديق بريدٍ مسلسلة مختومة (م٥، ق٢٠).

لكل عقدةٍ تياران إضافيان-فقط بنمط Ledger نفسه: `outbox` تكتبه العقدة
(نيّتها) و`inbox` يكتبه **الموجّه حصرًا** (تسليمه) — والحصر يفرضه
المستهلك لا الوصف: قيدُ عبورٍ في السجل الرئيس شرطُ الاستهلاك
(tools/run_node.py)، فالمدسوس في الوارد لا يُخدَم (عيب تدقيق م٥).

الكتابة `sealed_append` كاملًا (قفل ← فحص الختم ← إلحاق ← رسو)،
والقراءة تشترط تطابق الرأس/العدد مع الختم تمامًا قبل أن تعيد قيدًا
واحدًا — فقصُّ الذيل دون مسّ الختم مكشوف (عيب تدقيق م٥: verify_chain
غير الصارم كان يترك الختم وزنًا ميتًا في مسار القراءة).

الإزاحات إسقاطُ قارئٍ يُهمل ويُعاد **لأن درء التكرار في السجلات
المختومة لا فيها**: الموجّه يدرأ بقيود العبور، والعقدة بوسم
`in_reply_to` في صادرها — ففقدُ الإزاحة إعادةُ مسحٍ لا إعادةُ أثر.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from core.canonical import digest
from core.knowledge import (KnowledgeEvent, validated_event)
from core.ledger import Ledger, LedgerCorrupt
from core.seal import require_seal, sealed_append

_RECORD_KEYS = frozenset({"kind", "event", "event_digest"})


def _event_from_payload(p: dict, where: str) -> KnowledgeEvent:
    try:
        return KnowledgeEvent(
            kind=p["kind"], from_node=p["from_node"], to=p["to"],
            correlation_id=p["correlation_id"], data_policy=p["data_policy"],
            payload=p["payload"], idempotency_key=p["idempotency_key"],
            budget_cap_micros=p["budget_cap_micros"],
            in_reply_to=p["in_reply_to"],
        )
    except (KeyError, TypeError) as exc:
        raise LedgerCorrupt(f"{where}: رسالة مشوهة — {exc}") from exc


class Stream:
    """تيار واحد (inbox أو outbox) — كتابة مختومة وقراءة تشترط الختم."""

    def __init__(self, path: str | os.PathLike):
        self._ledger = Ledger(path)
        self.path = Path(path)

    def emit(self, event: KnowledgeEvent) -> str:
        """يتحقق ويقيّد الرسالة ختمًا (بمفتاحها خارج البصمة كما العقد)."""
        e = validated_event(event)
        payload = {**e.fingerprint_payload(),
                   "idempotency_key": e.idempotency_key}
        event_digest = digest(e.fingerprint_payload())
        sealed_append(self._ledger,
                      {"kind": "event", "event": payload,
                       "event_digest": event_digest},
                      f"تيار {self.path.parent.name}/{self.path.name}")
        return event_digest

    def read_from(self, offset: int) -> list[tuple[int, KnowledgeEvent, str]]:
        """يعيد [(الإزاحة التالية، الرسالة، بصمتها)] لما بعد `offset` —
        الختمُ شرطُ القراءة كلِّها، وكلُّ رسالةٍ تُعاد تُتحقق،
        والمشوَّه يُغلق التيار لا يُتخطى."""
        require_seal(self._ledger,
                     f"تيار {self.path.parent.name}/{self.path.name}")
        self._ledger.verify_chain()
        out = []
        for i, entry in enumerate(self._ledger.entries()):
            if i < offset:
                continue
            rec = entry.get("record", {})
            if set(rec) != _RECORD_KEYS:
                raise LedgerCorrupt(f"قيد تيار رقم {i}: حقوله لا تطابق العقد")
            ev = validated_event(_event_from_payload(rec["event"],
                                                     f"قيد {i}"))
            if digest(ev.fingerprint_payload()) != rec["event_digest"]:
                raise LedgerCorrupt(
                    f"قيد تيار رقم {i}: بصمة الرسالة لا تطابق إعادة بنائها")
            out.append((i + 1, ev, rec["event_digest"]))
        return out

    def contains(self, event_digest: str) -> bool:
        """هل بصمةُ الرسالة مقيدة في التيار؟ — حارسُ درء التكرار عند
        الاستئناف بعد انهيارٍ بين التسليم وقيد العبور (فحص خام مقصود:
        القراءة المتحققة بابُ الاستهلاك لا بابُ الدرء)."""
        return any(e.get("record", {}).get("event_digest") == event_digest
                   for e in self._ledger.entries())

    def count(self) -> int:
        return self._ledger.count()

    def head(self) -> str:
        return self._ledger.head()


def node_streams_dir(root: Path, node: str) -> Path:
    return root / "streams" / node


def outbox(root: Path, node: str) -> Stream:
    return Stream(node_streams_dir(root, node) / "outbox.jsonl")


def inbox(root: Path, node: str) -> Stream:
    return Stream(node_streams_dir(root, node) / "inbox.jsonl")


class Offsets:
    """إزاحات قارئٍ واحد — ملف جانبي خاص به، يُهمل ويُعاد بلا خسارة
    (درءُ التكرار في السجلات المختومة لا هنا — انظر رأس الملف)."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)

    def get(self, stream_key: str) -> int:
        if not self.path.exists():
            return 0
        try:
            return int(json.loads(self.path.read_text(encoding="utf-8"))
                       .get(stream_key, 0))
        except (json.JSONDecodeError, ValueError, TypeError):
            return 0   # إسقاطٌ فاسد يُعاد من الصفر — والدرء في السجلات

    def set(self, stream_key: str, offset: int) -> None:
        data = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
        data[stream_key] = offset
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # كتابة ذرّية: انهيارٌ وسطها يترك القديمَ سليمًا لا ملفًا مبتورًا
        # يعيد كلَّ الإزاحات صفرًا (عيب تدقيق م٥)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
