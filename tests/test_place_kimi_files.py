"""أمرُ توزيع ملفّات Kimi كما هو مكتوبٌ في الوثيقة، يُشغَّل هنا على مجلّداتٍ مصطنعة.

الأمرُ يُلصق في الطرفية من `docs/external/PLACE-KIMI-FILES.md`، فالاختبارُ يستخرجه من
الوثيقة نفسها: إن انكسر النصّ المنشور سقط هنا، لا على جهاز المالك.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "external" / "PLACE-KIMI-FILES.md"


def _script() -> str:
    blocks = re.findall(r"```bash\n(.*?)```", DOC.read_text(encoding="utf-8"), re.S)
    assert len(blocks) == 1, "كتلة أمرٍ واحدة في الوثيقة"
    return blocks[0]


def _suite(ids):
    return {"schema_version": 1, "suite_id": "k", "split": "development",
            "description": "d",
            "cases": [{"case_id": i, "capability": "a",
                       "messages": [{"role": "user", "content": "q"}], "reference": "r",
                       "rubric": ["r"], "checks": [], "critical": False} for i in ids]}


def _kimi(tmp: Path, *, tamper=False, unlisted=False) -> Path:
    src = tmp / "kimi-benchmark"
    (src / "open" / "tier_a").mkdir(parents=True)
    (src / "open" / "tier_a" / "kimi_a_001.json").write_text(json.dumps(_suite(["o1"])))
    (src / "open" / "tier_a" / "kimi_a_001.meta.json").write_text("{}")
    (src / "REPORT.md").write_text("# v1.1")
    (src / "disputed.json").write_text("{}")
    sealed = src / "sealed" / "tier_a"
    sealed.mkdir(parents=True)
    raw = json.dumps(_suite(["s1", "s2"])).encode()
    (sealed / "kimi_a_101.json").write_bytes(raw)
    manifest = {"generated_at": "x", "authored_by": "kimi", "files": [
        {"path": "sealed/tier_a/kimi_a_101.json",
         "sha256": hashlib.sha256(raw).hexdigest(),
         "count": 3 if tamper else 2, "capabilities": ["a"]}]}
    (src / "sealed" / "MANIFEST.json").write_text(json.dumps(manifest))
    if unlisted:
        (sealed / "kimi_a_102.json").write_text(json.dumps(_suite(["s3"])))
    return src


def _run(tmp: Path, src: Path) -> subprocess.CompletedProcess:
    diwan = tmp / "diwan"
    (diwan / ".git").mkdir(parents=True, exist_ok=True)
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp), "SRC": str(src),
           "DIWAN": str(diwan), "SEALED_DST": str(tmp / "diwan-sealed" / "kimi_v1")}
    return subprocess.run(["bash", "-c", _script()], cwd=src, env=env,
                          capture_output=True, text=True, timeout=60)


def test_the_published_command_is_valid_bash():
    block = _script()
    lines = block.strip("\n").split("\n")
    # يُلصق في zsh، فيُغلَّف في bash مستقلّ: خروجُه برمزٍ لا يُغلق نافذة الطرفية.
    assert lines[0] == "bash <<'KIMI'" and lines[-1] == "KIMI"
    inner = "\n".join(lines[1:-1])
    assert subprocess.run(["bash", "-n", "-c", inner]).returncode == 0


def test_a_refusal_does_not_kill_the_calling_shell(tmp_path):
    """الصقه في صدفةٍ تفاعلية ثم نفّذ أمرًا بعده: يجب أن يصل إليه."""
    src = tmp_path / "empty"
    src.mkdir()
    shell = _script() + "echo السطر_التالي_بقي_حيًّا\n"
    result = subprocess.run(["bash", "-c", shell], cwd=src, capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert "توقّفت" in result.stdout and "السطر_التالي_بقي_حيًّا" in result.stdout


def test_the_happy_path_places_everything_and_keeps_sealed_out(tmp_path):
    src = _kimi(tmp_path)
    result = _run(tmp_path, src)
    assert result.returncode == 0, result.stdout + result.stderr
    bank = tmp_path / "diwan" / "evaluation" / "banks" / "kimi_v1"
    assert (bank / "open" / "tier_a" / "kimi_a_001.json").is_file()
    assert (bank / "REPORT.md").is_file() and (bank / "disputed.json").is_file()
    assert (bank / "sealed" / "MANIFEST.json").is_file()
    assert sorted(p.name for p in (bank / "sealed").iterdir()) == ["MANIFEST.json"]
    assert (tmp_path / "diwan-sealed" / "kimi_v1" / "tier_a" / "kimi_a_101.json").is_file()
    assert '"s1"' not in result.stdout, "لا يُطبع محتوى محجوب"


@pytest.mark.parametrize("kind", ["tamper", "unlisted"])
def test_a_manifest_that_does_not_match_stops_before_copying(tmp_path, kind):
    src = _kimi(tmp_path, **{kind: True})
    result = _run(tmp_path, src)
    assert result.returncode != 0
    assert "البيان لا يطابق" in result.stdout
    assert not (tmp_path / "diwan" / "evaluation").exists(), "لم يُنسخ شيء"
    assert not (tmp_path / "diwan-sealed").exists()


def test_an_existing_target_is_never_overwritten(tmp_path):
    src = _kimi(tmp_path)
    earlier = tmp_path / "diwan-sealed" / "kimi_v1"
    earlier.mkdir(parents=True)
    (earlier / "keep.json").write_text("{}")
    result = _run(tmp_path, src)
    assert result.returncode != 0 and "موجود من قبل" in result.stdout
    assert [p.name for p in earlier.iterdir()] == ["keep.json"]


DEV = ("arabic_general_v3.json", "agentic_v2.json", "agentic_v2.meta.json")


def test_the_development_suites_are_placed_beside_the_bank(tmp_path):
    src = _kimi(tmp_path)
    for name in DEV:
        (src / name).write_text("{}")
    result = _run(tmp_path, src)
    assert result.returncode == 0, result.stdout + result.stderr
    suites = tmp_path / "diwan" / "evaluation" / "suites"
    assert sorted(p.name for p in suites.iterdir()) == sorted(DEV)


def test_an_existing_development_suite_stops_before_anything_is_copied(tmp_path):
    src = _kimi(tmp_path)
    (src / "agentic_v2.json").write_text('{"new": true}')
    suites = tmp_path / "diwan" / "evaluation" / "suites"
    suites.mkdir(parents=True)
    (suites / "agentic_v2.json").write_text('{"old": true}')
    result = _run(tmp_path, src)
    assert result.returncode != 0 and "موجود من قبل" in result.stdout
    assert (suites / "agentic_v2.json").read_text() == '{"old": true}'
    assert not (tmp_path / "diwan" / "evaluation" / "banks").exists()
    assert not (tmp_path / "diwan-sealed").exists()
