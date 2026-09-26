"""مُشغِّلُ بنك الترجمة (غ٤): كلُّ حالةٍ جولةٌ وكيلة بتعليمات الترجمة وأداة `check_translation` وحدها.

- **الطريقُ طريقُ وضع الترجمة:**
  - الحلقةُ الوكيلة نفسُها، والتعليماتُ المسجَّلة ببصمتها.
  - ورسالةُ الجولة كما تبلغ النموذجَ من الواجهة بعينها:
    - النصُّ ثم مسردُه إن كان (`translation_request`).
    - في غلاف المدخل الوكيل (`encode_input`).
    - محجورًا ما يُحجر (`model_facing_input`).
  - فالرقمُ يقيس وضعَ الترجمة كما يراه المستخدم، لا نسخةً أنظفَ منه.
- **ما يُسجَّل لكل حالة:** الترجمةُ وحدها، فيُعاد حسابُ الرقم من التقرير (`rescore`) ولا يطابقه رقمٌ عُدِّل باليد.
- **العطبُ ليس رسوبًا:** حالةُ `error` برمزها تخرج من المقام، والعتبةُ لا تُعدّ مستوفاةً ما بقي عطب.
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
from agent.translation import CHECK_TRANSLATION, TRANSLATE_SYSTEM, translation_request
from services.agent_workspace import encode_input, model_facing_input
from core.budget import Budget
from core.ledger import Ledger
from evaluation.translation_bank import META, SUITE, load, score_item, summarize

RUNNER_VERSION = 1   # الرسالةُ في غلاف المدخل الوكيل كما في الواجهة
MAX_ANSWER_CHARS = 6000
LIMITS = [
    "checks_numbers_tokens_glossary_script_copying_and_length_mechanically_not_style_or_fluency",
    "meaning_is_matched_by_authored_keyword_groups_a_synonym_outside_them_fails",
    "arabic_matching_folds_tashkeel_alef_forms_taa_marbuta_and_the_lam_of_lil_not_full_morphology",
    "direction_is_fixed_arabic_to_english_and_else_to_arabic",
    "single_attempt_per_item_no_variance_estimate",
    "bank_authored_by_a_developer_family_not_blind",
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_item(item: dict, provider, *, model: str, model_version: str, max_steps: int = 4,
             deadline_s: float = 180.0, max_output: int = 1024) -> dict:
    base = {"id": item["id"], "category": item["category"]}
    scratch = Path(tempfile.mkdtemp(prefix="diwan-translation-")).resolve()
    try:
        workspace = scratch / "workspace"
        workspace.mkdir()
        context = ToolContext(workspace, Journal(workspace), frozenset({"auto"}))
        message = model_facing_input(encode_input(
            translation_request(item["source"], [tuple(p) for p in item["glossary"]]), [], None))
        try:
            run = run_agent(message, provider, ToolRegistry(CHECK_TRANSLATION), context,
                            ledger=Ledger(scratch / "ledger.jsonl"), budget=Budget(0, 0), model=model,
                            model_version=model_version, max_steps=max_steps, max_output=max_output,
                            deadline_s=deadline_s, system=TRANSLATE_SYSTEM,
                            action_store=ActionStore(scratch / "control", workspace),
                            session_id="translation", turn_id=item["id"])
        except Exception as exc:                       # عطبُ بنيةٍ لا فشلُ قدرة
            return {**base, "status": "error", "code": "loop_raised",
                    "detail": f"{type(exc).__name__}: {str(exc)[:300]}"}
        if run.status in ("refused", "failed"):
            return {**base, "status": "error", "code": run.code or run.status}
        answer = run.answer[:MAX_ANSWER_CHARS]
        checks = sum(1 for step in run.steps for call in step.tool_calls if call.name == "check_translation")
        return {**base, "status": "measured", "loop_status": run.status, "steps": len(run.steps),
                "self_checks": checks, "translation": answer, **score_item(item, answer)}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def run_bank(provider, *, model: str, model_version: str, **options) -> dict:
    suite, meta = load()
    results = [run_item(item, provider, model=model, model_version=model_version, **options)
               for item in suite["items"]]
    config = {"runner_version": RUNNER_VERSION, "suite_id": suite["suite_id"], "suite_sha256": _sha(SUITE),
              "meta_sha256": _sha(META), "prompt_sha256": hashlib.sha256(TRANSLATE_SYSTEM.encode("utf-8")).hexdigest(),
              "model": model, "model_version": model_version, "options": dict(options)}
    return {"schema_version": 1, "kind": "translation_report", "config": config,
            "summary": summarize(results, meta["thresholds"]), "results": results, "measurement_limits": LIMITS}


class BankChanged(ValueError):
    pass


def rescore(report: dict) -> dict:
    """الرقمُ من الترجمات المسجَّلة وحدها، على البنك المجمَّد نفسِه."""
    if report["config"]["suite_sha256"] != _sha(SUITE) or report["config"]["meta_sha256"] != _sha(META):
        raise BankChanged("البنكُ أو ملفُّه الجانبيُّ تغيّر منذ التقرير")
    suite, meta = load()
    by_id = {item["id"]: item for item in suite["items"]}
    results = [row if row["status"] != "measured" else {**row, **score_item(by_id[row["id"]], row["translation"])}
               for row in report["results"]]
    return summarize(results, meta["thresholds"])
