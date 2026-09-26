"""عربيةٌ مقروءة في الرسوم (ج٨): matplotlib لا يصل الحروف ولا يرتّبها من اليمين.

    from diwan_ar import ar
    plt.title(ar("الإيرادات حسب الفرع"))

`ar` تشكّل الحروف بـarabic-reshaper ثم ترتّبها للعرض بـpython-bidi. وهي للعرض وحده:
لا تُكتب نتائجُها في ملفّات بيانات، فالنصُّ المشكَّل ليس النصَّ الأصلي.
"""
import arabic_reshaper
from bidi import get_display


def ar(text) -> str:
    return get_display(arabic_reshaper.reshape(str(text)))
