#!/usr/bin/env python3
"""يشغّل بنكَ الذاكرة المحكومة (ك٤٨) على الطريق الموصول بمحرّكٍ حيّ في Ollama المحلي (جديد-memory-probe).

    python3 tools/evaluate_memory.py --model qwen3.5:9b --agent <معرّفك> --out docs/probe/memory-live-<التاريخ>.json
    python3 tools/evaluate_memory.py --recount docs/probe/memory-live-<التاريخ>.json   # إعادةُ عدّ التسرّب من الرسوبات المسجَّلة

- كلُّ جولةٍ تذهب إلى النموذج الحقيقيّ عبر `webui.server.LocalApp`، والاقتراحُ وحده مكتوبٌ سلفًا
  (البنكُ يقيس الموافقةَ عليه، لا أن النموذج يقترح).
- المقاييسُ ما بلغ النموذجَ وما بقي على القرص، كما في `docs/MEMORY-DESIGN.md` §٦.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.attribution import normalize  # noqa: E402
from core.canonical import PayloadRejected  # noqa: E402
from evaluation.memory_bank import validate_memory_bank  # noqa: E402
from evaluation.memory_runner import recount_leakage, run_memory_bank  # noqa: E402
from providers.ollama import OllamaProvider  # noqa: E402

REGISTRY = ROOT / "registry" / "agents.json"
DEFAULT_SUITE = ROOT / "evaluation" / "suites" / "memory_v1.json"
# البنوكُ المكلَّف بها بأعدادها كما في تكليفها؛ فتسليمٌ مبتورٌ لا يُقاس «١/١» (ملاحظة Codex على #129)
COMMISSIONED = {
    "memory_kimi_v1": {"total": 40, "forget": 10, "isolation": 8, "consent": 8, "backup": 6, "injection": 8},
}


def _digest(model: str, base: str = "http://127.0.0.1:11434") -> str | None:
    """بصمةُ النموذج المثبَّت كما يعرضها Ollama، فالوسمُ وحده يتغيّر بسحبٍ جديد (ملاحظة Codex على #129). وتُطلب من
    Ollama على الجهاز بلا وسيط البيئة، كما يتّصل به المزوّد: `HTTP_PROXY` بلا `NO_PROXY` كان يُرسلها إلى الوسيط فيُجيب
    ببصمةٍ لأوزانٍ لم تُشغَّل (ملاحظة Codex على #144، والفجوةُ نفسُها هنا)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(base + "/api/tags", timeout=10) as response:
            models = json.loads(response.read().decode("utf-8"))["models"]
    except (OSError, ValueError, KeyError):
        return None
    wanted = model if ":" in model else model + ":latest"
    return next((m.get("digest") for m in models if m.get("name") == wanted), None)


def commissioned_shortfall(bank: dict) -> str | None:
    wanted = COMMISSIONED.get(bank["suite_id"])
    if wanted is None:
        return "suite_not_commissioned"
    scenarios = bank["scenarios"]
    # نسخةُ سيناريو باسمٍ آخر لا تزيد البنكَ سعةً، فلا تُعدّ في أعداده: كلُّ نصٍّ يُحفظ في سيناريو واحد، ويُقارن
    # بعد التطبيع العربيّ فلا يفلت تكرارٌ بتشكيلٍ أو مسافة (ملاحظة Codex على #129)
    owner = {}
    for scenario in scenarios:
        for step in scenario["steps"]:
            if step.get("op") in ("remember", "propose"):
                if owner.setdefault(" ".join(normalize(step["text"]).split()), scenario["id"]) != scenario["id"]:
                    return "suite_duplicate_scenario"
    if len(scenarios) < wanted["total"]:
        return "suite_below_commissioned_total"
    for category, minimum in wanted.items():
        if category != "total" and sum(s["category"] == category for s in scenarios) < minimum:
            return "suite_below_commissioned_category"
    return None

LIMITS = [
    "measures_what_reaches_the_model_and_what_stays_on_disk_not_what_the_model_does_with_a_memory",
    "proposals_are_scripted_the_bank_measures_consent_not_that_the_model_proposes",
    "retrieval_is_lexical_so_a_paraphrased_question_can_miss_a_stored_item",
    "the_context_checked_is_the_first_request_of_each_turn_later_tool_steps_are_not_inspected",
    "single_run_on_one_local_model_no_variance_estimate",
    "commissioned_scenarios_are_distinct_by_saved_text_a_near_copy_with_new_text_still_counts",
    "probes_reuse_one_session_per_project_a_turn_still_awaiting_the_owner_after_three_denials_resets_it_and_is_counted",
    "every_persisted_item_is_shown_in_its_project_context_turns_before_it_is_forgotten_and_before_a_backup_so_the_same_session_history_held_it_and_the_snapshot_holds_that_session",
    "a_probe_turn_the_model_leaves_awaiting_the_owner_is_stopped_by_agent_stop_before_its_session_is_abandoned_and_a_stop_that_fails_is_named_in_stuck_probe_turns",
    "exposure_probes_ask_a_neutral_question_that_carries_nothing_of_the_item_so_neither_a_directive_nor_a_value_enters_the_probe_session_history_as_the_owner_s_own_words_and_every_item_within_the_context_capacity_is_exposed_by_the_block_itself_and_a_scenario_whose_absent_witness_or_saved_text_overlaps_the_probe_question_is_refused_before_measurement_as_is_a_context_step_whose_own_question_or_an_earlier_context_question_of_its_project_repeats_an_absent_witness_or_whose_absent_witness_falls_inside_a_message_role_name_or_the_fixed_wire_message_fields_or_a_tool_schema_of_the_evaluator_s_own_registry_as_the_provider_serializes_it_not_every_default_tool_or_the_fixed_agent_request_envelope_or_a_fixed_field_of_the_ollama_request_body_as_the_live_provider_builds_it_including_the_live_model_name_or_inside_the_question_as_sent_after_quoted_directives_are_quarantined_and_the_wired_absence_check_reads_the_whole_request_body_as_the_provider_builds_it_with_its_fixed_outer_fields_roles_field_names_tool_call_ids_and_arguments_the_tool_schemas_as_serialized_and_the_whole_current_agent_envelope_as_sent_including_the_model_s_own_earlier_replies_so_a_model_that_echoes_a_value_before_it_is_forgotten_fails_the_scenario_because_the_memory_system_does_not_redact_the_model_s_past_words_and_every_rendered_context_block_is_scanned_for_a_raw_directive_in_any_category_and_any_explicit_probe_counted_once_per_step",
    "the_store_driver_snapshots_and_restores_every_store_together_as_the_workspace_backup_does_a_store_created_after_the_snapshot_is_restored_empty",
    "the_residue_scan_masks_the_store_s_generated_item_id_sha256_approved_at_and_forgotten_at_values_and_the_receipt_s_generated_references_by_their_json_values_so_forgotten_text_identical_to_one_of_them_is_not_seen_on_disk_while_forget_rate_still_reports_it_forgotten",
    "leakage_counts_only_an_absent_witness_served_by_the_memory_itself_at_the_check_the_retrieval_the_owner_list_or_the_current_context_block_that_is_the_text_of_another_project_s_item_saved_before_the_check_and_not_of_a_checked_project_item_still_in_its_store_so_the_model_s_own_echo_or_a_failed_forget_of_the_checked_project_is_named_in_failures_but_not_counted_as_a_cross_project_leak_while_a_forgotten_local_twin_does_not_hide_one_and_a_recount_of_a_published_report_reads_the_scenario_s_expected_store_at_the_recorded_step_and_the_recorded_failure_location",
]


def recount(path: Path, suite: Path, parser) -> int:
    """يعيد عدَّ `leakage` في تقريرٍ منشور من رسوباته المسجَّلة بالقاعدة الحالية (`recount_leakage`)، على البنك الذي قيس به
    وحده (بصمتُه في التقرير)، ويكتب التقريرَ في موضعه مع `recount.leakage` القديمِ والجديد وتاريخِ الإعادة؛ الرسوباتُ
    والحدودُ كما قيست لا تُمسّ (ملاحظة Codex على #129، الجولة الأربعون)."""
    raw = suite.read_bytes()
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("suite_sha256") != hashlib.sha256(raw).hexdigest():
        print(json.dumps({"status": "refused", "code": "suite_digest_mismatch"}, ensure_ascii=False))
        return 2
    try:
        # بجسد طلب النموذج الذي قيس به التقريرُ (`engine.model`) لا المعتمَد: شاهدٌ يقع في اسم المعتمَد وحده قُبل عند القياس،
        # فلا يُردّ عند إعادة العدّ (ملاحظة Codex على #129، الجولة الثانية والأربعون)
        bank = validate_memory_bank(json.loads(raw.decode("utf-8")), model=(report.get("engine") or {}).get("model"))
    except (PayloadRejected, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "refused", "code": getattr(exc, "code", "bank_invalid")}, ensure_ascii=False))
        return 2
    recounted = recount_leakage(report, bank, {"date": datetime.date.today().isoformat()})
    path.write_text(json.dumps(recounted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "recounted", "leakage": recounted["recount"]["leakage"], "out": str(path)}, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", help="اسمُ النموذج كما يعرفه Ollama")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--agent", help="معرّفُ من يشغّل القياس، مسجَّلًا في registry/agents.json")
    parser.add_argument("--out", type=Path, help="مسارُ التقرير؛ لا يُستبدل ملفٌّ قائم")
    parser.add_argument("--recount", type=Path, help="تقريرٌ منشور يُعاد فيه عدُّ التسرّب من رسوباته المسجَّلة بلا قياس")
    args = parser.parse_args(argv)
    if args.recount is not None:
        return recount(args.recount, args.suite, parser)
    if not (args.model and args.agent and args.out):
        parser.error("--model و--agent و--out لازمة للقياس الحيّ")
    if args.out.exists():
        parser.error(f"التقريرُ قائم: {args.out}")
    if args.agent not in json.loads(REGISTRY.read_text(encoding="utf-8"))["agents"]:
        parser.error(f"agent_unregistered: {args.agent}")
    # بنكٌ لم يُفحص قد يمرّ ١٠٠٪ بخطواتٍ يتجاهلها المُشغِّل؛ فالمدقّقُ قبل أي نداء (ملاحظة Codex على #129)
    raw = args.suite.read_bytes()
    # البنكُ المكلَّف بالشروط الأشدّ: كلُّ فئةٍ تؤدّي ما تسمّيه (ملاحظة Codex على #129)
    commissioned = args.suite.resolve() != DEFAULT_SUITE.resolve()
    try:
        # بجسد طلب النموذج المختار لا المعتمَد: شاهدٌ يقع في اسمه يُرفض هنا لا بعد القياس (ملاحظة Codex على #129)
        bank = validate_memory_bank(json.loads(raw.decode("utf-8")), strict=commissioned, model=args.model)
    except (PayloadRejected, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "refused", "code": getattr(exc, "code", "bank_invalid")}, ensure_ascii=False))
        return 2
    # غيرُ البنك المودَع بنكٌ مكلَّفٌ به، بأعداده كاملةً
    shortfall = commissioned_shortfall(bank) if commissioned else None
    if shortfall:
        print(json.dumps({"status": "refused", "code": shortfall}, ensure_ascii=False))
        return 2
    # البصمةُ قبل أيّ نداءٍ وبعد آخره: تقريرٌ بالوسم وحده، أو بوسمٍ تغيّرت أوزانُه أثناء التشغيل، لا يسمّي
    # ما أجاب (ملاحظتا Codex على #129)
    digest = _digest(args.model)
    if not digest:
        print(json.dumps({"status": "refused", "code": "model_digest_unresolved"}, ensure_ascii=False))
        return 2
    provider = OllamaProvider(model=args.model)
    report = run_memory_bank(bank, driver="live", delegate=provider)
    if _digest(args.model) != digest:
        print(json.dumps({"status": "refused", "code": "model_digest_drifted"}, ensure_ascii=False))
        return 2
    report.update(suite_sha256=hashlib.sha256(raw).hexdigest(), date=datetime.date.today().isoformat(), agent=args.agent,
                  engine={"provider": "ollama", "model": args.model, "digest": digest},
                  measurement_limits=LIMITS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"metrics": report["metrics"], "meets_thresholds": report["meets_thresholds"],
                      "passed": report["passed"], "total": report["total"], "out": str(args.out)},
                     ensure_ascii=False))
    return 0 if report["meets_thresholds"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
