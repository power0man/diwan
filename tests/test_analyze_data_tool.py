"""ج٨ (#44): أداةُ analyze_data فوق مخزن الأفعال ودفتر الرجوع، بخُلفيّةٍ مزيّفة لا Docker فيها.

- المخرجاتُ المعلنة تُكتب بالدفتر، وتُرجع كلُّها بزرٍّ واحد.
- وإن عُدّل أحدُها بعد النداء رُفض الرجوعُ كلُّه ولم يُرجع عن شيء، فلا رجوعَ نصفيّ.
- كودٌ فشل، أو انتهت مهلتُه، أو نقص مخرجُه، أو كبر: رفضٌ مسمًّى، ولم يُكتب شيء.
- مسارٌ مخفيٌّ أو خارجٌ عن المساحة يُرفض قبل أن تُستدعى الخُلفيّة.
- والواجهةُ لا تُعلن الأداةَ إلا حيث ضُبطت صورتُها، وجلسةٌ محفوظةٌ أعلنتها تُفتح بلا صورة فيُرفض نداؤها باسمه.
"""
from __future__ import annotations

import json

import pytest

from agent.action_revert import revert_prepared
from agent.actions import ActionStore
from agent.journal import Journal
from agent.registry import ToolContext, ToolRegistry
from analysis import backend as analysis
from analysis.backend import AnalysisResult
from analysis.tool import ANALYZE_DATA
from core.contracts import ToolCall

POSITION = dict(session_id="session", turn_id="turn-1", step_index=0)
REQUEST = "a" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


class FakeBackend:
    def __init__(self, result):
        self.result, self.calls = result, []

    def analyze(self, code, inputs, outputs, *, timeout_s):
        self.calls.append((code, tuple(inputs), tuple(outputs), timeout_s))
        return self.result


def _result(**changes):
    values = dict(exit_code=0, timed_out=False, stdout="الإجمالي 30\n", stderr="",
                  outputs={"answer.json": '{"الإجمالي": 30}'.encode(), "chart.png": PNG},
                  missing=(), oversized=(), boundary="docker:" + "c" * 64)
    values.update(changes)
    return AnalysisResult(**values)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    work = tmp_path.resolve() / "work"
    work.mkdir()
    (work / "sales.csv").write_text("الفرع,الكمية\nالرياض,3\n", encoding="utf-8")
    (work / "answer.json").write_text("قديم", encoding="utf-8")
    fake = FakeBackend(_result())
    monkeypatch.setattr(analysis, "_BACKENDS", {work: fake})
    store = ActionStore(tmp_path.resolve() / "control", work)
    context = ToolContext(work, Journal(work), frozenset({"auto", "logged"}))
    return work, fake, store, context


def _call(store, context, arguments, index=0):
    registry = ToolRegistry(ANALYZE_DATA)
    call = ToolCall(f"call-{index}", "analyze_data", arguments)
    position = {**POSITION, "turn_id": f"turn-{index}"}
    store.register_step(**position, request_digest=REQUEST, calls=(call,), specs=registry.specs())
    return registry.invoke_prepared(call, context, store=store, **position, call_index=0, request_digest=REQUEST)


ARGS = {"code": "print(1)", "inputs": ["sales.csv"], "outputs": ["answer.json", "chart.png"]}


def test_declared_outputs_are_written_with_one_revert_for_the_whole_call(setup):
    work, fake, store, context = setup
    result = _call(store, context, ARGS)
    assert result["status"] == "ok" and len(result["journal_action_ids"]) == 2
    assert "journal_action_id" not in result and fake.calls == [("print(1)", ("sales.csv",),
                                                                 ("answer.json", "chart.png"), 120)]
    assert json.loads((work / "answer.json").read_text(encoding="utf-8")) == {"الإجمالي": 30}
    assert (work / "chart.png").read_bytes() == PNG
    assert "الإجمالي 30" in result["content"] and "معاينة answer.json" in result["content"]
    undo = revert_prepared(store, context, result["action_id"], session_id="session", request_id="undo-1")
    assert undo["status"] == "ok"
    assert (work / "answer.json").read_text(encoding="utf-8") == "قديم" and not (work / "chart.png").exists()


@pytest.mark.parametrize("edited", ["answer.json", "chart.png"])
def test_a_later_edit_to_any_output_refuses_the_whole_revert_and_reverts_nothing(setup, edited):
    """الأوّلُ يُرجع آخرًا: فتعديلُه بعد النداء لا يُكتشف إلا بفحص المجموعة كلِّها قبل أيِّ رجوع."""
    work, _, store, context = setup
    result = _call(store, context, ARGS)
    (work / edited).write_bytes(b"owner edit")
    undo = revert_prepared(store, context, result["action_id"], session_id="session", request_id="undo-1")
    assert undo["status"] == "refused" and undo["code"] == "changed_since_action"
    expected = {"answer.json": '{"الإجمالي": 30}'.encode(), "chart.png": PNG, edited: b"owner edit"}
    assert {name: (work / name).read_bytes() for name in expected} == expected


@pytest.mark.parametrize("changes, code", [
    ({"exit_code": 1, "stderr": "KeyError: 'الكمية'", "outputs": {}, "missing": ("answer.json", "chart.png")},
     "analysis_script_failed"),
    ({"exit_code": None, "timed_out": True, "outputs": {}, "missing": ("answer.json", "chart.png")},
     "analysis_timed_out"),
    ({"outputs": {"answer.json": b"{}"}, "missing": ("chart.png",)}, "analysis_outputs_missing"),
    ({"outputs": {"answer.json": b"{}"}, "oversized": ("chart.png",)}, "analysis_output_too_large"),
])
def test_an_unfinished_analysis_writes_nothing_and_says_why(setup, changes, code):
    work, fake, store, context = setup
    fake.result = _result(**changes)
    result = _call(store, context, ARGS)
    assert result["status"] == "refused" and result["code"] == code
    assert "لم يُكتب شيء" in result["content"]
    assert (work / "answer.json").read_text(encoding="utf-8") == "قديم" and not (work / "chart.png").exists()
    if "stderr" in changes:
        assert "KeyError" in result["content"]


def test_a_journal_failure_midway_rolls_back_what_was_written(setup, monkeypatch):
    work, _, store, context = setup
    original, writes = Journal.write_bytes, []

    def failing(self, relative, raw):
        writes.append(relative)
        if relative == "chart.png":
            from agent.journal import JournalRefused
            raise JournalRefused("file_too_large", "محاكاةُ عطبٍ في الكتابة الثانية")
        return original(self, relative, raw)
    monkeypatch.setattr(Journal, "write_bytes", failing)
    result = _call(store, context, ARGS)
    assert result["status"] == "refused" and result["code"] == "file_too_large" and writes == ["answer.json", "chart.png"]
    assert (work / "answer.json").read_text(encoding="utf-8") == "قديم" and not (work / "chart.png").exists()


@pytest.mark.parametrize("arguments, code", [
    ({**ARGS, "outputs": [".diwan-journal/journal.jsonl"]}, "path_protected"),
    ({**ARGS, "outputs": ["../escape.csv"]}, "path_invalid"),
    ({**ARGS, "outputs": []}, "argument_invalid"),
    ({**ARGS, "code": "  "}, "argument_invalid"),
    ({**ARGS, "timeout_s": "5"}, "argument_invalid"),
])
def test_a_bad_path_or_argument_is_refused_before_the_backend(setup, arguments, code):
    _, fake, store, context = setup
    result = _call(store, context, arguments)
    assert result["status"] == "refused" and result["code"] == code and fake.calls == []


def test_without_an_image_the_call_is_refused_by_name(setup, monkeypatch):
    _, _, store, context = setup
    monkeypatch.setattr(analysis, "_BACKENDS", {})
    result = _call(store, context, ARGS)
    assert result["status"] == "refused" and result["code"] == "analysis_backend_unavailable"


# ————— الواجهة —————

def _receipt(tmp_path):
    receipt = tmp_path / "analysis-receipt.json"
    receipt.write_text(json.dumps({"schema_version": 1, "image_id": "sha256:" + "a" * 64, "lock_sha256": "b" * 64,
                                   "python_version": "3.12.14"}), encoding="utf-8")
    receipt.chmod(0o600)
    return receipt


def _app(root, **options):
    from webui.server import LocalApp
    app = LocalApp(root, model="m", model_version="v" * 64, provider_factory=lambda: None,
                   agent_provider_factory=lambda: None, **options)
    app.api = lambda action, **values: app.dispatch({"action": action, **values})
    return app


def test_the_ui_declares_the_tool_only_where_its_image_is_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis, "_BACKENDS", {})
    base = tmp_path.resolve()
    base.chmod(0o700)
    plain = _app(base / "plain")
    project = plain.project(plain.api("create_project", name="أ")["id"])
    assert "analyze_data" not in {spec.name for spec in plain.agent_registry(project).specs()}
    plain.close()
    wired = _app(base / "wired", analysis_receipt=_receipt(base), docker_executable="/usr/bin/docker")
    project = wired.project(wired.api("create_project", name="ب")["id"])
    specs = {spec.name: spec for spec in wired.agent_registry(project).specs()}
    assert specs["analyze_data"].consent == "logged" and specs["analyze_data"].reversible
    assert wired.agent_backends[project.name]["analysis_status"] == "configured"
    assert analysis.analysis_backend(project / "agent-workspace").docker == "/usr/bin/docker"
    wired.close()


def test_a_saved_session_that_declared_the_tool_reopens_without_the_image(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis, "_BACKENDS", {})
    base = tmp_path.resolve()
    base.chmod(0o700)
    app = _app(base / "root", analysis_receipt=_receipt(base))
    project_id = app.api("create_project", name="أ")["id"]
    session_id = app.api("create_session", project=project_id, name="وكيلة", mode="agent")["id"]
    project = app.project(project_id)
    app.agent_session(project, session_id)
    app.close()
    analysis._BACKENDS.clear()
    reopened = _app(base / "root")
    project = reopened.project(project_id)
    session = reopened.agent_session(project, session_id)
    assert "analyze_data" in {spec.name for spec in session.registry.specs()}
    reopened.close()


# ————— مُشغِّلُ المهامّ الوكيلة (بنك ك٥٠) —————

def test_the_agentic_runner_configures_the_image_per_task_and_releases_it(tmp_path, monkeypatch):
    import sys
    from core.contracts import Response, Usage
    from evaluation.agentic_runner import run_agentic_suite

    monkeypatch.setattr(analysis, "_BACKENDS", {})
    monkeypatch.setenv("DIWAN_DISPOSABLE_HOST", "pytest-attested-host")
    seen = []

    def analyze(self, code, inputs, outputs, *, timeout_s):
        seen.append((self.root, dict(analysis._BACKENDS)))
        return _result(outputs={"answer.json": b'{"n": 1}'}, stdout="")
    monkeypatch.setattr(analysis.AnalysisBackend, "analyze", analyze)

    def says(content, *calls):
        return Response(content, Usage(1, 1), "complete", 0, provider="scripted", model_version="v1",
                        tool_calls=tuple(calls))

    class Scripted:
        name, is_local = "scripted", True

        def __init__(self, *responses):
            self.queue = list(responses)

        def estimate_micros(self, request):
            return 0

        def complete(self, request):
            return self.queue.pop(0)

    task = {"task_id": "t1", "capability": "analyst_aggregation", "workspace": {"data.csv": "n\n1\n"},
            "instruction": "احسب.", "forbidden": ["data.csv"], "rubric": ["—"], "max_steps": 4,
            "success": {"kind": "command_exit_zero", "command": [
                sys.executable, "-c", "import json; assert json.load(open('answer.json')) == {'n': 1}"]}}
    suite = {"schema_version": 1, "suite_id": "analyst_fixture", "kind": "agentic_tasks", "description": "—",
             "tasks": [task]}
    provider = Scripted(says("", ToolCall("c1", "analyze_data", {"code": "print(1)", "inputs": ["data.csv"],
                                                                 "outputs": ["answer.json"]})), says("تم."))
    receipt = _receipt(tmp_path.resolve())
    report = run_agentic_suite(suite, provider, ToolRegistry(ANALYZE_DATA), model="m", model_version="v",
                               analysis_receipt=receipt)
    assert report["results"][0]["passed"] is True, report["results"][0]
    assert report["config"]["analysis"] == {"image_id": "sha256:" + "a" * 64}
    ((root, configured),) = seen
    assert root.name == "workspace" and list(configured) == [root]    # مساحةُ المهمّة وحدها
    assert analysis._BACKENDS == {}                                       # وحُرِّرت بعدها
