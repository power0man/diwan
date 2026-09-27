"""ملفُّ الإزاحات الفاسد يُعاد من الصفر كما كان، لكن باسمه لا صامتًا (مسحُ الإخفاقات الصامتة)."""
from __future__ import annotations

from core.streams import Offsets


def test_corrupt_offsets_are_named_and_restart_from_zero(tmp_path, capsys):
    path = tmp_path / "offsets.json"
    path.write_text("{not json", encoding="utf-8")
    offsets = Offsets(path)
    assert offsets.get("s") == 0 and offsets.corrupt == "offsets_corrupt:JSONDecodeError"
    assert "offsets_corrupt:JSONDecodeError" in capsys.readouterr().err
    path.write_text('{"s": "abc"}', encoding="utf-8")
    assert offsets.get("s") == 0 and offsets.corrupt == "offsets_corrupt:ValueError"
    assert Offsets(tmp_path / "missing.json").corrupt is None
