#!/usr/bin/env python3
"""قياسُ محرّكٍ على بنكٍ كامل: يجمع ما يفرّقه `evaluate_capabilities` حزمةً حزمة.

`evaluate_suite` يقيس حزمةً واحدة، والبنكُ إحدى وثلاثون. فهذا يجمعها في رقمٍ
واحد لكل محرّك — **ومعه ما يجعله قابلًا للقراءة**:

- **الرقمُ يُفصَّل بالطبقة**، فمتوسّطٌ واحدٌ يخفي أن محرّكًا يتفوّق في البرمجة
  ويسقط في الامتناع. والطبقة (ج) تقيس الامتناعَ لا المعرفة، فخلطُها بالبقيّة
  يكافئ الجرأة.
- **الحالاتُ بلا فحصٍ آليّ تُعزل ولا تُحسب نجاحًا.** فحالةٌ لا فحصَ لها لا
  تشهد للمحرّك بشيء، وعدُّها في المقام يخفض الدرجة بلا سبب، وعدُّها في البسط
  يرفعها بلا دليل.
- **أخطاءُ التنفيذ تُعلن ولا تُبتلع.** نداءٌ سقط ليس جوابًا خاطئًا.

ولا يُقارن محرّكان إلا على الحزم نفسِها: `--require-same-suites` يتوقّف إن
اختلفت، فمقارنةُ رقمٍ على عيّنةٍ برقمٍ على أخرى لا معنى لها.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.sandbox import configure_sandbox_backend
from tools.model_digest import ModelDigestError, pin_model_digest, quarantine_runs_since, verify_model_digest
from evaluation.capabilities import CapabilityError, evaluate_suite, load_suite
from providers.ollama import OllamaProvider


_TRAILING_PUNCTUATION = ".,،؛;:!?؟…"


def _trim_terminal_punctuation(value: str) -> str:
    """قراءةٌ تفسيرية فقط؛ لا تغيّر حكم `exact` الصارم (ق٥٧)."""
    return value.rstrip().rstrip(_TRAILING_PUNCTUATION).rstrip()


def _lenient_exact_recovery(result: dict) -> bool:
    """هل كان الرسوب الصارم سببه الوحيد ترقيمًا ختاميًّا في فحص exact؟"""
    if result.get("status") != "complete" or result.get("automatic_pass") is not False:
        return False
    checks = result.get("checks")
    answer = result.get("answer")
    if not isinstance(checks, list) or not checks or not isinstance(answer, str):
        return False
    recovered = False
    for check in checks:
        if check.get("passed") is True:
            continue
        if check.get("kind") != "exact" or not isinstance(check.get("value"), str):
            return False
        if _trim_terminal_punctuation(answer) != _trim_terminal_punctuation(check["value"]):
            return False
        recovered = True
    return recovered


def _summary_counts(summary: dict, offered: int) -> tuple[int, int, int]:
    """يرفض تقريرًا يجعل خطأَ التنفيذ نجاحًا أو يغيّر مقام العيّنة."""
    names = ("cases", "automatic_passes", "automatic_failures",
             "without_checks", "execution_errors")
    if any(type(summary.get(name)) is not int or summary[name] < 0 for name in names):
        raise CapabilityError("report.summary", "measurement_counts_invalid",
                              "عدادات التقرير أعداد صحيحة غير سالبة مطلوبة")
    passes = summary["automatic_passes"]
    failures = summary["automatic_failures"]
    without_checks = summary["without_checks"]
    errors = summary["execution_errors"]
    judged = passes + failures
    if (summary["cases"] != offered
            or passes + failures + without_checks + errors != offered
            or passes > judged or judged > offered):
        raise CapabilityError("report.summary", "measurement_counts_invalid",
                              "الحالات والأخطاء والأحكام لا تطابق المقام المعروض")
    return passes, failures, judged


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _tier_of(suite_id: str) -> str:
    body = suite_id[len("sample_"):] if suite_id.startswith("sample_") else suite_id
    return body.split("__")[0] if "__" in body else "غير_مصنّف"


def measure(suites, model: str, out_dir: Path, *,
            max_output: int, deadline_s: int, model_version: str,
            provider=None) -> dict:
    """يقيس ويُبلغ بمقامٍ **ثابت**: عددُ الحالات المعروضة، لا ما تصادف أن قِيس.

    العلّةُ التي يعالجها هذا: `judged` كان خاصيّةً للمحرّك لا للبنك. فالحالةُ
    التي يُقطع جوابُها عند `max_output`، أو يسقط نداؤها، أو تسقط حزمتُها كلُّها
    — تخرج من البسط **ومن المقام معًا**. فالمحرّكُ الذي يُخفق بأكثر من طريقةٍ
    يصغر مقامُه فترتفع نسبتُه، ومحرّكان يُقارنان على مقامين مختلفين.

    فتُنشر ثلاثةُ أرقامٍ لا واحد:
      - `offered`: كلُّ حالةٍ في العيّنة، تُعدّ من الملفّ قبل أي نداء. لا يغيّرها
        المحرّك، فهي وحدها تصلح أساسًا للمقارنة.
      - `pass_rate_offered = passes / offered` — المحافظ. ما لم يُقَس يُحسب
        غيرَ ناجح، فلا يربح أحدٌ بإخفاقٍ لم يُسجَّل.
      - `pass_rate_judged = passes / judged` — ما كان يُنشر، ويبقى للتشخيص وحده.
    والفرقُ بينهما هو `coverage`، ويُنشر معهما.
    """
    provider = provider or OllamaProvider(model)
    if "cloud" in model or "oss" in model:
        setattr(provider, "allow_thinking", True)
    per_tier = defaultdict(lambda: defaultdict(int))
    per_suite, failures = [], []
    started = time.monotonic()
    for index, path in enumerate(suites, 1):
        suite = load_suite(path)
        tier = _tier_of(suite["suite_id"])
        offered = len(suite["cases"])
        # يُعدّ المعروضُ قبل النداء، فتبقى الحزمةُ الساقطة في المقام
        per_tier[tier]["offered"] += offered
        try:
            report = evaluate_suite(suite, provider, out_dir, max_output=max_output,
                                    deadline_s=deadline_s, model_version=model_version)
            s = report["summary"]
            passes, _, judged = _summary_counts(s, offered)
        except CapabilityError as exc:
            failures.append({"suite_id": suite["suite_id"], "tier": tier,
                             "offered": offered, "code": exc.code})
            per_tier[tier]["lost_to_failed_suite"] += offered
            print(f"[{index}/{len(suites)}] ✗ {suite['suite_id']}: {exc.code} "
                  f"({offered} حالةً تبقى في المقام ولا تُحسب نجاحًا)", flush=True)
            continue
        lenient_recoveries = sum(_lenient_exact_recovery(r)
                                 for r in report.get("results", []))
        for key in ("cases", "automatic_passes", "automatic_failures",
                    "without_checks", "execution_errors"):
            per_tier[tier][key] += s[key]
        per_tier[tier]["judged"] += judged
        per_tier[tier]["lenient_exact_recoveries"] += lenient_recoveries
        per_suite.append({"suite_id": suite["suite_id"], "tier": tier,
                          "offered": offered, "judged": judged,
                          "lenient_exact_recoveries": lenient_recoveries, **s})
        rate = f"{100 * passes / offered:.0f}٪" if offered else "—"
        print(f"[{index}/{len(suites)}] {suite['suite_id']}: {rate} "
              f"({passes}/{offered} معروضة، قِيس {judged})", flush=True)

    tiers = {}
    for tier, c in per_tier.items():
        offered = c["offered"]
        judged = c["judged"]
        tiers[tier] = {
            "offered": offered, "judged": judged,
            "automatic_passes": c["automatic_passes"],
            "automatic_failures": c["automatic_failures"],
            "without_checks": c["without_checks"],
            "execution_errors": c["execution_errors"],
            "lost_to_failed_suite": c["lost_to_failed_suite"],
            "lenient_exact_recoveries": c["lenient_exact_recoveries"],
            "pass_rate_offered": _rate(c["automatic_passes"], offered),
            "pass_rate_judged": _rate(c["automatic_passes"], judged),
            "coverage": _rate(judged, offered),
        }
    offered = sum(t["offered"] for t in tiers.values())
    judged = sum(t["judged"] for t in tiers.values())
    passes = sum(t["automatic_passes"] for t in tiers.values())
    lenient_recoveries = sum(t["lenient_exact_recoveries"] for t in tiers.values())
    lenient_passes = passes + lenient_recoveries
    return {
        "schema_version": 2, "model": model, "model_version": model_version,
        "suites": len(per_suite), "suites_failed": len(failures), "failures": failures,
        "overall": {
            "offered": offered, "judged": judged, "passes": passes,
            "pass_rate_offered": _rate(passes, offered),
            "pass_rate_judged": _rate(passes, judged),
            "coverage": _rate(judged, offered),
            "without_checks": sum(t["without_checks"] for t in tiers.values()),
            "execution_errors": sum(t["execution_errors"] for t in tiers.values()),
            "lost_to_failed_suite": sum(t["lost_to_failed_suite"] for t in tiers.values()),
        },
        "exact_readings": {
            "strict": {"passes": passes,
                       "pass_rate_offered": _rate(passes, offered),
                       "pass_rate_judged": _rate(passes, judged)},
            "lenient": {"passes": lenient_passes,
                        "pass_rate_offered": _rate(lenient_passes, offered),
                        "pass_rate_judged": _rate(lenient_passes, judged)},
            "lost_to_trailing_punctuation": lenient_recoveries,
        },
        "by_tier": tiers, "by_suite": per_suite,
        "elapsed_s": round(time.monotonic() - started, 1),
        "sandbox": None,
        "measurement_limits": [
            "**قارِن بـpass_rate_offered وحده.** فـpass_rate_judged مقامُه خاصيّةٌ "
            "للمحرّك: يصغر بقطع الجواب وبخطأ التشغيل وبسقوط الحزمة، فيرتفع بإخفاقٍ "
            "لم يُسجَّل. وانظر coverage قبل أي مقارنة: مقامان متساويان وتغطيتان "
            "مختلفتان يعنيان أن ما قِيس ليس واحدًا.",
            "الدرجةُ آليّةٌ على الحالات ذات الفحص؛ والحالاتُ بلا فحصٍ آليّ داخلةٌ في "
            "المقام المعروض وغيرُ قابلةٍ للنجاح فيه، فهي خصمٌ ثابتٌ على كل المحرّكات.",
            "الرقمُ الكلّي يخفي فروق الطبقات، ووزنُ كل طبقةٍ فيه حجمُها لا أهمّيتها. "
            "والطبقة (ج) تقيس الامتناع لا المعرفة.",
            "هويةُ النموذج بصمةُ أوزانه كما عرضها Ollama قبل القياس وبعده؛ والبصمةُ لا تثبت ما يجري خلفها في نموذجٍ سحابي.",
            "نداءٌ واحد لكل حالة، بلا تقدير تباينٍ ولا إعادة.",
        ],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("suites_dir", type=Path)
    ap.add_argument("--model", required=True)
    ap.add_argument("--model-version", help="بصمةُ النموذج المتوقَّعة؛ تُقارَن بما يعرضه Ollama")
    ap.add_argument("--max-output", type=int, default=800)
    ap.add_argument("--deadline-s", type=int, default=240)
    ap.add_argument("--out", type=Path, required=True, help="ملفّ النتيجة JSON")
    ap.add_argument("--require-same-suites", type=Path,
                    help="نتيجةُ محرّكٍ سابق: يتوقّف إن اختلفت الحزم")
    # فحوصُ python_sandbox تحتاج خُلفيّةً معزولة، ولا تُقلَع من متغيّر بيئة
    # (`core/sandbox.py`: إقلاعٌ موثوقٌ وحده). وبلا إقلاعها تُردّ كلُّ حالةٍ
    # برمجيّة بـsandbox_backend_unavailable — فتخرج البرمجةُ والامتناعُ من
    # القياس كلِّه، ويُنشر رقمٌ لا يقيس أهمَّ ما وُضع البنك له.
    ap.add_argument("--sandbox-receipt", type=Path,
                    help="إيصالُ تشغيلٍ موثوق (schema_version وimage_id ولوك وpython_version)، "
                         "خارج المستودع وبصلاحيات 600. بدونه تُعزل الحالاتُ ذاتُ python_sandbox")
    ap.add_argument("--sandbox-workspace", type=Path, default=ROOT / "var/sandbox",
                    help="جذرُ مساحة المرشّح المعزولة")
    args = ap.parse_args(argv)

    suites = sorted(args.suites_dir.glob("*.json"))
    if not suites:
        print(json.dumps({"error": "no_suites", "dir": str(args.suites_dir)}))
        return 2
    if args.require_same_suites:
        prior = json.loads(args.require_same_suites.read_text(encoding="utf-8"))
        # الأسماءُ وحدها لا تكفي: حزمتان بالاسم نفسه وعددِ حالاتٍ مختلف تُنتجان
        # مقامين مختلفين، فتُقارن درجتان على بنكين.
        want = {s["suite_id"]: s.get("offered", s.get("cases")) for s in prior["by_suite"]}
        for entry in prior.get("failures", []):
            suite_id = entry.get("suite_id", entry.get("suite"))
            if suite_id:
                want.setdefault(suite_id, entry.get("offered"))
        have = {}
        for p in suites:
            suite = load_suite(p)
            if suite["suite_id"] in have:
                print(json.dumps({"error": "suite_id_duplicate",
                                  "suite": suite["suite_id"]}, ensure_ascii=False))
                return 2
            have[suite["suite_id"]] = len(suite["cases"])
        if set(want) != set(have):
            print(json.dumps({"error": "suite_set_differs",
                              "only_prior": sorted(set(want) - set(have))[:5],
                              "only_now": sorted(set(have) - set(want))[:5]}, ensure_ascii=False))
            return 2
        sized = [k for k in want if want[k] is not None and want[k] != have[k]]
        if sized:
            print(json.dumps({"error": "suite_size_differs",
                              "suites": sized[:5]}, ensure_ascii=False))
            return 2

    sandbox = None
    if args.sandbox_receipt:
        args.sandbox_workspace.mkdir(parents=True, exist_ok=True)
        configure_sandbox_backend(args.sandbox_receipt.resolve(),
                                  args.sandbox_workspace.resolve())
        sandbox = str(args.sandbox_receipt)

    # البصمةُ تُحلّ قبل القياس وتُعاد بعده، ولا يُكتب دليلٌ بلا بصمةٍ أو ببصمةٍ تغيّرت أثناءه (#285)
    try:
        model_version = pin_model_digest(args.model, args.model_version)
    except ModelDigestError as exc:
        print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
        return 1
    run_root = ROOT / "var/capabilities"
    started = time.time() - 1
    result = measure(suites, args.model, run_root,
                     max_output=args.max_output, deadline_s=args.deadline_s,
                     model_version=model_version)
    try:
        verify_model_digest(args.model, model_version)
    except ModelDigestError as exc:
        # تشغيلاتُ الحزم التي كُتبت أثناء الانحراف تُحجر فلا يُعاد عرضُها (ملاحظة Codex على #290)
        quarantined = quarantine_runs_since(run_root, started)
        print(json.dumps({"status": "refused", "code": exc.code, "runs_quarantined": len(quarantined)},
                         ensure_ascii=False))
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    result["sandbox"] = sandbox
    if sandbox is None:
        result["measurement_limits"].insert(
            0, "خُلفيّةُ العزل لم تُقلَع: كلُّ حالةٍ فحصُها python_sandbox معزولةٌ "
               "غيرُ مقيسة — ومنها البرمجةُ والامتناع. والرقمُ لا يشملها.")
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps({"model": result["model"], **result["overall"],
                      "suites_failed": result["suites_failed"],
                      "elapsed_s": result["elapsed_s"], "out": str(args.out)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
