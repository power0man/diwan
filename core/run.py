"""الحلقة: تحقّق ← بصمة ← حجز ← نداء ← تحقّق الجواب ← تسوية ← قيد.

وترتيبها هو العقد نفسه:
  * التحقّق **قبل** كل شيء، وقبل اختيار المزوّد.
  * مفتاح الإيقاف **قبل** تسليم الحمولة إلى المزوّد — ولو للتقدير.
  * الحجز **قبل** النداء — فالسقف حاجزٌ لا تقرير.
  * **جوابُ المزوّد لا يُصدَّق**: يُفحص، ويُبصَم القيد، **قبل** تحريك المال.
  * القيد في كل الحالات: نجاحًا وفشلًا وانقطاعًا ورفضًا — بحارس `finally`
    لا بتعداد أنواع الاستثناءات.
  * والانقطاع **نتيجةٌ غير مؤكّدة**: يُسوّى بالمحجوز، ولا يُعاد الفعل.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.budget import Budget, BudgetRefused
from core.canonical import check_payload, digest
from core.contracts import (CALL_ID, LOCAL_ONLY_POLICIES, Request, Response,
                            ToolCall, Usage)
from core.ledger import EFFECTFUL_KINDS
from core.validate import validated
from providers.base import ProviderError

MAX_COST_MICROS = 10**12  # سقفٌ مطلق: كلفةٌ فوقه خطأ مزوّد لا فاتورة


class RouteRefused(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code = code
        self.reason = reason


class ProviderFailed(RuntimeError):
    """نداءٌ فشل بلا إعادة: يحمل رمزَ المزوّد (`Outcome.error_code`) لا اسمَ استثناءٍ خام (ك٣٧)."""

    def __init__(self, code: str | None, reason: str):
        self.code = code or "provider_failed"
        self.reason = reason
        super().__init__(f"{reason} [{self.code}]")


@dataclass(frozen=True)
class Outcome:
    response: Response | None
    request_digest: str
    ledger_digest: str
    replayed: bool = False
    error_code: str | None = None


def _record(kind: str, req: Request, req_digest: str, **extra) -> dict:
    return {
        "kind": kind,
        "request_digest": req_digest,
        "model": req.model,
        "model_version": req.model_version,
        "data_policy": req.data_policy,
        "idempotency_key": req.idempotency_key,
        **extra,
    }


def _check_tool_calls(resp, req: Request) -> None:
    """نداءُ الأداة **مدخلٌ غير موثوق** — والنموذج لا يخترع أداةً.

    يُفحص قبل أن يراه المنفّذ: لا أداةً غيرَ مُعلَنة، ولا نداءَين بمعرّفٍ
    واحد، ولا نداءً حيث لم تُعلَن أدواتٌ أصلًا (وذاك فشلٌ مغلق: مزوّدٌ
    يردّ أدواتٍ لم تُطلَب منه يُخالف عقدَه).
    """
    calls = resp.tool_calls
    if not isinstance(calls, tuple):
        raise RouteRefused("tool_calls_type", "نداءات الأدوات tuple")
    if not calls:
        return
    declared = {t.name for t in req.tools}
    if not declared:
        raise RouteRefused("tool_calls_unsolicited",
                           "المزوّد ردّ نداءَ أداةٍ ولم تُعلَن له أدوات")
    previous_ids = {call.call_id for message in req.messages
                    for call in getattr(message, "tool_calls", ())}
    seen = set()
    for call in calls:
        if not isinstance(call, ToolCall):
            raise RouteRefused("tool_call_type", "عنصر ليس ToolCall")
        if not isinstance(call.call_id, str) or not CALL_ID.fullmatch(call.call_id):
            raise RouteRefused("tool_call_id_invalid", f"معرّف نداء غير صالح: {call.call_id!r}")
        if call.call_id in seen:
            raise RouteRefused("tool_call_id_duplicate", f"معرّف مكرَّر: {call.call_id}")
        if call.call_id in previous_ids:
            raise RouteRefused("tool_call_id_reused", "معرّف نداء استُعمل في تاريخ الطلب")
        seen.add(call.call_id)
        if call.name not in declared:
            raise RouteRefused("tool_call_undeclared", f"أداةٌ غير مُعلَنة: {call.name!r}")
        if not isinstance(call.arguments, dict):
            raise RouteRefused("tool_call_arguments", "وسائطُ الأداة كائن")


def _check_response(resp) -> None:
    """جوابُ المزوّد مدخلٌ خارجيّ: يُفحص كما يُفحص الطلب."""
    if not isinstance(resp, Response):
        raise RouteRefused("response_type", f"جواب غير مدعوم: {type(resp).__name__}")
    c = resp.cost_micros
    if isinstance(c, bool) or not isinstance(c, int):
        raise RouteRefused("cost_type", "كلفة المزوّد عدد صحيح بالميكرو-دولار")
    if c < 0:
        raise RouteRefused("cost_negative", f"كلفة سالبة: {c}")
    if c > MAX_COST_MICROS:
        raise RouteRefused("cost_implausible", f"كلفة فوق السقف المطلق: {c}")
    u = resp.usage
    if not isinstance(u, Usage):
        raise RouteRefused("usage_type", "الاستهلاك Usage")
    for name, v in (("input_tokens", u.input_tokens), ("output_tokens", u.output_tokens)):
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise RouteRefused("usage_value", f"{name} عدد صحيح غير سالب")
    if not isinstance(resp.content, str):
        raise RouteRefused("content_type", "المحتوى نصّ")
    if resp.stop_reason not in ("complete", "max_output", "deadline", "error", "refused"):
        raise RouteRefused("stop_reason_unknown", f"سبب توقّف غير معروف: {resp.stop_reason!r}")


def _is_local(provider) -> bool:
    """المحليّة تُقاس بالهوية لا بالصدق المنطقي.

    فدالّةٌ غير مُستدعاة، ونصٌّ «false»، وقائمةٌ فيها صفر — كلّها صادقة
    منطقيًّا. والعقد في providers/base.py يُعلن `is_local: bool`، فيُشترط.
    """
    return getattr(provider, "is_local", None) is True


def _response_error(response: Response | None) -> str | None:
    """Keep a provider stop distinguishable even when replaying an old ok record."""
    if response is not None and response.stop_reason != "complete":
        return "response_" + response.stop_reason
    return None


def _restore_response(payload, request: Request) -> Response | None:
    if payload is None:
        return None
    try:
        response = Response(
            content=payload["content"], usage=Usage(**payload["usage"]),
            stop_reason=payload["stop_reason"], cost_micros=payload["cost_micros"],
            provider=payload.get("provider", ""), model_version=payload.get("model_version", ""),
            tool_calls=tuple(ToolCall(**call) for call in payload.get("tool_calls", ())),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RouteRefused("replay_response_invalid", "جواب محفوظ لا يطابق عقد الاستعادة") from None
    _check_response(response)
    _check_tool_calls(response, request)
    check_payload(payload, "ledger.response")
    return response


def execute(request: Request, provider, budget: Budget, ledger) -> Outcome:
    # ١ — التحقّق الإلزامي. قبل المزوّد، وقبل المال، وقبل البصم.
    req = validated(request)
    req_digest = digest(req.fingerprint_payload())

    # ٢ — الخصوصية. قبل أن يرى المزوّد الحمولة — ولو للتقدير. وتُقيَّد المحاولة.
    if req.data_policy in LOCAL_ONLY_POLICIES and not _is_local(provider):
        ledger.append(_record("refused", req, req_digest,
                              error_code="policy_requires_local",
                              provider_name=str(getattr(provider, "name", "?"))))
        raise RouteRefused("policy_requires_local",
                           f"تصنيف {req.data_policy} لا يخرج إلى مزوّد غير محليّ")

    # ٣ — مفتاح الإيقاف قبل تسليم الحمولة إلى المزوّد.
    if budget.kill_switch:
        ledger.append(_record("refused", req, req_digest, error_code="kill_switch"))
        raise RouteRefused("kill_switch", "مفتاح الإيقاف مرفوع")

    # ٤ — عدم التكرار: الفعل المقيَّد لا يُعاد. والرفض ليس فعلًا فلا
    # يُطابَق — **وكذلك العطلُ القابل للإعادة**: retryable=true معناه
    # «المزوّد لم يرَ الطلب»، فليس فعلًا يُحمى من تكراره، ولو طابقه
    # العرضُ لتسمَّم المفتاح الحتمي عبر التشغيلات: نصٌّ أخفق تعريبُه
    # لانقطاعٍ عابر كان يصير غير قابل للتعريب أبدًا (عيب تدقيق م٥).
    # فحص التنازع يبقى على **كل** القيود: المفتاح نفسه لحمولة مختلفة
    # يُرَدّ ولو كان قيدُه عطلًا قابلًا للإعادة.
    if req.idempotency_key:
        prior = None
        for e in ledger.entries():
            rec = e.get("record", {})
            if rec.get("idempotency_key") != req.idempotency_key \
                    or rec.get("kind") not in EFFECTFUL_KINDS:
                continue
            if rec.get("request_digest") != req_digest:
                raise RouteRefused("idempotency_key_conflict",
                                   "المفتاح نفسه لحمولة مختلفة")
            if rec.get("kind") == "error" and rec.get("retryable") is True:
                continue    # لا فعل وقع — يُنادى حيًّا لا يُعاد عرضه
            prior = e
        if prior is not None:
            rec = prior["record"]
            resp = _restore_response(rec.get("response"), req)
            return Outcome(resp, req_digest, prior["digest"], replayed=True,
                           error_code=_response_error(resp) or rec.get("error_code"))

    # ٥ — الحجز قبل النداء.
    estimate = provider.estimate_micros(req)
    handle = f"{req_digest}:{ledger.count()}:{len(ledger.entries())}"
    try:
        budget.reserve(handle, estimate)
    except BudgetRefused as e:
        ledger.append(_record("refused", req, req_digest,
                              error_code=e.code, estimate_micros=estimate))
        raise RouteRefused(e.code, e.reason) from e

    # ٦ — من هنا: كلُّ مخرجٍ يُسوّي ويُقيّد. الحارس لا يُعدّ أنواع الاستثناءات.
    settled = False
    try:
        try:
            resp = provider.complete(req)
        except ProviderError as e:
            spent = budget.settle_unknown(handle)
            settled = True
            d = ledger.append(_record("error", req, req_digest, error_code=e.code,
                                      retryable=bool(e.retryable), settled_micros=spent))
            return Outcome(None, req_digest, d, error_code=e.code)

        _check_response(resp)
        _check_tool_calls(resp, req)

        # القيد يُبصَم **قبل** تحريك المال: فلا يُخصم ثم يتعذّر القيد.
        body = _record("ok", req, req_digest, estimate_micros=estimate,
                       settled_micros=resp.cost_micros,
                       response={
                           "content": resp.content,
                           "usage": {"input_tokens": resp.usage.input_tokens,
                                     "output_tokens": resp.usage.output_tokens},
                           "stop_reason": resp.stop_reason,
                           "cost_micros": resp.cost_micros,
                           "provider": str(resp.provider),
                           "model_version": str(resp.model_version),
                           # يظهر حين يوجد فقط: قيودُ ما قبل الأدوات تبقى
                           # كما هي، ولا تنزاح بصمةُ سجلٍّ مختوم.
                           **({"tool_calls": [c.declared() for c in resp.tool_calls]}
                              if resp.tool_calls else {}),
                       })
        check_payload(body, "ledger.record")

        spent = budget.settle(handle, resp.cost_micros)
        settled = True
        d = ledger.append(body)
        return Outcome(resp, req_digest, d, error_code=_response_error(resp))

    finally:
        if not settled and handle in budget.reservations:
            # انقطاعٌ أو عطلٌ لم يُصنَّف: النتيجة غير مؤكّدة، فتُسوّى بالمحجوز،
            # ويُقيَّد أثرٌ يمنع إعادة الفعل بالمفتاح نفسه.
            spent = budget.settle_unknown(handle)
            try:
                ledger.append(_record("error", req, req_digest,
                                      error_code="aborted_unclassified",
                                      retryable=False, settled_micros=spent))
            except Exception:
                pass  # لا يُخفى الاستثناء الأصلي بخطأ قيدٍ تابع
