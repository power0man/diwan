#!/usr/bin/env python3
"""عيّنةٌ طبقيّةٌ من بنكٍ مفتوح: تُصغِّر الكلفة ولا تُغيِّر التركيب.

قياسُ محرّكٍ على ١٩٠٢ حالة نحو ستّ ساعاتٍ على هذا الماك، وأربعةُ محرّكات
يومان. والعيّنةُ تُنزل ذلك إلى ساعة — بشرطٍ واحد: **ألّا تُغيّر ما يُقاس**.

فلو أُخذت الحالاتُ من أوائل الملفّات لقِيس ما صادف أن كُتب أولًا. ولو أُخذت
عشوائيًّا بلا طبقات لاختلّت نسبُ القدرات بين محرّكٍ وآخر إن تغيّر البذر.
فالعيّنةُ هنا:

- **طبقيّةٌ على (الطبقة × القدرة)**: كلُّ قدرةٍ تأخذ من العيّنة بنسبة حجمها
  في البنك، فتبقى خريطةُ البنك كما هي مصغَّرة.
- **حتميّة**: الاختيار بترتيبٍ مشتقٍّ من بصمة `case_id`، فالعيّنةُ نفسُها
  تخرج في كل تشغيلة وعلى كل جهاز، ويُقاس المحرّكان على الحالات نفسِها.
- **تحفظ الحرِجَ**: كلُّ حالةٍ `critical` تدخل العيّنة وجوبًا **ولو فاقت نصيبَ
  طبقتها**، فهي التي وُضعت لتكشف الإخفاق. فالهدفُ يحكم غيرَ الحرِج وحده، وحجمُ
  العيّنة `max(target, عدد الحرِج)`. ويُنشر `critical_dropped` في المخرَج ليُرى
  الضمانُ مقيسًا لا موعودًا.

والناتجُ حزمٌ صالحةٌ تمرّ `validate_suite`، فتُقاس بالمُشغِّل نفسِه بلا
استثناء.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.capabilities import validate_suite

MAX_CASES_PER_SUITE = 100


def _slug(text: str) -> str:
    """معرّفٌ ASCII ثابتٌ لاسم قدرةٍ قد يكون عربيًّا.

    المدقِّقُ يشترط معرّفًا ASCII، وأسماءُ القدرات قد تُكتب بالعربية. فيُبقى
    ما يصلح ويُلحق به بصمةٌ قصيرة تحفظ التمييز، ولا يُطرح اسمٌ لأنه عربيّ.
    """
    kept = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")
    if not text.isascii() or not kept:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
        kept = f"{kept}_{digest}" if kept else f"cap_{digest}"
    return kept


def _rank(case_id: str, salt: str) -> str:
    """ترتيبٌ ثابتٌ لا يتبع ترتيب الملفّ ولا حالةَ المفسِّر."""
    return hashlib.sha256(f"{salt}:{case_id}".encode("utf-8")).hexdigest()


def load_capability_suites(bank_open: Path) -> list[tuple[Path, dict]]:
    out = []
    for path in sorted(bank_open.rglob("*.json")):
        if path.name.endswith(".meta.json"):
            continue
        suite = json.loads(path.read_text(encoding="utf-8"))
        if suite.get("kind") == "agentic_tasks":
            continue  # للمهامّ الوكيلة مُشغِّلٌ آخر
        out.append((path, suite))
    return out


def stratified(suites: list[tuple[Path, dict]], target: int, salt: str) -> dict[str, list[dict]]:
    """يُعيد {الطبقة_القدرة: حالات} بنسب البنك نفسِها."""
    strata: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for path, suite in suites:
        tier = path.parent.name
        for case in suite["cases"]:
            strata[(tier, case["capability"])].append(case)

    total = sum(len(v) for v in strata.values())
    picked: dict[tuple[str, str], list[dict]] = {}
    for key, cases in strata.items():
        ordered = sorted(cases, key=lambda c: _rank(c["case_id"], salt))
        critical = [c for c in ordered if c.get("critical")]
        rest = [c for c in ordered if not c.get("critical")]
        # نصيبُ الطبقة بنسبتها، وحالةٌ واحدة على الأقلّ لكل قدرةٍ موجودة
        share = max(1, round(target * len(cases) / total))
        # **الحرِجُ كلُّه يدخل، ولو فاق النصيب.** وكان يُقصّ بـcritical[:share]،
        # فتسقط ٧٧ حالةً حرِجة من ٣١٥ عند هدف ٣٠٠ — وأشدُّ ما يسقط حيث الحرِجُ
        # أكثف، أي في القدرات التي وُضعت لكشف الإخفاق بعينها. وإسقاطُها يرفع
        # الدرجة بلا عمل، ولا يظهر في أي رقم. فالهدفُ يحكم غيرَ الحرِج وحده،
        # وحجمُ العيّنة يصير max(target, عدد الحرِج) — وتُعلن الكلفةُ في المخرَج.
        picked[key] = critical + rest[: max(0, share - len(critical))]
    return {f"{t}__{_slug(c)}": v for (t, c), v in picked.items() if v}


def write_sample(picked: dict[str, list[dict]], out_dir: Path, bank: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, cases in sorted(picked.items()):
        for part in range(0, len(cases), MAX_CASES_PER_SUITE):
            chunk = cases[part : part + MAX_CASES_PER_SUITE]
            suffix = "" if len(cases) <= MAX_CASES_PER_SUITE else f"_{part // MAX_CASES_PER_SUITE}"
            sid = f"sample_{name}{suffix}"
            suite = {"schema_version": 1, "suite_id": sid, "split": "development",
                     "description": f"عيّنةٌ طبقيّة من {bank}: {name}. لا تُنشر رقمًا للبنك كلِّه.",
                     "cases": chunk}
            validate_suite(suite)
            path = out_dir / f"{sid}.json"
            path.write_text(json.dumps(suite, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            written.append(path)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bank_open", type=Path, help="مجلّد open/ في البنك")
    ap.add_argument("--target", type=int, default=300, help="حجم العيّنة المستهدَف")
    ap.add_argument("--salt", default="k2", help="بذرُ الترتيب الحتميّ")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    suites = load_capability_suites(args.bank_open)
    total = sum(len(s["cases"]) for _, s in suites)
    picked = stratified(suites, args.target, args.salt)
    files = write_sample(picked, args.out, args.bank_open.parent.name)
    n = sum(len(v) for v in picked.values())
    bank_critical = sum(1 for _, s in suites for c in s["cases"] if c.get("critical"))
    sample_critical = sum(1 for v in picked.values() for c in v if c.get("critical"))
    print(json.dumps({
        "bank_cases": total, "bank_suites": len(suites),
        "sample_cases": n, "sample_suites": len(files),
        "coverage_pct": round(100 * n / total, 1),
        # يُنشر الحرِجُ صراحةً: بلا هذين الرقمين يُقرأ الضمانُ من الوثيقة
        # ولا شيء في المخرَج ينقضه لو انكسر يومًا.
        "bank_critical": bank_critical, "sample_critical": sample_critical,
        "critical_dropped": bank_critical - sample_critical,
        "target": args.target,
        "strata": len(picked), "salt": args.salt,
        "out": str(args.out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
