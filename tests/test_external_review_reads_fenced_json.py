"""مراجعٌ يتجاهل حقلَ المخطّط لا يُقصى لعيبٍ في النقل — ولا يُعفى من الفحص.

قِيس في ٢٤ سبتمبر ٢٠٢٦: `mistral-large-3:675b-cloud` عبر Ollama **يتجاهل حقل
`format`** ويردّ نثرًا عربيًّا داخله كتلةُ JSON بين سياج ```، بينما
`deepseek-v4-flash:cloud` يحترمه. فكان ردُّ Mistral يُرفض بـ`invalid_json`
ويسقط المراجعُ كلُّه — لا لضعفٍ في حكمه بل لشكل ردّه. وذلك يحصر المراجعين في
النماذج ذات المخرج البنيويّ الأصيل، وهو انحيازٌ لا صلة له بجودة المراجعة،
ويُفرغ ق٥٠ من معناها إذ تبقى عائلةٌ واحدةٌ صالحة.

والاستخراجُ **نقلٌ لا ترخيص**: ما يُستخرج يمرّ بالفحص الصارم نفسِه.
"""
from __future__ import annotations

import json

import pytest

from evaluation.external_review import json_object_in, validate_response
from evaluation.multi_system_review import AutomaticReviewError

IDS = ["c1"]


def _judgment(**over) -> dict:
    base = {"id": "c1", "reference": "correct", "rubric": "sufficient",
            "my_answer": "٥٦", "reason": "الضربُ صحيح", "fix": None}
    base.update(over)
    return base


def _body(*judgments: dict) -> str:
    return json.dumps({"judgments": list(judgments)}, ensure_ascii=False)


def _fenced(body: str) -> str:
    return f"إليك حكمي على الحالة:\n\n```json\n{body}\n```\n\nوبالله التوفيق."


def test_a_fenced_reply_is_accepted():
    """الحالةُ المقيسة على Mistral."""
    assert validate_response(_fenced(_body(_judgment())), IDS) == [_judgment()]


def test_a_bare_json_reply_still_works():
    """ما كان يمرّ — ردُّ DeepSeek — يبقى يمرّ."""
    assert validate_response(_body(_judgment()), IDS) == [_judgment()]


def test_a_closing_brace_inside_a_string_does_not_truncate():
    raw = _fenced(_body(_judgment(reason="المرجعُ يقول } ثم يتمّ")))
    assert validate_response(raw, IDS)[0]["reason"] == "المرجعُ يقول } ثم يتمّ"


def test_a_reply_with_no_object_is_still_an_error():
    with pytest.raises(AutomaticReviewError) as exc:
        validate_response("لا أستطيع الحكم على هذا الملف.", IDS)
    assert exc.value.code == "invalid_json"


def test_extraction_does_not_excuse_a_bad_verdict():
    """الاستخراجُ لا يُمرّر حكمًا خارج القيم المسموحة."""
    raw = _fenced(_body(_judgment(reference="ربما")))
    with pytest.raises(AutomaticReviewError) as exc:
        validate_response(raw, IDS)
    assert exc.value.code == "reference_verdict_invalid"


def test_extraction_does_not_excuse_missing_fields():
    raw = _fenced('{"judgments": [{"id": "c1"}]}')
    with pytest.raises(AutomaticReviewError) as exc:
        validate_response(raw, IDS)
    assert exc.value.code == "judgment_fields"


def test_duplicate_keys_are_still_refused_after_extraction():
    """صرامةُ parse_json لا تُفقد بالاستخراج."""
    raw = _fenced('{"judgments": [], "judgments": []}')
    with pytest.raises(AutomaticReviewError):
        validate_response(raw, IDS)


@pytest.mark.parametrize("raw", ["لا كائن هنا", "", "   "])
def test_the_extractor_returns_the_reply_untouched_when_there_is_no_object(raw):
    assert json_object_in(raw) == raw


# ——— حقلُ fix: تسامحٌ حيث يُشار، لا حيث يُحكَم ———

def test_a_structured_fix_is_normalised_not_discarded():
    """قِيس على Mistral: يردّ `fix` كائنًا، وحكمُه صحيح.

    وكانت المراجعةُ كلُّها تُطرح لشكل حقلٍ استشاريّ لا يُبنى عليه قرار.
    """
    raw = _fenced(_body(_judgment(reference="incorrect", rubric="insufficient",
                                  fix={"reference": "٥٦"})))
    out = validate_response(raw, IDS)
    assert out[0]["reference"] == "incorrect"
    assert isinstance(out[0]["fix"], str)
    assert "٥٦" in out[0]["fix"]


def test_a_none_fix_stays_none():
    assert validate_response(_body(_judgment(fix=None)), IDS)[0]["fix"] is None


def test_a_plain_string_fix_is_untouched():
    out = validate_response(_body(_judgment(fix="اجعل المرجع ٥٦")), IDS)
    assert out[0]["fix"] == "اجعل المرجع ٥٦"


@pytest.mark.parametrize("verdict_field,bad", [("reference", {"v": "correct"}),
                                               ("rubric", ["sufficient"])])
def test_the_verdict_fields_are_never_normalised(verdict_field, bad):
    """الحدُّ الفاصل: ما يُبنى عليه قرارٌ يبقى مغلقًا على قيمه.

    ولولا هذا لصار التسامحُ في `fix` بابًا لقبول حكمٍ غيرِ محدَّد.
    """
    raw = _fenced(_body(_judgment(**{verdict_field: bad})))
    with pytest.raises(AutomaticReviewError):
        validate_response(raw, IDS)
