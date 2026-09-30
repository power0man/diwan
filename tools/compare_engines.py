#!/usr/bin/env python3
"""أعِد توليد ملخّصَي ك٢ وك١٢ من ملفات العينات العامة المودعة فقط.

لا تنادي هذه الأداة نموذجًا ولا تقرأ بنكًا محجوبًا. وتتحقق أولًا من أن أسماء
الحزم وأحجامها متطابقة، ثم تعيد الأرقام المحافظة (`offered`) وقراءتي `exact`
الصارمة والمشذبة. أعداد الاسترداد التاريخية في `comparison_metadata` منقولة
من تدقيق ك١٦ المنشور؛ أما القياسات الجديدة فيكتبها `measure_engine` في
`exact_readings` مباشرةً.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


TOOL = "tools/compare_engines.py"
COMMAND = "python tools/compare_engines.py --probe-dir docs/probe --write"
K2_OUT = "k2-engine-comparison-20260925.json"
K12_OUT = "k12-arabic-specialist-20260925.json"
K2_SAMPLES = (
    "k2-qwen3-14b-sample.json",
    "k2-qwen35-9b-sample.json",
    "k2-gemma4-sample.json",
)
K12_SAMPLE = "k12-command-r7b-arabic-sample.json"
ARABIC_CAPABILITIES = (
    "grammar_parsing", "lexical_semantics", "morphology", "rhetoric_balagha",
)


class ComparisonError(ValueError):
    pass


def _rate(numerator: int, denominator: int, digits: int = 4) -> float | None:
    return round(numerator / denominator, digits) if denominator else None


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"sample_unreadable:{path.name}") from exc
    if not isinstance(value, dict):
        raise ComparisonError(f"sample_object_required:{path.name}")
    return value


def _shape(sample: dict) -> dict[str, int]:
    shape: dict[str, int] = {}
    for entry in [*sample.get("by_suite", []), *sample.get("failures", [])]:
        suite_id = entry.get("suite_id", entry.get("suite"))
        offered = entry.get("offered", entry.get("cases"))
        if not isinstance(suite_id, str) or type(offered) is not int or offered < 0:
            raise ComparisonError("suite_shape_invalid")
        if suite_id in shape:
            raise ComparisonError(f"suite_id_duplicate:{suite_id}")
        shape[suite_id] = offered
    return shape


def _validate_sample(sample: dict) -> None:
    overall = sample.get("overall", {})
    for field in ("offered", "judged", "passes", "without_checks",
                  "execution_errors", "lost_to_failed_suite"):
        if type(overall.get(field)) is not int or overall[field] < 0:
            raise ComparisonError(f"overall_count_invalid:{field}")
    offered, judged, passes = (overall[k] for k in ("offered", "judged", "passes"))
    if passes > judged or judged > offered:
        raise ComparisonError("overall_denominator_invalid")
    expected = {
        "pass_rate_offered": _rate(passes, offered),
        "pass_rate_judged": _rate(passes, judged),
        "coverage": _rate(judged, offered),
    }
    if any(overall.get(name) != value for name, value in expected.items()):
        raise ComparisonError("overall_rate_mismatch")
    tiers = sample.get("by_tier")
    if not isinstance(tiers, dict) or not tiers:
        raise ComparisonError("tiers_missing")
    if sum(t.get("offered", -1) for t in tiers.values()) != offered:
        raise ComparisonError("tier_offered_mismatch")
    if sum(t.get("judged", -1) for t in tiers.values()) != judged:
        raise ComparisonError("tier_judged_mismatch")
    if sum(t.get("automatic_passes", -1) for t in tiers.values()) != passes:
        raise ComparisonError("tier_passes_mismatch")
    if sum(_shape(sample).values()) != offered:
        raise ComparisonError("suite_offered_mismatch")


def _exact_reading(sample: dict) -> dict:
    overall = sample["overall"]
    strict_passes = overall["passes"]
    if "exact_readings" in sample:
        exact = sample["exact_readings"]
        recoveries = exact.get("lost_to_trailing_punctuation")
        lenient_passes = exact.get("lenient", {}).get("passes")
        if (exact.get("strict", {}).get("passes") != strict_passes
                or type(recoveries) is not int or type(lenient_passes) is not int
                or lenient_passes != strict_passes + recoveries):
            raise ComparisonError("exact_readings_invalid")
        source = "tools/measure_engine.py::exact_readings"
    else:
        metadata = sample.get("comparison_metadata", {})
        recoveries = metadata.get("exact_trailing_punctuation_recoveries")
        if type(recoveries) is not int or recoveries < 0:
            raise ComparisonError("exact_audit_missing")
        lenient_passes = strict_passes + recoveries
        source = metadata.get("source")
        if not isinstance(source, str) or not source:
            raise ComparisonError("exact_audit_source_missing")
    if lenient_passes > overall["judged"]:
        raise ComparisonError("lenient_passes_exceed_judged")
    return {
        "strict": _rate(strict_passes, overall["judged"], 3),
        "lenient": _rate(lenient_passes, overall["judged"], 3),
        "lost_to_punctuation": recoveries,
        "source": source,
    }


def _load_samples(probe_dir: Path) -> list[dict]:
    paths = [*(probe_dir / name for name in K2_SAMPLES), probe_dir / K12_SAMPLE]
    samples = [_read(path) for path in paths]
    for sample in samples:
        _validate_sample(sample)
    models = [sample.get("model") for sample in samples]
    if any(not isinstance(model, str) or not model for model in models):
        raise ComparisonError("model_missing")
    if len(models) != len(set(models)):
        raise ComparisonError("model_duplicate")
    reference = _shape(samples[0])
    for sample in samples[1:]:
        shape = _shape(sample)
        if set(shape) != set(reference):
            raise ComparisonError("suite_set_differs")
        if shape != reference:
            raise ComparisonError("suite_size_differs")
    return samples


def _tier_rates(sample: dict) -> dict[str, float | None]:
    return {tier: values["pass_rate_offered"]
            for tier, values in sample["by_tier"].items()}


def _suite_rate(sample: dict, suffix: str) -> float:
    matches = [entry for entry in sample["by_suite"]
               if entry["suite_id"].endswith("__" + suffix)]
    if len(matches) != 1:
        raise ComparisonError(f"suite_missing_or_duplicate:{suffix}")
    entry = matches[0]
    return _rate(entry["automatic_passes"], entry["offered"], 3)


def _k2(samples: list[dict]) -> dict:
    engines = {}
    for sample in samples[:3]:
        overall = sample["overall"]
        metadata = sample.get("comparison_metadata", {})
        if not isinstance(metadata.get("model_size"), str):
            raise ComparisonError(f"model_size_missing:{sample['model']}")
        engines[sample["model"]] = {
            "size": metadata["model_size"],
            "pass_rate_offered": overall["pass_rate_offered"],
            "pass_rate_judged": overall["pass_rate_judged"],
            "coverage": overall["coverage"],
            "execution_errors": overall["execution_errors"],
            "elapsed_s": sample["elapsed_s"],
            "by_tier": _tier_rates(sample),
            "exact_readings": _exact_reading(sample),
        }
    ranking = sorted(([v["pass_rate_offered"], k] for k, v in engines.items()),
                     reverse=True)
    winner = ranking[0][1]
    offered = samples[0]["overall"]["offered"]
    return {
        "schema_version": 2,
        "probe": "k2_engine_comparison",
        "task": "ك٢",
        "date": "2026-09-25",
        "agent": "anthropic/claude-opus-5",
        "tool": TOOL,
        "command": COMMAND,
        "source_samples": [*K2_SAMPLES],
        "bank": "kimi_v1 v1.1 — عيّنةٌ طبقيّة من الشطر المفتوح",
        "sample": {"offered": offered, "of_bank": 1902, "coverage_pct": 19.8,
                   "critical_dropped": 0, "salt": "k2", "same_suites_enforced": True},
        "engines": engines,
        "ranking_offered": ranking,
        "headline": (f"{winner} يتصدّر بـ{100 * engines[winner]['pass_rate_offered']:.1f}٪ "
                     f"على {offered} حالة معروضة، ويتقدّم في طبقتين من ثلاث، "
                     f"وهو أصغرُ المرشّحين ({engines[winner]['size'].replace('GB', 'غيغا')})."),
        "measurement_limits": [
            "عيّنةٌ ١٩٫٨٪ من الشطر المفتوح، لا البنك كلَّه، ولا المحجوبَ منه إطلاقًا.",
            "الطبقةُ (أ) وزنُها ٢٦٢ من ٣٧٦ (٧٠٪)، فالرقمُ الكلّي هو رقمُها تقريبًا. ووزنُ كل طبقةٍ حجمُها لا أهمّيتها.",
            "الطبقة (ب) تغطيتُها نحو النصف (٢٤ حالةً محكومة من ٤٩)، ففرقُ نقطتين فيها حالةٌ واحدة ولا يُبنى عليه شيء.",
            "نداءٌ واحد لكل حالة بلا إعادةٍ ولا تقدير تباين، فلا فاصلَ ثقةٍ منشور.",
            "الفروقُ دون ٣ نقاطٍ لا تُقرأ ترتيبًا بهذا الحجم.",
            "**البنك نفسُه فيه سبعُ عثراتٍ مؤكَّدة** فُرزت في ك١١ وأُرسلت لإصلاحها (ك١٥)، وإحداها لم تُصلَح بعد. فالقياسُ جرى على نسخةٍ فيها هذه العثرات، وأثرُها واحدٌ على المحرّكات الثلاثة.",
            "هويةُ النماذج أسماءُ خدمةٍ في Ollama لا بصماتِ أوزان.",
            "وأخطاءُ التنفيذ تفاوتت (٤ و٩ و٢٣)، فـcoverage تفاوتت — ولهذا يُقارن بـpass_rate_offered وحده.",
            "قراءةُ exact الصارمة هي الحكم بق٥٧؛ والمشذبة تشخيصٌ لفارق الترقيم، وأعدادها من تدقيق ك١٦ المنشور لا من إعادة تشغيل نموذج.",
        ],
        "owner_decision_required": "اعتمادُ المحرّك §٦. وتبعةٌ لا تظهر في الرقم: اعتمادُ Gemma يُخرج عائلةَ Google من التحكيم بموجب §٤ القاعدة ٢، ومنها Gemini أحدُ المطوّرين الثلاثة. ولا يقع هذا على qwen3.5 لأنه عائلةُ المحرّك الحالي.",
    }


def _k12(samples: list[dict]) -> dict:
    display = lambda sample: "gemma4" if sample["model"] == "gemma4:latest" else sample["model"]
    results = {display(sample): round(sample["overall"]["pass_rate_offered"], 3)
               for sample in samples}
    capabilities = {
        capability: {display(sample): _suite_rate(sample, capability)
                     for sample in samples}
        for capability in ARABIC_CAPABILITIES
    }
    exact = {sample["model"]: _exact_reading(sample) for sample in samples}
    return {
        "schema_version": 2,
        "probe": "k12_arabic_specialist",
        "task": "ك١٢",
        "date": "2026-09-25",
        "agent": "anthropic/claude-opus-5",
        "tool": TOOL,
        "command": COMMAND,
        "source_samples": [*K2_SAMPLES, K12_SAMPLE],
        "question": "هل يغلب التخصّصُ في العربية الحجمَ والعمومَ في بنكٍ محورُه العربية؟",
        "substitute_for_jais": {
            "why": "Jais و jais2 يردّان 404 في مكتبة Ollama — غائبان لا مؤجَّلان.",
            "chosen": "command-r7b-arabic:7b (Cohere، متخصّصٌ في العربية، 5.1 غيغا)",
            "rejected": "aya و aya-expanse: متعدّدةُ اللغات لا متخصّصةٌ في العربية، فلا تختبر الفرضية.",
        },
        "answer": "لا. النموذجُ العربيُّ المتخصّص أضعفُ الأربعة بفارقٍ كبير — **وأضعفُ ما يكون في القدرات العربية نفسِها**.",
        "results_offered": results,
        "arabic_capabilities": capabilities,
        "exact_readings": exact,
        "eighth_measurement_defect": {
            "what": "فحصُ `exact` يُسقط جوابًا صحيحًا لنقطةٍ في آخره: «بليغ.» مقابل «بليغ».",
            "why_it_matters": "لا يضرب المحرّكات بالتساوي، فيصير جزءٌ من المقارنة مقارنةَ عادةٍ في الترقيم.",
            "measured": {model: {key: value for key, value in reading.items() if key != "source"}
                         for model, reading in exact.items()},
            "verdict": "**الترتيبُ يصمد بالقراءتين**، فلا يُصلَح الفحصُ لأجل الترتيب. ولا يُخفَّف أيضًا: معايير البنك تشترط «يلتزم الصيغة المطلوبة»، فالتزامُ الصيغة جزءٌ مما يُقاس. ويبقى الرقمان منشورَين معًا.",
            "notable": "qwen3.5:9b يخسر **صفرًا** بالنقطة — وهي قدرةٌ بذاتها: التزامُ الصيغة.",
        },
        "measurement_limits": [
            "نموذجٌ عربيٌّ واحد، لا الصنفُ كلُّه. وسقوطُه لا يُثبت أن التخصّص لا ينفع، بل أن **هذا** المتخصّص لا يغلب هؤلاء العامّين على **هذا** البنك.",
            "٧ مليارات مقابل ٩ و١٤ — فالفارقُ فارقُ حجمٍ وتخصّصٍ معًا، ولا يفصل بينهما هذا القياس.",
            "عيّنةٌ ١٩٫٨٪، ونداءٌ واحد لكل حالة بلا تقدير تباين.",
            "القدراتُ العربيةُ الأربع حزمٌ صغيرة (٤ إلى ١٦ حالة)، ففروقُها أشدُّ تأثّرًا بالمصادفة من الرقم الكلّي.",
            "أعداد القراءة المشذبة من تدقيق ك١٦ المنشور في العينات؛ لم يُعد تشغيل نموذج.",
        ],
    }


def build_summaries(probe_dir: Path) -> tuple[dict, dict]:
    samples = _load_samples(probe_dir)
    return _k2(samples), _k12(samples)


def _payload(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-dir", type=Path, default=Path("docs/probe"))
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true")
    action.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        k2, k12 = build_summaries(args.probe_dir)
    except ComparisonError as exc:
        print(json.dumps({"status": "refused", "code": str(exc)}, ensure_ascii=False))
        return 2
    outputs = {args.probe_dir / K2_OUT: k2, args.probe_dir / K12_OUT: k12}
    if args.write:
        for path, value in outputs.items():
            path.write_text(_payload(value), encoding="utf-8")
        print(json.dumps({"status": "written", "files": [str(p) for p in outputs]},
                         ensure_ascii=False))
        return 0
    stale = [str(path) for path, value in outputs.items()
             if not path.exists() or path.read_text(encoding="utf-8") != _payload(value)]
    print(json.dumps({"status": "verified" if not stale else "comparison_stale",
                      "files": stale}, ensure_ascii=False))
    return 0 if not stale else 1


if __name__ == "__main__":
    raise SystemExit(main())
