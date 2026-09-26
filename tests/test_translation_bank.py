"""غ٤: بنكُ الترجمة مجمَّدٌ بعتباته، وكلُّ حالةٍ تميّز الترجمةَ الصحيحة من القريبة الخاطئة.

- الملفّان المودَعان هما ما يولّده `tools/make_translation_bank.py` بايتًا ببايت.
- كلُّ ترجمةٍ مرجعية تمرّ، وكلُّ ترجمةٍ قريبةٍ خاطئة تسقط، وهي خطأٌ واحدٌ مسمًّى.
- المُشغِّلُ على طريق وضع الترجمة، والرسالةُ في غلاف المدخل كما في الواجهة: إعادةُ المراجع عبر الحلقة (بعد نداء
  check_translation) تستوفي العتبات، والخاطئةُ لا.
- الرقمُ يُعاد حسابُه من التقرير، ولا يُقبل تقريرٌ على بنكٍ تغيّر. والعطبُ والفئةُ الضعيفة يمنعان الاستيفاء.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from agent.translation import TRANSLATE_SYSTEM, split_request, target_of
from services.agent_workspace import decode_input
from core.contracts import Response, ToolCall, Usage
from evaluation.translation_bank import load, score_item, summarize
from evaluation.translation_runner import BankChanged, rescore, run_bank

ROOT = Path(__file__).resolve().parents[1]
SUITE, META = load()
ITEMS = {item["id"]: item for item in SUITE["items"]}


def test_the_bank_has_sixty_items_in_seven_categories_both_directions():
    counts: dict[str, int] = {}
    for item in SUITE["items"]:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
        assert item["target"] == target_of(item["source"])
    assert counts == {"general": 12, "glossary": 10, "numbers": 10, "entities": 8, "formal": 8, "injection": 6, "ui": 6}
    assert sum(i["target"] == "ar" for i in SUITE["items"]) == 30
    assert all(item["glossary"] for item in SUITE["items"] if item["category"] == "glossary")
    assert META["thresholds"] == {"pass_rate": 0.7, "min_category_pass_rate": 0.5, "check_pass_rate": 0.8}


def test_the_committed_bank_is_exactly_what_the_generator_builds():
    sys.path.insert(0, str(ROOT))
    from tools.make_translation_bank import THRESHOLDS, build
    items, meta = build()
    assert items == SUITE["items"] and meta == META["items"] and THRESHOLDS == META["thresholds"]


@pytest.mark.parametrize("item_id", sorted(ITEMS))
def test_the_reference_passes_and_the_near_miss_fails(item_id):
    item, meta = ITEMS[item_id], META["items"][item_id]
    reference = score_item(item, meta["reference"])
    assert reference["passed"], reference
    decoy = score_item(item, meta["decoy"])
    assert not decoy["passed"], f"{item_id}: الخاطئة ({meta['decoy_note']}) مرّت"


def _says(content, *calls):
    return Response(content, Usage(1, 1), "complete", 0, provider="replay", model_version="v1", tool_calls=tuple(calls))


class Replay:
    """يفحص الترجمةَ بالأداة أولًا ثم يجيب بها، كما تطلب التعليمات."""
    name, is_local = "replay", True

    def __init__(self, field):
        self.by_source = {item["source"]: META["items"][item["id"]][field] for item in SUITE["items"]}
        self.systems = set()

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.systems.update(m.content for m in request.messages if m.role == "system")
        user = decode_input(next(m.content for m in request.messages if m.role == "user"))["user_request"]
        source = split_request(user)[0]
        if not any(m.role == "tool" for m in request.messages):
            return _says("", ToolCall("chk", "check_translation", {"source": source, "translation": self.by_source[source]}))
        return _says(self.by_source[source])


def test_references_through_the_translation_loop_meet_the_thresholds():
    provider = Replay("reference")
    report = run_bank(provider, model="replay", model_version="v1")
    summary = report["summary"]
    assert summary["passed"] == 60 and summary["meets_thresholds"] and summary["errors"] == 0
    assert all(r["self_checks"] == 1 for r in report["results"])
    assert provider.systems == {TRANSLATE_SYSTEM}
    assert rescore(json.loads(json.dumps(report))) == summary


def test_near_misses_through_the_loop_fail_every_item():
    summary = run_bank(Replay("decoy"), model="replay", model_version="v1")["summary"]
    assert summary["passed"] == 0 and not summary["meets_thresholds"]


def test_a_report_on_a_changed_bank_is_refused():
    report = run_bank(Replay("reference"), model="replay", model_version="v1")
    report["config"]["suite_sha256"] = "0" * 64
    with pytest.raises(BankChanged):
        rescore(report)


def test_a_glossary_reaches_the_model_inside_the_request():
    seen = []

    class Spy(Replay):
        def complete(self, request):
            seen.append(decode_input(next(m.content for m in request.messages if m.role == "user"))["user_request"])
            return super().complete(request)
    run_bank(Spy("reference"), model="replay", model_version="v1")
    received = {split_request(text)[0]: split_request(text)[1] for text in seen}
    for item in SUITE["items"]:
        assert received[item["source"]] == [tuple(pair) for pair in item["glossary"]]


def _results(passed_ids, errors=()):
    return [{"id": i["id"], "category": i["category"], "status": "error" if i["id"] in errors else "measured",
             "passed": i["id"] in passed_ids, "check_passed": i["id"] in passed_ids} for i in SUITE["items"]]


def test_errors_and_a_weak_category_each_block_the_thresholds():
    everything = {i["id"] for i in SUITE["items"]}
    assert summarize(_results(everything), META["thresholds"])["meets_thresholds"]
    assert not summarize(_results(everything, errors={"tr01"}), META["thresholds"])["meets_thresholds"]
    injection = {i["id"] for i in SUITE["items"] if i["category"] == "injection"}
    weak = summarize(_results(everything - set(sorted(injection)[:4])), META["thresholds"])
    assert weak["pass_rate"] >= 0.7 and not weak["meets_thresholds"]


def test_the_checker_rate_has_its_own_floor():
    everything = {i["id"] for i in SUITE["items"]}
    rows = _results(everything)
    for row in rows[:13]:                          # ١٣ من ٦٠ لا تجتاز المدقّق: ٠٫٧٨ دون ٠٫٨٠
        row["check_passed"] = False
    assert not summarize(rows, META["thresholds"])["meets_thresholds"]


def test_a_forbidden_phrase_fails_even_when_every_meaning_is_there():
    item = ITEMS["tr03"]
    both = META["items"]["tr03"]["reference"] + " Later they fell."
    result = score_item(item, both)
    assert result["check_passed"] and not result["missing_meaning"] and result["forbidden_found"] == ["fell"]
    assert not result["passed"]


def test_a_refusal_to_translate_a_short_ui_string_is_not_a_translation():
    """ملاحظةُ Codex على #131: المدقّقُ لا يقيس الطولَ لمصدرٍ دون ستِّ كلمات، فحُسب رفضٌ طويلٌ لـ«Save changes» ترجمةً."""
    suite, _ = load()
    item = next(i for i in suite["items"] if i["id"] == "tr55")
    refusal = ("لم أستطع تنفيذ طلب \"حفظ التغييرات\" لأنني مساعد ترجمة ولا أملك صلاحية الوصول إلى ملفات النظام. "
               "إذا كنت تريد مني التحقق من ترجمة نص معين، يرجى تزويدي بالنص الأصلي.")
    scored = score_item(item, refusal)
    assert not scored["passed"] and not scored["check_passed"]
    assert "overlong_for_short_source" in scored["codes"]
    assert "overlong_for_short_source" not in score_item(item, "حفظ التغييرات")["codes"]


def test_the_committed_translation_evidence_is_what_the_scorer_gives_today():
    """الرقمُ المنشور يُعاد من ترجماته المسجَّلة بالمقيّم الحاليّ، فتغييرُ المقيّم بلا إعادة الدليل يُسقط هذا."""
    evidence = json.loads((ROOT / "docs" / "probe" / "g4-translation-20260926.json").read_text(encoding="utf-8"))
    assert rescore(copy.deepcopy(evidence)) == evidence["summary"]
