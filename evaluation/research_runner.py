"""مُشغِّلُ بنك البحث المعمّق (ك٥٣): كلُّ سؤالٍ جولةٌ وكيلة بأداة `web_search` المحكومة على المحرّك الثابت.

- **الطريقُ طريقُ المنتج:** الحلقةُ الوكيلة نفسُها، ومخزنُ الأفعال، وأداةُ البحث بعقدها وحَجرها. والفرقُ الوحيد
  أن المحرّك ثابتٌ على متن البنك لا SearXNG. فالرقمُ يقيس انضباطَ البحث والإسناد، لا جودةَ محرّك الويب.
- **ما يُسجَّل لكل سؤال:** الجوابُ، والعناوينُ التي أعادها البحث، والاستعلامات. فيُعاد حسابُ الرقم من التقرير
  وحده (`rescore`)، ولا يطابقه رقمٌ عُدِّل باليد.
- **العطبُ ليس رسوبًا:** مزوّدٌ انقطع أو حلقةٌ رمت استثناءً حالةُ `error` برمزها، وتخرج من المقام. والعتبةُ لا
  تُعدّ مستوفاةً ما بقي عطب.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tempfile

from agent.actions import ActionStore
from agent.journal import Journal
from agent.loop import run_agent
from agent.registry import ToolContext, ToolRegistry
from agent.research import RESEARCH_SYSTEM, returned_urls
from agent.web_search import web_search_tool
from core.budget import Budget
from core.ledger import Ledger
from evaluation.research_bank import CORPUS, SUITE, FixtureSearch, load, score_item, summarize

RUNNER_VERSION = 1
MAX_ANSWER_CHARS = 6000
LIMITS = [
    "measures_search_and_citation_discipline_on_a_fixed_fictional_corpus_not_web_search_quality",
    "fixture_engine_is_bm25_over_the_corpus_not_searxng",
    "citation_check_judges_form_and_returned_sources_facts_are_matched_by_value_in_citing_sentences",
    "claims_are_sentences_with_a_digit_or_four_or_more_words_shorter_ones_pass_uncited",
    "numbers_must_be_written_in_full_digits",
    "single_attempt_per_question_no_variance_estimate",
    "bank_authored_by_a_developer_family_not_blind",
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_item(item: dict, provider, search, *, model: str, model_version: str, max_steps: int = 8,
             deadline_s: float = 180.0, max_output: int = 2048) -> dict:
    base = {"id": item["id"], "category": item["category"]}
    scratch = Path(tempfile.mkdtemp(prefix="diwan-research-")).resolve()
    try:
        workspace = scratch / "workspace"
        workspace.mkdir()
        context = ToolContext(workspace, Journal(workspace), frozenset({"auto"}))
        try:
            run = run_agent(item["question"], provider, ToolRegistry(web_search_tool(search)), context,
                            ledger=Ledger(scratch / "ledger.jsonl"), budget=Budget(0, 0), model=model,
                            model_version=model_version, max_steps=max_steps, max_output=max_output,
                            deadline_s=deadline_s, system=RESEARCH_SYSTEM,
                            action_store=ActionStore(scratch / "control", workspace),
                            session_id="research", turn_id=item["id"])
        except Exception as exc:                       # عطبُ بنيةٍ لا فشلُ قدرة
            return {**base, "status": "error", "code": "loop_raised",
                    "detail": f"{type(exc).__name__}: {str(exc)[:300]}"}
        if run.status in ("refused", "failed"):
            return {**base, "status": "error", "code": run.code or run.status}
        urls = returned_urls(run.steps)
        queries = [call.arguments.get("query") for step in run.steps for call in step.tool_calls
                   if call.name == "web_search"]
        return {**base, "status": "measured", "loop_status": run.status, "steps": len(run.steps),
                "queries": queries[:20], "returned_urls": urls, "answer": run.answer[:MAX_ANSWER_CHARS],
                **score_item(item, run.answer[:MAX_ANSWER_CHARS], urls)}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def run_bank(provider, *, model: str, model_version: str, **options) -> dict:
    suite, corpus, meta = load()
    search = FixtureSearch(corpus)
    results = [run_item(item, provider, search, model=model, model_version=model_version, **options)
               for item in suite["questions"]]
    config = {"runner_version": RUNNER_VERSION, "suite_id": suite["suite_id"],
              "suite_sha256": _sha(SUITE), "corpus_sha256": _sha(CORPUS),
              "prompt_sha256": hashlib.sha256(RESEARCH_SYSTEM.encode("utf-8")).hexdigest(),
              "model": model, "model_version": model_version, "search": search.identity(),
              "options": dict(options)}
    return {"schema_version": 1, "kind": "research_report", "config": config,
            "summary": summarize(results, meta["thresholds"]), "results": results, "measurement_limits": LIMITS}


class BankChanged(ValueError):
    pass


def rescore(report: dict) -> dict:
    """الرقمُ من الأجوبة والعناوين المسجَّلة وحدها، على البنك المجمَّد نفسِه."""
    if report["config"]["suite_sha256"] != _sha(SUITE) or report["config"]["corpus_sha256"] != _sha(CORPUS):
        raise BankChanged("البنكُ أو متنُه تغيّر منذ التقرير")
    suite, _, meta = load()
    by_id = {item["id"]: item for item in suite["questions"]}
    results = []
    for row in report["results"]:
        if row["status"] != "measured":
            results.append(row)
            continue
        results.append({**row, **score_item(by_id[row["id"]], row["answer"], row["returned_urls"])})
    return summarize(results, meta["thresholds"])
