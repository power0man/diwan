"""أرقامُ `docs/probe/project-status.json` بحدودها، كسائر أرقام docs/probe (دليلُ المراجعة §٢).

المولِّدُ نفسُه (`tools/check_docs.py::derive`) يضع الحدود، فلا يُكتفى بتعديل الملف المنشور؛
و`check_docs --check` يطابق الملفَّ بما يولّده.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import check_docs  # noqa: E402


def test_the_generated_status_carries_its_measurement_limits():
    state, _, _ = check_docs.derive(ROOT, 0)
    limits = state["measurement_limits"]
    assert limits and all(isinstance(item, str) and item for item in limits)
    published = json.loads((ROOT / "docs" / "probe" / "project-status.json").read_text(encoding="utf-8"))
    assert published["measurement_limits"] == limits
