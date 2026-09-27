"""مجسّا ج٥ لا يشهدان بما لم يقع (ملاحظات Codex على #136).

- مجسُّ الحدّ لا يخرج بـ0 إلا إن انتهت كلُّ حالةٍ إلى ما يجب وتحقّق التنظيف، ولا يُسمّي حدَّ الصندوق «مراجَعًا» إن لم تؤدِّ
  البرامجُ الثلاثة ما يثبته؛ ويرفض مسارَ Docker غيرَ الذي تبني به `core.sandbox` خلفيّتَها (ق٦٦: الملفُّ لمسار openai).
- جولةُ الواجهة لا تخرج بـ0 إلا إن نجح web_search بنتيجةٍ فيها رابط، وانتظر run_command المالكَ ثم نجح في الحاوية، بحدٍّ يُقرأ من حقل
  نتيجته نفسِها بصيغته كاملةً؛ ولا تبدأ بمسار Docker لا تبني به الواجهةُ خلفيّتَه.
- والبرامجُ الثلاثة في الصندوق تنتهي كلٌّ إلى حالِه ورمزَي خروجه وخطئه بعينها، لا إلى نجاحٍ أو سقوطٍ وحده.
- والبحثُ من SearXNG على عنوان الجهاز وحده، بحاويةٍ تنشر منفذَ الرابط بالصورة المثبَّتة في G5.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import tools.probe_execution_boundary as boundary
import tools.probe_j5_agent_round as j5

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "probe" / "j5-docker-searxng-20260927e.json"


def test_the_published_round_records_the_acceptance_its_fields_give():
    """الحكمُ المنشور يُعاد من حقول الدليل؛ وهذه الجولةُ سبقت عدَّ المصادر في نتيجة web_search فلا تشهد بها."""
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    failed = j5.acceptance(evidence)
    assert evidence["acceptance"] == {"passed": not failed, "failed": failed}


def _passing():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    evidence["web_search"]["attempts"][-1]["tool_calls_by_the_model"] = [
        {"name": "web_search", "status": "ok", "sourced_results": 3}]
    evidence["docker_execution"]["tool_calls_by_the_model"] = [
        {"name": "run_command", "status": "ok", "boundary": "docker:<64hex>"}]
    evidence["docker_execution"]["pending_argv"] = ["python3", "-c", "print(2+2)"]
    assert j5.acceptance(evidence) == []
    return evidence


@pytest.mark.parametrize("change, code", [
    (lambda e: [a.update(tool_calls_by_the_model=[]) for a in e["web_search"]["attempts"]],
     "web_search_never_succeeded"),
    (lambda e: [a.update(tool_calls_by_the_model=[{"name": "web_search", "status": "error"}])
                for a in e["web_search"]["attempts"]], "web_search_never_succeeded"),
    (lambda e: [a.update(tool_calls_by_the_model=[{"name": "web_search", "status": "ok", "sourced_results": 0}])
                for a in e["web_search"]["attempts"]], "web_search_returned_no_sourced_result"),
    (lambda e: [a.update(tool_calls_by_the_model=[{"name": "web_search", "status": "ok"}])
                for a in e["web_search"]["attempts"]], "web_search_returned_no_sourced_result"),
    (lambda e: [a.update(tool_calls_by_the_model=[{"name": "web_search", "status": "error", "sourced_results": 3}])
                for a in e["web_search"]["attempts"]], "web_search_never_succeeded"),
    (lambda e: e["docker_execution"].update(pending_argv=["echo", "4"]), "run_command_pending_is_not_the_requested_command"),
    (lambda e: e["docker_execution"].pop("pending_argv"), "run_command_pending_is_not_the_requested_command"),
    (lambda e: e["docker_execution"].update(first_status="complete"), "run_command_did_not_wait_for_the_owner"),
    (lambda e: e["docker_execution"].update(owner_approved=False), "run_command_did_not_wait_for_the_owner"),
    (lambda e: e["docker_execution"].update(tool_calls_by_the_model=[{"name": "run_command", "status": "error",
                                                                      "boundary": "docker:<64hex>"}]),
     ["run_command_did_not_succeed", "run_command_boundary_is_not_docker"]),
    (lambda e: e["docker_execution"].update(tool_calls_by_the_model=[{"name": "run_command", "status": "ok",
                                                                      "boundary": "malformed"}]),
     "run_command_boundary_is_not_docker"),
    (lambda e: e["docker_execution"].update(tool_calls_by_the_model=[{"name": "run_command", "status": "ok"}]),
     "run_command_boundary_is_not_docker"),
])
def test_each_missing_condition_is_named(change, code):
    evidence = _passing()
    change(evidence)
    assert j5.acceptance(evidence) == (code if isinstance(code, list) else [code])


def test_the_boundary_is_read_whole_from_the_tool_result_not_from_the_reply_text():
    """ملاحظةُ Codex على #136: معرّفٌ مبتور كان يُطبَّع إلى الشكل المقبول، ورمزٌ في نصّ النموذج كان يكفي."""
    full = "docker:" + "a" * 64

    def reply(boundary=None, **extra):
        result = {"name": "run_command", "status": "ok", "content": "4", **extra}
        if boundary is not None:
            result["boundary"] = boundary
        return {"status": "complete", "content": f"الحاوية {full}", "steps": [{"tool_results": [result]}]}

    assert j5._tools(reply(full)) == [{"name": "run_command", "status": "ok", "boundary": "docker:<64hex>"}]
    for truncated in ("docker:deadbeefdead", full[:-1], full + "0", full.upper(), "docker:" + "g" * 64, 7):
        assert j5._tools(reply(truncated))[0]["boundary"] == "malformed"
    assert j5._tools(reply()) == [{"name": "run_command", "status": "ok"}]


def test_sources_are_counted_from_the_tool_result_not_from_the_answer():
    """ملاحظةُ Codex على #136: ردُّ SearXNG بقائمةٍ فارغة كان نتيجةً بحالة ok تُقبل بلا مصدرٍ واحد."""
    def reply(results):
        result = {"name": "web_search", "status": "ok", "content": "(لا نتائج)", "result_count": len(results or [])}
        if results is not None:
            result["results"] = results
        return {"status": "complete", "content": "المصدر: https://www.python.org/", "steps": [{"tool_results": [result]}]}

    sourced = [{"title": "Python", "url": "https://www.python.org/", "snippet": ""},
               {"title": "Docs", "url": "http://docs.python.org/", "snippet": ""}]
    assert j5._tools(reply(sourced)) == [{"name": "web_search", "status": "ok", "sourced_results": 2}]
    for unsourced in ([], None, "x", [{"title": "t", "url": ""}], [{"title": "t", "url": "ftp://x"}], ["https://x"],
                      [{"title": "t", "url": 7}]):
        assert j5._tools(reply(unsourced)) == [{"name": "web_search", "status": "ok", "sourced_results": 0}]


class _EmptyThenFoundApp:
    """بحثٌ أول يعود بلا نتائج، ثم بحثٌ بمصدر؛ ولا أمر."""

    def __init__(self, *args, **kwargs):
        self.searches = 0

    def dispatch(self, request):
        if request["action"] in ("create_project", "create_session"):
            return {"id": "x"}
        if request["message"] == j5.COMMAND_REQUEST:
            return {"status": "complete", "steps": [], "content": "لا أعرف."}
        self.searches += 1
        results = [{"title": "t", "url": "https://example.org/", "snippet": ""}] if self.searches > 1 else []
        return {"status": "complete", "content": "",
                "steps": [{"tool_results": [{"name": "web_search", "status": "ok", "results": results}]}]}

    def close(self):
        pass


def test_an_empty_search_does_not_end_the_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(j5, "LocalApp", _EmptyThenFoundApp)
    monkeypatch.setattr(j5, "_digest", lambda model: "sha256:weights")
    monkeypatch.setattr(j5, "_searxng", lambda url, container, docker: {"url": url, "image": j5.PINNED_SEARXNG})
    out = tmp_path / "r.json"
    assert j5.main(["--model", "m", "--web-search-url", "http://127.0.0.1:8888",
                    "--runtime-receipt", str(tmp_path / "receipt.json"), "--out", str(out)]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    counts = [a["tool_calls_by_the_model"][0]["sourced_results"] for a in report["web_search"]["attempts"]]
    assert counts == [0, 1]
    assert "web_search_returned_no_sourced_result" not in report["acceptance"]["failed"]
    assert "web_search_never_succeeded" not in report["acceptance"]["failed"]


def test_only_the_requested_command_passes_as_the_pending_argv():
    """ملاحظةُ Codex على #144: الأمرُ المطلوب حرفيًّا، لا python بدل python3 ولا شيفرةٌ بمسافاتٍ أخرى."""
    assert j5.requested_command(["python3", "-c", "print(2+2)"])
    for argv in (["python", "-c", "print(2+2)"], ["python3", "-c", "print( 2 + 2 )"], ["echo", "4"], ["python3", "-c", "print(2+3)"], ["python3", "-c", "print(2+2)", "x"],
                 ["/bin/sh", "-c", "print(2+2)"], ["python3", "-m", "print(2+2)"], "python3 -c print(2+2)", None,
                 ["python3", "-c", 4]):
        assert not j5.requested_command(argv)


class _PendingApp:
    """جولةُ أمرٍ يقترح فيها النموذجُ argv بعينه؛ والبحثُ ينجح بمصدر."""

    def __init__(self, argv):
        self.argv, self.decided = argv, False

    def dispatch(self, request):
        action = request["action"]
        if action in ("create_project", "create_session"):
            return {"id": "x"}
        if action == "agent_decide":
            self.decided = True
            return {"status": "approved"}
        if action == "agent_resume":
            return {"status": "complete", "content": "4", "steps": [{"tool_results": [
                {"name": "run_command", "status": "ok", "boundary": "docker:" + "a" * 64}]}]}
        if request["message"] == j5.COMMAND_REQUEST:
            return {"status": "awaiting_owner", "steps": [], "pending": [
                {"name": "run_command", "action_id": "a1", "call_digest": "d", "revision": 1,
                 "arguments": {"argv": self.argv}}]}
        return {"status": "complete", "content": "", "steps": [{"tool_results": [
            {"name": "web_search", "status": "ok", "results": [{"title": "t", "url": "https://example.org/"}]}]}]}

    def close(self):
        pass


@pytest.mark.parametrize("argv, approved", [(["python3", "-c", "print(2+2)"], True), (["echo", "4"], False)])
def test_only_the_requested_pending_command_is_approved(tmp_path, monkeypatch, argv, approved):
    """ملاحظةُ Codex على #136: كان المجسُّ يوافق على أوّل أمرٍ معلَّق أيًّا كان، فيُنشر «echo 4» كأنه ما طُلب."""
    app = _PendingApp(argv)
    monkeypatch.setattr(j5, "LocalApp", lambda *a, **k: app)
    monkeypatch.setattr(j5, "_digest", lambda model: "sha256:weights")
    monkeypatch.setattr(j5, "_searxng", lambda url, container, docker: {"url": url, "image": j5.PINNED_SEARXNG})
    out = tmp_path / "r.json"
    code = j5.main(["--model", "m", "--web-search-url", "http://127.0.0.1:8888",
                    "--runtime-receipt", str(tmp_path / "receipt.json"), "--out", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert app.decided is approved and report["docker_execution"]["pending_argv"] == argv
    assert report["source_sha256"]["tools/probe_j5_agent_round.py"] == j5._sources()["tools/probe_j5_agent_round.py"]
    if approved:
        assert code == 0 and report["acceptance"] == {"passed": True, "failed": []}
    else:
        assert code == 1 and "run_command_pending_is_not_the_requested_command" in report["acceptance"]["failed"]


def test_a_source_that_changes_during_the_round_writes_no_report(tmp_path, monkeypatch, capsys):
    """ملاحظةُ Codex على #136: البصماتُ كانت تُؤخذ بعد الجولة، فتشهد لبايتاتٍ قد لا تكون ما نُفّذ."""
    app = _PendingApp(["python3", "-c", "print(2+2)"])
    monkeypatch.setattr(j5, "LocalApp", lambda *a, **k: app)
    monkeypatch.setattr(j5, "_digest", lambda model: "sha256:weights")
    monkeypatch.setattr(j5, "_searxng", lambda url, container, docker: {"url": url, "image": j5.PINNED_SEARXNG})
    real = j5._sources()
    seen = iter([real, {**real, "tools/probe_j5_agent_round.py": "0" * 64}])
    monkeypatch.setattr(j5, "_sources", lambda: next(seen))
    out = tmp_path / "r.json"
    assert j5.main(["--model", "m", "--web-search-url", "http://127.0.0.1:8888",
                    "--runtime-receipt", str(tmp_path / "receipt.json"), "--out", str(out)]) == 2
    refused = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert refused == {"status": "refused", "code": "sources_changed_during_the_run",
                       "changed": ["tools/probe_j5_agent_round.py"]}
    assert not out.exists()


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


def test_a_docker_path_the_run_command_backend_cannot_use_is_refused_before_the_round(tmp_path, monkeypatch, capsys):
    """ملاحظةُ Codex على #136: الواجهةُ تبني خلفيّةَ run_command بالمسار الافتراضي وحده (#142)."""
    monkeypatch.setattr(j5, "LocalApp", lambda *a, **k: pytest.fail("جولةٌ قبل الرفض"))
    monkeypatch.setattr(j5, "_searxng", lambda *a, **k: pytest.fail("Docker سُئل قبل الرفض"))
    out = tmp_path / "r.json"
    code = j5.main(["--model", "m", "--web-search-url", "http://127.0.0.1:8888", "--docker", "/usr/bin/docker",
                    "--runtime-receipt", str(tmp_path / "receipt.json"), "--out", str(out)])
    assert code == 2 and json.loads(capsys.readouterr().out)["code"] == "run_command_backend_uses_the_default_docker_path"
    assert not out.exists()
    assert j5.EXECUTION_DOCKER == boundary.SANDBOX_DOCKER == "/usr/local/bin/docker"


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


BOUNDARY = ROOT / "docs" / "probe" / "execution-boundary-20260927c.json"
SANDBOX = ROOT / "docs" / "probe" / "sandbox-container-20260927c.json"


def test_the_published_sandbox_run_ends_each_program_where_it_must():
    evidence = json.loads(SANDBOX.read_text(encoding="utf-8"))
    assert boundary.sandbox_failures(evidence) == []
    assert evidence["acceptance"] == {"passed": True, "failed": []}


@pytest.mark.parametrize("name, change", [
    ("correct_program", {"passed": False, "exit_code": 1, "error_code": "exit_1"}),
    ("incorrect_program", {"error_code": "execution_runtime_mismatch", "exit_code": -1}),
    ("incorrect_program", {"error_code": "exit_2", "exit_code": 2}),
    ("exit_zero_before_the_harness", {"error_code": "execution_timeout", "exit_code": -1}),
    ("exit_zero_before_the_harness", {"error_code": "exit_1", "exit_code": 1}),
])
def test_a_program_that_falls_for_another_reason_is_named(name, change):
    """ملاحظةُ Codex على #136: رفضُ الخلفية أو خطأُ الصياغة يُسقطان الخاطئَ والمبكّرَ دون أن يحكم المدقّق."""
    evidence = json.loads(SANDBOX.read_text(encoding="utf-8"))
    evidence[name].update(change)
    assert boundary.sandbox_failures(evidence) == [name]


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
    # الرمزُ 7 وحده لا يشهد بأن الحمولةَ المزوّرة طُبعت (ملاحظة Codex على #136)
    assert _flip("forged_stdout", stdout="") == ["forged_stdout"]
    assert _flip("forged_stdout", stdout='{"exit_code":1}\n') == ["forged_stdout"]
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
            return SimpleNamespace(exit_code=7, stdout=boundary.FORGED_PAYLOAD + "\n", timed_out=False,
                                   boundary="docker:" + "b" * 64)
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
    (True, False, "not_established", 1),
])
def test_the_run_succeeds_only_when_every_case_and_the_sandbox_hold(tmp_path, monkeypatch, good, cleanup,
                                                                     expected_label, expected_exit):
    counts = iter([{"a" * 64}, {"a" * 64} if cleanup else {"a" * 64, "b" * 64}])
    monkeypatch.setattr(boundary, "DockerExecutionBackend", _Backend)
    monkeypatch.setattr(boundary, "_containers", lambda docker: next(counts))
    monkeypatch.setattr(boundary.sandbox, "configure_sandbox_backend", lambda receipt, work: None)
    verdicts = iter([SimpleNamespace(passed=True, exit_code=0, error_code=None) if good
                     else SimpleNamespace(passed=False, exit_code=-1, error_code="execution_runtime_mismatch"),
                     SimpleNamespace(passed=False, exit_code=1, error_code="exit_1"),
                     SimpleNamespace(passed=False, exit_code=0, error_code="verdict_missing")])
    monkeypatch.setattr(boundary.sandbox, "run_in_sandbox", lambda code, harness: next(verdicts))
    out_boundary, out_sandbox = tmp_path / "b.json", tmp_path / "s.json"
    code = boundary.main(["--receipt", str(_receipt(tmp_path)), "--out-boundary", str(out_boundary),
                          "--out-sandbox", str(out_sandbox)])
    assert code == expected_exit
    sandbox_report = json.loads(out_sandbox.read_text(encoding="utf-8"))
    assert sandbox_report["boundary"] == expected_label
    assert sandbox_report["acceptance"]["passed"] is good
    assert json.loads(out_boundary.read_text(encoding="utf-8"))["acceptance"]["passed"] is cleanup


def test_a_source_that_changes_during_the_boundary_run_writes_no_report(tmp_path, monkeypatch, capsys):
    """ملاحظةُ Codex على #136: الدليلُ مربوطٌ بالمجسّ نفسِه، وملفٌّ يتغيّر في أثناء التشغيل لا يُنشر له تقرير."""
    monkeypatch.setattr(boundary, "DockerExecutionBackend", _Backend)
    monkeypatch.setattr(boundary, "_containers", lambda docker: {"a" * 64})
    monkeypatch.setattr(boundary.sandbox, "configure_sandbox_backend", lambda receipt, work: None)
    real = boundary._sources()
    seen = iter([real, {**real, "tools/probe_execution_boundary.py": "0" * 64}])
    monkeypatch.setattr(boundary, "_sources", lambda: next(seen))
    monkeypatch.setattr(boundary.sandbox, "run_in_sandbox",
                        lambda code, harness: SimpleNamespace(passed=True, exit_code=0, error_code=None))
    out_boundary, out_sandbox = tmp_path / "b.json", tmp_path / "s.json"
    assert boundary.main(["--receipt", str(_receipt(tmp_path)), "--out-boundary", str(out_boundary),
                          "--out-sandbox", str(out_sandbox)]) == 2
    refused = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert refused["code"] == "sources_changed_during_the_run"
    assert refused["changed"] == ["tools/probe_execution_boundary.py"]
    assert not out_boundary.exists() and not out_sandbox.exists()


def test_a_receipt_replaced_during_the_run_writes_no_report(tmp_path, monkeypatch, capsys):
    """ملاحظةُ Codex على #144: الخلفيّةُ تحفظ الإيصالَ الذي قرأته، فإيصالٌ يُستبدل بعدها لا يُنسب إليه الدليل."""
    receipt = _receipt(tmp_path)
    monkeypatch.setattr(boundary, "DockerExecutionBackend", _Backend)
    monkeypatch.setattr(boundary, "_containers", lambda docker: {"a" * 64})
    monkeypatch.setattr(boundary.sandbox, "configure_sandbox_backend", lambda receipt, work: None)

    def swap_then_pass(code, harness):
        receipt.write_text(json.dumps({"image_id": "sha256:" + "e" * 64, "lock_sha256": "d" * 64}), encoding="utf-8")
        return SimpleNamespace(passed=True, exit_code=0, error_code=None)
    monkeypatch.setattr(boundary.sandbox, "run_in_sandbox", swap_then_pass)
    out_boundary, out_sandbox = tmp_path / "b.json", tmp_path / "s.json"
    assert boundary.main(["--receipt", str(receipt), "--out-boundary", str(out_boundary),
                          "--out-sandbox", str(out_sandbox)]) == 2
    refused = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert refused["code"] == "sources_changed_during_the_run" and refused["changed"] == ["runtime_receipt"]
    assert not out_boundary.exists() and not out_sandbox.exists()


def test_the_reports_carry_the_probe_hash_taken_before_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "DockerExecutionBackend", _Backend)
    monkeypatch.setattr(boundary, "_containers", lambda docker: {"a" * 64})
    monkeypatch.setattr(boundary.sandbox, "configure_sandbox_backend", lambda receipt, work: None)
    verdicts = iter([SimpleNamespace(passed=True, exit_code=0, error_code=None),
                     SimpleNamespace(passed=False, exit_code=1, error_code="exit_1"),
                     SimpleNamespace(passed=False, exit_code=0, error_code="verdict_missing")])
    monkeypatch.setattr(boundary.sandbox, "run_in_sandbox", lambda code, harness: next(verdicts))
    out_boundary, out_sandbox = tmp_path / "b.json", tmp_path / "s.json"
    assert boundary.main(["--receipt", str(_receipt(tmp_path)), "--out-boundary", str(out_boundary),
                          "--out-sandbox", str(out_sandbox)]) == 0
    probe = boundary._sources()["tools/probe_execution_boundary.py"]
    import hashlib
    receipt_sha = hashlib.sha256((tmp_path / "runtime.json").read_bytes()).hexdigest()
    for out in (out_boundary, out_sandbox):
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["probe_sha256"] == probe and report["runtime_receipt_sha256"] == receipt_sha
        assert report["runtime_image_id"] == "sha256:" + "c" * 64


def test_cleanup_compares_container_ids_not_their_count(tmp_path, monkeypatch):
    """ملاحظةُ Codex على #136: حاويةٌ غريبة تُحذف وحاويةُ مجسٍّ تبقى، فيتساوى العددُ قبل وبعد."""
    sets = iter([{"a" * 64}, {"b" * 64}])
    monkeypatch.setattr(boundary, "DockerExecutionBackend", _Backend)
    monkeypatch.setattr(boundary, "_containers", lambda docker: next(sets))
    monkeypatch.setattr(boundary.sandbox, "configure_sandbox_backend", lambda receipt, work: None)
    verdicts = iter([SimpleNamespace(passed=True, exit_code=0, error_code=None),
                     SimpleNamespace(passed=False, exit_code=1, error_code="exit_1"),
                     SimpleNamespace(passed=False, exit_code=0, error_code="verdict_missing")])
    monkeypatch.setattr(boundary.sandbox, "run_in_sandbox", lambda code, harness: next(verdicts))
    out_boundary = tmp_path / "b.json"
    assert boundary.main(["--receipt", str(_receipt(tmp_path)), "--out-boundary", str(out_boundary),
                          "--out-sandbox", str(tmp_path / "s.json")]) == 1
    report = json.loads(out_boundary.read_text(encoding="utf-8"))
    assert report["cleanup_verified"] is False and report["acceptance"]["failed"] == ["cleanup"]
    assert report["containers_before_after"] == [1, 1] and report["containers_left_by_the_run"] == 1
    assert "a" * 64 not in out_boundary.read_text(encoding="utf-8")


@pytest.mark.parametrize("flag, expected", [(0, False), (os.ST_RDONLY, True)])
def test_the_read_only_root_is_read_from_the_mount_flag(monkeypatch, capsys, flag, expected):
    """ملاحظةُ Codex على #136: كتابةُ UID 1000 في `/` تفشل ولو كان الجذرُ قابلًا للكتابة، فلا تشهد بالقراءة وحدها."""
    import socket

    class _Closed:
        def settimeout(self, _):
            pass

        def connect(self, _):
            raise OSError("unreachable")

        def close(self):
            pass

    import builtins
    import io
    real_open = builtins.open
    # المجسُّ يقرأ /proc/mounts داخل الحاوية؛ وعلى مضيفٍ بلا /proc (الماك) يُعطى ملفَّ تركيبٍ مصطنعًا
    fake_open = lambda path, *a, **k: (io.StringIO("overlay / overlay ro 0 0\n") if path == "/proc/mounts"
                                       else real_open(path, *a, **k))
    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.setattr(os, "statvfs", lambda path: SimpleNamespace(f_flag=flag))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: _Closed())
    exec(compile(boundary.BOUNDARY_PROBE, "<boundary-probe>", "exec"), {})
    assert json.loads(capsys.readouterr().out)["rootfs_readonly"] is expected


def test_a_failed_container_enumeration_is_never_read_as_a_clean_run(tmp_path, monkeypatch, capsys):
    """ملاحظةُ Codex على #136: `docker ps` الساقطُ يعيد مخرجًا فارغًا، فكان يُقرأ «لا حاويةَ بقيت»."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="daemon"))
    assert boundary._containers("docker") is None
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="a\nb\n", stderr=""))
    assert boundary._containers("docker") == {"a", "b"}

    monkeypatch.setattr(boundary, "DockerExecutionBackend", lambda *a, **k: pytest.fail("حالةٌ قبل الرفض"))
    monkeypatch.setattr(boundary, "_containers", lambda docker: None)
    assert boundary.main(["--receipt", str(_receipt(tmp_path)), "--out-boundary", str(tmp_path / "b.json"),
                          "--out-sandbox", str(tmp_path / "s.json")]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "container_enumeration_failed"

    listed = iter([{"a" * 64}, None])
    monkeypatch.setattr(boundary, "DockerExecutionBackend", _Backend)
    monkeypatch.setattr(boundary, "_containers", lambda docker: next(listed))
    monkeypatch.setattr(boundary.sandbox, "configure_sandbox_backend", lambda receipt, work: None)
    verdicts = iter([SimpleNamespace(passed=True, exit_code=0, error_code=None),
                     SimpleNamespace(passed=False, exit_code=1, error_code="exit_1"),
                     SimpleNamespace(passed=False, exit_code=0, error_code="verdict_missing")])
    monkeypatch.setattr(boundary.sandbox, "run_in_sandbox", lambda code, harness: next(verdicts))
    out = tmp_path / "b2.json"
    assert boundary.main(["--receipt", str(_receipt(tmp_path)), "--out-boundary", str(out),
                          "--out-sandbox", str(tmp_path / "s2.json")]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["cleanup_verified"] is False and report["acceptance"]["failed"] == ["cleanup"]
    assert report["containers_left_by_the_run"] is None
