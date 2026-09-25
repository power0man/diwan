"""التسلسل القانوني والبصمة.

القاعدة: الحمولة الواحدة تعطي بايتات واحدة، فبصمة واحدة. وما لا يُبصم
بأمان يُرفض عند الحدّ لا يُمرَّر.

قياسٌ سابق أثبت أنّ هذا ليس تحصيل حاصل: 1.0 و1.5e-7 و9007199254740993
تعطي بصمات مختلفة بين بايثون وJavaScript وGo، والأخير يفقد الدقّة صامتًا
فوق 2^53 في JavaScript. والنواة هنا بايثون واحدة، فلا مشكلة تطابق بين
اللغات اليوم — ويبقى المنع قائمًا لأنّ يوم تدخل لغة ثانية لا يُعاد بناء
الحمولات المبصومة.
"""
from __future__ import annotations

import hashlib
import json

SAFE_INT = 2**53 - 1


class PayloadRejected(ValueError):
    """حمولة لا تُبصم بأمان. تحمل مسارًا ورمزًا ليُفحص آليًّا."""

    def __init__(self, path: str, code: str, reason: str):
        super().__init__(f"{path}: {reason} [{code}]")
        self.path = path
        self.code = code
        self.reason = reason


def check_payload(value, path: str = "$") -> None:
    """يرمي PayloadRejected عند أوّل ما لا يُبصم بأمان."""
    if value is None:
        return
    if isinstance(value, str):
        # النصّ الحامل لبديلٍ منفرد (U+D800–U+DFFF) لا يُرمَّز UTF-8، فينفجر
        # البصم بعد المرور من الفحص. فيُرفض هنا لا هناك.
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise PayloadRejected(path, "string_not_encodable",
                                  "نصّ لا يُرمَّز UTF-8 (بديلٌ منفرد؟)") from None
        return
    if isinstance(value, bool):  # قبل int: bool نوع فرعي منه في بايثون
        return
    if isinstance(value, int):
        if abs(value) > SAFE_INT:
            raise PayloadRejected(path, "int_out_of_safe_range",
                                  f"عدد صحيح خارج المدى الآمن ({value})؛ استعمل نصًّا")
        return
    if isinstance(value, float):
        raise PayloadRejected(path, "float_not_allowed",
                              "عدد عشري؛ استعمل وحدات صغرى صحيحة")
    if isinstance(value, list):
        for i, v in enumerate(value):
            check_payload(v, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise PayloadRejected(path, "non_string_key", f"مفتاح غير نصّي: {k!r}")
            check_payload(v, f"{path}.{k}")
        return
    raise PayloadRejected(path, "unsupported_type", f"نوع غير مدعوم: {type(value).__name__}")


def canonical_bytes(value) -> bytes:
    check_payload(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()
