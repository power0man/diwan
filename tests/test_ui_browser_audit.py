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
def need(condition, code="request_invalid"):
    if not condition:
        raise UIError(code)


def check(value, exc):
    need(value.get("mode", "text") in MODES, "metadata_invalid")
    need(len(value) < 3, "collection_limit")  # تعليق
    need(ok, "turn_missing")
    need(value)
    need(value.get("tools") == value.get("contract"),
         "tool_contract_changed")
    need(value, code="keyword_code")
    need(value, value.code)
    raise UIError("id_invalid")


def handle(exc):
    status = {"error_code": "http_refused"}
    code = getattr(exc, "code", "request_failed")
    shown = getattr(exc, "code", "status_field_only")
    configured.update(error_code=getattr(exc, "code", "capability_field_only"))
'''
APP = '''const errors = {
  collection_limit: "بلغت المشروعاتُ حدَّها.",
  http_refused: "انتهت جلسة الواجهة.",
};
if(["turn_missing"].includes(e.code)) notice("لم تُسجَّل الجولة.");
if(error.code === "keyword_code") notice("…");
const label = "tool_contract_changed";
'''


def test_error_coverage_parses_calls_structurally_with_defaults_keywords_and_fallbacks():
    """ملاحظة Codex على #175: نداءُ need الممتدّ أسطرًا، والنداءُ بقيمته الافتراضية request_invalid، والرمزُ باسمه code،
    وبديلُ المعالج request_failed كلُّها رموزٌ تبلغ المستخدم؛ وحقلُ الحالة والرمزُ المتغيّر ليسا رمزًا حرفيًّا."""
    coverage = audit.error_code_coverage(SERVER, APP)
    assert coverage["server_codes"] == 9, coverage          # لا "text" من وسيطٍ داخليّ، ولا حقولُ الحالة
    assert coverage["with_arabic_message"] == 2
    assert coverage["handled_by_a_branch"] == ["keyword_code", "turn_missing"]   # ذكرُ الرمز نصًّا ليس فرعًا
    assert coverage["raw_codes"] == ["id_invalid", "metadata_invalid", "request_failed", "request_invalid",
                                     "tool_contract_changed"]
    assert coverage["raw_code_lines"]["tool_contract_changed"] == 12   # سطرُ النداء لا سطرُ الرمز
    assert coverage["dynamic_code_lines"] == [4, 15]   # UIError(code) في need نفسِها، وneed(value, value.code)


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


ABSOLUTE = ["/workspace/private/secret", "see /mnt/data", "(/etc/passwd)", "path=/srv/project", "at:/opt/x",
            "~/diwan/notes", "C:\\Users\\me", "D:/data/x", "file:///tmp/x", "FILE://host/share", "\\\\server\\share",
            "[/var/lib/x]", "المسار «/workspace/private/secret»", "path>/etc/passwd", "a|/srv/x", "x;/opt/y", "{/mnt/z}",
            "\t/var/x", "line\n/home/u"]
NOT_ABSOLUTE = ["https://example.org/a/b", "http://127.0.0.1:8000/api", "ORIGIN/api",
                "docs/probe/ui-browser-audit-20260928/01-empty-desktop.png", "webui/static/app.js:57", "1366x768",
                "1/2", "GET / ← 200", "and/or", "T18:17:31Z", "a11y-label", "نعم/لا", "./rel/x", "../up/x", "a-b/c", "x_y/z"]


def test_the_evidence_guard_refuses_any_absolute_path_and_passes_urls_and_repo_paths():
    """ملاحظتا Codex على #175: الحارسُ كان يعدّ بادئاتٍ (/home/ و/tmp/…) ثم فواصلَ قبل «/» فيمرّ «/workspace» بعد «» أو
    «>»؛ صار يلتقط «/» لا يسبقها حرفٌ ولا رقمٌ ولا . ولا - ولا _ ولا «/»، و~/، وحرفَ القرص بشرطتيه، وUNC، وfile://؛ ولا
    يلتقط عنوانَ URL ولا مسارًا نسبيًّا في المستودع ولا كلمتين عربيتين بينهما «/»."""
    missed = [text for text in ABSOLUTE if audit.evidence_guard({"x": text}) != ["absolute_path:$.x"]]
    assert missed == [], missed
    flagged = [text for text in NOT_ABSOLUTE if audit.evidence_guard({"x": text})]
    assert flagged == [], flagged


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


def test_an_axe_that_did_not_run_refuses_the_evidence(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #175: طُلب axe فلم يعمل (ملفٌّ غائب أو سكربتٌ غيرُ صالح) فلا يُكتب دليلٌ يبدو نظيفًا."""
    ran = {"status": "run", "violations": [], "errors": [],
           "states": [{"journey": "current", "state": "empty-desktop"}, {"journey": "single-page", "state": "sp-empty-mobile"}]}
    assert audit.axe_problem(ran, True) is None
    # ملاحظة Codex على #175: رحلةٌ مطلوبة بلا حالةٍ فحصها axe (الصفحةُ الموحّدة هدفُ القبول) لا يغطّيها نجاحُ غيرها
    only_current = {**ran, "states": ran["states"][:1]}
    assert audit.axe_problem(only_current, True, ["current", "single-page"]) == "axe_journey_unaudited"
    assert audit.axe_problem(only_current, True, ["single-page"]) == "axe_journey_unaudited"
    assert audit.axe_problem(only_current, True, ["current"]) is None
    assert audit.axe_problem({"status": "not_run"}, False) is None
    assert audit.axe_problem({"status": "failed", "code": "axe_unavailable", "errors": ["ENOENT"]}, True) == "axe_unavailable"
    assert audit.axe_problem({**ran, "errors": ["axe is not defined"]}, True) == "axe_failed"
    assert audit.axe_problem({**ran, "states": []}, True) == "axe_failed"
    assert audit.axe_problem({"status": "not_run"}, True) == "axe_failed"
    monkeypatch.setattr(audit, "browser_prerequisites", lambda node=None: ("node_missing", None, None))
    assert audit.main(["--axe", str(tmp_path / "missing-axe.min.js")]) == 3
    assert json.loads(capsys.readouterr().out)["code"] == "axe_unavailable"


def test_a_rerun_touches_the_target_only_after_validation_and_only_its_own_shots(tmp_path, monkeypatch):
    """ملاحظة Codex على #175: كان التشغيلُ يحذف كلَّ لقطةٍ تطابق النمط قبل أن يعمل المتصفّح، فتشغيلٌ ساقط يترك دليلًا سابقًا
    يشير إلى لقطاتٍ محذوفة، ويُحذف ملفٌّ للمستخدم يطابق النمط. صارت اللقطاتُ في مجلّدٍ مؤقّت حتى يمرّ الدليل، ثم تُستبدل
    بأسمائها، وتُحذف القديمةُ بأسمائها من الدليل السابق وحده."""
    shots, out = tmp_path / "shots", tmp_path / "evidence.json"
    shots.mkdir()
    before = {"01-empty-desktop.png": b"old-empty", "02-stale-desktop.png": b"old-stale", "01-notes.png": b"mine"}
    for name, data in before.items():
        (shots / name).write_bytes(data)
    out.write_text(json.dumps({"screenshots": [{"file": "01-empty-desktop.png"}, {"file": "02-stale-desktop.png"}]}))
    old_json = out.read_bytes()
    axe_file = tmp_path / "axe.min.js"
    axe_file.write_text("")

    def browser(axe):
        def run(node, env, config, workdir):
            (Path(config["shots_dir"]) / "01-empty-desktop.png").write_bytes(b"new-empty")
            return 0, {"journeys": {"current": {"steps": [{"id": "open", "ok": True}], "checks": {}}},
                       "screenshots": [{"file": "01-empty-desktop.png", "step": "open", "viewport": "1366x768"}],
                       "axe": axe}, ""
        return run

    monkeypatch.setattr(audit, "browser_prerequisites", lambda node=None: (None, "node", {}))
    argv = ["--journey", "current", "--out", str(out), "--shots", str(shots)]
    monkeypatch.setattr(audit, "run_browser", browser({"status": "failed", "code": "axe_failed", "errors": ["x"]}))
    assert audit.main([*argv, "--axe", str(axe_file)]) == 1
    assert out.read_bytes() == old_json
    assert {p.name: p.read_bytes() for p in shots.iterdir()} == before, "تشغيلٌ ساقط مسّ الدليلَ السابق"
    monkeypatch.setattr(audit, "run_browser", browser({"status": "not_run"}))
    assert audit.main(argv) == 0
    assert (shots / "01-empty-desktop.png").read_bytes() == b"new-empty"
    assert not (shots / "02-stale-desktop.png").exists(), "لقطةُ الدليل السابق التي لم يعد يعلنها باقية"
    assert (shots / "01-notes.png").read_bytes() == b"mine", "حُذف ملفٌّ لم تنتجه الأداة"
    assert [s["file"] for s in json.loads(out.read_text())["screenshots"]] == ["01-empty-desktop.png"]
    assert sorted(p.name for p in shots.iterdir()) == ["01-empty-desktop.png", "01-notes.png"]


EVIDENCE = ROOT / "docs" / "probe" / "ui-browser-audit-20260928.json"


def test_the_published_evidence_names_the_tool_it_was_made_with():
    """ملاحظة Codex على #175: سجّل الدليلُ مراجعةً ليست في تاريخ الفرع، والأداةُ تغيّرت بعدها، فلا يُعاد إنتاجُه منها. صار
    الدليلُ يحمل بصمةَ محتوى الأداة وسائقها وملفّات الواجهة، وأنها كانت في المراجعة المسجَّلة بلا تعديل؛ وهذا الاختبارُ يسقط
    متى تغيّرت الأداةُ أو سائقُها بلا إعادة الفحص. وبصماتُ الواجهة تعرّف النسخةَ المفحوصة ولا تُحرس: تفترق بعد #166 بحقّ."""
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    assert evidence["commit_contains_inputs"] is True, "الدليلُ أُنتج من شجرةٍ فيها تعديلٌ غيرُ مودَع"
    recorded = evidence["content_sha256_12"]
    assert set(recorded) == set(audit.DIGESTED)
    assert {path: recorded[path] for path in audit.IMPLEMENTATION} == audit.content_digests(audit.IMPLEMENTATION), \
        "تغيّرت الأداةُ بعد الدليل: أعد الفحص بـtools/ui_browser_audit.py ثم أودِع الدليلَ فوق إيداع الشيفرة"


def test_axe_incomplete_checks_are_kept_and_surfaced_for_review():
    """ملاحظة Codex السادسة على #175: كان السائقُ يحفظ عددَ ما لم يحسمه axe وحده ويُسقط قاعدتَه وعناصرَه وأسبابَه، فتبدو حالةٌ
    بلا مخالفةٍ نظيفةً وفيها ما يحتاج مراجعةً يدوية. صار كلُّ سجلٍّ محفوظًا بتفاصيله، ولكل حالةٍ حكمُها، ولكل قاعدةٍ وحالةٍ نتيجةٌ
    needs_review."""
    raw = {"journeys": {"current": {"steps": [{"id": "open", "ok": True}], "checks": {}}},
           "axe": {"status": "run", "version": "4.13.0", "errors": [], "violations": [],
                   "states": [{"journey": "current", "state": "approval-dialog-desktop", "violations": 0, "passes": 11,
                               "incomplete": 1},
                              {"journey": "current", "state": "empty-desktop", "violations": 0, "passes": 34, "incomplete": 0},
                              {"journey": "current", "state": "remember-dialog-desktop", "violations": 1, "passes": 13,
                               "incomplete": 1}],
                   "incomplete": [{"id": "color-contrast", "impact": "serious", "nodes": 2, "targets": ["#dialog button"],
                                   "help_url": "https://dequeuniversity.com/rules/axe/4.13/color-contrast",
                                   "reasons": ["س" * 500], "reasons_total": 4, "state": "approval-dialog-desktop"},
                                  {"id": "color-contrast", "impact": "serious", "nodes": 1, "targets": ["textarea"],
                                   "reasons": ["خلفيةٌ متراكبة"], "state": "remember-dialog-desktop"}]}}
    evidence = audit.build_evidence(raw, commit="abc", date="2026-09-28", coverage={}, sources={})
    first, second = evidence["axe"]["incomplete"]
    assert first["id"] == "color-contrast" and first["targets"] == ["#dialog button"] and first["impact"] == "serious"
    assert first["state"] == "approval-dialog-desktop" and first["reasons_total"] == 4
    assert len(first["reasons"][0]) <= audit.AXE_TEXT and first["help_url"].startswith("https://")
    assert (second["state"], second["targets"], second["reasons_total"]) == ("remember-dialog-desktop", ["textarea"], 1)
    verdicts = {state["state"]: state["verdict"] for state in evidence["axe"]["states"]}
    assert verdicts == {"approval-dialog-desktop": "needs_review", "empty-desktop": "clean",
                        "remember-dialog-desktop": "violations"}, "حالةٌ فيها ما لم يُحسم ليست نظيفة"
    review = [(f["id"], f["state"], f["targets"]) for f in evidence["findings"] if f["kind"] == "needs_review"]
    assert review == [("a11y-review-color-contrast", "approval-dialog-desktop", ["#dialog button"]),
                      ("a11y-review-color-contrast", "remember-dialog-desktop", ["textarea"])]
    assert audit.evidence_guard(evidence) == []


def test_one_rule_in_two_states_keeps_each_states_own_elements():
    """ملاحظة Codex السابعة على #175: كان دمجُ القاعدة بين الحالات يُبقي عناصرَ أول حالة، فتُوجّه كلُّ نتيجةٍ إلى عناصر غيرها.
    صار السجلُّ لكل (قاعدة، حالة)، وكلُّ نتيجةٍ بعناصر حالتها وأسبابها."""
    raw = {"journeys": {}, "axe": {"status": "run", "errors": [], "incomplete": [],
           "states": [{"journey": "current", "state": s, "violations": 1, "incomplete": 0} for s in ("a-desktop", "b-mobile")],
           "violations": [{"id": "label", "impact": "critical", "nodes": 1, "targets": ["#one"], "state": "a-desktop"},
                          {"id": "label", "impact": "critical", "nodes": 2, "targets": ["#two", "#three"], "state": "b-mobile"}]}}
    evidence = audit.build_evidence(raw, commit="abc", date="2026-09-28", coverage={}, sources={})
    found = [(f["id"], f["state"], f["targets"]) for f in evidence["findings"] if f["kind"] == "violation"]
    assert found == [("a11y-label", "a-desktop", ["#one"]), ("a11y-label", "b-mobile", ["#two", "#three"])]


def test_axe_records_beyond_the_cap_are_named_not_silently_dropped():
    """ملاحظة Codex السابعة على #175: فوق السقف كانت السجلّاتُ تسقط صامتةً والعدّاداتُ تحسبها. صار الباقي مسمًّى في omitted
    ونتيجةً a11y-omitted بقواعده، والحالةُ لا تكون clean، والسقفُ في حدود القياس."""
    extra = audit.AXE_KEPT + 5
    raw = {"journeys": {}, "axe": {"status": "run", "errors": [], "violations": [],
           "states": [{"journey": "single-page", "state": "sp-empty-desktop", "violations": 0, "incomplete": extra}],
           "incomplete": [{"id": f"rule-{i:02d}", "impact": "minor", "nodes": 1, "targets": ["x"],
                           "state": "sp-empty-desktop"} for i in range(extra)]}}
    evidence = audit.build_evidence(raw, commit="abc", date="2026-09-28", coverage={}, sources={})
    assert len(evidence["axe"]["incomplete"]) == audit.AXE_KEPT and evidence["axe"]["records_cap"] == audit.AXE_KEPT
    named = [f"rule-{i:02d}" for i in range(audit.AXE_KEPT, extra)]
    assert [item["id"] for item in evidence["axe"]["omitted"]] == named
    [omitted] = [f for f in evidence["findings"] if f["kind"] == "omitted"]
    assert omitted["omitted_ids"] == named and omitted["omitted_total"] == 5 and omitted["state"] == "sp-empty-desktop"
    assert evidence["axe"]["states"][0]["verdict"] == "needs_review", "حالةٌ حُذف بعضُ سجلّاتها ليست clean"
    assert any("at_most_20_per_kind" in limit for limit in evidence["measurement_limits"])


def test_every_published_axe_state_that_needs_review_names_its_items():
    """الدليلُ المنشور لا يقول أقوى من حجّته: كلُّ حالةٍ فيها ما لم يحسمه axe تُسمّى needs_review، وله سجلٌّ بقاعدته
    وعناصره، ونتيجةٌ صريحة."""
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    axe = evidence["axe"]
    for state in axe["states"]:
        assert state["verdict"] == audit.axe_state_verdict(state), state
        if state["incomplete"]:
            items = [item for item in axe["incomplete"] if item["state"] == state["state"]]
            assert items and all(item["id"] and item["targets"] for item in items), state["state"]
            assert any(f["kind"] == "needs_review" and f["state"] == state["state"] for f in evidence["findings"])


def test_content_digests_and_the_committed_check(tmp_path):
    import hashlib
    import subprocess
    (tmp_path / "a.txt").write_text("أ", encoding="utf-8")
    assert audit.content_digests(["a.txt"], root=tmp_path) == {"a.txt": hashlib.sha256("أ".encode()).hexdigest()[:12]}
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], check=True)
    assert audit.inputs_committed(["a.txt"], root=tmp_path) is False, "ملفٌّ غيرُ متتبَّع ليس في المراجعة"
    subprocess.run([*git, "add", "a.txt"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "a"], check=True)
    assert audit.inputs_committed(["a.txt"], root=tmp_path) is True
    (tmp_path / "a.txt").write_text("ب", encoding="utf-8")
    assert audit.inputs_committed(["a.txt"], root=tmp_path) is False, "ملفٌّ معدَّل ليس ما في المراجعة"


def test_a_missing_browser_is_named_not_a_traceback():
    assert audit.browser_prerequisites(which=lambda name: None)[0] == "node_missing"

    class Ran:
        def __init__(self, code):
            self.returncode, self.stdout = code, ""

    which = lambda name: f"/usr/bin/{name}"
    assert audit.browser_prerequisites(which=which, run=lambda *a, **k: Ran(1))[0] == "playwright_missing"
    code, node, env = audit.browser_prerequisites(which=which, run=lambda *a, **k: Ran(0))
    assert code is None and node == "/usr/bin/node"
