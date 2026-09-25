"""دليل قبول م٠ — بنصّه:

    طلب يمرّ ← جواب ← قيدٌ بالتكلفة الفعلية ← إيقافٌ عند السقف يُختبَر فعلًا.
"""
import pytest

from core.budget import Budget
from core.contracts import Message, Request
from core.ledger import Ledger
from core.run import RouteRefused, execute
from providers.base import ProviderError
from providers.echo import EchoProvider


def _req(key=None, max_output=16, text="اكتب فقرة"):
    return Request((Message("user", text),), "echo", "1", max_output, 5.0,
                   "local_only", key)


def test_a_request_passes_and_is_recorded_with_actual_cost(tmp_path):
    led, bud = Ledger(tmp_path / "l.jsonl"), Budget(10_000, 50_000)
    out = execute(_req(), EchoProvider(), bud, led)

    assert out.response is not None and out.response.content
    assert led.count() == 1 and led.verify_chain()
    rec = led.entries()[0]["record"]
    assert rec["kind"] == "ok"
    # القيد يحمل التكلفة **الفعلية**، وهي المُسوّاة لا المُقدَّرة
    assert rec["settled_micros"] == out.response.cost_micros
    assert bud.outstanding_micros == 0


def test_the_cap_stops_the_call_before_it_happens(tmp_path):
    """الإيقاف عند السقف يُختبَر فعلًا: المزوّد لا يُنادى أصلًا."""
    called = []

    class Spy(EchoProvider):
        def complete(self, request):
            called.append(1)
            return super().complete(request)

    led = Ledger(tmp_path / "l.jsonl")
    bud = Budget(day_remaining_micros=0, month_remaining_micros=50_000)
    with pytest.raises(RouteRefused) as e:
        execute(_req(max_output=1000), Spy(), bud, led)

    assert e.value.code == "day_cap"
    assert called == []                       # لم يُنادَ المزوّد
    assert led.entries()[0]["record"]["kind"] == "refused"
    assert bud.day_remaining_micros == 0      # ولم يُخصم شيء


def test_kill_switch_refuses_everything(tmp_path):
    bud = Budget(10_000, 50_000, kill_switch=True)
    with pytest.raises(RouteRefused) as e:
        execute(_req(), EchoProvider(), bud, Ledger(tmp_path / "l.jsonl"))
    assert e.value.code == "kill_switch"


def test_an_interrupted_call_is_settled_at_the_reservation(tmp_path):
    """الانقطاع نتيجةٌ غير مؤكّدة: لا يُفترض أنّ شيئًا لم يُصرف."""
    class Flaky(EchoProvider):
        def complete(self, request):
            raise ProviderError("timeout", "انقطاع", retryable=True)

    led, bud = Ledger(tmp_path / "l.jsonl"), Budget(10_000, 50_000)
    before = bud.day_remaining_micros
    out = execute(_req(), Flaky(), bud, led)

    assert out.response is None and out.error_code == "timeout"
    rec = led.entries()[0]["record"]
    assert rec["kind"] == "error" and rec["settled_micros"] > 0
    assert bud.day_remaining_micros < before      # خُصم المحجوز، لا صفر
    assert bud.outstanding_micros == 0


def test_idempotency_replays_instead_of_repeating_the_effect(tmp_path):
    calls = []

    class Counting(EchoProvider):
        def complete(self, request):
            calls.append(1)
            return super().complete(request)

    led, bud, p = Ledger(tmp_path / "l.jsonl"), Budget(10_000, 50_000), Counting()
    first = execute(_req(key="k-1"), p, bud, led)
    second = execute(_req(key="k-1"), p, bud, led)

    assert len(calls) == 1 and second.replayed and led.count() == 1
    assert second.response.content == first.response.content


def test_same_key_different_payload_is_refused(tmp_path):
    led, bud, p = Ledger(tmp_path / "l.jsonl"), Budget(10_000, 50_000), EchoProvider()
    execute(_req(key="k-1", text="أ"), p, bud, led)
    with pytest.raises(RouteRefused) as e:
        execute(_req(key="k-1", text="ب"), p, bud, led)
    assert e.value.code == "idempotency_key_conflict"
