"""مجسّا ج٥ لا يشهدان بما لم يقع (ملاحظات Codex على #136).

- حدُّ الصندوق يُبنى بمسار Docker الذي أُعطيه المجسّ، ولا يُسمّى «مراجَعًا» إن لم تؤدِّ البرامجُ الثلاثة ما يثبته.
- جولةُ الواجهة لا تخرج بـ0 إلا إن نادى النموذجُ web_search، وانتظر run_command المالكَ ثم نجح في الحاوية.
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
EVIDENCE = ROOT / "docs" / "probe" / "j5-docker-searxng-20260927.json"


def test_the_published_round_meets_every_acceptance_condition():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert j5.acceptance(evidence) == []
    assert evidence["acceptance"] == {"passed": True, "failed": []}


@pytest.mark.parametrize("change, code", [
    (lambda e: e["web_search"].update(called_web_search=False), "web_search_never_called_by_the_model"),
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
    assert "web_search_never_called_by_the_model" in report["acceptance"]["failed"]


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


class _Backend:
    seen: list[str] = []

    def __init__(self, receipt, work, files, *, docker_executable):
        type(self).seen.append(docker_executable)

    def run(self, argv, *, timeout_s):
        return SimpleNamespace(exit_code=0, stdout="{}", timed_out=False, boundary="docker:" + "b" * 64)


@pytest.mark.parametrize("good, expected_label, expected_exit", [
    (True, "reviewed_disposable_docker", 0),
    (False, "not_established", 1),
])
def test_the_sandbox_is_built_with_the_given_docker_and_named_only_when_it_worked(tmp_path, monkeypatch, good,
                                                                                  expected_label, expected_exit):
    receipt = tmp_path / "runtime.json"
    receipt.write_text(json.dumps({"image_id": "sha256:" + "c" * 64, "lock_sha256": "d" * 64}), encoding="utf-8")
    _Backend.seen = []
    configured = []
    monkeypatch.setattr(boundary, "DockerExecutionBackend", _Backend)
    monkeypatch.setattr(boundary, "_containers", lambda docker: 0)
    monkeypatch.setattr(boundary.sandbox, "configure_sandbox_backend",
                        lambda receipt, work, **kw: configured.append(kw))
    verdicts = iter([good, False, False])
    monkeypatch.setattr(boundary.sandbox, "run_in_sandbox",
                        lambda code, harness: SimpleNamespace(passed=next(verdicts), exit_code=0, error_code=None))
    out_boundary, out_sandbox = tmp_path / "b.json", tmp_path / "s.json"
    code = boundary.main(["--receipt", str(receipt), "--docker", "/usr/bin/docker",
                          "--out-boundary", str(out_boundary), "--out-sandbox", str(out_sandbox)])
    assert code == expected_exit
    assert set(_Backend.seen) == {"/usr/bin/docker"} and configured == [{"docker_executable": "/usr/bin/docker"}]
    assert json.loads(out_sandbox.read_text(encoding="utf-8"))["boundary"] == expected_label
