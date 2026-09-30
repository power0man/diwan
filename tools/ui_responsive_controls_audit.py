#!/usr/bin/env python3
"""Measure #224 in Chromium with public UI assets and a synthetic provider.

Requires node, Playwright and its Chromium on the caller's configured PATH /
NODE_PATH / PLAYWRIGHT_BROWSERS_PATH. No installs, external calls or owner data.
Usage: python tools/ui_responsive_controls_audit.py --out /tmp/layout.json
Exit 0: all measured predicates pass; 1: failure (receipt retains observations).
The ordinary pytest tests only check source contracts, not browser rendering.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

import ui_browser_audit as audit

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    audit.TEXT_ANSWER = "\n".join(["سطر جواب مصطنع يثبت بقاء النص قابلًا للقراءة."] * 30) + "\nنهاية الجواب المصطنع"
    rows, statuses = [], []
    with tempfile.TemporaryDirectory(prefix="diwan-layout-audit-") as directory:
        temp = Path(directory).resolve()
        for width in (1366, 390):
            with audit.serving(temp / str(width)) as (url, _provider):
                raw = temp / f"{width}.json"
                run = subprocess.run(["node", str(ROOT / "tests/webui_responsive_controls_browser.cjs"),
                                      url, str(raw), str(width)], cwd=ROOT, env=os.environ.copy(), check=False)
                statuses.append(run.returncode)
                rows.append(json.loads(raw.read_text()))
    result = {
        "scope": "synthetic_provider_chromium_geometry_and_native_checkbox_only",
        "sources": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                    for name in (*audit.UI_SOURCES, "tools/ui_responsive_controls_audit.py",
                                 "tests/webui_responsive_controls_browser.cjs")},
        "limits": ["headless_chromium_only", "no_model_quality_or_owner_acceptance",
                   "last_answer_line_is_scrolled_to_viewport_bottom_before_hit_testing",
                   "keyboard_checkbox_test_is_not_a_screen_reader_test",
                   "axe_and_focus_dialogs_are_checked_separately_by_ui_browser_audit"],
        "passed": all(status == 0 for status in statuses), "viewports": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
