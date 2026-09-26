"""أمرُ النجاح يجري في حاويةٍ زائلة على لقطةٍ من المساحة بعد عمل الوكيل، لا على الجهاز (ج٣).

الطريقُ القديم كان `subprocess` على المضيف: شيفرةٌ كتبها النموذج تُنفَّذ على جهاز
المالك بلا حدٍّ إلا إقرارٌ في متغيّر بيئة. وهنا: كلُّ مهمّةٍ تُرسَل إلى خُلفيّة
Docker على جذرها وحده، والمضيفُ لا يُنادى، ورفضُ الحاوية لا يُعاد على المضيف.

ولا حاويةَ حيّةً في هذه الاختبارات — محرّكُ Docker مصطنعٌ
(`tests.test_execution_boundary.FakeDocker`) فيُثبَت العقدُ: ما يُرسَل، وأين،
وما يُفعل بجواب المحرّك. أما الحدُّ الحيّ فتشغيلةٌ مستقلّة على الماك بإيصالٍ معتمَد.
"""
from __future__ import annotations

import base64
import json

import pytest

from core import execution
from core.canonical import PayloadRejected
from core.contracts import ToolCall
from evaluation import agentic_runner
from evaluation.agentic_runner import (DockerSuccessExecutor, run_agentic_suite, run_task,
                                       workspace_snapshot)
from tests.test_agentic_harness_guard import CONFTEST_EXPLOIT, FIX_TASK, Scripted, registry, says
from tests.test_execution_boundary import FakeDocker

FIXED = "def area(w, h):\n    return w * h\n"
RECEIPT = {"schema_version": 1, "image_id": "sha256:" + "a" * 64,
           "lock_sha256": "b" * 64, "python_version": "3.14.7"}
SUITE = {"schema_version": 1, "suite_id": "probe", "kind": "agentic_tasks",
         "description": "بنكٌ صوريّ داخل اختبار", "tasks": [FIX_TASK]}


@pytest.fixture
def receipt(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(RECEIPT), encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture
def daemon(monkeypatch):
    """محرّكُ Docker مصطنع لكلّ خُلفيّةٍ تُنشأ، وأيُّ عمليةٍ على المضيف تُسقط الاختبار."""
    settings = {"exit_code": 0, "stdout": b"ok\n", "stderr": b"", "start_error": None,
                "instances": []}
    fakes = {}

    def _docker(self, *args, data=b"", timeout=20):
        fake = fakes.get(id(self))
        if fake is None:
            fake = fakes[id(self)] = FakeDocker(self)
            fake.exit_code, fake.stdout = settings["exit_code"], settings["stdout"]
            fake.stderr, fake.start_error = settings["stderr"], settings["start_error"]
            settings["instances"].append(fake)
        return fake(*args, data=data, timeout=timeout)
    monkeypatch.setattr(execution.DockerExecutionBackend, "_docker", _docker)

    def host_forbidden(*args, **kwargs):
        pytest.fail("أمرُ النجاح جرى على المضيف")
    monkeypatch.setattr(agentic_runner.subprocess, "run", host_forbidden)
    monkeypatch.setattr(execution.subprocess, "Popen", host_forbidden)
    return settings


def candidate_payloads(settings):
    """ما استلمته حاويةُ المرشّح على stdin: الأمرُ واللقطة."""
    payloads = []
    for fake in settings["instances"]:
        for args, data, _ in fake.calls:
            if args[0] == "start":
                payload = json.loads(data)
                if "argv" in payload:
                    payloads.append(payload)
    return payloads


def fixing_provider():
    return Scripted(says("أصلح المصدر.", ToolCall("fix", "write_file",
                                                  {"path": "src/calc.py", "content": FIXED})),
                    says("تمّ الإصلاح."))


def _run(task, provider, registry, executor):
    return run_task(task, provider, registry, model="fixture", model_version="v1",
                    host=None, success_executor=executor)


# ————— العقد: الأمرُ في الحاوية، على المساحة بعد العمل —————

def test_the_command_runs_in_the_container_on_the_post_run_workspace(receipt, daemon, registry):
    result = _run(FIX_TASK, fixing_provider(), registry, DockerSuccessExecutor(receipt))
    assert (result["status"], result["passed"]) == ("passed", True), result
    assert result["success_boundary"] == "docker:" + "c" * 64
    [payload] = candidate_payloads(daemon)
    assert payload["argv"] == FIX_TASK["success"]["command"]
    files = {item["path"]: base64.b64decode(item["data"]).decode() for item in payload["files"]}
    assert files["src/calc.py"] == FIXED, "اللقطةُ بعد عمل الوكيل لا قبله"
    assert files["tests/test_calc.py"] == FIX_TASK["workspace"]["tests/test_calc.py"]
    assert not any(part.startswith(".") for name in files for part in name.split("/")), \
        "دفترُ الرجوع وما خفي لا يبلغ الحاوية"


def test_a_nonzero_exit_from_the_container_is_a_measured_failure(receipt, daemon, registry):
    daemon["exit_code"], daemon["stderr"] = 1, b"FAILED tests/test_calc.py::test_area\n"
    result = _run(FIX_TASK, Scripted(says("لم أفعل شيئًا.")), registry, DockerSuccessExecutor(receipt))
    assert (result["status"], result["code"], result["passed"]) == ("failed", "command_nonzero", False)
    assert result["success_boundary"] == "docker:" + "c" * 64


def test_a_runner_missing_inside_the_container_is_unjudgeable_not_a_failure(receipt, daemon, registry):
    daemon["exit_code"], daemon["stderr"] = 1, b"/opt/venv/bin/python3: No module named pytest\n"
    result = _run(FIX_TASK, Scripted(says("لم أفعل شيئًا.")), registry, DockerSuccessExecutor(receipt))
    assert (result["status"], result["code"]) == ("error", "success_command_unavailable")


def test_a_container_timeout_is_named_and_never_retried_on_the_host(receipt, daemon, registry):
    daemon["start_error"] = execution.ExecutionRefused("execution_timeout", "انقضت المهلة")
    result = _run(FIX_TASK, fixing_provider(), registry, DockerSuccessExecutor(receipt))
    assert (result["status"], result["code"]) == ("failed", "success_command_timeout")


def test_a_missing_docker_engine_is_an_error_outside_the_denominator(tmp_path, receipt, registry,
                                                                     monkeypatch):
    """بلا محرّك: لا يُعاد الأمرُ على المضيف، ويخرج من المقام لا يُحسب رسوبًا."""
    def host_forbidden(*args, **kwargs):
        pytest.fail("أمرُ النجاح جرى على المضيف")
    monkeypatch.setattr(agentic_runner.subprocess, "run", host_forbidden)
    executor = DockerSuccessExecutor(receipt, docker_executable=str(tmp_path / "no-such-docker"))
    result = _run(FIX_TASK, fixing_provider(), registry, executor)
    assert (result["status"], result["code"]) == ("error", "success_command_unavailable")
    assert "execution_runtime_unavailable" in result["success_detail"]


def test_harness_tampering_is_judged_before_any_container_is_created(receipt, daemon, registry):
    provider = Scripted(says("أعبث.", ToolCall("cheat", "write_file",
                                                {"path": "conftest.py", "content": CONFTEST_EXPLOIT})),
                        says("تمّ."))
    result = _run(FIX_TASK, provider, registry, DockerSuccessExecutor(receipt))
    assert result["code"] == "harness_tampered"
    assert daemon["instances"] == [], "لا حاويةَ لمهمّةٍ سقطت قبل الحكم"


def test_the_snapshot_carries_only_visible_regular_files(tmp_path):
    root = tmp_path / "ws"
    for folder in ("src", ".diwan-journal", "__pycache__", ".pytest_cache"):
        (root / folder).mkdir(parents=True)
    (root / "src/a.py").write_text("x", encoding="utf-8")
    (root / "main.py").write_text("y", encoding="utf-8")
    (root / ".diwan-journal/0001.json").write_text("{}", encoding="utf-8")
    (root / "__pycache__/a.pyc").write_bytes(b"\0")
    (root / ".pytest_cache/v").write_text("", encoding="utf-8")
    (root / ".hidden").write_text("h", encoding="utf-8")
    (root / "link.py").symlink_to(root / "src/a.py")
    assert workspace_snapshot(root) == ("main.py", "src/a.py")


# ————— البنك كلُّه: الإيصالُ يغني عن الإقرار، والتقريرُ يقول أين جرى الأمر —————

def test_a_suite_with_a_receipt_needs_no_host_attestation_and_says_where_it_ran(receipt, daemon,
                                                                                registry, monkeypatch):
    monkeypatch.delenv("DIWAN_DISPOSABLE_HOST", raising=False)
    report = run_agentic_suite(SUITE, fixing_provider(), registry, model="m", model_version="v",
                               execution_receipt=receipt)
    config = report["config"]
    assert (config["success_executor"], config["attested_host"], config["runner_version"]) == ("docker", None, 7)
    assert config["execution_receipt"] == {key: RECEIPT[key] for key in ("image_id", "lock_sha256", "python_version")}
    limits = report["measurement_limits"]
    assert "success_command_runs_inside_the_docker_backend_on_a_snapshot_of_the_post_run_workspace" in limits
    assert "docker_backend_is_a_container_boundary_not_a_separate_host" in limits
    assert "success_command_runs_on_the_host_not_inside_the_docker_backend" not in limits
    assert report["summary"]["passed"] == 1
    assert report["results"][0]["success_boundary"] == "docker:" + "c" * 64


def test_a_broken_receipt_refuses_the_whole_suite_before_any_task(tmp_path):
    receipt = tmp_path / "bad.json"
    receipt.write_text("{}", encoding="utf-8")
    receipt.chmod(0o600)
    with pytest.raises(PayloadRejected) as exc:
        DockerSuccessExecutor(receipt)
    assert exc.value.code == "execution_receipt_invalid"


def test_without_a_receipt_the_host_path_still_demands_attestation(registry, monkeypatch):
    monkeypatch.delenv("DIWAN_DISPOSABLE_HOST", raising=False)
    with pytest.raises(PayloadRejected) as exc:
        run_agentic_suite(SUITE, Scripted(), registry, model="m", model_version="v")
    assert exc.value.code == "disposable_host_not_attested"


def test_the_host_path_names_itself_in_config_and_in_the_task_boundary(registry, monkeypatch):
    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", "ci-container")
    report = run_agentic_suite(SUITE, Scripted(says("لم أفعل شيئًا.")), registry, model="m",
                               model_version="v")
    assert (report["config"]["success_executor"], report["config"]["execution_receipt"]) == ("attested_host", None)
    assert report["results"][0]["success_boundary"] == "host:ci-container"
    assert "success_command_runs_on_the_host_not_inside_the_docker_backend" in report["measurement_limits"]
