"""السجل — إضافيّ فقط، وسلسلةُ بصماتٍ تكشف العبث.

لا واجهةَ تعديلٍ ولا حذف. وكل قيدٍ يحمل بصمة القيد السابق.

**وحدود الكشف مُعلَنة لا مُدَّعى خلافُها:** السلسلة وحدها تكشف تغييرَ قيدٍ
وسطيّ أو حذفَه أو إعادةَ ترتيبه. ولا تكشف — بذاتها — **قصَّ الذيل** ولا
**إلحاقًا مُزوَّرًا** على رأسٍ صحيح، لأنّ الناتج سلسلةٌ متّسقة. فلذلك
`anchor()` يكتب الرأس والعدد في ملفٍّ جانبيّ، و`verify_chain(strict=True)`
يقارن بهما. وبلا مرساة، الكشفُ ناقصٌ بحدٍّ معروف.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from core.canonical import canonical_bytes, digest

GENESIS = "0" * 64
ENTRY_KEYS = frozenset({"digest", "prev", "seq", "record"})
EFFECTFUL_KINDS = frozenset({"ok", "error"})


class LedgerCorrupt(RuntimeError):
    pass


REPO_ROOT = Path(__file__).resolve().parents[1]
# سجلاتُ حالة التشغيل: تُنشأ عند الحاجة وإن كانت حاكمةً موقَّعة
RUNTIME_LEDGERS = frozenset({"ledger/main.jsonl"})


def implicit_creation_refused(path: Path) -> bool:
    """السجلاتُ الحاكمة لبيانات المالك في جذر المستودع لا تُنشأ ضمنًا (ك٢٧).

    إنشاؤها فعلٌ صريح لأدوات البذر (`create=True`)، لا أثرٌ جانبيّ لتشغيل عقدةٍ أو
    خدمةٍ أو اختبار. فبدون هذا كان تشغيلُ الاختبارات في اللقطة العامة، حيث المتونُ
    غائبة، يترك فهرسًا ومسردًا وسجلَّ مصادرٍ فارغةً بلا مراسٍ، فتسقط بعده فحوصُ
    التوقيع والوثائق برسالة «لا مرساة». أمّا السجلُّ الرئيس والتياراتُ فحالةُ تشغيلٍ
    تُنشأ عند الحاجة، والمساراتُ خارج الجذر (مؤقّتة أو تجريبية) لا يمسّها هذا.
    """
    from core.signing import SIGNED_LEDGERS, is_stream_scope   # متأخّرٌ: signing يستورد هذا الملف
    try:
        rel = Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return False
    return rel in SIGNED_LEDGERS and rel not in RUNTIME_LEDGERS and not is_stream_scope(rel)


class Ledger:
    def __init__(self, path: str | os.PathLike, *, create: bool | None = None):
        self.path = Path(path)
        # أدوات الفحص لا تنشئ سجلًا مفقودًا ولا تغير mtime لموجود.
        # create=False يمنع أثر التهيئة فقط؛ ليس حاجز صلاحيات للكتابة.
        # وcreate=None (الافتراضي) ينشئ إلا سجلًّا حاكمًا لبيانات المالك في الجذر.
        if create is None:
            create = not implicit_creation_refused(self.path)
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch(exist_ok=True)

    @property
    def anchor_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".anchor")

    # — كتابة —

    def append(self, record: dict) -> str:
        """يقيّد ويعيد بصمة القيد. لا يُعدِّل ولا يحذف شيئًا."""
        if not isinstance(record, dict):
            raise TypeError("القيد كائنٌ نصّيّ المفاتيح")
        prev = self.head()
        entry = {"prev": prev, "seq": self.count(), "record": record}
        entry_digest = digest(entry)                     # يرمي قبل الكتابة إن لم يُبصم
        line = canonical_bytes({"digest": entry_digest, **entry}).decode("utf-8")
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry_digest

    def anchor(self) -> dict:
        """يثبّت الرأس والعدد خارج الملف — بها وحدها يُكشف قصُّ الذيل.
        الكتابة ذرّية (tmp+replace+fsync): انهيارٌ وسطها يترك القديمة
        سليمة لا مرساةً مبتورة (تدقيق م٧)."""
        a = {"head": self.head(), "count": self.count()}
        tmp = self.anchor_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(canonical_bytes(a).decode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.anchor_path)
        return a

    def read_anchor(self) -> dict:
        """يفك المرساة — المبتورة/المشوهة LedgerCorrupt لا انفجار خام."""
        try:
            a = json.loads(self.anchor_path.read_text(encoding="utf-8"))
            if not isinstance(a, dict) or "head" not in a or "count" not in a:
                raise ValueError("حقول ناقصة")
            return a
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            raise LedgerCorrupt(
                f"مرساة {self.anchor_path.name} مبتورة أو مشوهة: {exc} — "
                "كتابة المرساة ذرّية فهذا عبثٌ أو عطبُ وسيط") from exc

    # — قراءة —

    def entries(self) -> list[dict]:
        out = []
        with self.path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LedgerCorrupt(f"سطر {i} غير صالح: {exc}") from exc
                if not isinstance(e, dict):
                    raise LedgerCorrupt(f"سطر {i} ليس كائنًا")
                out.append(e)
        return out

    def count(self) -> int:
        return len(self.entries())

    def head(self) -> str:
        es = self.entries()
        return es[-1]["digest"] if es else GENESIS

    def find_by_idempotency_key(self, key: str, kinds=EFFECTFUL_KINDS) -> dict | None:
        """أوّل قيدٍ **ذي أثر** يحمل هذا المفتاح.

        والرفض ليس أثرًا: لم يُنادَ مزوّد ولم يُخصم مال. فلو طابقه البحث
        لتسمَّم المفتاح إلى الأبد — طلبٌ رُفض لنفاد رصيد اليوم لا يُنفَّذ غدًا.
        """
        if not key:
            return None
        for e in self.entries():
            rec = e.get("record", {})
            if rec.get("idempotency_key") == key and rec.get("kind") in kinds:
                return e
        return None

    def find_last_by_idempotency_key(self, key: str,
                                     kinds=EFFECTFUL_KINDS) -> dict | None:
        """**آخر** قيدٍ ذي أثر بهذا المفتاح — تشخيصُ حال المفتاح الآن.

        الأول يجيب «هل وقع فعلٌ قط؟» (عقد عدم التكرار)، والأخير يجيب
        «ما آخرُ ما وقع؟»: قيدُ عطلٍ قابلٍ للإعادة قديمٌ يليه عطلٌ غير
        قابلٍ كان يقنّع الأخيرَ فتُحرق محاولاتٌ عبثًا (تدقيق م٦)."""
        if not key:
            return None
        last = None
        for e in self.entries():
            rec = e.get("record", {})
            if rec.get("idempotency_key") == key and rec.get("kind") in kinds:
                last = e
        return last

    def verify_chain(self, strict: bool = False) -> bool:
        prev = GENESIS
        entries = self.entries()
        for i, e in enumerate(entries):
            missing = {"digest", "prev", "seq", "record"} - set(e)
            if missing:
                raise LedgerCorrupt(f"قيد {i} ناقص الحقول: {sorted(missing)}")
            extra = set(e) - ENTRY_KEYS
            if extra:
                # حقلٌ عُلويّ مدسوسٌ لا يدخل البصمة، فيُرفض بذاته
                raise LedgerCorrupt(f"قيد {i} فيه حقلٌ غير معروف: {sorted(extra)}")
            if e["prev"] != prev:
                raise LedgerCorrupt(f"سلسلة مكسورة عند {i}: prev لا يطابق")
            if e["seq"] != i:
                raise LedgerCorrupt(f"ترتيب مكسور عند {i}: seq={e['seq']}")
            if digest({"prev": e["prev"], "seq": e["seq"], "record": e["record"]}) != e["digest"]:
                raise LedgerCorrupt(f"بصمة لا تطابق عند {i}")
            prev = e["digest"]

        if strict:
            if not self.anchor_path.exists():
                raise LedgerCorrupt("لا مرساة: قصُّ الذيل غير قابل للكشف")
            # مرساةٌ مبتورة LedgerCorrupt مسمّاة لا JSONDecodeError خام (ك١٩)
            a = self.read_anchor()
            if len(entries) < a["count"]:
                raise LedgerCorrupt(
                    f"قيود ناقصة: {len(entries)} والمرساة تقول {a['count']}")
            if a["count"] > 0 and entries[a["count"] - 1]["digest"] != a["head"]:
                raise LedgerCorrupt("الرأس عند موضع المرساة لا يطابقها")
        return True
