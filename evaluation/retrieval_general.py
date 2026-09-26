"""الرقمُ الدلاليّ الحيّ (غ٣): BM25 مقابل المتّجهات مقابل الهجين RRF على بنكٍ عربيٍّ عام.

- **البنك** `evaluation/suites/retrieval_general_v1.json` مجمَّدٌ ببصمته قبل القياس (`BANK_SHA256`).
- **الأذرع الثلاث** على المقاطع نفسِها:
  - `bm25`: FTS5 على النصّ المطبَّع (`tools.rebuild_index.normalize`)، بلا توسيعٍ صرفيّ.
  - `vectors`: `core.vector_retrieval.VectorIndex` بالمُضمِّن المسمّى.
  - `hybrid`: دمجُ الرتبتين بالرتب التبادلية (RRF، k=60) كما في `core.hybrid_retrieval`.
- **المقاييس:** hit@5 حاكمٌ (بروتوكول ك٤٦ لمكوّن المتّجهات)، وnDCG@10 وMRR ثانويّان.
- **والمُضمِّنُ مسترجعٌ لا حَكَم:** الحكمُ بالمقطع الذهبيّ المسجَّل في البنك وحده.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import random
import re
import sqlite3
import tempfile

from core.vector_retrieval import VectorIndex, passage
from tools.rebuild_index import normalize

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "evaluation" / "suites" / "retrieval_general_v1.json"
BANK_SHA256 = "df7ca9456ae1f451d79b021b981d2ed23ef374a6025f546c373f525354a8da4f"
RRF_K = 60
DEPTH = 50
MIN_QUERIES = 100
ARMS = ("bm25", "vectors", "hybrid")
_WORD = re.compile(r"\w+", re.UNICODE)


class RetrievalBankError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code


def load_bank(path: Path = BANK, *, frozen: bool = True) -> dict:
    raw = Path(path).read_bytes()
    if frozen and hashlib.sha256(raw).hexdigest() != BANK_SHA256:
        raise RetrievalBankError("bank_not_frozen", "بصمةُ البنك لا تطابق المسجَّلة قبل القياس")
    bank = json.loads(raw.decode("utf-8"))
    ids = [d["id"] for d in bank["documents"]]
    if len(set(ids)) != len(ids):
        raise RetrievalBankError("document_id_duplicate", "معرّفُ مقطعٍ مكرّر")
    if len(bank["queries"]) < MIN_QUERIES:
        raise RetrievalBankError("too_few_queries", f"أقلّ من {MIN_QUERIES} استعلام")
    known = set(ids)
    for q in bank["queries"]:
        if not q["relevant"] or not set(q["relevant"]) <= known:
            raise RetrievalBankError("relevant_unknown", q["id"])
    return bank


def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[str]:
    """الدمجُ بالرتب التبادلية: مجموعُ 1/(k+rank+1) عبر القنوات، والتعادلُ بالمعرّف."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return [key for key, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


class _Bm25:
    def __init__(self, documents: list[dict]):
        self.con = sqlite3.connect(":memory:")
        self.con.execute("CREATE VIRTUAL TABLE docs USING fts5(id UNINDEXED, text, tokenize='unicode61')")
        self.con.executemany("INSERT INTO docs(id, text) VALUES (?, ?)",
                             [(d["id"], normalize(d["text"])) for d in documents])

    def rank(self, query: str, depth: int = DEPTH) -> list[str]:
        words = _WORD.findall(normalize(query))
        if not words:
            return []
        expression = " OR ".join('"' + w.replace('"', "") + '"' for w in words)
        rows = self.con.execute("SELECT id FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) LIMIT ?",
                                (expression, depth)).fetchall()
        return [row[0] for row in rows]


def _metrics(ranking: list[str], relevant: set[str]) -> dict:
    rank = next((i for i, key in enumerate(ranking) if key in relevant), None)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(relevant), 10)))
    dcg = sum(1 / math.log2(i + 2) for i, key in enumerate(ranking[:10]) if key in relevant)
    return {"rank": None if rank is None else rank + 1, "hit_at_5": rank is not None and rank < 5,
            "ndcg_at_10": round(dcg / ideal, 4), "rr": 0.0 if rank is None else round(1 / (rank + 1), 4)}


def wilson(successes: int, n: int, z: float = 1.96) -> list[float]:
    if not n:
        return [0.0, 0.0]
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [round(centre - half, 4), round(centre + half, 4)]


def bootstrap_mean(values: list[float], *, resamples: int = 2000, seed: int = 0) -> list[float]:
    """مجالٌ ٩٥٪ بإعادة المعاينة، ببذرةٍ ثابتة فيُعاد الرقمُ نفسُه."""
    if not values:
        return [0.0, 0.0]
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(resamples))
    return [round(means[int(0.025 * resamples)], 4), round(means[int(0.975 * resamples) - 1], 4)]


def _summary(rows: list[dict]) -> dict:
    n = len(rows)
    hits = sum(r["hit_at_5"] for r in rows)
    ndcg = [r["ndcg_at_10"] for r in rows]
    return {"queries": n, "hit_at_5": round(hits / n, 4), "hit_at_5_ci95": wilson(hits, n),
            "ndcg_at_10": round(sum(ndcg) / n, 4), "ndcg_at_10_ci95": bootstrap_mean(ndcg),
            "mrr": round(sum(r["rr"] for r in rows) / n, 4)}


def run(bank: dict, embedder, *, depth: int = DEPTH) -> dict:
    """الأذرعُ الثلاث على كلّ استعلام، والملخّصُ كلُّه وبحسب نوع الاستعلام."""
    documents = bank["documents"]
    bm25 = _Bm25(documents)
    with tempfile.TemporaryDirectory(prefix="diwan-g3-") as tmp:
        path = Path(tmp) / "vectors.sqlite"
        VectorIndex.build(path, [passage(d["id"], "retrieval_general_v1", d["id"], d["topic"], d["text"])
                                 for d in documents], embedder)
        index = VectorIndex(path)
        rows: dict[str, list[dict]] = {arm: [] for arm in ARMS}
        for q in bank["queries"]:
            lexical = bm25.rank(q["text"], depth)
            semantic = [hit["passage_id"] for hit in index.search(q["text"], embedder, limit=depth)]
            relevant = set(q["relevant"])
            for arm, ranking in (("bm25", lexical), ("vectors", semantic), ("hybrid", rrf([lexical, semantic]))):
                rows[arm].append({"id": q["id"], "type": q["type"], "topic": q["topic"],
                                  **_metrics(ranking, relevant), "top5": ranking[:5]})
    types = sorted({q["type"] for q in bank["queries"]})
    return {"arms": {arm: {"overall": _summary(rows[arm]),
                           "by_type": {t: _summary([r for r in rows[arm] if r["type"] == t]) for t in types}}
                     for arm in ARMS},
            "rows": rows}


def paired(on: list[dict], off: list[dict]) -> list[list[dict]]:
    """صفوفُ الذراعين بشكل `evaluation.ablation.compare`: النجاحُ hit@5."""
    shape = lambda rows: [{"id": r["id"], "category": r["type"], "status": "measured", "passed": r["hit_at_5"]}
                          for r in rows]
    return [shape(on), shape(off)]
