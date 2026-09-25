"""الحلقة: يفعل ← يرى الأثر ← يصحّح — فوق النواة المحكومة لا بدلًا منها.

كلُّ خطوةٍ نداءٌ واحدٌ عبر `core.run.execute`: تحقّقٌ وبصمةٌ وحجزٌ وفحصٌ
وتسويةٌ وقيد. فما تكسبه الحلقةُ من النواة ليس تجميلًا — الوكيلُ ينادي
عشرَ مراتٍ في المهمّة الواحدة، فالميزانيةُ وعدمُ التكرار وإعادةُ العرض
تصير ضرورةً لا ترفًا.

تمييزُ مخارج الحلقة جزء من عقدها:
  * `complete`       — أجاب النموذج بلا نداءِ أداة.
  * `awaiting_owner` — نداءٌ بدرجةٍ لا يمنحها الميثاق. **يتوقف** ولا يكمل:
                       إكمالُ الاستدلال على نتيجةٍ لم تقع تخمينٌ لا عمل.
  * `step_limit`     — استُنفد سقفُ الخطوات. **ليس فشلًا وليس نجاحًا**:
                       عجزٌ مُعلَن يُعرض بما أُنجز، لا حلقةٌ عمياء.
  * توقف المزوّد أو نتيجة فعل مجهولة لا يُسمّيان اكتمالًا ولا يجيزان أداة.

مفتاح عدم التكرار يشمل حمولة الطلب الثابتة كاملةً وهوية الجولة، لا نصوص
الرسائل وحدها. خطة الأدوات تودع قبل أول أثر؛ الإيصال المكتمل يعاد عرضه
والفعل الذي بدأ دون إيصال اكتمال يتوقف مجهول النتيجة. لا تنفيذ مباشر بلا مخزن.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json

from core.budget import Budget, BudgetRefused
from core.canonical import PayloadRejected, digest
from core.contracts import Message, Request, ToolCall
from core.run import Outcome, RouteRefused, execute
from core.ledger import EFFECTFUL_KINDS
from core.quoted import quarantine
from core.validate import validated
from agent.registry import ToolContext, ToolRegistry

MAX_STEPS = 8

SYSTEM = (
    "أنت «ديوان»: مساعدٌ يعمل بالأدوات لا بالوصف. "
    "لديك أدواتٌ مُعلَنة — استعملها لتتحقّق بدل أن تخمّن: اقرأ الملفَّ قبل "
    "أن تصفه، وشغّل الاختبار قبل أن تقول إنه يمرّ. "
    "وإن ردَّت أداةٌ نداءك برمزٍ مسمّى فصحّح النداء ولا تكرّره كما هو. "
    "وإن نقصك ما لا تبلغه أداةٌ فقل ما نقصك صراحةً؛ «لم أستطع» أصدقُ من "
    "جوابٍ مؤلَّف. وحين تفرغ، أجب بلا نداءِ أداة."
)


@dataclass(frozen=True)
class Step:
    index: int
    content: str
    request_digest: str
    ledger_digest: str | None
    replayed: bool
    tool_results: tuple = ()
    stop_reason: str = "complete"
    tool_calls: tuple = ()
    quarantined: tuple = ()     # (call_id, code) لكل مقطعٍ حُجر من نتيجة أداة
    cost_micros: int = 0        # ما سُوّي لهذا النداء؛ والمُعاد عرضُه صفرٌ لأنه لم يُنفق جديدًا
    thinking: str = ""          # تفكيرُ النموذج محجورًا حين طُلب (ك٤٧)؛ لا يعود إليه رسالةً


@dataclass(frozen=True)
class Run:
    status: str
    code: str | None
    answer: str
    steps: tuple = ()
    pending: tuple = ()
    transcript: tuple = ()

    @property
    def cost_micros(self) -> int:
        """ما أنفقه هذا التشغيل: مجموعُ ما سُوّي لخطواته (ك٤٢). كان صفرًا ثابتًا."""
        return sum(step.cost_micros for step in self.steps)


# حقولُ التحكّم في نتيجة الأداة: رموزٌ وبصماتٌ وأعداد، لا مادّةٌ يقرؤها النموذج.
_CONTROL_KEYS = frozenset({"status", "code", "action_id", "reverts_to"})


def _quarantine_value(value, findings: list[str]):
    """كلُّ نصٍّ في نتيجة الأداة مادّةٌ خارجية: يُحجَر ما فيه من أمرٍ موجَّهٍ إلى المساعد."""
    if isinstance(value, str):
        held = quarantine(value)
        findings.extend(f.code for f in held.findings)
        return held.text
    if isinstance(value, list):
        return [_quarantine_value(item, findings) for item in value]
    if isinstance(value, dict):
        return {key: _quarantine_value(item, findings) for key, item in value.items()}
    return value


def quarantined_result(result: dict) -> tuple[dict, tuple[str, ...]]:
    """نتيجةُ الأداة كما تصل النموذجَ، وما حُجر منها برموزه.

    ملفٌّ يقرؤه الوكيل أو مخرَجُ أمرٍ أو نتيجةُ بحثٍ بياناتٌ لا تعليمات؛ فأمرٌ
    مدسوسٌ فيه («تجاهل التعليمات…»، «[ملاحظة إلى المساعد: …]») لا يبلغ النموذج
    بل يُستبدل بعلامةٍ ظاهرةٍ تحمل رمزَه، كما يُحجَر المقتبَسُ في مسار المحادثة
    (`core/quoted.py`). والحدُّ حدُّ الحاجر: مطابقةٌ بالأنماط تخفض السطح ولا تُثبته.
    """
    findings: list[str] = []
    payload = {k: (v if k in _CONTROL_KEYS else _quarantine_value(v, findings))
               for k, v in result.items() if k not in ("call_id", "name")}
    return payload, tuple(findings)


def _result_message(result: dict) -> Message:
    """نتيجةُ الأداة تعود إلى النموذج كاملةً برمزها — نجاحًا ورفضًا — بعد حَجر أوامرها."""
    payload, _ = quarantined_result(result)
    return Message("tool", json.dumps(payload, ensure_ascii=False, sort_keys=True),
                   tool_call_id=result["call_id"])


def _keyed_request(request: Request, prefix, session_id, turn_id, ledger) -> Request:
    """Reuse matching historical model records without authorizing old tool effects."""
    validated(request)
    if prefix is not None and (not isinstance(prefix, str) or not prefix.strip()):
        raise PayloadRejected("agent.idempotency_prefix", "idempotency_prefix_invalid", "بادئة نصية غير فارغة مطلوبة")
    if prefix is None and session_id is None:
        return request  # Preserve callers that did not request text replay.
    payload = {"request": request.fingerprint_payload()}
    if session_id is not None:
        payload["session_id"], payload["turn_id"] = session_id, turn_id
    key = f"{prefix or 'agent'}-{digest(payload)}"
    if prefix is not None:
        legacy_key = f"{prefix}-{digest([[m.role, m.content] for m in request.messages])[:24]}"
        request_digest = digest(request.fingerprint_payload())
        # The old loop derived keys from role/content only. Preserve text replay,
        # but the caller still requires a saved ActionStore step before any tool.
        for entry in ledger.entries():
            record = entry.get("record", {})
            if (record.get("kind") in EFFECTFUL_KINDS
                    and record.get("idempotency_key") == legacy_key
                    and record.get("request_digest") == request_digest):
                key = legacy_key
                break
    return replace(request, idempotency_key=key)


def run_agent(task: str, provider, registry: ToolRegistry, context: ToolContext, *,
              ledger, budget: Budget, model: str, model_version: str,
              max_steps: int = MAX_STEPS, max_output: int = 1024,
              deadline_s: float = 120.0, data_policy: str = "local_only",
              system: str = SYSTEM, idempotency_prefix: str | None = None,
              action_store=None, session_id: str | None = None,
              turn_id: str | None = None, initial_messages: tuple[Message, ...] = (),
              stop_check=None, thinking: bool = False) -> Run:
    if not isinstance(task, str) or not task.strip():
        raise ValueError("المهمّةُ نصٌّ غير فارغ")
    if type(max_steps) is not int or not 1 <= max_steps <= 64:
        raise ValueError("سقفُ الخطوات بين ١ و٦٤")
    if not isinstance(initial_messages, tuple):
        raise ValueError("تاريخ الرسائل tuple مكتمل قبل الطلب الجديد")
    if stop_check is not None and not callable(stop_check):
        raise ValueError("stop_check must be callable")
    if not isinstance(thinking, bool):
        raise ValueError("طلبُ التفكير True أو False")
    def stopped():
        return stop_check is not None and stop_check() is True
    specs = registry.specs()
    messages = list(initial_messages) if initial_messages else [Message("system", system)]
    messages.append(Message("user", task))
    steps: list[Step] = []

    def finish(status, code, answer, pending=()):
        return Run(status, code, answer, tuple(steps), tuple(pending), tuple(messages))

    if action_store is not None or session_id is not None or turn_id is not None:
        if any(not isinstance(value, str) or not value.strip() for value in (session_id, turn_id)):
            return finish("refused", "action_identity_required", "")

    for index in range(max_steps):
        if stopped():
            return finish("cancelled", "stop_requested", steps[-1].content if steps else "")
        request = Request(messages=tuple(messages), model=model,
                          model_version=model_version, max_output=max_output,
                          deadline_s=deadline_s, data_policy=data_policy,
                          idempotency_key=None, tools=specs, thinking=thinking)
        try:
            request = _keyed_request(request, idempotency_prefix, session_id, turn_id, ledger)
            outcome: Outcome = execute(request, provider, budget, ledger)
        except (RouteRefused, BudgetRefused, PayloadRejected) as exc:
            return finish("refused", exc.code, "")

        response = outcome.response
        # الكلفةُ التي سوّاها `execute` لهذا النداء؛ والمُعاد عرضُه لم يُنفق جديدًا
        spent = 0 if response is None or outcome.replayed else response.cost_micros
        # محجورٌ في النواة قبل القيد؛ يُحفظ في الخطوة ولا يدخل `messages` أبدًا
        thought = "" if response is None else response.thinking
        if response is None:
            steps.append(Step(index, "", outcome.request_digest, outcome.ledger_digest,
                              outcome.replayed))
            return finish("failed", outcome.error_code or "response_missing", "")

        if response.stop_reason != "complete":
            steps.append(Step(index, response.content, outcome.request_digest,
                              outcome.ledger_digest, outcome.replayed,
                              stop_reason=response.stop_reason, tool_calls=response.tool_calls, cost_micros=spent, thinking=thought))
            status = {"max_output": "truncated", "deadline": "timed_out",
                      "error": "failed", "refused": "refused"}[response.stop_reason]
            return finish(status, outcome.error_code or "response_" + response.stop_reason, response.content)

        # A returned model response has already reached the durable core ledger.
        # Cancellation can suppress subsequent work, never erase that record.
        if stopped():
            steps.append(Step(index, response.content, outcome.request_digest,
                              outcome.ledger_digest, outcome.replayed,
                              tool_calls=response.tool_calls, cost_micros=spent, thinking=thought))
            return finish("cancelled", "stop_requested", response.content)

        if not response.tool_calls:
            steps.append(Step(index, response.content, outcome.request_digest,
                              outcome.ledger_digest, outcome.replayed, cost_micros=spent, thinking=thought))
            messages.append(Message("assistant", response.content))
            return finish("complete", None, response.content)

        messages.append(Message("assistant", response.content, tool_calls=response.tool_calls))
        if action_store is None:
            steps.append(Step(index, response.content, outcome.request_digest,
                              outcome.ledger_digest, outcome.replayed, tool_calls=response.tool_calls, cost_micros=spent, thinking=thought))
            return finish("refused", "action_store_required", response.content)

        try:
            action_store.register_step(session_id=session_id, turn_id=turn_id,
                step_index=index, request_digest=outcome.request_digest,
                calls=response.tool_calls, specs=specs, allow_new=not outcome.replayed)
        except PayloadRejected as exc:
            steps.append(Step(index, response.content, outcome.request_digest,
                              outcome.ledger_digest, outcome.replayed, tool_calls=response.tool_calls, cost_micros=spent, thinking=thought))
            return finish("refused", exc.code, response.content)

        results, pending, held = [], [], []
        for call_index, call in enumerate(response.tool_calls):
            if stopped():
                break
            result = registry.invoke_prepared(call, context, store=action_store,
                session_id=session_id, turn_id=turn_id, step_index=index,
                call_index=call_index, request_digest=outcome.request_digest,
                allow_new=True)
            results.append(result)
            if result["status"] == "awaiting_owner":
                pending.append(result)
                break  # Do not execute later calls in this ordered batch.
            if result["status"] == "outcome_unknown":
                break
            if result["status"] == "refused" and "action_id" not in result:
                break  # Receipt/setup errors do not authorize later effects.
            held.extend((result["call_id"], code) for code in quarantined_result(result)[1])
            messages.append(_result_message(result))

        steps.append(Step(index, response.content, outcome.request_digest,
                          outcome.ledger_digest, outcome.replayed, tuple(results),
                          tool_calls=response.tool_calls, quarantined=tuple(held),
                          cost_micros=spent, thinking=thought))
        if results and results[-1]["status"] == "outcome_unknown":
            return finish("outcome_unknown", results[-1].get("code", "action_outcome_unknown"), response.content)
        if stopped():
            return finish("cancelled", "stop_requested", response.content)
        if pending:
            # لا يُكمل الاستدلالَ على نتيجةٍ لم تقع. وما نُفّذ قبلها مُقيَّدٌ
            # في الخطوة، فلا يضيع عملٌ ولا يُدَّعى عملٌ لم يقع.
            return finish("awaiting_owner", "consent_required", response.content, pending)
        if results and results[-1]["status"] == "refused" and "action_id" not in results[-1]:
            return finish("refused", results[-1].get("code", "action_receipt_missing"), response.content)

    return finish("step_limit", "max_steps_exhausted", steps[-1].content if steps else "")
