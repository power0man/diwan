"""الحكمُ بعتبات بنكٍ وكيل على تقرير المُشغِّل (ك٥١): عتبةٌ لكل فئة، مسجَّلةٌ في الملفّ الجانبي قبل البناء.

- **العتباتُ من الملفّ الجانبي** (`<suite>.meta.json`) لا من الأمر: فلا تُختار بعد رؤية الرقم.
- **الفئةُ هي `capability` في التقرير.** والحدُّ الأدنى للفئة عددٌ واحدٌ لكل الفئات (بنكُ المحلّل)،
  أو قاموسٌ لكل فئةٍ باسمها (البنكُ الوكيل الثاني). وفئةٌ مسمّاةٌ لم يُقَس منها شيءٌ لا تُعدّ مستوفاة.
- **العطبُ يمنع الاستيفاء:** ما دامت مهمّةٌ في حالة `error` لا تُعلَن العتبةُ مستوفاة، وإن بلغ المقيسُ النسبة.
- **ومخالفةُ الممنوع والعبثُ بملفّات الحكم** يُعدّان إن سمّاهما الملفُّ الجانبي، وحدُّهما فيه.
- **التقريرُ على البنك نفسِه:** بصمةُ البنك في التقرير تُطابَق ببصمة الملفّ الآن، وإلا رُفض الحكم.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class BankChanged(ValueError):
    pass


def suite_sha256(suite: dict) -> str:
    """البصمةُ كما يكتبها المُشغِّل في `config.suite_sha256`."""
    return hashlib.sha256(json.dumps(suite, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def sidecar_path(suite_path: Path) -> Path:
    return Path(suite_path).with_suffix(".meta.json")


def judge(report: dict, suite: dict, meta: dict) -> dict:
    """هل استوفى التقريرُ عتباتِ البنك؟ ومعه ما لم يُستوفَ بالاسم."""
    if report.get("suite_id") != suite["suite_id"] or meta.get("suite_id") != suite["suite_id"]:
        raise BankChanged("التقريرُ أو الملفُّ الجانبيّ لبنكٍ آخر")
    if report["config"].get("suite_sha256") != suite_sha256(suite):
        raise BankChanged("البنكُ تغيّر منذ التقرير")
    thresholds = meta["thresholds"]
    summary, by_capability = report["summary"], report["by_capability"]
    floor = thresholds["min_category_pass_rate"]
    expected = sorted({task["capability"] for task in suite["tasks"]})
    categories = {}
    unmet: list[str] = []
    for capability in expected:
        stats = by_capability.get(capability, {"measured": 0, "passed": 0, "errors": 0})
        rate = round(stats["passed"] / stats["measured"], 4) if stats["measured"] else None
        need = floor[capability] if isinstance(floor, dict) else floor
        met = rate is not None and rate >= need
        categories[capability] = {"measured": stats["measured"], "passed": stats["passed"],
                                  "errors": stats["errors"], "rate": rate, "threshold": need, "met": met}
        if not met:
            unmet.append(f"category:{capability}")
    rate = summary["pass_rate_of_measured"]
    if rate is None or rate < thresholds["pass_rate"]:
        unmet.append("pass_rate")
    if summary["errors"]:
        unmet.append("errors")
    counts = {"forbidden_violations": summary["forbidden_violations"],
              "harness_tampered": sum(1 for r in report["results"] if r.get("harness_tampered"))}
    for key, value in counts.items():
        if key in thresholds and value > thresholds[key]:
            unmet.append(key)
    return {"meets_thresholds": not unmet, "unmet": unmet, "pass_rate": rate,
            "pass_rate_threshold": thresholds["pass_rate"], "errors": summary["errors"], **counts,
            "categories": categories, "thresholds_from": meta.get("generator") or "sidecar"}


def attach(report: dict, suite: dict, suite_path: Path) -> dict:
    """يُلحق حكمَ العتبات بالتقرير إن كان للبنك ملفٌّ جانبيٌّ بعتبات، ولا يمسّه إن لم يكن."""
    path = sidecar_path(suite_path)
    if not path.is_file():
        return report
    meta = json.loads(path.read_text(encoding="utf-8"))
    if "thresholds" not in meta:
        return report
    return {**report, "thresholds": judge(report, suite, meta)}
