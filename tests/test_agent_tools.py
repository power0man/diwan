"""دفترُ الرجوع وسجلُّ الأدوات (الحلقة — الشريحة ٢).

الخاصيّةُ التي يقوم عليها «العمل دون تدخّل»: ما يُردّ لا يحتاج إذنًا
واقعةً بواقعة. فالرَّجعيةُ تُختبر هنا كما تُختبر الوظيفة.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.contracts import ToolCall
from agent.builtin_tools import DEFAULT_TOOLS
from agent.journal import JOURNAL_DIR, Journal, JournalRefused
from agent.registry import ToolContext, ToolRefused, ToolRegistry


@pytest.fixture
def space(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# مشروع\nسطرٌ ثانٍ\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def context(space):
    return ToolContext(root=space, journal=Journal(space),
                       allowed_consents=frozenset({"auto", "logged"}))


@pytest.fixture
def registry():
    return ToolRegistry(*DEFAULT_TOOLS)


def call(registry, context, name, **arguments):
    return registry.invoke(ToolCall("c1", name, arguments), context)


# ————— دفترُ الرجوع: الترتيبُ هو الضمانة —————

def test_the_prior_state_is_journalled_before_the_write_lands(space):
    """انقطاعٌ بين الإيداع والكتابة يترك رجوعًا ممكنًا؛ والعكسُ لا."""
    journal = Journal(space)
    target = space / "src/app.py"
    seen = {}

    original_place = Journal._place

    def crash(self, path, raw):
        # لحظةُ ما بعد الإيداع وقبل الأثر
        seen["journalled"] = len(self.actions())
        seen["untouched"] = target.read_text(encoding="utf-8")
        raise OSError("انقطاع")

    Journal._place = crash
    try:
        with pytest.raises(OSError):
            journal.write_file("src/app.py", "جديد")
    finally:
        Journal._place = original_place
    assert seen["journalled"] == 1, "أُودعت الحالةُ السابقة قبل محاولة الكتابة"
    assert seen["untouched"].endswith("a - b\n")
    # والرجوعُ عن فعلٍ لم يقع عدمُ عملٍ مُعلَن: الحالُ كما كانت قبله
    assert journal.revert(journal.actions()[0].action_id) == {
        "status": "already_reverted", "action_id": journal.actions()[0].action_id,
        "result": "no_change"}
    assert target.read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"


def test_writing_a_new_file_reverts_by_removing_it(space):
    journal = Journal(space)
    action = journal.write_file("notes/fresh.md", "نصّ")
    assert (space / "notes/fresh.md").is_file()
    assert action.before_sha256 is None
    assert journal.revert(action.action_id)["result"] == "removed"
    assert not (space / "notes/fresh.md").exists()


def test_overwriting_reverts_to_the_exact_prior_bytes(space):
    journal = Journal(space)
    before = (space / "src/app.py").read_bytes()
    action = journal.write_file("src/app.py", "def add(a, b):\n    return a + b\n")
    assert (space / "src/app.py").read_bytes() != before
    journal.revert(action.action_id)
    assert (space / "src/app.py").read_bytes() == before


def test_reverting_refuses_when_someone_edited_afterwards(space):
    journal = Journal(space)
    action = journal.write_file("src/app.py", "أوّل")
    (space / "src/app.py").write_text("يدُ إنسانٍ", encoding="utf-8")
    with pytest.raises(JournalRefused) as exc:
        journal.revert(action.action_id)
    assert exc.value.code == "changed_since_action"
    assert (space / "src/app.py").read_text(encoding="utf-8") == "يدُ إنسانٍ"


def test_two_writes_revert_in_any_order_to_a_consistent_state(space):
    journal = Journal(space)
    first = journal.write_file("src/app.py", "ألف")
    second = journal.write_file("src/app.py", "باء")
    journal.revert(second.action_id)
    assert (space / "src/app.py").read_text(encoding="utf-8") == "ألف"
    journal.revert(first.action_id)
    assert (space / "src/app.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"


@pytest.mark.parametrize("path", ["../escape.txt", "/etc/passwd", ".git/config",
                                  f"{JOURNAL_DIR}/journal.jsonl", "a/../../b"])
def test_the_journal_refuses_paths_that_leave_or_hide(space, path):
    with pytest.raises(Exception) as exc:
        Journal(space).write_file(path, "x")
    assert getattr(exc.value, "code", "") in ("path_invalid", "path_protected",
                                              "path_escapes_root")


def test_an_unknown_action_id_is_refused_not_ignored(space):
    with pytest.raises(JournalRefused) as exc:
        Journal(space).revert("act-ffffffffffffffff")
    assert exc.value.code == "action_unknown"


# ————— الإذنُ: ميثاقٌ للدرجة، وموافقةٌ للنداء —————

def test_an_owner_grade_call_waits_instead_of_running(registry, context, space):
    marker = space / "ran.txt"
    result = registry.invoke(
        ToolCall("c1", "run_command", {"argv": ["touch", str(marker)]}), context)
    assert result["status"] == "awaiting_owner"
    assert result["code"] == "consent_required" and result["consent"] == "owner"
    assert not marker.exists(), "الانتظارُ يسبق التنفيذ لا يليه"


def test_an_approved_call_id_cannot_grant_owner_consent(registry, space):
    approved = ToolContext(root=space, journal=Journal(space),
                           allowed_consents=frozenset({"auto"}),
                           approved_call_ids=frozenset({"c9"}),
                           disposable_host="pytest-host")
    ok = registry.invoke(ToolCall("c9", "run_command", {"argv": ["echo", "مرحبًا"]}), approved)
    assert ok["status"] == "awaiting_owner" and ok["code"] == "consent_required"
    other = registry.invoke(ToolCall("c8", "run_command", {"argv": ["echo", "x"]}), approved)
    assert other["status"] == "awaiting_owner"


def test_the_charter_can_never_grant_the_owner_grade(space):
    with pytest.raises(ValueError):
        ToolContext(root=space, journal=Journal(space),
                    allowed_consents=frozenset({"auto", "owner"}))


def test_a_charter_of_auto_alone_withholds_writing(registry, space):
    read_only = ToolContext(root=space, journal=Journal(space))
    result = registry.invoke(ToolCall("c1", "write_file",
                                      {"path": "x.txt", "content": "y"}), read_only)
    assert result["status"] == "awaiting_owner"
    assert not (space / "x.txt").exists()


def test_run_command_refuses_without_a_configured_container_backend(registry, space):
    from agent.actions import ActionStore
    approved = ToolContext(root=space, journal=Journal(space),
                           approved_call_ids=frozenset({"c9"}))
    store = ActionStore(space.parent / (space.name + "-actions"), space)
    call = ToolCall("c9", "run_command", {"argv": ["echo", "x"]})
    position = dict(session_id="s", turn_id="t", step_index=0, request_digest="a" * 64)
    store.register_step(**position, calls=(call,), specs=registry.specs())
    result = registry.invoke_prepared(call, approved, store=store, **position, call_index=0)
    assert result["status"] == "refused"
    assert result["code"] == "execution_backend_unavailable"


# ————— الأدوات —————

def test_read_search_and_list(registry, context):
    assert "مشروع" in call(registry, context, "read_file", path="README.md")["content"]
    hits = call(registry, context, "search_files", pattern=r"return", suffix=".py")
    assert hits["status"] == "ok" and "src/app.py:2" in hits["content"]
    listing = call(registry, context, "list_files")
    assert {"README.md", "src/app.py"} <= set(listing["content"].split("\n"))


def test_write_file_reports_how_to_revert(registry, context, space):
    result = call(registry, context, "write_file", path="src/app.py",
                  content="def add(a, b):\n    return a + b\n")
    assert result["status"] == "ok" and result["action_id"].startswith("act-")
    assert "+ b" in (space / "src/app.py").read_text(encoding="utf-8")
    Journal(space).revert(result["action_id"])
    assert "- b" in (space / "src/app.py").read_text(encoding="utf-8")


def test_run_tests_uses_a_fixed_argv_and_refuses_injected_flags(registry, context):
    for hostile in (["-p", "evil"], ["--co"], ["/etc"], ["../outside"],
                    ["core"], ["tools/rebuild_index.py"], ["tests/ghost_suite.py"]):
        result = registry.invoke(ToolCall("c1", "run_tests", {"paths": hostile}), context)
        assert result["status"] == "refused", hostile


@pytest.mark.parametrize("name,arguments,code", [
    ("read_file", {"path": "ghost.txt"}, "file_not_found"),
    ("read_file", {"path": "../../etc/passwd"}, "path_invalid"),
    ("read_file", {}, "argument_invalid"),
    ("search_files", {"pattern": "[unclosed"}, "pattern_invalid"),
    ("write_file", {"path": ".git/config", "content": "x"}, "path_protected"),
    ("write_file", {"path": "ok.txt"}, "argument_invalid"),
    ("list_files", {"prefix": "nowhere"}, "directory_not_found"),
])
def test_bad_arguments_are_named_refusals_not_faults(registry, context, name, arguments, code):
    """الرفضُ يعود إلى النموذج ليصحّح؛ والعطبُ إشارةُ خللٍ في النظام."""
    result = registry.invoke(ToolCall("c1", name, arguments), context)
    assert result["status"] == "refused" and result["code"] == code


def test_a_huge_result_is_truncated_with_a_visible_marker(registry, context, space):
    (space / "big.txt").write_text("ب" * 40_000, encoding="utf-8")
    result = call(registry, context, "read_file", path="big.txt")
    assert result["status"] == "ok"
    assert result["content"].endswith("[قُطع الناتج عند الحدّ المعلن]")


def test_every_declared_tool_states_a_consent_grade_and_keeps_its_promise(registry):
    specs = registry.specs()
    assert {s.name for s in specs} == {"read_file", "search_files", "list_files",
                                       "run_tests", "write_file", "edit_file", "run_command"}
    for spec in specs:
        assert spec.consent in ("auto", "logged", "owner")
        assert spec.description.strip()
        # التعهّدُ نفسه الذي تفرضه النواة: إذنٌ مُسجَّل يلزمه أثرٌ رَجعيّ
        if spec.consent == "logged":
            assert spec.reversible is True


def test_reverting_twice_is_idempotent_not_a_failure(space):
    journal = Journal(space)
    action = journal.write_file("src/app.py", "جديد")
    assert journal.revert(action.action_id)["status"] == "reverted"
    assert journal.revert(action.action_id)["status"] == "already_reverted"
    assert (space / "src/app.py").read_text(encoding="utf-8").endswith("a - b\n")


def test_run_tests_refuses_host_execution_without_a_container_backend(registry, tmp_path):
    """حتى اختبارات المشروع شيفرة مرشحة، فلا تُنفّذ على المضيف."""
    root = Path(__file__).resolve().parent.parent
    context = ToolContext(root=root, journal=Journal(tmp_path),
                          allowed_consents=frozenset({"auto"}))
    result = registry.invoke(
        # ملفٌّ آخر عمدًا: تشغيلُ هذا الملفِّ من داخله عَودٌ لا ينتهي — وهو ما
        # أظهره أولُ تشغيلٍ لهذا الاختبار، فصار العَودُ مقيَّدًا بالتصميم.
        ToolCall("c1", "run_tests", {"paths": ["tests/test_tool_contract.py"]}), context)
    assert result["status"] == "refused"
    assert result["code"] == "execution_backend_unavailable"


def test_run_tests_uses_the_pinned_container_interpreter():
    """المفسّر داخل صورة التشغيل المعتمدة، لا Python المضيف أو PATH."""
    source = (Path(__file__).resolve().parent.parent / "agent/builtin_tools.py").read_text()
    body = source.split("def _run_tests")[1].split("\ndef ")[0]
    assert '"/opt/venv/bin/python"' in body and "execute_candidate" in body
    assert "sys.executable" not in body and "subprocess.run" not in body


def test_sovereign_tools_integration_with_agent(space):
    from agent.builtin_tools import get_all_tools, get_sovereign_tools
    sov = get_sovereign_tools()
    assert len(sov) == 6
    all_tools = get_all_tools()
    assert len(all_tools) == 13

    reg = ToolRegistry(*all_tools)
    ctx = ToolContext(root=space, journal=Journal(space), allowed_consents=frozenset({"auto", "logged"}))

    # فحص نداء أداة التحليل الصرفي عبر سجل الأدوات الموسع
    res = reg.invoke(ToolCall("c-morph", "analyze_arabic_morphology", {"word": "المستكشفون"}), ctx)
    assert res["status"] == "ok"
    assert "كشف" in res["content"]
