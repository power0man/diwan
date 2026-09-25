"""ختمُ الاعتماد وقفلُ الكتابة للسجلات الحاكمة (ق٢١).

المرساة في `Ledger` تكشف قصَّ الذيل فقط، والإلحاقُ بعدها مقبولٌ بطبيعتها
— وهذا يكفي سجلاتِ المعرفة النامية، **ولا يكفي السجلات الحاكمة**
(الأصول، العقود، المسارد، السوابق): قيدٌ ملتفٌّ حسنُ الصياغة بعد المرساة
كان يصير «النافذ» ويجتاز الفحص الصارم (قاتل تدقيق م٣).

العلاج: في السجل الحاكم تكون المرساةُ **ختمَ اعتمادٍ تامًّا** — كل كتابةٍ
محكومة تُقفَل (flock) وتُلحق وتُعيد الرسوَّ في الطقس نفسه، وكلُّ قراءةٍ
تشترط تطابقَ الرأس والعدد مع الختم تطابقًا تامًّا: قيدٌ بعد الختم يُغلق
السجلَ كلَّه (فشلٌ مغلق) حتى اعتمادٍ جديد عبر القناة المحكومة.

حدٌّ معلن لا مُدَّعى خلافه: من يملك إعادةَ كتابة ملف الختم نفسه مع السجل
معًا خارجُ هذا الكشف — حماية ملف الختم حدُّ ثقة نظام الملفات، كمرساة م٠
سواء (وترقيتُه إلى توقيعٍ درزُ م٧ المقيد في ق١٥).
"""
from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager

from core.ledger import Ledger, LedgerCorrupt
from core.signing import UNSIGNED


@contextmanager
def write_lock(ledger: Ledger):
    """قفلُ الكتّاب المتزامنين: نافذة (قراءة الذيل ← إلحاق ← رسو) حصرية —
    كاتبان بلا قفلٍ يكتبان قيدين بنفس prev فتنكسر السلسلة على القرص
    كسرًا لا جراحة له (عيب تدقيق م٣/٩)."""
    lock_path = ledger.path.with_suffix(ledger.path.suffix + ".lock")
    with lock_path.open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def require_seal(ledger: Ledger, what: str) -> str:
    """يرفض سجلًّا حاكمًا خارج ختمه: قيودٌ بلا ختم، أو قيودٌ بعده.

    يُستدعى في كل مسار قراءةٍ للسجلات الحاكمة — فالالتفاف على الكاتب
    المحكوم مكشوفٌ قبل أن يُخدَم قيدُه لأي مستهلك.
    """
    from core.signing import anchor_signature_state, expects_signature
    entries = ledger.entries()
    if not ledger.anchor_path.exists():
        if entries:
            raise LedgerCorrupt(
                f"{what}: قيود بلا ختم اعتماد — قصُّ ذيلٍ أو بناءٌ خارج "
                f"القناة المحكومة؛ يُفحص يدويًّا")
        from core.signing import sig_path
        if sig_path(ledger).exists():
            # توقيعٌ بلا مرساةٍ ولا قيود: مُحيَ السجل وبقي توقيعه —
            # الفرع كان يعود «سليمًا» قبل أي فحص (قاتل تدقيق ق٢٥)
            raise LedgerCorrupt(
                f"{what}: توقيعٌ قائم بلا مرساة ولا قيود — السجل مُحيَ "
                f"أو أُفرِغ خارج القناة المحكومة؛ يُفحص يدويًّا")
        # سجلٌّ حاكم بلا توقيع: يُعلَن (ويقتل تحت الصرامة) ولا يُبتلع —
        # والفارغُ الجديد مشروعٌ قبل أول كتابة
        return anchor_signature_state(ledger, what)
    # «إجباري الصدق حيث وُجد التوقيع» — مع تمييز **تعذُّر** التحقق
    # (لا مفتاح: حدُّ بيئةٍ يُعلَن، والختمُ أدناه مفروضٌ كاملًا) عن
    # **فشله** (اختلافٌ فعلي: يُرفض في كل بيئة). خلطُهما كان يقفل
    # السجلات الحاكمة كلَّها أمام أي بيئةٍ بلا مفتاح (تدقيق التسليم)
    state = anchor_signature_state(ledger, what)
    a = ledger.read_anchor()
    if type(a["count"]) is not int or len(entries) != a["count"]:
        raise LedgerCorrupt(
            f"{what}: {len(entries)} قيدًا والختم يعتمد {a['count']} — "
            f"كتابةٌ خارج القناة المحكومة أو قصُّ ذيل")
    head = entries[-1]["digest"] if entries else "0" * 64
    if head != a["head"]:
        raise LedgerCorrupt(f"{what}: رأس السجل لا يطابق ختم الاعتماد")
    return state       # المستدعي يعرف حالَ التوقيع برمجيًّا لا بـstderr


def sealed_append(ledger: Ledger, record: dict, what: str) -> str:
    """الكتابة الحاكمة الوحيدة: قفلٌ ← فحص الختم ← إلحاق ← رسوٌّ جديد —
    وسجلٌّ موقَّعُ المرساة يُعاد توقيعُه. **الكتابةُ فيه لحامل المفتاح
    وحده**: بلا مفتاحٍ تُرَدّ برمزها (مرساةٌ جديدة بتوقيعٍ قديم تصير
    إنذارَ عبثٍ كاذبًا) — والقراءةُ بخلافها مفتوحةٌ بختمها."""
    from core.signing import (expects_signature, prepare_signer,
                              require_initial_signature, sign_anchor)
    with write_lock(ledger):
        require_initial_signature(ledger)
        require_seal(ledger, what)
        # المفتاح يُطلب **قبل** أي كتابة: كان الترتيب إلحاقًا ورسوًّا
        # ثم رفضًا، فيبقى الأثرُ ويصير السجل في `signature_mismatch`
        # دائم — عين الضرر الذي زعم العقدُ منعَه (قاتل تدقيق ق٢٥)
        signer = prepare_signer(ledger) if expects_signature(ledger) else None
        entry_digest = ledger.append(record)
        ledger.anchor()
        if signer is not None:
            sign_anchor(ledger, signer=signer)
        return entry_digest
