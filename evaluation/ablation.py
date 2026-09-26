"""الاستئصال (ك٤٦): ذراعان على الحالات نفسِها، لا يفرق بينهما إلا مكوّنٌ واحد، والقرارُ مسجَّلٌ قبل التشغيل.

- **الطريقُ طريقُ المنتج الافتراضي (ج١):**
  - الحلقةُ الوكيلة بمساحةٍ فارغة وأدواتِ المنتج.
  - ورسالةُ المستخدم كما تبنيها الواجهة: في غلاف المدخل (`encode_input`)، محجورةً (`model_facing_input`).
  - فذراعُ الأساس هي ما يلقاه المستخدمُ اليوم، والذراعُ الأخرى تنزع مكوّنًا واحدًا (`BASELINE`).
- **الحكمُ بفحوص البنك الآلية نفسِها** (`evaluation/capabilities.py::_checks`)، فلا محكِّم. والحالةُ بلا فحصٍ لا تدخل.
- **المقارنةُ مزدوجة:** كلُّ حالةٍ تُقارن بنفسِها في الذراع الأخرى.
  - ومجالُ الثقة ٩٥٪ للفرق بين نسبتين مزدوجتين بطريقة Agresti–Min، حتميٌّ بلا بذرة.
  - والعطبُ في أيّ ذراعٍ يُخرج الحالةَ من الأزواج، ويُعدّ.
- **القرارُ من البروتوكول** (`evaluation/protocols/ablation_v1.json`):
  - «إن ≥ س فـ أ وإلا ب»، وأصغرُ عيّنةٍ تقلب الجواب.
  - وما دونها «ناقصُ القوة» لا قرار (`docs/PROJECT-PLAN-20260925.md` §٦).
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile

from agent.actions import ActionStore
from agent.builtin_tools import DEFAULT_TOOLS
from agent.journal import Journal
from agent.loop import SYSTEM as AGENT_SYSTEM, run_agent
from agent.registry import ToolContext, ToolRegistry
from core.budget import Budget
from core.canonical import PayloadRejected
from core.contracts import Message
from core.ledger import Ledger
from evaluation.capabilities import _checks
from services.agent_workspace import encode_input, model_facing_input

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "evaluation" / "protocols" / "ablation_v1.json"
RUNNER_VERSION = 1
MAX_ANSWER_CHARS = 6000
Z95 = 1.959963984540054
# ذراعُ الأساس: ما في المنتج اليوم (الأدواتُ معلنة، والتفكيرُ غير مطلوب، ورسالةُ المستخدم محجورةُ المقتبَس)
BASELINE = {"tools": "default", "thinking": False, "quarantine": True}
_ARM_VALUES = {"tools": ("default", "none"), "thinking": (False, True), "quarantine": (True, False)}


class AblationError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def arm(changes: dict | None = None) -> dict:
    """ذراعٌ = الأساسُ بعد تغييرٍ مسمًّى؛ ولا مفتاحَ ولا قيمةَ خارج المعلَن."""
    changes = dict(changes or {})
    for key, value in changes.items():
        if key not in _ARM_VALUES or value not in _ARM_VALUES[key] or type(value) is not type(_ARM_VALUES[key][0]):
            raise AblationError("arm_invalid", f"{key}={value!r}")
    return {**BASELINE, **changes}


def _user(text: str, quarantine: bool) -> str:
    encoded = encode_input(text, [], None)
    return model_facing_input(encoded) if quarantine else encoded


def case_messages(case: dict, quarantine: bool) -> tuple[tuple[Message, ...], str]:
    """الحوارُ كما يبنيه الطريقُ الوكيل: الجولاتُ السابقة تاريخٌ، والأخيرةُ مهمّةُ الجولة."""
    messages = case["messages"]
    if not messages or messages[-1]["role"] != "user":
        raise AblationError("case_invalid", case.get("case_id", "?"))
    history = tuple(Message(m["role"], _user(m["content"], quarantine) if m["role"] == "user" else m["content"])
                    for m in messages[:-1])
    initial = (Message("system", AGENT_SYSTEM), *history) if history else ()
    return initial, _user(messages[-1]["content"], quarantine)


def auto_checked(case: dict, *, sandbox: bool = True) -> bool:
    """الحالةُ تدخل إن كان لها فحصٌ آليّ؛ وبلا حاويةِ فحصٍ تخرج حالاتُ python_sandbox."""
    return bool(case["checks"]) and (sandbox or all(c["kind"] != "python_sandbox" for c in case["checks"]))


def run_case(case: dict, provider, arm_config: dict, *, model: str, model_version: str, max_steps: int = 4,
             max_output: int = 800, deadline_s: float = 240.0) -> dict:
    base = {"id": case["case_id"], "category": case["capability"]}
    scratch = Path(tempfile.mkdtemp(prefix="diwan-ablation-")).resolve()
    try:
        workspace = scratch / "workspace"
        workspace.mkdir()
        context = ToolContext(workspace, Journal(workspace), frozenset({"auto"}))
        registry = ToolRegistry(*DEFAULT_TOOLS) if arm_config["tools"] == "default" else ToolRegistry()
        initial, task = case_messages(case, arm_config["quarantine"])
        try:
            run = run_agent(task, provider, registry, context, ledger=Ledger(scratch / "ledger.jsonl"),
                            budget=Budget(0, 0), model=model, model_version=model_version, max_steps=max_steps,
                            max_output=max_output, deadline_s=deadline_s,
                            action_store=ActionStore(scratch / "control", workspace), session_id="ablation",
                            turn_id=case["case_id"], initial_messages=initial, thinking=arm_config["thinking"])
        except Exception as exc:                       # عطبُ بنيةٍ لا فشلُ قدرة
            return {**base, "status": "error", "code": "loop_raised", "detail": f"{type(exc).__name__}"}
        if run.status in ("refused", "failed"):
            return {**base, "status": "error", "code": run.code or run.status}
        answer = run.answer[:MAX_ANSWER_CHARS]
        try:
            checks = _checks(answer, case["checks"])
        except PayloadRejected as exc:                 # حاويةُ الفحص غائبة: لم يُقَس، لا رسوب
            return {**base, "status": "error", "code": exc.code}
        return {**base, "status": "measured", "loop_status": run.status, "steps": len(run.steps),
                "tool_calls": sum(len(step.tool_calls) for step in run.steps), "answer": answer,
                "passed": all(c["passed"] for c in checks)}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def run_arm(cases: list[dict], provider, arm_config: dict, **options) -> list[dict]:
    return [run_case(case, provider, arm_config, **options) for case in cases]


def compare(on: list[dict], off: list[dict], *, categories: set[str] | None = None) -> dict:
    """الفرقُ «مع المكوّن − بدونه» على الأزواج المقيسة في الذراعين، ومجالُه ٩٥٪ (Agresti–Min)."""
    off_by_id = {row["id"]: row for row in off}
    if set(off_by_id) != {row["id"] for row in on}:
        raise AblationError("arms_differ", "الذراعان على حالاتٍ مختلفة")
    pairs = errors = both = on_only = off_only = 0
    for row in on:
        if categories is not None and row["category"] not in categories:
            continue
        other = off_by_id[row["id"]]
        if row["status"] != "measured" or other["status"] != "measured":
            errors += 1
            continue
        pairs += 1
        both += row["passed"] and other["passed"]
        on_only += row["passed"] and not other["passed"]
        off_only += other["passed"] and not row["passed"]
    if not pairs:
        return {"pairs": 0, "errors": errors, "on_rate": None, "off_rate": None, "effect": None, "ci95": None,
                "on_only": 0, "off_only": 0}
    n = pairs + 2                                      # Agresti–Min: نصفٌ يُضاف إلى كل خليّةٍ من الأربع
    p10, p01 = (on_only + 0.5) / n, (off_only + 0.5) / n
    centre = p10 - p01
    half = Z95 * math.sqrt(max(p10 + p01 - centre ** 2, 0.0) / n)
    return {"pairs": pairs, "errors": errors, "on_rate": round((both + on_only) / pairs, 4),
            "off_rate": round((both + off_only) / pairs, 4), "effect": round((on_only - off_only) / pairs, 4),
            "ci95": [round(max(centre - half, -1.0), 4), round(min(centre + half, 1.0), 4)],
            "on_only": on_only, "off_only": off_only}


def min_items(power_effect: float, discordance: float) -> int:
    """أصغرُ عددٍ من الأزواج يضيق فيه نصفُ المجال إلى الأثر المطلوب، بتنافرٍ مفترَض: ⌈z²·r/δ²⌉."""
    return math.ceil(Z95 ** 2 * discordance / power_effect ** 2)


def decide(rule: dict, overall: dict, subset: dict | None = None, *, discordance: float) -> dict:
    """القرارُ من القاعدة المسجَّلة؛ وكلُّ قرارٍ يحمل سببه."""
    need = min_items(rule["power_effect"], discordance)
    if overall["pairs"] < need:
        return {"decision": "underpowered", "reason": f"pairs {overall['pairs']} < min_items {need}"}
    low, high = overall["ci95"]
    if rule["kind"] == "quality":
        if low > 0:
            return {"decision": "keep", "reason": "ci95_low_above_zero"}
        if high < rule["min_effect"]:
            return {"decision": "remove", "reason": "ci95_high_below_min_effect"}
        return {"decision": rule["on_inconclusive"], "reason": "inconclusive_registered_default"}
    if rule["kind"] == "guard":
        # الحارسُ لا يُحذف برقم جودةٍ وحده: كلفةٌ مثبَتة بلا نفعٍ مثبَت تُرفع إلى المالك
        benefit = subset is not None and subset["pairs"] > 0 and subset["ci95"][0] > 0
        cost = high < -rule["max_general_cost"]
        if cost and not benefit:
            return {"decision": "owner_review", "reason": "cost_shown_benefit_not_shown"}
        return {"decision": "keep", "reason": "benefit_shown" if benefit else "no_cost_shown"}
    raise AblationError("rule_invalid", rule["kind"])


def protocol() -> dict:
    return json.loads(PROTOCOL.read_text(encoding="utf-8"))


def _sha(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def judge(component: str, on: list[dict], off: list[dict]) -> dict:
    """المقارنةُ والقرارُ لمكوّنٍ من البروتوكول، من صفوف الذراعين وحدها."""
    data = protocol()
    spec = data["components"][component]
    if spec["status"] != "ready":
        raise AblationError("component_blocked", component)
    rule = spec["rule"]
    overall = compare(on, off)
    subset = None
    if rule["kind"] == "guard":
        subset = compare(on, off, categories=set(rule["benefit_categories"]))
        rest = {row["category"] for row in on} - set(rule["benefit_categories"])
        overall = compare(on, off, categories=rest)
    return {"component": component, "overall": overall, "benefit_subset": subset,
            **decide(rule, overall, subset, discordance=data["assumed_discordance"]),
            "meaning": spec["decisions"], "protocol_sha256": _sha(PROTOCOL.read_bytes())}
