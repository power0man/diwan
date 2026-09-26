"""تصديرُ المستندات باتّجاهٍ عربيّ (ج١٠): docx وxlsx بالمكتبة القياسية وحدها، وPDF بتحويل docx.

المستندُ نموذجٌ صغيرٌ محكوم: عنوانٌ وكتلٌ (عنوانٌ فرعيّ، وفقرة، وقائمة، وجدول). ويُكتب بصيغة Office
المفتوحة (OOXML) كما تكتبها برامجها، والاتّجاهُ في كلِّ موضعٍ يحتاجه لا في موضعٍ واحد:
- **docx:** `w:bidi` في كل فقرةٍ وفي القسم، و`w:rtl` ولغةُ `ar-SA` في كل مقطع، و`w:bidiVisual` في كل جدول.
  ولا `w:jc` في الفقرات: في الفقرة ثنائية الاتّجاه تُقرأ `right` نسبةً إلى الاتّجاه فتصير يسارًا (رُئي ذلك في
  LibreOffice)، والمحاذاةُ الافتراضية بدايةُ السطر، وهي اليمين. والجدولُ يُحاذى يمينًا بـ`w:jc` في خصائصه.
- **xlsx:** `rightToLeft="1"` في عرض كل ورقة، والنصُّ `inlineStr` محفوظُ المسافات، والأعدادُ أعداد.

- **pdf:** تشكيلُ العربية فيه يحتاج محرّكَ تشكيلٍ وخطًّا مضمَّنًا، فلا يُكتب بالمكتبة القياسية. يُبنى docx أعلاه
  ثم يحوّله LibreOffice (`soffice --convert-to pdf`، وفيه HarfBuzz) بأمرٍ ثابتٍ لا يؤلّفه النموذج، على ملفٍّ أنتجه
  ديوان في مجلّدٍ مؤقّت، بملفِّ تعريفٍ معزول وبيئةٍ مقصوصة ومهلة. وحيث لا LibreOffice يُرفض بالاسم
  (`pdf_converter_unavailable`) ولا يُكتب شيء. ولا يُضاف اعتمادٌ بايثونيّ (قرارُ #46، الطريق «ب»).
  وبايتاتُ PDF لا تتكرّر حرفيًّا (فيها تاريخُ الإنشاء)؛ فالحتميّةُ في docx الذي يُحوَّل.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import signal
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

MAX_BLOCKS = 400
MAX_TEXT = 20000
MAX_ROWS = 2000
MAX_COLUMNS = 50
FORMATS = ("docx", "xlsx", "pdf")
# محارفُ التحكّم ممنوعةٌ في XML 1.0 إلا الجدولةَ ونهايةَ السطر
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_FIXED_TIME = (2026, 1, 1, 0, 0, 0)     # تاريخٌ ثابت في الأرشيف: المدخلُ نفسُه يعطي البايتات نفسَها


class ExportRefused(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(f"{reason} [{code}]")
        self.code, self.reason = code, reason


def _text(value, field: str) -> str:
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise ExportRefused("document_invalid", f"«{field}» نصٌّ حتى {MAX_TEXT} محرف")
    if _XML_ILLEGAL.search(value):
        raise ExportRefused("document_invalid", f"«{field}» فيه محارفُ تحكّمٍ لا يقبلها المستند")
    return value


def _cell(value):
    if isinstance(value, bool) or value is None:
        raise ExportRefused("document_invalid", "خليّةُ الجدول نصٌّ أو عدد")
    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):
            raise ExportRefused("document_invalid", "عددٌ غير منتهٍ في الجدول")
        return value
    return _text(value, "cell")


def validate(document: dict) -> dict:
    """يعيد المستندَ مطبَّعًا، أو يرفض برمزٍ مسمًّى. لا مفاتيحَ غير معروفة، ولا أحجامَ بلا حدّ."""
    if not isinstance(document, dict) or set(document) - {"title", "blocks"} or "blocks" not in document:
        raise ExportRefused("document_invalid", "المستندُ {title, blocks}")
    title = _text(document.get("title", ""), "title")
    blocks = document["blocks"]
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= MAX_BLOCKS:
        raise ExportRefused("document_invalid", f"كتلٌ بين ١ و{MAX_BLOCKS}")
    out = []
    for block in blocks:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "heading" and set(block) <= {"type", "text", "level"}:
            level = block.get("level", 1)
            if level not in (1, 2, 3):
                raise ExportRefused("document_invalid", "مستوى العنوان ١ أو ٢ أو ٣")
            out.append({"type": kind, "text": _text(block.get("text"), "heading"), "level": level})
        elif kind == "paragraph" and set(block) == {"type", "text"}:
            out.append({"type": kind, "text": _text(block["text"], "paragraph")})
        elif kind == "list" and set(block) == {"type", "items"} and isinstance(block["items"], list) \
                and 1 <= len(block["items"]) <= MAX_ROWS:
            out.append({"type": kind, "items": [_text(item, "item") for item in block["items"]]})
        elif kind == "table" and set(block) <= {"type", "rows", "header", "name"} and isinstance(block.get("rows"), list):
            rows = block["rows"]
            if not 1 <= len(rows) <= MAX_ROWS or not all(isinstance(r, list) and 1 <= len(r) <= MAX_COLUMNS for r in rows):
                raise ExportRefused("document_invalid", f"جدولٌ حتى {MAX_ROWS} صفًّا و{MAX_COLUMNS} عمودًا")
            if len({len(r) for r in rows}) != 1:
                raise ExportRefused("document_invalid", "صفوفُ الجدول متساويةُ الطول")
            header = block.get("header", True)
            if not isinstance(header, bool):
                raise ExportRefused("document_invalid", "«header» صحيحٌ أو خطأ")
            out.append({"type": kind, "rows": [[_cell(c) for c in r] for r in rows], "header": header,
                        "name": _text(block.get("name", ""), "name")[:31]})
        else:
            raise ExportRefused("document_invalid", f"كتلةٌ غير معروفة: {kind!r}")
    return {"title": title, "blocks": out}


def _zip(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, xml in parts.items():
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, xml.encode("utf-8"))
    return buffer.getvalue()


# ————— docx —————

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_RUN = '<w:r><w:rPr>{bold}<w:rtl/><w:lang w:val="ar-SA" w:bidi="ar-SA"/></w:rPr><w:t xml:space="preserve">{text}</w:t></w:r>'


def _paragraph(text: str, *, style: str | None = None, bold: bool = False, numbered: bool = False) -> str:
    props = "".join(filter(None, (
        f'<w:pStyle w:val="{style}"/>' if style else "",
        '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if numbered else "",
        "<w:bidi/>")))
    return f'<w:p><w:pPr>{props}</w:pPr>' + _RUN.format(bold="<w:b/><w:bCs/>" if bold else "",
                                                      text=escape(text)) + "</w:p>"


def _table(block) -> str:
    rows = []
    for index, row in enumerate(block["rows"]):
        cells = "".join(
            '<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>'
            + _paragraph(str(value), bold=block["header"] and index == 0) + "</w:tc>" for value in row)
        rows.append(f"<w:tr>{cells}</w:tr>")
    grid = "".join('<w:gridCol w:w="2000"/>' for _ in block["rows"][0])
    borders = "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
                      for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    return ('<w:tbl><w:tblPr><w:bidiVisual/><w:tblW w:w="0" w:type="auto"/><w:jc w:val="right"/>'
            f"<w:tblBorders>{borders}</w:tblBorders></w:tblPr><w:tblGrid>{grid}</w:tblGrid>"
            + "".join(rows) + "</w:tbl>")


def to_docx(document: dict) -> bytes:
    document = validate(document)
    body = [_paragraph(document["title"], style="Title")] if document["title"] else []
    for block in document["blocks"]:
        if block["type"] == "heading":
            body.append(_paragraph(block["text"], style=f"Heading{block['level']}"))
        elif block["type"] == "paragraph":
            body.append(_paragraph(block["text"]))
        elif block["type"] == "list":
            body.extend(_paragraph(item, numbered=True) for item in block["items"])
        else:
            body.append(_table(block))
            body.append(_paragraph(""))
    section = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
               '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>'
               "<w:bidi/></w:sectPr>")
    heading_styles = "".join(
        f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
        f'<w:pPr><w:bidi/></w:pPr><w:rPr><w:b/><w:bCs/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/></w:rPr></w:style>'
        for sid, name, size in (("Title", "Title", 40), ("Heading1", "heading 1", 32),
                                ("Heading2", "heading 2", 28), ("Heading3", "heading 3", 26)))
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
            "</Types>"),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            "</Relationships>"),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
            "</Relationships>"),
        "word/styles.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="{W}">'
            '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>'
            '<w:sz w:val="24"/><w:szCs w:val="24"/><w:lang w:val="ar-SA" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>'
            '<w:pPrDefault><w:pPr><w:bidi/></w:pPr></w:pPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:bidi/></w:pPr></w:style>'
            f"{heading_styles}</w:styles>"),
        "word/numbering.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering xmlns:w="{W}">'
            '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
            '<w:lvlText w:val="•"/><w:lvlJc w:val="right"/><w:pPr><w:bidi/><w:ind w:right="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
            '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'),
        "word/document.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{W}"><w:body>'
            + "".join(body) + section + "</w:body></w:document>"),
    }
    return _zip(parts)


# ————— xlsx —————

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _sheet(rows, header: bool) -> str:
    out = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{_column(c)}{r}"
            style = ' s="1"' if header and r == 1 else ""
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"{style}><v>{value!r}</v></c>')
            else:
                cells.append(f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>')
        out.append(f'<row r="{r}">{"".join(cells)}</row>')
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{S}" xmlns:r="{R}">'
            '<sheetViews><sheetView rightToLeft="1" workbookViewId="0"/></sheetViews>'
            f'<sheetData>{"".join(out)}</sheetData></worksheet>')


def to_xlsx(document: dict) -> bytes:
    """كلُّ جدولٍ ورقة. ومستندٌ بلا جداول تصير كتلُه النصية عمودًا في ورقةٍ واحدة."""
    document = validate(document)
    tables = [b for b in document["blocks"] if b["type"] == "table"]
    if not tables:
        lines = [[document["title"]]] if document["title"] else []
        for block in document["blocks"]:
            lines += [[item] for item in block["items"]] if block["type"] == "list" else [[block["text"]]]
        tables = [{"rows": lines, "header": bool(document["title"]), "name": ""}]
    names, sheets = [], {}
    for index, table in enumerate(tables, start=1):
        name = re.sub(r"[\[\]:*?/\\]", " ", table["name"]).strip() or f"ورقة {index}"
        while name in names:
            name = f"{name[:28]} {index}"
        names.append(name)
        sheets[f"xl/worksheets/sheet{index}.xml"] = _sheet(table["rows"], table["header"])
    count = len(names)
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                      'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                      for i in range(1, count + 1))
            + "</Types>"),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"),
        "xl/workbook.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{S}" xmlns:r="{R}">'
            '<bookViews><workbookView/></bookViews><sheets>'
            + "".join(f'<sheet name="{escape(n, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
                      for i, n in enumerate(names, start=1))
            + "</sheets></workbook>"),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                      f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, count + 1))
            + f'<Relationship Id="rId{count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"),
        "xl/styles.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="{S}">'
            '<fonts count="2"><font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font></fonts>'
            '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment readingOrder="2"/></xf>'
            '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1"><alignment readingOrder="2"/></xf></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            "</styleSheet>"),
        **sheets,
    }
    return _zip(parts)


# ————— pdf —————

PDF_TIMEOUT_S = 120
MAX_PDF_BYTES = 64 * 1024 * 1024
_MAC_SOFFICE = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
# ما يبلغ المحوِّلَ من البيئة: ما يحتاجه ليعمل ويجد الخطوط، لا مفاتيحَ ولا أسرار
_ENV_KEEP = ("PATH", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL", "FONTCONFIG_FILE", "FONTCONFIG_PATH")


def find_converter() -> str | None:
    """مسارُ soffice: في PATH، أو في موضعه المعروف على الماك؛ وإلّا None."""
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    return _MAC_SOFFICE if os.path.isfile(_MAC_SOFFICE) and os.access(_MAC_SOFFICE, os.X_OK) else None


def pdf_command(converter: str, workdir: Path) -> list[str]:
    """أمرُ التحويل ثابت: لا يدخله من المستند إلا ملفٌّ كتبه ديوان باسمٍ ثابت داخل المجلّد المؤقّت.
    وملفُّ التعريف في المجلّد نفسِه، فلا يمسّ إعداداتِ المستخدم ولا يتصادم تحويلان متزامنان."""
    return [converter, f"-env:UserInstallation={(workdir / 'profile').as_uri()}", "--headless", "--norestore",
            "--nolockcheck", "--convert-to", "pdf", "--outdir", str(workdir / "out"), str(workdir / "document.docx")]


def _stop(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)     # soffice يُطلق عمليّةً بنت؛ تُقتل المجموعةُ كلُّها
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass
    proc.wait()


def to_pdf(document: dict, *, converter: str | None = None, timeout_s: float = PDF_TIMEOUT_S) -> bytes:
    docx = to_docx(document)                         # التحقّقُ أوّلًا: المستندُ الفاسد يُرفض قبل البحث عن محوِّل
    converter = converter or find_converter()
    if not converter:
        raise ExportRefused("pdf_converter_unavailable",
                            "تصديرُ PDF يحتاج LibreOffice (soffice) على هذا الجهاز؛ وdocx وxlsx متاحان بدونه")
    with tempfile.TemporaryDirectory(prefix="diwan-pdf-") as tmp:
        workdir = Path(tmp).resolve()           # على الماك /var رابطٌ إلى /private/var
        tmp = str(workdir)
        (workdir / "document.docx").write_bytes(docx)
        env = {key: os.environ[key] for key in _ENV_KEEP if key in os.environ}
        env.update(HOME=tmp, TMPDIR=tmp, TEMP=tmp, TMP=tmp)
        try:
            proc = subprocess.Popen(pdf_command(converter, workdir), cwd=tmp, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                    start_new_session=os.name == "posix")
        except OSError as exc:
            raise ExportRefused("pdf_converter_unavailable", f"تعذّر تشغيلُ المحوِّل: {type(exc).__name__}") from None
        try:
            _, err = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            _stop(proc)
            raise ExportRefused("pdf_conversion_failed", f"لم ينتهِ التحويلُ في {timeout_s:g} ثانية") from None
        output = workdir / "out" / "document.pdf"
        if proc.returncode != 0 or not output.is_file():
            tail = err.decode("utf-8", "replace").strip()[-300:]
            raise ExportRefused("pdf_conversion_failed", f"لم يُخرج المحوِّلُ PDF (الخروج {proc.returncode}) {tail}".strip())
        if output.stat().st_size > MAX_PDF_BYTES:
            raise ExportRefused("pdf_conversion_failed", f"PDF أكبرُ من {MAX_PDF_BYTES} بايت")
        raw = output.read_bytes()
    if not raw.startswith(b"%PDF-") or b"%%EOF" not in raw[-1024:]:
        raise ExportRefused("pdf_conversion_failed", "مخرجُ المحوِّل ليس PDF تامًّا")
    return raw


def export(document: dict, fmt: str) -> bytes:
    if fmt not in FORMATS:
        raise ExportRefused("format_unsupported", f"الصيغ المتاحة: {'، '.join(FORMATS)}")
    return {"docx": to_docx, "xlsx": to_xlsx, "pdf": to_pdf}[fmt](document)
