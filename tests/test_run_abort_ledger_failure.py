"""انقطاعٌ غير مصنَّف ثم تعذُّرُ قيده: المالُ يُسوّى بالمحجوز، وكان القيدُ الضائع يُبتلع بلا أثر (مسحُ الإخفاقات الصامتة،
ق٦٧-٦). صار يُعلَّق على الاستثناء الأصلي باسمه ويُكتب للمشغّل، والأصليُّ يبقى هو المرفوع."""
from __future__ import annotations

import io
import sys

import pytest

from core.budget import Budget
from core.contracts import Message, Request
from core.ledger import Ledger
from core.run import execute

NOTE = "ledger_append_failed_after_abort:OSError"


class Provider:
    name = "fixture"
    is_local = True

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        raise RuntimeError("boom")


class FailingLedger(Ledger):
    def append(self, body):
        if body.get("kind") == "error":
            raise OSError("disk full")
        return super().append(body)


def _request():
    return Request((Message("user", "request"),), "fixture", "v1", 10, 10, "local_only", "fixed-key")


def test_a_lost_abort_record_is_noted_on_the_original_exception_and_printed(tmp_path, capsys):
    with pytest.raises(RuntimeError) as raised:
        execute(_request(), Provider(), Budget(0, 0), FailingLedger(tmp_path / "calls.jsonl"))
    assert str(raised.value) == "boom", "الاستثناءُ الأصلي هو المرفوع لا خطأُ القيد"
    assert NOTE in getattr(raised.value, "__notes__", []), "القيدُ الضائع لم يُسمَّ على الأصلي"
    assert NOTE in capsys.readouterr().err


def test_a_written_abort_record_carries_no_note(tmp_path, capsys):
    ledger = Ledger(tmp_path / "calls.jsonl")
    with pytest.raises(RuntimeError) as raised:
        execute(_request(), Provider(), Budget(0, 0), ledger)
    assert not getattr(raised.value, "__notes__", []) and NOTE not in capsys.readouterr().err
    assert [e["record"]["kind"] for e in ledger.entries()] == ["error"]


def test_a_closed_stderr_does_not_replace_the_original_exception(tmp_path, monkeypatch):
    """ملاحظةُ Codex على #158: حين يتعذّر القيدُ ويتعذّر الإبلاغُ معًا (stderr مغلقٌ في عمليةٍ مضمَّنة) كان `print` يرفع من
    `finally` فيستبدل الأصليَّ الذي يحمل الملاحظة؛ الإبلاغُ جهدٌ لا ضمان."""
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stderr", closed)
    with pytest.raises(RuntimeError) as raised:
        execute(_request(), Provider(), Budget(0, 0), FailingLedger(tmp_path / "calls.jsonl"))
    assert str(raised.value) == "boom" and NOTE in getattr(raised.value, "__notes__", [])
