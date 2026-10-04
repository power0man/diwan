"""تشخيصُ هبوط الهجين في غ٣ (#287): يعيد القناتين المسجَّلتين بلا مُضمِّن، ويحاكي دمجَ المنتج."""
from __future__ import annotations

import copy
import json

import pytest

from evaluation import retrieval_general as rg
from tools import diagnose_hybrid as dh
from tools import probe_evidence


@pytest.fixture(scope="module")
def bank():
    return rg.load_bank()


@pytest.fixture(scope="module")
def report(bank):
    return dh.load_report(dh.REPORT, bank)


def test_the_replay_reproduces_the_three_recorded_g3_arms(report, bank):
    """الإعادةُ أمينة: الأذرعُ الثلاث من القناتين المسجَّلتين تطابق ما نشره تقريرُ غ٣، فما يُبنى عليها يخصّ القياسَ نفسَه."""
    variants = dh.diagnose(report, bank)["variants"]
    arms = report["arms"]
    assert variants["vectors_only"]["hit_at_5"] == arms["vectors"]["overall"]["hit_at_5"]
    assert variants["bm25_only"]["hit_at_5"] == arms["bm25"]["overall"]["hit_at_5"]
    assert variants["g3_harness_rrf"]["hit_at_5"] == arms["hybrid"]["overall"]["hit_at_5"]
    assert variants["g3_harness_rrf"]["by_type"]["paraphrase"] == arms["hybrid"]["by_type"]["paraphrase"]["hit_at_5"]


def test_the_mechanism_is_double_credit_on_paraphrases(report, bank):
    """ما أخرج الذهبيَّ من الخمسة الأولى: مقاطعُ خاطئة تأخذ حدَّي RRF من القناتين، والذهبيُّ المعاد صياغتُه غائبٌ عن BM25."""
    mechanism = dh.diagnose(report, bank)["mechanism"]
    assert mechanism["vector_list_depth"] == 50 and mechanism["corpus_passages"] == 60
    assert mechanism["queries_lost_by_fusion"] == 19
    assert mechanism["queries_lost_by_type"] == {"lexical": 1, "paraphrase": 18}
    assert mechanism["lost_queries_with_the_gold_absent_from_bm25"] == 16
    assert mechanism["median_passages_above_the_gold_credited_by_both_channels"] == 14


class _StubBm25:
    """قوائمُ مصطنعة: الصارمةُ بحسب عددٍ يختاره الاختبار، وأيُّ كلمةٍ ثابتة."""

    def __init__(self, exact):
        self.exact = exact

    def search(self, query, depth, *, match_any):
        return (["c", "d"] if match_any else list(self.exact))[:depth]


def test_product_like_mirrors_the_hybrid_retriever_fusion():
    """كـ`HybridRetriever.search`: أيُّ كلمةٍ بنصف الوزن ولا تدخل إلا إن قلّت الصارمةُ عن الحدّ، والمتّجهاتُ بعمق ضعف الحدّ.
    وكلماتُ الاستعلام حرفٌ واحد فلا مفرداتَ صرفية."""
    vectors = ["d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "v11", "v12"]
    fused = dh.product_like("ب ت", vectors, _StubBm25(["a", "b"]))
    # d: أيُّ كلمة (0.5/62) والمتّجهات (1/61)؛ ثم a وb من الصارمة، وe يعادل b فيتأخّر عنه
    assert fused[:4] == ["d", "a", "b", "e"]
    assert "v11" not in fused and "v12" not in fused
    full = dh.product_like("ب ت", vectors, _StubBm25(["a", "b", "x", "y", "z"]))
    assert "c" not in full


def test_a_report_on_another_bank_or_whose_channels_do_not_reproduce_it_is_refused(tmp_path, report, bank):
    other = copy.deepcopy(report)
    other["config"]["bank_sha256"] = "0" * 64
    (tmp_path / "other.json").write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(dh.DiagnosisRefused) as refused:
        dh.load_report(tmp_path / "other.json", bank)
    assert refused.value.code == "g3_report_bank_not_frozen"
    # قناةُ متّجهاتٍ مُبدَلة لاستعلامٍ أصابه الهجين تُخرج الذهبيَّ فلا تعيد الذراعَ المسجَّل
    tampered = copy.deepcopy(report)
    hit = next(row["id"] for row in report["rows"]["hybrid"] if row["hit_at_5"])
    gold = next(q["relevant"][0] for q in bank["queries"] if q["id"] == hit)
    others = [d["id"] for d in bank["documents"] if d["id"] != gold]
    tampered["channels"][hit] = {"bm25": "", "vectors": " ".join(others)}
    (tmp_path / "tampered.json").write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(dh.DiagnosisRefused) as refused:
        dh.load_report(tmp_path / "tampered.json", bank)
    assert refused.value.code == "g3_report_channels_do_not_reproduce_its_hybrid_arm"


def test_the_cli_writes_evidence_the_probe_guard_accepts(tmp_path, capsys):
    out = tmp_path / "g3-hybrid-diagnosis-test.json"
    assert dh.main(["--agent", "anthropic/claude-opus-5-5", "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["agent"] == "anthropic/claude-opus-5-5" and payload["measurement_limits"] == dh.LIMITS
    assert not probe_evidence.validate_payload(payload, available_names={out.name}, current_name=out.name)
    assert dh.main(["--agent", "x", "--report", str(tmp_path / "missing.json")]) == 2
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["code"] == "g3_report_unreadable"
