"""اللقطة والذيل — تدوير السجلات النامية بلا فقدِ حرفٍ (م٧، ق٢٠).

مجس م٦ قاس: الإلحاق المختوم خطيٌّ بحجم الملف والتراكم تربيعي — فالعلاج
المقرر معماريًّا «لقطة + ذيل»: عند التدوير يُجمَّد الملفُ الحالي لقطةً
مختومة (`<اسم>.snap-N.jsonl` بمرساتها + بصمة بايتاتها)، ويُفتتح الذيلُ
الجديد بقيد **وصلٍ** يحمل اسم اللقطة ورأسَها وعدَّها وبصمةَ ملفها —
فالسلسلة الكاملة قائمة عبر الأجيال، والإلحاق يعود رخيصًا.

فروضُ التدوير (تدقيق م٧ — كانت وعودَ وصفٍ فصارت بنية):
- **التيارات لا تُدوَّر**: قراؤها بإزاحات قيودٍ مطلقة، والتدوير يعيد
  الترقيم فيُسقط رسائل صامتًا أو يحجر التيار — رفضٌ بنيوي مسمى.
- **التاريخ كله يُتحقق قبل التدوير** (`full_entries`): لا تدوير فوق
  سلسلةٍ مكسورة أو لقطةٍ غائبة، وترقيمُ اللقطة الجديد أقصى الأرقام+1.
- **الانهيار لا يفقد حرفًا**: اللقطة تُكتب وتُثبَّت (fsync ملفًا
  ومجلدًا) وتُتحقق قبل مسّ الأصل، والذيلُ الجديد يُبنى في ملف مؤقت
  ويحل محل الأصل بـ`os.replace` الذرّي — انهيارٌ قبل الاستبدال يترك
  الأصل سليمًا (ولقطةً يتيمة يرفض تكرارها الاسمي فتُفحص)، وانهيارٌ
  بين الاستبدال والرسوّ فشلٌ مغلق تعافيه موثق: يُتحقق الذيلُ يدويًّا
  على قيد وصله ثم يُرسى.
- **السجل الموقَّع يبقى موقَّعًا**: مرساتا اللقطة والذيل تُوقَّعان
  بالمفتاح نفسه، وغيابُه يرفض التدوير كله — لا `signature_mismatch`
  زائفًا بعد صيانة مشروعة.

قراءةُ التاريخ الكامل (`full_entries`) تعيد بناءه لقطةً فلقطةً
متحققةً — كسرُ أي حلقةٍ يُغلق الكل (`LedgerCorrupt`). ودرءُ التكرار
واستهلاكُ العقد (ق٢٢) يقرآن التاريخَ الكامل لا الذيل، فالتدوير لا
يمحو حقيقةَ عبورٍ ولا يجعل مسلَّمةً «مدسوسة».
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

from core.ledger import GENESIS, Ledger, LedgerCorrupt
from core.seal import require_seal, write_lock

LINK_KIND = "snapshot_link"
_SNAP_RE = re.compile(r"\.snap-(\d+)\.jsonl$")


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snap_path(path: Path, n: int) -> Path:
    return path.with_name(path.name.replace(".jsonl", f".snap-{n}.jsonl"))


def snapshots_of(path: Path) -> list[Path]:
    out = []
    for p in path.parent.glob(path.name.replace(".jsonl", ".snap-*.jsonl")):
        if _SNAP_RE.search(p.name) and not p.name.endswith(".anchor"):
            out.append(p)
    return sorted(out, key=lambda p: int(_SNAP_RE.search(p.name).group(1)))


def _fsync_file_and_dir(path: Path) -> None:
    with path.open("rb") as f:
        os.fsync(f.fileno())
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def rotate(path: Path) -> Path:
    """يجمّد السجل الحي لقطةً ويفتتح ذيلًا موصولًا. يعيد مسار اللقطة."""
    path = Path(path)
    if "streams" in path.parts:
        raise LedgerCorrupt(
            f"تدوير {path.name}: التيارات لا تُدوَّر — قراؤها بإزاحات "
            "مطلقة والتدوير يُسقط رسائل صامتًا أو يحجر التيار "
            "(رفض بنيوي، تدقيق م٧)")
    from core.signing import sig_path, sign_anchor, prepare_signer
    led = Ledger(path)
    with write_lock(led):
        # التاريخ كله — لا الذيل وحده — يُتحقق قبل أي مساس
        full_entries(path)
        entries = led.entries()
        if not entries:
            raise LedgerCorrupt(f"تدوير {path.name}: سجل فارغ — لا معنى")
        if entries[0].get("record", {}).get("kind") == LINK_KIND \
                and len(entries) == 1:
            raise LedgerCorrupt(f"تدوير {path.name}: ذيل بلا قيود جديدة")
        was_signed = sig_path(led).exists()
        # Match the active policy and private/public identity before writing a
        # snapshot. HMAC material must never enter the Ed renewal path.
        signer = prepare_signer(led) if was_signed else None
        nums = [int(_SNAP_RE.search(p.name).group(1))
                for p in snapshots_of(path)]
        n = (max(nums) + 1) if nums else 1
        snap = _snap_path(path, n)
        if snap.exists():
            raise LedgerCorrupt(f"لقطة قائمة: {snap.name} — بقايا "
                                "تدوير منقطع؛ يُفحص يدويًّا")
        # ١ — اللقطة تُكتب وتُثبَّت وتُتحقق قبل مسّ الأصل
        snap.write_bytes(path.read_bytes())
        _fsync_file_and_dir(snap)
        snap_led = Ledger(snap)
        snap_led.anchor()
        if was_signed:
            sign_anchor(snap_led, signer=signer)
        require_seal(snap_led, f"لقطة {snap.name}")
        snap_led.verify_chain()
        link = {"kind": LINK_KIND, "snapshot": snap.name,
                "snapshot_head": led.head(),
                "snapshot_count": led.count(),
                "snapshot_file_digest": _file_digest(snap)}
        # ٢ — الذيل الجديد يُبنى جانبًا ثم يحل محل الأصل ذرّيًّا
        tmp = path.with_name(path.name + ".rotating")
        tmp.unlink(missing_ok=True)
        tmp_led = Ledger(tmp)
        tmp_led.append(link)
        _fsync_file_and_dir(tmp)
        os.replace(tmp, path)
        fresh = Ledger(path)
        fresh.anchor()
        if was_signed:
            sign_anchor(fresh, signer=signer)
        return snap


def _checked_generation(path: Path, generation_check=None) -> tuple[list[dict], dict | None]:
    """قيود جيلٍ واحد متحققًا ختمُه وسلسلتُه + قيدُ وصله إن وُجد."""
    led = Ledger(path, create=False)
    if generation_check is None:
        require_seal(led, f"جيل {path.name}")
        led.verify_chain()
    else:
        # Trusted caller supplies the complete per-generation seal/chain check.
        # A public-only verifier can use its explicit root/key without globals.
        generation_check(led)
    entries = led.entries()
    link = None
    if entries and entries[0].get("record", {}).get("kind") == LINK_KIND:
        link = entries[0]["record"]
        entries = entries[1:]
    return entries, link


def full_entries(path: Path, *, generation_check=None) -> list[dict]:
    """التاريخ الكامل عبر الأجيال — كل حلقة وصلٍ تُفحص أو يُغلق الكل."""
    chain: list[list[dict]] = []
    current = Path(path)
    seen: set[str] = set()
    while True:
        entries, link = _checked_generation(current, generation_check)
        chain.append(entries)
        if link is None:
            break
        base = re.sub(r"\.snap-[0-9]+(?=\.jsonl$)", "", current.name).removesuffix(".jsonl")
        name = link.get("snapshot")
        if (not isinstance(name, str)
                or re.fullmatch(re.escape(base) + r"\.snap-[0-9]+\.jsonl", name) is None
                or type(link.get("snapshot_count")) is not int or link["snapshot_count"] < 0
                or any(not isinstance(link.get(field), str)
                       or re.fullmatch(r"[0-9a-f]{64}", link[field]) is None
                       for field in ("snapshot_head", "snapshot_file_digest"))):
            raise LedgerCorrupt("invalid snapshot link or scope")
        snap = current.parent / name
        if snap.is_symlink():
            raise LedgerCorrupt("snapshot aliases are not allowed")
        if snap.name in seen:
            raise LedgerCorrupt(f"دورة وصلٍ في اللقطات: {snap.name}")
        seen.add(snap.name)
        if not snap.exists():
            raise LedgerCorrupt(
                f"قيد الوصل يسمّي لقطة غائبة: {snap.name}")
        info = snap.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise LedgerCorrupt("snapshot must be an independent regular file")
        if _file_digest(snap) != link["snapshot_file_digest"]:
            raise LedgerCorrupt(
                f"بصمة ملف اللقطة لا تطابق قيد الوصل: {snap.name}")
        snap_led = Ledger(snap, create=False)
        if snap_led.head() != link["snapshot_head"] \
                or snap_led.count() != link["snapshot_count"]:
            raise LedgerCorrupt(
                f"رأس/عدد اللقطة لا يطابق قيد الوصل: {snap.name}")
        current = snap
    out: list[dict] = []
    for gen in reversed(chain):
        out.extend(gen)
    return out


def verify_full(path: Path) -> bool:
    """تحققٌ تام عبر الأجيال — يعيد True أو يرمي LedgerCorrupt."""
    full_entries(path)
    return True
