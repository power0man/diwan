"""إخفاقاتُ العقدتين رموزٌ مسمّاة لا أسماءُ استثناءاتٍ خام (ك٣٧، #39).

في إعادة قياس م١٤ (ك٣٢) سجّل ذراعُ «ديوان كاملًا» عطبَين بالرمز `ValueError`:
جوابٌ مبتور عند `max_output` استنفد محاولاته، فرفعت العقدة `ValueError` بلا رمز،
وقرأ الذراعُ اسمَ الصنف رمزًا. فصار كلُّ إخفاقٍ تخرج به العقدتان يحمل `.code`:
- المبتور `answer_truncated`، والخالي من الإحالة `citation_missing`.
- الإعادةُ المستنفدة `provider_retry_exhausted`، والمزوّدُ الفاشل برمزه.
- الشواهدُ بلا حقٍّ مشترك `evidence_rights_disjoint`.
- وفي التعريب `translation_truncated` و`glossary_terms_missing`.

والأصنافُ باقيةٌ كما كانت (`PayloadRejected` ترث `ValueError`، و`ProviderFailed`
ترث `RuntimeError`)، فلا يتغيّر شيءٌ عند من يلتقطها.
"""
from __future__ import annotations

import pytest

from core.budget import Budget
from core.canonical import PayloadRejected
from core.contracts import Response, Usage
from core.ledger import Ledger
from core.run import ProviderFailed
from evaluation.benchmark_arms import DiwanFullArm
from providers.base import ProviderError
from tests.test_node import ScriptedProvider, _mini_root, _node


class Truncating(ScriptedProvider):
    def complete(self, request):
        return Response(self._content, Usage(10, 5), "max_output", 0,
                        provider=self.name, model_version="1")


def _truncating_node(tmp_path):
    root, dg = _mini_root(tmp_path)
    node = _node(root, "تلتزم السفينة بشهادة [ش1", dg)
    node.provider = Truncating("تلتزم السفينة بشهادة")
    return node


def test_a_truncated_answer_is_answer_truncated(tmp_path):
    node = _truncating_node(tmp_path)
    with pytest.raises(PayloadRejected) as err:
        node.answer("ما شهادة الصابورة؟")
    assert (err.value.path, err.value.code) == ("answer.output", "answer_truncated")
    assert isinstance(err.value, ValueError)


def test_an_uncited_answer_is_citation_missing(tmp_path):
    root, dg = _mini_root(tmp_path)
    node = _node(root, "جواب مرسل بلا أي استشهاد.", dg)
    with pytest.raises(PayloadRejected) as err:
        node.answer("ما شهادة الصابورة؟")
    assert (err.value.path, err.value.code) == ("answer.citations", "citation_missing")


def test_retries_exhausted_on_a_flaky_provider_are_named(tmp_path):
    root, dg = _mini_root(tmp_path)
    node = _node(root, "لن يصل.", dg)

    def flaky(request):
        raise ProviderError("unreachable", "قطع مؤقت", retryable=True)
    node.provider.complete = flaky
    with pytest.raises(PayloadRejected) as err:
        node.answer("ما شهادة الصابورة؟")
    assert (err.value.path, err.value.code) == ("answer.provider", "provider_retry_exhausted")


def test_a_dead_provider_keeps_its_own_code(tmp_path):
    root, dg = _mini_root(tmp_path)
    node = _node(root, "لن يصل.", dg)

    def dead(request):
        raise ProviderError("timeout", "غير مؤكد", retryable=False)
    node.provider.complete = dead
    with pytest.raises(ProviderFailed) as err:
        node.answer("ما شهادة الصابورة؟")
    assert err.value.code == "timeout"
    assert isinstance(err.value, RuntimeError)


def test_evidence_without_shared_rights_is_named(tmp_path):
    root, dg = _mini_root(tmp_path)
    node = _node(root, "لن يصل.", dg)
    used = [{"item": {"use_internal": False, "use_distribution": False}}]
    with pytest.raises(PayloadRejected) as err:
        node._derived_item("نص", used)
    assert err.value.code == "evidence_rights_disjoint"


def test_the_full_diwan_arm_records_the_named_code_not_the_class(tmp_path):
    """الموضعُ الذي كشف العطب: الذراعُ يقرأ `.code`، فلا يبقى `ValueError` رمزًا."""
    arm = object.__new__(DiwanFullArm)
    arm.maritime_node = _truncating_node(tmp_path)
    result = arm.run("ما شهادة الصابورة؟", {"domain": "maritime"})
    assert result["error_code"] == "answer_truncated"
    assert result["abstained"] is False


# — عقدة اللسانيات: التعريب —

def _linguistics(tmp_path, provider):
    from core.acquisitions import Acquisition, SourceRegister
    from core.glossary import Glossary
    from nodes.linguistics.node import LinguisticsNode
    reg = SourceRegister(tmp_path / "sources" / "acquisitions.jsonl")
    reg.acquire(Acquisition("src", "ت", "https://x", "2026-09-20",
                            True, False, "دليل"))
    return LinguisticsNode(tmp_path, provider, Budget(1000, 10000),
                           Ledger(tmp_path / "ling-ledger.jsonl"),
                           search_fn=lambda *a, **k: [],
                           glossary=Glossary(tmp_path / "glossary.jsonl"))


def test_a_truncated_translation_is_translation_truncated(tmp_path):
    node = _linguistics(tmp_path, Truncating("ترجمة ناقصة"))
    with pytest.raises(PayloadRejected) as err:
        node.translate("The ship shall carry a certificate.", "src", "l1")
    assert (err.value.path, err.value.code) == ("translate.output", "translation_truncated")


def test_a_dead_provider_in_translation_keeps_its_own_code(tmp_path):
    provider = ScriptedProvider("لن يصل")

    def dead(request):
        raise ProviderError("timeout", "غير مؤكد", retryable=False)
    provider.complete = dead
    node = _linguistics(tmp_path, provider)
    with pytest.raises(ProviderFailed) as err:
        node.translate("The ship shall carry a certificate.", "src", "l1")
    assert err.value.code == "timeout"


def test_empty_text_to_translate_is_named(tmp_path):
    node = _linguistics(tmp_path, ScriptedProvider("x"))
    with pytest.raises(PayloadRejected) as err:
        node.translate("   ", "src", "l1")
    assert err.value.code == "text_empty"
