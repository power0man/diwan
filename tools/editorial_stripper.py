#!/usr/bin/env python3
"""أداة تجريد المتن التراثي من الحواشي والزيادات التحريرية المعاصرة (المسار ب - م١٩ من نظام حقوق المؤلف السعودي).

تسقط المصنفات التراثية الكلاسيكية (مثل المعاجم العربية التاريخية: لسان العرب، تاج العروس،
مقاييس اللغة، الصحاح) في الملك العام بقوة النظام بعد مضي 50 عاماً على وفاة مؤلفيها
(المادة 19 من نظام حماية حقوق المؤلف الصادر بالمرسوم الملكي رقم م/41).
غير أن الطبعات الحديثة تحتوي غالباً على زيادات تحريرية معاصرة (هوامش المحققين، تخريجات حديثة،
أرقام صفحات الطبعات التجارية، تعليقات دور النشر) ذات حماية مستقلة.

يقوم هذا السكربت بـ:
1. تجريد هوامش وهوامش المحققين وتخريجاتهم المعاصرة.
2. عزل ترقيم الصفحات التجاري وعلامات الفهارس المحدثة.
3. استبقاء المتن التراثي الأصيل وشواهده الشعرية والقرآنية الصافية.
4. إخراج سجلات معرفية مجردة موسومة بالملك العام القانوني الصريح.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# أنماط الحواشي والتعليقات التحريرية المعاصرة
_MODERN_FOOTNOTE_PATTERNS = [
    # [1] أو (1) في بداية السطر متبوعة بتعليق أو تخريج دار نشر
    re.compile(r"^[^\S\r\n]*[\[\(]\d+[\]\)][^\S\r\n]*(?:انظر|راجع|دار|طبعة|أخرجه|في المطبوعة|في الأصل|هامش|المحقق).*$", re.MULTILINE),
    # هوامش داخلية واضحة: [هامش: ...] أو (تعليق المحقق: ...)
    re.compile(r"[\[\(](?:هامش|تعليق المحقق|المحقق|تخريج|حاشية|دار النشر)[^\]\)\n]*[\]\)]"),
    # إحالات الطبعات المعاصرة: (طبعة دار الفكر...) أو (طبعة بولاق...)
    re.compile(r"[\[\(]طبعة\s+[^\]\)\n]+[\]\)]"),
    # علامات الصفحات التجارية المستقلة في سطرها: [ص: 123] أو (ج 1 / ص 50) أو --- ص: 12 ---
    re.compile(r"^[^\S\r\n]*(?:---|===)?[^\S\r\n]*[\[\(]?(?:ص|ج\s*\d+\s*[/،]\s*ص)\s*:\s*\d+[\]\)]?[^\S\r\n]*(?:---|===)?[^\S\r\n]*$", re.MULTILINE),
]

# أنماط رؤوس الفهارس أو زيادات التبويب التجاري
_EDITORIAL_APPARATUS = [
    re.compile(r"^[^\S\r\n]*===\s*فهرس\s+.*$", re.MULTILINE),
    re.compile(r"^[^\S\r\n]*\[فهرس\s+.*\]\s*$", re.MULTILINE),
    re.compile(r"^[^\S\r\n]*مقدمة التحقيق\s*$", re.MULTILINE),
]


class EditorialStripper:
    """مجرّد المتون التراثية من الزيادات التحريرية والتجارية المعاصرة."""

    SAUDI_COPYRIGHT_ARTICLE_19_BASIS = (
        "الملك العام بموجب المادة 19 من نظام حماية حقوق المؤلف السعودي (مرسوم ملكي م/41) "
        "— انقضاء أكثر من 50 عاماً على وفاة صاحب المصنف التراثي الأصلي، مع تجريد حواشي المحققين المعاصرين."
    )

    @classmethod
    def strip_text(cls, text: str) -> Tuple[str, List[str]]:
        """يجرد النص من كافة الهوامش والإضافات التحريرية المعاصرة.

        يعيد النص الصافي وقائمة بما جُرّد للتدقيق والمطابقة.
        """
        if not text:
            return "", []

        stripped_elements: List[str] = []
        result = text

        for pattern in _MODERN_FOOTNOTE_PATTERNS:
            for match in pattern.finditer(result):
                stripped_elements.append(match.group(0))
            result = pattern.sub("", result)

        for pattern in _EDITORIAL_APPARATUS:
            for match in pattern.finditer(result):
                stripped_elements.append(match.group(0))
            result = pattern.sub("", result)

        # تنظيف الأسطر الفارغة الزائدة الناتجة عن الحذف
        cleaned_lines = [line.rstrip() for line in result.split("\n")]
        # ضغط الأسطر الفارغة المتتالية
        condensed: List[str] = []
        for line in cleaned_lines:
            if not line and condensed and not condensed[-1]:
                continue
            condensed.append(line)

        final_text = "\n".join(condensed).strip()
        return final_text, stripped_elements

    @classmethod
    def process_record(cls, record: Dict[str, Any]) -> Dict[str, Any]:
        """يعالج سجلاً معرفياً ويجرد متنه ويوسمه بسند الملك العام القانوني."""
        item = dict(record.get("item", record))
        original_text = item.get("text", "")
        clean_text, removed = cls.strip_text(original_text)

        item["text"] = clean_text
        item["originality"] = "classical_public_domain"
        item["legal_basis"] = cls.SAUDI_COPYRIGHT_ARTICLE_19_BASIS
        item["editorial_apparatus_stripped"] = len(removed) > 0
        item["stripped_elements_count"] = len(removed)

        return {"item": item, "removed_elements": removed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", "-i", type=Path, help="ملف JSONL المدخل")
    parser.add_argument("--output", "-o", type=Path, help="ملف JSONL المخرج المجرد")
    parser.add_argument("--preview", action="store_true", help="معاينة نموذج التجريد دون كتابة")
    args = parser.parse_args()

    sample_text = (
        "جمل: الجيم والميم واللام أصل واحد يدل على عظم وتجمع.\n"
        "[1] انظر: لسان العرب، دار صادر، طبعة بيروت، ج 11 ص 120.\n"
        "والجَمَل: معروف، وجمعه جِمال وأجمال.\n"
        "(تعليق المحقق: هذا ما أثبته ابن فارس وقيده صاحب الصحاح)\n"
        "قال تعالى: ﴿حَتَّى يَلِجَ الْجَمَلُ فِي سَمِّ الْخِيَاطِ﴾."
    )

    if args.preview or not args.input:
        clean, removed = EditorialStripper.strip_text(sample_text)
        print("=== النص الأصلي ===")
        print(sample_text)
        print("\n=== العناصر المجردة المحذوفة ===")
        for r in removed:
            print(f"- {r}")
        print("\n=== المتن التراثي الصافي المستبقى ===")
        print(clean)
        return 0

    if not args.input.exists():
        print(f"الملف غير موجود: {args.input}", file=sys.stderr)
        return 1

    count = 0
    total_stripped = 0
    with args.input.open("r", encoding="utf-8") as in_f:
        out_f = args.output.open("w", encoding="utf-8") if args.output else None
        try:
            for line in in_f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                processed = EditorialStripper.process_record(rec)
                total_stripped += processed["item"]["stripped_elements_count"]
                count += 1
                if out_f:
                    out_f.write(json.dumps(processed["item"], ensure_ascii=False) + "\n")
        finally:
            if out_f:
                out_f.close()

    print(f"تمت معالجة {count} سجلاً وتجريد {total_stripped} عنصراً تحريرياً.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
