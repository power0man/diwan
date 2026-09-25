"""بوابة التحقق لنتائج هرمس — المدخل الخارجي لا يُصدَّق (م٦، ق١٧/ق٢٠).

هرمس يبحث ويكتب في `~/diwan-work/hermes-research/findings/` خارج
المستودع (ق١٧)، ونتيجتُه **مدخلٌ خارجي غير موثوق**: قد تحمل حقنَ
توجيهاتٍ من صفحات الويب التي قرأها، أو ادعاءاتٍ بلا مصدر. هذه البوابة
هي الدرزُ الوحيد الذي تعبر منه نتيجةُ هرمس إلى ديوان:

1. **صدُّ الحقن أولًا**: أنماطُ توجيهٍ للوكلاء (تجاهل التعليمات،
   system:، كتل أحداثٍ مصطنعة…) تُغلق الملفَ كلَّه برمزٍ مسمى — لا
   «تعقيم» جزئيًّا، فالملوث عمدًا لا يُرمَّم.
2. **التأريخ شرط**: نتيجةٌ بلا تاريخ (في اسم الملف أو متنه) تُرَدّ.
3. **الادعاء بمصدره**: كلُّ بندٍ بلا رابط مصدرٍ يُرَدّ برمزه (بندًا)،
   ونتيجةٌ بلا أي بندٍ موثق تُرَدّ كلها.
4. **الحقوق عند الباب**: المقبول يصير موادَّ `summarized` من مصدرٍ
   مسجلٍ في سجل الأصول (`hermes-research-local`) عبر `admitted_item`
   — لا مادة بلا قيد استحواذ.

الرمزُ المسمى هو العقد: `instruction_injection`، `finding_undated`،
`claim_unsourced`، `no_sourced_claims`، `finding_empty`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.acquisitions import SourceRegister
from core.canonical import PayloadRejected, digest
from core.knowledge import KnowledgeItem

SOURCE_ID = "hermes-research-local"

# أنماط حقن التوجيه — قائمة مغلقة مسماة تتسع بقرار لا بصمت
INJECTION_PATTERNS = (
    ("ignore_instructions_en",
     re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)", re.I)),
    ("ignore_instructions_ar",
     re.compile(r"تجاهل\s+(?:كل\s+)?(?:التعليمات|ما\s+سبق)")),
    ("system_role", re.compile(r"(?:^|\n)\s*system\s*:", re.I)),
    ("fake_notification",
     re.compile(r"<(?:task-notification|system-reminder|antml)", re.I)),
    ("agent_directive",
     re.compile(r"(?:نفّذ|شغّل)\s+(?:الأمر|السكربت)|run\s+this\s+command",
                re.I)),
    ("shell_payload", re.compile(r"\b(?:curl|wget)\s+http|rm\s+-rf\s")),
    ("event_shaped",
     re.compile(r'"kind"\s*:\s*"(?:query|answer|item_published)"')),
)

_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_LINK = re.compile(r"https://[^\s/]+\.[^\s/]+\S*")   # https بمضيفٍ منقوط
_BULLET = re.compile(r"^\s*[-*•]\s+(.+)$")
# محارف التحكم والاتجاه وصفرية العرض — تعبر UTF-8 وتخدع العرض والمطابقة
_CONTROL = re.compile("[" + "".join(map(chr,
    list(range(0x00, 0x09)) + [0x0b, 0x0c]
    + list(range(0x0e, 0x20)) + [0x7f]
    + list(range(0x200b, 0x2010)) + list(range(0x202a, 0x202f))
    + list(range(0x2066, 0x206a)) + [0xfeff])) + "]")
MAX_FILE_BYTES = 1_000_000
MAX_CLAIM_CHARS = 2_000


def _valid_recent_date(s: str, today: "datetime.date") -> bool:
    import datetime as _dt
    try:
        d = _dt.date.fromisoformat(s)
    except ValueError:
        return False
    return d <= today


def review_finding(path: Path, register: SourceRegister) -> dict:
    """يعيد {"items": [(مادة مقبولة، بصمتها)], "rejected_claims",
    "date"} — أو يرمي PayloadRejected برمزٍ مسمى للملف كله."""
    import datetime as _dt
    p = Path(path)
    if p.stat().st_size > MAX_FILE_BYTES:
        raise PayloadRejected("hermes.finding", "finding_too_large",
                              f"{p.name}: {p.stat().st_size} بايت فوق "
                              f"السقف {MAX_FILE_BYTES}")
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PayloadRejected("hermes.finding", "finding_not_utf8",
                              f"{p.name}: ترميز غير UTF-8 — {exc.reason}"
                              ) from exc
    if not text.strip():
        raise PayloadRejected("hermes.finding", "finding_empty",
                              f"نتيجة فارغة: {p.name}")
    for code_name, pat in INJECTION_PATTERNS:
        m = pat.search(text)
        if m:
            raise PayloadRejected(
                "hermes.finding", "instruction_injection",
                f"نمط حقن توجيه ({code_name}) في {p.name}: "
                f"{m.group(0)[:60]!r} — الملف يُصد كله")
    # التاريخ يُقوَّم تقويمًا لا نمطًا (2026-13-45 كانت تمر)، ولا
    # يُلتقط من داخل رابطٍ يتحكم به الخارج، ولا يُقبل مستقبليًّا
    today = _dt.date.today()
    candidates = _DATE.findall(p.name) \
        or _DATE.findall(_LINK.sub(" ", text))
    date = next((c for c in candidates
                 if _valid_recent_date(c, today)), None)
    if date is None:
        raise PayloadRejected(
            "hermes.finding", "finding_undated",
            f"نتيجة بلا تاريخ تقويمي صحيح غير مستقبلي: {p.name}")

    items, rejected = [], []
    lines = text.splitlines()
    bullet_no = 0
    for line in lines:
        bm = _BULLET.match(line)
        if not bm:
            continue
        bullet_no += 1
        claim = bm.group(1).strip()
        if _CONTROL.search(claim):   # الملوث لا يُرمَّم — يُرَدّ بندًا
            rejected.append({"bullet": bullet_no,
                             "code": "claim_control_chars",
                             "text": claim[:80]})
            continue
        if len(claim) > MAX_CLAIM_CHARS:
            rejected.append({"bullet": bullet_no, "code": "claim_too_long",
                             "text": claim[:80]})
            continue
        if not _LINK.search(claim):
            rejected.append({"bullet": bullet_no, "code": "claim_unsourced",
                             "text": claim[:80]})
            continue
        item = register.admitted_item(KnowledgeItem(
            text=claim, lang="ar", domain="governance-intel",
            use_internal=True, use_distribution=False,
            source_id=SOURCE_ID,
            locus=f"{Path(path).name} — بند {bullet_no}",
            originality="summarized",
            part=f"un-law-scout {date}",
        ))
        items.append((item, digest(item.fingerprint_payload())))
    if not items:
        raise PayloadRejected(
            "hermes.finding", "no_sourced_claims",
            f"لا بند موثقًا بمصدر في {p.name} "
            f"(رُدّ {len(rejected)} بندًا)")
    import hashlib
    return {"items": items, "rejected_claims": rejected, "date": date,
            # بصمة الملف — ليقيّد المستهلكُ استهلاكَه فلا يُعاد قبول
            # المعدَّل بلا أثر (حد معلن: سجل الاستهلاك درزُ مستهلكه)
            "finding_digest": hashlib.sha256(
                text.encode("utf-8")).hexdigest()}
