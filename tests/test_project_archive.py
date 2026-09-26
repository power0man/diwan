"""ج٩ (#45) الشطر الثاني: استيرادُ مشروعٍ مضغوط إلى مساحة المشروع، وتصديرُ مجلّدٍ منها.

- **الاستيرادُ كلُّه أو لا شيء:** مسارٌ غير آمن، أو حجمٌ فوق الحدّ، أو حجمٌ معلَنٌ كاذب، أو مجلّدٌ قائم،
  يردّ الأرشيفَ قبل أن يُكتب ملفّ.
- **ما يُتخطّى ويُسمّى:** المخفيُّ ومخلّفاتُ الأنظمة، ويُنزع المجلّدُ الجامع.
- **كلُّ ملفٍّ مستورَد قيدٌ في دفتر الرجوع:** فيُرجع ما يعدّله الوكيلُ بعدها إلى المستورَد.
- **التصديرُ حتميّ:** بلا المخفيّ ولا حالة ديوان، والاستيرادُ ثم التصديرُ يعيدان الملفّاتِ نفسَها.
"""
from __future__ import annotations

import base64
import io
import stat
import uuid
import zipfile

import pytest

from agent.journal import MAX_BYTES, Journal
from services.project_archive import (MAX_FILES, ArchiveRefused, export_archive, import_archive,
                                      read_archive)
from webui.server import LocalApp


def _zip(entries: dict, *, symlink: str | None = None, lie: str | None = None) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    raw = buffer.getvalue()
    if lie:                                       # يعلن الأرشيفُ حجمًا غير ما يحمله
        raw = _lie(raw, lie)
    return base64.b64encode(raw).decode("ascii")


def _lie(raw: bytes, name: str) -> bytes:
    """يُصغّر الحجمَ المعلَن للملف في الدليل المركزيّ، فيقرأ القارئُ أكثرَ مما أُعلن."""
    source = zipfile.ZipFile(io.BytesIO(raw))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as out:
        for info in source.infolist():
            out.writestr(info.filename, source.read(info.filename))
    data = bytearray(buffer.getvalue())
    marker = b"PK\x01\x02"
    at = data.find(marker)
    while at != -1:
        name_len = int.from_bytes(data[at + 28:at + 30], "little")
        if data[at + 46:at + 46 + name_len].decode() == name:
            data[at + 24:at + 28] = (1).to_bytes(4, "little")        # الحجمُ غير المضغوط المعلَن: ١
        at = data.find(marker, at + 1)
    return bytes(data)


@pytest.fixture
def workspace(tmp_path):
    root = (tmp_path / "ws").resolve()
    root.mkdir(mode=0o700)
    return root


PROJECT = {"proj/src/app.py": "print('مرحبا')\n", "proj/README.md": "# مشروع\n",
           "proj/.git/config": "[core]\n", "proj/.env": "TOKEN=سرّ\n", "proj/src/__pycache__/app.pyc": b"\x00",
           "__MACOSX/proj/._README.md": b"\x00", "proj/data/table.csv": "أ,ب\n1,2\n"}


def test_an_archive_is_imported_file_by_file_through_the_journal(workspace):
    out = import_archive(workspace, "مشروعي", _zip(PROJECT))
    assert out["status"] == "imported" and out["files"] == 3
    assert (workspace / "مشروعي" / "src" / "app.py").read_text(encoding="utf-8") == "print('مرحبا')\n"
    assert (workspace / "مشروعي" / "data" / "table.csv").read_text(encoding="utf-8") == "أ,ب\n1,2\n"
    assert sorted(out["skipped"]) == ["__MACOSX/proj/._README.md", "proj/.env", "proj/.git/config",
                                      "proj/src/__pycache__/app.pyc"]
    assert not (workspace / "مشروعي" / ".env").exists()
    journal = Journal(workspace)
    assert sorted(a.path for a in journal.actions()) == ["مشروعي/README.md", "مشروعي/data/table.csv",
                                                         "مشروعي/src/app.py"]
    assert [a.action_id for a in journal.actions()] == out["journal_action_ids"]


def test_an_agent_edit_after_import_reverts_to_the_imported_file(workspace):
    import_archive(workspace, "p", _zip({"a/x.py": "x = 1\n", "a/y.py": "y = 2\n"}))
    journal = Journal(workspace)
    edit, _, _ = journal.edit_file("p/x.py", "x = 1", "x = 10")
    journal.revert(edit.action_id)
    assert (workspace / "p" / "x.py").read_text(encoding="utf-8") == "x = 1\n"


def test_a_single_common_folder_is_stripped_only_when_every_file_is_under_it(workspace):
    files, _ = read_archive(_zip({"top/a.txt": "أ", "top/b/c.txt": "ب"}))
    assert set(files) == {"a.txt", "b/c.txt"}
    files, _ = read_archive(_zip({"top/a.txt": "أ", "root.txt": "ر"}))
    assert set(files) == {"top/a.txt", "root.txt"}


@pytest.mark.parametrize("entries,extra,code", [
    ({"../escape.txt": "x"}, {}, "archive_path_unsafe"),
    ({"/etc/passwd": "x"}, {}, "archive_path_unsafe"),
    ({"a\\b.txt": "x"}, {}, "archive_path_unsafe"),
    ({"C:/win.txt": "x"}, {}, "archive_path_unsafe"),
    ({"ok.txt": "x"}, {"symlink": "link"}, "archive_path_unsafe"),
    ({"a.txt": "x" * (MAX_BYTES + 1)}, {}, "archive_file_too_large"),
    ({"a.txt": "x" * 10, "b.txt": "y"}, {"lie": "a.txt"}, "archive_corrupt"),
    ({".hidden": "x"}, {}, "archive_empty"),
])
def test_an_unsafe_archive_is_refused_whole_before_anything_is_written(workspace, entries, extra, code):
    entries = {**entries, "good/file.txt": "يُكتب لو قُبل الأرشيف"} if code != "archive_empty" else entries
    with pytest.raises(ArchiveRefused) as err:
        import_archive(workspace, "p", _zip(entries, **extra))
    assert err.value.code == code
    assert not (workspace / "p").exists() and Journal(workspace).actions() == []


def test_too_many_files_are_refused(workspace):
    with pytest.raises(ArchiveRefused) as err:
        read_archive(_zip({f"f{i}.txt": "x" for i in range(MAX_FILES + 1)}))
    assert err.value.code == "archive_too_many_files"


@pytest.mark.parametrize("value,code", [("not base64!", "archive_invalid"),
                                        (base64.b64encode(b"not a zip").decode(), "archive_invalid"),
                                        ("A" * 600_000, "archive_too_large")])
def test_what_is_not_a_zip_is_refused_by_name(value, code):
    with pytest.raises(ArchiveRefused) as err:
        read_archive(value)
    assert err.value.code == code


@pytest.mark.parametrize("folder", ["", ".hidden", "a/b", "a\\b", " p", "x" * 81, "agent-control\x00"])
def test_the_target_is_a_single_visible_folder(workspace, folder):
    with pytest.raises(ArchiveRefused) as err:
        import_archive(workspace, folder, _zip({"a.txt": "x"}))
    assert err.value.code == "folder_invalid"


def test_an_existing_folder_is_never_written_over(workspace):
    (workspace / "p").mkdir(mode=0o700)
    (workspace / "p" / "keep.txt").write_text("للمالك\n", encoding="utf-8")
    with pytest.raises(ArchiveRefused) as err:
        import_archive(workspace, "p", _zip({"keep.txt": "فوقه"}))
    assert err.value.code == "import_target_exists"
    assert (workspace / "p" / "keep.txt").read_text(encoding="utf-8") == "للمالك\n"


def test_export_is_deterministic_and_leaves_out_hidden_and_internal_state(workspace):
    import_archive(workspace, "p", _zip({"s/a.py": "a\n", "b.md": "ب\n"}))
    first, second = export_archive(workspace, "p"), export_archive(workspace, "p")
    assert first["archive"] == second["archive"] and first["files"] == 2 and first["name"] == "p.zip"
    names = zipfile.ZipFile(io.BytesIO(base64.b64decode(first["archive"]))).namelist()
    assert names == ["b.md", "s/a.py"]
    everything = zipfile.ZipFile(io.BytesIO(base64.b64decode(export_archive(workspace, "")["archive"]))).namelist()
    assert everything == ["p/b.md", "p/s/a.py"]                     # لا .diwan-journal ولا مخفيّ


def test_import_then_export_returns_the_same_files(workspace):
    files = {"proj/src/a.py": "a = 1\n", "proj/docs/دليل.md": "# دليل\n", "proj/data.bin": bytes(range(256))}
    import_archive(workspace, "p", _zip(files))
    back = zipfile.ZipFile(io.BytesIO(base64.b64decode(export_archive(workspace, "p")["archive"])))
    assert {n: back.read(n) for n in back.namelist()} == {
        "src/a.py": b"a = 1\n", "docs/دليل.md": "# دليل\n".encode(), "data.bin": bytes(range(256))}


def test_exporting_a_missing_folder_is_refused(workspace):
    with pytest.raises(ArchiveRefused) as err:
        export_archive(workspace, "absent")
    assert err.value.code == "export_folder_missing"


def test_the_ui_imports_and_exports_through_the_project_workspace(tmp_path):
    root = tmp_path.resolve()
    root.chmod(0o700)
    app = LocalApp(root / "app", model="m", model_version="v" * 64, provider_factory=lambda: None,
                   agent_provider_factory=lambda: None)
    project = app.dispatch({"action": "create_project", "name": "أ"})["id"]
    app.dispatch({"action": "create_session", "project": project, "name": "ب", "mode": "coder"})
    out = app.dispatch({"action": "agent_import", "project": project, "folder": "p",
                        "archive": _zip({"x/a.py": "a\n"})})
    assert out["files"] == 1
    assert "p/a.py" in [f["path"] for f in app.dispatch({"action": "agent_files", "project": project})["files"]]
    exported = app.dispatch({"action": "agent_export", "project": project, "folder": "p"})
    assert zipfile.ZipFile(io.BytesIO(base64.b64decode(exported["archive"]))).namelist() == ["a.py"]
    app.close()


def test_a_lone_file_at_the_root_is_not_taken_for_a_common_folder(workspace):
    out = import_archive(workspace, "p", _zip({"only.txt": "وحيد\n"}))
    assert out["files"] == 1 and (workspace / "p" / "only.txt").read_text(encoding="utf-8") == "وحيد\n"


def test_base64_is_read_strictly(workspace):
    encoded = _zip({"a.txt": "x"})
    with pytest.raises(ArchiveRefused) as err:
        import_archive(workspace, "p", encoded[:8] + "\n!" + encoded[8:])
    assert err.value.code == "archive_invalid" and not (workspace / "p").exists()
