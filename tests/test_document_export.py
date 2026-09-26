"""ج١٠: تصديرُ المستندات باتّجاهٍ عربيّ — فحصٌ آليٌّ للاتّجاه في كل موضعٍ يحتاجه.

docx: كلُّ فقرةٍ `w:bidi`، وكلُّ مقطعٍ `w:rtl` بلغةٍ عربية، وكلُّ جدولٍ `w:bidiVisual`، والقسمُ `w:bidi`.
xlsx: كلُّ ورقةٍ `rightToLeft="1"`، والنصُّ نصٌّ والعددُ عدد. والنصُّ يعود كما كُتب، والمدخلُ نفسُه يعطي
البايتاتِ نفسَها، والرفضُ مسمًّى، والتصديرُ يُرجع عنه بالدفتر. وPDF في `test_document_export_pdf.py`.
"""
from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET

import pytest

from agent.builtin_tools import DEFAULT_TOOLS
from agent.journal import Journal
from agent.registry import ToolContext, ToolRegistry
from core.contracts import ToolCall
from documents.export import ExportRefused, export, to_docx, to_xlsx

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

DOCUMENT = {
    "title": "تقرير المبيعات الربعي",
    "blocks": [
        {"type": "heading", "text": "الملخّص", "level": 1},
        {"type": "paragraph", "text": "ارتفعت المبيعات ١٢٪ عن الربع السابق، وبلغت 4,500 وحدة في مارس."},
        {"type": "list", "items": ["فرع الرياض: نموٌّ ثابت", "فرع جدة: تراجعٌ طفيف"]},
        {"type": "table", "name": "المبيعات", "rows": [["الشهر", "الوحدات"], ["يناير", 1200], ["فبراير", 1350.5]]},
        {"type": "paragraph", "text": "رموزٌ خاصة: <وسم> & \"اقتباس\" 'مفرد'"},
    ],
}


def _parts(raw: bytes) -> dict[str, ET.Element]:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.testzip() is None
        return {name: ET.fromstring(archive.read(name)) for name in archive.namelist() if name.endswith((".xml", ".rels"))}


def test_every_docx_paragraph_run_table_and_section_is_right_to_left():
    parts = _parts(to_docx(DOCUMENT))
    assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml", "word/styles.xml",
            "word/numbering.xml", "word/_rels/document.xml.rels"} <= set(parts)
    body = parts["word/document.xml"].find(f"{W}body")
    paragraphs = list(body.iter(f"{W}p"))
    assert paragraphs and all(p.find(f"{W}pPr/{W}bidi") is not None for p in paragraphs)
    runs = list(body.iter(f"{W}r"))
    assert runs and all(r.find(f"{W}rPr/{W}rtl") is not None for r in runs)
    assert all(r.find(f"{W}rPr/{W}lang").get(f"{W}bidi") == "ar-SA" for r in runs)
    tables = list(body.iter(f"{W}tbl"))
    assert len(tables) == 1 and all(t.find(f"{W}tblPr/{W}bidiVisual") is not None for t in tables)
    assert all(t.find(f"{W}tblPr/{W}jc").get(f"{W}val") == "right" for t in tables)
    assert body.find(f"{W}sectPr/{W}bidi") is not None


def test_no_paragraph_forces_an_alignment_that_rtl_would_mirror_to_the_left():
    """`w:jc="right"` في فقرةٍ ثنائية الاتّجاه يُقرأ نسبةً إلى الاتّجاه فيصير يسارًا (رُئي في LibreOffice)."""
    parts = _parts(to_docx(DOCUMENT))
    assert not [p for p in parts["word/document.xml"].iter(f"{W}p") if p.find(f"{W}pPr/{W}jc") is not None]
    assert not list(parts["word/styles.xml"].iter(f"{W}jc"))


def test_the_docx_text_comes_back_as_written_and_in_order():
    parts = _parts(to_docx(DOCUMENT))
    texts = [t.text or "" for t in parts["word/document.xml"].iter(f"{W}t")]
    expected = ["تقرير المبيعات الربعي", "الملخّص", DOCUMENT["blocks"][1]["text"], "فرع الرياض: نموٌّ ثابت",
                "فرع جدة: تراجعٌ طفيف", "الشهر", "الوحدات", "يناير", "1200", "فبراير", "1350.5", "",
                DOCUMENT["blocks"][4]["text"]]
    assert texts == expected


def test_every_xlsx_sheet_is_right_to_left_and_keeps_numbers_as_numbers():
    parts = _parts(to_xlsx(DOCUMENT))
    names = [s.get("name") for s in parts["xl/workbook.xml"].iter(f"{S}sheet")]
    assert names == ["المبيعات"]
    sheet = parts["xl/worksheets/sheet1.xml"]
    assert [v.get("rightToLeft") for v in sheet.iter(f"{S}sheetView")] == ["1"]
    cells = {c.get("r"): c for c in sheet.iter(f"{S}c")}
    assert cells["A1"].get("t") == "inlineStr" and cells["A1"].find(f"{S}is/{S}t").text == "الشهر"
    assert cells["A1"].get("s") == "1" and cells["A2"].get("s") is None
    assert cells["B2"].get("t") is None and cells["B2"].find(f"{S}v").text == "1200"
    assert cells["B3"].find(f"{S}v").text == "1350.5"


def test_a_document_without_tables_becomes_one_right_to_left_sheet():
    parts = _parts(to_xlsx({"title": "ملاحظات", "blocks": [{"type": "paragraph", "text": "سطرٌ أول"},
                                                           {"type": "list", "items": ["بندٌ", "بندٌ ثانٍ"]}]}))
    sheet = parts["xl/worksheets/sheet1.xml"]
    assert [v.get("rightToLeft") for v in sheet.iter(f"{S}sheetView")] == ["1"]
    assert [t.text for t in sheet.iter(f"{S}t")] == ["ملاحظات", "سطرٌ أول", "بندٌ", "بندٌ ثانٍ"]


def test_the_same_document_gives_the_same_bytes():
    assert to_docx(DOCUMENT) == to_docx(DOCUMENT) and to_xlsx(DOCUMENT) == to_xlsx(DOCUMENT)


@pytest.mark.parametrize("document", [
    {"blocks": [{"type": "image", "src": "x"}]},
    {"blocks": [{"type": "table", "rows": [["أ", "ب"], ["ج"]]}]},
    {"blocks": [{"type": "paragraph", "text": "محرف تحكم \x07"}]},
    {"blocks": [{"type": "table", "rows": [[True]]}]},
    {"blocks": []},
    {"blocks": [{"type": "heading", "text": "ع", "level": 7}]},
    {"blocks": [{"type": "paragraph", "text": "ن"}], "author": "x"},
])
def test_an_invalid_document_is_refused_by_name(document):
    with pytest.raises(ExportRefused) as err:
        export(document, "docx")
    assert err.value.code == "document_invalid"


def test_an_unknown_format_is_refused_by_name():
    with pytest.raises(ExportRefused) as err:
        export(DOCUMENT, "odt")
    assert err.value.code == "format_unsupported"


@pytest.fixture
def context(tmp_path):
    return ToolContext(root=tmp_path, journal=Journal(tmp_path), allowed_consents=frozenset({"auto", "logged"}))


def _export(context, path, document=DOCUMENT):
    return ToolRegistry(*DEFAULT_TOOLS).invoke(
        ToolCall("x1", "export_document", {"path": path, "document": document}), context)


def test_the_tool_writes_through_the_journal_and_is_reverted(tmp_path, context):
    result = _export(context, "reports/q1.docx")
    assert result["status"] == "ok" and result["format"] == "docx"
    assert (tmp_path / "reports/q1.docx").read_bytes() == to_docx(DOCUMENT)
    (action,) = context.journal.actions()
    assert context.journal.revert(action.action_id)["status"] == "reverted"
    assert not (tmp_path / "reports/q1.docx").exists()


def test_the_tool_refuses_an_unknown_extension_or_document_and_writes_nothing(tmp_path, context):
    assert _export(context, "q1.odt")["code"] == "format_unsupported"
    assert _export(context, "q1.xlsx", {"blocks": []})["code"] == "document_invalid"
    assert context.journal.actions() == [] and not any(tmp_path.glob("q1.*"))
