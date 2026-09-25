"""علامةُ اللقطة العامة: ملفٌّ في جذر الشجرة المصدَّرة يسمّي ما استُبعد منها (ك٢٧).

المستودعُ الخاص لا يحمل هذه العلامة أبدًا (حارسُها `tests/test_export_public.py`)؛
فوجودُها يعني أن الشجرةَ لقطةٌ عامة بُنيت بـ`tools/export_public.py`. وما تسمّيه
«مستبعدًا» من السجلات الحاكمة يُعامَل غيابُه **الكامل** تخطّيًا معلَنًا لا عطبًا.
والغيابُ الكامل وحده: سجلٌّ حاضرٌ بلا مرساة يبقى عطبًا مهما قالت العلامة، فلا
تُستعمل العلامةُ لإخفاء عبث.

والعلامةُ المعطوبة أو الغريبة تُقرأ «لا علامة» — فاتجاهُ الخطأ نحو الصرامة.
"""
from __future__ import annotations

import json
from pathlib import Path

MARKER_NAME = "PUBLIC-EXPORT.json"
MARKER_KIND = "public_export"


def read_marker(root: Path) -> dict | None:
    """العلامةُ إن وُجدت وصحّ شكلُها، وإلا None."""
    path = Path(root) / MARKER_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("kind") != MARKER_KIND:
        return None
    return data


def excluded_streams(root: Path) -> frozenset[str]:
    """السجلاتُ الحاكمة التي تسمّيها العلامةُ مستبعَدةً من اللقطة؛ فارغةٌ بلا علامة."""
    marker = read_marker(root)
    if marker is None:
        return frozenset()
    streams = marker.get("excluded_streams")
    if not isinstance(streams, list):
        return frozenset()
    return frozenset(s for s in streams if isinstance(s, str))
