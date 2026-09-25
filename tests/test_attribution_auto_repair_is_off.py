"""الإصلاحُ الآليّ للإسناد موقوفٌ في طريق الجواب (ق٦٢، غ٦).

سببُ الإيقاف كان مقيسًا هنا: الادعاءُ الذي ينفي شاهدَه يُلحَق به رقمُ الشاهد
فيصير مسنَدًا. وأُغلق في غ٦ بموازنة القطبية (`tests/test_attribution_repair_polarity.py`)،
والإيقافُ الافتراضيّ في `MaritimeNode.answer` باقٍ بقرار ق٦٢ حتى يقرّر المالكُ إعادته.

الطفرتان اللتان يقتلهما هذا الملف: جعلُ `auto_repair=True` افتراضيًّا، وحذفُ
شرط `self.auto_repair` من موضع الاستدعاء.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.attribution import auto_repair_attribution, unsupported
from core.canonical import PayloadRejected
from nodes.maritime import node as maritime

SUPPORT = "يجب على الربان إبلاغ السلطة البحرية خلال أربع وعشرين ساعة من وقوع الحادث"
OTHER = "تسجل السفينة في ميناء التسجيل بعد استيفاء الشهادات"
PAGES_BY_REF = {1: {"text": SUPPORT, "part": "p", "locus": "l1"},
                2: {"text": OTHER, "part": "p", "locus": "l2"}}


def _page(text: str, digest: str) -> dict:
    return {"item": {"text": text, "part": "p", "locus": digest,
                     "use_internal": True, "use_distribution": True,
                     "source_id": "src"},
            "item_digest": digest}


def _node(monkeypatch, **kwargs):
    """عقدةٌ بلا مخزن ولا نموذج: الشواهدُ ثابتة، والجوابُ يُحيل إلى الشاهد الخطأ."""
    node = object.__new__(maritime.MaritimeNode)
    node.auto_repair = kwargs.get("auto_repair", _default_auto_repair())
    node.provider = SimpleNamespace(model="fake")
    monkeypatch.setattr(node, "_evidence",
                        lambda q: [_page(SUPPORT, "d1"), _page(OTHER, "d2")],
                        raising=False)
    answer = SUPPORT + " [ش2]"
    outcome = SimpleNamespace(
        response=SimpleNamespace(content=answer, stop_reason="complete"),
        error_code=None)
    monkeypatch.setattr(node, "_governed_call",
                        lambda key, messages, policy: outcome, raising=False)
    return node


def _default_auto_repair() -> bool:
    import inspect
    return inspect.signature(maritime.MaritimeNode.__init__).parameters[
        "auto_repair"].default


def _spy(monkeypatch) -> list:
    calls: list = []

    def spy(text, pages, *a, **k):
        calls.append(text)
        return auto_repair_attribution(text, pages, *a, **k)
    monkeypatch.setattr(maritime, "auto_repair_attribution", spy)
    return calls


def test_the_reason_is_closed_a_claim_that_negates_its_witness_is_no_longer_repaired():
    """كان هذا سببَ الإيقاف: الادعاءُ المنفيّ يُلحَق به رقمُ شاهده. وصار يُرفض (غ٦)،
    والإيقافُ الافتراضيّ باقٍ حتى يقرّر المالكُ إعادته."""
    negated = "لا " + SUPPORT
    repaired, bindings, changed = auto_repair_attribution(negated, PAGES_BY_REF)
    assert changed is False and repaired == negated
    assert unsupported(bindings)


def test_auto_repair_is_off_by_default():
    assert _default_auto_repair() is False


def test_the_default_node_rejects_a_weak_citation_without_repairing_it(monkeypatch):
    calls = _spy(monkeypatch)
    node = _node(monkeypatch)
    with pytest.raises(PayloadRejected) as err:
        node.answer("متى يبلغ الربان؟")
    assert calls == []
    assert "citation_overlap_low" in str(err.value)


def test_the_explicit_switch_still_reaches_the_repair(monkeypatch):
    """الشرطُ هو المفتاح لا شيفرةٌ ميتة: `auto_repair=True` يستدعي الإصلاح."""
    calls = _spy(monkeypatch)
    monkeypatch.setattr(maritime.MaritimeNode, "_derived_item",
                        lambda self, text, used: text)
    node = _node(monkeypatch, auto_repair=True)
    result = node.answer("متى يبلغ الربان؟")
    assert calls, "المفتاحُ الصريح لم يبلغ الإصلاح"
    assert result["answer"].endswith("[ش1]")
