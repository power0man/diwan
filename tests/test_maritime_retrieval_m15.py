"""اختبارات عدم تكرار لترقية سلم الاسترجاع الذكي وإلزام الاكتمال (م١٥)."""
from pathlib import Path
import pytest

from core.budget import Budget
from core.ledger import Ledger
from nodes.maritime.node import (MaritimeNode, RetrievalFailed,
                                 _extract_queries, _keywords)
from providers.echo import EchoProvider

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "corpus" / "maritime" / "_catalog.jsonl"
# المتنُ ملفاتُ المالك الخاصة (ك٢٧): اللقطةُ العامة بلا فهرس، فاختباراتُ الاسترجاع الحيّ
# تتخطّى باسمها، ومعرّفاتُ الوثائق وعناوينُها تُستمَدّ من الفهرس ولا تُكتب هنا.
needs_corpus = pytest.mark.skipif(not CATALOG.exists(),
                                  reason="corpus_missing: المتنُ خاصٌّ خارج اللقطة العامة (ك٢٧)")


def _doc(prefix: str) -> str:
    """معرّفُ الوثيقة الوحيدة في الفهرس التي تبدأ بهذا الرقم."""
    from core.corpus import CorpusCatalog
    matches = [d for d in CorpusCatalog(CATALOG, create=False).current() if d.startswith(prefix)]
    assert len(matches) == 1, matches
    return matches[0]


def _title(doc_id: str) -> str:
    return doc_id.split("__", 1)[1].replace("-", " ")


def test_extract_queries_filters_procedural_stopwords():
    q = "ما الفرق بين سفينة الصيد ووحدة الصيد في لائحة تسجيل السفن من حيث الحمولة الكلية؟"
    queries = _extract_queries(q)
    assert len(queries) >= 2
    # التأكد من استبعاد "الفرق" و"حيث" و"لائحة"
    for q_str in queries:
        assert "الفرق" not in q_str.split()
        assert "حيث" not in q_str.split()
    # التأكد من وجود استعلام الموضوع في البداية
    assert "سفينة الصيد ووحدة" in queries[0] or "سفينة الصيد وحدة" in queries[0]


def test_extract_queries_handles_waw_conjunctions():
    q = "ما تعريف وحدة النزهة والغوص وسفينة النزهة والغوص، وأين يُحظر على الأولى الإبحار؟"
    queries = _extract_queries(q)
    # التأكد من استبعاد "وأين"
    assert not any("وأين" in q_str.split() for q_str in queries)
    # التأكد من البحث عن الكيان المزدوج
    assert any("النزهة" in q_str and "الغوص" in q_str for q_str in queries)


@needs_corpus
def test_retrieval_finds_article_1_for_fishing_vessel_comparison(tmp_path):
    node = MaritimeNode(
        ROOT, EchoProvider(), Budget(100, 100),
        Ledger(tmp_path / "test_retrieval_led.jsonl")
    )
    q = f"ما الفرق بين سفينة الصيد ووحدة الصيد في {_title(_doc('002__'))} من حيث الحمولة الكلية والطول؟"
    pages = node._evidence(q)
    assert len(pages) > 0
    # التحقق من أن المادة الأولى من لائحة تسجيل السفن حاضرة في الشواهد
    loci = [p["item"]["locus"] for p in pages]
    docs = [p["doc_id"] for p in pages]
    assert _doc("002__") in docs
    assert any("المادة الأولى" in l for l in loci)


@needs_corpus
def test_retrieval_finds_article_1_for_pleasure_craft(tmp_path):
    node = MaritimeNode(
        ROOT, EchoProvider(), Budget(100, 100),
        Ledger(tmp_path / "test_retrieval_led.jsonl")
    )
    q = "ما تعريف وحدة النزهة والغوص وسفينة النزهة والغوص، وأين يُحظر على الأولى الإبحار؟"
    pages = node._evidence(q)
    assert len(pages) > 0
    docs = [p["doc_id"] for p in pages]
    loci = [p["item"]["locus"] for p in pages]
    assert _doc("002__") in docs
    assert any("المادة الأولى" in l for l in loci)


@needs_corpus
def test_retrieval_finds_bareboat_charter_definition(tmp_path):
    node = MaritimeNode(
        ROOT, EchoProvider(), Budget(100, 100),
        Ledger(tmp_path / "test_retrieval_led.jsonl")
    )
    q = "ما هو عقد إيجار سفينة غير مجهزة في النظام البحري ولائحة التسجيل؟"
    pages = node._evidence(q)
    assert len(pages) > 0
    docs = [p["doc_id"] for p in pages]
    loci = [p["item"]["locus"] for p in pages]
    assert _doc("002__") in docs
    assert any("المادة الأولى" in l for l in loci)


@needs_corpus
def test_retrieval_prioritizes_regulations_over_notes(tmp_path):
    node = MaritimeNode(
        ROOT, EchoProvider(), Budget(100, 100),
        Ledger(tmp_path / "test_retrieval_led.jsonl")
    )
    pages = node._evidence("سفينة الصيد ووحدة الصيد")
    # التأكد من أن أول شاهد ينتمي إلى لائحة أصلية وليس مستخلصات أو ملاحظات
    assert pages[0]["item"]["domain"] == "maritime-regulation"


def test_extract_queries_strips_preposition_baa():
    q = "ما المقصود بسفينة الركاب في اللائحة؟"
    queries = _extract_queries(q)
    assert any("سفينة" in q_str.split() for q_str in queries)


def test_extract_queries_generates_adjacent_bigrams():
    q = "ما هي النسبة المحددة لأبعاد علم المملكة وشكله ولونه؟"
    queries = _extract_queries(q)
    assert any("علم المملكة" in q_str for q_str in queries)


@needs_corpus
def test_retrieval_finds_saudi_flag_definition(tmp_path):
    node = MaritimeNode(
        ROOT, EchoProvider(), Budget(100, 100),
        Ledger(tmp_path / "test_retrieval_led.jsonl")
    )
    q = "ما هي النسبة المحددة لأبعاد علم المملكة وشكله ولونه وموضعه على السفن وفق اللائحة؟"
    pages = node._evidence(q)
    assert len(pages) > 0
    docs = [p["doc_id"] for p in pages]
    loci = [p["item"]["locus"] for p in pages]
    assert _doc("002__") in docs
    assert any("المادة الأولى" in l for l in loci)


@needs_corpus
def test_retrieval_finds_offshore_platform(tmp_path):
    node = MaritimeNode(
        ROOT, EchoProvider(), Budget(100, 100),
        Ledger(tmp_path / "test_retrieval_led.jsonl")
    )
    q = "ما هي المنصة البحرية وهل تعد في حكم السفينة في لائحة تسجيل السفن؟"
    pages = node._evidence(q)
    assert len(pages) > 0
    docs = [p["doc_id"] for p in pages]
    loci = [p["item"]["locus"] for p in pages]
    assert _doc("002__") in docs
    assert any("المادة الأولى" in l for l in loci)


@needs_corpus
def test_retrieval_finds_passenger_ship_solas(tmp_path):
    node = MaritimeNode(
        ROOT, EchoProvider(), Budget(100, 100),
        Ledger(tmp_path / "test_retrieval_led.jsonl")
    )
    q = "ما المقصود بسفينة الركاب في اللائحة التنفيذية لمعاهدة سولاس 1974؟"
    pages = node._evidence(q)
    assert len(pages) > 0
    docs = [p["doc_id"] for p in pages]
    loci = [p["item"]["locus"] for p in pages]
    assert _doc("001__") in docs
    assert any("المادة (1)" in l for l in loci)


@needs_corpus
def test_retrieval_finds_short_international_voyage(tmp_path):
    node = MaritimeNode(
        ROOT, EchoProvider(), Budget(100, 100),
        Ledger(tmp_path / "test_retrieval_led.jsonl")
    )
    q = "ما هو تعريف الرحلة الدولية القصيرة وما هي المسافات المحددة لها بالأميال البحرية؟"
    pages = node._evidence(q)
    assert len(pages) > 0
    docs = [p["doc_id"] for p in pages]
    loci = [p["item"]["locus"] for p in pages]
    assert _doc("001__") in docs
    assert any("المادة (1)" in l for l in loci)


def test_extract_queries_juridical_expansion():
    q23 = "ما هي المادة المانعة لالتصاق الشوائب المحظورة دولياً بموجب اتفاقية AFS ولائحتها التنفيذية؟"
    queries23 = _extract_queries(q23)
    assert any("يحظر" in q_str for q_str in queries23)
    assert any("المقاومة" in q_str for q_str in queries23)

    q25 = "ما هي عقوبة مزاولة أعمال النقل البحري دون الحصول على الترخيص الملاحي وفق جدول المخالفات؟"
    queries25 = _extract_queries(q25)
    assert any("المخالفة الجسيمة" in q_str or "يعاقب" in q_str for q_str in queries25)


@needs_corpus
def test_retrieval_finds_afs_prohibited_substances(tmp_path):
    node = MaritimeNode(
        ROOT, EchoProvider(), Budget(100, 100),
        Ledger(tmp_path / "test_retrieval_led.jsonl")
    )
    q = "ما هي المادة المانعة لالتصاق الشوائب المحظورة دولياً بموجب اتفاقية AFS ولائحتها التنفيذية؟"
    pages = node._evidence(q)
    assert len(pages) > 0
    docs = [p["doc_id"] for p in pages]
    loci = [p["item"]["locus"] for p in pages]
    assert _doc("019__") in docs
    assert any("المادة الخامسة" in l for l in loci)


@needs_corpus
def test_retrieval_finds_unlicensed_transport_violation(tmp_path):
    node = MaritimeNode(
        ROOT, EchoProvider(), Budget(100, 100),
        Ledger(tmp_path / "test_retrieval_led.jsonl")
    )
    q = "ما هي عقوبة مزاولة أعمال النقل البحري دون الحصول على الترخيص الملاحي وفق جدول المخالفات؟"
    pages = node._evidence(q)
    assert len(pages) > 0
    docs = [p["doc_id"] for p in pages]
    assert _doc("018__") in docs





# ————— عطبُ البحث ليس غيابَ معرفة —————
# كان `_search_safe` يبتلع كلَّ استثناءٍ مرتين ويعود []، فيُقرأ العطبُ
# «لا شواهد في المخزن» فيمتنع النظام — فتُحسب الخليةُ امتناعًا حكيمًا وهي
# عطبٌ صامت. وهو فشلٌ مفتوحٌ في قلب المقياس الذي تقوم عليه أرقام م١٤.

class _IndexBroken(Exception):
    """يحاكي عطبَ SQLite: لا يرث LookupError ولا TypeError."""


def _node_with_search(search_fn, top_k=7):
    node = MaritimeNode.__new__(MaritimeNode)
    node.search = search_fn
    node.top_k = top_k
    return node


def test_search_failure_raises_instead_of_returning_no_evidence():
    def broken(q, limit=10, match_any=False):
        raise _IndexBroken("database disk image is malformed")
    with pytest.raises(RetrievalFailed) as exc:
        _node_with_search(broken)._search("سفينة الصيد", limit=50)
    assert "_IndexBroken" in str(exc.value)
    assert "malformed" in str(exc.value)


def test_search_failure_on_the_compatibility_fallback_also_raises():
    """دالّةُ بحثٍ لا تعرف match_any ثم تنكسر: لا تُبتلع هي أيضًا."""
    calls = []

    def legacy_then_broken(q, limit=10, **kw):
        if kw:
            raise TypeError("unexpected keyword 'match_any'")
        calls.append(q)
        raise _IndexBroken("no such table: pages")
    with pytest.raises(RetrievalFailed):
        _node_with_search(legacy_then_broken)._search("سفينة", limit=10, match_any=True)
    assert calls, "كان يجب أن يُعاد النداءُ بالتوقيع القديم قبل الرفع"


def test_retrieval_failure_is_not_swallowed_as_absent_knowledge():
    """`services/research.py` يبتلع LookupError بوصفه غيابَ معرفة.

    فلو ورثه RetrievalFailed لعاد العطبُ يُقرأ امتناعًا — وهذا ما يجعل
    عدمَ الوراثة حاملًا لا تجميلًا.
    """
    assert not issubclass(RetrievalFailed, LookupError)
    assert issubclass(RetrievalFailed, RuntimeError)


def test_type_error_alone_is_still_treated_as_a_signature_mismatch():
    """التوافقُ مع دالّةِ بحثٍ لا تعرف match_any يبقى عاملًا."""
    def legacy(q, limit=10):
        return [{"item_digest": "d1"}]
    assert _node_with_search(legacy)._search("سفينة", limit=5, match_any=True) == [
        {"item_digest": "d1"}]


def test_a_healthy_empty_result_is_still_empty_not_an_error():
    """«لا نتائج» الصحيحةُ تبقى لا نتائج — لا يُحوّلها الإصلاح إلى عطب."""
    assert _node_with_search(lambda q, limit=10, match_any=False: [])._search(
        "كلمةٌ لا توجد", limit=5) == []
