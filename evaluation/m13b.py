"""م١٣-ب (ك٤٩، سياسات): العقدةُ مقابل المركز بالاسترجاع نفسِه على المتن نفسِه، والقرارُ من بروتوكولٍ مسجَّل.

- **السؤال** (`docs/PROJECT-PLAN-20260925.md` §١، م٢): هل تبقى لعقدة السياسات شيفرتُها الخاصة، إذا أُعطي
  المركزُ الاسترجاعَ نفسَه على المتن نفسِه؟
- **الذراعان على الحالات نفسِها، والمحرّكُ نفسُه:**
  - **العقدة:** `DiwanFullArm` (`nodes/maritime/node.py`).
  - **المركز:** `CenterRetrievalArm` (`evaluation/benchmark_arms.py`)، أي الحلقةُ الوكيلة بأداة استرجاعٍ
    مرقّمة على `hybrid_search` البحريّ.
- **الحكمُ لكل حالة** بمقاييس م١٤ نفسِها (`evaluation/benchmark_metrics.py::evaluate_case_response`):
  - **يمرّ** ما لم يَعطب ولم يمتنع، وبلغت جودتُه العتبةَ المسجَّلة.
  - **ويُسنَد** ما سُنِد كلُّ ادعاءٍ فيه.
- **القرار:**
  - **تبقى** الشيفرةُ إن زادت المركزَ صحّةً أو إسنادًا (الحدُّ الأدنى لمجال الفرق فوق الصفر).
  - **وتُحذف** إن كان الحدُّ الأعلى للفرقين دون الأثر المسجَّل.
  - وما بينهما الافتراضيُّ المسجَّل، وما دون أصغر عيّنةٍ «ناقصُ القوة».
"""
from __future__ import annotations

import json
from pathlib import Path

from evaluation.ablation import _sha, compare, min_items
from evaluation.benchmark_metrics import evaluate_case_response

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "evaluation" / "protocols" / "m13b_v1.json"
ARMS = ("node", "center")


def protocol() -> dict:
    return json.loads(PROTOCOL.read_text(encoding="utf-8"))


def score_row(case: dict, arm_out: dict, *, pass_floor: float) -> dict:
    """صفُّ حالةٍ في ذراع: عطبٌ خارج الأزواج، أو قياسٌ بحكمَي المرور والإسناد."""
    metrics = evaluate_case_response(arm_out["answer"], case, arm_out["pages_by_ref"],
                                     abstained=arm_out.get("abstained", False),
                                     error_code=arm_out.get("error_code"))
    base = {"id": case["case_id"], "category": case.get("domain", "maritime"), "answer": arm_out["answer"]}
    if metrics.get("errored"):
        return {**base, "status": "error", "code": metrics.get("error_code")}
    answered = not metrics["abstained"]
    return {**base, "status": "measured", "abstained": metrics["abstained"],
            "overall": metrics["overall_score"],
            "passed": answered and metrics["overall_score"] >= pass_floor,
            "attributed": answered and metrics["attribution"]["score"] >= 1.0}


def run(cases: list[dict], arms: dict, *, pass_floor: float) -> dict[str, list[dict]]:
    if set(arms) != set(ARMS):
        raise ValueError("ذراعان: node وcenter")
    return {name: [score_row(case, arms[name].run(case["question"], case), pass_floor=pass_floor)
                   for case in cases] for name in ARMS}


def judge(node: list[dict], center: list[dict]) -> dict:
    data = protocol()
    rule = data["rule"]
    answers = compare(node, center)
    attribution = compare([{**r, "passed": r["attributed"]} if r["status"] == "measured" else r for r in node],
                          [{**r, "passed": r["attributed"]} if r["status"] == "measured" else r for r in center])
    need = min_items(rule["power_effect"], data["assumed_discordance"])
    base = {"answers": answers, "attribution": attribution, "min_items": need,
            "meaning": data["decisions"], "protocol_sha256": _sha(PROTOCOL.read_bytes())}
    if answers["pairs"] < need:
        return {**base, "decision": "underpowered", "reason": f"pairs {answers['pairs']} < min_items {need}"}
    if answers["ci95"][0] > 0 or attribution["ci95"][0] > 0:
        return {**base, "decision": "keep", "reason": "node_adds_correctness_or_attribution"}
    if answers["ci95"][1] < rule["min_effect"] and attribution["ci95"][1] < rule["min_effect"]:
        return {**base, "decision": "remove", "reason": "center_with_retrieval_is_not_worse_by_min_effect"}
    return {**base, "decision": rule["on_inconclusive"], "reason": "inconclusive_registered_default"}
