"""الرقمُ الدلاليّ الحيّ (غ٣): BM25 مقابل المتّجهات مقابل الهجين RRF على بنكٍ عربيٍّ عام.

- **البنك** `evaluation/suites/retrieval_general_v1.json` مجمَّدٌ ببصمته قبل القياس (`BANK_SHA256`).
- **الأذرع الثلاث** على المقاطع نفسِها:
  - `bm25`: FTS5 على النصّ المطبَّع (`tools.rebuild_index.normalize`)، بلا توسيعٍ صرفيّ.
  - `vectors`: `core.vector_retrieval.VectorIndex` بالمُضمِّن المسمّى.
  - `hybrid`: دمجُ الرتبتين بالرتب التبادلية (RRF، k=60)، والتعادلُ لـBM25 كما في `core.hybrid_retrieval`.
- **المقاييس:** hit@5 حاكمٌ (بروتوكول ك٤٦ لمكوّن المتّجهات)، وnDCG@10 وMRR ثانويّان.
- **المجالات** بالمقطع الذهبيّ لا بالاستعلام، فلكلّ مقطعٍ استعلامان مترابطان: hit@5 بـWilson على حجمٍ فعليّ
  من أثر التصميم، وnDCG@10 وفرقُ hit@5 بإعادة معاينة المقاطع.
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
from evaluation import ablation
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
    """الدمجُ بالرتب التبادلية: مجموعُ 1/(k+rank+1) عبر القنوات.

    والتعادلُ لما أدخلته قناةٌ أسبق (BM25 قبل المتّجهات)، كما في `HybridRetriever` الذي يرتّب بالنقاط وحدها
    ترتيبًا مستقرًّا؛ لا بالمعرّف، فقد يعبر مقطعٌ حدَّ الخمسة الأولى بغير ما يفعل المنتج (ملاحظة Codex على #132).
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda key: -scores[key])


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


def _cluster(query: dict) -> str:
    return "+".join(sorted(query["relevant"]))


def design_effect(rows: list[dict], value) -> float:
    """أثرُ التصميم (Kish): ١ + (م − ١)·ρ، وρ الارتباطُ داخل المقطع بتقدير تحليل التباين، ولا يقلّ عن ١."""
    grouped: dict[str, list[float]] = {}
    for row in rows:
        grouped.setdefault(row["cluster"], []).append(value(row))
    k, n = len(grouped), len(rows)
    if k < 2 or n == k:
        return 1.0
    mean = sum(value(r) for r in rows) / n
    means = {c: sum(v) / len(v) for c, v in grouped.items()}
    msb = sum(len(v) * (means[c] - mean) ** 2 for c, v in grouped.items()) / (k - 1)
    msw = sum((x - means[c]) ** 2 for c, v in grouped.items() for x in v) / (n - k)
    m0 = (n - sum(len(v) ** 2 for v in grouped.values()) / n) / (k - 1)
    denominator = msb + (m0 - 1) * msw
    icc = (msb - msw) / denominator if denominator > 0 else 0.0
    return max(1.0, 1 + (n / k - 1) * icc)


def hit_interval(rows: list[dict]) -> list[float]:
    """مجالُ hit@5: Wilson على الحجم الفعليّ n/أثر التصميم، فلا يضيق إلى نقطةٍ حين تنجح العيّنةُ كلُّها
    (٦٠/٦٠ لا تعني يقينًا؛ ملاحظة Codex على #132)، ولا يعامل استعلامَي المقطع مستقلَّين."""
    deff = design_effect(rows, lambda r: float(r["hit_at_5"]))
    return wilson(sum(r["hit_at_5"] for r in rows) / deff, len(rows) / deff)


def cluster_bootstrap(rows: list[dict], value, *, resamples: int = 2000, seed: int = 0) -> list[float]:
    """مجالٌ ٩٥٪ بإعادة معاينة المقاطع الذهبية، لا الاستعلامات.

    استعلاما المقطع الواحد (اللفظيّ والمُعادُ صياغتُه) يتشاركان الذهبيَّ والمنافسين، فلا يُعاملان مستقلَّين
    (ملاحظة Codex على #132). ولا تُحفظ طبقاتُ المواضيع: في كلٍّ منها خمسةُ مقاطع، وإعادةُ المعاينة داخل طبقةٍ
    من خمسة تُنقص التباينَ خُمسًا. والبذرةُ ثابتة فيُعاد الرقمُ نفسُه.
    """
    if not rows:
        return [0.0, 0.0]
    grouped: dict[str, list[float]] = {}
    for row in rows:
        grouped.setdefault(row["cluster"], []).append(value(row))
    clusters = list(grouped.values())
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        total = count = 0
        for _ in clusters:
            picked = rng.choice(clusters)
            total += sum(picked)
            count += len(picked)
        means.append(total / count)
    means.sort()
    return [round(means[int(0.025 * resamples)], 4), round(means[int(0.975 * resamples) - 1], 4)]


def _summary(rows: list[dict]) -> dict:
    n = len(rows)
    hits = sum(r["hit_at_5"] for r in rows)
    ndcg = [r["ndcg_at_10"] for r in rows]
    return {"queries": n, "hit_at_5": round(hits / n, 4),
            "hit_at_5_ci95": hit_interval(rows),
            "hit_at_5_design_effect": round(design_effect(rows, lambda r: float(r["hit_at_5"])), 4),
            "ndcg_at_10": round(sum(ndcg) / n, 4), "ndcg_at_10_ci95": cluster_bootstrap(rows, lambda r: r["ndcg_at_10"]),
            "mrr": round(sum(r["rr"] for r in rows) / n, 4)}


def summaries(rows: dict[str, list[dict]]) -> dict:
    types = sorted({r["type"] for arm_rows in rows.values() for r in arm_rows})
    return {arm: {"overall": _summary(rows[arm]),
                  "by_type": {t: _summary([r for r in rows[arm] if r["type"] == t]) for t in types}}
            for arm in rows}


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
        channels: dict[str, dict[str, list[str]]] = {}
        for q in bank["queries"]:
            lexical = bm25.rank(q["text"], depth)
            semantic = [hit["passage_id"] for hit in index.search(q["text"], embedder, limit=depth)]
            channels[q["id"]] = {"bm25": lexical, "vectors": semantic}
            relevant = set(q["relevant"])
            for arm, ranking in (("bm25", lexical), ("vectors", semantic), ("hybrid", rrf([lexical, semantic]))):
                rows[arm].append({"id": q["id"], "type": q["type"], "topic": q["topic"], "cluster": _cluster(q),
                                  **_metrics(ranking, relevant), "top5": ranking[:5]})
    return {"arms": summaries(rows), "rows": rows, "channels": channels}


def hybrid_rows_from_channels(channels: dict, bank: dict) -> list[dict]:
    """صفوفُ الهجين من رتبتَي القناتين المسجَّلتين، فيُعاد الدمجُ بلا مُضمِّن."""
    rows = []
    for q in bank["queries"]:
        ranking = rrf([channels[q["id"]]["bm25"].split(), channels[q["id"]]["vectors"].split()])
        metrics = _metrics(ranking, set(q["relevant"]))
        rows.append({"id": q["id"], "type": q["type"], "rank": metrics["rank"], "hit_at_5": metrics["hit_at_5"],
                     "ndcg_at_10": metrics["ndcg_at_10"]})
    return rows


def rows_from_report(report_rows: dict[str, list[dict]], bank: dict) -> dict[str, list[dict]]:
    """الصفوفُ المسجَّلة في التقرير (المعرّف والنوع والرتبة وhit@5 وnDCG@10) مكمَّلةً من البنك، فيُعاد كلُّ رقمٍ بلا نموذج."""
    queries = {q["id"]: q for q in bank["queries"]}
    return {arm: [{**r, "topic": queries[r["id"]]["topic"], "cluster": _cluster(queries[r["id"]]),
                   "rr": 0.0 if r["rank"] is None else round(1 / r["rank"], 4)} for r in arm_rows]
            for arm, arm_rows in report_rows.items()}


def paired(on: list[dict], off: list[dict]) -> list[list[dict]]:
    """صفوفُ الذراعين بشكل `evaluation.ablation.compare`: النجاحُ hit@5."""
    shape = lambda rows: [{"id": r["id"], "category": r["type"], "status": "measured", "passed": r["hit_at_5"]}
                          for r in rows]
    return [shape(on), shape(off)]


def paired_effect_ci(on: list[dict], off: list[dict], **options) -> list[float]:
    """مجالُ فرق hit@5 «مع − بدون» بإعادة معاينة المقاطع، بجانب مجال Agresti–Min الذي يفترض الاستقلال."""
    off_by_id = {r["id"]: r for r in off}
    diffs = [{**r, "difference": float(r["hit_at_5"]) - float(off_by_id[r["id"]]["hit_at_5"])} for r in on]
    return cluster_bootstrap(diffs, lambda r: r["difference"], **options)


def comparisons(rows: dict[str, list[dict]], arms: dict) -> dict:
    """كلُّ ذراعٍ مقابل BM25 في hit@5، وصفًا لا حكمًا.

    قاعدةُ ك٤٦ لمكوّن المتّجهات تقرّر تفعيلَ قناته في `HybridRetriever` المنتج. وأذرعُ هذا القياس ليست
    المنتجَ مع القناة وبدونها: BM25 هنا بلا التوسيع الصرفيّ، والهجينُ بلا قائمتَي exact/any وأوزانهما ولا
    تجميع الصفحات؛ والمقارنةُ على الاستعلامات والمقاطعُ عناقيدُ لا يراها `ablation.judge`. فلا يُستدعى الحكمُ
    هنا ولو صار المكوّنُ `ready`، والقرارُ من قياس `HybridRetriever` نفسِه بقلب `vector` وحده (ملاحظات Codex
    على #132). والمتّجهاتُ وحدها تحلّ محلّ BM25 لا تضاف إليه، فهي وصفيةٌ على كل حال. ولا تُقرأ هنا حالةُ
    البروتوكول الحيّة، فيُعاد الدليلُ من صفوفه وحدها بعد أيّ انتقالٍ فيه؛ وحالتُه وقتَ القياس في `config`.
    """
    out = {}
    for name, on in (("hybrid_vs_bm25", "hybrid"), ("vectors_vs_bm25", "vectors")):
        on_rows, off_rows = paired(rows[on], rows["bm25"])
        entry = {"hit_at_5": ablation.compare(on_rows, off_rows),
                 "hit_at_5_effect_ci95_clustered": paired_effect_ci(rows[on], rows["bm25"]),
                 "ndcg_at_10_difference": round(arms[on]["overall"]["ndcg_at_10"]
                                                - arms["bm25"]["overall"]["ndcg_at_10"], 4)}
        if on == "hybrid":
            entry["protocol"] = {"component": "vectors", "decision": "not_applied",
                                 "reason": "descriptive_these_arms_are_not_the_product_retriever_with_vector_toggled"}
        else:
            entry["protocol"] = {"decision": "not_applied",
                                 "reason": "descriptive_the_vector_rule_is_for_the_hybrid_not_a_bm25_replacement"}
        out[name] = entry
    return out
