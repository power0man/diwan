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
    """المفتوحُ وحده إلى current/open، ولا محجوبَ ولا بيان؛ ولا كتابةَ فوق current/ قائم."""
    bank = ROOT / "evaluation" / "banks" / "kimi_v1"
    done = _run(["setup"], tmp_path)
    assert done.returncode == 0, done.stderr
    cur = tmp_path / "current"
    rel = lambda base: {p.relative_to(base) for p in base.rglob("*") if p.is_file()}
    assert rel(cur / "open") == rel(bank / "open")
    assert not (cur / "sealed").exists(), "Kimi نموذجٌ سحابيّ: لا محجوبَ ولا بيانَ يصله"
    marker = next((cur / "open").rglob("*.json"))
    marker.write_text("نسختُه", encoding="utf-8")
    again = _run(["setup"], tmp_path)
    assert again.returncode != 0 and marker.read_text(encoding="utf-8") == "نسختُه"


def test_setup_refuses_any_existing_current_folder_not_only_its_open_half(tmp_path):
    """ملاحظةُ Codex على #128: current/ قائمٌ بلا open/ وفيه sealed/ كان يمرّ، فيبقى المحجوبُ في متناول Kimi."""
    stale = tmp_path / "current" / "sealed" / "old.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    done = _run(["setup"], tmp_path)
    assert done.returncode != 0
    assert not (tmp_path / "current" / "open").exists()


def test_the_open_only_bundle_never_asks_kimi_for_a_sealed_half(tmp_path):
    """ملاحظةُ Codex على #128: الرأسُ قال «لا sealed/» والتحديثُ بعده طلب بيانًا ومحجوبًا، فتناقضت الحزمة."""
    request = (ROOT / "docs" / "external" / "KIMI-NEXT.md").read_text(encoding="utf-8").split("\n---\n", 1)[1]
    assert "أصدر `sealed/MANIFEST.json`" not in request
    assert "`open/` و`sealed/` كما في v1.1" not in request
    assert "ولا `sealed/`" in request and "هذه الدورةُ للشطر المفتوح وحده" in request
    header = (ROOT / "docs" / "external" / "KIMI-WORKSPACE-HEADER.md").read_text(encoding="utf-8")
    assert "يتقدّم على كل ما يخالفه بعده" in header


def test_the_open_only_chain_judges_agentic_tasks_in_a_container_before_placing(tmp_path):
    """ملاحظةُ Codex على #128: سلسلةُ v1.2 الموثّقة كانت توزّع بلا حكمٍ وكيل، فتدخل مهمّةٌ لا تسقط قبل حلّها."""
    import re
    doc = (ROOT / "docs" / "external" / "KIMI-DRIVER.md").read_text(encoding="utf-8")
    chain = next(b for b in re.findall(r"```bash\n(.*?)```", doc, re.S) if "OPEN_ONLY=1 tools/kimi_drive.sh place" in b)
    judge = chain.index("--agentic")
    assert "--network none" in chain and "--open-only --agentic" in chain
    assert judge < chain.index("UPDATE=1 OPEN_ONLY=1 tools/kimi_drive.sh place")
