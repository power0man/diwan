"""الأذرع الثلاثة لبنك الجودة المقيس م١٤:
1. الذراع الأول: النموذج وحده (Model Alone) — بلا استرجاع.
2. الذراع الثاني: استرجاع ساذج (Naive RAG) — بحث FTS خام وإلحاق النصوص مباشرة بلا حوكمة.
3. الذراع الثالث: ديوان كاملاً (Diwan Full) — تنقيح الكلمات، والشواهد المعقمة، وحلقة النداء المحكومة، وفحص الإسناد الصارم (ق٢٦)، وفرض المسرد.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from rebuild_index import normalize

from core.budget import Budget
from core.canonical import PayloadRejected
from core.contracts import Message, Request
from core.corpus import CorpusCatalog, CorpusFile
from core.ledger import Ledger
from core.run import execute
from nodes.linguistics.node import LinguisticsNode
from nodes.maritime.node import INSUFFICIENT, MaritimeNode


class NaiveSearchFailed(PayloadRejected):
    """عطبُ استرجاعٍ في الذراع الساذج: يُسمّى برمزه ولا يُقرأ «لا مستندات» (ك٢١)."""


def _verdict(answer: str, error_code: str | None, *refusal_marks: str) -> bool:
    """الامتناعُ قرارُ النظام لا عطبُه (ك٢١): بلا رمزِ عطبٍ، وجوابٌ فارغ أو يعلن العجز.

    كان `abstained = bool(error_code or …)` فيدخل انقطاعُ المزوّد ومهلتُه وعطبُ
    الفهرس في معدّل الامتناع «حكمةً». صار العطبُ رمزًا وحده (`error_code`) يخرج من
    المقام، والامتناعُ علامةً وحدها (`abstained`)، ولا يجتمعان في نتيجةٍ واحدة.
    """
    if error_code:
        return False
    text = (answer or "").strip()
    return not text or any(text.startswith(mark) or mark in text for mark in refusal_marks)

SYSTEM_GENERAL = (
    "أنت مساعد عربي عام. أجب عن سؤال المستخدم بدقة ووضوح بالعربية الفصحى. "
    "صرّح بما لا تعرفه ولا تختلق معلومات."
)

SYSTEM_NAIVE = (
    "أنت مساعد يجيب عن الأسئلة بناءً على المستندات المعطاة. "
    "استعن بالمستندات المرفقة أدناه للإجابة عن السؤال بوضوح وأمانة."
)


class ModelAloneArm:
    """الذراع الأول: النموذج وحده — يقيس المعرفة الكامنة في أوزان النموذج المجرد."""

    def __init__(self, provider, budget: Budget, ledger: Ledger):
        self.provider = provider
        self.budget = budget
        self.ledger = ledger

    def run(self, question: str, case: dict) -> dict[str, Any]:
        t0 = time.time()
        key = "bm-alone-" + hashlib.sha256((self.provider.model + "|" + question).encode()).hexdigest()[:20]
        req = Request(
            messages=(
                Message("system", SYSTEM_GENERAL),
                Message("user", question),
            ),
            model=self.provider.model,
            model_version="benchmark",
            max_output=700,
            deadline_s=120.0,
            data_policy="internal",
            idempotency_key=key,
        )
        outcome = execute(req, self.provider, self.budget, self.ledger)
        latency = round(time.time() - t0, 3)
        answer = outcome.response.content if outcome.response else ""
        abstained = _verdict(answer, outcome.error_code, "تعذر")

        return {
            "arm": "model_alone",
            "answer": answer,
            "pages_by_ref": None,
            "latency_s": latency,
            "error_code": outcome.error_code,
            "abstained": abstained,
        }


class NaiveRagArm:
    """الذراع الثاني: استرجاع ساذج — يبحث بنص السؤال الخام في FTS دون تنقيح ويلصق النصوص."""

    def __init__(self, root: Path, provider, budget: Budget, ledger: Ledger, top_k: int = 3):
        self.root = root
        self.provider = provider
        self.budget = budget
        self.ledger = ledger
        self.top_k = top_k
        self.maritime_db = root / "projections" / "maritime-fts.sqlite"
        self.lexicon_db = root / "projections" / "lexicons-fts.sqlite"

    def _raw_search(self, query: str, domain: str) -> list[str]:
        db_path = self.lexicon_db if domain == "lexicon" else self.maritime_db
        if not db_path.exists():
            raise NaiveSearchFailed("naive_rag.index", "naive_index_missing",
                                    f"لا إسقاطَ بحثٍ للمجال {domain!r}: {db_path.name}")

        # استخراج كلمات السؤال الخام دون تنقيح حوكمة مع التطبيع
        clean = re.sub(r"[^\w\s]", " ", query)
        tokens = [normalize(w) for w in clean.split() if len(w) >= 2]
        if not tokens:
            return []
        match_query = " OR ".join(tokens[:5])

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT norm FROM pages WHERE pages MATCH ? LIMIT ?",
                (match_query, self.top_k),
            )
            rows = cursor.fetchall()
            conn.close()
            return [r[0] for r in rows]
        except Exception as exc:
            # عطبُ الفهرس أو الاستعلام ليس «لا مستندات»: يُرفع برمزه فيُقيَّد خطأً لا امتناعًا
            raise NaiveSearchFailed("naive_rag.search", f"naive_search_failed:{type(exc).__name__}",
                                    str(exc)[:200]) from exc

    def run(self, question: str, case: dict) -> dict[str, Any]:
        t0 = time.time()
        domain = case.get("domain", "maritime")
        try:
            snippets = self._raw_search(question, domain)
        except NaiveSearchFailed as exc:
            # بلا استرجاعٍ لا ذراعَ ساذجة: لا يُسأل النموذجُ بلا مستندات ثم يُحسب جوابُه
            return {"arm": "naive_rag", "answer": "", "pages_by_ref": None,
                    "latency_s": round(time.time() - t0, 3),
                    "error_code": exc.code, "abstained": False}

        if snippets:
            docs_text = "\n\n---\n\n".join(snippets)
            prompt = f"المستندات المسترجعة:\n{docs_text}\n\nالسؤال: {question}\n\nأجب بدقة من واقع المستندات."
        else:
            prompt = f"السؤال: {question}\n\nأجب بدقة ووضوح."

        key = "bm-naive-" + hashlib.sha256((self.provider.model + "|" + question).encode()).hexdigest()[:20]
        req = Request(
            messages=(
                Message("system", SYSTEM_NAIVE),
                Message("user", prompt),
            ),
            model=self.provider.model,
            model_version="benchmark",
            max_output=700,
            deadline_s=120.0,
            data_policy="internal",
            idempotency_key=key,
        )
        outcome = execute(req, self.provider, self.budget, self.ledger)
        latency = round(time.time() - t0, 3)
        answer = outcome.response.content if outcome.response else ""
        abstained = _verdict(answer, outcome.error_code, "تعذر")

        # الشواهد في الاسترجاع الساذج غير مرقمة كعقد، لكننا نسجلها للمقارنة
        pages_by_ref = {
            i: {"text": snip, "part": "naive_doc", "locus": f"doc_{i}"}
            for i, snip in enumerate(snippets, 1)
        } if snippets else None

        return {
            "arm": "naive_rag",
            "answer": answer,
            "pages_by_ref": pages_by_ref,
            "latency_s": latency,
            "error_code": outcome.error_code,
            "abstained": abstained,
        }


class DiwanFullArm:
    """الذراع الثالث: ديوان كاملاً — استرجاع محكوم، وشواهد معقمة، وفحص إسناد ق٢٦، وفرض المسرد."""

    def __init__(self, root: Path, provider, budget: Budget, ledger: Ledger):
        self.root = root
        self.provider = provider
        self.budget = budget
        self.ledger = ledger
        self.maritime_node = MaritimeNode(root, provider, budget, ledger)
        self.ling_node = LinguisticsNode(root, provider, budget, ledger)

    def run(self, question: str, case: dict) -> dict[str, Any]:
        t0 = time.time()
        domain = case.get("domain", "maritime")

        try:
            if domain == "lexicon":
                exp = self.ling_node.explain(question)
                answer = exp["answer_text"]
                pages_by_ref = {
                    1: {
                        "text": exp["evidence"][0]["item"]["text"],
                        "part": exp["evidence"][0]["item"]["part"],
                        "locus": exp["evidence"][0]["item"]["locus"],
                    }
                }
            elif domain == "translation":
                # استخلاص النص الأجنبي من بين علامات التنصيص إن وجد
                m = re.search(r"'(.*?)'|\"(.*?)\"", question)
                foreign_text = (m.group(1) or m.group(2)) if m else question
                res = self.ling_node.translate(
                    foreign_text,
                    source_id="un-treaties-cn",
                    locus="benchmark",
                    part="terms",
                )
                answer = res["item"].text
                pages_by_ref = None  # الترجمة ببوابة مسرد لا شواهد لوائح
            else:
                # حوكمة بحرية كاملة
                res = self.maritime_node.answer(question, data_policy="internal")
                answer = res["answer"].text
                pages = res.get("evidence", [])
                pages_by_ref = {
                    i: {"text": p["item"]["text"], "part": p["item"]["part"], "locus": p["item"]["locus"]}
                    for i, p in enumerate(pages, 1)
                }
            error_code = None
        except LookupError as exc:
            # غيابُ معرفةٍ لا عطب (قاعدةُ services/research.py): امتناعٌ مُعلَن
            answer = f"{INSUFFICIENT}: {exc}"
            pages_by_ref = None
            error_code = None
        except Exception as exc:
            answer = f"تعذر إكمال طلب ديوان: {exc}"
            pages_by_ref = None
            error_code = getattr(exc, "code", str(type(exc).__name__))

        latency = round(time.time() - t0, 3)
        abstained = _verdict(answer, error_code, INSUFFICIENT)
        return {
            "arm": "diwan_full",
            "answer": answer,
            "pages_by_ref": pages_by_ref,
            "latency_s": latency,
            "error_code": error_code,
            "abstained": abstained,
        }
