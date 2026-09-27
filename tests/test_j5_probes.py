"""مجسّا ج٥ لا يشهدان بما لم يقع (ملاحظات Codex على #136).

- مجسُّ الحدّ لا يخرج بـ0 إلا إن انتهت كلُّ حالةٍ إلى ما يجب وتحقّق التنظيف، ولا يُسمّي حدَّ الصندوق «مراجَعًا» إن لم تؤدِّ
  البرامجُ الثلاثة ما يثبته؛ ويرفض مسارَ Docker غيرَ الذي تبني به `core.sandbox` خلفيّتَها (ق٦٦: الملفُّ لمسار openai).
- جولةُ الواجهة لا تخرج بـ0 إلا إن نجح web_search، وانتظر run_command المالكَ ثم نجح في الحاوية.
- والبحثُ من SearXNG على عنوان الجهاز وحده، بحاويةٍ تنشر منفذَ الرابط بالصورة المثبَّتة في G5.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import tools.probe_execution_boundary as boundary
import tools.probe_j5_agent_round as j5

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "probe" / "j5-docker-searxng-20260927b.json"


def test_the_published_round_meets_every_acceptance_condition():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert j5.acceptance(evidence) == []
    assert evidence["acceptance"] == {"passed": True, "failed": []}


@pytest.mark.parametrize("change, code", [
    (lambda e: [a.update(tool_calls_by_the_model=[]) for a in e["web_search"]["attempts"]],
     "web_search_never_succeeded"),
    (lambda e: [a.update(tool_calls_by_the_model=[{"name": "web_search", "status": "error"}])
                for a in e["web_search"]["attempts"]], "web_search_never_succeeded"),
    (lambda e: e["docker_execution"].update(first_status="complete"), "run_command_did_not_wait_for_the_owner"),
    (lambda e: e["docker_execution"].update(owner_approved=False), "run_command_did_not_wait_for_the_owner"),
    (lambda e: e["docker_execution"].update(tool_calls_by_the_model=[{"name": "run_command", "status": "error"}]),
     "run_command_did_not_succeed"),
    (lambda e: e["docker_execution"].update(boundary_shapes=[]), "run_command_boundary_is_not_docker"),
])
def test_each_missing_condition_is_named(change, code):
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    change(evidence)
    assert j5.acceptance(evidence) == [code]


class _SilentApp:
    """واجهةٌ يجيب فيها النموذجُ بلا أداة: لا بحثَ ولا أمر."""

    def __init__(self, *args, **kwargs):
        pass

    def dispatch(self, request):
        if request["action"] in ("create_project", "create_session"):
            return {"id": "x"}
        return {"status": "complete", "steps": [], "content": "لا أعرف."}

    def close(self):
        pass


def test_a_round_where_the_model_never_searches_is_written_as_failed_and_exits_non_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(j5, "LocalApp", _SilentApp)
    monkeypatch.setattr(j5, "_digest", lambda model: "sha256:weights")
    monkeypatch.setattr(j5, "_searxng", lambda url, container, docker: {"url": url, "image": j5.PINNED_SEARXNG})
    out = tmp_path / "r.json"
    assert j5.main(["--model", "m", "--web-search-url", "http://127.0.0.1:8888",
                    "--runtime-receipt", str(tmp_path / "receipt.json"), "--out", str(out)]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["acceptance"]["passed"] is False
    assert "web_search_never_succeeded" in report["acceptance"]["failed"]


def _docker(image: str, bindings: list[dict]):
    def run(argv, **kwargs):
        fmt = argv[argv.index("--format") + 1]
        out = {"{{.Config.Image}}": image, "{{.Image}}": "sha256:" + "a" * 64,
               "{{json .NetworkSettings.Ports}}": json.dumps({"8080/tcp": bindings})}[fmt]
        return SimpleNamespace(stdout=out + "\n", returncode=0)
    return run


def test_search_comes_only_from_the_pinned_searxng_on_the_loopback_port(monkeypatch):
    local = [{"HostIp": "127.0.0.1", "HostPort": "8888"}]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Docker سُئل قبل فحص الرابط"))
    for url in ("http://search.example.com:8888", "https://127.0.0.1:8888", "http://10.0.0.5:8888",
                "http://127.0.0.1"):
        with pytest.raises(SystemExit) as refused:
            j5._searxng(url, "searxng", "docker")
        assert json.loads(str(refused.value))["code"] == "web_search_url_not_loopback"
    monkeypatch.setattr(subprocess, "run", _docker("searxng/searxng:latest", local))
    with pytest.raises(SystemExit) as refused:
        j5._searxng("http://127.0.0.1:8888", "searxng", "docker")
    assert json.loads(str(refused.value))["code"] == "searxng_image_not_pinned"
    for bindings in ([{"HostIp": "0.0.0.0", "HostPort": "8888"}], [{"HostIp": "127.0.0.1", "HostPort": "9999"}]):
        monkeypatch.setattr(subprocess, "run", _docker(j5.PINNED_SEARXNG, bindings))
        with pytest.raises(SystemExit) as refused:
            j5._searxng("http://127.0.0.1:8888", "searxng", "docker")
        assert json.loads(str(refused.value))["code"] == "searxng_container_does_not_serve_the_url"
    monkeypatch.setattr(subprocess, "run", _docker(j5.PINNED_SEARXNG, local))
    backend = j5._searxng("http://127.0.0.1:8888", "searxng", "docker")
    assert backend["image"] == j5.PINNED_SEARXNG and backend["url"] == "http://127.0.0.1:8888"


BOUNDARY = ROOT / "docs" / "probe" / "execution-boundary-20260927.json"


def test_the_published_boundary_run_meets_every_case():
    evidence = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    assert boundary.boundary_failures(evidence["cases"], evidence["cleanup_verified"]) == []
    assert evidence["acceptance"] == {"passed": True, "failed": []}


def _flip(case, **change):
    evidence = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    cases = [dict(c, **change) if c["case"] == case else c for c in evidence["cases"]]
    return boundary.boundary_failures(cases, True)


def test_each_boundary_case_that_regresses_is_named():
    """ملاحظةُ Codex على #136: رمزُ الخروج كان من البرامج الثلاثة وحدها، فحدٌّ تُخطئ حالاتُه يُنشر ناجحًا."""
    assert _flip("boundary_probe", stdout='{"uid": 0}') == ["boundary_probe"]
    assert _flip("boundary_probe", boundary="unexpected") == ["boundary_probe"]
    leaky = {"uid": 1000, **{flag: True for flag in boundary.PROBE_FLAGS}, "network_unreachable": False}
    assert _flip("boundary_probe", stdout=json.dumps(leaky)) == ["boundary_probe"]
    assert _flip("forged_stdout", exit_code=0) == ["forged_stdout"]
    assert _flip("timeout_with_child", code="execution_outcome_unverified") == ["timeout_with_child"]
    assert _flip("mismatched_runtime_preflight", code=None) == ["mismatched_runtime_preflight"]
    evidence = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    assert boundary.boundary_failures(evidence["cases"], False) == ["cleanup"]


class _Backend:
    """خلفيّةٌ تعيد ما تعيده الحقيقيةُ لكل حالة، فالمجسُّ يُفحص بلا Docker."""

    def __init__(self, receipt, work, files, *, docker_executable):
        if json.loads(Path(receipt).read_text(encoding="utf-8")).get("lock_sha256") == "0" * 64:
            raise boundary.ExecutionRefused("execution_runtime_mismatch", "forged")

    def run(self, argv, *, timeout_s):
        code = argv[-1]
        if code == boundary.BOUNDARY_PROBE:
            flags = {flag: True for flag in boundary.PROBE_FLAGS}
            return SimpleNamespace(exit_code=0, stdout=json.dumps({"uid": 1000, **flags}), timed_out=False,
                                   boundary="docker:" + "b" * 64)
        if code == boundary.CHILD_SLEEPER:
            raise boundary.ExecutionRefused("execution_timeout", "timeout")
        if "raise SystemExit(7)" in code:
            return SimpleNamespace(exit_code=7, stdout="{}", timed_out=False, boundary="docker:" + "b" * 64)
        raise boundary.ExecutionRefused("execution_outcome_unverified", "ambiguous")


def _receipt(tmp_path):
    receipt = tmp_path / "runtime.json"
    receipt.write_text(json.dumps({"image_id": "sha256:" + "c" * 64, "lock_sha256": "d" * 64}), encoding="utf-8")
    return receipt


def test_a_docker_path_the_sandbox_cannot_use_is_refused_before_any_case(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(boundary, "DockerExecutionBackend", lambda *a, **k: pytest.fail("حالةٌ قبل الرفض"))
    monkeypatch.setattr(boundary, "_containers", lambda docker: pytest.fail("Docker سُئل قبل الرفض"))
    code = boundary.main(["--receipt", str(_receipt(tmp_path)), "--docker", "/usr/bin/docker",
                          "--out-boundary", str(tmp_path / "b.json"), "--out-sandbox", str(tmp_path / "s.json")])
    assert code == 2 and json.loads(capsys.readouterr().out)["code"] == "sandbox_backend_uses_the_default_docker_path"
    assert not (tmp_path / "b.json").exists()


@pytest.mark.parametrize("good, cleanup, expected_label, expected_exit", [
    (True, True, "reviewed_disposable_docker", 0),
    (False, True, "not_established", 1),
    (True, False, "reviewed_disposable_docker", 1),
])
def test_the_run_succeeds_only_when_every_case_and_the_sandbox_hold(tmp_path, monkeypatch, good, cleanup,
                                                                     expected_label, expected_exit):
    counts = iter([0, 0 if cleanup else 1])
    monkeypatch.setattr(boundary, "DockerExecutionBackend", _Backend)
    monkeypatch.setattr(boundary, "_containers", lambda docker: next(counts))
    monkeypatch.setattr(boundary.sandbox, "configure_sandbox_backend", lambda receipt, work: None)
    verdicts = iter([good, False, False])
    monkeypatch.setattr(boundary.sandbox, "run_in_sandbox",
                        lambda code, harness: SimpleNamespace(passed=next(verdicts), exit_code=0, error_code=None))
    out_boundary, out_sandbox = tmp_path / "b.json", tmp_path / "s.json"
    code = boundary.main(["--receipt", str(_receipt(tmp_path)), "--out-boundary", str(out_boundary),
                          "--out-sandbox", str(out_sandbox)])
    assert code == expected_exit
    assert json.loads(out_sandbox.read_text(encoding="utf-8"))["boundary"] == expected_label
    assert json.loads(out_boundary.read_text(encoding="utf-8"))["acceptance"]["passed"] is cleanup
