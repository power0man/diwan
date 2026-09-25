"""ضوابط كشف نتائج التقييم — الخمسة، مفروضةً لا موصوفة.

حتى مع حجب الحالات، يؤدّي كشفُ الدرجات بعد محاولاتٍ كثيرة إلى الإفراط
في ملاءمة المقياس. فهذه الضوابط تُمنَع بالبنية لا بالتعليمات:

  ١. النظام يرى **المهمة** دون الجواب المرجعي ولا بيانات التصحيح.
  ٢. حالاتُ تطويرٍ منفصلة يجوز كشف تفاصيلها.
  ٣. عددُ المحاولات يُحدَّد سلفًا ويُسجَّل.
  ٤. يجوز كشف نتائج **مجمَّعة** دون الأجوبة المرجعية.
  ٥. كل حالةٍ يُكشَف حلّها **تُنقل إلى مجموعة التطوير**.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class DisclosureRefused(RuntimeError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class Case:
    case_id: str
    prompt: str
    reference: str          # لا يخرج من هنا إلى النظام أبدًا
    rubric: str = ""


@dataclass
class EvaluationSession:
    """جلسةُ قبولٍ محكومة. المحاولات معدودة **ومفروضة**، والكشف مُقيَّد.

    والعدّاد ليس تقريريًّا: لا تسجيلَ نتيجةٍ بلا محاولةٍ مفتوحة، ولا كشفَ
    مجمَّعٍ إلا بإغلاقها. فمَن لم يستدعِ `begin_attempt` لا يسجّل شيئًا.

    و`min_aggregate` يمنع أن تصير القناةُ «المجمَّعة» نتيجةَ حالةٍ بعينها:
    تجميعُ حالةٍ واحدة كشفٌ لها. **وحدُّ الكشف ليس كاملًا**: يبقى الفرقُ
    بين تجميعين متتاليين قناةً ضيّقة، وحدُّها الحقيقيّ عددُ المحاولات.
    """

    cases: tuple[Case, ...]
    max_attempts: int
    min_aggregate: int = 5
    _attempts: int = 0
    _attempt_open: bool = False
    _revealed: set = field(default_factory=set)
    _scores: dict = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) \
                or self.max_attempts <= 0:
            raise DisclosureRefused("max_attempts", "عدد المحاولات يُحدَّد سلفًا، موجبًا")
        if not self.cases:
            raise DisclosureRefused("empty_suite", "مجموعة تقييمٍ فارغة لا تُقبل")
        if isinstance(self.min_aggregate, bool) or not isinstance(self.min_aggregate, int) \
                or self.min_aggregate < 1:
            raise DisclosureRefused("min_aggregate", "حدّ التجميع عدد صحيح موجب")
        if self.min_aggregate > len(self.cases):
            raise DisclosureRefused("min_aggregate_above_suite",
                                    "حدّ التجميع يتجاوز حجم المجموعة")

    # ١ — ما يراه النظام
    def visible_tasks(self) -> list[dict]:
        return [{"case_id": c.case_id, "prompt": c.prompt} for c in self.cases
                if c.case_id not in self._revealed]

    # ٣ — المحاولات
    def begin_attempt(self) -> int:
        if self._attempt_open:
            raise DisclosureRefused("attempt_already_open", "محاولةٌ مفتوحة لم تُغلق")
        if self._attempts >= self.max_attempts:
            raise DisclosureRefused("attempts_exhausted",
                                    f"استُنفدت المحاولات المعلنة ({self.max_attempts})")
        self._attempts += 1
        self._attempt_open = True
        self._scores.clear()          # كل محاولة تُقاس على نتائجها هي
        return self._attempts

    def record(self, case_id: str, passed: bool) -> None:
        if not self._attempt_open:
            raise DisclosureRefused("no_open_attempt",
                                    "لا تسجيلَ نتيجةٍ بلا محاولةٍ مفتوحة ومعدودة")
        if case_id in self._revealed:
            raise DisclosureRefused("case_moved_to_dev",
                                    f"الحالة {case_id} صارت مادّة تطوير")
        if not any(c.case_id == case_id for c in self.cases):
            raise DisclosureRefused("case_unknown", f"حالة غير معروفة: {case_id}")
        self._scores[case_id] = bool(passed)

    # ٤ — الكشف المجمَّع فقط
    def aggregate(self) -> dict:
        if not self._attempt_open:
            raise DisclosureRefused("no_open_attempt", "لا كشفَ مجمَّعٍ بلا محاولةٍ معدودة")
        live = [c.case_id for c in self.cases if c.case_id not in self._revealed]
        scored = {k: v for k, v in self._scores.items() if k in live}
        if not scored:
            raise DisclosureRefused("nothing_scored", "لا نتائج مسجَّلة لحالات حيّة")
        if len(scored) < self.min_aggregate:
            raise DisclosureRefused(
                "aggregate_too_small",
                f"تجميعُ {len(scored)} دون حدّ {self.min_aggregate} كشفٌ لحالةٍ بعينها")
        self._attempt_open = False    # التجميع يُغلق المحاولة، فتُعَدّ فعلًا
        return {
            "n": len(scored),
            "passed": sum(1 for v in scored.values() if v),
            "attempts_used": self._attempts,
            "attempts_declared": self.max_attempts,
            "live_cases": len(live),
            "revealed_cases": len(self._revealed),
        }

    def per_case_results(self):
        raise DisclosureRefused("per_case_disclosure_blocked",
                                "لا تُكشَف نتيجةُ حالةٍ بعينها؛ المجمَّع فقط")

    # ٥ — ما كُشف حلّه ينتقل
    def reveal(self, case_id: str) -> Case:
        """يُستدعى عند كشف الحلّ لمطوّرٍ أو وكيل. الحالة تخرج من القبول."""
        match = [c for c in self.cases if c.case_id == case_id]
        if not match:
            raise DisclosureRefused("case_unknown", f"حالة غير معروفة: {case_id}")
        self._revealed.add(case_id)
        self._scores.pop(case_id, None)
        return match[0]

    @property
    def holdout_size(self) -> int:
        return len([c for c in self.cases if c.case_id not in self._revealed])
