"""القائدُ ناقلٌ لا مؤلِّف: ما يصل Kimi نصُّ المستودع كما هو، لا ارتجالَ جلسةٍ عابرة.

قيمةُ Kimi كلُّها في أنه لم يرَ شيفرة ديوان ولا بنوكه. فلو أضاف قائدٌ سطرًا
من عنده — تلميحًا إلى نتيجةٍ أو إلى ما يريده المطوّرون — ألّف Kimi بنكًا
يوافق ما لُقِّن، بلا أثرٍ يدلّ على ذلك: فالحزمةُ تُبنى في اللحظة وتذهب.

فهذا الاختبار يثبت أن `tools/kimi_drive.sh bundle` يُخرج **حرفيًّا** وصلَ
ثلاثة نصوصٍ مودَعة، لا حرفَ زيادة. وأن الأداة ترفض مجلّد عملٍ داخل
`diwan-work`، فمن عرف مكان المستودع قرأه.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DRIVER = ROOT / "tools" / "kimi_drive.sh"


def _after_first_rule(path: Path) -> str:
    """ما بعد أول خطٍّ فاصل، كما يقتطعه sed في الأداة."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.rstrip("\n") == "---":
            return "".join(lines[i + 1 :])
    raise AssertionError(f"لا خطَّ فاصل في {path}")


def _run(args: list[str], work: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, KIMI_WORK=str(work), DIWAN=str(ROOT))
    return subprocess.run(
        ["bash", str(DRIVER), *args], capture_output=True, text=True, env=env
    )


def test_bundle_is_exactly_three_repo_texts(tmp_path):
    done = _run(["bundle"], tmp_path)
    assert done.returncode == 0, done.stderr
    produced = Path(done.stdout.strip()).read_text(encoding="utf-8")

    expected = (
        _after_first_rule(ROOT / "docs" / "external" / "KIMI-WORKSPACE-HEADER.md")
        + "\n"
        + (ROOT / "docs" / "KIMI-BENCHMARK-BRIEF.md").read_text(encoding="utf-8")
        + "\n"
        + _after_first_rule(ROOT / "docs" / "external" / "KIMI-NEXT.md")
    )
    assert produced == expected, "الحزمةُ تخالف نصوصَ المستودع المودَعة"


def test_bundle_never_reveals_where_the_repo_lives(tmp_path):
    done = _run(["bundle"], tmp_path)
    produced = Path(done.stdout.strip()).read_text(encoding="utf-8")
    for needle in (str(ROOT), "diwan-work", str(Path.home())):
        assert needle not in produced, f"الحزمةُ تدلّ Kimi على {needle}"


@pytest.mark.parametrize("bad", ["diwan-work", "diwan-work/diwan/tmp"])
def test_workspace_inside_diwan_work_is_refused(bad):
    """يُفحص بـinspect لا بـsetup: فلو سقط الحارسُ يومًا لم يكتب الاختبارُ نفسُه في المستودع."""
    target = Path.home() / bad
    done = _run(["inspect"], target)
    assert done.returncode != 0
    assert "داخل diwan-work" in done.stderr
    assert not target.exists() or target.samefile(Path.home() / "diwan-work")


def test_run_refuses_when_the_tool_is_absent(tmp_path):
    """بلا أداةٍ لا يُصطنع تشغيل: يتوقّف ويقول السبب، ولا يترك حزمةً معلّقة.

    ويتخطّى نفسَه حيثما تُوجد الأداة — بالمسارين اللذين تسألهما الأداةُ نفسها —
    فلا يستدعي اختبارٌ نموذجًا حقيقيًّا."""
    if shutil.which("kimi") or (Path.home() / ".kimi-code" / "bin" / "kimi").exists():
        pytest.skip("الأداة مركَّبة على هذا الجهاز")
    done = _run(["run"], tmp_path)
    assert done.returncode != 0
    assert "لا توجد أداة kimi" in done.stderr
    assert not list((tmp_path / "prompts").glob("*.txt")) if (tmp_path / "prompts").exists() else True


def test_setup_gives_kimi_the_current_open_bank_and_no_sealed_file(tmp_path):
    """المفتوحُ وبيانُ المحجوب العام إلى current/، ولا ملفَّ محجوب؛ ولا كتابةَ فوق current/ قائم."""
    bank = ROOT / "evaluation" / "banks" / "kimi_v1"
    done = _run(["setup"], tmp_path)
    assert done.returncode == 0, done.stderr
    cur = tmp_path / "current"
    rel = lambda base: {p.relative_to(base) for p in base.rglob("*") if p.is_file()}
    assert rel(cur / "open") == rel(bank / "open")
    assert rel(cur / "sealed") == {Path("MANIFEST.json")}
    marker = next((cur / "open").rglob("*.json"))
    marker.write_text("نسختُه", encoding="utf-8")
    again = _run(["setup"], tmp_path)
    assert again.returncode != 0 and marker.read_text(encoding="utf-8") == "نسختُه"
