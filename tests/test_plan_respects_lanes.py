"""الخطةُ الحاكمة لا تُسند إلى Claude عقدًا في مسار openai (ق٦٦).

ق٦٦ سحب احتياطَ Claude في مسار openai: لا يعدّل ملفّاته ولو بسطر «تسليم:». فمهمّةٌ منفّذُها Claude (السحابيّ أو
الماك أو Nitro) لا يسمّي مُخرجُها ولا دليلُ قبولها ملفًّا من ذلك المسار، وإلا لم يُنجزها إلا بمخالفة القرار
أو بانتظار مهمّةٍ تابعةٍ لها (ملاحظات Codex على #141). والمسارُ يُقرأ من `registry/lanes.json` لا من قائمةٍ هنا.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
PLAN = json.loads((ROOT / "docs" / "PLAN-20260926.json").read_text(encoding="utf-8"))
OPENAI = json.loads((ROOT / "registry" / "lanes.json").read_text(encoding="utf-8"))["lanes"]["openai"]


def _openai_paths(text: str) -> list[str]:
    """مساراتُ openai المسمّاة في النصّ، مسارًا كاملًا لا جزءًا من اسمٍ آخر (فـ`webui/` لا تطابق `my_webui/`)، والمجلّدُ
    بشرطته (فـ`--analysis-receipt` ليس `analysis/`). والحدُّ قبله محرفٌ لاتينيّ لا عربيّ، فواوُ العطف الملتصقة
    («وwebui/») لا تُخفيه."""
    return [path for path in OPENAI
            if re.search(r"(?<![A-Za-z0-9_/.-])" + re.escape(path) + ("" if path.endswith("/") else r"(?![A-Za-z0-9_])"),
                         text)]


def test_the_matcher_names_whole_paths_only():
    assert _openai_paths("يعدّل webui/server.py وconversation/session.py") == ["conversation/", "webui/"]
    assert _openai_paths("core/execution.py") == ["core/execution.py"]
    assert _openai_paths("my_webui/x.py وtools/probe_execution_boundary.py وcore/executions.py") == []
    assert _openai_paths("README يوثّق --analysis-receipt") == [] and _openai_paths("analysis/report.py") == ["analysis/"]


def test_no_claude_task_contract_names_an_openai_lane_file():
    claude = [task for task in PLAN["tasks"] if task["assignee"].startswith("claude")]
    assert claude, "لا مهمّة لـClaude في الخطة؟"
    offending = {task["id"]: _openai_paths(task.get("deliverable", "") + " " + task.get("acceptance_evidence", ""))
                 for task in claude}
    assert {k: v for k, v in offending.items() if v} == {}
