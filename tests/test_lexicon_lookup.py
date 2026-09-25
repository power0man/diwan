"""استرجاع معجمي فوق FTS حقيقي صغير، بلا نموذج أو بيانات المالك."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from core.acquisitions import Acquisition, SourceRegister
from core.canonical import PayloadRejected
from core.corpus import CorpusCatalog, CorpusFile
from core.knowledge import KnowledgeItem
import rebuild_index as ri
from run_node import load_node_module


@pytest.fixture
def lexicon(tmp_path, monkeypatch):
    """يبني مخازن وFTS حقيقيين بعقود المشروع في جذر مؤقت."""
    monkeypatch.setattr(ri, "ROOT", tmp_path)
    register = SourceRegister(tmp_path / "sources" / "acquisitions.jsonl")
    register.acquire(Acquisition(
        "test-lexicon", "معجم اختباري", "https://example.test/lexicon",
        "2026-09-20", True, False, "نصوص اختبار مصطنعة"))

    def build(stores):
        for store, texts in stores.items():
            directory = tmp_path / "corpus" / store
            directory.mkdir(parents=True)
            corpus = CorpusFile(directory / "test.jsonl")
            items = [KnowledgeItem(
                text=text, lang="ar", domain="lexicon",
                use_internal=True, use_distribution=False,
                source_id="test-lexicon", locus=f"ص{i}",
                originality="original", part=store)
                for i, text in enumerate(texts, 1)]
            head = corpus.ingest("test", items, register)
            corpus.anchor()
            catalog = CorpusCatalog(directory / "_catalog.jsonl")
            catalog.record("test", str(corpus.path.relative_to(tmp_path)),
                           len(items), head)
            monkeypatch.setitem(
                ri.CORPORA, store,
                (catalog._ledger.path,
                 tmp_path / "projections" / f"{store}-fts.sqlite"))
            ri.rebuild(store)
        return load_node_module("linguistics").LinguisticsNode(tmp_path)

    return build


@pytest.mark.parametrize("word", ["صابورة", "صَابُورَة"])
def test_bare_word_finds_article_and_conjunction_in_real_fts(lexicon, word):
    node = lexicon({
        "lexicons": ["(الصابورة) ثقل يوضع في السفينة."],
        "lexicons-local": ["والصّابُورَةُ: ثقل في المركب."],
    })
    # لغم التشغيلة الحية: FTS الدقيق لا يصل من المجرد إلى المعرف.
    assert ri.search(word, corpus="lexicons") == []
    assert ri.search(word, corpus="lexicons-local") == []
    pages = node.lookup(word)
    assert {p["store"] for p in pages} == {"lexicons", "lexicons-local"}
    assert len(pages) == 2
    assert all(p["item"]["locus"] == "ص1" for p in pages)


def test_exact_first_per_store_and_definite_word_fallback(lexicon):
    node = lexicon({
        "lexicons": ["الصابورة تعريف مباشر.", "والصابورة تعريف إضافي."],
        "lexicons-local": ["والصابورة تعريف في معجم آخر."],
    })
    calls = []

    def search(word, **kwargs):
        calls.append((kwargs["corpus"], word))
        return ri.search(word, **kwargs)

    node.search = search
    pages = node.lookup("الصابورة")
    assert len(pages) == 2
    assert [word for store, word in calls if store == "lexicons"] \
        == ["الصابورة"]
    assert next(p for p in pages if p["store"] == "lexicons-local") \
        ["item"]["locus"] == "ص1"


@pytest.mark.parametrize("word,target,unrelated", [
    ("التقاء", "الالتقاء", "تقاء"),
    ("ألف", "والألف", "ف"),
])
def test_original_initial_alef_lam_is_never_stripped(
        lexicon, word, target, unrelated):
    node = lexicon({"lexicons": [f"{target} تعريف مقصود.",
                                 f"{unrelated} تعريف آخر."]})
    pages = node.lookup(word)
    assert [p["item"]["locus"] for p in pages] == ["ص1"]


@pytest.mark.parametrize("word", ["صبر", "التقاء", "ألف"])
def test_exact_hit_does_not_expand(lexicon, word):
    node = lexicon({"lexicons": [f"{word} مدخل مباشر.",
                                 f"وال{word} مدخل آخر."]})
    calls = []

    def search(query, **kwargs):
        calls.append(query)
        return ri.search(query, **kwargs)

    node.search = search
    assert len(node.lookup(word)) == 1
    assert calls == [word]


@pytest.mark.parametrize("word", ["مياه صابورة", "ballast", "صابورة؟", "ص"])
def test_no_fallback_for_phrases_non_arabic_or_single_letter(lexicon, word):
    node = lexicon({"lexicons": ["نص معجمي آخر."]})
    calls = []

    def search(query, **kwargs):
        calls.append(query)
        return ri.search(query, **kwargs)

    node.search = search
    with pytest.raises(LookupError):
        node.lookup(word)
    assert calls == [word]


def test_fallback_deduplicates_and_respects_limit_per_store(lexicon):
    node = lexicon({"lexicons": [
        "الصابورة والصابورة تعريف واحد.",
        "والصابورة تعريف ثان.",
        "فالصابورة تعريف ثالث.",
    ]})
    pages = node.lookup("صابورة", limit=2)
    assert len(pages) == 2
    assert len({p["item_digest"] for p in pages}) == 2
    assert {p["item"]["locus"] for p in pages} == {"ص1", "ص2"}
    assert len(node.lookup("صابورة", limit=1)) == 1


def test_missing_index_remains_named_failure_without_fallback(lexicon):
    node = lexicon({"lexicons": ["الصابورة تعريف."]})
    ri.CORPORA["lexicons"][1].unlink()
    calls = []

    def search(query, **kwargs):
        calls.append(query)
        return ri.search(query, **kwargs)

    node.search = search
    with pytest.raises(PayloadRejected) as exc:
        node.lookup("صابورة")
    assert exc.value.code == "index_missing"
    assert calls == ["صابورة"]
    assert not ri.CORPORA["lexicons"][1].exists()
