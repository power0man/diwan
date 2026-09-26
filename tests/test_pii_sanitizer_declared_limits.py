"""غ٥: حدودُ معقِّم البيانات الشخصية (`core/router_sovereign.py::PIISanitizer`) اختباراتٌ لا نثر.

المعقِّمُ شرطُ المستوى الطليعيّ، وبوابةُ م٤ «صفرُ تسرّبٍ في بنك البيانات الشخصية العدائيّ»
(`evaluation/protocols/sovereign_v1.json`). وقبل أيّ ناقلٍ هذه حالُه:
- ثلاثُ صورٍ يحجبها: الهويةُ بأرقامٍ غربيةٍ متّصلة، والجوّالُ المتّصل، والبريدُ بصيغته.
- وكلُّ صورةٍ في `LEAKS` تمرّ كما هي. فالناقلُ يبقى معطَّلًا (`docs/SOVEREIGN-CARRIER.md` §١).

ومن يُغلق صورةً ينقلها من `LEAKS` إلى `REDACTED`، فينقلب اختبارُها تأكيدًا على الحجب.
"""
from __future__ import annotations

import pytest

from core.router_sovereign import PIISanitizer

REDACTED = {
    "id_western": ("هويته 1082736451", "1082736451"),
    "phone_contiguous": ("جواله 0501112233", "0501112233"),
    "email": ("بريده a.b@example.com", "a.b@example.com"),
}

LEAKS = {
    "id_eastern_digits": ("هويته ١٠٨٢٧٣٦٤٥١", "١٠٨٢٧٣٦٤٥١"),
    "id_spaced": ("هويته 108 273 6451", "108 273 6451"),
    "id_dashed": ("هويته 1082-736-451", "1082-736-451"),
    "id_glued_to_arabic": ("رقمالهوية1082736451فقط", "1082736451"),
    "phone_spaced": ("جواله 050 111 2233", "050 111 2233"),
    "phone_intl_spaced": ("جواله +966 50 111 2233", "+966 50 111 2233"),
    "phone_eastern_digits": ("جواله ٠٥٠١١١٢٢٣٣", "٠٥٠١١١٢٢٣٣"),
    "email_spelled_out": ("بريده a.b at example dot com", "a.b at example dot com"),
    "iban": ("حسابه SA0380000000608010167519", "SA0380000000608010167519"),
    "iban_spaced": ("حسابه SA03 8000 0000 6080 1016 7519", "SA03 8000 0000 6080 1016 7519"),
    "full_name": ("اسمه محمد بن عبدالله القحطاني", "محمد بن عبدالله القحطاني"),
    "passport": ("جوازه A12345678", "A12345678"),
    "national_address_code": ("عنوانه الوطني RRRD2929", "RRRD2929"),
}


@pytest.mark.parametrize("name", sorted(REDACTED))
def test_the_forms_it_redacts(name):
    text, secret = REDACTED[name]
    sanitized, mapping = PIISanitizer.sanitize(text)
    assert secret not in sanitized and secret in mapping.values()
    assert PIISanitizer.desanitize(sanitized, mapping) == text


@pytest.mark.parametrize("name", sorted(LEAKS))
def test_declared_limit_this_form_leaks(name):
    text, secret = LEAKS[name]
    sanitized, mapping = PIISanitizer.sanitize(text)
    assert secret in sanitized and not mapping, f"{name}: أُغلق الحدّ؟ انقله إلى REDACTED"
