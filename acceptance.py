#!/usr/bin/env python3
"""مُشغِّل دليل قبول م٠ — يُعيد إنتاج الدليل ويطبعه، ويخرج بغير صفر عند الفشل.

    python3 acceptance.py

لا شبكة، ولا مفاتيح، ولا إنفاق: المزوّد محليّ حتميّ.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.budget import Budget
from core.contracts import Message, Request
from core.ledger import Ledger
from core.run import RouteRefused, execute
from providers.echo import EchoProvider

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def req(key=None, max_output=16, text="اكتب فقرةً عن الحوكمة"):
    return Request((Message("user", text),), "echo", "1", max_output, 5.0,
                   "local_only", key)


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    led, bud, prov = Ledger(tmp / "ledger.jsonl"), Budget(10_000, 50_000), EchoProvider()

    # ١ — طلب يمرّ، وجوابٌ يعود
    out = execute(req(key="acc-1"), prov, bud, led)
    check("طلبٌ يمرّ ويعود بجواب", out.response is not None and bool(out.response.content),
          out.response.content if out.response else "—")

    # ٢ — قيدٌ بالتكلفة الفعلية **لا المُقدَّرة**، والفرق بينهما مُحقَّق
    #     (سقفُ إخراجٍ كبير يرفع التقدير، والجوابُ قصير فتقلّ التسوية)
    led_b, bud_b = Ledger(tmp / "gap.jsonl"), Budget(10_000, 50_000)
    gap = execute(req(max_output=900, text="نعم"), prov, bud_b, led_b)
    grec = led_b.entries()[0]["record"]
    check("القيد يحمل التكلفة الفعلية المُسوّاة لا المُقدَّرة",
          grec["kind"] == "ok"
          and grec["settled_micros"] == gap.response.cost_micros
          and grec["estimate_micros"] > grec["settled_micros"],
          f"المُسوّى {grec['settled_micros']} والمُقدَّر {grec['estimate_micros']} — والفرق مردودٌ إلى الرصيد")

    # ٣ — السلسلة سليمة والحجز مُصفّى
    check("سلسلة السجل سليمة", led.verify_chain(), f"{led.count()} قيد")
    check("لا حجزَ معلّقًا", bud.outstanding_micros == 0,
          f"متبقّي اليوم {bud.day_remaining_micros}")

    # ٤ — الإيقاف عند السقف: المزوّد لا يُنادى
    called: list[int] = []

    class Spy(EchoProvider):
        def complete(self, request):
            called.append(1)
            return super().complete(request)

    led2, bud2 = Ledger(tmp / "ledger2.jsonl"), Budget(0, 50_000)
    code = None
    try:
        execute(req(max_output=1000), Spy(), bud2, led2)
    except RouteRefused as e:
        code = e.code
    check("السقف يوقف قبل النداء", code == "day_cap" and not called,
          f"الرمز {code}، ونداءات المزوّد {len(called)}")
    check("الرفض مُقيَّد في السجل", led2.entries()[0]["record"]["kind"] == "refused")

    # ٥ — عدم التكرار: الفعل لا يُعاد
    before = led.count()
    again = execute(req(key="acc-1"), prov, bud, led)
    check("المفتاح نفسه يُعيد القيد ولا يُعيد الفعل",
          again.replayed and led.count() == before)

    # ٦ — الخصوصية: التصنيف المحليّ لا يخرج
    class Remote:
        name, is_local = "remote", False
        def estimate_micros(self, r): return 1
        def complete(self, r): raise AssertionError("نُودي مزوّدٌ غير محليّ")

    code = None
    try:
        execute(req(), Remote(), Budget(10_000, 10_000), Ledger(tmp / "l3.jsonl"))
    except RouteRefused as e:
        code = e.code
    check("التصنيف المحليّ لا يرى مزوّدًا بعيدًا", code == "policy_requires_local",
          f"الرمز {code}")

    width = max(len(n) for n, _, _ in CHECKS)
    print("\n— دليل قبول م٠ —\n")
    for name, ok, detail in CHECKS:
        print(f"  {'✓' if ok else '✗'}  {name.ljust(width)}   {detail}")
    failed = [n for n, ok, _ in CHECKS if not ok]
    print(f"\n  {len(CHECKS) - len(failed)}/{len(CHECKS)} اجتازت.\n")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
