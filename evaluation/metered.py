"""النداءُ السحابيُّ عبر `core.run` بمزوّدٍ `is_local=False` مسعَّر، فتعمل `Budget` فعلًا (جديد-spend-ledger، البند ٣ من #295).

كانت نقولُ المراجعة الخارجية (`OllamaChat` على ollama.com، وموجّهُ HF) تنادي الشبكةَ خارج `core.run`، فلا حجزَ قبل النداء ولا
قيدَ في سجلٍّ مبصوم. هنا يُغلَّف أيُّ نقلٍ `transport(model, system, user, schema) -> str` بمزوّدٍ يمرّ بـ`core.run.execute`:
- **التقديرُ قبل النداء** من `registry/prices.json` (`core/prices.py`): بايتاتُ الرسالتين وحدُّ التأطير بسعر المدخل، و`max_output`
  بسعر المخرج، و`max_output` هو حدُّ النقل نفسُه الذي يفرضه على السلك (`max_tokens`: `num_predict` في Ollama) لا رقمٌ مستقلّ؛ والاشتراكُ الثابت صفرٌ بأساسه؛ وفهرسٌ حيٌّ يلزمه سعرٌ مثبَّت من النقل نفسِه (`_pin`). وبلا سعرٍ `price_unknown`
  قبل الشبكة.
- **الحجزُ والتسوية** بـ`core.budget.Budget`: ما فوق السقف `spend_cap_reached` ولا نداء. والتسويةُ بالكلفة التي أبلغها المزوّد
  إن أبلغها، وإلا بتوكنات الردّ بالسعر نفسِه. وردٌّ بلا توكناتٍ كاملة `usage_missing`: يُسوّى بالمحجوز كلِّه (المزوّدُ قد يكون
  نفّذ) ويُقيَّد خطأً، فلا يُحسب نداءٌ سحابيٌّ بلا توكنات.
- **القيدُ** في `core.ledger.Ledger` المبصوم: كلُّ نداءٍ قيدٌ فيه التقديرُ والمسوّى والتوكنات. ويُرسّى السجلُّ بعد كل نداءٍ قُيِّد أو رُفض
  (`anchor`)، فيُكشف قصُّ ذيله بـ`verify_chain(strict=True)`. وتقريرُ الإنفاق يجمع ما سُوّي على
  النداءات **وما سُوّي على الأعطال** (`settled_micros` في كل رفض)، فلا يقول «صفرًا» لما خصمته الميزانية.

صفوفُ `provider_usage` للنقل تبقى كما يكتبها كاتبُها (حارسُ الختم يقرؤها بشكلها)؛ وما يضيفه هذا الغلاف يُكتب في `core_run`
من تقرير الإنفاق. والحدُّ: الطلبُ هنا رسالتان نصّيتان بلا أدواتٍ ولا تفكير، لأن المراجعةَ الخارجية هكذا تُرسَل.

**حدودٌ معلَنة:** يُغلَّف اليوم نقلُ Ollama وحده (`tools/external_review.py`): ollama.com، والخادمُ المحليّ لنداءات نماذجه السحابية. وموجّهُ HF يحجز ويسوّي بـ`Budget` بنفسه (#308)
فلا يُغلَّف مرّتين على السقف نفسِه. وKimi يُنادى بأداة سطر أوامرٍ خارج هذه العملية (`tools/kimi_drive.sh`) فلا يمرّ بـ`core.run`،
والمحكِّمُ مسجَّلٌ لم يُشغَّل بعد (`evaluation/judge.py`)؛ وكلاهما يُغلَّف بهذا الغلاف حين يصير نداؤه في هذه العملية.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from core import prices as price_table
from core.budget import Budget
from core.contracts import Message, Request, Response, Usage
from core.ledger import LedgerCorrupt
from core.run import RouteRefused, execute
from evaluation.multi_system_review import AutomaticReviewError
from providers.base import ProviderError

# تأطيرُ المحادثة توكناتٌ لا تقابلها بايتاتٌ في التكليف؛ الحدُّ نفسُه الذي يحجزه موجّهُ HF (ملاحظة Codex على #308)
FRAMING_TOKENS = 512
MICROS_PER_USD = 1_000_000
_REFUSAL_CODES = {"day_cap": "spend_cap_reached", "month_cap": "spend_cap_reached"}


def _ceil_micros(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def _enforced_max_output(transport, requested: int | None) -> int:
    """حدُّ المخرج الذي يُحجز عليه هو الحدُّ الذي يفرضه النقلُ على السلك (`max_tokens`: `num_predict` في Ollama، و`max_tokens` في
    الواجهات المتوافقة)، لا رقمٌ مستقلّ عنه؛ فحجزٌ على حدٍّ لا يُفرض يسمح لنقلٍ مسعَّر بتجاوز السقف (ملاحظة Codex على #295).
    نقلٌ بلا حدٍّ معلَن لا يُغلَّف، وحدٌّ مطلوبٌ يخالف حدَّ النقل يُرفض."""
    enforced = getattr(transport, "max_tokens", None)
    if type(enforced) is not int or enforced <= 0:
        raise ValueError("max_output_unenforced: النقلُ لا يعلن max_tokens فلا يُحجز على حدٍّ لا يفرضه")
    if requested is not None and requested != enforced:
        raise ValueError(f"max_output_mismatch: المطلوب {requested} والنقلُ يفرض {enforced}")
    return enforced


class MeteredProvider:
    """مزوّدٌ غيرُ محليّ يغلّف نقلًا واحدًا لطلبٍ واحد؛ يُبنى لكل نداء ويُستهلك مرّة."""

    is_local = False

    def __init__(self, metered: "MeteredTransport", schema: dict):
        self.metered = metered
        self.schema = schema
        self.name = f"metered:{metered.provider_key}"
        self.entry: dict | None = None
        self.pin: dict | None = None
        self.estimate = 0
        self.call: dict | None = None

    def _price(self, model: str, bound: int) -> tuple[dict, dict | None]:
        try:
            entry = price_table.lookup(self.metered.prices, self.metered.provider_key, model)
        except price_table.PriceUnknown as exc:
            raise ProviderError("price_unknown", str(exc), retryable=False) from exc
        pin = None
        if entry["basis"] == "live_catalog":
            pin = self.metered.pin(model, bound)
            if pin is None:
                raise ProviderError("price_unknown", f"{entry['key']}:pin_required", retryable=False)
        return entry, pin

    def estimate_micros(self, request: Request) -> int:
        bound = sum(len(message.content.encode("utf-8")) for message in request.messages) + FRAMING_TOKENS
        self.entry, self.pin = self._price(request.model, bound)
        try:
            self.estimate = price_table.micros(self.entry, bound, request.max_output, pin=self.pin)
        except price_table.PriceUnknown as exc:
            raise ProviderError("price_unknown", str(exc), retryable=False) from exc
        return self.estimate

    def complete(self, request: Request) -> Response:
        transport, model = self.metered.transport, request.model
        system = "\n".join(m.content for m in request.messages if m.role == "system")
        user = "\n".join(m.content for m in request.messages if m.role == "user")
        rows = len(transport.provider_usage)
        try:
            content = transport(model, system, user, self.schema)
        except AutomaticReviewError as exc:
            raise ProviderError(exc.code, model, retryable=False) from exc
        row = transport.provider_usage[-1] if len(transport.provider_usage) > rows else None
        usage = (row or {}).get("usage") or {}
        counts = {key: usage.get(key) for key in ("prompt_tokens", "completion_tokens")}
        if any(type(value) is not int or value < 0 for value in counts.values()):
            raise ProviderError("usage_missing", model, retryable=False)
        if row is not None and row.get("cost_status") == "reported":
            cost = _ceil_micros(Decimal(str(row["cost_usd"])) * MICROS_PER_USD)
            basis = "reported_by_provider"
        else:
            cost = price_table.micros(self.entry, counts["prompt_tokens"], counts["completion_tokens"], pin=self.pin)
            basis = "subscription_flat" if self.entry["basis"] == "subscription_flat" else "estimated_from_prices"
        # لا يُحسب نداءً حتى تقبل النواةُ الردّ: ردٌّ ترفضه (كلفةٌ سالبة مثلًا) يُسوّى بالمحجوز ويُعدّ رفضًا وحده، فلا يجمع التقريرُ
        # كلفتَه المرفوضة فوق ما خُصم (ملاحظة Codex على #352)
        self.call = {"model": model, "price_key": self.entry["key"], "cost_basis": basis,
                     "estimate_micros": self.estimate, "cost_micros": cost, **counts}
        return Response(content=content, usage=Usage(counts["prompt_tokens"], counts["completion_tokens"]),
                        stop_reason="complete", cost_micros=cost, provider=self.name, model_version=model)


class MeteredTransport:
    """نقلٌ بالتوقيع نفسِه `(model, system, user, schema) -> str`، كلُّ نداءٍ فيه يمرّ بـ`core.run.execute`."""

    def __init__(self, transport, *, provider_key: str, budget: Budget, ledger, prices: dict | None = None,
                 max_output: int | None = None, deadline_s: float = 900.0, data_policy: str = "public",
                 meters=None):
        self.transport, self.provider_key = transport, provider_key
        # `meters(model)`: هل يُحاسَب نداءُ هذا النموذج. فالخادمُ المحليُّ يمرّر `:cloud` إلى الحساب السحابيّ ويجيب المحليَّ بنفسه
        # (ملاحظة Codex على #352)؛ وبلا `meters` يُحاسَب كلُّ نداء
        self.meters = meters
        self.budget, self.ledger = budget, ledger
        self.prices = prices if prices is not None else price_table.load()
        self.max_output = _enforced_max_output(transport, max_output)
        self.deadline_s, self.data_policy = deadline_s, data_policy
        self.calls: list[dict] = []
        self.refusals: list[dict] = []

    def __getattr__(self, name):
        # ما سوى النداء (الفهرسُ، والوصفُ، وسجلُّ المحاولات) للنقل المغلَّف نفسِه
        return getattr(self.transport, name)

    def pin(self, model: str, bound: int) -> dict | None:
        """السعرُ المثبَّت من فهرس النقل الحيّ (موجّه HF)، بوحدة الجدول؛ وNone إن لم يثبّت النقلُ سعرًا."""
        pinner = getattr(self.transport, "_pin", None)
        if pinner is None:
            return None
        pin = pinner(model, bound)
        return {side: price_table.usd_per_million_to_micros_per_1k(pin[side]) for side in ("input", "output")}

    def __call__(self, model: str, system: str, user: str, schema: dict) -> str:
        if self.meters is not None and not self.meters(model):
            # نموذجٌ يجيبه الخادمُ المحليّ بنفسه: لا فاتورة، فلا حجزَ ولا قيد، وصفُّه في `provider_usage` كما يكتبه النقل
            return self.transport(model, system, user, schema)
        self._verify_ledger()
        provider = MeteredProvider(self, schema)
        request = Request(messages=(Message(role="system", content=system), Message(role="user", content=user)),
                          model=model, model_version=model, max_output=self.max_output, deadline_s=self.deadline_s,
                          data_policy=self.data_policy, idempotency_key=None)
        before = self.budget.day_remaining_micros
        try:
            outcome = execute(request, provider, self.budget, self.ledger)
            if outcome.response is None:
                # عطلٌ قيّده `core.run` وسوّاه بالمحجوز: يُسمّى هنا كما يُسمّى الرفض، ولا يُحسب نداءً، وما سُوّي عليه يُحسب
                raise self._refused(model, outcome.error_code or "provider_error", provider, before)
            # يُحسب النداءُ قبل المرساة: مرساةٌ تسقط بعد التسوية (قرصٌ ممتلئ) لا تُسقط كلفتَه من التقرير (ملاحظة Codex على #352)
            self.calls.append(provider.call)
        except AutomaticReviewError:
            raise
        except RouteRefused as exc:
            raise self._refused(model, _REFUSAL_CODES.get(exc.code, exc.code), provider, before) from exc
        except ProviderError as exc:
            raise self._refused(model, exc.code, provider, before) from exc
        except Exception:
            # عطلٌ لم يصنّفه `core.run`: سوّاه بالمحجوز وقيّده `aborted_unclassified`؛ يُحسب هنا ثم يُرفع كما هو
            self._refused(model, "aborted_unclassified", provider, before)
            raise
        finally:
            # المرساةُ بعد كل ما قيّده `core.run` نجاحًا أو رفضًا أو عطلًا: بلا مرساةٍ لا يُكشف قصُّ آخر قيود الإنفاق (ملاحظة Codex
            # على #352)
            self.ledger.anchor()
        return outcome.response.content

    def _verify_ledger(self) -> None:
        """يُتحقَّق من السجلّ بمرساته قبل أي قيدٍ جديد: كان قيدٌ جديدٌ ومرساتُه بعده يمحوان قصَّ ذيلٍ سابق، فيمرّ التحقّقُ الصارم
        بعدهما ويختفي قيدُ إنفاقٍ محذوف (ملاحظة Codex على #352). وسجلٌّ فيه قيودٌ بلا مرساة يُرفض كذلك؛ والفارغُ بلا مرساةٍ بدايةٌ."""
        # والعدُّ نفسُه يقرأ السجلّ: سجلٌّ تالفٌ بلا مرساة يُرفض بالاسم نفسِه لا استثناءً خامًا (ملاحظة Codex على #352)
        # والمرساةُ تطابق السجلَّ كلَّه لا ما قبلها وحده: قيودٌ صالحةُ السلسلة بعد آخر مرساة (انهيارٌ أو تحريرٌ يدويّ) كانت
        # يُرسّيها النداءُ التالي فتصير مشروعة (ملاحظة Codex على #352)
        try:
            if self.ledger.anchor_path.exists() or self.ledger.count():
                self.ledger.verify_chain(strict=True)
                anchor = self.ledger.read_anchor()
                if (anchor["count"], anchor["head"]) != (self.ledger.count(), self.ledger.head()):
                    raise LedgerCorrupt(f"قيودٌ بعد المرساة لم تُرسَّ: المرساة {anchor['count']} والسجلّ {self.ledger.count()}")
        except (LedgerCorrupt, UnicodeDecodeError) as exc:
            raise AutomaticReviewError("spend_ledger_corrupt", str(exc)) from exc

    def _refused(self, model: str, code: str, provider: MeteredProvider, before: int) -> AutomaticReviewError:
        # ما خصمته الميزانيةُ على هذا النداء وإن لم يُحسب نداءً: ردٌّ بلا توكناتٍ يُسوّى بالمحجوز كلِّه، فلا يُعلن التقريرُ صفرًا
        # لما خُصم (ملاحظة Codex على #295)؛ والرفضُ قبل الحجز صفرٌ لأن شيئًا لم يُحجز
        settled = before - self.budget.day_remaining_micros
        self.refusals.append({"model": model, "code": code, "estimate_micros": provider.estimate,
                              "settled_micros": settled})
        return AutomaticReviewError(code, model)

    def spend_report(self) -> dict:
        inner = self.transport.spend_report() if hasattr(self.transport, "spend_report") else {}
        on_calls = sum(call["cost_micros"] for call in self.calls)
        on_errors = sum(refusal["settled_micros"] for refusal in self.refusals)
        return {**inner, "core_run": {
            "ledger": str(getattr(self.ledger, "path", "")), "calls": self.calls, "refusals": self.refusals,
            "settled_usd": str(Decimal(on_calls + on_errors) / MICROS_PER_USD),
            "settled_on_errors_micros": on_errors,
            "outstanding_micros": self.budget.outstanding_micros}}
