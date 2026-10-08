"""سجلُّ تحوّلات التكليف (ق٧٦ البندان ٦ و٧): ترتيبٌ مفروض، ودليلٌ لازم، ورأسٌ واحد، ولا إرسالَ ثانيًا بلا استحواذٍ مُثبَت."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.ledger import LedgerCorrupt
from team.ledger import TeamLedger, TransitionError, lease_expired


def _ledger(tmp_path, clock="2026-10-06T10:00:00+00:00"):
    return TeamLedger(tmp_path / "dispatch.jsonl", clock=lambda: clock)


def _dispatch(ledger, issue=7):
    ledger.append(issue, "dispatched", brief_sha256="b" * 64, worker="claude", family="anthropic", branch=f"team/{issue}-anthropic")
    ledger.append(issue, "claimed", pid=1, started_at="2026-10-06T10:00:00+00:00")


def test_states_follow_the_order(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(7, "dispatched", brief_sha256="b" * 64, worker="claude", family="anthropic", branch="team/7-anthropic")
    with pytest.raises(TransitionError) as exc:
        ledger.append(7, "validated", head_sha="a" * 40, checks_ref="checks:a")
    assert exc.value.code == "out_of_order"


def test_evidence_is_required(tmp_path):
    ledger = _ledger(tmp_path)
    with pytest.raises(TransitionError) as exc:
        ledger.append(7, "dispatched", worker="claude", family="anthropic", branch="team/7-anthropic")
    assert exc.value.code == "evidence_missing:brief_sha256"


def test_validated_must_match_the_completed_head(tmp_path):
    ledger = _ledger(tmp_path)
    _dispatch(ledger)
    ledger.append(7, "completed", head_sha="a" * 40, branch="team/7-anthropic")
    with pytest.raises(TransitionError) as exc:
        ledger.append(7, "validated", head_sha="c" * 40, checks_ref="checks:c")
    assert exc.value.code == "head_mismatch"
    ledger.append(7, "validated", head_sha="a" * 40, checks_ref="checks:a")
    assert ledger.main_state(7)["state"] == "validated"


def test_a_new_head_after_verified_drops_back_to_completed(tmp_path):
    ledger = _ledger(tmp_path)
    _dispatch(ledger)
    ledger.append(7, "completed", head_sha="a" * 40, branch="team/7-anthropic")
    ledger.append(7, "validated", head_sha="a" * 40, checks_ref="checks:a")
    ledger.append(7, "verified", head_sha="a" * 40, review_ref="c1", reviewer="codex", reviewer_family="openai")
    ledger.append(7, "completed", head_sha="d" * 40, branch="team/7-anthropic")
    assert ledger.main_state(7)["state"] == "completed"
    with pytest.raises(TransitionError) as exc:
        ledger.append(7, "accepted", head_sha="d" * 40, merge_sha="m" * 40)
    assert exc.value.code == "out_of_order"


def test_already_dispatched_blocks_a_second_dispatch(tmp_path):
    ledger = _ledger(tmp_path)
    _dispatch(ledger)
    with pytest.raises(TransitionError) as exc:
        ledger.append(7, "dispatched", brief_sha256="b" * 64, worker="claude", family="anthropic", branch="team/7-anthropic")
    assert exc.value.code == "already_dispatched"


def test_takeover_requires_expired_lease_and_absence_proof_and_owner_authorization(tmp_path):
    ledger = _ledger(tmp_path)
    _dispatch(ledger)
    proof = {"no_process": True, "no_session": True, "no_new_commits": True}
    with pytest.raises(TransitionError) as exc:
        ledger.append(7, "takeover", lease_expired_at="t", absence_proof=proof, owner_authorization="نفّذ")
    assert exc.value.code == "lease_not_expired"
    ledger.append(7, "expired", last_activity_at="2026-10-05T00:00:00+00:00")
    with pytest.raises(TransitionError) as exc:
        ledger.append(7, "takeover", lease_expired_at="t", absence_proof={**proof, "no_process": False}, owner_authorization="نفّذ")
    assert exc.value.code == "absence_not_proven"
    with pytest.raises(TransitionError) as exc:
        ledger.append(7, "takeover", lease_expired_at="t", absence_proof=proof, owner_authorization=" ")
    assert exc.value.code == "owner_authorization_missing"
    ledger.append(7, "takeover", lease_expired_at="t", absence_proof=proof, owner_authorization="نفّذ")
    ledger.append(7, "dispatched", brief_sha256="b" * 64, worker="claude", family="anthropic", branch="team/7-anthropic")
    assert ledger.attempt_of(7) == 2


def test_truncated_ledger_is_detected_on_open(tmp_path):
    ledger = _ledger(tmp_path)
    _dispatch(ledger)
    path = tmp_path / "dispatch.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(lines[:-1]), encoding="utf-8")
    with pytest.raises(LedgerCorrupt):
        TeamLedger(path)


def test_lease_expiry_is_twenty_four_hours():
    assert lease_expired("2026-10-05T09:00:00+00:00", "2026-10-06T10:00:00+00:00")
    assert not lease_expired("2026-10-06T09:00:00+00:00", "2026-10-06T10:00:00+00:00")


def test_append_waits_for_the_ledger_lock(tmp_path):
    """حاملٌ خارجيّ للقفل يوقف الإلحاق حتى يُفرج عنه؛ فالفحصُ والإلحاقُ والمرساةُ لا تتداخل بين أمرين (ملاحظة Codex السابعة على #344)."""
    import threading

    from core import filelock

    ledger = _ledger(tmp_path)
    holder = ledger.lock_path.open("a", encoding="utf-8")
    filelock.lock(holder)
    done = threading.Event()

    def write():
        ledger.append(7, "dispatched", brief_sha256="b" * 64, worker="claude", family="anthropic", branch="team/7-anthropic")
        done.set()

    thread = threading.Thread(target=write, daemon=True)
    thread.start()
    try:
        assert not done.wait(0.4), "الإلحاقُ مضى والقفلُ محجوز"
        assert ledger.records() == []
    finally:
        filelock.unlock(holder)
        holder.close()
    assert done.wait(5)
    thread.join(5)
    assert ledger.last(7)["state"] == "dispatched"


def test_concurrent_appends_from_separate_processes_keep_one_chain(tmp_path):
    """ستُّ عملياتٍ تلحق في السجلّ نفسِه معًا لمسائل مختلفة: السلسلةُ تصمد والأرقامُ التسلسلية لا تتكرّر."""
    import subprocess
    import sys

    path = tmp_path / "dispatch.jsonl"
    TeamLedger(path)
    script = (
        "import sys; from team.ledger import TeamLedger\n"
        "issue = int(sys.argv[2]); ledger = TeamLedger(sys.argv[1])\n"
        "for k in range(12):\n"
        "    ledger.append(issue, 'refused', code=f'c{k}')\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script, str(path), str(100 + i)], cwd=str(Path(__file__).resolve().parents[1]))
             for i in range(6)]
    assert [proc.wait(60) for proc in procs] == [0] * 6
    reopened = TeamLedger(path)                                   # verify_chain(strict=True) عند الفتح
    entries = reopened.ledger.entries()
    assert len(entries) == 72 and [e["seq"] for e in entries] == list(range(72))


def test_opening_waits_for_the_ledger_lock(tmp_path):
    """فتحُ السجلّ (وفيه التحقق من السلسلة والمرساة) ينتظر القفلَ أيضًا؛ وإلا قرأ قيدًا كُتب قبل مرساته فحسبه عبثًا."""
    import threading

    from core import filelock

    path = tmp_path / "dispatch.jsonl"
    first = TeamLedger(path)
    first.append(7, "refused", code="x")
    holder = first.lock_path.open("a", encoding="utf-8")
    filelock.lock(holder)
    opened = threading.Event()
    thread = threading.Thread(target=lambda: (TeamLedger(path), opened.set()), daemon=True)
    thread.start()
    try:
        assert not opened.wait(0.4), "الفتحُ مضى والقفلُ محجوز"
    finally:
        filelock.unlock(holder)
        holder.close()
    assert opened.wait(5)
    thread.join(5)


def _taken_over(ledger, issue=7):
    ledger.append(issue, "expired", last_activity_at="2026-10-06T10:00:00+00:00")
    ledger.append(issue, "takeover", lease_expired_at="2026-10-07T11:00:00+00:00", owner_authorization="نفّذ",
                  absence_proof={"no_process": True, "no_session": True, "no_new_commits": True})


def test_a_taken_over_attempt_accepts_no_further_main_state(tmp_path):
    """بعد الاستحواذ لا تُقيَّد حالةٌ رئيسة على المحاولة القديمة (كان `claimed` أو `validated` يمرّان عليها)."""
    ledger = _ledger(tmp_path)
    _dispatch(ledger)
    _taken_over(ledger)
    with pytest.raises(TransitionError) as exc:
        ledger.append(7, "completed", head_sha="a" * 40, branch="team/7-anthropic")
    assert exc.value.code == "attempt_taken_over"
    ledger.append(7, "dispatched", brief_sha256="c" * 64, worker="claude", family="anthropic", branch="team/7-anthropic-a2")
    assert ledger.main_state(7)["attempt"] == 2                  # المحاولةُ الجديدة تمضي


def test_accepted_is_refused_after_a_later_rejection_on_the_same_head(tmp_path):
    """القبولُ يُفحص في السجلّ تحت القفل: رفضٌ أحدثُ من verified على الرأس نفسِه يمنع accepted ولو مرّ فحصُ المرسِل قبله."""
    ledger = _ledger(tmp_path)
    _dispatch(ledger)
    head = "a" * 40
    ledger.append(7, "completed", head_sha=head, branch="team/7-anthropic")
    ledger.append(7, "validated", head_sha=head, checks_ref="checks:a")
    ledger.append(7, "verified", head_sha=head, review_ref="c-1", reviewer="codex", reviewer_family="openai")
    ledger.append(7, "review_rejected", head_sha=head, review_ref="c-2", reviewer="codex", reviewer_family="openai", verdict="reject")
    with pytest.raises(TransitionError) as exc:
        ledger.append(7, "accepted", head_sha=head, merge_sha="m" * 40)
    assert exc.value.code == "review_rejected_after_verified"


def test_accepted_is_refused_after_a_later_uncalibrated_pass_under_lock(tmp_path):
    ledger = _ledger(tmp_path)
    _dispatch(ledger)
    head = "a" * 40
    ledger.append(7, "completed", head_sha=head, branch="team/7-anthropic")
    ledger.append(7, "validated", head_sha=head, checks_ref="checks:a")
    ledger.append(7, "verified", head_sha=head, review_ref="c-1", reviewer="codex", reviewer_family="openai")
    ledger.append(7, "review_uncalibrated", head_sha=head, reviewer="codex", verdict="pass")
    with pytest.raises(TransitionError, match="review_uncalibrated_after_verified"):
        ledger.append(7, "accepted", head_sha=head, merge_sha="m" * 40)
    assert ledger.main_state(7)["state"] == "verified"
    # A later newly verified review on the same head supersedes the uncalibrated event.
    ledger.append(7, "completed", head_sha=head, branch="team/7-anthropic")
    ledger.append(7, "validated", head_sha=head, checks_ref="checks:a")
    ledger.append(7, "verified", head_sha=head, review_ref="c-3", reviewer="codex", reviewer_family="openai")
    ledger.append(7, "accepted", head_sha=head, merge_sha="m" * 40)
    assert ledger.main_state(7)["state"] == "accepted"
