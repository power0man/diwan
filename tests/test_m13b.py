"""ك٤٩ (سياسات): م١٣-ب — العقدةُ مقابل المركز بالاسترجاع نفسِه، والقرارُ من بروتوكولٍ مسجَّل.

- ذراعُ المركز طريقُ المنتج الوكيل: رسالةٌ محجورة، وأدواتُ المنتج، وأداةُ استرجاعٍ مرقّمة، وسطرُ إسنادٍ واحد.
- الحكمُ لكل حالةٍ بمقاييس م١٤، والقرارُ من فرقَي الصحّة والإسناد، و«ناقصُ القوة» دون أصغر عيّنة.
- الملخّصُ المنشور أعدادٌ وقرار، لا جوابَ فيه ولا نصَّ من المتن.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent.registry import ToolRefused
from core.budget import Budget
from core.contracts import Response, ToolCall, Usage
from core.ledger import Ledger
from evaluation import m13b
from evaluation.benchmark_arms import CENTER_CITATION, CenterRetrievalArm, numbered_search_tool
from services.agent_workspace import decode_input

ROOT = Path(__file__).resolve().parents[1]
PAGES = [{"text": "يجب على الربان إبلاغ السلطة خلال ٢٤ ساعة.", "part": "نظام", "locus": "م٥"},
         {"text": "تُحفظ السجلات سنتين.", "part": "لائحة", "locus": "م٩"}]


def fake_search(query, limit):
    return PAGES[:limit]


def test_the_search_tool_numbers_each_passage_once_per_turn():
    pages = {}
    tool = numbered_search_tool(fake_search, pages)
    first = tool.run({"query": "الإبلاغ", "limit": 2}, None)["content"]
    again = tool.run({"query": "الإبلاغ", "limit": 1}, None)["content"]
    assert "[ش1]" in first and "[ش2]" in first and again.startswith("[ش1]") and len(pages) == 2
    assert pages[1] == PAGES[0]
    for bad in ({"query": ""}, {"query": "x", "limit": 0}, {"query": "x", "limit": 9}, {"query": 3}):
        with pytest.raises(ToolRefused):
            tool.run(bad, None)


class Replay:
    name, is_local, model = "replay", True, "replay"

    def __init__(self, final):
        self.final, self.requests = final, []

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.requests.append(request)
        if not any(m.role == "tool" for m in request.messages):
            return Response("", Usage(1, 1), "complete", 0, provider="replay", model_version="v1",
                            tool_calls=(ToolCall("s1", "search_regulations", {"query": "مهلة الإبلاغ"}),))
        return Response(self.final, Usage(1, 1), "complete", 0, provider="replay", model_version="v1")


def _center(tmp_path, final):
    provider = Replay(final)
    arm = CenterRetrievalArm(provider, Budget(0, 0), Ledger(tmp_path / "ledger.jsonl"), search=fake_search)
    return arm, provider


def test_the_center_arm_is_the_product_agent_path_with_numbered_retrieval(tmp_path):
    arm, provider = _center(tmp_path, "يجب الإبلاغ خلال ٢٤ ساعة [ش1].")
    out = arm.run("متى يجب الإبلاغ؟", {"case_id": "m1"})
    assert out["arm"] == "center_retrieval" and not out["abstained"] and out["error_code"] is None
    assert out["answer"] == "يجب الإبلاغ خلال ٢٤ ساعة [ش1]." and out["pages_by_ref"][1] == PAGES[0]
    request = provider.requests[0]
    assert request.messages[0].content.endswith(CENTER_CITATION)
    assert decode_input(request.messages[-1].content)["user_request"] == "متى يجب الإبلاغ؟"
    assert "search_regulations" in {spec.name for spec in request.tools}


def test_insufficient_evidence_is_an_abstention_and_a_broken_provider_an_error(tmp_path):
    arm, _ = _center(tmp_path, "الشواهد غير كافية.")
    assert arm.run("سؤال", {"case_id": "m2"})["abstained"] is True

    class Broken(Replay):
        def complete(self, request):
            raise RuntimeError("boom")
    broken = CenterRetrievalArm(Broken(""), Budget(0, 0), Ledger(tmp_path / "l2.jsonl"), search=fake_search)
    out = broken.run("سؤال", {"case_id": "m3"})
    assert out["error_code"] and out["abstained"] is False


def _metrics(**kw):
    return {"errored": False, "abstained": False, "overall_score": 0.7, "attribution": {"score": 1.0}, **kw}


def test_a_case_passes_at_the_registered_floor_and_is_attributed_only_when_fully(monkeypatch):
    seen = iter([_metrics(), _metrics(overall_score=0.59), _metrics(attribution={"score": 0.9}),
                 _metrics(abstained=True, overall_score=None, attribution=None),
                 {"errored": True, "error_code": "http_500"}])
    monkeypatch.setattr(m13b, "evaluate_case_response", lambda *a, **k: next(seen))
    out = {"answer": "x", "pages_by_ref": None}
    rows = [m13b.score_row({"case_id": f"c{i}"}, out, pass_floor=0.6) for i in range(5)]
    assert [r.get("passed") for r in rows] == [True, False, True, False, None]
    assert [r.get("attributed") for r in rows] == [True, True, False, False, None]
    assert rows[4]["status"] == "error" and rows[4]["code"] == "http_500"


def rule_min_effect():
    return m13b.protocol()["rule"]["min_effect"]


def _rows(outcomes):
    return [{"id": f"c{i}", "category": "maritime", "status": "measured", "passed": p, "attributed": a,
             "abstained": False, "answer": "نصٌّ من اللائحة"} for i, (p, a) in enumerate(outcomes)]


def _pairs(node_pc, center_pc, node_ac=None, center_ac=None):
    node_ac = node_pc if node_ac is None else node_ac
    center_ac = center_pc if center_ac is None else center_ac
    return _rows(zip(node_pc, node_ac)), _rows(zip(center_pc, center_ac))


def test_the_decision_reads_correctness_and_attribution():
    n = 200
    same = [True] * 120 + [False] * 80
    better = [True] * 150 + [False] * 50
    assert m13b.judge(*_pairs(same[:50], same[:50]))["decision"] == "underpowered"
    assert m13b.judge(*_pairs(better, same))["decision"] == "keep"
    only_attr = m13b.judge(*_pairs(same, same, better, same))
    assert only_attr["decision"] == "keep" and only_attr["answers"]["effect"] == 0.0
    assert only_attr["reason"] == "node_adds_correctness_or_attribution"
    assert m13b.judge(*_pairs(same, better))["decision"] == "remove"
    one_more = [True] * 121 + [False] * 79                              # فرقُ حالةٍ واحدة: دون الأثر المسجَّل
    assert m13b.judge(*_pairs(one_more, same))["decision"] == "remove"
    node = [True] * 10 + [False] * 6 + [True] * 104 + [False] * 80
    center = [False] * 10 + [True] * 6 + [True] * 104 + [False] * 80
    tie = m13b.judge(*_pairs(node, center))                              # المجالُ يعبر الصفرَ والأثرَ معًا
    assert tie["answers"]["ci95"][0] < 0 < rule_min_effect() < tie["answers"]["ci95"][1]
    assert tie["decision"] == "keep" and tie["reason"] == "inconclusive_registered_default"
    open_attribution = m13b.judge(*_pairs(same, same, node, center))  # الصحّةُ محسومة والإسنادُ لا: لا حذف
    assert open_attribution["answers"]["ci95"][1] < rule_min_effect() < open_attribution["attribution"]["ci95"][1]
    assert open_attribution["decision"] == "keep"
    assert len(same) == len(better) == n


PROTOCOL = ROOT / "evaluation" / "protocols" / "m13b_v1.json"


def test_the_protocol_is_registered_before_the_run():
    assert hashlib.sha256(PROTOCOL.read_bytes()).hexdigest() == \
        "fe56b3b6022e8cae0c4edc33d0f990493e9c1ef9d99e496ae0fcabe8cf4beec8"
    data = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    assert data["status"] == "registered_not_run" and data["node"] == "policies"
    assert set(data["decisions"]) == {"keep", "remove"} and data["pass_floor"] == 0.6
    assert m13b.min_items(data["rule"]["power_effect"], data["assumed_discordance"]) == 116


def test_the_published_summary_carries_no_answer_and_no_corpus_text():
    import sys
    sys.path.insert(0, str(ROOT))
    from tools.evaluate_m13b import public_summary
    node, center = _pairs([True] * 130, [False] * 130)
    verdict = m13b.judge(node, center)
    summary = public_summary({"node": node, "center": center}, verdict, {"model": "replay"})
    text = json.dumps(summary, ensure_ascii=False)
    assert "نصٌّ من اللائحة" not in text and '"answer"' not in text
    assert summary["counts"]["node"]["passed"] == 130 and summary["judgment"]["decision"] == "keep"


def test_run_needs_exactly_the_two_arms():
    with pytest.raises(ValueError):
        m13b.run([], {"node": object()}, pass_floor=0.6)
