"""استرجاعٌ بالمتجهات يُدمج مع BM25 (غ٢): المُضمِّنُ مسمًّى، والفهرسُ يحمل هويّته، والدمجُ يقول قنواته.

بلا نموذجٍ هنا: المُضمِّنُ الحتميّ (تجزئةُ ثلاثيّات الحروف، وليس دلاليًّا) يُثبت مسارَ
الفهرسة والبحث والدمج؛ ومُضمِّنُ Ollama يُثبت عقدُه بفاتحٍ مصطنع. والرقمُ الدلاليُّ
يُقاس على الماك بنموذجٍ حيّ لا هنا.
"""
from __future__ import annotations

from contextlib import contextmanager
import io
import json
import math

import pytest

from core import hybrid_retrieval
from core.canonical import PayloadRejected
from core.hybrid_retrieval import HybridRetriever
from core.vector_retrieval import (HashEmbedder, OllamaEmbedder, VectorIndex, file_passages, passage)

TEXTS = {
    "reg:m1": "المادة الأولى: مياه الصابورة هي المياه المحملة في السفينة لضبط الاتزان والاستقرار الملاحي",
    "reg:m2": "المادة الثانية: تفرض غرامة مالية على كل سفينة تخالف شروط السلامة",
    "reg:m3": "المادة الثالثة: تخضع المنشآت البحرية للتفتيش المفاجئ من قبل مفتشي الهيئة",
}


def passages():
    return [passage(pid, "reg", pid.split(":")[1], "لائحة", text) for pid, text in TEXTS.items()]


@pytest.fixture
def index(tmp_path):
    VectorIndex.build(tmp_path / "vec.sqlite", passages(), HashEmbedder(128))
    return VectorIndex(tmp_path / "vec.sqlite")


# ————— المُضمِّنُ الحتميّ —————

def test_the_hash_embedder_is_deterministic_unit_length_and_declares_itself_non_semantic():
    embedder = HashEmbedder(64)
    [a], [b] = embedder.embed(["مياه الصابورة"]), embedder.embed(["مياه الصابورة"])
    assert a == b and len(a) == 64 and math.isclose(sum(x * x for x in a), 1.0, abs_tol=1e-5)
    assert embedder.identity() == {"embedder": "hash-trigram", "dim": 64, "semantic": False}
    assert embedder.embed([""]) == [[0.0] * 64]


def test_normalized_spellings_share_a_vector():
    embedder = HashEmbedder(64)
    assert embedder.embed(["السفينة"]) == embedder.embed(["السفينه"]) == embedder.embed(["السَّفِينَة"])


# ————— الفهرس —————

def test_the_index_records_its_embedder_and_finds_the_nearest_passage_first(index):
    assert index.count() == 3 and index.identity()["embedder"] == "hash-trigram"
    hits = index.search("الصابورة والاتزان", HashEmbedder(128), limit=2)
    assert [h["passage_id"] for h in hits] == ["reg:m1", "reg:m2"] or hits[0]["passage_id"] == "reg:m1"
    assert hits[0]["similarity"] > hits[1]["similarity"] > 0
    assert set(hits[0]) == {"passage_id", "source", "locus", "part", "item_digest", "similarity"}


def test_an_index_refuses_a_different_embedder(index):
    with pytest.raises(PayloadRejected) as exc:
        index.search("مياه", HashEmbedder(64))
    assert exc.value.code == "embedder_identity_mismatch"


def test_an_existing_index_is_not_overwritten_silently(tmp_path):
    VectorIndex.build(tmp_path / "vec.sqlite", passages(), HashEmbedder(32))
    with pytest.raises(PayloadRejected) as exc:
        VectorIndex.build(tmp_path / "vec.sqlite", passages(), HashEmbedder(32))
    assert exc.value.code == "vector_index_exists"
    assert VectorIndex.build(tmp_path / "vec.sqlite", passages()[:1], HashEmbedder(32), replace=True)["count"] == 1


@pytest.mark.parametrize("mutate,code", [
    (lambda p: p.unlink(), "vector_index_missing"),
    (lambda p: p.write_bytes(b"not a database"), "vector_index_corrupt"),
])
def test_a_missing_or_corrupt_index_is_a_named_refusal(tmp_path, mutate, code):
    path = tmp_path / "vec.sqlite"
    VectorIndex.build(path, passages(), HashEmbedder(32))
    mutate(path)
    with pytest.raises(PayloadRejected) as exc:
        VectorIndex(path).search("x", HashEmbedder(32))
    assert exc.value.code == code


def test_empty_passages_are_refused(tmp_path):
    with pytest.raises(PayloadRejected) as exc:
        VectorIndex.build(tmp_path / "vec.sqlite", [], HashEmbedder(32))
    assert exc.value.code == "passages_empty"


# ————— ملفّاتُ المالك —————

def test_file_passages_chunk_text_files_and_skip_hidden_binary_and_links(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes/a.md").write_text("فقرة أولى.\n\nفقرة ثانية.\n\n" + "س" * 1500, encoding="utf-8")
    (tmp_path / ".secret.txt").write_text("مخفي", encoding="utf-8")
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00")
    (tmp_path / "link.md").symlink_to(tmp_path / "notes/a.md")
    found = file_passages(tmp_path, chunk_chars=1200)
    assert [p.source for p in found] == ["notes/a.md"] * 3, "الترتيبُ ترتيبُ الملف: القصيرُ ثم الطويلُ مقصوصًا"
    assert found[0].text == "فقرة أولى.\nفقرة ثانية." and found[0].passage_id == "notes/a.md#0"
    assert len(found[1].text) == 1200 and found[1].locus == "chunk-1"
    assert len(found[2].text) == 300 and found[2].passage_id == "notes/a.md#2"


# ————— مُضمِّنُ Ollama: العقدُ بفاتحٍ مصطنع —————

class FakeOpener:
    def __init__(self, body):
        self.body, self.requests = body, []

    @contextmanager
    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        yield io.BytesIO(self.body)


def test_the_ollama_embedder_posts_the_named_model_and_returns_one_vector_per_text():
    opener = FakeOpener(json.dumps({"embeddings": [[0.1, 0.2], [0.3, 0.4]]}).encode())
    embedder = OllamaEmbedder("qwen3-embedding:0.6b", "http://127.0.0.1:11434/", opener=opener)
    assert embedder.embed(["أ", "ب"]) == [[0.1, 0.2], [0.3, 0.4]]
    [(request, timeout)] = opener.requests
    assert request.full_url == "http://127.0.0.1:11434/api/embed" and timeout == 120.0
    assert json.loads(request.data) == {"model": "qwen3-embedding:0.6b", "input": ["أ", "ب"]}
    assert embedder.identity() == {"embedder": "ollama", "model": "qwen3-embedding:0.6b", "semantic": True}


@pytest.mark.parametrize("body", [b"{}", b'{"embeddings": [[1.0]]}', b'{"embeddings": [[1.0], [1.0, 2.0]]}', b"<html>"])
def test_a_malformed_embedding_reply_is_refused(body):
    embedder = OllamaEmbedder(opener=FakeOpener(body))
    with pytest.raises(PayloadRejected) as exc:
        embedder.embed(["أ", "ب"])
    assert exc.value.code == "embedder_malformed"


# ————— الدمج مع BM25 —————

@pytest.fixture
def fused(monkeypatch, index):
    """BM25 مصطنع يعرف m2 وm3 وحدهما، والمتّجهاتُ تعرف الثلاثة: ما يظهر بقناتيه، وما يظهر بواحدة."""
    def fake_fts(query, limit=10, match_any=False, corpus="maritime"):
        return [{"doc_id": "reg", "part": "لائحة", "locus": "m2", "item_digest": "d2"},
                {"doc_id": "reg", "part": "لائحة", "locus": "m3", "item_digest": "d3"}]
    monkeypatch.setattr(hybrid_retrieval, "fts_search", fake_fts)
    monkeypatch.setattr(HybridRetriever, "_get_page_text",
                        lambda self, corpus, doc_id, locus: TEXTS.get(f"{doc_id}:{locus}", ""))
    return HybridRetriever(vector=(index, HashEmbedder(128)))


def test_a_page_found_only_by_vectors_enters_the_fusion_with_its_channel_named(fused):
    results = fused.search("مياه الصابورة والاتزان", limit=3)
    by_locus = {r["locus"].split(" - ")[0]: r for r in results}   # المقطّعُ يُلحق عنوانَ المادة بالموضع
    assert "m1" in by_locus and by_locus["m1"]["channels"] == ["vector"]
    assert by_locus["m2"]["channels"] == ["bm25", "vector"]
    # القناةُ الثانية تزيد درجةَ RRF للصفحة نفسِها (علاوةُ المقطع اللفظية واحدةٌ في الحالين)
    plain = {r["locus"].split(" - ")[0]: r for r in HybridRetriever().search("مياه الصابورة والاتزان", limit=3)}
    assert by_locus["m2"]["score"] > plain["m2"]["score"]


def test_without_a_vector_index_the_retriever_behaves_as_before(monkeypatch, fused):
    plain = HybridRetriever()
    results = plain.search("مياه الصابورة والاتزان", limit=3)
    assert {r["locus"].split(" - ")[0] for r in results} == {"m2", "m3"}
    assert all(r["channels"] == ["bm25"] for r in results)


def test_a_refused_vector_channel_surfaces_instead_of_reading_as_no_results(fused, index):
    broken = HybridRetriever(vector=(index, HashEmbedder(64)))
    with pytest.raises(PayloadRejected) as exc:
        broken.search("مياه", limit=3)
    assert exc.value.code == "embedder_identity_mismatch"
