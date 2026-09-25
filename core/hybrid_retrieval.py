"""محرك الاسترجاع المعرفي الهجين والتقطيع التشريعي (Track A).

يدمج بين:
1. الاسترجاع المعجمي الصريح عبر FTS5 (BM25).
2. التوسيع اللساني بالجذور الصرفية عبر `projections/morphology.py`.
3. خوارزمية الدمج التبادلي الرتبي (Reciprocal Rank Fusion - RRF).
4. التقطيع الدلالي الهيكلي على مستوى المادة والفقرة والتعريف عبر `core/chunking.py`.
5. قناةُ المتّجهات (غ٢) حين يُعطى المسترجعُ فهرسًا ومُضمِّنه (`core/vector_retrieval.py`):
   تُدمج بالرتب التبادلية نفسِها، وكلُّ نتيجةٍ تقول قنواتها (`channels`). وبلا فهرسٍ يبقى
   السلوكُ كما كان: BM25 والتوسيعُ الصرفي وحدهما.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from core.chunking import LegislativeChunk, LegislativeChunker
from core.corpus import CorpusCatalog, CorpusFile
from core.linguistics.morphology import analyze, singularize_broken_plural
from tools.rebuild_index import CORPORA, normalize, search as fts_search

ROOT = Path(__file__).resolve().parent.parent


class HybridRetriever:
    """مسترجع هجين يجمع بين البحث الدقيق والتوسيع الصرفي والتقطيع الدلالي."""

    def __init__(self, root: Path = ROOT, rrf_k: int = 60, vector=None):
        """`vector` زوجٌ (فهرسُ متّجهات، مُضمِّنُه) أو None؛ والهويّةُ تُفحص عند أوّل بحث."""
        self.root = root
        self.rrf_k = rrf_k
        self.vector = vector
        self.chunker = LegislativeChunker()
        self._page_cache: dict[str, dict[str, str]] = {}

    def _get_page_text(self, corpus: str, doc_id: str, locus: str) -> str:
        cache_key = f"{corpus}:{doc_id}"
        if cache_key not in self._page_cache:
            cat_path = self.root / "corpus" / corpus / "_catalog.jsonl"
            if not cat_path.exists():
                return ""
            catalog = CorpusCatalog(cat_path, create=False)
            curr = catalog.current()
            if doc_id not in curr:
                return ""
            rec = curr[doc_id]
            cf = CorpusFile(self.root / rec["file"], create=False)
            pages_map = {}
            for p in cf.pages():
                it = p["item"]
                pages_map[it["locus"]] = it["text"]
            self._page_cache[cache_key] = pages_map

        return self._page_cache[cache_key].get(locus, "")

    def search(self, query: str, limit: int = 5, corpus: str = "maritime") -> list[dict]:
        clean_q = query.strip()
        if not clean_q:
            return []

        # 1. الاسترجاع المعجمي الدقيق
        exact_results = fts_search(clean_q, limit=limit * 2, match_any=False, corpus=corpus)

        # 2. استخراج الجذور والمفردات والتوسيع الصرفي
        tokens = [t for t in normalize(clean_q).split() if len(t) >= 2]
        roots: list[str] = []
        lemmas: list[str] = []
        for t in tokens:
            analysis = analyze(t)
            if analysis.root and analysis.root not in roots:
                roots.append(analysis.root)
            if analysis.lemma and analysis.lemma != t and analysis.lemma not in lemmas:
                lemmas.append(analysis.lemma)

        # بحث موسع بالمفردات أو أي كلمة إن لم نجد نتائج كافية بالبحث الصارم
        any_results = []
        if len(exact_results) < limit:
            try:
                # محاولة البحث بجملة المفردات
                if lemmas:
                    lemma_q = " ".join(lemmas)
                    lemma_results = fts_search(lemma_q, limit=limit, match_any=False, corpus=corpus)
                    exact_results.extend([r for r in lemma_results if r not in exact_results])

                any_results = fts_search(clean_q, limit=limit * 2, match_any=True, corpus=corpus)
            except Exception:
                any_results = []

        # 2ب. قناةُ المتّجهات (غ٢): رفضُها رفضٌ مسمًّى يصعد، لا صمتٌ يُقرأ «لا نتائج»
        vector_results: list[dict] = []
        if self.vector is not None:
            index, embedder = self.vector
            vector_results = index.search(clean_q, embedder, limit=limit * 2)

        # 3. دمج النتائج وحساب رتب RRF
        # RRF: score = sum(1.0 / (k + rank))
        scores: dict[str, float] = {}
        items_meta: dict[str, dict] = {}
        channels: dict[str, set] = {}

        for rank, res in enumerate(exact_results):
            key = f"{res['doc_id']}:{res['locus']}"
            items_meta[key] = res
            scores[key] = scores.get(key, 0.0) + (1.0 / (self.rrf_k + rank + 1))
            channels.setdefault(key, set()).add("bm25")

        for rank, res in enumerate(any_results):
            key = f"{res['doc_id']}:{res['locus']}"
            if key not in items_meta:
                items_meta[key] = res
            scores[key] = scores.get(key, 0.0) + (0.5 / (self.rrf_k + rank + 1))
            channels.setdefault(key, set()).add("bm25")

        for rank, res in enumerate(vector_results):
            key = f"{res['source']}:{res['locus']}"
            if key not in items_meta:
                items_meta[key] = {"doc_id": res["source"], "part": res["part"], "locus": res["locus"],
                                   "item_digest": res["item_digest"]}
            scores[key] = scores.get(key, 0.0) + (1.0 / (self.rrf_k + rank + 1))
            channels.setdefault(key, set()).add("vector")

        # ترتيب الصفحات حسب نقاط RRF
        sorted_keys = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)

        final_chunks: list[dict] = []
        for key in sorted_keys[:limit * 2]:
            meta = items_meta[key]
            doc_id = meta["doc_id"]
            page_locus = meta["locus"]
            part = meta["part"]
            page_text = self._get_page_text(corpus, doc_id, page_locus)

            if not page_text:
                # إذا تعذر قراءة النص الكامل نستخدم ما في الميتا
                final_chunks.append({
                    "doc_id": doc_id,
                    "part": part,
                    "locus": page_locus,
                    "heading": page_locus,
                    "text": "",
                    "score": int(round(scores[key] * 10000)),
                    "matched_roots": roots,
                    "item_digest": meta["item_digest"],
                    "channels": sorted(channels[key]),
                })
                continue

            # تقطيع الصفحة إلى مقاطع تشريعية واختيار المقطع الأكثر صلة
            chunks = self.chunker.chunk_text(page_text, doc_id=doc_id, part=part, base_locus=page_locus)
            if not chunks:
                final_chunks.append({
                    "doc_id": doc_id,
                    "part": part,
                    "locus": page_locus,
                    "heading": page_locus,
                    "text": page_text[:400],
                    "score": int(round(scores[key] * 10000)),
                    "matched_roots": roots,
                    "item_digest": meta["item_digest"],
                    "channels": sorted(channels[key]),
                })
                continue

            # حساب أفضل مقطع في الصفحة للاستعلام
            best_chunk = chunks[0]
            best_score = -1.0
            norm_tokens = [normalize(t) for t in tokens]

            for chunk in chunks:
                norm_chunk = normalize(chunk.text)
                hit_tokens = sum(1 for nt in norm_tokens if nt in norm_chunk)
                hit_roots = sum(1 for r in roots if r in norm_chunk)
                chunk_score = (hit_tokens * 2.0) + (hit_roots * 1.0)
                if chunk_score > best_score:
                    best_score = chunk_score
                    best_chunk = chunk

            matched_in_chunk = [r for r in roots if r in normalize(best_chunk.text)]
            score_val = int(round((scores[key] + (max(0.0, best_score) * 0.01)) * 10000))
            final_chunks.append({
                "doc_id": best_chunk.doc_id,
                "part": best_chunk.part,
                "locus": best_chunk.locus,
                "heading": best_chunk.heading,
                "text": best_chunk.text,
                "score": score_val,
                "matched_roots": matched_in_chunk,
                "item_digest": best_chunk.digest,
                "channels": sorted(channels[key]),
            })

            if len(final_chunks) >= limit:
                break

        return final_chunks[:limit]


_DEFAULT_RETRIEVER = HybridRetriever()


def hybrid_search(query: str, limit: int = 5, corpus: str = "maritime") -> list[dict]:
    """واجهة البحث الهجين السريعة المعتمدة لمنظومة ديوان."""
    return _DEFAULT_RETRIEVER.search(query, limit=limit, corpus=corpus)
