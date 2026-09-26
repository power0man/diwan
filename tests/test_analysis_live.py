"""ج٨ (#44): المحلّلُ على صورةٍ حقيقية بإيصالها — حلولٌ من فئات بنك ك٥٠ كلِّها، ويحكم عليها البنكُ نفسُه.

يلزمه `DIWAN_ANALYSIS_RECEIPT` (إيصالٌ من `analysis/prepare.py`) وDocker، ويُتخطّى بالاسم بدونهما.
و`DIWAN_REQUIRE_ANALYSIS=1` يجعل الغيابَ سقوطًا (مهمّةُ `analysis` في CI).

**ما يثبته:** أن الأداة تُعبّر عن حلول البنك. فكودٌ صحيح بـpandas يمرّ بالأداة كما يمرّ بها كودُ النموذج،
ثم يحكم عليه أمرُ نجاح المهمّة من البنك المجمَّد. ولا يُكتب في المساحة إلا ما أُعلن، ولا يُمسّ ملفُّ بيانات.
**وما لا يثبته:** أن نموذجًا يكتب هذا الكود. فالعتبةُ (٠٫٧٠) تُقاس حيًّا بمحرّكٍ عبر `tools/evaluate_agentic.py`.
وفيه كذلك أن الحاوية بلا شبكة، وبمستخدمٍ غير جذر، ونظامُ ملفّاتها للقراءة، ولا ترى إلا المدخلات المسمّاة.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from agent.actions import ActionStore
from agent.journal import Journal
from agent.registry import ToolContext, ToolRegistry
from analysis import backend as analysis
from analysis.tool import ANALYZE_DATA
from core.contracts import ToolCall
from evaluation.agentic_runner import forbidden_touches, materialize

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = os.environ.get("DIWAN_ANALYSIS_RECEIPT")
DOCKER = os.environ.get("DIWAN_DOCKER") or shutil.which("docker")
REQUIRED = os.environ.get("DIWAN_REQUIRE_ANALYSIS") == "1"
if not (RECEIPT and DOCKER and Path(RECEIPT).is_file()):
    if REQUIRED:
        raise RuntimeError("DIWAN_REQUIRE_ANALYSIS=1 بلا إيصال صورة المحلّل أو بلا Docker")
    pytest.skip("لا إيصالَ لصورة المحلّل (DIWAN_ANALYSIS_RECEIPT) أو لا Docker", allow_module_level=True)

TASKS = {t["task_id"]: t for t in json.loads((ROOT / "evaluation/suites/analyst_v1.json").read_text(encoding="utf-8"))["tasks"]}

READ = "import json, pandas as pd\n"
REVENUE = "df = pd.read_csv('sales.csv')\ndf['الإيراد'] = df['الكمية'] * df['السعر']\n"
SOLUTIONS = {
    "an01": (["answer.json"], READ + REVENUE +
             "json.dump({'إجمالي_الإيرادات': round(float(df['الإيراد'].sum()), 2)}, "
             "open('answer.json', 'w', encoding='utf-8'), ensure_ascii=False)\n"),
    "an06": (["result.csv"], READ + "df = pd.read_csv('sales.csv')\n"
             "g = df.groupby('الفرع', as_index=False)['الكمية'].sum().sort_values('الكمية', ascending=False)\n"
             "g.to_csv('result.csv', index=False)\n"),
    "an11": (["answer.json"], READ + "df = pd.read_csv('sales.csv')\n"
             "n = int(((df['الفرع'] == 'الرياض') & (df['الكمية'] >= 10)).sum())\n"
             "json.dump({'العدد': n}, open('answer.json', 'w', encoding='utf-8'), ensure_ascii=False)\n"),
    "an15": (["result.csv"], READ + REVENUE +
             "g = df.groupby('المنتج')['الإيراد'].sum().round(2).sort_values(ascending=False).head(3)\n"
             "out = pd.DataFrame({'الترتيب': range(1, len(g) + 1), 'المنتج': g.index, 'الإيراد': g.values})\n"
             "out.to_csv('result.csv', index=False)\n"),
    "an19": (["result.csv"], READ + REVENUE +
             "df['الشهر'] = pd.to_datetime(df['التاريخ']).dt.strftime('%Y-%m')\n"
             "g = df.groupby('الشهر', as_index=False)['الإيراد'].sum().sort_values('الشهر')\n"
             "g['الإيراد'] = g['الإيراد'].round(2)\n"
             "g.to_csv('result.csv', index=False)\n"),
    "an24": (["result.csv"], READ + "orders, customers = pd.read_csv('orders.csv'), pd.read_csv('customers.csv')\n"
             "m = orders.merge(customers, on='رقم_العميل', how='inner')\n"
             "g = m.groupby('المدينة', as_index=False)['المبلغ'].sum().rename(columns={'المبلغ': 'الإجمالي'})\n"
             "g['الإجمالي'] = g['الإجمالي'].round(2)\n"
             "g.sort_values('الإجمالي', ascending=False).to_csv('result.csv', index=False)\n"),
    "an28": (["answer.json"], READ + "df = pd.read_csv('amounts.csv', dtype=str)\n"
             "digits = str.maketrans('٠١٢٣٤٥٦٧٨٩٫٬', '0123456789.,')\n"
             "total = sum(float(v.translate(digits).replace(',', '')) for v in df['المبلغ'])\n"
             "value = int(total) if total == int(total) else round(total, 2)\n"
             "json.dump({'المجموع': value}, open('answer.json', 'w', encoding='utf-8'), ensure_ascii=False)\n"),
    "an33": (["result.csv"], READ + "df = pd.read_excel('inventory.xlsx', sheet_name='المخزون')\n"
             "df[df['الكمية'] < df['حد_الطلب']][['الصنف', 'الكمية', 'حد_الطلب']].to_csv('result.csv', index=False)\n"),
    "an35": (["report.xlsx"], READ + REVENUE + "from openpyxl import Workbook\n"
             "g = df.groupby('الفرع')['الإيراد'].sum().round(2).sort_values(ascending=False)\n"
             "wb = Workbook(); ws = wb.active; ws.title = 'الفروع'; ws.sheet_view.rightToLeft = True\n"
             "ws.append(['الفرع', 'الإيراد'])\n"
             "for branch, value in g.items(): ws.append([branch, float(value)])\n"
             "wb.save('report.xlsx')\n"),
    "an36": (["result.csv"], READ + "prices = pd.read_excel('catalog.xlsx', sheet_name='الأسعار')\n"
             "qty = pd.read_excel('catalog.xlsx', sheet_name='الكميات')\n"
             "m = qty.merge(prices, on='الصنف')\n"
             "m['القيمة'] = (m['الكمية'] * m['السعر']).round(2)\n"
             "m.sort_values('الصنف')[['الصنف', 'الكمية', 'السعر', 'القيمة']].to_csv('result.csv', index=False)\n"),
    "an37": (["chart_data.csv", "chart.png"], READ + REVENUE + "import matplotlib.pyplot as plt\nfrom diwan_ar import ar\n"
             "g = df.groupby('الفرع')['الإيراد'].sum().round(2).sort_values(ascending=False)\n"
             "pd.DataFrame({'الفرع': g.index, 'الإيراد': g.values}).to_csv('chart_data.csv', index=False)\n"
             "fig, ax = plt.subplots(figsize=(6, 4), dpi=100)\n"
             "ax.bar([ar(b) for b in g.index], g.values); ax.set_title(ar('الإيراد حسب الفرع'))\n"
             "fig.savefig('chart.png')\n"),
}


def _run(tmp_path, task, code, outputs, inputs=None):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    materialize(task, workspace)
    analysis.configure_analysis_backend(Path(RECEIPT), workspace, docker_executable=DOCKER)
    try:
        store = ActionStore(tmp_path / "control", workspace)
        context = ToolContext(workspace, Journal(workspace), frozenset({"auto", "logged"}))
        registry = ToolRegistry(ANALYZE_DATA)
        call = ToolCall("call-1", "analyze_data", {"code": code, "outputs": outputs,
                                                   "inputs": sorted(task["workspace"]) if inputs is None else inputs})
        position = dict(session_id="s", turn_id="t", step_index=0)
        store.register_step(**position, request_digest="a" * 64, calls=(call,), specs=registry.specs())
        result = registry.invoke_prepared(call, context, store=store, **position, call_index=0,
                                          request_digest="a" * 64)
        return workspace, context, result
    finally:
        analysis.release_analysis_backend(workspace)


def _judged(task, workspace) -> subprocess.CompletedProcess:
    command = task["success"]["command"]
    assert command[0] == "python3"
    return subprocess.run([sys.executable, *command[1:]], cwd=workspace, capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("task_id", sorted(SOLUTIONS))
def test_a_correct_solution_through_the_tool_passes_the_banks_own_check(tmp_path, task_id):
    task = TASKS[task_id]
    outputs, code = SOLUTIONS[task_id]
    workspace, context, result = _run(tmp_path, task, code, outputs)
    assert result["status"] == "ok", result["content"]
    assert forbidden_touches(task, context.journal) == []
    assert sorted(a.path for a in context.journal.actions()) == sorted(outputs)
    judged = _judged(task, workspace)
    assert judged.returncode == 0, judged.stdout + judged.stderr


def test_every_bank_category_has_a_solution_here():
    assert {TASKS[t]["capability"] for t in SOLUTIONS} == {t["capability"] for t in TASKS.values()}


def test_the_container_has_no_network_no_root_a_readonly_image_and_only_named_inputs(tmp_path):
    task = {**TASKS["an24"]}
    code = (
        "import json, os, socket\n"
        "try:\n    socket.create_connection(('1.1.1.1', 53), timeout=3); network = True\n"
        "except OSError:\n    network = False\n"
        "try:\n    open('/opt/venv/planted', 'w'); image_writable = True\n"
        "except OSError:\n    image_writable = False\n"
        "open('undeclared.txt', 'w').write('لا يعود')\n"
        "json.dump({'uid': os.getuid(), 'network': network, 'image_writable': image_writable,\n"
        "           'seen': sorted(os.listdir('.'))}, open('probe.json', 'w'))\n")
    workspace, context, result = _run(tmp_path, task, code, ["probe.json"], inputs=["orders.csv"])
    assert result["status"] == "ok", result["content"]
    probe = json.loads((workspace / "probe.json").read_text())
    assert probe["uid"] == 1000 and probe["network"] is False and probe["image_writable"] is False
    assert probe["seen"] == ["orders.csv", "undeclared.txt"]     # قبل probe.json؛ ولا customers.csv
    assert not (workspace / "undeclared.txt").exists()


def test_a_failing_script_writes_nothing_and_returns_its_traceback(tmp_path):
    task = TASKS["an01"]
    workspace, context, result = _run(tmp_path, task, "import pandas as pd\npd.read_csv('sales.csv')['عمود_غائب']\n",
                                      ["answer.json"])
    assert result["status"] == "refused" and result["code"] == "analysis_script_failed"
    assert "KeyError" in result["content"] and context.journal.actions() == []
    assert not (workspace / "answer.json").exists()
