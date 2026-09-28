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

لا يحمل التقريرُ نصًّا ولا مسارًا ولا معرّفَ مشروعٍ أو جلسةٍ أو جولة ولا اسمًا ولا بصمةَ نصّ ولا جوابَ نموذج ولا شيئًا من
محتوى local_only: أعدادٌ ورموزُ آلةٍ وتواريخُ وأزمنةٌ فقط. والأداةُ تفحص تقريرَها بذلك قبل كتابته، فتقريرٌ فيه نصٌّ أو معرّفٌ
يُرفض باسمه (`report_leak`) ولا يُكتب.

الاستعمال (على الماك بعد الاستعمال):
    python tools/journeys.py                            # var/daily-ui ← docs/probe/journeys-<اليوم>.json
    python tools/journeys.py --root <مخزن> --out <ملف>
    python tools/journeys.py --baseline-from 2026-10-12 --baseline-until 2026-11-08   # نافذةٌ غيرُ نافذة الخطة

الرفضُ مسمًّى وبرمز خروجٍ غير صفري: `root_missing` (المخزنُ غائب أو ليس دليلًا)، `root_unsafe` (وصلةٌ رمزية)، `root_unreadable`،
`output_exists` (لا يُكتب فوق ملفٍّ قائم)، `output_dir_missing`، `output_unwritable`، `baseline_date_invalid`،
`baseline_window_invalid` (البدءُ بعد النهاية)، `report_leak`. والجلسةُ التي لا تُقرأ أو فسدت لا تُسقط
الأداة: تُعدّ في `unreadable` برمزها.

الحدود (وهي في التقرير `measurement_limits`): الواجهةُ لا تحفظ زمنَ الجولة، فالزمنُ فارغٌ بسببه المسمّى، والتاريخُ يوم
الجلسة حين يتّحد يومُ إنشائها (زمنُ تعديل meta.json) ويومُ آخر كتابةٍ فيها (زمنُ تعديل state.json)، وإلا فارغٌ بسببه؛
والنتيجةُ ما حفظته الواجهة لا جودةُ الجواب.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
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

SCHEMA_VERSION = 1
TOOL = "tools/journeys.py"
TASK = "جديد-journeys-log"
DEFAULT_ROOT = ROOT / "var" / "daily-ui"
PROBE_DIR = ROOT / "docs" / "probe"
PLAN = ROOT / "docs" / "PLAN-20260926.json"     # الخطةُ الحاكمة (ق٦٤): فيها بدءُ م١ ونهايتُها
M1 = "م١"
MAX_STATE_BYTES = 32 * 1024 * 1024              # حدُّ حالة الجلسة الوكيلة في المنتج نفسِه
IDENTIFIER = re.compile(r"[a-f0-9]{32}\Z")      # معرّفاتُ المشروعات والجلسات في LocalApp
MACHINE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
HEX_RUN = re.compile(r"[0-9a-f]{12,}")          # معرّفٌ أو بصمةٌ داخل ما يبدو رمزًا
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")

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
BASELINE_MIN_DISTINCT_DATES = 2
UNREADABLE_ENTRIES = "unreadable_entries"
UNDATED_BEFORE_CUT = "undated_journeys_before_cut"
CUT_ORDER_UNKNOWN = "baseline_cut_order_unknown"
DURATION_UNKNOWN = "turn_timestamps_not_stored"
MEASUREMENT_LIMITS = (
    "counts_only_what_the_daily_ui_stored_under_projects_a_session_left_in_staging_by_an_interrupted_creation_is_not_counted",
    "a_journey_is_one_stored_turn_one_request_of_the_owner_in_one_session_whatever_its_length",
    "steps_are_model_calls_recorded_for_the_turn_one_for_the_text_and_media_paths_and_tool_calls_are_the_calls_the_model_requested",
    "the_ui_stores_no_per_turn_timestamps_so_every_duration_is_null_with_its_named_reason",
    "the_date_is_the_utc_day_of_the_session_when_its_creation_meta_json_mtime_and_its_last_write_state_json_mtime_fall_on_the_same_utc_day_otherwise_null_with_its_reason_so_a_session_reused_across_days_leaves_its_journeys_undated",
    "file_mtimes_are_not_protected_a_restore_or_copy_that_resets_them_moves_or_hides_the_date",
    "a_turn_without_a_stored_result_is_counted_as_outcome_unknown_and_is_not_settled_from_the_call_ledger",
    "integrity_is_checked_by_the_state_envelope_digest_only_not_by_the_call_ledger_chain_nor_the_action_receipts",
    "the_outcome_is_what_the_ui_stored_and_says_nothing_about_the_quality_or_truth_of_the_answer",
    "the_data_is_the_owner_s_own_use_on_one_machine_not_a_sample_of_users",
    "the_report_carries_counts_machine_codes_utc_dates_and_durations_only_and_the_tool_refuses_to_write_a_report_with_any_other_string",
    "the_baseline_cohort_is_the_first_30_dated_journeys_inside_the_m1_window_recorded_in_the_plan_unless_the_owner_passes_baseline_from_or_baseline_until_and_the_cumulative_totals_are_not_the_baseline",
    "within_a_utc_day_journeys_are_ordered_by_their_session_s_creation_mtime_then_their_position_in_it_and_a_cut_between_sessions_whose_writes_overlap_or_tie_that_day_is_refused_not_guessed",
    "an_unreadable_entry_or_an_undated_journey_that_may_fall_before_the_cut_blocks_baseline_ready_and_nulls_the_completion_rate_it_would_bias",
    "a_later_write_to_a_session_that_holds_baseline_journeys_can_undate_or_reorder_them_so_the_baseline_is_the_first_report_that_says_baseline_ready",
)
CARRIES = "counts_machine_codes_utc_dates_and_durations_only"
# مخطّطُ التقرير مغلق: مفتاحٌ لا تبنيه `build_report` أو `_journey` تسرّبٌ ولو كانت قيمتُه رمزَ آلة
REPORT_FIELDS = frozenset({"schema_version", "tool", "task", "commit", "generated_on", "root", "carries", "totals",
                           "unreadable", "by_outcome", "by_mode", "by_date", "distinct_dates", "completion_rate",
                           "completion_rate_unavailable_reason", "baseline_ready", "baseline", "outcome_labels",
                           "journeys", "measurement_limits"})
JOURNEY_FIELDS = frozenset({"mode", "outcome", "status", "error_code", "steps", "tool_calls", "date", "date_basis",
                            "date_unknown_reason", "session_first_day", "session_last_day", "duration_s",
                            "duration_unknown_reason", "baseline_position"})
BASELINE_RULE = ("the_first_30_dated_journeys_inside_the_m1_window_by_utc_date_then_session_creation_then_turn_"
                 "on_at_least_2_distinct_utc_dates_with_no_unreadable_entry_no_undated_journey_before_the_cut_"
                 "and_a_provable_order_at_the_cut")

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


def _read_json(name, dir_fd, missing="state_missing") -> tuple[object, float]:
    """(القيمة، زمنُ التعديل) لملفٍّ عاديّ بلا اتّباع وصلة؛ وما سواه يُسمّى."""
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
    try:
        return json.loads(b"".join(chunks).decode("utf-8")), info.st_mtime
    except (ValueError, UnicodeError, RecursionError):
        raise Unreadable("state_corrupt") from None


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


def _journey(turn: dict, mode: str, agent: bool, dating: dict) -> dict:
    result = turn["result"]
    if result is None:
        status = "pending"
    elif isinstance(result, dict):
        status = machine_code(result.get("status")) or "unknown"
    else:
        status = "unknown"
    steps, tool_calls = 1, 0
    if agent:
        recorded = result.get("steps") if isinstance(result, dict) else None
        if isinstance(recorded, list):
            steps = len(recorded)
            tool_calls = sum(len(step["tool_calls"]) for step in recorded
                             if isinstance(step, dict) and isinstance(step.get("tool_calls"), list))
        else:
            steps = len(turn["calls"]) if isinstance(turn.get("calls"), list) else 0
    error_code = machine_code(result.get("error_code")) if isinstance(result, dict) else None
    return {
        "mode": mode,
        "outcome": UNSETTLED.get((status, error_code)) or OUTCOMES.get(status, status),
        "status": status,
        "error_code": error_code,
        "steps": steps,
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
    finally:
        os.close(state_fd)
    dating = _dating(created, last)
    # ترتيبُ الرحلة لقطع خطّ الأساس؛ داخليٌّ يُنزع قبل التقرير (معرّفُ الجلسة وأزمنتُها الدقيقة لا تخرج)
    return [{**_journey(turn, mode, mode in AGENT_MODES, dating),
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
        project_names = [] if projects_fd is None else _entries(projects_fd)
        for project in project_names:
            try:
                if not IDENTIFIER.fullmatch(project):
                    raise Unreadable("entry_invalid")
                project_fd = _open_dir(project, projects_fd)
            except Unreadable as exc:
                counts["unreadable_projects"] += 1
                unreadable[exc.code] += 1
                continue
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
                try:
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
            finally:
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


def build_report(scanned: dict, *, generated_on: str, default_root: bool, commit: str | None,
                 window: dict) -> dict:
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
    start, end = window["from"], window["until"]
    missing, cohort, positions = [], None, {}
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
        positions = {id(journey): index for index, journey in enumerate(members, 1)}
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
    journeys = sorted(({**{key: value for key, value in journey.items() if not key.startswith("_")},
                        "baseline_position": positions.get(id(journey))} for journey in records),
                      key=lambda j: (j["date"] or "", j["baseline_position"] or 0, j["mode"], j["outcome"], j["status"],
                                     j["error_code"] or "", j["steps"], j["tool_calls"]))
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
        "completion_rate": completion_rate,
        "completion_rate_unavailable_reason": rate_reason,
        "baseline_ready": not missing,
        "baseline": {"rule": BASELINE_RULE, "window": window, "min_dated_journeys": BASELINE_MIN_DATED_JOURNEYS,
                     "min_distinct_dates": BASELINE_MIN_DISTINCT_DATES, "cohort": cohort, "missing": missing},
        "outcome_labels": OUTCOME_LABELS,
        "journeys": journeys,
        "measurement_limits": list(MEASUREMENT_LIMITS),
    }


_CONSTANTS = frozenset({TOOL, TASK, CARRIES, BASELINE_RULE, *MEASUREMENT_LIMITS, *OUTCOME_LABELS.values()})


def check_report(report: dict) -> None:
    """التقريرُ أعدادٌ ورموزُ آلةٍ وتواريخُ وأزمنةٌ فقط: مفاتيحُه ومفاتيحُ كلِّ رحلةٍ هي ما تبنيه هذه الوحدةُ لا غير، وكلُّ
    نصٍّ فيه (مفتاحًا أو قيمة) ثابتٌ من هذه الوحدة أو تاريخٌ أو رمزُ آلةٍ بلا تسلسلٍ ستّ عشريٍّ طويل، والبصمةُ الوحيدة
    بصمةُ الإيداع في `commit`. وما سوى ذلك `report_leak`. وحدُّه المعلَن: قيمةٌ بشكل رمز آلة في حقلٍ مفتوح (الحالة ورمزُ
    الخطأ) لا يُعرف أهي من المنتج أم من نصّ المالك؛ ذلك يحرسه الاختبارُ ببايتات التقرير لا هذا الفحص."""
    if report.get("commit") is not None and not COMMIT.fullmatch(str(report["commit"])):
        raise Refused("report_leak")
    if (set(report) != REPORT_FIELDS or not isinstance(report["journeys"], list)
            or any(not isinstance(journey, dict) or set(journey) != JOURNEY_FIELDS for journey in report["journeys"])):
        raise Refused("report_leak")

    def allowed(text) -> bool:
        return (text in _CONSTANTS or bool(DATE.fullmatch(text))
                or (bool(MACHINE_CODE.fullmatch(text)) and not HEX_RUN.search(text)))

    def walk(value, top=False):
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str) or not allowed(key):
                    raise Refused("report_leak")
                if not (top and key == "commit"):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            if not allowed(value):
                raise Refused("report_leak")
        elif value is not None and not isinstance(value, (bool, int, float)):
            raise Refused("report_leak")

    walk(report, top=True)


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
    args = parser.parse_args(argv)
    today = datetime.now(timezone.utc).date()
    root = DEFAULT_ROOT if args.root is None else args.root
    out = args.out if args.out is not None else PROBE_DIR / f"journeys-{today:%Y%m%d}.json"
    try:
        window = baseline_window(args.baseline_from, args.baseline_until)
        report = build_report(scan(root), generated_on=today.isoformat(), default_root=args.root is None,
                              commit=_commit(), window=window)
        check_report(report)
        _write(out, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
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
