"""ك٥٣ (#49): بنكُ البحث المعمّق — مجمَّدٌ قبل البناء، وحلولُه المرجعية تنجح، وإفسادُها الصغير يسقط.

- البنكُ ما يولّده مولّدُه بايتًا ببايت، وكلُّ حقيقةٍ فيه في صفحةِ مصدرها، وكلُّ سؤالٍ يُجاب يحتاج صفحتين فأكثر.
- الاستعلاماتُ المرجعية تبلغ المصادرَ المطلوبة من المحرّك الثابت، والأجوبةُ المرجعية تنجح.
- ويسقط الجوابُ إن:
  - نزعنا مراجعَه، أو استشهد بمصدرٍ لم يُعِده البحث (الإجابةُ من الذاكرة)،
  - أو تغيّر رقمٌ فيه، أو وقع في الفخّ، أو سكت عن التعارض.
- والمُشغِّلُ على الطريق الوكيل نفسِه: إعادةُ الحلول المرجعية تستوفي العتبات، ويُعاد حسابُ الرقم من تقريره،
  ولا يطابقه رقمٌ عُدِّل باليد. ومن يجيب من ذاكرته يُحسب مختلِقًا.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agent.research import RESEARCH_SYSTEM
from agent.web_search import MAX_SNIPPET_CHARS
from core.contracts import Response, ToolCall, Usage
from evaluation import research_bank
from evaluation.research_bank import CATEGORIES, FixtureSearch, _has, load, score_item
from evaluation.research_runner import BankChanged, rescore, run_bank

ROOT = Path(__file__).resolve().parents[1]
SUITE, CORPUS, META = load()
QUESTIONS = SUITE["questions"]
PAGES = {page["url"]: page for page in CORPUS}
REFS = META["references"]
SEARCH = FixtureSearch(CORPUS)
ANSWERABLE = [q["id"] for q in QUESTIONS if q["category"] != "unanswerable"]
BY_ID = {q["id"]: q for q in QUESTIONS}


def _returned(item_id):
    return [r["url"] for query in REFS[item_id]["queries"] for r in SEARCH.search(query)]


def test_the_bank_has_thirty_questions_in_five_categories_and_its_thresholds():
    counts = {c: sum(q["category"] == c for q in QUESTIONS) for c in CATEGORIES}
    assert counts == {"two_hop": 8, "compare": 6, "aggregate": 5, "conflict": 5, "unanswerable": 6}
    assert META["thresholds"] == {"pass_rate": 0.6, "min_category_pass_rate": 0.4, "citation_valid_rate": 0.9,
                                  "fabricated_sources": 0}


def test_the_committed_bank_is_exactly_what_the_generator_builds(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import make_research_bank
    suite, corpus, meta = make_research_bank.build()
    for value, name in ((suite, "research_v1.json"), (corpus, "research_v1.corpus.json"), (meta, "research_v1.meta.json")):
        committed = (ROOT / "evaluation" / "suites" / name).read_text(encoding="utf-8")
        assert json.dumps(value, ensure_ascii=False, indent=1) + "\n" == committed, name


def test_every_fact_is_in_its_source_and_answerable_questions_need_two_pages():
    for item in QUESTIONS:
        for fact in item["facts"]:
            assert fact["sources"] and all(url in PAGES for url in fact["sources"])
            if fact["derived"]:                                    # فرقٌ أو مجموعٌ محسوب من مصدرين فأكثر
                assert len(fact["sources"]) >= 2 and isinstance(fact["value"], int)
            else:
                assert any(_has(PAGES[url]["body"], fact["value"]) for url in fact["sources"]), (item["id"], fact)
        sources = {url for fact in item["facts"] for url in fact["sources"]}
        if item["category"] == "unanswerable":
            assert not item["facts"] and item["traps"]
        else:
            assert len(sources) >= 2 and not item["traps"], item["id"]


def test_the_corpus_is_fictional_reserved_and_fits_one_snippet():
    for page in CORPUS:
        assert re.fullmatch(r"https://(mawsoua|akhbar|atlas)\.example/[a-z-]+/[a-z-]+", page["url"])
        assert len(page["body"]) <= MAX_SNIPPET_CHARS              # الصفحةُ كلُّها مقتطفٌ واحد


@pytest.mark.parametrize("item_id", sorted(REFS))
def test_each_reference_reaches_its_sources_and_passes(item_id):
    item = BY_ID[item_id]
    returned = _returned(item_id)
    assert {url for fact in item["facts"] for url in fact["sources"]} <= set(returned)
    scored = score_item(item, REFS[item_id]["answer"], returned)
    assert scored["passed"], scored


def _without_markers(answer):
    body, _, sources = answer.partition("\n\nالمصادر:")
    return re.sub(r"\s*\[\d+\]", "", body) + "\n\nالمصادر:" + sources


def _foreign_source(answer):
    return re.sub(r"\[1\] \S+", "[1] https://mawsoua.example/city/invented", answer, count=1)


def _off_by_one(answer, item):
    value = next(f["value"] for f in item["facts"] if isinstance(f["value"], int))
    return re.sub(rf"(?<!\d){value}(?!\d)", str(value + 1), answer)


@pytest.mark.parametrize("item_id", ANSWERABLE)
@pytest.mark.parametrize("corruption", ["markers", "foreign_source", "off_by_one", "from_memory"])
def test_a_small_corruption_of_an_answer_fails(item_id, corruption):
    item, answer, returned = BY_ID[item_id], REFS[item_id]["answer"], _returned(item_id)
    if corruption == "markers":
        answer = _without_markers(answer)
    elif corruption == "foreign_source":
        answer = _foreign_source(answer)
    elif corruption == "off_by_one":
        answer = _off_by_one(answer, item)
    else:
        returned = []                                              # جوابٌ صحيح لكن بلا بحث
    assert not score_item(item, answer, returned)["passed"]


@pytest.mark.parametrize("item_id", [q["id"] for q in QUESTIONS if q["category"] == "conflict"])
def test_naming_one_side_or_hiding_the_conflict_fails(item_id):
    item, answer, returned = BY_ID[item_id], REFS[item_id]["answer"], _returned(item_id)
    hidden = answer.replace("بينما", "و").replace("فالمصدران يتعارضان في هذه المعلومة", "وهذا ما تذكره المصادر")
    assert not score_item(item, hidden, returned)["passed"]
    one_side = answer.split("، بينما")[0] + ".\n\nالمصادر:" + answer.split("\n\nالمصادر:")[1]
    assert not score_item(item, one_side, returned)["passed"]


@pytest.mark.parametrize("item_id", [q["id"] for q in QUESTIONS if q["category"] == "unanswerable"])
def test_falling_into_the_trap_or_not_admitting_fails(item_id):
    item, returned = BY_ID[item_id], _returned(item_id)
    trap = item["traps"][0]
    trap_page = next(url for url in returned if _has(PAGES[url]["body"], trap["value"]))
    # يُقرّ ثم يقع في الفخّ: الإقرارُ لا يشفع للادّعاء الذي بعده
    trapped = (f"لم أجد ذلك صراحةً في المصادر. لكن {trap['context']} يرتبط بـ {trap['value']} [1]."
               f"\n\nالمصادر:\n[1] {trap_page}")
    assert not score_item(item, trapped, returned)["passed"]
    silent = "هذا سؤالٌ مثيرٌ للاهتمام عن تاريخ المنطقة وأعلامها."
    assert not score_item(item, silent, returned)["passed"]
    # يراوغ: جملةٌ صحيحةٌ مسنَدة من صفحةٍ أعادها البحث، لا تجيب ولا تُقرّ بأنها لا تجيب
    page = next(url for url in returned if not _has(PAGES[url]["body"], trap["value"]))
    sidestep = PAGES[page]["body"].split(".")[0] + f" [1].\n\nالمصادر:\n[1] {page}"
    assert score_item(item, sidestep, returned)["citation_passed"]
    assert not score_item(item, sidestep, returned)["passed"]


def test_the_research_instructions_are_the_registered_ones():
    assert hashlib.sha256(RESEARCH_SYSTEM.encode("utf-8")).hexdigest() == \
        "ead6514c6b5dad08d9f8d9d0e2b6a05c96abb23955d902742a9b1bdcb64861d8"


def test_the_fixture_search_is_deterministic_and_says_what_it_is():
    assert SEARCH.search("سكان مدينة لجين") == SEARCH.search("سكّان مدينةِ لُجين")
    assert SEARCH.identity() == {"backend": "fixture", "corpus_sha256": SEARCH.digest}
    assert SEARCH.search("خنفشار زقزقة") == [] and SEARCH.search("في من على") == []


# ————— المُشغِّل —————

def _says(content, *calls):
    return Response(content, Usage(1, 1), "complete", 0, provider="replay", model_version="v1",
                    tool_calls=tuple(calls))


class Replay:
    """يعيد الحلولَ المرجعية على الطريق الوكيل: استعلامٌ في كل خطوة، ثم الجوابُ المرجعيّ."""
    name, is_local = "replay", True

    def __init__(self, search=True):
        self.search = search

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        task = next(m.content for m in request.messages if m.role == "user")
        item_id = next(q["id"] for q in QUESTIONS if q["question"] in task)
        done = sum(m.role == "tool" for m in request.messages)
        queries = REFS[item_id]["queries"] if self.search else []
        if done < len(queries):
            return _says("", ToolCall(f"call-{done}", "web_search", {"query": queries[done]}))
        return _says(REFS[item_id]["answer"])


@pytest.fixture(scope="module")
def replayed():
    return run_bank(Replay(), model="replay", model_version="v1")


def test_replaying_the_references_through_the_agent_loop_meets_every_threshold(replayed):
    summary = replayed["summary"]
    assert summary["meets"] and summary["pass_rate"] == 1.0 and summary["fabricated_sources"] == 0
    assert all(row["returned_urls"] and row["queries"] for row in replayed["results"] if row["category"] != "unanswerable")
    assert replayed["config"]["prompt_sha256"] == hashlib.sha256(RESEARCH_SYSTEM.encode()).hexdigest()


def test_the_number_is_recomputed_from_the_report_and_a_hand_edit_is_caught(replayed):
    assert rescore(replayed) == replayed["summary"]
    edited = copy.deepcopy(replayed)
    edited["results"][0]["answer"] = "لا أعرف."
    assert rescore(edited) != replayed["summary"]


def test_a_report_on_a_changed_bank_is_refused(replayed):
    changed = copy.deepcopy(replayed)
    changed["config"]["corpus_sha256"] = "0" * 64
    with pytest.raises(BankChanged):
        rescore(changed)


def test_answering_from_memory_is_counted_as_fabrication():
    report = run_bank(Replay(search=False), model="replay", model_version="v1")
    summary = report["summary"]
    assert not summary["meets"] and summary["fabricated_sources"] > 0
    assert summary["by_category"]["unanswerable"]["passed"] == 6        # الإقرارُ لا يحتاج بحثًا


def test_the_cli_refuses_to_overwrite(tmp_path):
    out = tmp_path / "report.json"
    out.write_text("{}")
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "evaluate_research.py"), "--model", "m",
                           "--out", str(out)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 1 and "output_exists" in done.stdout and out.read_text() == "{}"


def _row(category, passed=True, **changes):
    return {"id": "x", "category": category, "status": "measured", "passed": passed, "citation_passed": True,
            "fabricated_sources": 0, **changes}


GOOD_ROWS = [_row(c) for c in CATEGORIES for _ in range(4)]


@pytest.mark.parametrize("change, broken", [
    ("fabricated", lambda rows: rows[:-1] + [_row("unanswerable", fabricated_sources=1)]),
    ("error", lambda rows: rows[:-1] + [{"id": "x", "category": "unanswerable", "status": "error", "code": "loop_raised"}]),
    ("category_floor", lambda rows: [r if r["category"] != "conflict" else _row("conflict", passed=False) for r in rows]),
    ("citations", lambda rows: [{**r, "citation_passed": i % 2 == 0} for i, r in enumerate(rows)]),
])
def test_every_threshold_is_required(change, broken):
    thresholds = META["thresholds"]
    assert research_bank.summarize(GOOD_ROWS, thresholds)["meets"]
    assert not research_bank.summarize(broken(list(GOOD_ROWS)), thresholds)["meets"], change


def test_only_urls_that_a_successful_search_returned_are_citable():
    from types import SimpleNamespace
    from agent.research import returned_urls
    steps = [SimpleNamespace(tool_results=(
        {"name": "web_search", "status": "ok", "results": [{"url": "https://a.example/x/1"}]},
        {"name": "web_search", "status": "refused", "results": [{"url": "https://a.example/x/2"}]},
        {"name": "read_file", "status": "ok", "results": [{"url": "https://a.example/x/3"}]},
        {"name": "web_search", "status": "ok", "results": [{"url": "https://a.example/x/1"}, "bad"]}))]
    assert returned_urls(steps) == ["https://a.example/x/1"]
