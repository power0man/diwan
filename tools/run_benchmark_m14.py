#!/usr/bin/env python3
"""أداة تشغيل بنك الجودة المقيس م١٤ (Three-Arm Benchmark CLI).

    .venv/bin/python tools/run_benchmark_m14.py [--limit N] [--arms model_alone,naive_rag,diwan_full]

يقارن ثلاثة أذرع (النموذج وحده، استرجاع ساذج، ديوان كاملاً) عبر أربعة أبعاد:
الصحة، الاكتمال، الإسناد (ق٢٦)، وأمانة الاصطلاح.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.benchmark_runner import BenchmarkRunner
from providers.ollama import OllamaProvider


def format_markdown_table(summary: dict) -> str:
    lines = [
        "| الذراع (Arm) | أعطال (خارج المقام) | معدل الإجابة | الصحة عند الإجابة | عقوبة الهلوسة | الاكتمال | الإسناد (ق٢٦) | أمانة المسرد | جودة الإجابة | الجودة الفعلية (الموزونة) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    name_map = {
        "model_alone": "١. النموذج وحده (Model Alone)",
        "naive_rag": "٢. استرجاع ساذج (Naive RAG)",
        "diwan_full": "٣. ديوان كاملاً (Diwan Full)",
    }
    for arm, stats in summary.items():
        title = name_map.get(arm, arm)
        trans_str = f"{stats['mean_translation'] * 100:.1f}%" if stats.get("mean_translation") is not None else "—"
        errors = f"{stats.get('error_count', 0)}/{stats['total_cases']}"
        lines.append(
            f"| **{title}** | {errors} | {stats['answered_rate'] * 100:.1f}% | {stats['mean_factuality'] * 100:.1f}% | "
            f"-{stats['mean_hallucination_penalty'] * 100:.1f}% | {stats['mean_completeness'] * 100:.1f}% | "
            f"{stats['mean_attribution'] * 100:.1f}% | {trans_str} | "
            f"**{stats['mean_overall'] * 100:.1f}%** | **{stats['effective_overall'] * 100:.1f}%** |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="تشغيل بنك الجودة المقيس م١٤ وفق ق٤٢")
    parser.add_argument("--limit", type=int, default=None, help="عدد القضايا المراد تشغيلها")
    parser.add_argument(
        "--arms",
        type=str,
        default="model_alone,naive_rag,diwan_full",
        help="الأذرع مفصولة بفواصل (يتطلب الأذرع الثلاثة وفق ق٤٢)",
    )
    parser.add_argument(
        "--diagnostic-single-arm",
        action="store_true",
        help="تشغيل تشخيصي استكشافي لذراع مفرد دون توثيق تقرير مقارنة رسمي (ق٤٢)",
    )
    parser.add_argument("--review-file", type=str, default=None, help="واجهة قديمة مرفوضة؛ راجع التقرير المحفوظ عبر review_automatically.py")
    parser.add_argument(
        "--no-require-review",
        action="store_true",
        help="تشغيل تشخيصي مؤقت غير قابل للاعتماد",
    )
    parser.add_argument("--domain", type=str, default=None, help="فلترة القضايا بنطاق محدد (maritime, lexicon, translation)")
    parser.add_argument("--offset", type=int, default=0, help="تخطي عدد من القضايا في البداية")
    parser.add_argument("--model", type=str, default="qwen3:14b", help="نموذج التشغيل")
    parser.add_argument(
        "--report-doc",
        type=str,
        default="var/benchmarks/m14/latest-diagnostic.md",
        help="مسار توثيق تقرير المقارنة",
    )
    args = parser.parse_args()

    arms = tuple(a.strip() for a in args.arms.split(","))
    provider = OllamaProvider(args.model)

    print(f"بدء تشغيل بنك الجودة المقيس م١٤ عبر النموذج: {args.model}")
    print(f"الأذرع المحددة: {arms}")
    if args.domain:
        print(f"النطاق المستهدف: {args.domain}")
    if args.offset:
        print(f"إزاحة البداية: {args.offset}")
    if args.limit:
        print(f"الحد الأقصى للقضايا: {args.limit}")

    runner = BenchmarkRunner(ROOT, provider)
    report = runner.run_benchmark(
        limit=args.limit,
        offset=args.offset,
        domain=args.domain,
        arms=arms,
        human_review_file=args.review_file,
        require_human_review=not args.no_require_review,
        diagnostic_single_arm=args.diagnostic_single_arm,
    )

    print("\n" + "=" * 70)
    print("نتائج بنك الجودة المقيس م١٤ — مصفوفة المقارنة الثلاثية (المقياس المصحح ف٠):")
    print("=" * 70)
    table_md = format_markdown_table(report["summary"])
    print(table_md)
    print("=" * 70)

    print(f"\nالحالة: {report['status']}؛ لا اعتماد تلقائي من مشغّل القياس.")
    print("المراجعة متعددة الأنظمة خطوة منفصلة على ملف النتائج نفسه دون إعادة تشغيله.")

    # تصدير تقرير المقارنة إذا كانت الأذرع مكتملة وفق ق٤٢
    if not args.diagnostic_single_arm:
        report_doc = Path(args.report_doc)
        if not report_doc.is_absolute():
            report_doc = ROOT / report_doc
        content = [
            "# تقرير بنك الجودة المقيس — م١٤ (Three-Arm Benchmark)",
            "",
            f"**معرف التشغيل:** `{report['run_id']}`",
            f"**النموذج:** `{args.model}`",
            f"**الحالة:** `{report['status']}`",
            "",
            "## مصفوفة المقارنة التشخيصية (المقياس المصحح ف٠ وق٤٢)",
            "",
            table_md,
            "",
            "## قراءة الأرقام وتحليل الأثر وفق خطة التثبيت",
            "",
            "1. **فصل الامتناع عن الجودة (ف٠):**",
            "   - الامتناع يُحسب في معدل الإجابة/الامتناع مستقلاً، ولا يُحتسب صفراً في جودة الإجابة حينما يجيب ديوان.",
            "   - الصمت الأمين عند غياب الشاهد يُكافأ ولا يُعاقب كما كان في المقياس القديم.",
            "2. **عقوبة الهلوسة والتناقض:**",
            "   - الادعاءات المناقضة للنص الذهبي (مثل تفريغ على 3 أميال بدل 200) تخصم من الصحة بدلاً من مكافأة الطلاقة الكاذبة.",
            "3. **أمانة المسرد:**",
            "   - قُصر بُعد المسرد على الحالات التي تختبر المسرد فعلياً (بدلاً من منح 100% للجميع).",
            "",
        ]
        report_doc.parent.mkdir(parents=True, exist_ok=True)
        report_doc.write_text("\n".join(content), encoding="utf-8")
        print(f"\nتم حفظ تقرير القياس غير المعتمد في: {report_doc}")
    else:
        print("\n[تنبيه ق٤٢]: تشغيل استكشافي فردي — حُجب التصدير إلى التقرير الرسمي حتى تشغيل الأذرع الثلاثة معاً.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
