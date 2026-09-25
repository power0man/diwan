"""استبدالُ ملفٍّ ذرّيًّا أثناء قراءته تغيُّرٌ يُعاد بعده القراءة، لا مسارٌ غيرُ آمن.

كشفه CI في ٢٥ سبتمبر ٢٠٢٦ (`test_two_stop_threads_do_not_reset_running_session_descriptor`): خيطان
يطلبان إيقافَ الجولة معًا، فيستبدل الثاني علامةَ الإيقاف بـ`os.replace` بينما الأول يقرؤها؛ فتصير
وصلاتُ المعرّف المفتوح صفرًا ويحكم `_regular(after)` بأن الملف «غيرُ آمن» (`unsafe_path`) فيسقط
الإيقافُ كلُّه. والاستبدالُ الذرّي هو ما يفعله كلُّ كاتبٍ مصرَّحٍ له في هذه الأدلّة عمدًا، فحقُّه
`file_changed` الذي تعيد بعده `_value` القراءةَ مرّاتٍ محدودة. الحارسُ يصنع السباقَ حتميًّا: يستبدل
الملفَّ بين قراءتَي الهوية.

ثم كشف CI نافذةً ثانية للسباق نفسِه في طلب الدمج ١٤: الاستبدالُ بين `os.open` وأول `fstat`، فتكون
وصلاتُ `before` صفرًا ويسقط `_regular(before)` بـ`unsafe_path`. والحارسُ الثاني يصنعها حتميًّا.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import journal  # noqa: E402


def _racing_fstat(replace_once):
    real = os.fstat
    state = {"done": False}

    def fstat(fd):
        info = real(fd)
        if not state["done"]:
            state["done"] = True
            replace_once()                       # بين `before` و`after`: كاتبٌ آخر يستبدل الملف
        return info
    return fstat


def test_a_file_replaced_atomically_during_the_read_is_file_changed_not_unsafe(tmp_path, monkeypatch):
    target, fresh = tmp_path / "stop.json", tmp_path / "stop-write.tmp"
    target.write_bytes(b'{"old": 1}')
    fresh.write_bytes(b'{"new": 2}')
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        monkeypatch.setattr(journal.os, "fstat", _racing_fstat(lambda: os.replace(fresh, target)))
        with pytest.raises(journal.JournalRefused) as refused:
            journal._read(parent, "stop.json", 1 << 20)
        assert refused.value.code == "file_changed", refused.value.code
    finally:
        os.close(parent)


def _fstat_after_replacing(replace_once):
    real = os.fstat
    state = {"done": False}

    def fstat(fd):
        if not state["done"]:
            state["done"] = True
            replace_once()                       # بين `os.open` و`before`: الاسمُ صار لملفٍّ آخر
        return real(fd)
    return fstat


def test_a_file_replaced_between_open_and_the_first_fstat_is_file_changed_not_unsafe(tmp_path, monkeypatch):
    target, fresh = tmp_path / "stop.json", tmp_path / "stop-write.tmp"
    target.write_bytes(b'{"old": 1}')
    fresh.write_bytes(b'{"new": 2}')
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        monkeypatch.setattr(journal.os, "fstat", _fstat_after_replacing(lambda: os.replace(fresh, target)))
        with pytest.raises(journal.JournalRefused) as refused:
            journal._read(parent, "stop.json", 1 << 20)
        assert refused.value.code == "file_changed", refused.value.code
    finally:
        os.close(parent)


def test_an_unreplaced_file_still_reads_and_a_hard_linked_one_is_still_unsafe(tmp_path):
    target = tmp_path / "state.json"
    target.write_bytes(b"x")
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert journal._read(parent, "state.json", 1 << 20)[0] == b"x"
        os.link(target, tmp_path / "twin.json")
        with pytest.raises(journal.JournalRefused) as refused:
            journal._read(parent, "state.json", 1 << 20)
        assert refused.value.code == "unsafe_path"
    finally:
        os.close(parent)
