#!/usr/bin/env python3
"""كلُّ دليلٍ جديد في docs/probe يسمّي نموذجًا يعلن إنفاقَه في كتلة `spend` واحدة (جديد-spend-ledger، البند ٤ من #295).

ق٦٧-٣: «يُسجَّل كلُّ إنفاقٍ في docs/probe/». وحارسُ الخصوصية (#188) يعدّ حقولَ الفوترة المتفرّقة بياناتٍ تشغيليّةً
خاصّة: `input_tokens` و`output_tokens` و`estimate_usd` و`billed_usd` و`credits`. فالإنفاقُ المنشور كتلةٌ واحدة بشكلٍ
ثابت، وأسماؤها غيرُ تلك:
`{"cloud_calls", "prompt_tokens", "completion_tokens", "cost_usd", "cost_basis"}`.
ويبقى ما سواها من حقول الحساب والفوترة خاصًّا كما كان.

الجِدّةُ هي جِدّةُ حارس الرخص نفسُها (`registry/model_licenses.json`): ملفٌّ ليس في القائمة التاريخيّة، أو مؤرَّخٌ من
تاريخ الإنفاذ. والنماذجُ تُقرأ على أيّ عمق. والدليلُ المحليّ يعلن إنفاقَه كذلك:
`cloud_calls: 0` و`cost_basis: local_no_charge`.
فلا يُحتاج إلى تخمين محلّيّة النموذج من اسمه، ويُغلق عند الشكّ.

أسسُ الكلفة مسمّاة:
- `local_no_charge`: لا نداءَ سحابيّ.
- `subscription_flat`: اشتراكٌ ثابت كـOllama السحابي، والكلفةُ الحدّيّة صفر.
- `free_tier`: واجهةٌ مجانيّة بدليل مجانيّتها.
- `reported_by_provider`: كلفةٌ أبلغها المزوّد.
- `estimated_from_prices`: تقديرٌ من جدول أسعارٍ مؤرَّخ.
- `unpriced`: لم تُسعَّر، والكلفةُ `null` معلنةً لا صفرًا.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import model_licenses as ml  # noqa: E402

SPEND_KEYS = frozenset({"cloud_calls", "prompt_tokens", "completion_tokens", "cost_usd", "cost_basis"})
COUNTS = ("cloud_calls", "prompt_tokens", "completion_tokens")
BASES = frozenset({"local_no_charge", "subscription_flat", "free_tier", "reported_by_provider",
                   "estimated_from_prices", "unpriced"})


def _count(value: object) -> bool:
    return type(value) is int and value >= 0


def spend_findings(file: str, spend: object) -> list[str]:
    """كتلةُ الإنفاق بشكلها الثابت، وأساسُ الكلفة يتّسق مع أعدادها."""
    if not isinstance(spend, dict):
        return [f"spend_missing:{file}"]
    if set(spend) != SPEND_KEYS:
        return [f"spend_keys:{file}"]
    problems = [f"spend_count:{file}:{key}" for key in COUNTS if not _count(spend[key])]
    basis, cost = spend["cost_basis"], spend["cost_usd"]
    if basis not in BASES:
        problems.append(f"spend_basis_unknown:{file}")
    elif basis == "unpriced":
        if cost is not None:
            problems.append(f"spend_unpriced_with_cost:{file}")
    # Infinity وNaN ليسا كلفة، ولا يُكتبان في JSON صارم (ملاحظة Codex على #310)
    elif type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
        problems.append(f"spend_cost:{file}")
    if not problems:
        if basis == "local_no_charge" and (spend["cloud_calls"] or cost):
            problems.append(f"spend_local_with_cloud:{file}")
        elif basis != "local_no_charge" and spend["cloud_calls"] == 0:
            problems.append(f"spend_cloud_basis_without_calls:{file}")
        elif basis in {"subscription_flat", "free_tier"} and cost:
            problems.append(f"spend_free_basis_with_cost:{file}")
    return problems


def findings(registry: dict, evidence: dict[str, object]) -> list[str]:
    historical, enforced_from = registry.get("historical_evidence"), registry.get("enforced_from")
    if not isinstance(historical, list) or not ml._valid_day(enforced_from):
        return ["registry_malformed"]
    problems: list[str] = []
    for file, payload in sorted(evidence.items()):
        if not isinstance(payload, dict):
            continue
        day = payload.get("date")
        new = file not in set(historical) or (isinstance(day, str) and day[:10] >= enforced_from)
        if new and ml.all_named_models(payload):
            problems += spend_findings(file, payload.get("spend"))
    return sorted(problems)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=ml.REGISTRY)
    parser.add_argument("--probe", type=Path, default=ml.PROBE)
    args = parser.parse_args(argv)
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    problems = findings(registry, ml.load_evidence(args.probe))
    print(json.dumps({"schema_version": 1, "status": "failed" if problems else "passed", "findings": problems},
                     ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
