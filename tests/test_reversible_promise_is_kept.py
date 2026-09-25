"""`reversible=True` تعهّدٌ يُختبر بالسلوك، لا حقلٌ يُعلَن.

الواقعة (فحص ٢١:١٣ غرينتش، ٢٢ سبتمبر ٢٠٢٦): `write_workspace_document`
أعلنت `consent="logged"` و`reversible=True` ووصفًا يقول «مع التراجع
التلقائي»، وكانت تكتب بـ`write_text` بلا إيداع. فكان الناتجُ المُقاس:
`success=True`, `reversible=True`, `snapshot=None`, و`rollback()` يُرجع
False، والأصلُ ضائع — بدرجةٍ تعمل بلا حضور المالك.

وحارسُ النواة (`validated`) يفحص **الإعلان** لا **السلوك**، فمرّ الإعلانُ
الكاذب من فوقه. وهذا الملفُّ هو الحارسُ الناقص: كلُّ أداةٍ تُعلن الرَّجعية
تُشغَّل فعلًا ويُثبت أن الحالةَ السابقة تُستعاد.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.actions import ActionStore
from agent.registry import ToolContext
from agent.journal import Journal
from core.action_loop import ActionControl, GovernedActionLoop
from core.contracts import ToolCall
from core.tools_registry import default_tools_registry

ORIGINAL = "الأصلُ المهم الذي لا يجوز أن يضيع"


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (tmp_path / "ws-evil").mkdir()          # جارٌ يشترك في البادئة النصّية
    (root / "doc.md").write_text(ORIGINAL, encoding="utf-8")
    return root


@pytest.fixture
def control(workspace):
    return ActionControl(ActionStore(workspace.parent / "control", workspace),
        ToolContext(workspace, Journal(workspace), frozenset({"auto", "logged"})), "test", "write")


@pytest.fixture
def loop():
    specs, handlers = default_tools_registry()
    return GovernedActionLoop(specs, handlers)


def _declared_reversible():
    specs, _ = default_tools_registry()
    return [name for name, spec in specs.items() if spec.reversible]


def test_at_least_one_tool_declares_reversibility(loop):
    """وإلّا صار هذا الملفُّ اختبارًا لا يستطيع الفشل."""
    assert _declared_reversible(), "لا أداةَ رَجعية — فالحارسُ أدناه فراغ"


@pytest.mark.parametrize("name", _declared_reversible())
def test_every_reversible_tool_actually_restores_the_prior_state(loop, workspace, control, name):
    """التعهّدُ يُختبر بالتشغيل: يُكتب، ثم يُرجَع، ثم تُقارن البايتات."""
    result = loop.dispatch_call(
        ToolCall("c1", name, {"path": "doc.md", "content": "مُستبدَل"}),
        control=control)
    assert result.success is True, result.error
    assert result.reversible is True
    assert (workspace / "doc.md").read_text(encoding="utf-8") == "مُستبدَل"

    action_id = result.output.get("action_id")
    assert action_id, "أداةٌ رَجعية يلزمها أن تُرجع معرّفَ فعلٍ يُرجَع به"
    assert loop.rollback(result, control=control, request_id="restore-original")
    assert (workspace / "doc.md").read_text(encoding="utf-8") == ORIGINAL


def test_creating_a_new_file_is_reverted_by_removing_it(loop, workspace, control):
    result = loop.dispatch_call(
        ToolCall("c1", "write_workspace_document",
                 {"path": "notes/fresh.md", "content": "نصّ"}),
        control=control)
    assert result.success is True
    assert (workspace / "notes/fresh.md").is_file()
    assert loop.rollback(result, control=control, request_id="remove-created")
    assert not (workspace / "notes/fresh.md").exists()


# ————— الهروبُ بالبادئة النصّية —————

@pytest.mark.parametrize("path", ["../ws-evil/pwned.txt", "../../outside.txt",
                                  "/etc/passwd", ".git/config",
                                  ".diwan-journal/journal.jsonl"])
def test_the_write_refuses_to_leave_the_workspace_before_any_effect(loop, workspace, control, path):
    """الحظرُ السابق كان `startswith` بلا فاصلٍ، فقبل الجارَ `ws-evil`.

    والأخطرُ أن الأثرَ كان يقع ثم يُرفع الخطأُ من `relative_to` بعده —
    خطأٌ يلي الأثر لا يمنعه. فالتأكيدُ هنا على الاثنين: يُردّ، ولا يُكتب.
    """
    before = sorted(p.name for p in workspace.parent.rglob("*") if p.is_file() and not p.is_relative_to(control.store.directory))
    result = loop.dispatch_call(ToolCall("c1", "write_workspace_document",
                                        {"path": path, "content": "اختراق"}),
                                control=control)
    assert result.success is False
    assert sorted(p.name for p in workspace.parent.rglob("*") if p.is_file() and not p.is_relative_to(control.store.directory)) == before, \
        "وقع أثرٌ رغم الردّ"


def test_a_sibling_directory_sharing_the_name_prefix_is_not_inside(workspace):
    """الحالةُ التي أسقطت الحظرَ السابق، مُثبَتةً على مستوى الدفتر."""
    from agent.journal import JournalRefused
    with pytest.raises((JournalRefused, Exception)) as exc:
        Journal(workspace).write_file("../ws-evil/pwned.txt", "اختراق")
    assert getattr(exc.value, "code", "") in ("path_invalid", "path_escapes_root")
    assert not (workspace.parent / "ws-evil" / "pwned.txt").exists()
