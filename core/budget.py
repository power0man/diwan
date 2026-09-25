"""سقف الصرف — قبل النداء لا بعده، ومفتاح إيقاف واحد.

الحجز يسبق النداء، والتسوية تعقبه على الاستهلاك المُبلَّغ. والانقطاع
نتيجةٌ غير مؤكّدة: يُسوّى بالمحجوز لا بصفر، لأنّ المزوّد قد يكون نفّذ.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class BudgetRefused(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code = code
        self.reason = reason


@dataclass
class Budget:
    """كل المبالغ **ميكرو-دولار صحيح**. لا عشريّ في المال."""

    day_remaining_micros: int
    month_remaining_micros: int
    kill_switch: bool = False
    _reserved: dict[str, int] = field(default_factory=dict)

    def reserve(self, handle: str, estimate_micros: int) -> int:
        if self.kill_switch:
            raise BudgetRefused("kill_switch", "مفتاح الإيقاف مرفوع")
        if isinstance(estimate_micros, bool) or not isinstance(estimate_micros, int):
            raise BudgetRefused("estimate_type", "التقدير عدد صحيح بالميكرو-دولار")
        if estimate_micros < 0:
            raise BudgetRefused("estimate_negative", f"تقدير سالب: {estimate_micros}")
        if handle in self._reserved:
            raise BudgetRefused("handle_reused", f"مقبض حجزٍ مستعمل: {handle}")
        if estimate_micros > self.day_remaining_micros:
            raise BudgetRefused("day_cap", "التقدير يتجاوز متبقّي اليوم")
        if estimate_micros > self.month_remaining_micros:
            raise BudgetRefused("month_cap", "التقدير يتجاوز متبقّي الشهر")
        self._reserved[handle] = estimate_micros
        self.day_remaining_micros -= estimate_micros
        self.month_remaining_micros -= estimate_micros
        return estimate_micros

    def settle(self, handle: str, actual_micros: int) -> int:
        """يُعيد الفرق إلى الرصيد. الاستهلاك الفعلي قد يفوق التقدير فيُخصم."""
        if handle not in self._reserved:
            raise BudgetRefused("handle_unknown", f"مقبض غير محجوز: {handle}")
        if isinstance(actual_micros, bool) or not isinstance(actual_micros, int):
            raise BudgetRefused("actual_type", "الاستهلاك عدد صحيح بالميكرو-دولار")
        if actual_micros < 0:
            raise BudgetRefused("actual_negative", f"استهلاك سالب: {actual_micros}")
        reserved = self._reserved.pop(handle)
        delta = reserved - actual_micros
        self.day_remaining_micros += delta
        self.month_remaining_micros += delta
        return actual_micros

    def settle_unknown(self, handle: str) -> int:
        """انقطاعٌ بلا استهلاك مُبلَّغ: يُسوّى بالمحجوز — لا يُفترض أنه لم يُصرف."""
        if handle not in self._reserved:
            raise BudgetRefused("handle_unknown", f"مقبض غير محجوز: {handle}")
        return self.settle(handle, self._reserved[handle])

    @property
    def reservations(self) -> frozenset:
        """المقابض المحجوزة — ليقرأها الحارس دون أن يلمس الحالة."""
        return frozenset(self._reserved)

    @property
    def outstanding_micros(self) -> int:
        return sum(self._reserved.values())
