"""#285: التغليفُ يحترم .gitignore، فملفٌّ متجاهَلٌ في git داخل مجلّدٍ مصدريّ لا يدخل العجلة ولا الحزمة المصدرية.

مع `ignore-vcs = true` كان ملفٌّ يطابق قائمةَ السماح (`*.py` تحت `core/` مثلًا) يدخل العجلة ولو تجاهله git، فمخازنُ المالك
الموضوعة في مواضعَ متجاهَلة (ك٢٩) تُغلَّف إن طابقت. وقد بُنيت العجلةُ بـhatchling 1.27.0 بالإعدادين: لا فرقَ إلا الملفَّ
المتجاهَل، فلا يسقط مطلوب.
"""
from __future__ import annotations

from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_the_build_honours_gitignore():
    build = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"]["build"]
    assert build.get("ignore-vcs", False) is False
    for target in build.get("targets", {}).values():
        assert target.get("ignore-vcs", False) is False
