#!/usr/bin/env python3
"""يختم دليلًا في docs/probe بكتلتَي `licenses` و`spend` من مصادرهما، فلا تُكتبان باليد (#301، #295).

حارسا الرخص والإنفاق (`tools/model_licenses.py` و`tools/probe_spend.py`) يردّان كلَّ دليلٍ جديد يسمّي نموذجًا بلا الكتلتين،
ولا كاتبَ في الأدوات يكتبهما بعد. فهذه الأداةُ تكتبهما على الدليل بعد كتابته وقبل إيداعه:

- `licenses`: لكل نموذجٍ يسمّيه الدليلُ على أيّ عمق (بقراءة `model_licenses.all_named_models` نفسِها) رخصتُه من السجلّ.
  والمعلّقُ أو الغائبُ عن السجلّ لا يُختم بل يُسمّى، ويبقى الدليلُ مردودًا حتى تُقرأ رخصتُه.
  ورخصةٌ مكتوبةٌ تخالف السجلّ تُسمّى ولا تُستبدل.
- `spend`: كتلةٌ قائمة تُفحص ولا تُستبدل. وإلا تُشتقّ:
  - من سجلّ النداءات (`provider_usage`، كما يكتبه `tools/external_review.py`): النداءاتُ السحابيّة المرسَلة وحدها، بلا نداء
    الفهرس ولا النداء المحليّ.
  - أو `local_no_charge` إن كانت النماذجُ كلُّها محليّة.
  - وما سواهما لا يُخمَّن: `spend_basis_required`، ويُعطى بـ`--spend`.

    python3 tools/stamp_evidence.py docs/probe/j8-analyst-20261005.json
    python3 tools/stamp_evidence.py --check docs/probe/*.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.locality import is_cloud_model  # noqa: E402
from tools import model_licenses as ml  # noqa: E402
from tools import probe_spend  # noqa: E402

FREE_BACKENDS = frozenset({"github-models", "groq", "openrouter"})
# صيغُ معرّفٍ محليّ فيها «/»: أوزانُ Hugging Face عبر Ollama المحلي. وما سواها بشرطةٍ مائلة معرّفُ مزوّدٍ بعيد.
LOCAL_SLASH_PREFIXES = ("hf.co/", "huggingface.co/", "ollama:")


def is_local_name(name: str) -> bool:
    """اسمٌ محليّ: لا لاحقةَ سحابيّة، ولا معرّفَ مزوّدٍ بعيد (`ناشر/نموذج`) إلا أوزانًا تُسحب إلى Ollama المحلي."""
    if is_cloud_model(name):
        return False
    return "/" not in name or name.startswith(LOCAL_SLASH_PREFIXES)


def stamp_licenses(payload: dict, models: dict) -> tuple[dict, list[str]]:
    stated = payload.get("licenses") if isinstance(payload.get("licenses"), dict) else {}
    licenses, problems = dict(stated), []
    for name in dict.fromkeys(ml.canonical(raw) for raw in ml.all_named_models(payload)):
        entry = models.get(name)
        if entry is None:
            problems.append(f"license_unknown:{name}")
        elif "pending" in entry:
            problems.append(f"license_pending:{name}")
        elif name in stated and str(stated[name]).lower() != entry["license"].lower():
            problems.append(f"license_disagrees:{name}")
        else:
            licenses.setdefault(name, entry["license"])
    return dict(sorted(licenses.items())), problems


def _cost(row: dict) -> Decimal | None:
    value = row.get("cost_usd")
    return Decimal(str(value)) if isinstance(value, (str, int, float)) and not isinstance(value, bool) else None


def spend_from_usage(rows: list) -> tuple[dict | None, list[str]]:
    """كتلةُ الإنفاق من سجلّ النداءات: ما أُرسل إلى السحابة وحده. وأساسُ الكلفة من الواجهة لا من التخمين."""
    sent = [row for row in rows if isinstance(row, dict) and row.get("kind") != "catalog" and row.get("request_sent")]
    cloud = [row for row in sent if row.get("provider") != "ollama" or row.get("cloud")]
    # عدّادٌ غائبٌ في نداءٍ أُرسل مجهولٌ لا صفر: لا تُكتب كتلةٌ تعدّه صفرًا (ملاحظة Codex على #310)
    if any(not isinstance(row.get("usage"), dict)
           or any(type(row["usage"].get(key)) is not int for key in ("prompt_tokens", "completion_tokens"))
           for row in cloud):
        return None, ["spend_usage_incomplete"]
    totals = {key: sum(row["usage"][key] for row in cloud) for key in ("prompt_tokens", "completion_tokens")}
    providers = {row.get("provider") for row in cloud}
    costs = [_cost(row) for row in cloud]
    if not cloud:
        basis, cost = "local_no_charge", 0
    elif len(providers) != 1:
        return None, ["spend_mixed_providers"]
    elif providers == {"ollama"}:
        basis, cost = "subscription_flat", 0
    elif providers <= FREE_BACKENDS and all(row.get("zero_spend_proof") for row in cloud) \
            and all(value in (None, 0) for value in costs):
        # المجانيةُ بدليلها لكل نداء (`zero_spend_proof` في سجلّ النداءات)، لا بغياب الكلفة (ملاحظة Codex على #310)
        basis, cost = "free_tier", 0
    elif any(value is None for value in costs):
        basis, cost = "unpriced", None
    else:
        reported = all(row.get("cost_status") == "reported" for row in cloud)
        basis, cost = ("reported_by_provider" if reported else "estimated_from_prices"), float(sum(costs))
    return {"cloud_calls": len(cloud), **totals, "cost_usd": cost, "cost_basis": basis}, []


def stamp_spend(payload: dict, given: dict | None) -> tuple[dict | None, list[str]]:
    if isinstance(payload.get("spend"), dict) or given is not None:
        spend = payload["spend"] if isinstance(payload.get("spend"), dict) else given
        return spend, [problem.replace(":given", "") for problem in probe_spend.spend_findings("given", spend)]
    if isinstance(payload.get("provider_usage"), list):
        return spend_from_usage(payload["provider_usage"])
    names = ml.all_named_models(payload)
    if names and all(is_local_name(name) for name in names):
        return {"cloud_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0,
                "cost_basis": "local_no_charge"}, []
    return None, ["spend_basis_required"]


def stamp(payload: dict, models: dict, given_spend: dict | None = None) -> tuple[dict, list[str]]:
    """الدليلُ مختومًا بما أمكن، وكلُّ ما لم يُختم باسمه. ودليلٌ لا يسمّي نموذجًا لا يُختم ولا يُرَدّ."""
    if not ml.all_named_models(payload):
        return payload, []
    out = dict(payload)
    licenses, problems = stamp_licenses(payload, models)
    if licenses:
        out["licenses"] = licenses
    spend, spend_problems = stamp_spend(payload, given_spend)
    if spend is not None and not spend_problems:
        out["spend"] = spend
    return out, sorted(problems + spend_problems)


def _write_atomic(path: Path, payload: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--registry", type=Path, default=ml.REGISTRY)
    parser.add_argument("--spend", type=json.loads, default=None,
                        help="كتلةُ الإنفاق نصَّ JSON، لدليلٍ لا تُشتقّ كتلتُه (نماذج بعيدة بلا سجلّ نداءات)")
    parser.add_argument("--check", action="store_true", help="يفحص ولا يكتب")
    args = parser.parse_args(argv)
    models = json.loads(args.registry.read_text(encoding="utf-8")).get("models", {})
    report, failed = {}, False
    for path in args.files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        stamped, problems = stamp(payload, models, args.spend) if isinstance(payload, dict) else (payload, [])
        if args.check and stamped != payload:
            problems = sorted(problems + ["stamp_required"])     # ملفٌّ ينقصه ختمٌ لا يمرّ الفحص (ملاحظة Codex على #310)
        elif stamped != payload:
            _write_atomic(path, stamped)
        report[str(path)] = problems
        failed = failed or bool(problems)
    print(json.dumps({"schema_version": 1, "status": "failed" if failed else "passed", "findings": report},
                     ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
