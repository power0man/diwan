"""فحصُ الواجهة في المتصفّح (tools/ui_browser_audit.py): ما يُختبر هنا بلا متصفّح.

المتصفّحُ لا يعمل في CI، فلا يُشغَّل السائقُ (`tools/ui_browser_audit.cjs`) هنا. يُختبر المنطقُ الخالص الذي يحوّل ما رآه
المتصفّحُ إلى دليل: تغطيةُ رموز الرفض، والنتائجُ من الملاحظات، وحارسُ الدليل، وتلخيصُ الخطوات؛ ويُختبر الخادمُ الحقيقيّ
بالمزوّد المكتوب عبر HTTP كما يراه المتصفّح، فرحلةُ الفحص تقوم على ما يعمل. والحدُّ معلَن: الرحلةُ نفسُها تُشغَّل يدويًّا.
"""
from __future__ import annotations

import http.client
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import ui_browser_audit as audit  # noqa: E402


# — تغطيةُ رموز الرفض —

SERVER = '''
def check(value):
    need(value.get("mode", "text") in MODES, "metadata_invalid")
    need(len(value) < 3, "collection_limit")  # تعليق
    need(ok, "turn_missing")
    raise UIError("id_invalid")
'''
APP = '''const errors = {
  collection_limit: "بلغت المشروعاتُ حدَّها.",
};
if(["turn_missing"].includes(e.code)) notice("لم تُسجَّل الجولة.");
'''


def test_error_coverage_reads_the_code_as_the_last_argument_and_counts_handled_branches():
    coverage = audit.error_code_coverage(SERVER, APP)
    assert coverage["server_codes"] == 4, coverage          # لا "text" من وسيطٍ داخليّ
    assert coverage["with_arabic_message"] == 1
    assert coverage["handled_by_a_branch"] == ["turn_missing"]
    assert coverage["raw_codes"] == ["id_invalid", "metadata_invalid"]


# — النتائجُ من ملاحظات المتصفّح —

ALL_OBSERVED = {
    "raw_error_codes_visible": ["name_invalid"], "send_before_session_refused": True,
    "disabled_session_modes": ["research"], "disabled_modes_explained": False,
    "composer_in_first_screen_mobile": False, "ctrl_enter_sends": False, "enter_sends": False,
    "answer_markdown_literal": True, "agent_stream_latin": ["write_file"], "messages_live": False,
    "answer_announced": False, "unnamed_controls": [{"state": "remember-dialog", "role": "textbox"}],
    "hashes": [{"arabic_label": True, "direction": "ltr"}], "composer_position": "sticky",
    "composer_viewport_share": 0.4, "thinking_checkbox_width_px": 943,
    "dialog_focus": {"propose": True, "review": False}, "horizontal_scroll_390": True,
    "tab_order": [{"focus_visible": False}], "console_errors": [{"type": "error", "text": "x"}],
}
NONE_OBSERVED = {
    "raw_error_codes_visible": [], "send_before_session_refused": False, "disabled_session_modes": [],
    "composer_in_first_screen_mobile": True, "ctrl_enter_sends": False, "enter_sends": True,
    "answer_markdown_literal": False, "agent_stream_latin": [], "approval_button_label": "مراجعة فعل",
    "messages_live": True, "answer_announced": True, "unnamed_controls": [], "mobile_unnamed_controls": [],
    "hashes": [{"arabic_label": False, "direction": "ltr"}, {"arabic_label": True, "direction": "rtl"}],
    "composer_position": "static", "composer_viewport_share": 0.4, "thinking_checkbox_width_px": 20,
    "dialog_focus": {"propose": True, "review": True}, "horizontal_scroll_390": False,
    "horizontal_overflow_390_after_answer_px": 0, "tab_order": [{"focus_visible": True}], "console_errors": [],
}


def test_every_rule_fires_on_what_it_names_and_stays_silent_otherwise():
    fired = {f["id"] for f in audit.derive_findings(ALL_OBSERVED, {"raw_codes": []}, None, {})}
    assert fired == {rule[0] for rule in audit.FINDING_RULES}
    assert audit.derive_findings(NONE_OBSERVED, {"raw_codes": []}, None, {}) == []


def test_a_single_path_keeps_each_rule_honest():
    """قاعدةٌ لا تشتعل من شرطٍ واحدٍ ناقص: Enter وحده يكفي للإرسال، والتركيزُ المفقود في نافذةٍ واحدة يكفي للنتيجة."""
    enter_only = {"ctrl_enter_sends": False, "enter_sends": True}
    assert "no-keyboard-send" not in {f["id"] for f in audit.derive_findings(enter_only, {}, None, {})}
    one_dialog = {"dialog_focus": {"propose": True, "approval": False}}
    assert [f["id"] for f in audit.derive_findings(one_dialog, {}, None, {})] == ["dialog-focus-lost"]
    static_codes = audit.derive_findings({}, {"raw_codes": ["name_invalid"]}, None, {})
    assert [f["id"] for f in static_codes] == ["raw-error-codes"]


def test_findings_point_at_the_ui_source_carry_survival_and_tracking():
    sources = {"webui/static/app.js": "a\nb\n  const text = errors[e] || `تعذر إكمال العملية (${error.code || 1}`;\n"}
    [finding] = audit.derive_findings({"raw_error_codes_visible": ["x"]}, {}, None, sources,
                                      {"raw-error-codes": "#170"})
    assert finding["points_to"] == "webui/static/app.js:3"
    assert finding["survives_single_page"] == "yes" and finding["tracked_in"] == "#170"
    assert finding["severity"] == "high" and finding["screenshot"] == "06-raw-error-desktop.png"
    assert audit.locate("غير موجود", sources) is None


def test_axe_violations_become_findings_with_their_impact():
    axe = {"violations": [{"id": "label", "impact": "critical", "nodes": 1, "targets": ["textarea"]},
                          {"id": "region", "impact": "moderate", "nodes": 3}]}
    found = {f["id"]: f for f in audit.derive_findings({}, {}, axe, {})}
    assert found["a11y-label"]["severity"] == "high" and found["a11y-region"]["severity"] == "medium"
    assert found["a11y-label"]["targets"] == ["textarea"]


# — حارسُ الدليل —

def test_the_evidence_guard_refuses_paths_mail_long_text_and_oversized_shots(tmp_path):
    (tmp_path / "01-empty-desktop.png").write_bytes(b"x" * 10)
    (tmp_path / "02-big-desktop.png").write_bytes(b"x" * (audit.MAX_SHOT_BYTES + 1))
    clean = {"a": "نصٌّ قصير", "screenshots": [{"file": "01-empty-desktop.png"}]}
    assert audit.evidence_guard(clean, tmp_path) == []
    dirty = {"where": "/home/someone/diwan", "who": "a@b.org", "page": "ب" * (audit.MAX_TEXT_CHARS + 1),
             "screenshots": [{"file": "02-big-desktop.png"}, {"file": "../x.png"}, {"file": "03-gone-desktop.png"}]}
    found = audit.evidence_guard(dirty, tmp_path)
    assert "absolute_path:$.where" in found and "email:$.who" in found and "text_too_long:$.page" in found
    assert "screenshot_too_large:02-big-desktop.png" in found and "screenshot_missing:03-gone-desktop.png" in found
    assert any(item.startswith("screenshot_name:") for item in found)
    many = {"screenshots": [{"file": f"{i:02d}-s-desktop.png", "bytes": 1} for i in range(audit.MAX_SHOTS + 1)]}
    assert f"too_many_screenshots:{audit.MAX_SHOTS + 1}" in audit.evidence_guard(many)


def test_the_evidence_names_its_limits_and_counts_each_journey():
    raw = {"journeys": {"current": {"steps": [{"id": "open", "ok": True}, {"id": "ask", "ok": False}],
                                    "checks": {}, "timings_ms": {"ask_to_answer": 5}},
                        "single-page": {"steps": [{"id": "sp_open", "ok": True}], "checks": {}}}}
    evidence = audit.build_evidence(raw, commit="abc", date="2026-09-28", coverage={}, sources={"x": "y"})
    assert evidence["summary"]["current"] == {"steps": 2, "passed": 1, "failed": 1, "first_failed": "ask"}
    assert evidence["summary"]["single-page"]["failed"] == 0
    assert evidence["timings_ms"]["model_time"] == "zero_by_scripted_provider"
    assert evidence["timings_ms"]["current"] == {"ask_to_answer": 5}
    assert evidence["provider"]["model_time"] == "zero"
    limits = " ".join(evidence["measurement_limits"])
    for words in ("fake_provider", "headless_chromium_only", "not_a_user_study", "not_owner_acceptance"):
        assert words in limits


# — المزوّدُ المكتوب والخادمُ الحقيقيّ —

def _request(tools=(), messages=()):
    """ما يقرؤه المزوّدُ من الطلب وحده: الرسائلُ والأدواتُ المعلنة."""
    from types import SimpleNamespace
    from core.contracts import Message, ToolSpec
    specs = tuple(ToolSpec(name, "", {"type": "object"}) for name in tools)
    return SimpleNamespace(messages=tuple(messages) or (Message("user", "سؤال"),), tools=specs)


def test_the_scripted_provider_answers_by_what_was_asked():
    from core.contracts import Message, ToolCall
    provider = audit.ScriptedProvider()
    assert audit.ANSWER_MARKER in provider.complete(_request()).content
    tools = ("write_file", "propose_memory", "read_file")
    wrapped = json.dumps({"memory": "…", "user_request": audit.WRITE_REQUEST}, ensure_ascii=False)
    [write] = provider.complete(_request(tools, [Message("user", wrapped)])).tool_calls
    assert write.name == "write_file" and write.arguments["path"] == audit.WRITTEN_FILE
    [memory] = provider.complete(_request(tools, [Message("user", audit.MEMORY_REQUEST)])).tool_calls
    assert memory.name == "propose_memory"
    assert audit.ANSWER_MARKER in provider.complete(_request(tools, [Message("user", audit.QUESTION)])).content
    after = [Message("user", audit.MEMORY_REQUEST),
             Message("assistant", "", tool_calls=(ToolCall("m", "propose_memory", {"text": "x"}),)),
             Message("tool", "ok", tool_call_id="m")]
    assert provider.complete(_request(tools, after)).content == audit.MEMORY_DONE
    assert provider.calls == 5


def _http(origin, method, path, body=None, token=""):
    host = origin.removeprefix("http://")
    connection = http.client.HTTPConnection(*host.split(":"), timeout=10)
    headers = {"Host": host}
    if body is not None:
        headers.update({"Origin": origin, "X-Diwan-CSRF": token, "Content-Type": "application/json"})
    try:
        connection.request(method, path, body=None if body is None else json.dumps(body).encode(), headers=headers)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


def test_the_audited_server_is_the_real_app_answering_with_the_scripted_provider(tmp_path):
    """ما يفتحه المتصفّح: الصفحةُ الحقيقية بـdir=rtl، والمشروعُ والمحادثةُ والجوابُ العربيّ عبر /api نفسِه."""
    with audit.serving(tmp_path / "root") as (origin, provider):
        status, page = _http(origin, "GET", "/")
        assert status == 200 and 'dir="rtl"' in page
        token = re.search(r'name="diwan-token" content="([0-9a-f]{64})"', page).group(1)
        status, listed = _http(origin, "POST", "/api", {"action": "projects"}, token)
        listed = json.loads(listed)
        assert status == 200 and listed["agent_enabled"] is True and listed["research_enabled"] is False
        assert listed["media_enabled"] is False, "المستخدمُ الأوّل بلا نموذج رؤية"
        project = json.loads(_http(origin, "POST", "/api", {"action": "create_project", "name": "م"}, token)[1])["id"]
        session = json.loads(_http(origin, "POST", "/api", {"action": "create_session", "project": project,
                                                            "name": "ح", "mode": "text"}, token)[1])["id"]
        asked = {"action": "ask", "project": project, "session": session, "turn": "a" * 32,
                 "message": audit.QUESTION, "files": []}
        status, _ = _http(origin, "POST", "/api", asked, token)
        assert status == 200
        history = json.loads(_http(origin, "POST", "/api", {"action": "history", "project": project,
                                                           "session": session, "before": None}, token)[1])
        assert audit.ANSWER_MARKER in history["turns"][0]["content"] and provider.calls == 1


def test_a_missing_browser_is_named_not_a_traceback():
    assert audit.browser_prerequisites(which=lambda name: None)[0] == "node_missing"

    class Ran:
        def __init__(self, code):
            self.returncode, self.stdout = code, ""

    which = lambda name: f"/usr/bin/{name}"
    assert audit.browser_prerequisites(which=which, run=lambda *a, **k: Ran(1))[0] == "playwright_missing"
    code, node, env = audit.browser_prerequisites(which=which, run=lambda *a, **k: Ran(0))
    assert code is None and node == "/usr/bin/node"
