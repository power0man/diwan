"""فحصُ الدخان يسمّي ما يعمل وما تعذّر وما عطب، ولا يخلط بينها (ك٢٨).

بلا محرّكٍ حيّ هنا: يُحاكى خادمُ Ollama بدالّةِ قراءةٍ مزيّفة، وتُشغَّل الجولةُ الوكيلة بالمزوّد
المكتوب سلفًا فتُثبت الحلقةَ والأدوات، وتُقرأ عقدةُ السياسات على حال هذه النسخة. والحدُّ معلَن:
الجولةُ الحيّة بالمحرّك لا تُختبر هنا.
"""
from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import launch_check as lc  # noqa: E402


def _tags_with(*names):
    return lambda base_url: {"models": [{"name": n, "digest": "d" * 64} for n in names]}


def _down(base_url):
    raise urllib.error.URLError("connection refused")


def test_runtime_is_ready_here():
    step = lc.check_runtime(ROOT)
    assert step.status == "ok" and step.code.startswith("runtime_ready_")


def test_engine_states_are_named():
    assert lc.check_engine("qwen3:14b", "http://127.0.0.1:1", probe=_down).code == "engine_unreachable"
    missing = lc.check_engine("qwen3:14b", "http://x", probe=_tags_with("llama3:8b"))
    assert missing.status == "unavailable" and missing.code == "model_missing" and "ollama pull qwen3:14b" in missing.detail
    ready = lc.check_engine("qwen3:14b", "http://x", probe=_tags_with("qwen3:14b", "llama3:8b"))
    assert ready.status == "ok" and ready.code == "engine_ready"


def test_the_mechanism_turn_reads_the_file_through_the_governed_loop():
    step = lc.check_agent_turn("qwen3:14b", "http://x", live=False)
    assert step.status == "ok" and step.code == "mechanism_only", step
    assert lc.NOTE_TEXT in step.detail


def test_morphology_names_what_is_missing_and_is_never_ready_without_the_database():
    """ق٥٥: CAMeL لازمٌ للإطلاق — غيابُه أو غيابُ قاعدته تعذّرٌ معلن يسمّي أمرَه."""
    missing = lc.check_morphology(installed=lambda: False)
    assert missing.status == "unavailable" and missing.code == "camel_missing"
    assert lc.CAMEL_INSTALL in missing.detail and lc.CAMEL_DB_COMMAND in missing.detail
    no_db = lc.check_morphology(installed=lambda: True, analyzer=None)
    assert no_db.status == "unavailable" and no_db.code == "camel_db_missing"
    assert lc.CAMEL_DB_COMMAND in no_db.detail
    ready = lc.check_morphology(installed=lambda: True, analyzer=object())
    assert ready.status == "ok" and ready.code == "camel_ready"


def test_morphology_here_reports_the_real_state_of_this_machine():
    from core.linguistics.roots import camel_available
    step = lc.check_morphology()
    assert step.code in ("camel_missing", "camel_db_missing", "camel_ready"), step
    assert (step.status == "ok") == camel_available()


def test_a_symlinked_temporary_directory_is_resolved_before_the_journal_guard(tmp_path, monkeypatch):
    """عطبٌ كشفه التشغيلُ الحيّ على الماك (٢٥ سبتمبر): `tempfile` يعطي مسارًا تحت `/var` وهو رابطٌ
    رمزي إلى `/private/var`، فيرفضه حارسُ دفتر الرجوع (`unsafe_path`) — عطبُ ك٨ نفسُه."""
    import contextlib
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    @contextlib.contextmanager
    def linked_tmp(prefix=""):
        yield str(link)

    monkeypatch.setattr(lc.tempfile, "TemporaryDirectory", linked_tmp)
    step = lc.check_agent_turn("qwen3.5:9b", "http://x", live=False)
    assert step.status == "ok" and step.code == "mechanism_only", step


def test_a_model_that_answers_without_the_tool_is_a_failure(monkeypatch):
    class Silent(lc._Mechanism):
        def complete(self, request):
            from core.contracts import Response, Usage
            return Response("جوابٌ بلا قراءة", Usage(1, 1), "complete", 0, provider=self.name,
                            model_version="v1", tool_calls=())
    monkeypatch.setattr(lc, "_Mechanism", Silent)
    step = lc.check_agent_turn("qwen3:14b", "http://x", live=False)
    assert step.status == "failed" and step.code == "tool_not_used"


def test_policies_reflect_whether_the_corpus_is_placed():
    step = lc.check_policies(ROOT)
    if (ROOT / "corpus" / "maritime" / "_catalog.jsonl").is_file():
        assert step.code in ("policies_ready", "index_missing"), step
    else:
        assert step.status == "unavailable" and step.code == "corpus_missing"
        assert "place_private_stores" in step.detail


def test_exit_codes_separate_failed_unavailable_and_ready():
    ok = lc.Step("x", "ok", "c")
    un = lc.Step("y", "unavailable", "c")
    bad = lc.Step("z", "failed", "c")
    assert lc.exit_code([ok, ok]) == 0
    assert lc.exit_code([ok, un]) == 3
    assert lc.exit_code([ok, un, bad]) == 1


def test_run_checks_without_an_engine_is_a_declared_unavailability_not_a_failure():
    steps = lc.run_checks(ROOT, engine="qwen3:14b", base_url="http://127.0.0.1:1", probe=_down,
                          with_ui=False)
    by = {s.step: s for s in steps}
    assert [s.step for s in steps] == ["runtime", "morphology", "engine", "agent_turn", "policies"]
    assert by["runtime"].status == "ok"
    assert by["morphology"].status in ("ok", "unavailable")
    assert by["engine"].code == "engine_unreachable"
    assert by["agent_turn"].code == "mechanism_only", "بلا محرّكٍ تُثبَت الحلقةُ بالمزوّد الآلي"
    assert lc.exit_code(steps) == 3


# Ollama مزيّف على منفذٍ زائل؛ ومزوّدُ الواجهة يتّصل بـ127.0.0.1:11434 حرفيًّا، فيُحوَّل ذلك العنوانُ وحده إليه في الاختبار
MODEL, DIGEST = "qwen3.5:9b", "a" * 64


def _ollama(**change):
    entry = {"name": MODEL, "model": MODEL, "digest": DIGEST, "size": 6_600_000_000, "details": {"format": "gguf"}}
    show = {"details": {"format": "gguf"}, "capabilities": ["completion", "tools"],
            "model_info": {"general.architecture": "qwen35", "qwen35.context_length": 262144}}
    chat = {"model": MODEL, "done": True, "done_reason": "stop", "message": {"role": "assistant", "content": "نعم"},
            "prompt_eval_count": 9, "eval_count": 1}
    entry.update(change.get("entry", {}))
    show.update(change.get("show", {}))
    chat.update(change.get("chat", {}))
    return {"/api/tags": {"models": [entry]}, "/api/show": show, "/api/chat": chat}


@pytest.fixture
def fake_ollama(monkeypatch):
    import http.client
    import http.server
    import json as _json
    import threading
    served = {"routes": _ollama()}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self):
            route = self.path
            if self.command == "POST":
                sent = _json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if route == "/api/chat" and "tools" in sent and "/api/chat+tools" in served["routes"]:
                    route = "/api/chat+tools"      # ردٌّ آخر للطلب الذي يحمل أدوات وحده
            body = _json.dumps(served["routes"].get(route, {}), ensure_ascii=False).encode("utf-8")
            self.send_response(served["routes"].get("status", {}).get(route, 200) if route in served["routes"] else 404)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        do_GET = do_POST = answer

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    class Redirected(http.client.HTTPConnection):     # صنفٌ فرعيّ: يبقى ما يستعمله urllib من الصنف كما هو
        def __init__(self, host, port=None, **kwargs):
            if (host, port) == ("127.0.0.1", 11434):
                port = server.server_port
            super().__init__(host, port, **kwargs)
    monkeypatch.setattr(http.client, "HTTPConnection", Redirected)
    yield served
    server.shutdown()
    server.server_close()


def test_the_ui_entry_point_initialises(fake_ollama):
    """خطوةُ الواجهة تشغّل `serve_ui.py` الحقيقيّ على منفذٍ زائل وتقرأ الصفحةَ ونداءَ projects ثم توقفه.
    بلا محرّكٍ تُسمّي بصمتَها البديلة؛ وبمحرّكٍ وبصمته `ui_ready` بعد أن يمرّ فحصُ مزوّدَي الواجهة."""
    placeholder = lc.check_ui(ROOT)
    assert placeholder.status == "ok" and placeholder.code == "ui_ready_without_engine", placeholder
    ready = lc.check_ui(ROOT, model="qwen3.5:9b", digest="a" * 64)
    assert ready.status == "ok" and ready.code == "ui_ready", ready
    assert "qwen3.5:9b" in ready.detail and "dir=rtl" in ready.detail


def test_a_digest_serve_ui_refuses_is_a_named_start_failure():
    """البصمةُ تصل إلى serve_ui.py كما هي: بصمةٌ يرفضها المزوّدُ المحليّ تُسقط التشغيلَ باسمه، لا تُستبدل بالبديلة."""
    step = lc.check_ui(ROOT, model="qwen3.5:9b", digest="not-a-digest")
    assert step.status == "failed" and step.code == "ui_start_failed", step
    assert "local_chat_artifact_invalid" in step.detail


# بديلٌ عن serve_ui.py يسلك سلوكًا واحدًا يختاره FAKE_UI_MODE، ويُنهي نفسَه بعد مهلة فلا يبقى إن نجت طفرةُ الإيقاف
FAKE_UI = r'''
import http.server, json, os, sys, threading, time
guard = threading.Timer(30, lambda: os._exit(9))
guard.daemon = True
guard.start()
mode = os.environ["FAKE_UI_MODE"]
if os.environ.get("FAKE_UI_PID"):
    with open(os.environ["FAKE_UI_PID"], "w") as handle:
        handle.write(str(os.getpid()))
if mode == "exit":
    print("startup_failed", file=sys.stderr, flush=True)
    sys.exit(2)
if mode == "silent":
    time.sleep(60)
TOKEN = "" if mode == "notoken" else '<meta name="diwan-token" content="' + "a" * 64 + '">'
PAGE = '<!doctype html><html lang="ar" dir="' + ("ltr" if mode == "ltr" else "rtl") + '"><head>' + TOKEN + "</head></html>"
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def answer(self, status, body, kind):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        self.answer(500 if mode == "get500" else 200, PAGE, "text/html; charset=utf-8")
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        body = {"items": []} if mode == "apishape" else {"projects": []}
        self.answer(403 if mode == "api403" else 200, json.dumps(body), "application/json")
server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
print("ديوان المحلي: http://127.0.0.1:%d" % server.server_port, flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
'''


def _fake_ui(tmp_path, monkeypatch, mode, **kwargs):
    script = tmp_path / "fake_serve_ui.py"
    script.write_text(FAKE_UI, encoding="utf-8")
    pid = tmp_path / "fake.pid"
    monkeypatch.setenv("FAKE_UI_MODE", mode)
    monkeypatch.setenv("FAKE_UI_PID", str(pid))
    step = lc.check_ui(ROOT, model="qwen3.5:9b", digest="a" * 64, script=script, **kwargs)
    return step, pid


def _alive(pid_file) -> bool:
    import os
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:   # عمليةٌ انتهت ولم تُحصد بعد (zombie) ليست خادمًا حيًّا
        return Path(f"/proc/{pid}/status").read_text().find("State:\tZ") < 0
    except OSError:
        return True


def test_a_server_that_exits_before_announcing_itself_is_a_start_failure(tmp_path, monkeypatch):
    step, _ = _fake_ui(tmp_path, monkeypatch, "exit", timeout_s=20)
    assert step.status == "failed" and step.code == "ui_start_failed", step
    assert "برمز 2" in step.detail and "startup_failed" in step.detail


def test_a_server_that_never_announces_itself_times_out_and_is_stopped(tmp_path, monkeypatch):
    step, pid = _fake_ui(tmp_path, monkeypatch, "silent", timeout_s=1.5)
    assert step.status == "failed" and step.code == "ui_start_failed", step
    assert "1.5" in step.detail
    assert not _alive(pid), "بقي الخادمُ بعد انتهاء المهلة"


def test_a_page_error_is_ui_http_failed(tmp_path, monkeypatch):
    step, _ = _fake_ui(tmp_path, monkeypatch, "get500")
    assert step.status == "failed" and step.code == "ui_http_failed" and "500" in step.detail, step


def test_a_page_that_is_not_right_to_left_is_unexpected(tmp_path, monkeypatch):
    step, _ = _fake_ui(tmp_path, monkeypatch, "ltr")
    assert step.status == "failed" and step.code == "ui_page_unexpected" and "rtl" in step.detail, step


def test_a_page_without_its_session_token_is_unexpected(tmp_path, monkeypatch):
    step, _ = _fake_ui(tmp_path, monkeypatch, "notoken")
    assert step.status == "failed" and step.code == "ui_page_unexpected", step


def test_a_refused_read_call_is_ui_http_failed(tmp_path, monkeypatch):
    step, _ = _fake_ui(tmp_path, monkeypatch, "api403")
    assert step.status == "failed" and step.code == "ui_http_failed" and "403" in step.detail, step


def test_a_read_call_without_a_project_list_is_unexpected(tmp_path, monkeypatch):
    step, _ = _fake_ui(tmp_path, monkeypatch, "apishape")
    assert step.status == "failed" and step.code == "ui_page_unexpected" and "projects" in step.detail, step


def test_the_ui_step_stops_the_server_it_started(tmp_path, monkeypatch, fake_ollama):
    step, pid = _fake_ui(tmp_path, monkeypatch, "ok")
    assert step.status == "ok" and step.code == "ui_ready", step
    assert not _alive(pid), "بقي خادمُ الواجهة يعمل بعد الخطوة"


def test_a_model_the_ui_providers_refuse_is_not_ui_ready(tmp_path, monkeypatch, fake_ollama):
    """ملاحظة Codex الثامنة على #175: /api/tags يسرد الاسمَ ببصمةٍ صالحة لكن مزوّدَ الواجهة يرفض النموذج (حجمٌ غيرُ صالح،
    نموذجٌ بعيد، /api/show بلا قدرة tools أو بسياقٍ أصغر، جوابُ محادثةٍ مشوّه)، والجولةُ الحيّة بمزوّدٍ أرخى؛ فكان «جاهز»
    وأولُ جوابٍ في الواجهة يُرفض. صارت الخطوةُ تمرّ بفحص مزوّدَي الواجهة المسبق وجوابٍ قصير قبل «جاهز»."""
    cases = {
        "local_chat_artifact_invalid": _ollama(entry={"size": 0}),
        "local_chat_remote_model": _ollama(entry={"remote_host": "https://ollama.com"}),
        "local_tools_capability_missing": _ollama(show={"capabilities": ["completion"]}),
        "local_chat_context_unsupported": _ollama(show={"model_info": {"general.architecture": "qwen35",
                                                                       "qwen35.context_length": 2048}}),
        "local_tools_malformed": _ollama(chat={"done": False}),
        # المحادثةُ بالأدوات سليمة والنصّيةُ وحدها مشوّهة: يلزم الجوابُ عبر مزوّد النصّ أيضًا
        "local_chat_malformed": {**_ollama(chat={"done": False}), "/api/chat+tools": _ollama()["/api/chat"]},
    }
    for provider_code, routes in cases.items():
        fake_ollama["routes"] = routes
        step, _ = _fake_ui(tmp_path, monkeypatch, "ok")
        assert (step.status, step.code) == ("failed", "ui_engine_refused"), (provider_code, step)
        assert provider_code in step.detail, (provider_code, step.detail)
    fake_ollama["routes"] = _ollama()
    step, _ = _fake_ui(tmp_path, monkeypatch, "ok")
    assert (step.status, step.code) == ("ok", "ui_ready"), step


def test_a_server_that_refuses_tool_bearing_chat_is_not_ui_ready(tmp_path, monkeypatch, fake_ollama):
    """ملاحظة Codex التاسعة على #175: خادمٌ يعلن tools ويقبل المحادثةَ النصّية ويرفض /api/chat حين يحمل الطلبُ أدوات: الفحصُ
    المسبق والجوابُ النصّيّ يمرّان، وأولُ جولةٍ وكيلة (الافتراضُ في الواجهة) ترفض. صار جوابٌ قصير عبر LocalToolProvider.complete()
    بأداةٍ فارغة قبل «جاهز»."""
    for name, change in (("http_error", {"status": {"/api/chat+tools": 400}}),
                         ("malformed", {})):
        routes = _ollama()
        routes["/api/chat+tools"] = {"error": "tools unsupported"} if name == "http_error" else {"model": MODEL, "done": False}
        routes.update(change)
        fake_ollama["routes"] = routes
        step, _ = _fake_ui(tmp_path, monkeypatch, "ok")
        assert (step.status, step.code) == ("failed", "ui_engine_refused"), (name, step)
        assert "local_" in step.detail, (name, step.detail)


def test_run_checks_hands_the_engine_and_its_digest_to_the_ui_step():
    """الواجهةُ تُشغَّل بالمحرّك الذي وجدته خطوةُ المحرّك وبصمتِه كما في /api/tags؛ وبلا محرّكٍ بلا بصمة."""
    seen = []

    def ui_check(root, *, model, digest, base_url):
        seen.append((model, digest, base_url))
        return lc.Step("ui", "ok", "ui_ready")

    tags = lambda base_url: {"models": [{"name": "qwen3.5:9b", "digest": "b" * 64}]}
    lc.run_checks(ROOT, engine="qwen3.5:9b", base_url="http://x:1", probe=tags, with_agent=False, ui_check=ui_check)
    lc.run_checks(ROOT, engine="qwen3.5:9b", base_url="http://x:2", probe=_down, with_agent=False, ui_check=ui_check)
    assert seen == [("qwen3.5:9b", "b" * 64, "http://x:1"), ("qwen3.5:9b", None, "http://x:2")]


def test_an_engine_probed_elsewhere_is_not_a_ready_ui(tmp_path, monkeypatch):
    """ملاحظة Codex على #175: serve_ui.py يبلغ Ollama على 127.0.0.1:11434 وحده، ونداءُ القراءة لا يبلغ النموذج؛ فمحرّكٌ فُحص
    على منفذٍ آخر لا تُسمّى معه الواجهةُ جاهزة وإن خدمت صفحتَها."""
    step, _ = _fake_ui(tmp_path, monkeypatch, "ok", base_url="http://127.0.0.1:11500")
    assert step.status == "unavailable" and step.code == "ui_engine_endpoint_unpassed", step
    assert "http://127.0.0.1:11500" in step.detail
    assert lc.reaches_ui_engine("http://localhost:11434") and lc.reaches_ui_engine(lc.DEFAULT_BASE_URL)
    for other in ("http://127.0.0.1:11500", "http://192.168.1.5:11434", "https://127.0.0.1:11434", "http://127.0.0.1"):
        assert not lc.reaches_ui_engine(other), other


def test_only_the_exact_endpoint_the_ui_reaches_counts_as_the_ui_engine():
    """ملاحظة Codex الخامسة على #175: `http://localhost:11434/ollama` مرّ لأن الموازنةَ لم تنظر في المسار، والمحرّكُ فُحص على
    /ollama/api/… والمزوّدُ يبلغ /api/…. صارت الموازنةُ موجبةً كاملة بالعنوان الذي في شيفرة المزوّد نفسِه."""
    assert lc.ui_engine_endpoint() == ("127.0.0.1", 11434), "العنوانُ من HTTPConnection في providers/local_chat.py"
    for same in ("http://localhost:11434", "http://127.0.0.1:11434/", "HTTP://LOCALHOST:11434"):
        assert lc.reaches_ui_engine(same), same
    for other in ("http://localhost:11434/ollama", "http://127.0.0.1:11434/?x=1", "http://127.0.0.1:11434#f",
                  "http://u:p@127.0.0.1:11434", "http://u@127.0.0.1:11434", "http://127.0.0.1:11500",
                  "https://127.0.0.1:11434", "http://127.0.0.1"):
        assert not lc.reaches_ui_engine(other), other
    # العنوانُ يُقرأ من شيفرة المزوّد لا من نسخة: غيرُه يغيّر الحكم، وعنوانٌ غيرُ حرفيّ (#176) لا يُدّعى
    other_source = 'conn = http.client.HTTPConnection("10.1.2.3", 9999, timeout=t)'
    assert lc.ui_engine_endpoint(other_source) == ("10.1.2.3", 9999)
    assert lc.reaches_ui_engine("http://10.1.2.3:9999", ("10.1.2.3", 9999))
    assert not lc.reaches_ui_engine("http://localhost:11434", ("10.1.2.3", 9999))
    assert lc.ui_engine_endpoint("conn = http.client.HTTPConnection(host, port, timeout=t)") is None
    mixed = 'a = http.client.HTTPConnection("127.0.0.1", 11434)\nb = http.client.HTTPConnection(host, port)'
    assert lc.ui_engine_endpoint(mixed) is None, "مسارٌ مضبوطٌ بجانب الحرفيّ فلا يُدّعى عنوانٌ واحد"


def test_a_model_without_a_valid_artifact_digest_is_a_named_failure():
    """ملاحظة Codex الخامسة على #175: محرّكٌ مسرودٌ بلا بصمة كان «جاهزًا» فتُشغَّل الواجهةُ ببصمةٍ بديلة ويخرج الفحصُ 0، وأولُ
    جوابٍ يرفضه الفحصُ المسبق في المزوّد. البصمةُ بقاعدة المزوّد نفسِه وإلا engine_metadata_invalid وخروجٌ غيرُ صفر."""
    def tags(**entry):
        return lambda base_url: {"models": [{"name": "qwen3.5:9b", **entry}]}
    for entry in ({}, {"digest": None}, {"digest": ""}, {"digest": "a" * 63}, {"digest": "g" * 64}, {"digest": "A" * 64},
                  {"digest": "sha256:" + "a" * 64}, {"digest": 5}, {"digest": True}, {"digest": []}):
        step, digest = lc.locate_engine("qwen3.5:9b", "http://x", probe=tags(**entry))
        assert (step.status, step.code, digest) == ("failed", "engine_metadata_invalid", None), entry
    step, digest = lc.locate_engine("qwen3.5:9b", "http://x", probe=tags(digest="a" * 64))
    assert (step.status, step.code, digest) == ("ok", "engine_ready", "a" * 64)
    ui = lambda root, **kwargs: lc.Step("ui", "ok", "ui_ready_without_engine")
    steps = lc.run_checks(ROOT, engine="qwen3.5:9b", base_url="http://x", probe=tags(digest=""), with_agent=False,
                          ui_check=ui)
    assert lc.exit_code(steps) == 1, "بصمةٌ غائبة لا تنتهي بخروجٍ صفر"


def test_malformed_engine_metadata_is_named_with_a_complete_report():
    """ملاحظة Codex السابعة على #175: بصمةٌ رقمية أو منطقية أو قائمة كانت ترمي TypeError في فرع التفصيل (len)، فيخرج الفحصُ بلا
    تقريره. وكذلك ردٌّ ليس قاموسًا أو models فيه ليست قائمة أو اسمٌ غيرُ نصّيّ. كلُّها engine_metadata_invalid وتقريرٌ كامل."""
    ui = lambda root, **kwargs: lc.Step("ui", "ok", "ui_ready_without_engine")
    for tags in ({"models": [{"name": "qwen3.5:9b", "digest": 5}]}, {"models": [{"name": "qwen3.5:9b", "digest": True}]},
                 {"models": [{"name": "qwen3.5:9b", "digest": []}]}, [], {"models": "qwen3.5:9b"}, {"models": None}):
        steps = lc.run_checks(ROOT, engine="qwen3.5:9b", base_url="http://x", probe=lambda base_url, t=tags: t,
                              with_agent=False, ui_check=ui)
        assert [s.step for s in steps] == ["runtime", "morphology", "engine", "policies", "ui"], tags
        engine = steps[2]
        assert (engine.status, engine.code) == ("failed", "engine_metadata_invalid"), tags
        assert lc.exit_code(steps) == 1
    odd_names = {"models": [{"name": ["qwen3.5:9b"]}, {"name": 7}, {"name": "qwen3.5:9b", "digest": "a" * 64}]}
    assert lc.locate_engine("qwen3.5:9b", "http://x", probe=lambda base_url: odd_names)[0].code == "engine_ready"


def test_trimmed_details_say_that_they_are_trimmed():
    """لا قصَّ صامت في تفاصيل الفحص: النصُّ المقصوص ينتهي بـ«…»، وقائمةُ النماذج تذكر عددَ ما لم يُعرض."""
    assert lc._clip("ا" * 300).endswith("…") and len(lc._clip("ا" * 300)) == 200
    assert lc._clip("قصير") == "قصير"
    many = {"models": [{"name": f"m{i}", "digest": "a" * 64} for i in range(10)]}
    step = lc.check_engine("absent", "http://x", probe=lambda base_url: many)
    assert step.code == "model_missing" and "و4 غيرها" in step.detail


def test_a_journal_that_refuses_its_root_is_a_named_failure_not_a_traceback(monkeypatch):
    """رفضُ الدفتر (أو أيُّ عطبٍ قبل الجولة) يُسمّى `agent_turn_raised` في التقرير ولا يقطع الفحص.

    الطفرةُ التي تقتله: إخراجُ إنشاء `Journal` من `try` في `check_agent_turn`.
    """
    import agent.journal as journal_module

    def refuse(root):
        raise journal_module.JournalRefused("unsafe_path", "مُصطنَع")
    monkeypatch.setattr(journal_module, "Journal", refuse)
    step = lc.check_agent_turn("qwen3:14b", "http://x", live=False)
    assert step.status == "failed" and step.code == "agent_turn_raised"
    assert "JournalRefused" in step.detail


def test_a_windows_arabic_console_prints_the_report_instead_of_crashing(monkeypatch):
    """على Nitro انهار الفحصُ بـUnicodeEncodeError عند «✗» على طرفية cp1256 قبل أن يطبع سطرًا (#64)."""
    import io
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1256", errors="strict")
    monkeypatch.setattr(lc.sys, "stdout", console)
    monkeypatch.setattr(lc, "run_checks", lambda *a, **k: [
        lc.Step("runtime", "failed", "import_failed", "ModuleNotFoundError: No module named 'fcntl' ✓")])
    assert lc.main([]) == 1
    console.flush()
    text = raw.getvalue().decode("cp1256")
    assert "  x runtime: import_failed" in text and "x عطب: يُصلَح قبل الإطلاق." in text


def test_a_utf8_console_keeps_the_symbols():
    assert lc.marks_for("utf-8") == lc.MARKS and lc.marks_for("cp1256") == lc.ASCII_MARKS
    assert lc.marks_for("no-such-codec") == lc.ASCII_MARKS
