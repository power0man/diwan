#!/usr/bin/env python3
"""التقريرُ الشهريّ للإنفاق: كتلُ `spend` في أدلّة الشهر مجموعةً، وفاتورةُ HF بجانبها (جديد-spend-ledger، البند ٥ من #295).

يُكتب إلى `docs/probe/spend-<YYYY-MM>.json`. والمصدران:
- **الأدلّة:** كلُّ دليلٍ في `docs/probe/` تاريخُه (`date`) في الشهر ويحمل كتلةَ `spend` سليمة (`tools/probe_spend.py`). تُجمع
  الأعدادُ لكل أساس كلفة. والكلفةُ المعروفة تُجمع، وغيرُ المسعَّر يُعدّ ملفّاتٍ لا صفرًا. والكتلةُ المعيبة لا تُجمع، بل تُسمّى.
- **فاتورةُ HF:** تُقرأ من صفحة Billing بيد المالك أو جلسة الماك، وتُعطى بـ`--hf-invoice-usd` مع تاريخ القراءة ومصدرها.
  فلا تُقرأ من السحابة، ولا تُخمَّن. وبلاها يُكتب `hf_invoice: null` و`hf_invoice_status: not_read`.

**المقارنة:** الأساسان `reported_by_provider` و`estimated_from_prices` هما وحدهما ما يُفوتَر بالتوكن، وطريقُهما اليوم موجّهُ HF
(`tools/external_review.py --backend hf-router`). فالفرقُ بين الفاتورة ومجموعهما إنفاقٌ لا دليلَ له في المستودع: `hf_unaccounted_usd`.

أسماءُ الحقول خارج قائمة حارس الخصوصية (`tools/probe_evidence.py`): فلا `billed_usd` ولا `estimate_usd` ولا `credits`.
والكلفةُ `cost_usd` كما في كتلة الإنفاق (#303).

`--check` يعيد حسابَ شطر الأدلّة لتقريرٍ مكتوب ويقارنه، فلا يُعدَّل التقريرُ باليد. أمّا شطرُ الفاتورة فيُنقل كما كُتب.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import model_licenses as ml  # noqa: E402
from tools import probe_spend as ps  # noqa: E402

MONTH = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
REPORT = re.compile(r"^spend-\d{4}-\d{2}\.json$")
HTTPS = re.compile(r"^https://\S+$")
METERED = ("reported_by_provider", "estimated_from_prices")
LIMITS = [
    "sums_only_the_spend_blocks_of_evidence_dated_in_the_month_a_cloud_call_without_evidence_is_counted_only_in_the_hf_invoice",
    "the_hf_invoice_is_read_by_hand_from_the_billing_page_and_is_not_verified_by_this_tool",
    "hf_unaccounted_usd_assumes_every_metered_basis_is_the_hf_router_which_is_the_only_metered_path_today",
    "unpriced_evidence_is_counted_as_files_and_never_as_zero_cost",
    "the_ollama_subscription_is_a_flat_fee_outside_this_report",
]


def _money(value: float) -> float:
    return round(value, 6)


def _in_month(payload: dict, month: str) -> bool:
    day = payload.get("date")
    return isinstance(day, str) and day[:7] == month


def evidence_totals(evidence: dict[str, object], month: str) -> dict:
    """مجموعُ كتل الإنفاق لكل أساسٍ في أدلّة الشهر، والملفّاتُ غيرُ المسعَّرة والمعيبةُ بأسمائها."""
    by_basis: dict[str, dict] = {}
    unpriced: list[str] = []
    rejected: list[str] = []
    undated: list[str] = []
    for file, payload in sorted(evidence.items()):
        if REPORT.match(file) or not isinstance(payload, dict) or "spend" not in payload:
            continue
        if not ml._valid_day(str(payload.get("date", ""))[:10]):
            undated.append(file)   # إنفاقٌ بلا تاريخٍ لا يُنسب إلى شهر، فيُسمّى في كل تقرير ولا يُسقَط صامتًا
            continue
        if not _in_month(payload, month):
            continue
        spend = payload["spend"]
        problems = ps.spend_findings(file, spend)
        if problems:
            rejected += problems
            continue
        row = by_basis.setdefault(spend["cost_basis"], {"files": 0, "cloud_calls": 0, "prompt_tokens": 0,
                                                       "completion_tokens": 0, "cost_usd": 0.0})
        row["files"] += 1
        for key in ps.COUNTS:
            row[key] += spend[key]
        if spend["cost_usd"] is None:
            unpriced.append(file)
        else:
            row["cost_usd"] = _money(row["cost_usd"] + spend["cost_usd"])
    for basis in by_basis:
        if basis == "unpriced":
            by_basis[basis]["cost_usd"] = None
    known = _money(float(sum(row["cost_usd"] for row in by_basis.values() if row["cost_usd"] is not None)))
    metered = _money(float(sum(by_basis[b]["cost_usd"] for b in METERED if b in by_basis)))
    return {"by_basis": dict(sorted(by_basis.items())), "known_cost_usd": known, "metered_cost_usd": metered,
            "unpriced_files": unpriced, "rejected_spend": rejected, "undated_spend": undated}


def invoice(amount: float | None, read_on: str | None, source: str | None, month: str) -> dict | None:
    """فاتورةُ HF كما قرأها المالك: مبلغٌ منتهٍ غيرُ سالب، وتاريخُ قراءةٍ ليس قبل الشهر، ومصدرٌ https. وغيرُ ذلك يُرفض باسمه."""
    given = [amount is not None, read_on is not None, source is not None]
    if not any(given):
        return None
    if not all(given):
        raise ValueError("hf_invoice_incomplete")
    if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
        raise ValueError("hf_invoice_amount_invalid")
    if not ml._valid_day(read_on) or read_on[:7] < month:
        raise ValueError("hf_invoice_read_on_invalid")
    if not HTTPS.match(source):
        raise ValueError("hf_invoice_source_invalid")
    return {"cost_usd": _money(float(amount)), "read_on": read_on, "source": source}


def build(evidence: dict[str, object], month: str, hf: dict | None, today: str) -> dict:
    if not MONTH.match(month):
        raise ValueError("month_invalid")
    totals = evidence_totals(evidence, month)
    return {
        "schema_version": 1,
        "kind": "monthly_spend",
        "month": month,
        "date": today,
        "evidence": totals,
        "hf_invoice": hf,
        "hf_invoice_status": "read" if hf else "not_read",
        "hf_unaccounted_usd": None if hf is None else _money(hf["cost_usd"] - totals["metered_cost_usd"]),
        "measurement_limits": LIMITS,
    }


def check(report: dict, evidence: dict[str, object]) -> list[str]:
    """التقريرُ المكتوب يطابق إعادةَ حسابه من الأدلّة، وفرقُ الفاتورة يطابق الفاتورةَ المكتوبة."""
    month = report.get("month")
    if not isinstance(month, str) or not MONTH.match(month):
        return ["month_invalid"]
    problems = []
    if report.get("evidence") != evidence_totals(evidence, month):
        problems.append("evidence_totals_differ")
    hf = report.get("hf_invoice")
    try:
        expected_hf = None if hf is None else invoice(hf.get("cost_usd"), hf.get("read_on"), hf.get("source"), month)
    except (AttributeError, ValueError) as error:
        return problems + [str(error) if isinstance(error, ValueError) else "hf_invoice_malformed"]
    if expected_hf != hf:
        problems.append("hf_invoice_malformed")
    expected = build(evidence, month, expected_hf, report.get("date"))
    for key in ("hf_invoice_status", "hf_unaccounted_usd", "measurement_limits", "kind", "schema_version"):
        if report.get(key) != expected[key]:
            problems.append(f"{key}_differs")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--probe", type=Path, default=ml.PROBE)
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("build", help="يكتب docs/probe/spend-<month>.json")
    write.add_argument("--month", required=True, help="YYYY-MM")
    write.add_argument("--hf-invoice-usd", type=float)
    write.add_argument("--hf-read-on", help="YYYY-MM-DD: يومُ قراءة صفحة Billing")
    write.add_argument("--hf-source", help="رابطُ https للصفحة التي قُرئ منها المبلغ")
    write.add_argument("--today", default=date.today().isoformat())
    verify = sub.add_parser("check", help="يعيد حسابَ تقاريرَ مكتوبة ويقارنها")
    verify.add_argument("reports", nargs="*", type=Path)
    args = parser.parse_args(argv)
    evidence = ml.load_evidence(args.probe)
    if args.command == "build":
        try:
            hf = invoice(args.hf_invoice_usd, args.hf_read_on, args.hf_source, args.month)
            report = build(evidence, args.month, hf, args.today)
        except ValueError as error:
            print(json.dumps({"status": "refused", "code": str(error)}))
            return 2
        target = args.probe / f"spend-{args.month}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
        print(json.dumps({"status": "written", "file": str(target.name),
                          "rejected_spend": report["evidence"]["rejected_spend"]}, ensure_ascii=False))
        return 0
    reports = args.reports or sorted(p for p in args.probe.glob("spend-*.json") if REPORT.match(p.name))
    problems = {}
    for path in reports:
        found = check(json.loads(path.read_text(encoding="utf-8")), evidence)
        if found:
            problems[path.name] = found
    print(json.dumps({"status": "failed" if problems else "passed", "checked": len(reports), "findings": problems},
                     ensure_ascii=False))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
