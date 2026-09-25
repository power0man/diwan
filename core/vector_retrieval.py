"""استرجاعٌ بالمتجهات يُدمج مع BM25 (غ٢): مُضمِّنٌ مسمًّى، وفهرسٌ يحمل هويّته، وبلا numpy.

**ما هذا وما ليس هو.** الفهرسُ جدولُ SQLite يحفظ لكلّ مقطعٍ متّجهَه (مطبَّعًا إلى طول ١)
وهويّةَ المُضمِّن الذي أنتجه، ولا يقبل بحثًا بمُضمِّنٍ غيره (`embedder_identity_mismatch`):
فالمتّجهُ بلا مُضمِّنه رقمٌ بلا معنى. والدمجُ مع BM25 في `core/hybrid_retrieval.py`
بالرتب التبادلية (RRF) نفسِها، وكلُّ نتيجةٍ تقول من أيّ قناةٍ جاءت (`channels`).

**المُضمِّنان.** `OllamaEmbedder` يسأل Ollama المحلّي (`/api/embed`) بنموذجٍ مسمًّى
(الافتراض `qwen3-embedding:0.6b`، ومرشَّحُ المالك الآخر EmbeddingGemma) — وهو الطريقُ
الذي يُقاس به الرقم. و`HashEmbedder` تجزئةُ ثلاثيّات الحروف المطبَّعة: حتميٌّ بلا
نموذج، **ليس دلاليًّا**، يُثبت مسارَ الفهرسة والدمج في بيئةٍ بلا نموذج ولا يُنشر به رقم
(`identity()["semantic"] is False`).

**فوق ماذا.** مقاطعُ المتون (`corpus_passages`: صفحةٌ لكلّ موضع كما في فهرس FTS)،
وملفّاتُ المالك العامة (`file_passages`: ملفّاتٌ نصّية تُقطَّع فقراتٍ بسقفٍ معلَن).
**حدود.** بلا numpy الحسابُ حلقةُ Python (~١٫٣ مليون ضربٍ للاستعلام على ١٢٧٩ صفحة ×
١٠٢٤ بُعدًا)، يكفي للمتن الحالي لا لمئات الآلاف. ولم يُقَس أثرُ الدمج على جودة الاسترجاع
بعدُ إلا بالمُضمِّن الحتميّ؛ القياسُ بنموذجٍ حيّ على الماك (`tools/rebuild_vectors.py`).
"""
from __future__ import annotations

from array import array
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import urllib.error
import urllib.request

from core.canonical import PayloadRejected

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1
MAX_PASSAGE_CHARS = 4000
MAX_BATCH = 32
MAX_FILE_BYTES = 8 * 1024 * 1024
CHUNK_CHARS = 1200
SKIP_DIRS = {".git", ".diwan-journal", "__pycache__", "node_modules", ".venv", "var"}
_DIACRITICS = re.compile(r"[ً-ْٰـ]")
_ALEF = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _refuse(path: str, code: str, reason: str):
    raise PayloadRejected(path, code, reason)


def normalize(text: str) -> str:
    return _DIACRITICS.sub("", text).translate(_ALEF)


@dataclass(frozen=True)
class Passage:
    passage_id: str
    source: str
    locus: str
    part: str
    text: str
    digest: str


def passage(passage_id: str, source: str, locus: str, part: str, text: str, digest: str | None = None) -> Passage:
    return Passage(passage_id, source, locus, part, text,
                   hashlib.sha256(text.encode("utf-8")).hexdigest() if digest is None else digest)


def _unit(vector) -> array:
    values = array("f", (float(v) for v in vector))
    norm = math.sqrt(sum(v * v for v in values))
    if not math.isfinite(norm) or norm == 0.0:
        return array("f", [0.0] * len(values))
    return array("f", (v / norm for v in values))


# ————— المُضمِّنان —————

class HashEmbedder:
    """تجزئةُ ثلاثيّات الحروف: حتميٌّ، بلا نموذج، وليس دلاليًّا — لإثبات المسار لا لنشر رقم."""
    name = "hash-trigram"

    def __init__(self, dim: int = 256):
        if type(dim) is not int or not 8 <= dim <= 4096:
            _refuse("embedder.dim", "embedder_dim_invalid", "بُعدٌ بين ٨ و٤٠٩٦")
        self.dim = dim

    def identity(self) -> dict:
        return {"embedder": self.name, "dim": self.dim, "semantic": False}

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in normalize(text).split():
                padded = f" {token} "
                for i in range(len(padded) - 2):
                    slot = int.from_bytes(hashlib.blake2b(padded[i:i + 3].encode("utf-8"),
                                                          digest_size=4).digest(), "big")
                    vec[slot % self.dim] += 1.0
            out.append(list(_unit(vec)))
        return out


class OllamaEmbedder:
    """`POST /api/embed` على Ollama المحلّي بنموذجٍ مسمًّى؛ كلُّ عطبٍ رفضٌ مسمًّى."""
    name = "ollama"

    def __init__(self, model: str = "qwen3-embedding:0.6b", base_url: str = "http://127.0.0.1:11434",
                 *, timeout_s: float = 120.0, opener=None):
        if not isinstance(model, str) or not model.strip():
            _refuse("embedder.model", "embedder_model_invalid", "اسمُ نموذجٍ مطلوب")
        self.model, self.base_url, self.timeout_s = model.strip(), base_url.rstrip("/"), timeout_s
        self.opener = _LOCAL_OPENER if opener is None else opener

    def identity(self) -> dict:
        return {"embedder": self.name, "model": self.model, "semantic": True}

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        body = json.dumps({"model": self.model, "input": list(texts)}).encode("utf-8")
        request = urllib.request.Request(self.base_url + "/api/embed", data=body,
                                         headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.timeout_s) as response:
                raw = response.read(64 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            _refuse("embedder", f"embedder_http_{exc.code}", "خطأُ خادم التضمين")
        except (TimeoutError, urllib.error.URLError, OSError):
            _refuse("embedder", "embedder_unreachable", "تعذّر بلوغُ خادم التضمين")
        try:
            payload = json.loads(raw.decode("utf-8"))
            vectors = payload["embeddings"]
            if (not isinstance(vectors, list) or len(vectors) != len(texts)
                    or any(not isinstance(v, list) or not v or len(v) != len(vectors[0]) for v in vectors)):
                raise ValueError("shape")
            return [[float(x) for x in v] for v in vectors]
        except (ValueError, KeyError, TypeError):
            _refuse("embedder", "embedder_malformed", "ردُّ التضمين بلا متّجهاتٍ مطابقة العدد والبُعد")


# ————— المقاطع —————

def corpus_passages(corpus: str = "maritime", root: Path = ROOT) -> list[Passage]:
    """صفحةٌ لكلّ موضعٍ بآخر نسخته، كما يفهرسها FTS، فيلتقي المفتاحان `doc_id:locus`."""
    from core.corpus import CorpusCatalog, CorpusFile
    from tools.rebuild_index import _corpus_paths
    catalog_path = _corpus_paths(corpus)[0]
    if not catalog_path.exists():
        _refuse("corpus", "catalog_missing", f"لا فهرسَ للمتن {corpus!r}")
    catalog = CorpusCatalog(catalog_path, create=False)
    passages = []
    for rec in sorted(catalog.current().values(), key=lambda r: r["doc_id"]):
        latest = {}
        for page in CorpusFile(root / rec["file"], create=False).pages():
            latest[page["item"]["locus"]] = page
        for locus, page in sorted(latest.items()):
            item = page["item"]
            passages.append(passage(f'{rec["doc_id"]}:{locus}', rec["doc_id"], locus, item["part"],
                                    item["text"], page["item_digest"]))
    return passages


def file_passages(root: Path, *, chunk_chars: int = CHUNK_CHARS) -> list[Passage]:
    """ملفّاتُ المالك النصّية تحت جذرٍ، فقراتٍ بسقفٍ معلَن؛ المخفيُّ والروابطُ وغيرُ النصّ يُتركون."""
    root = Path(root)
    passages = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            path = Path(base) / name
            if name.startswith(".") or path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            try:
                text = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                continue
            relative = path.relative_to(root).as_posix()
            for index, chunk in enumerate(_chunks(text, chunk_chars)):
                passages.append(passage(f"{relative}#{index}", relative, f"chunk-{index}", "file", chunk))
    return passages


def _chunks(text: str, limit: int) -> list[str]:
    """فقراتٌ تُضمّ حتى السقف، وفقرةٌ أطولُ منه تُقصّ بالترتيب بعد ما سبقها."""
    chunks, current = [], ""
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > limit:
            if current:
                chunks.append(current)
                current = ""
            while len(paragraph) > limit:
                chunks.append(paragraph[:limit])
                paragraph = paragraph[limit:]
            if not paragraph:
                continue
        if current and len(current) + 1 + len(paragraph) > limit:
            chunks.append(current)
            current = paragraph
        else:
            current = paragraph if not current else current + "\n" + paragraph
    if current:
        chunks.append(current)
    return chunks


# ————— الفهرس —————

class VectorIndex:
    """فهرسُ متّجهاتٍ يحمل هويّةَ مُضمِّنه ولا يُسأل بغيره."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.exists():
            _refuse("index", "vector_index_missing", f"لا فهرسَ متّجهات: {self.path.name}")

    @classmethod
    def build(cls, path: Path, passages: list[Passage], embedder, *, replace: bool = False) -> dict:
        path = Path(path)
        if path.exists() and not replace:
            _refuse("index", "vector_index_exists", "فهرسٌ قائم؛ اطلب الاستبدال صراحةً")
        if not passages:
            _refuse("passages", "passages_empty", "لا مقاطعَ تُفهرس")
        identity = embedder.identity()
        if type(identity) is not dict or not identity.get("embedder"):
            _refuse("embedder", "embedder_identity_invalid", "هويّةُ مُضمِّنٍ مطلوبة")
        temp = path.with_suffix(path.suffix + ".building")
        if temp.exists():
            temp.unlink()
        con = sqlite3.connect(temp)
        dim = None
        try:
            con.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            con.execute("CREATE TABLE passages (passage_id TEXT PRIMARY KEY, source TEXT NOT NULL, "
                        "locus TEXT NOT NULL, part TEXT NOT NULL, digest TEXT NOT NULL, "
                        "head TEXT NOT NULL, embedding BLOB NOT NULL)")
            for start in range(0, len(passages), MAX_BATCH):
                batch = passages[start:start + MAX_BATCH]
                vectors = embedder.embed([p.text[:MAX_PASSAGE_CHARS] for p in batch])
                if len(vectors) != len(batch):
                    _refuse("embedder", "embedder_malformed", "عددُ المتّجهات لا يطابق المقاطع")
                for item, vector in zip(batch, vectors):
                    unit = _unit(vector)
                    if dim is None:
                        dim = len(unit)
                    if len(unit) != dim or dim == 0:
                        _refuse("embedder", "embedder_dim_inconsistent", "بُعدٌ متغيّر بين المتّجهات")
                    con.execute("INSERT INTO passages VALUES (?,?,?,?,?,?,?)",
                                (item.passage_id, item.source, item.locus, item.part, item.digest,
                                 item.text[:200], unit.tobytes()))
            provenance = hashlib.sha256("\n".join(sorted(p.digest for p in passages)).encode()).hexdigest()
            for key, value in (("schema_version", SCHEMA_VERSION), ("identity", identity),
                               ("dim", dim), ("count", len(passages)), ("passages_digest", provenance)):
                con.execute("INSERT INTO meta VALUES (?, ?)", (key, json.dumps(value, ensure_ascii=False, sort_keys=True)))
            con.commit()
        finally:
            con.close()
        os.replace(temp, path)
        return {"path": str(path), "count": len(passages), "dim": dim, "identity": identity,
                "passages_digest": provenance}

    def _meta(self) -> dict:
        con = sqlite3.connect(self.path)
        try:
            rows = con.execute("SELECT key, value FROM meta").fetchall()
        except sqlite3.DatabaseError:
            _refuse("index", "vector_index_corrupt", "فهرسُ المتّجهات غير مقروء")
        finally:
            con.close()
        meta = {k: json.loads(v) for k, v in rows}
        if meta.get("schema_version") != SCHEMA_VERSION or "identity" not in meta or "dim" not in meta:
            _refuse("index", "vector_index_corrupt", "بنيةُ فهرس المتّجهات غير معروفة")
        return meta

    def identity(self) -> dict:
        return self._meta()["identity"]

    def count(self) -> int:
        return self._meta()["count"]

    def search(self, query: str, embedder, *, limit: int = 10) -> list[dict]:
        meta = self._meta()
        if embedder.identity() != meta["identity"]:
            _refuse("embedder", "embedder_identity_mismatch",
                    f'الفهرسُ بُني بـ{meta["identity"]} لا بهذا المُضمِّن')
        if not isinstance(query, str) or not query.strip():
            _refuse("query", "query_empty", "استعلامٌ غير فارغ مطلوب")
        if type(limit) is not int or not 1 <= limit <= 200:
            _refuse("limit", "limit_invalid", "حدٌّ بين ١ و٢٠٠")
        vector = _unit(embedder.embed([query[:MAX_PASSAGE_CHARS]])[0])
        if len(vector) != meta["dim"]:
            _refuse("embedder", "embedder_dim_inconsistent", "بُعدُ الاستعلام لا يطابق الفهرس")
        con = sqlite3.connect(self.path)
        try:
            rows = con.execute("SELECT passage_id, source, locus, part, digest, embedding FROM passages").fetchall()
        finally:
            con.close()
        scored = []
        for passage_id, source, locus, part, digest, blob in rows:
            stored = array("f")
            stored.frombytes(blob)
            similarity = sum(a * b for a, b in zip(vector, stored))
            scored.append((-similarity, passage_id, source, locus, part, digest))
        scored.sort()
        return [{"passage_id": pid, "source": source, "locus": locus, "part": part,
                 "item_digest": digest, "similarity": round(-neg, 6)}
                for neg, pid, source, locus, part, digest in scored[:limit]]
