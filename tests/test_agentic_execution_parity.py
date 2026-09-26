"""أدواتُ التنفيذ في المُشغِّل الوكيل كما في الواجهة (ج٨، #117).

في أول قياسٍ حيّ لبنك المحلّل أعلن المُشغِّلُ `run_command` و`run_tests` ولم يضبط لهما منفذًا، وإيصالُ Docker
لا يخدم إلا أمرَ النجاح. فرُدّ نداؤهما بـ`execution_backend_unavailable`، وخرجت سبعُ مهامّ من المقام.
والواجهةُ لا تعلنهما إلا حيث تضبط منفذَ حاويةٍ على مساحة الوكيل (`webui/server.py::agent_registry`). فهنا:

- أمرُ القياس يختار الأدوات بقاعدة الواجهة نفسِها.
- إيصالُ Docker يضبط منفذًا على مساحة كل مهمّة، بلقطة المعيار نفسِها، ويُحرَّر بعدها.
- والإعدادُ يسجّل منفذَ الوكيل، وما أُعلن من أدوات التنفيذ بلا منفذ.
"""
from __future__ import annotations

import pytest

from core import execution
from core.contracts import ToolCall
from evaluation.agentic_runner import DockerSuccessExecutor, run_agentic_suite, run_task
from tests.test_agentic_harness_guard import FIX_TASK, Scripted, registry, says  # noqa: F401
from tests.test_agentic_success_in_docker import RECEIPT, SUITE, daemon, receipt  # noqa: F401
from tools.evaluate_agentic import select_tools

EXECUTION = {"run_command", "run_tests"}


class Recording(Scripted):
    def __init__(self, *responses):
        super().__init__(*responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return super().complete(request)


def runs_its_tests_then_stops():
    return Recording(says("أشغّل الاختبارات.", ToolCall("t", "run_tests", {"paths": ["tests/"]})),
                     says("انتهيت."))


def _names(tools):
    return {tool.spec.name for tool in tools}


@pytest.mark.parametrize("mode, analysis", [("agent", False), ("agent", True), ("coder", False)])
def test_execution_tools_are_announced_only_where_a_receipt_gives_them_a_backend(mode, analysis):
    assert not _names(select_tools(mode, execution=False, analysis=analysis)) & EXECUTION
    assert _names(select_tools(mode, execution=True, analysis=analysis)) >= EXECUTION
    assert ("analyze_data" in _names(select_tools(mode, execution=False, analysis=analysis))) is analysis


def test_with_a_receipt_the_agent_runs_its_tests_in_the_container_and_the_backend_is_released(receipt, daemon,
                                                                                                registry):
    provider = runs_its_tests_then_stops()
    result = run_task(FIX_TASK, provider, registry, model="fixture", model_version="v1", host=None,
                      success_executor=DockerSuccessExecutor(receipt), execution_receipt=receipt)
    assert result["loop_status"] == "complete" and result["code"] != "execution_backend_unavailable", result
    tool_turn = str(provider.requests[1].messages[-1])
    assert "ok" in tool_turn and "execution_backend_unavailable" not in tool_turn
    assert not execution._BACKENDS, "منفذُ المهمّة لا يبقى بعدها"


def test_without_a_receipt_the_announced_tool_is_refused_as_in_the_first_live_run(registry):
    result = run_task(FIX_TASK, runs_its_tests_then_stops(), registry, model="fixture", model_version="v1",
                      host="pytest-attested-host")
    assert (result["status"], result["code"]) == ("error", "execution_backend_unavailable")


def test_a_suite_with_a_receipt_gives_every_task_its_executor_and_says_so(receipt, daemon, registry):
    report = run_agentic_suite(SUITE, runs_its_tests_then_stops(), registry, model="m", model_version="v",
                               execution_receipt=receipt)
    [row], config = report["results"], report["config"]
    assert row["loop_status"] == "complete" and row["code"] != "execution_backend_unavailable", row
    assert (config["agent_execution"], config["execution_tools_without_backend"]) == ("docker", [])
    assert config["runner_version"] == 7 and not execution._BACKENDS


def test_the_report_names_execution_tools_announced_without_a_backend(registry, monkeypatch):
    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", "pytest-attested-host")
    config = run_agentic_suite(SUITE, Scripted(says("لم أفعل شيئًا.")), registry, model="m",
                               model_version="v")["config"]
    assert config["agent_execution"] is None and config["execution_tools_without_backend"] == sorted(EXECUTION)
