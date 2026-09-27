"""حارسُ فهرس السياق (ق٦٧-٥).

الفهرسُ مبنيٌّ من الوثائق نفسِها وحتميّ، و`--check` يسقط حين تتغيّر وثيقةٌ بلا إعادة توليد؛ والكتلةُ المولَّدة في
`AGENTS.md` §٠ لا تغيّر بصمةَ الملف الذي تصفه؛ و`AGENTS.md` يبقى دون حدِّ Codex لملفّ التعليمات؛ والمنجزُ من §٣ في
الأرشيف بنصّه وحده، وما بقي في §٣ مفتوحٌ كلُّه.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import context_index as ci  # noqa: E402

ROW = re.compile(r"^\| [كجغع][٠-٩]+ \|")
# الإيداعُ الذي نُقلت فيه الصفوف: الأرشيفُ يُقارن بنصّ §٣ عنده حين يكون التاريخُ حاضرًا
ARCHIVED_FROM = "113d1b4"


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for rel in [s.path for s in ci.SOURCES] + [ci.INDEX_MD, ci.INDEX_JSON]:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, root / rel)
    return root


def _run(root: Path, *args: str, capsys) -> tuple[int, dict]:
    code = ci.main([*args, "--root", str(root)])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_the_committed_index_is_fresh(capsys):
    code, report = _run(ROOT, "--check", capsys=capsys)
    assert (code, report["status"]) == (0, "verified"), report


def test_writing_is_idempotent_and_a_changed_document_is_stale_until_rewritten(tmp_path, capsys):
    root = _copy(tmp_path)
    assert _run(root, "--check", capsys=capsys)[0] == 0
    status = root / "docs/STATUS.md"
    status.write_text(status.read_text(encoding="utf-8") + "\n## عنوانٌ جديد\n", encoding="utf-8")
    code, report = _run(root, "--check", capsys=capsys)
    assert code == 1 and report["status"] == "index_stale"
    # الفهرسان يتأخّران، ومعهما كتلةُ §٠ لأنها تحمل بصمةَ الوثيقة التي تغيّرت
    assert set(report["files"]) == {ci.INDEX_MD, ci.INDEX_JSON, ci.AGENTS}, "تغيّرُ الوثيقة يجب أن يُسقط الفهرسين والكتلة"
    code, report = _run(root, "--write", capsys=capsys)
    assert code == 0 and set(report["written"]) == {ci.INDEX_MD, ci.INDEX_JSON, ci.AGENTS}
    code, report = _run(root, "--write", capsys=capsys)
    assert code == 0 and report["written"] == [], "الكتابةُ الثانية يجب ألّا تغيّر شيئًا"
    assert _run(root, "--check", capsys=capsys)[0] == 0


def test_the_generated_block_cannot_change_the_digest_it_reports(tmp_path, capsys):
    root = _copy(tmp_path)
    reported = ci.describe(root)["documents"][0]["sha256_12"]
    agents = root / ci.AGENTS
    text = agents.read_text(encoding="utf-8")
    # كتلةٌ عبثية بعدد أسطر الكتلة الحقيقية، فلا تتحرّك أسطرُ العناوين ويبقى الاختبارُ على البصمة وحدها
    rows = len(ci.split_block(text)[1].strip("\n").split("\n"))
    agents.write_text(ci.with_block(text, "\n".join(["> كتلةٌ عبثية"] * rows)), encoding="utf-8")
    assert ci.describe(root)["documents"][0]["sha256_12"] == reported, "الكتلةُ غيّرت بصمةَ AGENTS.md"
    code, report = _run(root, "--check", capsys=capsys)
    assert code == 1 and report["files"] == [ci.AGENTS], "الكتلةُ العبثية وحدها هي المتأخّرة"


def test_write_is_idempotent_whatever_the_old_block_looked_like_and_measures_agents_as_written(tmp_path, capsys):
    """ملاحظتا Codex على #148: كتلةٌ قديمة بعددِ أسطرٍ مختلف كانت تجعل --write ينجح ثم --check يسقط، وكان AGENTS.md
    يُقاس مفرَّغَ الكتلة فيُبخَس حجمُه. صار القياسُ من النصّ البديل، والكتابةُ ثابتةٌ من أول مرّة."""
    root = _copy(tmp_path)
    agents = root / ci.AGENTS
    agents.write_text(ci.with_block(agents.read_text(encoding="utf-8"), "> سطرٌ واحد"), encoding="utf-8")
    code, report = _run(root, "--write", capsys=capsys)
    assert code == 0 and ci.AGENTS in report["written"]
    assert _run(root, "--check", capsys=capsys)[0] == 0, "كتابةٌ واحدة يجب أن تكفي"
    state = json.loads((root / ci.INDEX_JSON).read_text(encoding="utf-8"))
    doc = next(d for d in state["documents"] if d["path"] == ci.AGENTS)
    on_disk = agents.read_text(encoding="utf-8")
    assert doc["bytes"] == len(on_disk.encode("utf-8")) and doc["tokens_estimate"] == ci.tokens_estimate(on_disk)
    assert doc["lines"] == on_disk.count("\n")


def test_a_missing_or_doubled_block_is_a_named_refusal(tmp_path, capsys):
    root = _copy(tmp_path)
    agents = root / ci.AGENTS
    original = agents.read_text(encoding="utf-8")
    begin, end = ci._block_markers()
    agents.write_text(original.replace(begin, "").replace(end, ""), encoding="utf-8")
    code, report = _run(root, "--check", capsys=capsys)
    assert (code, report["code"]) == (1, "generated_block_invalid")
    agents.write_text(original + "\n" + begin + "\n" + end + "\n", encoding="utf-8")
    code, report = _run(root, "--write", capsys=capsys)
    assert (code, report["code"]) == (1, "generated_block_invalid")


def test_agents_md_fits_the_codex_project_doc_budget():
    """Codex يبتر ملفَّ التعليمات بعد project_doc_max_bytes (٣٢٧٦٨ افتراضًا)؛ ما بعده لا يراه أصلًا."""
    assert ci.CODEX_PROJECT_DOC_MAX_BYTES == 32_768
    size = (ROOT / ci.AGENTS).stat().st_size
    assert size <= ci.CODEX_PROJECT_DOC_MAX_BYTES, f"AGENTS.md {size} بايت يتجاوز حدَّ Codex"
    assert ci.budget(ROOT)["agents_md_fits_codex"] is True


def test_the_block_names_every_open_task_and_no_done_row_remains_in_agents_md():
    text = (ROOT / ci.AGENTS).read_text(encoding="utf-8")
    section = text.split("## ٣ — المهام", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.split("\n") if ROW.match(line)]
    assert rows and not any("| منجزة" in line for line in rows), "صفٌّ منجز بقي في §٣ بدل الأرشيف"
    state = ci.describe(ROOT)
    ids = [line.split("|")[1].strip() for line in rows]
    assert [task["id"] for task in state["open_tasks"]] == ids
    _, body, _ = ci.split_block(text)
    assert all(task_id in body for task_id in ids), "الكتلةُ لا تسمّي كلَّ مفتوح"
    assert ci.render_block(state) == body.strip("\n")


def test_the_archive_holds_only_done_rows_and_verbatim_when_history_is_present():
    archive = (ROOT / "docs/TASKS-ARCHIVE.md").read_text(encoding="utf-8")
    rows = [line for line in archive.split("\n") if ROW.match(line)]
    assert len(rows) == 41 and all("| منجزة" in line for line in rows)
    shown = subprocess.run(["git", "-C", str(ROOT), "show", f"{ARCHIVED_FROM}:AGENTS.md"], capture_output=True, text=True)
    if shown.returncode != 0:
        pytest.skip("history_unavailable: الإيداعُ الأصلي ليس في هذه النسخة")
    before = {line for line in shown.stdout.split("\n") if ROW.match(line) and "| منجزة" in line}
    assert set(rows) == before, "صفٌّ في الأرشيف ليس بنصّه الأصلي"


def test_the_index_lists_every_source_with_its_current_digest():
    state = json.loads((ROOT / ci.INDEX_JSON).read_text(encoding="utf-8"))
    assert [d["path"] for d in state["documents"]] == [s.path for s in ci.SOURCES]
    for doc in state["documents"]:
        text = (ROOT / doc["path"]).read_text(encoding="utf-8")
        if doc["path"] == ci.AGENTS:
            text = ci.with_block(text, "")
        assert hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] == doc["sha256_12"], doc["path"]
    assert state["latest_decision"] >= 67
    md = (ROOT / ci.INDEX_MD).read_text(encoding="utf-8")
    assert all(f"`{s.path}`" in md and s.read_when in md for s in ci.SOURCES)
    # عناوينُ AGENTS.md بأسطرها على القرص لا في النصّ المفرَّغ الكتلة (ملاحظة Codex على #148)
    on_disk = (ROOT / ci.AGENTS).read_text(encoding="utf-8").split("\n")
    agents = next(d for d in state["documents"] if d["path"] == ci.AGENTS)
    assert agents["headings"], "لا عناوين"
    for heading in agents["headings"]:
        assert on_disk[heading["line"] - 1] == "#" * heading["level"] + " " + heading["title"], heading
    # ومجموعُ §٠ في الفهرس بلا الفهرس نفسِه، معلَنًا؛ والمجموعُ به في --print-budget
    assert "بلا هذا الفهرس" in md and ci.INDEX_MD in ci.budget(ROOT)["reading_set"]


def test_the_budget_report_is_deterministic_and_names_its_heuristic():
    first, second = ci.budget(ROOT), ci.budget(ROOT)
    assert first == second
    assert tuple(first["reading_set"]) == ci.READING_SET
    assert "tokens_estimate_is_characters_divided_by_three_not_a_tokenizer_count" in first["measurement_limits"]
    assert first["reading_set_total"]["bytes"] == sum(v["bytes"] for v in first["reading_set"].values())


def test_every_reader_is_pointed_at_the_index():
    for name in ("CLAUDE.md", "GEMINI.md"):
        lines = (ROOT / name).read_text(encoding="utf-8").lstrip().splitlines()
        assert lines[:2] == ["@AGENTS.md", "@docs/INDEX.md"], name
    assert "docs/INDEX.md" in json.loads((ROOT / "opencode.json").read_text(encoding="utf-8"))["instructions"]
    agents = (ROOT / ci.AGENTS).read_text(encoding="utf-8")
    assert "docs/INDEX.md" in agents.split("## ١ — ما هو ديوان")[0], "§٠ لا يوجّه إلى الفهرس"
    assert "tools/context_index.py --check" in agents
    from verification_checks import commands
    assert any(check.name == "context-index" for check in commands("python", "node"))
