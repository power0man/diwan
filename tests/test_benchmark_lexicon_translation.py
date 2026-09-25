"""اختبارات تكامل أذرع القياس لمجالي المعاجم والترجمة."""
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.budget import Budget
from core.ledger import Ledger
from evaluation.benchmark_arms import DiwanFullArm, NaiveRagArm
from providers.echo import EchoProvider
from tests.private_stores import needs_lexicons


@needs_lexicons
def test_naive_rag_raw_search_hits():
    # التحقق من أن البحث الساذج يسترجع مقاطع فعلية من الفهرس ولا يعيد قائمة فارغة
    budget = Budget(100, 100)
    ledger = Ledger(ROOT / "var" / "test_naive_ledger.jsonl")
    arm = NaiveRagArm(ROOT, EchoProvider(), budget, ledger)

    # بحث بحري
    hits_maritime = arm._raw_search("ما هي شروط السلامة للسفن؟", "maritime")
    assert len(hits_maritime) > 0

    # بحث معجمي
    hits_lexicon = arm._raw_search("ما معنى الصابورة في المعجم؟", "lexicon")
    assert len(hits_lexicon) > 0


@needs_lexicons
def test_diwan_full_lexicon_arm(tmp_path):
    budget = Budget(100, 100)
    ledger = Ledger(tmp_path / "test_full_ledger.jsonl")
    arm = DiwanFullArm(ROOT, EchoProvider(), budget, ledger)

    case = {"domain": "lexicon", "case_id": "test_lex_01"}
    res = arm.run("ما معنى الصابورة في المعجم الوسيط؟", case)

    assert res["arm"] == "diwan_full"
    assert res["error_code"] is None
    assert "[ش1]" in res["answer"]
    assert "الصابورة" in res["answer"]
    assert res["pages_by_ref"] is not None
    assert 1 in res["pages_by_ref"]
    assert "ج1 ص506" in res["pages_by_ref"][1]["locus"]
