"""ك٥٠: بنكُ المحلّل مجمَّدٌ ومدقّقُه يقيس.

- الملفّان المودَعان هما ما يولّده `tools/make_analyst_bank.py` بايتًا ببايت: لا تعديلَ يدويَّ بعد التجميد.
- كلُّ مهمّةٍ تسقط على مساحتها الابتدائية، وتمرّ بحلّها المرجعيّ، وتسقط بحلٍّ مرجعيٍّ أُفسد فيه رقمٌ
  أو خليّةٌ أو اتّجاهُ الورقة أو حجمُ الرسم. فالمدقّقُ يميّز الصحيحَ من الخطأ، لا وجودَ الملفّ وحده.
"""
from __future__ import annotations

import io
import json
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

from evaluation.agentic_runner import materialize, validate_agentic_suite, workspace_bytes

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "evaluation" / "suites" / "analyst_v1.json"
SUITE = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
META = json.loads(SUITE_PATH.with_suffix(".meta.json").read_text(encoding="utf-8"))
TASKS = {t["task_id"]: t for t in SUITE["tasks"]}


def _check(task, root: Path) -> subprocess.CompletedProcess:
    command = list(task["success"]["command"])
    command[0] = sys.executable            # في الحاوية python3؛ وهنا مفسّرُ الاختبار نفسُه
    return subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)


def _overlay(root: Path, files: dict) -> None:
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(workspace_bytes(content))


def test_the_bank_is_valid_and_has_forty_tasks_in_nine_categories():
    validate_agentic_suite(SUITE)
    counts: dict[str, int] = {}
    for task_id, item in META["tasks"].items():
        assert TASKS[task_id]["capability"] == f"analyst_{item['category']}"
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    assert len(TASKS) == 40 and counts == {"aggregation": 5, "groupby": 5, "filtering": 4, "topk": 4, "dates": 5,
                                           "joins": 4, "cleaning": 5, "xlsx": 4, "charts": 4}
    assert META["thresholds"] == {"pass_rate": 0.7, "min_category_pass_rate": 0.5}


def test_the_committed_bank_is_exactly_what_the_generator_builds():
    sys.path.insert(0, str(ROOT))
    from tools.make_analyst_bank import build
    tasks, meta = build()
    assert tasks == SUITE["tasks"] and meta == META["tasks"]


def test_expected_values_never_reach_the_agent_workspace():
    for task in SUITE["tasks"]:
        command = task["success"]["command"]
        assert command[:2] == ["python3", "-c"] and len(command) == 3
        assert not any(name.endswith(".py") for name in task["workspace"])


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_each_task_fails_before_and_passes_with_its_reference(task_id, tmp_path):
    task = TASKS[task_id]
    materialize(task, tmp_path)
    before = _check(task, tmp_path)
    assert before.returncode != 0, before.stdout
    _overlay(tmp_path, META["tasks"][task_id]["reference_solution"])
    after = _check(task, tmp_path)
    assert after.returncode == 0, after.stdout + after.stderr


def _tiny_png() -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes([255] * 50) for _ in range(50))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 50, 50, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _corrupt(files: dict) -> dict:
    """يُفسد مخرجًا واحدًا إفسادًا صغيرًا: رقمًا، أو خليّةً أخيرة، أو اتّجاهَ الورقة، أو حجمَ الرسم."""
    out = dict(files)
    for name in sorted(files):
        raw = workspace_bytes(files[name])
        if name.endswith(".png"):
            out[name] = {"base64": __import__("base64").b64encode(_tiny_png()).decode()}
            return out
        if name.endswith(".xlsx"):
            buffer = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(raw)) as src, zipfile.ZipFile(buffer, "w") as dst:
                for item in src.infolist():
                    data = src.read(item.filename)
                    if item.filename.startswith("xl/worksheets/"):
                        data = data.replace(b'rightToLeft="1"', b'rightToLeft="0"')
                    dst.writestr(item, data)
            out[name] = {"base64": __import__("base64").b64encode(buffer.getvalue()).decode()}
            return out
    name = "answer.json" if "answer.json" in files else next(n for n in sorted(files) if n.endswith(".csv"))
    text = workspace_bytes(files[name]).decode("utf-8")
    if name.endswith(".json"):
        value = json.loads(text)
        key = next(iter(value))
        value[key] = value[key] + 1 if isinstance(value[key], (int, float)) else value[key] + "س"
        out[name] = json.dumps(value, ensure_ascii=False)
    else:
        lines = text.rstrip("\n").split("\n")
        cells = lines[-1].split(",")
        try:
            cells[-1] = f"{float(cells[-1]) + 1:.2f}"
        except ValueError:
            cells[-1] += "س"
        out[name] = "\n".join(lines[:-1] + [",".join(cells)]) + "\n"
    return out


@pytest.mark.parametrize("task_id", sorted(TASKS))
def test_a_slightly_wrong_answer_fails(task_id, tmp_path):
    task = TASKS[task_id]
    materialize(task, tmp_path)
    _overlay(tmp_path, _corrupt(META["tasks"][task_id]["reference_solution"]))
    result = _check(task, tmp_path)
    assert result.returncode != 0, f"{task_id}: مدقّقٌ قبل مخرجًا فاسدًا"


@pytest.mark.parametrize("content", [{"base64": "QQ"}, {"base64": "QQ==", "extra": 1}, {"base64": "Q Q=="}, 7,
                                     {"base64": "QR=="}])      # تُفكّ إلى ما تُفكّ إليه QQ==: ملفٌّ واحد بشكلين
def test_a_binary_workspace_file_must_be_canonical_base64(content):
    from core.canonical import PayloadRejected
    task = json.loads(json.dumps(TASKS["an33"]))
    task["workspace"]["extra.bin"] = content
    with pytest.raises(PayloadRejected) as err:
        validate_agentic_suite({**SUITE, "tasks": [task]})
    assert err.value.code == "workspace_invalid"


def test_text_and_binary_files_materialize_byte_for_byte(tmp_path):
    task = TASKS["an33"]
    materialize(task, tmp_path)
    raw = (tmp_path / "inventory.xlsx").read_bytes()
    assert raw[:2] == b"PK" and raw == workspace_bytes(task["workspace"]["inventory.xlsx"])
