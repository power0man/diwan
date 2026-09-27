"""مجسّا ج٥ لا يشهدان بما لم يقع (ملاحظات Codex على #136).

- مجسُّ الحدّ لا يخرج بـ0 إلا إن انتهت كلُّ حالةٍ إلى ما يجب وتحقّق التنظيف، ولا يُسمّي حدَّ الصندوق «مراجَعًا» إن لم تؤدِّ
  البرامجُ الثلاثة ما يثبته؛ ويرفض مسارَ Docker غيرَ الذي تبني به `core.sandbox` خلفيّتَها (ق٦٦: الملفُّ لمسار openai).
- جولةُ الواجهة لا تخرج بـ0 إلا إن نجح web_search بنتيجةٍ فيها رابط، وانتظر run_command المالكَ ثم نجح في الحاوية، بحدٍّ يُقرأ من حقل
  نتيجته نفسِها بصيغته كاملةً؛ ولا تبدأ بمسار Docker لا تبني به الواجهةُ خلفيّتَه.
- والبرامجُ الثلاثة في الصندوق تنتهي كلٌّ إلى حالِه ورمزَي خروجه وخطئه بعينها، لا إلى نجاحٍ أو سقوطٍ وحده.
- والبحثُ من SearXNG على عنوان الجهاز وحده، بحاويةٍ تنشر منفذَ الرابط بالصورة المثبَّتة في G5.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import tools.probe_execution_boundary as boundary
import tools.probe_j5_agent_round as j5

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "probe" / "j5-docker-searxng-20260927h.json"


def test_the_published_round_records_the_acceptance_its_fields_give():
    """الحكمُ المنشور يُعاد من حقول الدليل؛ وهذه الجولةُ سبقت عدَّ المصادر في نتيجة web_search فلا تشهد بها."""
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    failed = j5.acceptance(evidence)
    assert evidence["acceptance"] == {"passed": not failed, "failed": failed}


ATTESTED = "http://127.0.0.1:8080"


def _ws(**change):
    """نتيجةُ web_search مقبولة: ناجحة، بمصدرٍ واحدٍ على الأقل، من SearXNG المشهود له."""
    return {"name": "web_search", "status": "ok", "sourced_results": 3, "source_endpoint": ATTESTED, **change}


def _run(**change):
    """نتيجةُ run_command مقبولة: ناجحة في الحاوية، بخروجٍ صفر وخرجٍ «4»؛ ومفتاحٌ قيمتُه None يُحذف."""
    call = {"name": "run_command", "status": "ok", "boundary": "docker:<64hex>", "exit_code": 0, "output": "4", **change}
    return {k: v for k, v in call.items() if v is not None or k not in change}


def _passing():
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    evidence["web_search"]["backend"]["url"] = ATTESTED
    evidence["web_search"]["attempts"][-1]["tool_calls_by_the_model"] = [_ws()]
    evidence["docker_execution"]["tool_calls_by_the_model"] = [_run()]
    evidence["docker_execution"]["pending_argv"] = ["python3", "-c", "print(2+2)"]
    assert j5.acceptance(evidence) == []
    return evidence


NO_SOURCE = ["web_search_returned_no_sourced_result", "web_search_source_is_not_the_attested_searxng"]
set_search = lambda call: (lambda e: [a.update(tool_calls_by_the_model=call) for a in e["web_search"]["attempts"]])
set_run = lambda calls: (lambda e: e["docker_execution"].update(tool_calls_by_the_model=calls))


@pytest.mark.parametrize("change, code", [
    (set_search([]), "web_search_never_succeeded"),
    (set_search([_ws(status="error")]), "web_search_never_succeeded"),
    (set_search([_ws(sourced_results=0)]), NO_SOURCE),
    (set_search([{"name": "web_search", "status": "ok"}]), NO_SOURCE),
    # ملاحظةُ Codex على #144 (الجولة التالية): النتيجةُ من SearXNG المشهود له بعينه
    (set_search([_ws(source_endpoint="http://127.0.0.1:9999")]), "web_search_source_is_not_the_attested_searxng"),
    (set_search([_ws(source_endpoint=None)]), "web_search_source_is_not_the_attested_searxng"),
    (lambda e: e["docker_execution"].update(pending_argv=["echo", "4"]), "run_command_pending_is_not_the_requested_command"),
    (lambda e: e["docker_execution"].pop("pending_argv"), "run_command_pending_is_not_the_requested_command"),
    (lambda e: e["docker_execution"].update(first_status="complete"), "run_command_did_not_wait_for_the_owner"),
    (lambda e: e["docker_execution"].update(owner_approved=False), "run_command_did_not_wait_for_the_owner"),
    (set_run([_run(status="error")]),
     ["run_command_did_not_succeed", "run_command_boundary_is_not_docker", "run_command_output_is_not_4"]),
    (set_run([_run(boundary="malformed")]), "run_command_boundary_is_not_docker"),
    (set_run([_run(boundary=None)]), "run_command_boundary_is_not_docker"),
    # والأمرُ أدّى ما طُلب: خروجٌ بصفر وخرجٌ «4» من نتيجة الأداة نفسِها
    (set_run([_run(output="5")]), "run_command_output_is_not_4"),
    (set_run([_run(exit_code=1)]), "run_command_output_is_not_4"),
    (set_run([_run(output=None)]), "run_command_output_is_not_4"),
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

    assert j5._tools(reply(full)) == [{"name": "run_command", "status": "ok", "exit_code": None, "output": "4",
                                       "boundary": "docker:<64hex>"}]
    for truncated in ("docker:deadbeefdead", full[:-1], full + "0", full.upper(), "docker:" + "g" * 64, 7):
        assert j5._tools(reply(truncated))[0]["boundary"] == "malformed"
    assert j5._tools(reply()) == [{"name": "run_command", "status": "ok", "exit_code": None, "output": "4"}]


def test_sources_are_counted_from_the_tool_result_not_from_the_answer():
    """ملاحظةُ Codex على #136: ردُّ SearXNG بقائمةٍ فارغة كان نتيجةً بحالة ok تُقبل بلا مصدرٍ واحد."""
    def reply(results):
        result = {"name": "web_search", "status": "ok", "content": "(لا نتائج)", "result_count": len(results or [])}
        if results is not None:
            result["results"] = results
        return {"status": "complete", "content": "المصدر: https://www.python.org/", "steps": [{"tool_results": [result]}]}

    sourced = [{"title": "Python", "url": "https://www.python.org/", "snippet": ""},
               {"title": "Docs", "url": "http://docs.python.org/", "snippet": ""}]
    assert j5._tools(reply(sourced)) == [{"name": "web_search", "status": "ok", "sourced_results": 2,
                                          "source_endpoint": None}]
    # وبادئةٌ بلا مضيف لا تُعدّ مصدرًا (ملاحظة Codex على #144)
    for unsourced in ([], None, "x", [{"title": "t", "url": ""}], [{"title": "t", "url": "ftp://x"}], ["https://x"],
                      [{"title": "t", "url": 7}], [{"title": "t", "url": "https://"}], [{"title": "t", "url": "http:///p"}],
                      [{"title": "t", "url": "https://[::1"}]):
        assert j5._tools(reply(unsourced)) == [{"name": "web_search", "status": "ok", "sourced_results": 0,
                                                "source_endpoint": None}]


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


PIN = {"id": "c" * 64, "image": j5.PINNED_SEARXNG, "image_id": "sha256:" + "a" * 64, "running": True,
       "started_at": "2026-09-27T03:00:00Z", "serves_url": True}


def _ui_receipt(tmp_path, **fields):
    """إيصالُ تشغيلٍ بصورته وقفله، كما يكتبه ci/prepare_runtime.py."""
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({"image_id": "sha256:" + "b" * 64, "lock_sha256": "c" * 64, **fields}), encoding="utf-8")
    return path


def _attest(monkeypatch, serving=None, digests=None):
    """SearXNG مشهودٌ له بمعرّفه، والنموذجُ ببصمته؛ وما يُرى منهما بعد الجولة كما قبلها ما لم يُمرَّر غيرُه."""
    digests = iter(digests or ["sha256:weights"] * 2)
    monkeypatch.setattr(j5, "_digest", lambda model: next(digests))
    monkeypatch.setattr(j5, "_searxng", lambda url, container, docker: ({"url": url, "image": j5.PINNED_SEARXNG}, PIN))
    monkeypatch.setattr(j5, "_serving", serving or (lambda url, container_id, docker: dict(PIN)))


def test_an_empty_search_does_not_end_the_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(j5, "LocalApp", _EmptyThenFoundApp)
    _attest(monkeypatch)
    out = tmp_path / "r.json"
    assert j5.main(["--model", "m", "--web-search-url", "http://127.0.0.1:8888",
                    "--runtime-receipt", str(_ui_receipt(tmp_path)), "--out", str(out)]) == 1
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

    def __init__(self, argv, decision=None):
        self.argv, self.decided = argv, False
        self.decision = decision or {"state": "approved"}

    def dispatch(self, request):
        action = request["action"]
        if action in ("create_project", "create_session"):
            return {"id": "x"}
        if action == "agent_decide":
            self.decided = True
            return self.decision
        if action == "agent_resume":
            return {"status": "complete", "content": "4", "steps": [{"tool_results": [
                {"name": "run_command", "status": "ok", "boundary": "docker:" + "a" * 64, "exit_code": 0,
                 "content": "4\n"}]}]}
        if request["message"] == j5.COMMAND_REQUEST:
            return {"status": "awaiting_owner", "steps": [], "pending": [
                {"name": "run_command", "action_id": "a1", "call_digest": "d", "revision": 1,
                 "arguments": {"argv": self.argv}}]}
        return {"status": "complete", "content": "", "steps": [{"tool_results": [
            {"name": "web_search", "status": "ok", "results": [{"title": "t", "url": "https://example.org/"}],
             "source": {"backend": "searxng", "endpoint": "http://127.0.0.1:8888"}}]}]}

    def close(self):
        pass


@pytest.mark.parametrize("argv, approved", [(["python3", "-c", "print(2+2)"], True), (["echo", "4"], False)])
def test_only_the_requested_pending_command_is_approved(tmp_path, monkeypatch, argv, approved):
    """ملاحظةُ Codex على #136: كان المجسُّ يوافق على أوّل أمرٍ معلَّق أيًّا كان، فيُنشر «echo 4» كأنه ما طُلب."""
    app = _PendingApp(argv)
    monkeypatch.setattr(j5, "LocalApp", lambda *a, **k: app)
    _attest(monkeypatch)
    out = tmp_path / "r.json"
    code = j5.main(["--model", "m", "--web-search-url", "http://127.0.0.1:8888",
                    "--runtime-receipt", str(_ui_receipt(tmp_path)), "--out", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert app.decided is approved and report["docker_execution"]["pending_argv"] == argv
    assert report["host"]["machine"] == os.uname().machine
    assert report["source_sha256"]["tools/probe_j5_agent_round.py"] == j5._sources()["tools/probe_j5_agent_round.py"]
    # والصورةُ من الإيصال المقروء قبل الجولة، وبصمتُه معها
    receipt = (tmp_path / "receipt.json").read_bytes()
    assert report["runtime"] == {"image_id": "sha256:" + "b" * 64, "lock_sha256": "c" * 64,
                                 "receipt_sha256": hashlib.sha256(receipt).hexdigest()}
    if approved:
        assert code == 0 and report["acceptance"] == {"passed": True, "failed": []}
    else:
        assert code == 1 and "run_command_pending_is_not_the_requested_command" in report["acceptance"]["failed"]


def test_a_decision_the_action_store_did_not_approve_is_not_an_approval(tmp_path, monkeypatch):
    """ردُّ الواجهة على agent_decide قد يكون خطأً مسمًّى؛ فالموافقةُ حالةُ approved من مخزن الأفعال وحدها."""
    app = _PendingApp(["python3", "-c", "print(2+2)"], decision={"error": "action_revision_stale"})
    monkeypatch.setattr(j5, "LocalApp", lambda *a, **k: app)
    _attest(monkeypatch)
    out = tmp_path / "r.json"
    assert j5.main(["--model", "m", "--web-search-url", "http://127.0.0.1:8888",
                    "--runtime-receipt", str(_ui_receipt(tmp_path)), "--out", str(out)]) == 1
    execution = json.loads(out.read_text(encoding="utf-8"))["docker_execution"]
    assert execution["owner_approved"] is False and execution["decision_state"] is None
    assert "run_command_did_not_wait_for_the_owner" in j5.acceptance(json.loads(out.read_text(encoding="utf-8")))


def test_a_source_that_changes_during_the_round_writes_no_report(tmp_path, monkeypatch, capsys):
    """ملاحظةُ Codex على #136: البصماتُ كانت تُؤخذ بعد الجولة، فتشهد لبايتاتٍ قد لا تكون ما نُفّذ."""
    app = _PendingApp(["python3", "-c", "print(2+2)"])
    monkeypatch.setattr(j5, "LocalApp", lambda *a, **k: app)
    _attest(monkeypatch)
    real = j5._sources()
    seen = iter([real, {**real, "tools/probe_j5_agent_round.py": "0" * 64}])
    monkeypatch.setattr(j5, "_sources", lambda: next(seen))
    out = tmp_path / "r.json"
    assert j5.main(["--model", "m", "--web-search-url", "http://127.0.0.1:8888",
                    "--runtime-receipt", str(_ui_receipt(tmp_path)), "--out", str(out)]) == 2
    refused = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert refused == {"status": "refused", "code": "sources_changed_during_the_run",
                       "changed": ["tools/probe_j5_agent_round.py"]}
    assert not out.exists()


@pytest.mark.parametrize("after, digests, changed", [
    ({"started_at": "2026-09-27T03:05:00Z"}, None, ["searxng_container"]),
    ({"id": "", "image": "", "image_id": "", "running": False, "started_at": "", "serves_url": False}, None,
     ["searxng_container"]),
    ({"serves_url": False}, None, ["searxng_container"]),
    ({}, ["sha256:weights", "sha256:repulled"], ["engine_digest"]),
])
def test_a_searxng_container_or_engine_that_changes_during_the_round_writes_no_report(tmp_path, monkeypatch, capsys,
                                                                                      after, digests, changed):
    """ملاحظةُ Codex على #144: الحاويةُ كانت تُفحص باسمها قبل الجولة وحدها، فحاويةٌ أُعيد تشغيلُها أو استُبدلت في أثنائها
    يشهد لها التقريرُ بالصورة المثبَّتة. فتُفحص بعدها بمعرّفها، ومعها بصمةُ النموذج."""
    app = _PendingApp(["python3", "-c", "print(2+2)"])
    monkeypatch.setattr(j5, "LocalApp", lambda *a, **k: app)
    asked = []
    _attest(monkeypatch, serving=lambda url, container_id, docker: asked.append(container_id) or {**PIN, **after},
            digests=digests)
    out = tmp_path / "r.json"
    assert j5.main(["--model", "m", "--web-search-url", "http://127.0.0.1:8888",
                    "--runtime-receipt", str(_ui_receipt(tmp_path)), "--out", str(out)]) == 2
    refused = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert refused == {"status": "refused", "code": "sources_changed_during_the_run", "changed": changed}
    assert asked == [PIN["id"]] and not out.exists()


def test_every_repo_module_the_round_loads_is_hashed_not_a_hand_picked_list():
    """ملاحظةُ Codex على #144: القائمةُ المنتقاة فاتها مهايئا المزوّد اللذان يبنيان طلبَ النموذج ويقرآن نداءات الأدوات؛
    فصار كلُّ ما حُمّل من المستودع يُبصم، ولا يُبصم ما ليس منه (الاختبارات والحزم المثبَّتة)."""
    hashed = j5._sources()
    assert set(j5.SOURCES) <= set(hashed)
    assert {"providers/local_tools.py", "providers/local_chat.py"} <= set(hashed)
    import sys
    loaded = {Path(m.__file__).resolve().relative_to(ROOT).as_posix() for m in list(sys.modules.values())
              if isinstance(getattr(m, "__file__", None), str) and m.__file__.endswith(".py")
              and Path(m.__file__).resolve().is_relative_to(ROOT)}
    assert {p for p in loaded if p.startswith(("core/", "agent/", "providers/", "webui/"))} <= set(hashed)
    assert not [p for p in hashed if p.startswith("tests/") or "site-packages" in p]


def test_a_receipt_replaced_during_the_round_or_unreadable_writes_no_report(tmp_path, monkeypatch, capsys):
    """الإيصالُ يُقرأ قبل الجولة ويُعاد قبل الكتابة كما في مجسّ الحدّ: إيصالٌ صالحٌ آخر يُكتب في أثنائها لا يُنسب إليه الدليل."""
    receipt = _ui_receipt(tmp_path)

    class _Swapping(_PendingApp):
        def dispatch(self, request):
            if request["action"] == "agent_resume":
                _ui_receipt(tmp_path, image_id="sha256:" + "d" * 64)
            return super().dispatch(request)

    app = _Swapping(["python3", "-c", "print(2+2)"])
    monkeypatch.setattr(j5, "LocalApp", lambda *a, **k: app)
    _attest(monkeypatch)
    out = tmp_path / "r.json"
    args = ["--model", "m", "--web-search-url", "http://127.0.0.1:8888", "--runtime-receipt", str(receipt),
            "--out", str(out)]
    assert j5.main(args) == 2
    refused = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert refused["code"] == "sources_changed_during_the_run" and refused["changed"] == ["runtime_receipt"]
    assert not out.exists()
    receipt.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(j5, "LocalApp", lambda *a, **k: pytest.fail("جولةٌ بإيصالٍ لا يُقرأ"))
    assert j5.main(args) == 2
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["code"] == "runtime_receipt_unreadable"


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
                    "--runtime-receipt", str(_ui_receipt(tmp_path)), "--out", str(out)])
    assert code == 2 and json.loads(capsys.readouterr().out)["code"] == "run_command_backend_uses_the_default_docker_path"
    assert not out.exists()
    assert j5.EXECUTION_DOCKER == boundary.SANDBOX_DOCKER == "/usr/local/bin/docker"


def test_a_round_where_the_model_never_searches_is_written_as_failed_and_exits_non_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(j5, "LocalApp", _SilentApp)
    _attest(monkeypatch)
    out = tmp_path / "r.json"
    assert j5.main(["--model", "m", "--web-search-url", "http://127.0.0.1:8888",
                    "--runtime-receipt", str(_ui_receipt(tmp_path)), "--out", str(out)]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["acceptance"]["passed"] is False
    assert "web_search_never_succeeded" in report["acceptance"]["failed"]


def _docker(image: str, bindings: list[dict], running: str = "true", asked: list | None = None,
            started: str = "2026-09-27T03:00:00Z", container_id: str = "c" * 64):
    """Docker يعرف حاويةً واحدة: الاسمُ «searxng» يُحلّ إلى معرّفها، وما سواه يُسأل بالمعرّف."""

    def run(argv, **kwargs):
        assert argv[1:3] == ["container", "inspect"]
        fmt, target = argv[argv.index("--format") + 1], argv[-1]
        if asked is not None:
            asked.append((fmt, target))
        if target not in ("searxng", container_id):
            return SimpleNamespace(stdout="", returncode=1)
        out = {"{{.Id}}": container_id, "{{.Config.Image}}": image, "{{.Image}}": "sha256:" + "a" * 64,
               "{{.State.Running}}": running, "{{.State.StartedAt}}": started,
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
    monkeypatch.setattr(subprocess, "run", _docker(j5.PINNED_SEARXNG, local, running="false"))
    with pytest.raises(SystemExit) as refused:
        j5._searxng("http://127.0.0.1:8888", "searxng", "docker")
    assert json.loads(str(refused.value))["code"] == "searxng_container_does_not_serve_the_url"
    with pytest.raises(SystemExit) as refused:
        j5._searxng("http://127.0.0.1:8888", "other", "docker")
    assert json.loads(str(refused.value))["code"] == "searxng_container_not_found"
    asked = []
    monkeypatch.setattr(subprocess, "run", _docker(j5.PINNED_SEARXNG, local, asked=asked))
    backend, pinned = j5._searxng("http://127.0.0.1:8888", "searxng", "docker")
    assert backend == {"url": "http://127.0.0.1:8888", "container": "searxng", "image": j5.PINNED_SEARXNG,
                       "image_id": "sha256:" + "a" * 64}
    assert pinned["id"] == "c" * 64 and pinned["running"] and pinned["serves_url"]
    # الاسمُ يُسأل مرّةً واحدة عن معرّفه، وكلُّ ما بعده بالمعرّف (ملاحظة Codex على #144)
    assert asked[0] == ("{{.Id}}", "searxng") and all(target == "c" * 64 for _, target in asked[1:])
    assert j5._serving("http://127.0.0.1:8888", "c" * 64, "docker") == pinned
    # وبعد الجولة: حاويةٌ أُعيد تشغيلُها، أو أُزيلت وحلّت محلَّها أخرى بالاسم نفسِه، أو كفّت عن النشر، لا تُقرأ كما كانت
    for later in (_docker(j5.PINNED_SEARXNG, local, started="2026-09-27T03:05:00Z"),
                  _docker(j5.PINNED_SEARXNG, local, container_id="d" * 64),
                  _docker(j5.PINNED_SEARXNG, local, running="false"),
                  _docker(j5.PINNED_SEARXNG, [])):
        monkeypatch.setattr(subprocess, "run", later)
        assert j5._serving("http://127.0.0.1:8888", "c" * 64, "docker") != pinned


BOUNDARY = ROOT / "docs" / "probe" / "execution-boundary-20260927d.json"
SANDBOX = ROOT / "docs" / "probe" / "sandbox-container-20260927d.json"


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
        # الجهازُ من المضيف نفسِه لا نصٌّ ثابت (ملاحظة Codex على #144)
        assert report["host"]["machine"] == os.uname().machine


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
