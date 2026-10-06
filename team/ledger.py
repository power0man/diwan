"""سجلُّ تحوّلات التكليف: ستُّ حالاتٍ مربوطةٌ بدليلٍ خارجيّ ورأسِ إيداعٍ واحد، وحالاتٌ جانبية مسمّاة (ق٧٦ البندان ٦ و٧).

يرث `core.ledger.Ledger` (إلحاقٌ فقط، وسلسلةُ بصمات، ومرساةٌ تكشف قصَّ الذيل)؛ فكلُّ تحوّلٍ قيدٌ لا يُعدَّل ولا يُحذف،
والحالةُ الحالية **تُشتقّ من آخر قيد** ولا تُخزَّن. ما تفرضه الآلة هنا:

- الترتيب: `dispatched → claimed → completed → validated → verified → accepted`؛ وما خالفه `TransitionError` برمزٍ مسمًّى.
- الدليل: لكل حالةٍ مفاتيحُ دليلٍ لازمة (بصمةُ التكليف، ومعرّفُ العملية، ورأسُ الإيداع، ومرجعُ الفحوص، ومرجعُ المراجعة، وإيداعُ الدمج).
- رأسٌ واحد لكل دليل: `validated` و`verified` على رأس `completed` نفسِه، و`accepted` تشترط تساوي الرؤوس الثلاثة؛
  وإيداعٌ جديد (`completed` برأسٍ آخر) يُسقط ما قبله ويعيد التحقق والمراجعة.
- `outcome_unknown` لا يعني «أعد التشغيل»: إرسالٌ ثانٍ لمسألةٍ لها تكليفٌ غيرُ منتهٍ يُرفض `already_dispatched`،
  ولا يُسمح بتكليفٍ جديد إلا بعد قيد `takeover` يحمل الإثباتَ الثلاثي: انتهاءُ الإيجار، وغيابُ العامل، وإذنُ المالك.
- القفل: الفحصُ والإلحاقُ والمرساةُ عمليةٌ واحدة تحت قفلٍ حصريّ على ملفٍّ جانبيّ (`<السجلّ>.lock`)؛ فأمران متزامنان
  لمسألتين مستقلّتين لا يتنازعان البصمةَ السابقة ولا الرقمَ التسلسلي ولا ملفَ المرساة المؤقت.

**الحدُّ المعلَن:** السجلُّ يشهد على ما قُيّد فيه بدليله المسمّى، لا على صحّة الدليل نفسِه؛ وبصماتُ الرؤوس تُقارَن نصًّا.
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core.ledger import Ledger, LedgerCorrupt

MAIN_STATES: tuple[str, ...] = ("dispatched", "claimed", "completed", "validated", "verified", "accepted")
SIDE_STATES: frozenset[str] = frozenset({
    "outcome_unknown", "validation_failed", "expired", "worker_unavailable", "reviewer_unavailable",
    "brief_stale", "frozen_by_launch_plan", "already_dispatched", "review_uncalibrated", "takeover", "refused",
    "reviewed_awaiting_validation", "external_review", "review_rejected",
})
TERMINAL_STATES: frozenset[str] = frozenset({"accepted"})
# مفاتيحُ الدليل اللازمة لكل حالة؛ غيابُ واحدٍ منها يُرفض قبل الكتابة.
EVIDENCE: dict[str, tuple[str, ...]] = {
    "dispatched": ("brief_sha256", "worker", "family", "branch"),
    "claimed": ("pid", "started_at"),
    "completed": ("head_sha", "branch"),
    "validated": ("head_sha", "checks_ref"),
    "verified": ("head_sha", "review_ref", "reviewer", "reviewer_family"),
    "accepted": ("head_sha", "merge_sha"),
    "outcome_unknown": ("reason",),
    "validation_failed": ("reason",),
    "expired": ("last_activity_at",),
    "worker_unavailable": ("code",),
    "reviewer_unavailable": ("code",),
    "brief_stale": ("expected_sha256", "found_sha256"),
    "frozen_by_launch_plan": ("code",),
    "already_dispatched": ("blocking_state",),
    "review_uncalibrated": ("head_sha", "reviewer"),
    "takeover": ("lease_expired_at", "absence_proof", "owner_authorization"),
    "refused": ("code",),
    "reviewed_awaiting_validation": ("head_sha", "review_ref", "reviewer", "reviewer_family"),
    "external_review": ("pr", "head_sha", "review_ref", "reviewer", "reviewer_family"),
    "review_rejected": ("head_sha", "review_ref", "reviewer", "reviewer_family", "verdict"),
}
PREDECESSOR: dict[str, str] = {"claimed": "dispatched", "validated": "completed", "verified": "validated", "accepted": "verified"}
LEASE_SECONDS = 24 * 3600


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class TransitionError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else f"{code}: {detail}")


class TeamLedger:
    """سجلُّ التكليفات؛ قيدٌ لكل تحوّل، والمرساةُ تُكتب بعد كل قيد."""

    def __init__(self, path: str | os.PathLike, *, clock=now_utc):
        self.path = Path(path)
        self.ledger = Ledger(self.path, create=True)
        self.clock = clock
        with self._locked():                           # فتحٌ أثناء إلحاقِ عمليةٍ أخرى (قيدٌ كُتب والمرساةُ بعدُ) ليس عبثًا
            if self.ledger.anchor_path.exists():
                self.ledger.verify_chain(strict=True)      # يرمي LedgerCorrupt عند قصّ الذيل أو كسر السلسلة
            elif self.ledger.count():
                raise LedgerCorrupt("سجلٌّ بلا مرساة: قصُّ الذيل غير قابل للكشف")

    # — قراءة —

    def records(self, issue: int | None = None) -> list[dict]:
        out = []
        for entry in self.ledger.entries():
            record = entry.get("record", {})
            if record.get("kind") != "transition":
                continue
            if issue is None or record.get("issue") == issue:
                out.append(record)
        return out

    def last(self, issue: int) -> dict | None:
        records = self.records(issue)
        return records[-1] if records else None

    def main_state(self, issue: int) -> dict | None:
        """آخرُ قيدٍ من الحالات الست، لا الجانبية."""
        for record in reversed(self.records(issue)):
            if record["state"] in MAIN_STATES:
                return record
        return None

    def last_of(self, issue: int, state: str, attempt: int | None = None) -> dict | None:
        for record in reversed(self.records(issue)):
            if record["state"] == state and (attempt is None or record.get("attempt") == attempt):
                return record
        return None

    def attempt_of(self, issue: int) -> int:
        """رقمُ المحاولة الحالية: عددُ قيود `dispatched` للمسألة."""
        return sum(1 for r in self.records(issue) if r["state"] == "dispatched")

    def open_attempt(self, issue: int) -> dict | None:
        """التكليفُ غيرُ المنتهي إن وُجد: آخرُ حالةٍ رئيسة ليست `accepted` ولم يعقبها `takeover`."""
        state = self.main_state(issue)
        if state is None or state["state"] in TERMINAL_STATES:
            return None
        last_takeover = self.last_of(issue, "takeover")
        if last_takeover and last_takeover.get("attempt") == state.get("attempt"):
            return None
        return state

    # — كتابة —

    @property
    def lock_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _locked(self):
        """قفلٌ حصريّ يحيط بالفحص والإلحاق والمرساة معًا؛ بدونه كان أمران متزامنان يكتبان قيدين بالبصمة السابقة والرقم
        التسلسلي نفسَيهما ويتنازعان ملفَ المرساة المؤقت (ملاحظة Codex السابعة على #344)."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def append(self, issue: int, state: str, **evidence) -> str:
        with self._locked():
            return self._append(issue, state, **evidence)

    def _append(self, issue: int, state: str, **evidence) -> str:
        if state not in MAIN_STATES and state not in SIDE_STATES:
            raise TransitionError("unknown_state", state)
        missing = [key for key in EVIDENCE.get(state, ()) if evidence.get(key) in (None, "")]
        if missing:
            raise TransitionError(f"evidence_missing:{missing[0]}", state)
        attempt = evidence.pop("attempt", None)
        if state == "dispatched":
            self._check_dispatch(issue, evidence)
            attempt = self.attempt_of(issue) + 1
        elif attempt is None:
            attempt = self.attempt_of(issue)
        if state in MAIN_STATES and state != "dispatched":
            self._check_order(issue, state, attempt, evidence)
        if state == "takeover":
            self._check_takeover(issue, evidence)
        record = {"kind": "transition", "issue": int(issue), "attempt": int(attempt), "state": state,
                  "at": self.clock(), **evidence}
        digest = self.ledger.append(record)
        self.ledger.anchor()
        return digest

    # — القواعد —

    def _check_dispatch(self, issue: int, evidence: dict) -> None:
        blocking = self.open_attempt(issue)
        if blocking is not None:
            raise TransitionError("already_dispatched", f"state={blocking['state']} attempt={blocking['attempt']}")

    def _check_order(self, issue: int, state: str, attempt: int, evidence: dict) -> None:
        current = self.main_state(issue)
        if current is None or current.get("attempt") != attempt:
            raise TransitionError("out_of_order", f"{state} بلا تكليفٍ جارٍ في المحاولة {attempt}")
        if state == "completed":
            if current["state"] not in ("claimed", "completed", "validated", "verified"):
                raise TransitionError("out_of_order", f"completed بعد {current['state']}")
            return                                  # رأسٌ جديد يُسقط ما بعده ويعيد التحقق والمراجعة
        expected = PREDECESSOR[state]
        if current["state"] != expected:
            raise TransitionError("out_of_order", f"{state} بعد {current['state']} لا بعد {expected}")
        if state in ("validated", "verified", "accepted"):
            completed = self.last_of(issue, "completed", attempt)
            if completed is None or completed.get("head_sha") != evidence["head_sha"]:
                raise TransitionError("head_mismatch", f"{state} على رأسٍ غير رأس completed")
        if state == "accepted":
            validated = self.last_of(issue, "validated", attempt)
            verified = self.last_of(issue, "verified", attempt)
            heads = {evidence["head_sha"], validated and validated.get("head_sha"), verified and verified.get("head_sha")}
            if len(heads) != 1:
                raise TransitionError("head_mismatch", "validated_head_sha == reviewed_head_sha == merge_candidate_head_sha شرطُ القبول")

    def _check_takeover(self, issue: int, evidence: dict) -> None:
        proof = evidence.get("absence_proof")
        if not isinstance(proof, dict) or not all(proof.get(k) is True for k in ("no_process", "no_session", "no_new_commits")):
            raise TransitionError("absence_not_proven", "يلزم إثباتُ غياب العامل: لا عملية، ولا جلسة، ولا إيداع جديد")
        if not str(evidence.get("owner_authorization") or "").strip():
            raise TransitionError("owner_authorization_missing")
        if self.last_of(issue, "expired") is None:
            raise TransitionError("lease_not_expired", "لا قيدَ expired قبل الاستحواذ")


def lease_expired(last_activity_iso: str, now_iso: str, lease_seconds: int = LEASE_SECONDS) -> bool:
    last = datetime.fromisoformat(last_activity_iso)
    now = datetime.fromisoformat(now_iso)
    return (now - last).total_seconds() >= lease_seconds
