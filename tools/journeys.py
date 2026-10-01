#!/usr/bin/env python3
"""سجلُّ رحلات المالك بلا نصوص (جديد-journeys-log، #163): خطُّ أساس م١ لبوابة م٢.

يقرأ مخزنَ الواجهة اليومية (`var/daily-ui` افتراضًا، كما في `tools/serve_ui.py`) **قراءةً وحدها**: لا قفلَ ولا إنشاءَ ولا
كتابةَ فيه، ولا يتبع وصلةً رمزية. ويكتب `docs/probe/journeys-<YYYYMMDD>.json` فيه لكل رحلةٍ (جولةٌ واحدة: طلبٌ واحد من
المالك في جلسة) حقائقَ لا نصَّ فيها: وضعُ الجلسة، وعددُ الخطوات ونداءاتِ الأدوات، وصنفُ النتيجة ورمزُها، والتاريخُ بتوقيت
UTC حين يُعرف، والزمنُ حين يُعرف؛ ثم المجاميع التراكمية: بالنتيجة وبالوضع وبالتاريخ، ونسبةُ الإنجاز؛ ثم خطُّ الأساس.

خطُّ الأساس («أولُ ٣٠ رحلة في م١»، خطةُ ٢٦ سبتمبر) فوجٌ منفصل عن المجاميع التراكمية، وهو **أولُ ثلاثين** رحلةً مؤرَّخة
داخل نافذة م١ لا كلُّها، فلا يتحرّك بما يُضاف بعدها. والنافذةُ بدءُ مرحلة م١ ونهايتُها كما تسجّلهما الخطة
(`docs/PLAN-20260926.json`) ما لم يمرّر المالك `--baseline-from`/`--baseline-until`، ولا تُخترع. والترتيبُ باليوم، ثم
بزمن إنشاء الجلسة (زمنُ تعديل meta.json، ومعرّفُها عند التساوي)، ثم بموضع الجولة في جلستها؛ لا بترتيب قراءة الدليل.
ولا يجهز خطُّ الأساس، ولا تُحسب نسبةُ إنجازٍ يُظنّ أنها كاملة، ما دام موضعٌ في الثلاثين لا يُعرف: جلسةٌ لا تُقرأ
(`unreadable_entries`)، أو رحلةٌ بلا تاريخ قد تقع قبل القطع (`undated_journeys_before_cut`)، أو قطعٌ بين جلستين تداخلت
كتابتُهما في يومه (`baseline_cut_order_unknown`). فالنسبةُ حينئذٍ `null` بسببها المسمّى لا رقمٌ نظيفُ المظهر.

وبوابةُ م٢ («من ٣٠ إلى ٥٠ رحلة، والإنجاز +١٠ نقاط فوق خط أساس م١») تُقرأ أعدادُها ونافذةُ م٢ من الخطة ولا تُخترع، وتقارن
فوجَين بالترتيب نفسِه: الثلاثين الأولى في م١، وأولَ عشرين (٥٠ − ٣٠) مؤرَّخةٍ داخل نافذة م٢ (`m2_gate.comparison`). فلا
يدخل المقارنةَ شيءٌ من م١؛ وما بعد الثلاثين في م١ لا يدخل فوجًا ويُعدّ في `m2_gate.m1_after_baseline`؛ ولا نسبةَ للمقارنة
حتى يكتمل فوجُها (`too_few_comparison_journeys`). أمّا `cumulative_completion_rate` فتراكميةٌ لكل ما حُفظ، ولا تدخل بوابة.

لا يحمل التقريرُ نصًّا ولا مسارًا ولا معرّفَ مشروعٍ أو جلسةٍ أو جولة ولا اسمًا ولا بصمةَ نصّ ولا جوابَ نموذج ولا شيئًا من
محتوى local_only: أعدادٌ ورموزُ آلةٍ وتواريخُ وأزمنةٌ فقط. والأداةُ تفحص تقريرَها بذلك قبل كتابته، فتقريرٌ فيه نصٌّ أو معرّفٌ
يُرفض باسمه (`report_leak`) ولا يُكتب.

الاستعمال (على الماك بعد الاستعمال):
    python tools/journeys.py                            # var/daily-ui ← docs/probe/journeys-<اليوم>.json
    python tools/journeys.py --root <مخزن> --out <ملف>
    python tools/journeys.py --baseline-from 2026-10-12 --baseline-until 2026-11-08   # نافذةٌ غيرُ نافذة الخطة

الرفضُ مسمًّى وبرمز خروجٍ غير صفري: `root_missing` (المخزنُ غائب أو ليس دليلًا)، `root_unsafe` (وصلةٌ رمزية)، `root_unreadable`،
`output_exists` (لا يُكتب فوق ملفٍّ قائم)، `output_dir_missing`، `output_unwritable`، `baseline_date_invalid`،
`baseline_window_invalid` (البدءُ بعد النهاية)، `report_leak`؛ ولخطّ الأساس المجمَّد: `baseline_already_frozen`،
`frozen_baseline_lost` (نُشر تقريرٌ يشير إليه ثم غاب)، `frozen_baseline_changed`، `frozen_baseline_unreadable`،
`frozen_baseline_unpublished` (لا تقريرَ منشورًا يشير إليه)، `probe_output_name_invalid` (تقريرٌ في docs/probe باسمٍ غير
journeys-*.json، أو باسم المجمَّد)، `baseline_publish_failed`، `refreeze_reason_invalid`،
`refreeze_requires_probe_output`، `refreeze_baseline_not_ready`، `journeys_report_unreadable` (تقريرٌ منشور تالف؛ تُقرّ به
إعادةُ التجميد ببصمة ملفّه في `acknowledged_reports`).

التجميد: أولُ تقريرٍ في docs/probe يجهز فيه خطُّ الأساس يكتب معه `docs/probe/journeys-baseline.json` (النافذةُ ومصدرُها،
والفوجُ بأعداده ونسبته، وبصمةُ هويّة أعضائه من معرّفاتٍ عشوائية، وإصدارُ الأداة). وكلُّ تشغيلٍ بعده يقارن به ولا يعيد
حسابه، ويُعلن ما اختلف فيه حسابُ اليوم (`baseline.frozen.drift`). وتغييرُه بـ`--refreeze-baseline <رمز سبب>` وحده. وما
يفرّق «لم يُجمَّد قطّ» من «جُمِّد ثم ضاع» بصمتُه في التقارير المنشورة (`baseline.frozen.digest`). والجلسةُ التي لا تُقرأ أو فسدت لا تُسقط
الأداة: تُعدّ في `unreadable` برمزها.

الحدود (وهي في التقرير `measurement_limits`): الواجهةُ لا تحفظ زمنَ الجولة، فالزمنُ فارغٌ بسببه المسمّى، والتاريخُ يوم
الجلسة حين يتّحد يومُ إنشائها (زمنُ تعديل meta.json) ويومُ آخر كتابةٍ فيها (زمنُ تعديل state.json)، وإلا فارغٌ بسببه؛
والنتيجةُ ما حفظته الواجهة لا جودةُ الجواب.
"""
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import hashlib
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.canonical import digest  # noqa: E402
from core.ledger import ENTRY_KEYS as LEDGER_ENTRY_KEYS, GENESIS  # noqa: E402

SCHEMA_VERSION = 1
TOOL = "tools/journeys.py"
TASK = "جديد-journeys-log"
DEFAULT_ROOT = ROOT / "var" / "daily-ui"
PROBE_DIR = ROOT / "docs" / "probe"
PLAN = ROOT / "docs" / "PLAN-20260926.json"     # الخطةُ الحاكمة (ق٦٤): فيها بدءُ م١ ونهايتُها
M1 = "م١"
M2 = "م٢"
# بوابةُ م٢ كما تكتبها الخطة: «docs/probe/journeys-<date>.json: من ٣٠ إلى ٥٠ رحلة، والإنجاز +١٠ نقاط فوق خط أساس م١»
M2_GATE = re.compile(r"journeys-<date>\.json:\s*من\s+(\d+)\s+إلى\s+(\d+)\s+رحلة.*?\+(\d+)\s+نقاط")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
MAX_STATE_BYTES = 32 * 1024 * 1024              # حدُّ حالة الجلسة الوكيلة في المنتج نفسِه
IDENTIFIER = re.compile(r"[a-f0-9]{32}\Z")      # معرّفاتُ المشروعات والجلسات في LocalApp
MACHINE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
HEX_RUN = re.compile(r"[0-9a-f]{12,}")          # معرّفٌ أو بصمةٌ داخل ما يبدو رمزًا
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
FROZEN_NAME = "journeys-baseline.json"            # خطُّ الأساس المجمَّد في docs/probe بجانب التقارير

TEXT_MODES = ("text", "media")                  # جلساتٌ حالتُها في sessions/<id>/chat/<id>/state.json
AGENT_MODES = ("agent", "research", "coder", "translate")   # حالتُها في agent-control/<id>/state.json
MODES = (*TEXT_MODES, *AGENT_MODES)

# حالةُ الجولة كما حفظتها الواجهة ← صنفُ النتيجة. «مرفوضة» تجمع الرفضَ والعطب (خطةُ ٢٦ سبتمبر)، والحالةُ الخامُ باقيةٌ
# في كل سجلٍّ فلا يضيع الفرق. وحالةٌ غيرُ معروفة تُعدّ باسمها إن كانت رمزَ آلة، وإلا `unknown`؛ ولا تسقط صامتة.
OUTCOMES = {
    "complete": "completed",
    "truncated": "truncated",
    "refused": "refused",
    "error": "refused",             # الطريقُ النصّيّ: عطبُ المزوّد أو رفضُه
    "failed": "refused",            # الطريقُ الوكيل: المزوّدُ لم يُجب
    "awaiting_owner": "awaiting_owner",
    "outcome_unknown": "outcome_unknown",
    "pending": "outcome_unknown",   # جولةٌ بلا نتيجةٍ محفوظة: جاريةٌ أو انقطعت
    "timed_out": "timed_out",
    "step_limit": "step_limit",
    "cancelled": "stopped",
}
# جولةٌ نصّية انقطع تنفيذُها بلا قيدٍ في سجلّ النداءات يحفظها المنتجُ «error» برمز `outcome_uncertain`
# (`conversation/session.py`، `_result` بلا قيد): نتيجتُها مجهولة لا رفضٌ ولا عطبٌ مثبت، كنظيرتها الوكيلة `outcome_unknown`
UNSETTLED = {("error", "outcome_uncertain"): "outcome_unknown"}
OUTCOME_LABELS = {
    "completed": "منجزة",
    "truncated": "مبتورة",
    "refused": "مرفوضة",
    "awaiting_owner": "تنتظر المالك",
    "outcome_unknown": "مجهولةُ النتيجة",
    "timed_out": "انتهت مهلتُها",
    "step_limit": "بلغت حدَّ الخطوات",
    "stopped": "أوقفها المالك",
}
BASELINE_MIN_DATED_JOURNEYS = 30
FROZEN_FIELDS = frozenset({"schema_version", "tool", "task", "kind", "commit", "frozen_on", "window", "cohort",
                           "members_digest", "refreeze_reason", "history", "acknowledged_reports",
                           "measurement_limits"})
FROZEN_COHORT_FIELDS = ("journeys", "distinct_dates", "by_date", "by_outcome", "completion_rate", "cut_date")
FROZEN_LIMITS = (
    "the_frozen_baseline_is_the_first_ready_baseline_published_in_docs_probe_and_later_runs_compare_against_it_without_recomputing_it",
    "members_digest_is_a_sha256_of_opaque_session_ids_and_turn_positions_and_carries_no_text",
    "a_recompute_that_differs_is_reported_as_drift_and_replaces_nothing_without_refreeze_baseline_and_its_reason_code",
)
BASELINE_MIN_DISTINCT_DATES = 2
# نوعا القيد اللذان بلغ فيهما النداءُ المزوّد (`core/run.py`)؛ و"refused" رفضٌ قبله فليس خطوة
CALL_KINDS = frozenset({"ok", "error"})
UNREADABLE_ENTRIES = "unreadable_entries"
UNDATED_BEFORE_CUT = "undated_journeys_before_cut"
CUT_ORDER_UNKNOWN = "baseline_cut_order_unknown"
DURATION_UNKNOWN = "turn_timestamps_not_stored"
MEASUREMENT_LIMITS = (
    "counts_only_what_the_daily_ui_stored_under_projects_a_session_left_in_staging_by_an_interrupted_creation_is_not_counted",
    "a_journey_is_one_stored_turn_one_request_of_the_owner_in_one_session_whatever_its_length",
    "steps_are_the_provider_calls_the_session_s_call_ledger_recorded_for_the_turn_zero_with_steps_evidence_none_when_it_recorded_none_and_null_with_ledger_unreadable_when_the_ledger_is_missing_corrupt_or_its_chain_broken_and_tool_calls_are_the_calls_the_model_requested",
    "the_ui_stores_no_per_turn_timestamps_so_every_duration_is_null_with_its_named_reason",
    "the_date_is_the_utc_day_of_the_session_when_its_creation_meta_json_mtime_and_its_last_write_state_json_mtime_fall_on_the_same_utc_day_otherwise_null_with_its_reason_so_a_session_reused_across_days_leaves_its_journeys_undated",
    "file_mtimes_are_not_protected_a_restore_or_copy_that_resets_them_moves_or_hides_the_date",
    "a_turn_without_a_stored_result_is_counted_as_outcome_unknown_and_is_not_settled_from_the_call_ledger",
    "the_outcome_rests_on_the_state_envelope_digest_and_the_steps_on_the_call_ledger_hash_chain_neither_on_the_ledger_anchor_nor_the_action_receipts",
    "the_outcome_is_what_the_ui_stored_and_says_nothing_about_the_quality_or_truth_of_the_answer",
    "the_data_is_the_owner_s_own_use_on_one_machine_not_a_sample_of_users",
    "the_report_carries_counts_machine_codes_utc_dates_and_durations_only_and_the_tool_refuses_to_write_a_report_with_any_other_string",
    "the_baseline_cohort_is_the_first_30_dated_journeys_inside_the_m1_window_recorded_in_the_plan_unless_the_owner_passes_baseline_from_or_baseline_until_and_the_cumulative_totals_are_not_the_baseline",
    "within_a_utc_day_journeys_are_ordered_by_their_session_s_creation_mtime_then_their_position_in_it_and_a_cut_between_sessions_whose_writes_overlap_or_tie_that_day_is_refused_not_guessed",
    "an_unreadable_entry_or_an_undated_journey_that_may_fall_before_the_cut_blocks_baseline_ready_and_nulls_the_completion_rate_it_would_bias",
    "a_later_write_to_a_session_that_holds_baseline_journeys_can_undate_or_reorder_them_so_the_baseline_is_the_first_report_that_says_baseline_ready",
    "the_m2_comparison_cohort_is_the_first_journeys_dated_inside_the_m2_window_the_plan_records_as_many_as_the_m2_gate_count_minus_the_30_of_the_baseline_and_has_no_rate_until_it_is_complete",
    "m1_journeys_after_the_baseline_30_belong_to_neither_cohort_and_are_counted_in_m1_after_baseline",
    "once_a_ready_baseline_is_published_in_docs_probe_it_is_frozen_in_journeys_baseline_json_and_later_runs_use_its_window_and_cohort_reporting_any_recompute_difference_as_drift",
    "the_cumulative_completion_rate_covers_every_stored_journey_pre_m1_and_baseline_included_and_is_not_an_input_of_any_gate",
)
CARRIES = "counts_machine_codes_utc_dates_and_durations_only"
# مخطّطُ التقرير مغلق: مفتاحٌ لا تبنيه `build_report` أو `_journey` تسرّبٌ ولو كانت قيمتُه رمزَ آلة
REPORT_FIELDS = frozenset({"schema_version", "tool", "task", "commit", "generated_on", "root", "carries", "totals",
                           "unreadable", "by_outcome", "by_mode", "by_date", "distinct_dates",
                           "cumulative_completion_rate", "cumulative_completion_rate_unavailable_reason",
                           "baseline_ready", "baseline", "m2_gate", "outcome_labels", "journeys", "measurement_limits"})
JOURNEY_FIELDS = frozenset({"mode", "outcome", "status", "error_code", "steps", "steps_evidence", "tool_calls", "date",
                            "date_basis", "date_unknown_reason", "session_first_day", "session_last_day", "duration_s",
                            "duration_unknown_reason", "baseline_position", "comparison_position"})
BASELINE_RULE = ("the_first_30_dated_journeys_inside_the_m1_window_by_utc_date_then_session_creation_then_turn_"
                 "on_at_least_2_distinct_utc_dates_with_no_unreadable_entry_no_undated_journey_before_the_cut_"
                 "and_a_provable_order_at_the_cut")
M2_RULE = ("the_first_journeys_inside_the_m2_window_by_the_same_order_as_many_as_the_plan_s_m2_count_minus_30_must_beat_"
           "the_baseline_cohort_completion_rate_by_the_plan_s_points_and_the_cumulative_rate_is_not_an_input")

_DIR = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
_FILE = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)


class Refused(Exception):
    """رفضٌ مسمًّى يُنهي الأداة برمزٍ غير صفري."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class Unreadable(Exception):
    """مشروعٌ أو جلسةٌ لا تُقرأ: تُعدّ برمزها ولا تُسقط الأداة."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def machine_code(value) -> str | None:
    """رمزُ آلةٍ كما هو، أو `unknown`: نصٌّ أو معرّفٌ أو بصمةٌ لا تبلغ التقرير من هذا الباب."""
    if value is None:
        return None
    if isinstance(value, str) and MACHINE_CODE.fullmatch(value) and not HEX_RUN.search(value):
        return value
    return "unknown"


def _utc_day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()


def _open_dir(name, dir_fd, missing="missing") -> int:
    """دليلٌ بلا اتّباع وصلة؛ وغيابُه برمز `missing` الذي يسمّيه المستدعي."""
    try:
        return os.open(name, _DIR, dir_fd=dir_fd)
    except FileNotFoundError:
        raise Unreadable(missing) from None
    except OSError:
        raise Unreadable("unsafe_path") from None


def _read_file(name, dir_fd, missing="state_missing") -> tuple[bytes, float]:
    """(البايتات، زمنُ التعديل) لملفٍّ عاديّ بلا اتّباع وصلة؛ وما سواه يُسمّى."""
    try:
        fd = os.open(name, _FILE, dir_fd=dir_fd)
    except FileNotFoundError:
        raise Unreadable(missing) from None
    except OSError:
        raise Unreadable("unsafe_path") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Unreadable("unsafe_path")
        if info.st_size > MAX_STATE_BYTES:
            raise Unreadable("state_too_large")
        chunks = []
        while chunk := os.read(fd, 1 << 20):
            chunks.append(chunk)
    except OSError:
        raise Unreadable("state_unreadable") from None
    finally:
        os.close(fd)
    return b"".join(chunks), info.st_mtime


def _read_json(name, dir_fd, missing="state_missing") -> tuple[object, float]:
    """(القيمة، زمنُ التعديل) لملفّ JSON؛ وما لا يُحلَّل `state_corrupt`."""
    raw, mtime = _read_file(name, dir_fd, missing)
    try:
        return json.loads(raw.decode("utf-8")), mtime
    except (ValueError, UnicodeError, RecursionError):
        raise Unreadable("state_corrupt") from None


def _ledger(dir_fd: int) -> list[dict] | None:
    """قيودُ سجلّ نداءات الجلسة (`calls.jsonl`) بعد التحقّق من سلسلتها، أو None إن غاب أو فسد أو انكسرت سلسلتُه:
    دليلُ الخطوات لا يُخمَّن."""
    try:
        raw, _ = _read_file("calls.jsonl", dir_fd, missing="ledger_missing")
        prev, records = GENESIS, []
        for seq, line in enumerate(raw.decode("utf-8").splitlines()):
            entry = json.loads(line)
            if (not isinstance(entry, dict) or set(entry) != LEDGER_ENTRY_KEYS or entry["prev"] != prev
                    or entry["seq"] != seq or not isinstance(entry["record"], dict)
                    or digest({"prev": entry["prev"], "seq": entry["seq"], "record": entry["record"]}) != entry["digest"]):
                return None
            prev = entry["digest"]
            records.append(entry["record"])
    except Exception:                                   # noqa: BLE001 -- سجلٌّ لا يُقرأ أو لا يُبصم مجهولٌ باسمه
        return None
    return records


def _state_turns(envelope) -> list:
    """جولاتُ الحالة بعد مطابقة بصمة غلافها كما يطابقها المنتج."""
    if not isinstance(envelope, dict) or set(envelope) != {"state", "sha256"}:
        raise Unreadable("state_corrupt")
    try:
        intact = digest(envelope["state"]) == envelope["sha256"]
    except Exception:                                   # noqa: BLE001 -- حمولةٌ لا تُبصم فاسدةٌ باسمها
        intact = False
    turns = envelope["state"].get("turns") if isinstance(envelope["state"], dict) else None
    if not intact or not isinstance(turns, list) or not all(isinstance(turn, dict) and "result" in turn
                                                            for turn in turns):
        raise Unreadable("state_corrupt")
    return turns


def _dating(created: float, last: float) -> dict:
    """تاريخُ رحلات الجلسة، ومداها من يوم إنشائها إلى يوم آخر كتابةٍ فيها (ليُعرف أتقع رحلةٌ بلا تاريخ في نافذة خطّ الأساس)."""
    first, final = _utc_day(created), _utc_day(last)
    if last < created:
        return {"date": None, "date_basis": None, "date_unknown_reason": "timestamps_inconsistent",
                "session_first_day": None, "session_last_day": None}
    span = {"session_first_day": first, "session_last_day": final}
    if first == final:
        return {"date": final, "date_basis": "session_single_day", "date_unknown_reason": None, **span}
    return {"date": None, "date_basis": None, "date_unknown_reason": "session_spans_days", **span}


def _calls(turn: dict, agent: bool, ledger: list[dict] | None) -> tuple[int | None, str]:
    """خطواتُ الجولة: نداءاتُ المزوّد التي قيّدها سجلُّ نداءات الجلسة لها، لا ما يُفترض. والطريقان على قاعدةٍ واحدة:
    الوكيلُ بمفاتيح نداءاته المحفوظة في الجولة، والنصُّ والوسائطُ بمفتاح `<الغرض>:<الجلسة>:<الجولة>`."""
    if ledger is None:
        return None, "ledger_unreadable"
    if agent:
        calls = turn.get("calls") if isinstance(turn.get("calls"), list) else []
        keys = {call.get("idempotency_key") for call in calls if isinstance(call, dict)}
        mine = keys.__contains__
    else:
        suffix = f":{turn.get('turn_id')}"
        mine = lambda key: key.endswith(suffix)                    # noqa: E731
    steps = sum(1 for record in ledger if record.get("kind") in CALL_KINDS
                and isinstance(record.get("idempotency_key"), str) and mine(record["idempotency_key"]))
    return steps, "call_ledger" if steps else "none"


def _journey(turn: dict, mode: str, agent: bool, dating: dict, ledger: list[dict] | None) -> dict:
    result = turn["result"]
    if result is None:
        status = "pending"
    elif isinstance(result, dict):
        status = machine_code(result.get("status")) or "unknown"
    else:
        status = "unknown"
    steps, evidence = _calls(turn, agent, ledger)
    tool_calls = 0
    recorded = result.get("steps") if agent and isinstance(result, dict) else None
    if isinstance(recorded, list):
        tool_calls = sum(len(step["tool_calls"]) for step in recorded
                         if isinstance(step, dict) and isinstance(step.get("tool_calls"), list))
    error_code = machine_code(result.get("error_code")) if isinstance(result, dict) else None
    return {
        "mode": mode,
        "outcome": UNSETTLED.get((status, error_code)) or OUTCOMES.get(status, status),
        "status": status,
        "error_code": error_code,
        "steps": steps,
        "steps_evidence": evidence,
        "tool_calls": tool_calls,
        **dating,
        "duration_s": None,
        "duration_unknown_reason": DURATION_UNKNOWN,
    }


def _session(project_fd: int, sessions_fd: int, name: str) -> list[dict]:
    session_fd = _open_dir(name, sessions_fd)
    try:
        meta, created = _read_json("meta.json", session_fd, missing="metadata_missing")
        if not isinstance(meta, dict) or meta.get("id") != name:
            raise Unreadable("metadata_invalid")
        mode = meta.get("mode", "text")
        if mode in AGENT_MODES:
            control_fd = _open_dir("agent-control", project_fd, missing="state_missing")
            try:
                state_fd = _open_dir(name, control_fd, missing="state_missing")
            finally:
                os.close(control_fd)
        elif mode in TEXT_MODES:
            chat_fd = _open_dir("chat", session_fd, missing="state_missing")
            try:
                state_fd = _open_dir(name, chat_fd, missing="state_missing")
            finally:
                os.close(chat_fd)
        else:
            raise Unreadable("mode_unknown")
    finally:
        os.close(session_fd)
    try:
        envelope, last = _read_json("state.json", state_fd)
        ledger = _ledger(state_fd)
    finally:
        os.close(state_fd)
    dating = _dating(created, last)
    # ترتيبُ الرحلة لقطع خطّ الأساس؛ داخليٌّ يُنزع قبل التقرير (معرّفُ الجلسة وأزمنتُها الدقيقة لا تخرج)
    return [{**_journey(turn, mode, mode in AGENT_MODES, dating, ledger),
             "_order": {"created": created, "last": last, "session": name, "index": index}}
            for index, turn in enumerate(_state_turns(envelope))]


def _entries(dir_fd: int) -> list[str]:
    try:
        return sorted(os.listdir(dir_fd))
    except OSError:
        raise Unreadable("unsafe_path") from None


def scan(root: Path) -> dict:
    """يقرأ المخزن ولا يكتب فيه: (الرحلات، والعدّ، وما لم يُقرأ برمزه)."""
    try:
        info = os.lstat(root)
    except FileNotFoundError:
        raise Refused("root_missing") from None
    except OSError:
        raise Refused("root_unreadable") from None
    if stat.S_ISLNK(info.st_mode):
        raise Refused("root_unsafe")
    if not stat.S_ISDIR(info.st_mode):
        raise Refused("root_missing")
    try:
        root_fd = os.open(root, _DIR)
    except OSError:
        raise Refused("root_unreadable") from None
    journeys, unreadable = [], Counter()
    counts = {"projects": 0, "sessions": 0, "unreadable_projects": 0, "unreadable_sessions": 0}
    try:
        try:
            projects_fd = _open_dir("projects", root_fd)
        except Unreadable as exc:
            if exc.code != "missing":
                raise Refused("root_unsafe") from None
            projects_fd = None
        try:
            project_names = [] if projects_fd is None else _entries(projects_fd)
        except Unreadable:
            if projects_fd is not None:
                os.close(projects_fd)
            raise Refused("root_unreadable") from None
        for project in project_names:
            try:
                if not IDENTIFIER.fullmatch(project):
                    raise Unreadable("entry_invalid")
                project_fd = _open_dir(project, projects_fd)
            except Unreadable as exc:
                counts["unreadable_projects"] += 1
                unreadable[exc.code] += 1
                continue
            sessions_fd = None
            try:
                try:
                    meta, _ = _read_json("meta.json", project_fd, missing="metadata_missing")
                    if not isinstance(meta, dict) or meta.get("id") != project:
                        raise Unreadable("metadata_invalid")
                    try:
                        sessions_fd = _open_dir("sessions", project_fd)
                    except Unreadable as exc:
                        if exc.code != "missing":
                            raise
                        sessions_fd = None
                    names = [] if sessions_fd is None else _entries(sessions_fd)
                except Unreadable as exc:
                    counts["unreadable_projects"] += 1
                    unreadable[exc.code] += 1
                    continue
                counts["projects"] += 1
                for name in names:
                    counts["sessions"] += 1
                    try:
                        if not IDENTIFIER.fullmatch(name):
                            raise Unreadable("entry_invalid")
                        journeys.extend(_session(project_fd, sessions_fd, name))
                    except Unreadable as exc:
                        counts["unreadable_sessions"] += 1
                        unreadable[exc.code] += 1
            finally:
                if sessions_fd is not None:
                    os.close(sessions_fd)
                os.close(project_fd)
        if projects_fd is not None:
            os.close(projects_fd)
    finally:
        os.close(root_fd)
    return {"journeys": journeys, "counts": counts, "unreadable": dict(sorted(unreadable.items()))}


def _commit() -> str | None:
    try:
        value = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                               timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return value if COMMIT.fullmatch(value) else None


def _day(value) -> str | None:
    """يومٌ صحيحٌ بصيغة YYYY-MM-DD كما هو، وإلا None."""
    if not isinstance(value, str) or not DATE.fullmatch(value):
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return value


def plan_window(plan: Path | None = None) -> tuple[str | None, str | None]:
    """بدءُ م١ ونهايتُها كما تسجّلهما الخطة، أو None لما لم تسجّله: النافذةُ لا تُخترع."""
    try:
        phases = json.loads(Path(PLAN if plan is None else plan).read_text(encoding="utf-8"))["phases"]
        phase = next(item for item in phases if isinstance(item, dict) and item.get("id") == M1)
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None, None
    return _day(phase.get("start")), _day(phase.get("end"))


def plan_m2_gate(plan: Path | None = None) -> dict | None:
    """عددُ رحلات بوابة م٢ ونقاطُها ونافذةُ م٢ كما تسجّلها الخطة، أو None لما لم تسجّله أو خالف خطَّ الأساس: البوابةُ لا
    تُخترع. وفوجُ المقارنة بعددِ البوابة ناقصَ الثلاثين («من ٣٠ إلى ٥٠» ← عشرون)."""
    try:
        phases = json.loads(Path(PLAN if plan is None else plan).read_text(encoding="utf-8"))["phases"]
        phase = next(item for item in phases if isinstance(item, dict) and item.get("id") == M2)
        found = next(match for gate in phase["gate"] if isinstance(gate, str)
                     for match in [M2_GATE.search(gate.translate(_ARABIC_DIGITS))] if match)
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None
    base, target, points = (int(value) for value in found.groups())
    m2_from, m2_until = _day(phase.get("start")), _day(phase.get("end"))
    if base != BASELINE_MIN_DATED_JOURNEYS or target <= base or m2_from is None or m2_until is None or m2_from > m2_until:
        return None
    return {"source": "plan_m2_gate", "target_journeys": target, "required_points": points,
            "comparison_size": target - base, "window": {"from": m2_from, "until": m2_until}}


def baseline_window(start: str | None = None, end: str | None = None) -> dict:
    """نافذةُ خطّ الأساس: ما مرّره المالك، وإلا ما في الخطة، وإلا مجهولةٌ فلا يجهز خطُّ الأساس."""
    for value in (start, end):
        if value is not None and _day(value) is None:
            raise Refused("baseline_date_invalid")
    planned = plan_window() if start is None or end is None else (None, None)
    window = {"from": start if start is not None else planned[0],
              "from_source": "option" if start is not None else "plan_m1_phase" if planned[0] else None,
              "until": end if end is not None else planned[1],
              "until_source": "option" if end is not None else "plan_m1_phase" if planned[1] else None}
    if window["from"] and window["until"] and window["from"] > window["until"]:
        raise Refused("baseline_window_invalid")
    return window


def _tally(journeys: list[dict]) -> tuple[dict, Counter]:
    by_outcome = {name: 0 for name in OUTCOME_LABELS}
    by_date = Counter()
    for journey in journeys:
        by_outcome[journey["outcome"]] = by_outcome.get(journey["outcome"], 0) + 1
        if journey["date"] is not None:
            by_date[journey["date"]] += 1
    return by_outcome, by_date


def _rate(by_outcome: dict, total: int, blockers: list[str]) -> tuple[float | None, str | None]:
    """نسبةُ الإنجاز، أو None بسببها: رحلاتٌ غائبة عن العدّ قد تكون المتعثّرة، فلا رقمَ نظيفَ المظهر فوقها."""
    if blockers:
        return None, blockers[0]
    if not total:
        return None, "no_journeys"
    return round(by_outcome["completed"] / total, 4), None


def _order(journey: dict) -> tuple:
    """اليومُ، ثم زمنُ إنشاء الجلسة ومعرّفُها، ثم موضعُ الجولة فيها: ترتيبٌ لا يتعلّق بترتيب قراءة الدليل."""
    order = journey["_order"]
    return journey["date"], order["created"], order["session"], order["index"]


def _cut_order_unknown(members: list[dict], rest: list[dict], cut: str) -> bool:
    """القطعُ بين جلستين في يومه لا يُثبت ترتيبُه إن كُتبت جلسةٌ فيها رحلةٌ مختارة بعد أن أُنشئت جلسةٌ فيها رحلةٌ متروكة
    أو في اللحظة نفسِها: تساوي الأزمنة (نسخٌ أو استعادة، أو دقّةُ نظام ملفاتٍ خشنة) لا يُثبت ترتيبًا، ولا يحسمه معرّفٌ عشوائي."""
    chosen = {j["_order"]["session"]: j["_order"] for j in members if j["date"] == cut}
    left = {j["_order"]["session"]: j["_order"] for j in rest if j["date"] == cut}
    return any(a["last"] >= b["created"] for sa, a in chosen.items() for sb, b in left.items() if sa != sb)


def _may_fall_in(journey: dict, start: str, end: str) -> bool:
    """رحلةٌ بلا تاريخ قد تقع في النافذة: مدى جلستها يتقاطع معها، أو مداها مجهول."""
    first, final = journey["session_first_day"], journey["session_last_day"]
    if first is None or final is None:
        return True
    return not (final < start or first > end)


def _m2_gate(records: list[dict], window: dict, gate: dict | None, baseline: dict | None,
             baseline_missing: list[str], unreadable: list[str], m1_after: int | None) -> tuple[dict, dict]:
    """بوابةُ م٢: أولُ الرحلات المؤرَّخة داخل نافذة م٢ بالترتيب نفسِه، بعددِ الخطة ناقصَ الثلاثين، ونسبتُها مقابل نسبة
    الفوج الأول. فلا يدخلها ما قبل م٢ (ولا ما بعد الثلاثين في م١: يُعدّ في m1_after_baseline) ولا ما بعدها ولا النسبةُ
    التراكمية؛ ولا حكمَ قبل أن يكتمل فوجُها ويُعرف موضعُ كلِّ رحلةٍ فيه."""
    block = {"rule": M2_RULE, "source": None, "baseline_journeys": BASELINE_MIN_DATED_JOURNEYS, "target_journeys": None,
             "required_points": None, "comparison_size": None, "window": None,
             "m1_after_baseline": m1_after,
             "comparison": None, "baseline_completion_rate": None, "comparison_completion_rate": None,
             "improvement_points": None, "passed": None, "missing": []}
    if gate is None:
        block["missing"].append("comparison_gate_unknown")
    else:
        block.update(gate)
    if window["from"] is None or window["until"] is None:
        block["missing"].append("baseline_window_unknown")
    if block["missing"]:
        return block, {}
    size, m2_from, m2_until = gate["comparison_size"], gate["window"]["from"], gate["window"]["until"]
    in_m2 = sorted((journey for journey in records
                    if journey["date"] is not None and m2_from <= journey["date"] <= m2_until), key=_order)
    after = in_m2[:size]
    full = len(after) == size
    cut = after[-1]["date"] if full else m2_until
    undated = sum(1 for journey in records if journey["date"] is None and _may_fall_in(journey, m2_from, cut))
    order_unknown = full and _cut_order_unknown(after, in_m2[size:], cut)
    outcomes, dates = _tally(after)
    blockers = ((["baseline_not_ready"] if baseline_missing else [])
                + (["baseline_window_overlaps_m2"] if window["until"] >= m2_from else []) + unreadable
                + ([UNDATED_BEFORE_CUT] if undated else []) + (["comparison_cut_order_unknown"] if order_unknown else [])
                + (["too_few_comparison_journeys"] if not full else []))
    rate, reason = _rate(outcomes, len(after), blockers)
    block["comparison"] = {"journeys": len(after), "window_dated_journeys": len(in_m2),
                           "by_date": dict(sorted(dates.items())), "by_outcome": outcomes, "completion_rate": rate,
                           "completion_rate_unavailable_reason": reason, "cut_date": cut if full else None,
                           "cut_order_known": not order_unknown, "undated_journeys_before_cut": undated}
    block["missing"] = blockers
    if not blockers:
        # الحكمُ بالأعداد لا بالنسبتين المقرَّبتين: نسبةُ المقارنة ناقصُ نسبة الفوج الأول، بالنقاط المئوية
        improvement = (Fraction(outcomes["completed"], len(after))
                       - Fraction(baseline["by_outcome"]["completed"], baseline["journeys"])) * 100
        block.update(baseline_completion_rate=baseline["completion_rate"], comparison_completion_rate=rate,
                     improvement_points=round(float(improvement), 2), passed=improvement >= gate["required_points"])
    return block, {id(journey): index for index, journey in enumerate(after, 1)}


def _baseline(records: list[dict], window: dict, unreadable: list[str]) -> tuple[dict | None, list[str], list[dict]]:
    """(الفوج، وما ينقصه، وأعضاؤه) من بيانات اليوم بنافذةٍ معطاة: أولُ ثلاثين مؤرَّخةٍ فيها بالترتيب الثابت."""
    start, end = window["from"], window["until"]
    missing, cohort, members = [], None, []
    if start is None or end is None:
        missing.extend(["baseline_window_unknown", *unreadable])
    else:
        in_window = sorted((journey for journey in records
                            if journey["date"] is not None and start <= journey["date"] <= end), key=_order)
        members, rest = in_window[:BASELINE_MIN_DATED_JOURNEYS], in_window[BASELINE_MIN_DATED_JOURNEYS:]
        full = len(members) == BASELINE_MIN_DATED_JOURNEYS
        cut = members[-1]["date"] if full else end
        undated = sum(1 for journey in records if journey["date"] is None and _may_fall_in(journey, start, cut))
        order_unknown = full and _cut_order_unknown(members, rest, cut)
        cohort_outcomes, cohort_dates = _tally(members)
        blockers = (unreadable + ([UNDATED_BEFORE_CUT] if undated else [])
                    + ([CUT_ORDER_UNKNOWN] if order_unknown else []))
        cohort_rate, cohort_reason = _rate(cohort_outcomes, len(members), blockers)
        cohort = {"journeys": len(members), "window_dated_journeys": len(in_window),
                  "distinct_dates": len(cohort_dates), "by_date": dict(sorted(cohort_dates.items())),
                  "by_outcome": cohort_outcomes, "completion_rate": cohort_rate,
                  "completion_rate_unavailable_reason": cohort_reason, "cut_date": cut if full else None,
                  "undated_journeys_before_cut": undated, "cut_order_known": not order_unknown}
        missing.extend(blockers)
    if cohort is None or cohort["journeys"] < BASELINE_MIN_DATED_JOURNEYS:
        missing.append("too_few_dated_journeys")
    if cohort is None or cohort["distinct_dates"] < BASELINE_MIN_DISTINCT_DATES:
        missing.append("too_few_distinct_dates")
    return cohort, missing, members


def _members_digest(members: list[dict]) -> str:
    """بصمةُ هويّة أعضاء الفوج: معرّفاتُ جلساتٍ عشوائية ومواضعُ جولات، لا نصّ؛ لا تخرج إلا في خطّ الأساس المجمَّد."""
    return digest(sorted([member["_order"]["session"], member["_order"]["index"]] for member in members))


def _drift(frozen: dict, cohort: dict | None, missing: list[str], members: list[dict]) -> list[str]:
    """ما اختلف فيه الحسابُ من بيانات اليوم عن خطّ الأساس المجمَّد، بأسمائه؛ يُعلَن ولا يحلّ محلَّه."""
    drift = []
    if not members or _members_digest(members) != frozen["members_digest"]:
        drift.append("baseline_members_changed")
    if cohort is None or cohort["by_outcome"] != frozen["cohort"]["by_outcome"]:
        drift.append("baseline_outcomes_changed")
    if missing:
        drift.append("baseline_not_ready_now")
    return drift


def build_report(scanned: dict, *, generated_on: str, default_root: bool, commit: str | None,
                 window: dict, gate: dict | None = None, frozen: dict | None = None) -> dict:
    """التقرير. ومع خطّ أساسٍ مجمَّد يُقارَن به هو ونافذتُه، ويُعلَن ما اختلف فيه الحسابُ من بيانات اليوم (drift)."""
    records = scanned["journeys"]
    total = len(records)
    by_outcome, by_date = _tally(records)
    by_mode = {name: 0 for name in MODES}
    for journey in records:
        by_mode[journey["mode"]] = by_mode.get(journey["mode"], 0) + 1
    dated = sum(by_date.values())
    counts = scanned["counts"]
    unreadable = [UNREADABLE_ENTRIES] if counts["unreadable_projects"] or counts["unreadable_sessions"] else []
    completion_rate, rate_reason = _rate(by_outcome, total, unreadable)
    if frozen is not None:
        window = frozen["window"]
    now, missing_now, members = _baseline(records, window, unreadable)
    positions = {id(journey): index for index, journey in enumerate(members, 1)}
    m1_after = None if now is None else now["window_dated_journeys"] - now["journeys"]
    if frozen is None:
        cohort, missing = now, missing_now
        state = {"state": "not_frozen", "digest": None, "frozen_on": None, "refreeze_reason": None, "drift": [],
                 "recomputed_missing": None}
    else:
        cohort, missing = dict(frozen["cohort"]), []
        state = {"state": "loaded", "digest": frozen["digest"], "frozen_on": frozen["frozen_on"],
                 "refreeze_reason": frozen["refreeze_reason"], "drift": _drift(frozen, now, missing_now, members),
                 "recomputed_missing": missing_now}
    m2_gate, compared = _m2_gate(records, window, gate, cohort, missing, unreadable, m1_after)
    journeys = sorted(({**{key: value for key, value in journey.items() if not key.startswith("_")},
                        "baseline_position": positions.get(id(journey)),
                        "comparison_position": compared.get(id(journey))} for journey in records),
                      key=lambda j: (j["date"] or "", j["baseline_position"] or 0, j["comparison_position"] or 0, j["mode"],
                                     j["outcome"], j["status"], j["error_code"] or "",
                                     -1 if j["steps"] is None else j["steps"], j["tool_calls"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "task": TASK,
        "commit": commit,
        "generated_on": generated_on,
        "root": "default" if default_root else "custom",
        "carries": CARRIES,
        "totals": {"journeys": total, "dated_journeys": dated, "undated_journeys": total - dated,
                   "projects": counts["projects"], "sessions": counts["sessions"]},
        "unreadable": {"projects": counts["unreadable_projects"], "sessions": counts["unreadable_sessions"],
                       "by_code": scanned["unreadable"]},
        "by_outcome": by_outcome,
        "by_mode": by_mode,
        "by_date": dict(sorted(by_date.items())),
        "distinct_dates": len(by_date),
        "cumulative_completion_rate": completion_rate,
        "cumulative_completion_rate_unavailable_reason": rate_reason,
        "baseline_ready": not missing,
        "baseline": {"rule": BASELINE_RULE, "window": window, "min_dated_journeys": BASELINE_MIN_DATED_JOURNEYS,
                     "min_distinct_dates": BASELINE_MIN_DISTINCT_DATES, "cohort": cohort, "missing": missing,
                     "frozen": state},
        "m2_gate": m2_gate,
        "outcome_labels": OUTCOME_LABELS,
        "journeys": journeys,
        "measurement_limits": list(MEASUREMENT_LIMITS),
    }


_CONSTANTS = frozenset({TOOL, TASK, CARRIES, BASELINE_RULE, M2_RULE, *MEASUREMENT_LIMITS, *FROZEN_LIMITS,
                        *OUTCOME_LABELS.values()})


def _allowed(text) -> bool:
    return (text in _CONSTANTS or bool(DATE.fullmatch(text))
            or (bool(MACHINE_CODE.fullmatch(text)) and not HEX_RUN.search(text)))


def _only_codes(value, hex_paths: frozenset, path: tuple = ()) -> None:
    """كلُّ نصٍّ (مفتاحًا أو قيمة) ثابتٌ أو تاريخٌ أو رمزُ آلةٍ بلا تسلسلٍ ستّ عشريّ؛ والبصماتُ في مواضعها المسمّاة وحدها."""
    if path in hex_paths:
        if value is not None and not (isinstance(value, str) and (SHA256.fullmatch(value) or COMMIT.fullmatch(value))):
            raise Refused("report_leak")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not _allowed(key):
                raise Refused("report_leak")
            _only_codes(item, hex_paths, (*path, key))
    elif isinstance(value, list):
        for item in value:
            _only_codes(item, hex_paths, (*path, "[]"))
    elif isinstance(value, str):
        if not _allowed(value):
            raise Refused("report_leak")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise Refused("report_leak")


def check_report(report: dict) -> None:
    """التقريرُ أعدادٌ ورموزُ آلةٍ وتواريخُ وأزمنةٌ فقط: مفاتيحُه ومفاتيحُ كلِّ رحلةٍ هي ما تبنيه هذه الوحدةُ لا غير، وكلُّ
    نصٍّ فيه (مفتاحًا أو قيمة) ثابتٌ من هذه الوحدة أو تاريخٌ أو رمزُ آلةٍ بلا تسلسلٍ ستّ عشريٍّ طويل، والبصمتان الوحيدتان
    بصمةُ الإيداع في `commit` وبصمةُ خطّ الأساس المجمَّد في `baseline.frozen.digest`. وما سوى ذلك `report_leak`. وحدُّه
    المعلَن: قيمةٌ بشكل رمز آلة في حقلٍ مفتوح (الحالة ورمزُ الخطأ) لا يُعرف أهي من المنتج أم من نصّ المالك؛ ذلك يحرسه
    الاختبارُ ببايتات التقرير لا هذا الفحص."""
    if report.get("commit") is not None and not COMMIT.fullmatch(str(report["commit"])):
        raise Refused("report_leak")
    if (set(report) != REPORT_FIELDS or not isinstance(report["journeys"], list)
            or any(not isinstance(journey, dict) or set(journey) != JOURNEY_FIELDS for journey in report["journeys"])):
        raise Refused("report_leak")
    frozen = report["baseline"].get("frozen") if isinstance(report["baseline"], dict) else None
    if frozen is not None and (not isinstance(frozen, dict) or (frozen.get("digest") is not None
                                                                 and not SHA256.fullmatch(str(frozen["digest"])))):
        raise Refused("report_leak")
    _only_codes(report, frozenset({("commit",), ("baseline", "frozen", "digest")}))


_FROZEN_HEX = frozenset({("commit",), ("members_digest",), ("history", "[]", "digest"), ("acknowledged_reports", "[]")})


def _artifact(report: dict, members: list[dict], *, today: str, previous: dict | None, reason: str | None,
              lost: set[str], damaged: set[str] = frozenset()) -> dict:
    """خطُّ الأساس المجمَّد: نافذتُه ومصدرُها، وفوجُه بأعداده ونسبته، وبصمةُ هويّة أعضائه، وإصدارُ الأداة؛ وسجلُّ ما جُمِّد
    قبله ببصماته وسبب إعادة التجميد، فلا يضيع أثرُ خطّ أساسٍ نُشر."""
    history = [] if previous is None else [*previous["history"], {"digest": previous["digest"],
                                                                  "frozen_on": previous["frozen_on"], "reason": reason}]
    history += [{"digest": value, "frozen_on": None, "reason": reason} for value in sorted(lost)]
    return {"schema_version": SCHEMA_VERSION, "tool": TOOL, "task": TASK, "kind": "journeys_baseline",
            "commit": report["commit"], "frozen_on": today, "window": report["baseline"]["window"],
            "cohort": {key: report["baseline"]["cohort"][key] for key in FROZEN_COHORT_FIELDS},
            "members_digest": _members_digest(members), "refreeze_reason": reason, "history": history,
            "acknowledged_reports": sorted({*(previous["acknowledged_reports"] if previous else ()), *damaged}),
            "measurement_limits": list(FROZEN_LIMITS)}


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _code_or_none(value) -> bool:
    return value is None or (isinstance(value, str) and bool(MACHINE_CODE.fullmatch(value)) and not HEX_RUN.search(value))


def _history_entry(item) -> bool:
    return (isinstance(item, dict) and set(item) == {"digest", "frozen_on", "reason"}
            and isinstance(item["digest"], str) and bool(SHA256.fullmatch(item["digest"]))
            and (item["frozen_on"] is None or _day(item["frozen_on"]) is not None) and _code_or_none(item["reason"]))


def _frozen_consistent(cohort: dict) -> bool:
    """أعدادُ الفوج المجمَّد متّسقةٌ فيما بينها: النتائجُ والأيامُ تجمع عددَه، وعددُ أيامه عددُ مفاتيحها، ونسبتُه منجزُه على
    عدده كما تحسبها الأداة. فلا يحمل تقريرٌ نسبةً وتحكم البوابةُ بعددٍ يناقضها."""
    journeys = cohort["journeys"]
    return (sum(cohort["by_outcome"].values()) == journeys
            and sum(cohort["by_date"].values()) == journeys
            and len(cohort["by_date"]) == cohort["distinct_dates"]
            and cohort["completion_rate"] == round(cohort["by_outcome"]["completed"] / journeys, 4))


def _frozen_schema(value) -> bool:
    """مخطّطُ خطّ الأساس المجمَّد كلُّه، حقلًا حقلًا ونوعًا نوعًا، قبل أيّ استعمال."""
    if not isinstance(value, dict) or set(value) != FROZEN_FIELDS:
        return False
    window, cohort, history = value["window"], value["cohort"], value["history"]
    return (value["schema_version"] == SCHEMA_VERSION and value["tool"] == TOOL and value["task"] == TASK
            and value["kind"] == "journeys_baseline"
            and (value["commit"] is None or (isinstance(value["commit"], str) and bool(COMMIT.fullmatch(value["commit"]))))
            and _day(value["frozen_on"]) is not None
            and isinstance(window, dict) and set(window) == {"from", "from_source", "until", "until_source"}
            and _day(window["from"]) is not None and _day(window["until"]) is not None
            and window["from"] <= window["until"]
            and window["from_source"] in ("option", "plan_m1_phase") and window["until_source"] in ("option", "plan_m1_phase")
            and isinstance(cohort, dict) and set(cohort) == set(FROZEN_COHORT_FIELDS)
            and cohort["journeys"] == BASELINE_MIN_DATED_JOURNEYS and _count(cohort["journeys"])
            and _count(cohort["distinct_dates"]) and cohort["distinct_dates"] >= BASELINE_MIN_DISTINCT_DATES
            and isinstance(cohort["by_date"], dict)
            and all(_day(day) is not None and _count(n) for day, n in cohort["by_date"].items())
            and isinstance(cohort["by_outcome"], dict) and _count(cohort["by_outcome"].get("completed"))
            and all(_code_or_none(name) and name is not None and _count(n) for name, n in cohort["by_outcome"].items())
            and isinstance(cohort["completion_rate"], (int, float)) and not isinstance(cohort["completion_rate"], bool)
            and 0 <= cohort["completion_rate"] <= 1 and _day(cohort["cut_date"]) is not None
            and _frozen_consistent(cohort)
            and isinstance(value["members_digest"], str) and bool(SHA256.fullmatch(value["members_digest"]))
            and _code_or_none(value["refreeze_reason"])
            and isinstance(history, list) and all(_history_entry(item) for item in history)
            and isinstance(value["acknowledged_reports"], list)
            and all(isinstance(item, str) and SHA256.fullmatch(item) for item in value["acknowledged_reports"])
            and value["measurement_limits"] == list(FROZEN_LIMITS))


def _load_frozen(path: Path) -> dict | None:
    """خطُّ الأساس المجمَّد، أو None إن لم يُجمَّد قطّ؛ وما لا يُقرأ أو خالف مخطّطَه `frozen_baseline_unreadable`، لا أثرٌ خام
    ولا إعادةُ حساب."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise Refused("frozen_baseline_unreadable") from None
    try:
        value = json.loads(data.decode("utf-8"))
        valid = _frozen_schema(value)
    except (ValueError, UnicodeError, RecursionError):
        valid = False
    if not valid:
        raise Refused("frozen_baseline_unreadable")
    # بعد المخطّط لا شيءَ يُفترض: كلُّ حقلٍ هنا ثبت نوعُه وشكلُه
    own = hashlib.sha256(data).hexdigest()
    return {**value, "digest": own, "known": {own, *(item["digest"] for item in value["history"])}}


def _report_name(name: str) -> bool:
    """اسمُ تقرير رحلاتٍ منشور كما يقرؤه الماسح: journeys-*.json سوى المجمَّد. ولا يُنشر في docs/probe باسمٍ غيره، فلا
    يولد مجمَّدٌ من تقريرٍ لا يراه التشغيلُ التالي."""
    return fnmatch.fnmatchcase(name, "journeys-*.json") and name != FROZEN_NAME


def _pointers(probe: Path, acknowledged: frozenset = frozenset()) -> tuple[set[str], set[str]]:
    """(بصماتُ خطّ الأساس المجمَّد التي سجّلتها تقاريرُ الرحلات المنشورة، وبصماتُ ملفّات التقارير التالفة): أثرٌ يفرّق «لم
    يُجمَّد قطّ» من «جُمِّد ثم ضاع». والتقريرُ التالف (مؤشّرُه ليس بصمةً من ٦٤ محرفًا) لا يُخمَّن مؤشّرُه؛ إلا ما أقرّت به
    إعادةُ تجميدٍ سابقة ببصمة ملفّه بعينها (`acknowledged_reports`)."""
    found, damaged = set(), set()
    for path in sorted(probe.glob("journeys-*.json")):
        if not _report_name(path.name):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            raise Refused("journeys_report_unreadable") from None
        own = hashlib.sha256(data).hexdigest()
        if own in acknowledged:
            continue
        try:
            value = json.loads(data.decode("utf-8"))
            state = value["baseline"].get("frozen")
            pointer = None if state is None else state["digest"]
            if pointer is not None and not (isinstance(pointer, str) and SHA256.fullmatch(pointer)):
                raise ValueError("frozen_pointer_invalid")
        except (ValueError, UnicodeError, KeyError, TypeError, AttributeError, RecursionError):
            damaged.add(own)
            continue
        if pointer is not None:
            found.add(pointer)
    return found, damaged


# عمليتا النشر الذرّيتان، باسمين في الوحدة ليُحقن فيهما العطبُ في الاختبار
_link = os.link
_swap = os.replace


def _publish(out: Path, report: bytes, frozen_path: Path | None = None, frozen: bytes | None = None) -> None:
    """ينشر التقريرَ وخطَّ الأساس المجمَّد معًا: يُكتب كلاهما مؤقّتًا كاملًا في دليله، ثم يُنشر التقريرُ بإنشاءٍ حصريٍّ ذرّيّ
    (وصلةٌ صلبة لا تكتب فوق قائم)، ثم يُستبدل المجمَّدُ ذرّيًّا. فإن سقط الاستبدالُ أُزيل التقريرُ الجديد وبقي المجمَّدُ
    السابق كما كان (أو غائبًا)؛ فلا يبقى مجمَّدٌ بلا تقريرٍ نشر بصمتَه."""
    staged = [out.with_name(f".{out.name}.{os.getpid()}.tmp")]
    if frozen is not None:
        staged.append(frozen_path.with_name(f".{frozen_path.name}.{os.getpid()}.tmp"))
    try:
        _write(staged[0], report)
        if frozen is not None:
            _write(staged[1], frozen)
        try:
            _link(staged[0], out)
        except FileExistsError:
            raise Refused("output_exists") from None
        except OSError as error:
            raise Refused("output_unwritable") from error
        if frozen is not None:
            try:
                _swap(staged[1], frozen_path)
            except OSError:
                out.unlink(missing_ok=True)
                raise Refused("baseline_publish_failed") from None
    finally:
        for path in staged:                 # ما نُشر صار وصلةً أو استُبدل؛ والمؤقّتُ يُزال، وقد لا يكون كُتب أصلًا
            with contextlib.suppress(OSError):
                path.unlink()


def _write(out: Path, data: bytes) -> None:
    try:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    except FileExistsError:
        raise Refused("output_exists") from None
    except FileNotFoundError:
        raise Refused("output_dir_missing") from None
    except OSError:
        raise Refused("output_unwritable") from None
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        out.unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="سجلُّ رحلات المالك بلا نصوص: أعدادٌ ورموزٌ وتواريخ من var/daily-ui")
    parser.add_argument("--root", type=Path, default=None, help="مخزنُ الواجهة (الافتراضيّ var/daily-ui)")
    parser.add_argument("--out", type=Path, default=None, help="ملفُّ التقرير (الافتراضيّ docs/probe/journeys-<اليوم>.json)")
    parser.add_argument("--baseline-from", default=None, help="أولُ يومٍ في نافذة خطّ الأساس YYYY-MM-DD (الافتراضيّ بدءُ م١ في الخطة)")
    parser.add_argument("--baseline-until", default=None, help="آخرُ يومٍ فيها YYYY-MM-DD (الافتراضيّ نهايةُ م١ في الخطة)")
    parser.add_argument("--refreeze-baseline", default=None, metavar="REASON_CODE",
                        help="إعادةُ تجميد خطّ الأساس بسببٍ برمز آلة (مثل cut_order_unknown_on_first_day)")
    args = parser.parse_args(argv)
    today = datetime.now(timezone.utc).date()
    root = DEFAULT_ROOT if args.root is None else args.root
    out = args.out if args.out is not None else PROBE_DIR / f"journeys-{today:%Y%m%d}.json"
    # التقريرُ في docs/probe منشور: فيه يُجمَّد خطُّ الأساس أولَ ما يجهز، ومنه تُقرأ آثارُ ما جُمِّد
    publish = out.resolve().parent == PROBE_DIR.resolve()
    frozen_path = PROBE_DIR / FROZEN_NAME
    reason = args.refreeze_baseline
    try:
        if publish and not _report_name(out.name):
            raise Refused("probe_output_name_invalid")
        if reason is not None and (not MACHINE_CODE.fullmatch(reason) or HEX_RUN.search(reason)):
            raise Refused("refreeze_reason_invalid")
        if reason is not None and not publish:
            raise Refused("refreeze_requires_probe_output")
        requested = baseline_window(args.baseline_from, args.baseline_until)
        try:
            frozen = _load_frozen(frozen_path)
        except Refused:
            if reason is None:
                raise
            frozen = None                   # إعادةُ التجميد تستبدل مجمَّدًا تالفًا، وبصماتُ ما نُشر تبقى في history
        pointers, damaged = _pointers(PROBE_DIR,
                                      frozenset(frozen["acknowledged_reports"]) if frozen is not None else frozenset())
        if damaged and reason is None:
            raise Refused("journeys_report_unreadable")
        if frozen is None and pointers and reason is None:
            raise Refused("frozen_baseline_lost")
        if frozen is not None and reason is None:
            if pointers - frozen["known"]:
                raise Refused("frozen_baseline_changed")
            if frozen["digest"] not in pointers:
                raise Refused("frozen_baseline_unpublished")
            if ((args.baseline_from is not None or args.baseline_until is not None)
                    and (requested["from"], requested["until"]) != (frozen["window"]["from"], frozen["window"]["until"])):
                raise Refused("baseline_already_frozen")
        if publish and os.path.lexists(out):
            raise Refused("output_exists")
        scanned = scan(root)
        report = build_report(scanned, generated_on=today.isoformat(), default_root=args.root is None,
                              commit=_commit(), window=requested, gate=plan_m2_gate(),
                              frozen=None if reason is not None else frozen)
        state = report["baseline"]["frozen"]
        if publish and (frozen is None or reason is not None) and report["baseline_ready"]:
            _, _, members = _baseline(scanned["journeys"], report["baseline"]["window"],
                                      [UNREADABLE_ENTRIES] if report["unreadable"]["projects"] or
                                      report["unreadable"]["sessions"] else [])
            artifact = _artifact(report, members, today=today.isoformat(), previous=frozen, reason=reason,
                                 lost=pointers - (frozen["known"] if frozen is not None else set()), damaged=damaged)
            _only_codes(artifact, _FROZEN_HEX)
            data = (json.dumps(artifact, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            state.update(state="refrozen" if frozen is not None else "frozen_now",
                         digest=hashlib.sha256(data).hexdigest(), frozen_on=today.isoformat(), refreeze_reason=reason)
        elif reason is not None:
            raise Refused("refreeze_baseline_not_ready")
        else:
            data = None
            if report["baseline_ready"] and state["state"] == "not_frozen":
                state["state"] = "ready_not_published"
        check_report(report)
        _publish(out, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), frozen_path, data)
    except Refused as exc:
        print(json.dumps({"status": "refused", "code": exc.code}))
        return 2
    print(json.dumps({"status": "written", "out": str(out), "journeys": report["totals"]["journeys"],
                      "unreadable_sessions": report["unreadable"]["sessions"],
                      "unreadable_projects": report["unreadable"]["projects"],
                      "baseline_ready": report["baseline_ready"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
