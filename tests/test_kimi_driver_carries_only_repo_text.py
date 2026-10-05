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



def test_the_memory_assignment_is_bundled_with_its_own_header(tmp_path):
    """ملاحظاتُ Codex على #129: الحزمةُ كانت تحمل KIMI-NEXT.md دائمًا، فلا طريقَ موثّقًا لإرسال تكليف الذاكرة؛ ثم حملت
    أولَ فقرةٍ من رأس v1.2 وفيها «سلّم في kimi-benchmark/»، فيتعارض الأمران ويُكتب تقريرُ الذاكرة فوق تقرير v1.2."""
    env = dict(os.environ, KIMI_WORK=str(tmp_path), DIWAN=str(ROOT), KIMI_TASK="memory")
    done = subprocess.run(["bash", str(DRIVER), "bundle"], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    produced = Path(done.stdout.strip()).read_text(encoding="utf-8")
    brief = (ROOT / "docs" / "KIMI-BENCHMARK-BRIEF.md").read_text(encoding="utf-8")
    independence = brief[brief.index("## ٠ — "):brief.index("## ٢ — ")]
    expected = (_after_first_rule(ROOT / "docs" / "external" / "KIMI-MEMORY-HEADER.md") + "\n"
                + independence + "\n"
                + _after_first_rule(ROOT / "docs" / "external" / "KIMI-MEMORY-BANK.md"))
    assert produced == expected
    assert "current/open" not in produced and "KIMI-NEXT" not in produced
    # موضعُ التسليم واحد: kimi-memory/، ولا أمرَ بالتسليم في kimi-benchmark/، ولا عقدُ تسليم بنك v1.2 من التكليف العامّ
    # (open/ وsealed/ وMANIFEST)؛ وقواعدُ الاستقلال حاضرة (ملاحظتا Codex على #129)
    assert "سلّم في `kimi-memory/`" in produced and "سلّم في `kimi-benchmark/`" not in produced
    assert "## ١ — قواعدُ الاستقلال" in produced
    for contract in ("## ١١ — ما تُسلِّمه", "sealed/MANIFEST.json", "├── open/", "## ٨ — الشطرُ المحجوب"):
        assert contract not in produced
    bad = subprocess.run(["bash", str(DRIVER), "bundle"], capture_output=True, text=True,
                         env={**env, "KIMI_TASK": "other"})
    assert bad.returncode != 0


def test_a_brief_whose_sections_moved_is_refused_not_cut_by_guess(tmp_path):
    """إن لم يوجد §٢ في التكليف العامّ لاقتطع awk منه حتى آخره، ومعه عقدُ تسليم بنك v1.2؛ فالأداةُ ترفض بدل أن تخمّن."""
    diwan = tmp_path / "diwan"
    for rel in ("docs/external/KIMI-MEMORY-HEADER.md", "docs/external/KIMI-MEMORY-BANK.md"):
        (diwan / rel).parent.mkdir(parents=True, exist_ok=True)
        (diwan / rel).write_text((ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")
    brief = (ROOT / "docs" / "KIMI-BENCHMARK-BRIEF.md").read_text(encoding="utf-8")
    (diwan / "docs" / "KIMI-BENCHMARK-BRIEF.md").write_text(brief.replace("## ٢ — ", "## 2 — "), encoding="utf-8")
    env = dict(os.environ, KIMI_WORK=str(tmp_path / "work"), DIWAN=str(diwan), KIMI_TASK="memory")
    done = subprocess.run(["bash", str(DRIVER), "bundle"], capture_output=True, text=True, env=env)
    assert done.returncode != 0 and "§٠ و§٢" in done.stderr
    assert not list((tmp_path / "work").rglob("prompt-*.txt"))


def test_the_memory_brief_documents_exactly_the_fields_the_validator_accepts():
    """ملاحظةُ Codex على #129: جدولُ الشكل أسقط `quarantined`، فبنكٌ يتبع التكليفَ حرفيًّا يرفضه المدقّقُ في كلِّ سيناريو حقن."""
    import re
    from evaluation.memory_bank import EXPECTS, OPS
    brief = (ROOT / "docs" / "external" / "KIMI-MEMORY-BANK.md").read_text(encoding="utf-8")
    documented = {}
    for shape in re.findall(r'`(\{"(?:op|expect)": "\w+"[^`]*\})`', brief):
        kind = re.match(r'\{"(op|expect)": "(\w+)"', shape).groups()
        documented.setdefault(kind, set()).update(re.findall(r'(?:\{|,\s*)"(\w+)"', shape))
    accepted = {**{("op", k): v for k, v in OPS.items()},
                **{("expect", k): v | ({"quarantined"} if k == "context" else set()) for k, v in EXPECTS.items()}}
    assert documented == accepted
    assert '"quarantined": true' in brief.split("### ما يُرفض به البنكُ كلُّه", 1)[1]


def test_a_memory_run_points_to_the_memory_tool_not_the_bank_intake(tmp_path):
    """ملاحظةُ Codex على #129: بعد تشغيل الذاكرة كانت الأداةُ توجّه إلى inspect/intake/place، وهي لشجرة open/sealed."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "kimi"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    work = tmp_path / "work"
    (work / "logs").mkdir(parents=True)
    env = dict(os.environ, KIMI_WORK=str(work), DIWAN=str(ROOT), KIMI_TASK="memory",
               PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    done = subprocess.run(["bash", str(DRIVER), "run"], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    assert "tools/evaluate_memory.py --suite" in done.stdout and "intake'" not in done.stdout
    # التسليمُ في kimi-memory/ لا في kimi-benchmark/ حيث تسليمُ v1.2، ولا يُكتب فوق تسليمٍ قائم (ملاحظة Codex على #129)
    assert f"{work}/kimi-memory/memory_kimi_v1.json" in done.stdout and "kimi-benchmark/memory" not in done.stdout
    (work / "kimi-benchmark").mkdir()
    (work / "kimi-benchmark" / "REPORT.md").write_text("تسليم v1.2", encoding="utf-8")
    again = subprocess.run(["bash", str(DRIVER), "run"], capture_output=True, text=True, env=env)
    assert again.returncode == 0, again.stderr
    for delivered in ("REPORT.md", "memory_kimi_v1.json"):
        (work / "kimi-memory" / delivered).write_text("{}", encoding="utf-8")
        refused = subprocess.run(["bash", str(DRIVER), "run"], capture_output=True, text=True, env=env)
        assert refused.returncode != 0 and "تسليمُ ذاكرةٍ قائم" in refused.stderr
        (work / "kimi-memory" / delivered).unlink()
    assert (work / "kimi-benchmark" / "REPORT.md").read_text(encoding="utf-8") == "تسليم v1.2"
    for cmd in ("inspect", "intake", "place"):
        refused = subprocess.run(["bash", str(DRIVER), cmd], capture_output=True, text=True, env=env)
        assert refused.returncode != 0 and "evaluate_memory" in refused.stderr
    plain = subprocess.run(["bash", str(DRIVER), "run"], capture_output=True, text=True,
                           env={**env, "KIMI_TASK": "next"})
    assert plain.returncode == 0 and "intake'" in plain.stdout


FAKE_KIMI = """#!/bin/sh
python3 - <<'PY'
import json, os
keys = ("KIMI_MODEL_NAME", "KIMI_MODEL_PROVIDER_TYPE", "KIMI_MODEL_BASE_URL", "KIMI_MODEL_CAPABILITIES")
seen = {k: os.environ.get(k) for k in keys}
seen["api_key_set"] = bool(os.environ.get("KIMI_MODEL_API_KEY"))
open("seen.json", "w").write(json.dumps(seen))
PY
"""


def _run_backend(tmp_path, backend, **extra):
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    (fake / "kimi").write_text(FAKE_KIMI)
    (fake / "kimi").chmod(0o755)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    env = dict(os.environ, KIMI_WORK=str(work), DIWAN=str(ROOT), KIMI_BACKEND=backend,
               PATH=f"{fake}:{os.environ['PATH']}", **extra)
    done = subprocess.run(["bash", str(DRIVER), "run"], capture_output=True, text=True, env=env)
    seen = work / "seen.json"
    return done, (__import__("json").loads(seen.read_text()) if seen.exists() else None)


@pytest.mark.parametrize("backend, model, url", [
    ("ollama", "kimi-k2.6:cloud", "http://localhost:11434/v1"),
    ("hf", "moonshotai/Kimi-K2.6", "https://router.huggingface.co/v1"),
])
def test_each_backend_passes_its_model_through_the_environment_only(tmp_path, backend, model, url):
    token = tmp_path / "token"
    token.write_text("hf_fake_for_test\n")
    done, seen = _run_backend(tmp_path, backend, HF_TOKEN_PATH=str(token))
    assert done.returncode == 0, done.stderr
    assert seen == {"KIMI_MODEL_NAME": model, "KIMI_MODEL_PROVIDER_TYPE": "openai", "KIMI_MODEL_BASE_URL": url,
                    "KIMI_MODEL_CAPABILITIES": "tool_use,thinking", "api_key_set": True}
    assert f"المزوّد: {backend}، والنموذج: {model}" in done.stdout
    assert "hf_fake_for_test" not in done.stdout + done.stderr, "المفتاحُ لا يُطبع"


def test_the_default_backend_leaves_the_kimi_environment_untouched(tmp_path):
    done, seen = _run_backend(tmp_path, "kimi")
    assert done.returncode == 0, done.stderr
    assert seen["KIMI_MODEL_NAME"] is None and not seen["api_key_set"]


def test_an_unknown_backend_is_refused_before_anything_runs(tmp_path):
    done, seen = _run_backend(tmp_path, "openrouter")
    assert done.returncode != 0 and "KIMI_BACKEND مجهول" in done.stderr
    assert seen is None


def test_hf_without_a_token_file_stops(tmp_path):
    done, seen = _run_backend(tmp_path, "hf", HF_TOKEN_PATH=str(tmp_path / "missing"))
    assert done.returncode != 0 and "لا توكن hf" in done.stderr and seen is None


def test_the_intake_forwards_the_sandbox_receipt_so_its_report_can_witness_the_general_number(tmp_path):
    """ملاحظةُ Codex على #312: الأمرُ الموثّق كان يستدعي kimi_intake بـ--open-only وحده، فيُردّ كلُّ استلامٍ
    عند الرقم العام (needs_sandbox). فـSANDBOX_RECEIPT يمرّر خيارات الحاوية، وغيابُه يُنبَّه عليه."""
    fake = tmp_path / "python"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGS_OUT"\n', encoding="utf-8")
    fake.chmod(0o755)
    work = tmp_path / "work"
    (work / "kimi-benchmark").mkdir(parents=True)
    args_out = tmp_path / "args.txt"
    env = dict(os.environ, KIMI_WORK=str(work), DIWAN=str(ROOT), PYTHON=str(fake), ARGS_OUT=str(args_out),
               OPEN_ONLY="1", SANDBOX_RECEIPT="/r/receipt.json", SANDBOX_WORKSPACE="/w")
    done = subprocess.run(["bash", str(DRIVER), "intake"], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    args = args_out.read_text(encoding="utf-8").split("\n")
    assert "--open-only" in args and "--sandbox-probes" in args
    assert args[args.index("--sandbox-receipt") + 1] == "/r/receipt.json"
    assert args[args.index("--sandbox-workspace") + 1] == "/w"
    env.pop("SANDBOX_RECEIPT")
    bare = subprocess.run(["bash", str(DRIVER), "intake"], capture_output=True, text=True, env=env)
    assert bare.returncode == 0 and "--sandbox-probes" not in args_out.read_text(encoding="utf-8").split("\n")
    assert "needs_sandbox" in bare.stderr
