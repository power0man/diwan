"""Adversarial regression: a cited clause never licenses uncited content."""
from __future__ import annotations

import pytest

from core.attribution import bind_claims, unsupported
from core.canonical import PayloadRejected
from core.ledger import Ledger
from services.research import ResearchService
from tests.test_node import _mini_root as node_root, _node
from tests.test_m6 import (_mini_root as service_root, _service,
                          SequencedProvider, SCRIPT)

PAGE = {"text": "تلتزم السفينة بشهادة إدارة مياه الصابورة سارية المفعول.",
        "part": "لائحة الصابورة", "locus": "المادة (5)"}
GOOD = "تلتزم السفينة بشهادة إدارة مياه الصابورة [ش1]"
BAD = GOOD + ". الغرامة مليون ريال. تسحب الرخصة خمس سنوات ويحال إلى النيابة."


def test_every_uncited_sentence_has_explicit_binding_and_refusal():
    bindings = bind_claims(BAD, {1: PAGE})
    assert len(bindings) == 3
    assert [b.refs for b in bindings] == [(1,), (), ()]
    assert [code for _, code, _ in unsupported(bindings)] == [
        "citation_missing", "citation_missing"]


@pytest.mark.parametrize("separator", [". ", "؟ ", "! ", "; ", "؛ ", "\n", "\r", "\u2028", "\u2029"])
def test_short_uncited_segment_cannot_borrow_previous_citation(separator):
    bad = unsupported(bind_claims(GOOD + separator + "الغرامة مليون ريال", {1: PAGE}))
    assert any(code == "citation_missing" for _, code, _ in bad)


def test_content_after_citation_without_sentence_delimiter_is_not_covered():
    bad = unsupported(bind_claims(GOOD + " وتسحب الرخصة خمس سنوات", {1: PAGE}))
    assert any(code == "citation_missing" for _, code, _ in bad)


def test_uncited_heading_and_preamble_do_not_get_heuristic_exemptions():
    for prefix in ["النتيجة", "# الغرامة مليون ريال", "**تمهيد**", "بلا قيد"]:
        bad = unsupported(bind_claims(prefix + "\n" + GOOD, {1: PAGE}))
        assert any(code == "citation_missing" for _, code, _ in bad)


def test_maritime_never_delivers_uncited_tail_as_derived_item(tmp_path):
    root, dg = node_root(tmp_path)
    node = _node(root, BAD, dg)
    with pytest.raises(PayloadRejected) as exc:
        node.answer("ما شهادة الصابورة؟")
    assert exc.value.code == "citation_missing"
    assert len(node.ledger.entries()) == 3
    assert node.budget.outstanding_micros == 0


def test_research_cannot_deliver_short_uncited_tail(tmp_path):
    root, dg, _ = service_root(tmp_path)
    bad = SCRIPT[2] + " الغرامة مليون ريال."
    provider = SequencedProvider(SCRIPT[:2] + [bad, bad, bad])
    ledger = Ledger(root / "research.jsonl")
    with pytest.raises(ValueError):
        _service(root, dg, provider, ledger).run("ما التزامات الصابورة ومعناها؟")
    assert provider.calls == 5
    assert not any(e["record"]["kind"] == "service_bill" for e in ledger.entries())


def test_research_without_material_payloads_still_checks_each_claim():
    service = object.__new__(ResearchService)
    ok, _ = service._check_brief(2)("شهادة [م1]. صبر [م2]. الغرامة مليون ريال.")
    assert not ok


@pytest.mark.parametrize("ref", ["[ش1] [ش2]", "[ش١] [ش٢]", "[ش۱] [ش２]"])
def test_adjacent_references_share_claim_without_becoming_empty_claims(ref):
    bindings = bind_claims("تلتزم السفينة بشهادة إدارة مياه الصابورة " + ref,
                           {1: PAGE, 2: PAGE})
    assert len(bindings) == 1 and bindings[0].refs == (1, 2)
    assert not unsupported(bindings)


def test_each_inline_cited_clause_has_its_own_evidence():
    other = {"text": "الغرامة مليون ريال", "part": "جدول الغرامات", "locus": "1"}
    bindings = bind_claims(GOOD + " والغرامة مليون ريال [ش2]", {1: PAGE, 2: other})
    assert [b.refs for b in bindings] == [(1,), (2,)]
    assert not unsupported(bindings)
    assert "الصابورة" in bindings[0].excerpt
    assert "مليون" in bindings[1].excerpt


def test_decimal_number_is_not_split_into_uncited_fragments():
    page = {"text": "الحمولة 1.5 طن", "part": "شهادة", "locus": "1"}
    bindings = bind_claims("الحمولة 1.5 طن [ش1].", {1: page})
    assert len(bindings) == 1 and not unsupported(bindings)


@pytest.mark.parametrize("bad_ref", ["[مغلط]", "[م-1]", "[م1.5]", "[م3"])
def test_research_rejects_invalid_reference_next_to_good_refs(bad_ref):
    service = object.__new__(ResearchService)
    ok, _ = service._check_brief(2)(f"شهادة [م1]. صبر {bad_ref} [م2].")
    assert not ok


def test_maritime_corrects_then_replays_without_new_calls(tmp_path):
    from core.contracts import Response, Usage
    root, dg = node_root(tmp_path)
    node = _node(root, GOOD + ".", dg)
    calls = []

    def respond(request):
        calls.append(request)
        return Response(BAD if len(calls) == 1 else GOOD + ".", Usage(10, 5),
                        "complete", 0, provider="scripted", model_version="1")

    node.provider.complete = respond
    result = node.answer("ما شهادة الصابورة؟")
    assert len(calls) == 2 and result["answer"].text == GOOD + "."
    assert calls[1].idempotency_key.endswith("-attribution-v2-a2")
    assert "بلا إحالة" in calls[1].messages[-1].content
    assert len(result["bindings"]) == 1
    repeated = node.answer("ما شهادة الصابورة؟")
    assert repeated["outcome"].replayed and len(calls) == 2
    assert repeated["answer"] == result["answer"]


def test_maritime_rechecks_previously_accepted_unsafe_answer(tmp_path, monkeypatch):
    import sys
    root, dg = node_root(tmp_path)
    node = _node(root, BAD, dg)
    module = sys.modules[type(node).__module__]
    original_bind = module.bind_claims
    with monkeypatch.context() as legacy:
        # Simulate the old gate accepting and recording the original call.
        legacy.setattr(module, "bind_claims", lambda *a, **k: [
            b for b in original_bind(*a, **k) if b.refs])
        old = node.answer("ما شهادة الصابورة؟")
    assert old["answer"].text == BAD and len(node.ledger.entries()) == 1
    first_entry = node.ledger.entries()[0]
    node.provider._content = GOOD + "."
    current = node.answer("ما شهادة الصابورة؟")
    assert current["answer"].text == GOOD + "."
    assert len(node.ledger.entries()) == 2
    assert node.ledger.entries()[0] == first_entry  # لا تعديل للتاريخ
    node.provider.complete = lambda _: (_ for _ in ()).throw(AssertionError("live call"))
    repeated = node.answer("ما شهادة الصابورة؟")
    assert repeated["outcome"].replayed and repeated["answer"] == current["answer"]


def test_research_corrects_then_replays_without_new_calls(tmp_path):
    root, dg, _ = service_root(tmp_path)
    bad = SCRIPT[2] + " الغرامة مليون ريال."
    provider = SequencedProvider(SCRIPT[:2] + [bad, SCRIPT[2]])
    ledger = Ledger(root / "research.jsonl")
    current = _service(root, dg, provider, ledger).run("ما التزامات الصابورة ومعناها؟")
    assert provider.calls == 4 and current["brief"] == SCRIPT[2]
    assert any(e["record"].get("idempotency_key", "").endswith("-attribution-v2-a2")
               for e in ledger.entries())
    records = ledger.entries()
    provider2 = SequencedProvider([])
    repeated = _service(root, dg, provider2, ledger).run("ما التزامات الصابورة ومعناها؟")
    assert repeated["brief"] == current["brief"] and provider2.calls == 0
    assert ledger.entries() == records


def test_research_rechecks_previously_accepted_unsafe_answer(tmp_path, monkeypatch):
    root, dg, _ = service_root(tmp_path)
    bad = SCRIPT[2] + " الغرامة مليون ريال."
    ledger = Ledger(root / "research.jsonl")
    with monkeypatch.context() as legacy:
        legacy.setattr(ResearchService, "_check_brief", lambda *_: lambda text: (True, text))
        _service(root, dg, SequencedProvider(SCRIPT[:2] + [bad]), ledger).run(
            "ما التزامات الصابورة ومعناها؟")
    old_records = ledger.entries()
    provider = SequencedProvider([SCRIPT[2]])
    current = _service(root, dg, provider, ledger).run("ما التزامات الصابورة ومعناها؟")
    assert provider.calls == 1 and current["brief"] == SCRIPT[2]
    assert ledger.entries()[:len(old_records)] == old_records
    provider2 = SequencedProvider([])
    repeated = _service(root, dg, provider2, ledger).run("ما التزامات الصابورة ومعناها؟")
    assert repeated["brief"] == SCRIPT[2] and provider2.calls == 0


@pytest.mark.parametrize('separator', ['۔', '。', '．', '｡', '።'])
def test_full_stop_variants_cannot_hide_uncited_leading_claim(separator):
    bad = unsupported(bind_claims('الغرامة مليون ريال' + separator + ' ' + GOOD, {1: PAGE}))
    assert any(code == 'citation_missing' for _, code, _ in bad)


@pytest.mark.parametrize('claim,source', [
    ('5.1', '1.5'), ('٥٫١', '١٫٥'), ('5٫1', '١.٥'),
    ('1.6', '1 و6'), ('١٫٦', '١ و٦'), ('1.5', '15'),
])
def test_decimal_number_is_one_supported_value_not_independent_digits(claim, source):
    page = {'text': f'الحمولة {source} طن', 'part': 'شهادة', 'locus': 'المادة (5)'}
    bad = unsupported(bind_claims(f'الحمولة {claim} طن [ش1]', {1: page}))
    assert any(code == 'citation_number_unsupported' for _, code, _ in bad)


@pytest.mark.parametrize('claim,source', [('1.5', '١٫٥'), ('١٫٥', '1.5'), ('١.٥', '١٫٥')])
def test_equivalent_arabic_and_ascii_decimal_notation_is_supported(claim, source):
    page = {'text': f'الحمولة {source} طن', 'part': 'شهادة', 'locus': '1'}
    assert not unsupported(bind_claims(f'الحمولة {claim} طن [ش1]', {1: page}))


@pytest.mark.parametrize('separator', ['\v', '\f', '\x1c', '\x1d', '\x1e', '\x85', '\r\n'])
def test_every_python_line_boundary_requires_local_citation(separator):
    page = {'text': 'يجب تسجيل السفينة قبل المغادرة والغرامة 5 ريال', 'part': 'نص', 'locus': '1'}
    text = 'يجب تسجيل السفينة قبل المغادرة' + separator + 'تسحب الرخصة دائمًا [ش1]'
    assert any(code == 'citation_missing' for _, code, _ in unsupported(bind_claims(text, {1: page})))


@pytest.mark.parametrize('claim,source', [
    ('-5', '5'), ('−5', '+5'), ('﹣5', '5'), ('－5', '5'), ('- 5', '+5'),
    ('−\u200e5', '5'), ('-٥٫١', '٥٫١'), ('5', '-5'), ('+5', '-5'),
    ('.5', '5'), ('−.5', '.5'), ('١/٥', '٥/١'), ('1 / 5', '5 و1'),
    ('1e5', '1 و5'), ('1e-5', '1e5'), ('1,00', '100'), ('1..5', '1 و5'),
    ('-½', '½'), ('-۵', '+۵'), ('±5', '5'),
])
def test_numeric_representation_never_borrows_separate_digits_or_loses_sign(claim, source):
    page = {'text': f'الحمولة {source} طن', 'part': 'شهادة', 'locus': 'المادة (9)'}
    bad = unsupported(bind_claims(f'الحمولة {claim} طن [ش1]', {1: page}))
    assert any(code == 'citation_number_unsupported' for _, code, _ in bad)


@pytest.mark.parametrize('claim,source', [
    ('−5', '-5'), ('- 5', '−٥'), ('＋٥', '5'), ('۵.۱', '٥٫١'), ('５.１', '5.1'),
    ('1,000.5', '١٬٠٠٠٫٥'), ('.5', '0.5'), ('٫٥', '.5'),
    ('1 / 5', '1/5'), ('-½', '-½'), ('1e+5', '1E05'),
])
def test_known_numeric_notations_remain_compatible_without_arithmetic(claim, source):
    page = {'text': f'الحمولة {source} طن', 'part': 'شهادة', 'locus': 'المادة (9)'}
    assert not unsupported(bind_claims(f'الحمولة {claim} طن [ش1]', {1: page}))
