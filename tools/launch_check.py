#!/usr/bin/env python3
"""فحصُ الدخان للإطلاق الأول: هل يعمل ديوان على هذا الجهاز؟ (ك٢٨، الخطة §١.٣-٣)

ستُّ خطواتٍ بالترتيب، كلٌّ منها تنتهي بحالةٍ من ثلاث ورمزٍ مسمًّى:
`ok` (عملت)، أو `unavailable` (تعذّرت بحدٍّ معلن: لا محرّك، لا متن)، أو `failed` (عطب).

1. **runtime** — بايثون ≥ 3.11، والوحداتُ تُستورد، وuv.lock حاضر، ونوعُ النسخة (عامة/خاصة).
2. **morphology** — CAMeL Tools وقاعدتُه الصرفية حاضران (ق٥٥: لازمان للإطلاق، والقالبيُّ
   احتياطيٌّ مسمًّى لا بديل)؛ وإلا `camel_missing` أو `camel_db_missing` مع أمر التركيب.
3. **engine** — خادمُ Ollama يجيب على `/api/tags` والمحرّكُ المطلوب مسحوب.
4. **agent_turn** — جولةٌ وكيلة محكومة كاملة في مساحةٍ مؤقّتة: النموذجُ يقرأ ملفًّا بأداة
   `read_file` ويجيب، والسجلُّ يقيّد. بالمحرّك الحيّ إن وُجد؛ وإلا بمزوّدٍ آليّ مكتوبٍ سلفًا
   يُثبت الحلقةَ والأدواتِ والحَجرَ دون النموذج (`mechanism_only`).
5. **policies** — عقدةُ السياسات: المتنُ موضوعٌ محليًّا وإسقاطُه مبنيٌّ واستعلامٌ واحد يعيد شاهدًا؛
   وإلا `corpus_missing` أو `index_missing` (النسخةُ العامة بلا متون، ك٢٩).
6. **ui** — `tools/serve_ui.py --help` يعمل (الواجهةُ تُستورد وتُهيّأ).

    python tools/launch_check.py [--engine qwen3.5:9b] [--base-url http://127.0.0.1:11434] [--json]

الخروج: 0 كلُّها `ok`؛ 3 فيها `unavailable` بلا `failed` (تعذّرٌ معلن — ليس جهازًا جاهزًا للإطلاق
لكنه ليس عطبًا)؛ 1 عطب. **الحدُّ المعلَن:** الجولةُ الحيّة تُثبت أن المحرّك يستعمل أداةً ويجيب،
لا جودةَ جوابه؛ والجودةُ تُقاس بالبنوك (`evaluation/`).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from providers.ollama import DEFAULT_MODEL as DEFAULT_ENGINE  # noqa: E402 — موضعٌ واحد للمحرّك (ق٥٤)

CAMEL_INSTALL = "uv sync --extra morphology"
CAMEL_DB_COMMAND = "uv run camel_data -i morphology-db-msa-r13"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
NOTE_TEXT = "مرحبًا بديوان على هذا الجهاز"
TASK = "اقرأ الملف notes.txt بأداة read_file ثم أخبرني في جملةٍ واحدة بما فيه."


@dataclass(frozen=True)
class Step:
    step: str
    status: str            # ok | unavailable | failed
    code: str
    detail: str = ""


def check_runtime(root: Path) -> Step:
    if sys.version_info < (3, 11):
        return Step("runtime", "failed", "python_too_old", f"بايثون {sys.version.split()[0]} والمطلوب ≥ 3.11")
    try:
        import cryptography  # noqa: F401
        import core.run  # noqa: F401
        import agent.loop  # noqa: F401
        import providers.ollama  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — أيُّ عطب استيرادٍ يُسمّى
        return Step("runtime", "failed", "import_failed", f"{type(exc).__name__}: {exc}"[:200])
    if not (root / "uv.lock").is_file():
        return Step("runtime", "failed", "lock_missing", "uv.lock غائب: التثبيتُ غيرُ مقفول")
    from core.public_export import read_marker
    kind = "public" if read_marker(root) else "private"
    return Step("runtime", "ok", f"runtime_ready_{kind}", f"بايثون {sys.version.split()[0]}، نسخةٌ {kind}")


def _camel_installed() -> bool:
    try:
        import camel_tools  # noqa: F401
    except ImportError:
        return False
    return True


def check_morphology(*, installed=_camel_installed, analyzer=...) -> Step:
    """CAMeL Tools وقاعدتُه (ق٥٥): غيابُ أحدهما تعذّرٌ معلن يسمّي أمرَه، لا جهازٌ جاهز."""
    if not installed():
        return Step("morphology", "unavailable", "camel_missing",
                    f"CAMeL Tools غيرُ مركَّب — `{CAMEL_INSTALL}` ثم `{CAMEL_DB_COMMAND}`")
    from core.linguistics.roots import CAMEL_DB, camel_analyzer
    ready = camel_analyzer() if analyzer is ... else analyzer
    if ready is None:
        return Step("morphology", "unavailable", "camel_db_missing",
                    f"قاعدةُ CAMeL الصرفية ({CAMEL_DB}) غيرُ منزَّلة — `{CAMEL_DB_COMMAND}`")
    try:
        from importlib.metadata import version
        camel_version = version("camel-tools")
    except Exception:  # noqa: BLE001 — الإصدارُ زينةٌ لا حكم
        camel_version = "?"
    return Step("morphology", "ok", "camel_ready", f"CAMeL Tools {camel_version} بقاعدة {CAMEL_DB}")


def probe_engine(base_url: str, timeout: float = 3.0) -> dict:
    """يقرأ قائمة النماذج من Ollama؛ يرمي OSError/URLError إن لم يُبلَغ."""
    with urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout) as response:  # noqa: S310 — عنوانٌ محلي معلن
        return json.loads(response.read().decode("utf-8"))


def check_engine(engine: str, base_url: str, *, probe=probe_engine) -> Step:
    try:
        tags = probe(base_url)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return Step("engine", "unavailable", "engine_unreachable",
                    f"لا يجيب Ollama على {base_url}: {type(exc).__name__} — شغّل `ollama serve`")
    names = {m.get("name") for m in tags.get("models", []) if isinstance(m, dict)}
    if engine not in names:
        return Step("engine", "unavailable", "model_missing",
                    f"المحرّك {engine} غيرُ مسحوب — `ollama pull {engine}`؛ الموجود: {sorted(n for n in names if n)[:6]}")
    return Step("engine", "ok", "engine_ready", f"{engine} على {base_url}")


class _Mechanism:
    """مزوّدٌ مكتوبٌ سلفًا: يطلب read_file ثم يجيب — يُثبت الحلقةَ لا النموذج."""
    name = "mechanism"
    is_local = True

    def __init__(self):
        self.calls = 0

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        from core.contracts import Response, ToolCall, Usage
        self.calls += 1
        if self.calls == 1:
            return Response("سأقرأ الملف", Usage(1, 1), "complete", 0, provider=self.name,
                            model_version="v1", tool_calls=(ToolCall("c1", "read_file", {"path": "notes.txt"}),))
        return Response(f"الملفُ يقول: {NOTE_TEXT}", Usage(1, 1), "complete", 0, provider=self.name,
                        model_version="v1", tool_calls=())


def check_agent_turn(engine: str, base_url: str, *, live: bool) -> Step:
    from agent.actions import ActionStore
    from agent.builtin_tools import DEFAULT_TOOLS
    from agent.journal import Journal
    from agent.loop import run_agent
    from agent.registry import ToolContext, ToolRegistry
    from core.budget import Budget
    from core.ledger import Ledger
    with tempfile.TemporaryDirectory(prefix="diwan-launch-") as directory:
        # يُحلّ المسارُ أولًا: على ماك يقع المؤقّت تحت /var وهو رابطٌ إلى /private/var، وحارسُ دفتر
        # الرجوع يرفض المسارَ غيرَ المحلول (unsafe_path) — كشفه التشغيلُ الحيّ في ٢٥ سبتمبر (عطبُ ك٨ نفسُه)
        directory = str(Path(directory).resolve())
        space = Path(directory) / "workspace"
        space.mkdir()
        (space / "notes.txt").write_text(NOTE_TEXT + "\n", encoding="utf-8")
        if live:
            from providers.ollama import OllamaProvider
            provider, model, version = OllamaProvider(engine, base_url), engine, engine
        else:
            provider, model, version = _Mechanism(), "mechanism", "v1"
        try:
            # إنشاءُ الدفتر داخل `try`: رفضُه (كما وقع على ماك قبل حلّ المسار) عطبٌ مسمًّى في التقرير لا تعقّبٌ يقطع الفحص.
            context = ToolContext(root=space, journal=Journal(space), allowed_consents=frozenset({"auto", "logged"}))
            run = run_agent(TASK, provider, ToolRegistry(*DEFAULT_TOOLS), context,
                            ledger=Ledger(space / "ledger.jsonl"), budget=Budget(0, 0),
                            action_store=ActionStore(Path(directory) / "actions", space),
                            session_id="launch-check", turn_id="turn-1",
                            model=model, model_version=version, max_steps=4, deadline_s=120.0)
        except Exception as exc:  # noqa: BLE001 — يُسمّى ولا يُبتلع
            return Step("agent_turn", "failed", "agent_turn_raised", f"{type(exc).__name__}: {exc}"[:200])
        read_calls = [call for step in run.steps for call in step.tool_calls if getattr(call, "name", "") == "read_file"]
        if run.status != "complete":
            return Step("agent_turn", "failed", f"agent_turn_{run.status}", f"{run.code}: {run.answer[:120]}")
        if not read_calls:
            return Step("agent_turn", "failed", "tool_not_used", "أجاب النموذجُ بلا قراءة الملف بأداة read_file")
        if not run.answer.strip():
            return Step("agent_turn", "failed", "empty_answer", "جولةٌ تمّت بلا جواب")
        code = "agent_turn_live" if live else "mechanism_only"
        return Step("agent_turn", "ok", code, f"{len(run.steps)} خطوات، والجواب: {run.answer[:80]}")


def check_policies(root: Path) -> Step:
    catalog = root / "corpus" / "maritime" / "_catalog.jsonl"
    if not catalog.is_file():
        return Step("policies", "unavailable", "corpus_missing",
                    "المتنُ غيرُ موضوع محليًّا — `python tools/place_private_stores.py --from ~/diwan-private`")
    try:
        import rebuild_index
        from core.canonical import PayloadRejected
    except Exception as exc:  # noqa: BLE001
        return Step("policies", "failed", "import_failed", f"{type(exc).__name__}: {exc}"[:200])
    try:
        hits = rebuild_index.search("سفينة", limit=1, match_any=True)
    except PayloadRejected as exc:
        if getattr(exc, "code", "") == "index_missing":
            return Step("policies", "unavailable", "index_missing", "الإسقاطُ غيرُ مبني — `python tools/rebuild_index.py rebuild`")
        return Step("policies", "failed", getattr(exc, "code", "search_refused"), str(exc)[:200])
    except Exception as exc:  # noqa: BLE001
        return Step("policies", "failed", "search_raised", f"{type(exc).__name__}: {exc}"[:200])
    if not hits:
        return Step("policies", "failed", "no_evidence", "استعلامٌ بسيط بلا شاهد رغم وجود المتن والإسقاط")
    return Step("policies", "ok", "policies_ready", f"شاهدٌ من {hits[0].get('doc_id', '?')}")


def check_ui(root: Path) -> Step:
    result = subprocess.run([sys.executable, str(root / "tools" / "serve_ui.py"), "--help"],
                            cwd=root, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return Step("ui", "failed", "ui_help_failed", (result.stderr or result.stdout)[-200:])
    return Step("ui", "ok", "ui_ready", "tools/serve_ui.py يُهيَّأ")


def run_checks(root: Path, *, engine: str, base_url: str, probe=probe_engine,
               with_agent: bool = True, with_ui: bool = True) -> list[Step]:
    steps = [check_runtime(root)]
    if steps[0].status == "failed":
        return steps
    steps.append(check_morphology())
    engine_step = check_engine(engine, base_url, probe=probe)
    steps.append(engine_step)
    if with_agent:
        steps.append(check_agent_turn(engine, base_url, live=engine_step.status == "ok"))
    steps.append(check_policies(root))
    if with_ui:
        steps.append(check_ui(root))
    return steps


def exit_code(steps: list[Step]) -> int:
    statuses = {s.status for s in steps}
    if "failed" in statuses:
        return 1
    if "unavailable" in statuses:
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", default=DEFAULT_ENGINE)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--json", action="store_true", help="تقريرٌ JSON بدل السطور")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args(argv)
    steps = run_checks(Path(args.root), engine=args.engine, base_url=args.base_url)
    code = exit_code(steps)
    if args.json:
        print(json.dumps({"schema_version": 1, "steps": [asdict(s) for s in steps], "exit_code": code,
                          "verdict": {0: "ready", 3: "unavailable_declared", 1: "failed"}[code]},
                         ensure_ascii=False, indent=2))
    else:
        mark = {"ok": "✓", "unavailable": "◻", "failed": "✗"}
        for s in steps:
            print(f"  {mark[s.status]} {s.step}: {s.code} — {s.detail}")
        print({0: "\n✓ جاهزٌ للإطلاق على هذا الجهاز.",
               3: "\n◻ تعذّر بحدٍّ معلن: أكمل ما سُمّي أعلاه ثم أعد الفحص.",
               1: "\n✗ عطب: يُصلَح قبل الإطلاق."}[code])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
