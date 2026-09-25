#!/usr/bin/env python3
"""قبول مصطنع لمرحلة م١٥ — سد ما يكشفه البنك (ترقية سلم الاسترجاع وإلزام الاكتمال)."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.budget import Budget
from core.ledger import Ledger
from nodes.maritime.node import MaritimeNode, _extract_queries, _strip_waw, SYSTEM
from providers.echo import EchoProvider

CATALOG = ROOT / "corpus" / "maritime" / "_catalog.jsonl"


def _doc(prefix: str) -> str:
    """معرّفُ الوثيقة الوحيدة في الفهرس التي تبدأ بهذا الرقم — لا يُكتب عنوانُ لائحةٍ هنا (ك٢٧)."""
    from core.corpus import CorpusCatalog
    matches = [d for d in CorpusCatalog(CATALOG, create=False).current() if d.startswith(prefix)]
    assert len(matches) == 1, matches
    return matches[0]


def _title(doc_id: str) -> str:
    return doc_id.split("__", 1)[1].replace("-", " ")


def run_acceptance_m15(tmp_dir: Path) -> dict:
    if not CATALOG.exists():
        # اللقطةُ العامة بلا متن: «تعذّر» لا «فشل» ولا «نجاح»
        print(json.dumps({"status": "unavailable", "code": "corpus_missing",
                          "reason": "المتنُ خاصٌّ خارج اللقطة العامة (ك٢٧)"}, ensure_ascii=False))
        raise SystemExit(3)
    # ١. فحص تجريد واو العطف التلقائي مع حماية الكلمات الأصيلة
    assert _strip_waw("ووحدة") == "وحدة"
    assert _strip_waw("وسفينة") == "سفينة"
    assert _strip_waw("وقيد") == "قيد"
    assert _strip_waw("وحدة") == "وحدة"
    assert _strip_waw("وزارة") == "وزارة"
    assert _strip_waw("وثيقة") == "وثيقة"

    # ٢. فحص استخراج استعلامات السلم المتدرجة
    q1 = f"ما الفرق بين سفينة الصيد ووحدة الصيد في {_title(_doc('002__'))} من حيث الحمولة الكلية والطول؟"
    queries = _extract_queries(q1)
    assert any("سفينة الصيد" in q for q in queries)
    assert not any("الفرق" in q.split() for q in queries)
    assert not any("حيث" in q.split() for q in queries)

    # ٣. فحص استرجاع الشواهد وتنويع الوثائق
    node = MaritimeNode(
        ROOT, EchoProvider(), Budget(100, 100),
        Ledger(tmp_dir / "acc_m15_ledger.jsonl")
    )
    pages = node._evidence(q1)
    assert len(pages) > 0
    # التحقق من تصدر اللائحة الأصلية
    assert pages[0]["item"]["domain"] == "maritime-regulation"
    docs = [p["doc_id"] for p in pages]
    assert _doc("002__") in docs

    # ٤. فحص صياغة إلزام الاكتمال في SYSTEM prompt
    assert "إجابة وافية ومكتملة" in SYSTEM
    assert "كافة شقوق السؤال" in SYSTEM

    return {
        "status": "passed",
        "checks": {
            "waw_stripping_safe": True,
            "queries_ladder_extracted": True,
            "evidence_regulations_prioritized": True,
            "target_regulation_hit": True,
            "completeness_prompt_enforced": True,
        }
    }


def main() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        res = run_acceptance_m15(Path(td))
        print("اجتياز قبول م١٥ المصطنع:", json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
