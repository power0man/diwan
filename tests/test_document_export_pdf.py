"""ج١٠ (الجزء ٢، #46): PDF باتّجاهٍ عربيّ بتحويل docx في LibreOffice — فحصٌ آليٌّ لما يراه القارئ.

قسمان:
- **التحويلُ الحقيقيّ** (LibreOffice وpoppler-utils): كلُّ سطرٍ ينتهي عند الهامش الأيمن والقصيرُ منها لا يبلغ
  النصف الأيسر، والكلماتُ تعود بترتيب القراءة، وأعمدةُ الجدول تجري من اليمين، والخطوطُ مضمَّنة. ويُتخطّى بالاسم حيث
  تغيب الأدوات، إلا في مهمّة `export-pdf` في CI التي تضبط `DIWAN_REQUIRE_PDF=1` فيصير التخطّي سقوطًا.
- **محوِّلٌ مزيّف** (يعمل دائمًا): الغيابُ رفضٌ مسمًّى لا يكتب شيئًا، والمهلةُ تقتل مجموعةَ العمليات كلَّها،
  والأمرُ ثابتٌ بملفِّ تعريفٍ معزول، والبيئةُ مقصوصةٌ فلا يبلغ المحوِّلَ سرّ، والمخرجُ غيرُ التامّ مرفوض،
  والأداةُ تكتب عبر الدفتر فيُرجع عنها.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import pytest

import documents.export as export_module
from agent.builtin_tools import DEFAULT_TOOLS
from agent.journal import Journal
from agent.registry import ToolContext, ToolRegistry
from core.contracts import ToolCall
from documents.export import ExportRefused, find_converter, pdf_command, to_pdf

TEXT_DOCUMENT = {
    "title": "تقرير المبيعات الربعي",
    "blocks": [
        {"type": "heading", "text": "الملخّص", "level": 1},
        {"type": "paragraph", "text": "ارتفعت المبيعات عن الربع السابق في كل الفروع."},
        {"type": "paragraph", "text": " ".join(["وتفصيل ذلك أن فرع الرياض نما نموًّا ثابتًا طوال الربع"] * 4)},
        {"type": "list", "items": ["فرع الرياض", "فرع جدة"]},
    ],
}
TABLE_DOCUMENT = {"blocks": [{"type": "table", "rows": [["الشهر", "الوحدات", "الفرع"], ["يناير", 1200, "جدة"]]}]}

_DIACRITICS = re.compile("[ً-ٰٟـ]")
_WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="[\d.]+">([^<]*)</word>')


def _plain(text: str) -> str:
    return _DIACRITICS.sub("", unicodedata.normalize("NFKC", text))


def _arabic_words(text: str) -> list[str]:
    return re.findall("[ء-ي]+", _plain(text))


# ————— التحويلُ الحقيقيّ —————

@pytest.fixture(scope="module")
def real_pdfs(tmp_path_factory):
    tools = find_converter(), shutil.which("pdftotext"), shutil.which("pdffonts")
    if not all(tools):
        if os.environ.get("DIWAN_REQUIRE_PDF") == "1":
            pytest.fail("DIWAN_REQUIRE_PDF=1 وLibreOffice أو poppler-utils غائب")
        pytest.skip("تخطٍّ معلن: LibreOffice وpoppler-utils غائبان هنا؛ مهمّةُ export-pdf في CI تمنع هذا التخطّي")
    out = tmp_path_factory.mktemp("pdf")
    paths = {}
    for name, document in (("text", TEXT_DOCUMENT), ("table", TABLE_DOCUMENT)):
        paths[name] = out / f"{name}.pdf"
        paths[name].write_bytes(to_pdf(document))
    return paths


def _words(pdf: Path):
    xml = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    width = float(re.search(r'<page width="([\d.]+)"', xml).group(1))
    return width, [(float(a), float(b), float(c), w) for a, b, c, w in _WORD.findall(xml)]


def test_every_line_ends_at_the_right_margin_and_short_lines_stay_right(real_pdfs):
    width, words = _words(real_pdfs["text"])
    lines: dict[int, list] = {}
    for x_min, y_min, x_max, word in words:
        if word != "•":                              # علامةُ القائمة خارج هامش النص، في يمينه
            lines.setdefault(round(y_min), []).append((x_min, x_max))
    assert len(lines) >= 6                           # العنوان، والفرعيّ، والفقرتان (الثانية أسطر)، والبندان
    rights = [max(x for _, x in line) for line in lines.values()]
    assert max(rights) - min(rights) < 3, rights     # كلُّ سطرٍ ينتهي عند الهامش نفسِه
    assert min(rights) > width * 0.8                 # وذلك الهامشُ هو الأيمن
    lefts = sorted(min(x for x, _ in line) for line in lines.values())
    assert lefts[-1] > width / 2                     # والسطرُ القصير لا يبدأ من اليسار


def test_the_words_come_back_in_reading_order(real_pdfs):
    text = subprocess.run(["pdftotext", "-layout", str(real_pdfs["text"]), "-"],
                          capture_output=True, text=True, check=True).stdout
    expected = [TEXT_DOCUMENT["title"]] + [b.get("text", "") for b in TEXT_DOCUMENT["blocks"]] + \
        TEXT_DOCUMENT["blocks"][-1]["items"]
    assert _arabic_words(text) == _arabic_words(" ".join(expected))


def test_table_columns_run_from_the_right(real_pdfs):
    _, words = _words(real_pdfs["table"])
    header = {_plain(w[::-1]): x_min for x_min, _, _, w in words}  # الكلمةُ في المربّع بترتيبها المرئيّ
    assert header["الشهر"] > header["الوحدات"] > header["الفرع"]


def test_every_font_is_embedded(real_pdfs):
    for pdf in real_pdfs.values():
        rows = subprocess.run(["pdffonts", str(pdf)], capture_output=True, text=True, check=True).stdout.splitlines()[2:]
        assert rows and all(row.split()[-5] == "yes" for row in rows), rows


# ————— محوِّلٌ مزيّف —————

_PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def _fake(tmp_path: Path, body: str) -> str:
    """محوِّلٌ مزيّف يتلقّى الأمرَ كما يتلقّاه soffice، ويسجّل ما رآه في ملفٍّ ثابت."""
    script = tmp_path / "fake-soffice"
    script.write_text(
        f"#!{sys.executable}\nimport json, os, pathlib, subprocess, sys, time\nargs = sys.argv[1:]\n"
        f"log = pathlib.Path({str(tmp_path / 'seen.json')!r})\n"
        "out = pathlib.Path(args[args.index('--outdir') + 1])\n"
        "log.write_text(json.dumps({'argv': args, 'env': dict(os.environ), 'cwd': os.getcwd()}))\n" + body,
        encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def _writes(data: bytes) -> str:
    return f"out.mkdir()\n(out / 'document.pdf').write_bytes({data!r})\n"


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state not in ("Z", "X")


def test_a_missing_converter_is_refused_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(export_module, "_MAC_SOFFICE", str(tmp_path / "absent" / "soffice"))
    assert find_converter() is None
    with pytest.raises(ExportRefused) as err:
        to_pdf(TEXT_DOCUMENT)
    assert err.value.code == "pdf_converter_unavailable"
    with pytest.raises(ExportRefused) as err:
        to_pdf(TEXT_DOCUMENT, converter=str(tmp_path / "not-there"))
    assert err.value.code == "pdf_converter_unavailable"


def test_an_invalid_document_is_refused_before_any_converter_is_sought(monkeypatch):
    monkeypatch.setattr(export_module, "find_converter", lambda: pytest.fail("بُحث عن محوِّلٍ لمستندٍ فاسد"))
    with pytest.raises(ExportRefused) as err:
        to_pdf({"blocks": []})
    assert err.value.code == "document_invalid"


def test_the_command_is_fixed_isolated_and_gets_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "not-for-the-converter")
    monkeypatch.setenv("GEMINI_API_KEY", "nor-this")
    raw = to_pdf(TEXT_DOCUMENT, converter=_fake(tmp_path, _writes(_PDF)))
    assert raw == _PDF
    seen = json.loads((tmp_path / "seen.json").read_text())
    workdir = Path(seen["cwd"])
    assert seen["argv"] == pdf_command("x", workdir)[1:]
    assert seen["argv"][0] == f"-env:UserInstallation={(workdir / 'profile').as_uri()}"
    assert not any(word in arg for arg in seen["argv"] for word in _arabic_words(json.dumps(TEXT_DOCUMENT)))
    assert "OLLAMA_API_KEY" not in seen["env"] and "GEMINI_API_KEY" not in seen["env"]
    added_by_the_fake = {"LC_CTYPE"}                # بايثونُ المزيّف يضيفه لنفسه (PEP 538)، لا to_pdf
    assert set(seen["env"]) <= set(export_module._ENV_KEEP) | {"HOME", "TMPDIR", "TEMP", "TMP"} | added_by_the_fake
    assert seen["env"]["HOME"] == str(workdir)
    assert not workdir.exists()                      # المجلّدُ المؤقّت يُمحى بعد التحويل


@pytest.mark.parametrize("body", [
    "sys.exit(3)",
    "pass",                                          # خرج صفرًا ولم يكتب شيئًا
    _writes(b"<html>not a pdf</html>"),
    _writes(b"%PDF-1.4\n...truncated"),              # بلا %%EOF: ملفٌّ مبتور
    _writes(_PDF) + "sys.exit(1)\n",                 # كتب PDF ثم أعلن فشله: لا يُؤخذ بنصف نجاح
])
def test_a_failed_or_partial_conversion_is_refused_by_name(tmp_path, body):
    with pytest.raises(ExportRefused) as err:
        to_pdf(TEXT_DOCUMENT, converter=_fake(tmp_path, body))
    assert err.value.code == "pdf_conversion_failed"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="حالةُ العمليات من /proc")
def test_a_hung_converter_is_killed_with_its_children(tmp_path):
    pids = tmp_path / "pids.json"
    body = ("child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"pathlib.Path({str(pids)!r}).write_text(json.dumps([os.getpid(), child.pid]))\n"
            "time.sleep(60)\n")
    started = time.monotonic()
    with pytest.raises(ExportRefused) as err:
        to_pdf(TEXT_DOCUMENT, converter=_fake(tmp_path, body), timeout_s=2)
    assert err.value.code == "pdf_conversion_failed" and time.monotonic() - started < 20
    deadline = time.monotonic() + 5
    while any(_alive(pid) for pid in json.loads(pids.read_text())) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not any(_alive(pid) for pid in json.loads(pids.read_text()))


@pytest.fixture
def context(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return ToolContext(root=root, journal=Journal(root), allowed_consents=frozenset({"auto", "logged"}))


def _export(context, path):
    return ToolRegistry(*DEFAULT_TOOLS).invoke(
        ToolCall("x1", "export_document", {"path": path, "document": TEXT_DOCUMENT}), context)


def test_the_tool_writes_the_pdf_through_the_journal_and_is_reverted(tmp_path, context, monkeypatch):
    converter = _fake(tmp_path, _writes(_PDF))
    monkeypatch.setattr(export_module, "find_converter", lambda: converter)
    result = _export(context, "reports/q1.pdf")
    assert result["status"] == "ok" and result["format"] == "pdf"
    assert (context.root / "reports/q1.pdf").read_bytes() == _PDF
    (action,) = context.journal.actions()
    assert context.journal.revert(action.action_id)["status"] == "reverted"
    assert not (context.root / "reports/q1.pdf").exists()


def test_the_tool_without_a_converter_refuses_and_writes_nothing(context, monkeypatch):
    monkeypatch.setattr(export_module, "find_converter", lambda: None)
    assert _export(context, "q1.pdf")["code"] == "pdf_converter_unavailable"
    assert context.journal.actions() == [] and not any(context.root.glob("q1.*"))



def test_a_path_outside_the_workspace_is_refused_before_any_conversion(context, monkeypatch):
    monkeypatch.setattr(export_module, "find_converter", lambda: pytest.fail("حُوِّل مستندٌ لمسارٍ مرفوض"))
    assert _export(context, "../outside.pdf")["code"] in {"path_escapes_root", "path_invalid"}
    assert context.journal.actions() == []

# ————— مهمّةُ CI التي تمنع التخطّي —————

def test_the_ci_job_runs_these_tests_with_no_skip_offline_and_read_only():
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "export-pdf.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read\n" in workflow and "secrets." not in workflow
    assert "libreoffice-writer-nogui poppler-utils" in workflow
    run = re.search(r"docker run [^\n]*\n[^\n]*\n[^\n]*\n", workflow).group(0)
    assert "--network none" in run and "-e DIWAN_REQUIRE_PDF=1" in run and ":/w:ro" in run
    assert "tests/test_document_export_pdf.py" in run
