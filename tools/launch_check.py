#!/usr/bin/env python3
"""فحصُ الدخان للإطلاق الأول: هل يعمل ديوان على هذا الجهاز؟ (ك٢٨، الخطة §١.٣-٣)

ستُّ خطواتٍ بالترتيب، كلٌّ منها تنتهي بحالةٍ من ثلاث ورمزٍ مسمًّى:
`ok` (عملت)، أو `unavailable` (تعذّرت بحدٍّ معلن: لا محرّك، لا متن)، أو `failed` (عطب).

1. **runtime** — بايثون ≥ 3.11، والوحداتُ تُستورد، وuv.lock حاضر، ونوعُ النسخة (عامة/خاصة).
2. **morphology** — CAMeL Tools وقاعدتُه الصرفية حاضران (ق٥٥: لازمان للإطلاق، والقالبيُّ
   احتياطيٌّ مسمًّى لا بديل)؛ وإلا `camel_missing` أو `camel_db_missing` مع أمر التركيب.
3. **engine** — خادمُ Ollama يجيب على `/api/tags` والمحرّكُ المطلوب مسحوب ببصمةٍ صالحة بقاعدة المزوّد المحليّ نفسِه
   (sha256 كاملة)؛ وبصمةٌ غائبة أو مشوّهة عطبٌ مسمًّى `engine_metadata_invalid`، لأن أولَ جوابٍ في الواجهة يرفضها.
4. **agent_turn** — جولةٌ وكيلة محكومة كاملة في مساحةٍ مؤقّتة: النموذجُ يقرأ ملفًّا بأداة
   `read_file` ويجيب، والسجلُّ يقيّد. بالمحرّك الحيّ إن وُجد؛ وإلا بمزوّدٍ آليّ مكتوبٍ سلفًا
   يُثبت الحلقةَ والأدواتِ والحَجرَ دون النموذج (`mechanism_only`).
5. **policies** — عقدةُ السياسات: المتنُ موضوعٌ محليًّا وإسقاطُه مبنيٌّ واستعلامٌ واحد يعيد شاهدًا؛
   وإلا `corpus_missing` أو `index_missing` (النسخةُ العامة بلا متون، ك٢٩).
6. **ui** — `tools/serve_ui.py` يُشغَّل كما يشغّله المستخدم (المزوّدُ المحليّ بالمحرّك الذي وجدته الخطوةُ ٣ وبصمتِه)
   على منفذٍ زائل في 127.0.0.1 وجذرٍ مؤقّت فارغ: `GET /` ← 200 وصفحةٌ `dir="rtl"` برمز جلستها، ثم نداءُ قراءةٍ واحد
   بلا نموذج (`projects`) ← 200، ثم يُوقَف. بمهلةٍ ورموزٍ مسمّاة: `ui_start_failed` (لم يعلن عنوانَه أو خرج)،
   `ui_http_failed` (ردٌّ غيرُ 200 أو انقطاع)، `ui_page_unexpected` (الصفحةُ أو الردُّ على غير ما يُنتظر). وبلا محرّك
   يُشغَّل ببصمةٍ بديلة معلنة (`ui_ready_without_engine`): الصفحةُ لا تنادي النموذج، والجوابُ يحتاجه. وserve_ui.py
   ومزوّدُه المحليّ يبلغان Ollama على 127.0.0.1:11434 وحده، فإن فُحص المحرّكُ على `--base-url` غيرِه لم تُسمَّ الواجهةُ
   جاهزة: `ui_engine_endpoint_unpassed` (تعذّرٌ معلن)، لأن نداءَ القراءة لا يبلغ النموذجَ فلا يكشف ذلك وحده. وبمحرّكٍ لا
   تُسمّى جاهزةً قبل أن يمرّ فحصُ مزوّدَيها المسبق وجوابٌ قصير عبرهما، وإلا `ui_engine_refused` برمز المزوّد.

    python tools/launch_check.py [--engine qwen3.5:9b] [--base-url http://127.0.0.1:11434] [--json]

الخروج: 0 كلُّها `ok`؛ 3 فيها `unavailable` بلا `failed` (تعذّرٌ معلن — ليس جهازًا جاهزًا للإطلاق
لكنه ليس عطبًا)؛ 1 عطب. **الحدُّ المعلَن:** الجولةُ الحيّة تُثبت أن المحرّك يستعمل أداةً ويجيب،
لا جودةَ جوابه؛ والجودةُ تُقاس بالبنوك (`evaluation/`).
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from providers.ollama import DEFAULT_MODEL as DEFAULT_ENGINE  # noqa: E402 — موضعٌ واحد للمحرّك (ق٥٤)

CAMEL_INSTALL = "uv sync"   # ك٣٣: CAMeL اعتماديةٌ رئيسة، لا extra
CAMEL_DB_COMMAND = "uv run camel_data -i morphology-db-msa-r13"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
NOTE_TEXT = "مرحبًا بديوان على هذا الجهاز"
TASK = "اقرأ الملف notes.txt بأداة read_file ثم أخبرني في جملةٍ واحدة بما فيه."


def _clip(text, limit: int = 200) -> str:
    """نصُّ التفصيل مقصوصًا بعلامة: ما قُصّ ينتهي بـ«…» فلا يُقرأ تفصيلٌ ناقص كاملًا."""
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _listing(names, shown: int = 6) -> str:
    """أولُ `shown` أسماءٍ مرتّبة، ومعها عددُ ما لم يُعرض إن وُجد."""
    names = sorted(str(name) for name in names)
    rest = len(names) - shown
    return f"{names[:shown]}" + (f" و{rest} غيرها" if rest > 0 else "")


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
        return Step("runtime", "failed", "import_failed", _clip(f"{type(exc).__name__}: {exc}"))
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


def _shape(value) -> str:
    """وصفُ قيمةٍ لا يرمي أيًّا كان نوعُها: النصُّ بطوله، وغيرُه بنوعه (رقمٌ أو منطقيّ أو قائمة من /api/tags)."""
    return f"نصٌّ من {len(value)} محرفًا" if isinstance(value, str) else f"قيمةٌ من نوع {type(value).__name__}"


def locate_engine(engine: str, base_url: str, *, probe=probe_engine) -> tuple[Step, str | None]:
    """خطوةُ المحرّك وبصمتُه كما يقرؤها `serve_ui.py` من `/api/tags` (`digest`)، أو لا بصمة إن لم يُوجد."""
    try:
        tags = probe(base_url)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        return Step("engine", "unavailable", "engine_unreachable",
                    f"لا يجيب Ollama على {base_url}: {type(exc).__name__} — شغّل `ollama serve`"), None
    # ردٌّ على غير شكله (ليس قاموسًا، أو models ليست قائمة) عطبٌ مسمًّى لا تعقّب؛ والاسمُ غيرُ النصّيّ لا يُعدّ محرّكًا
    entries = tags.get("models") if isinstance(tags, dict) else None
    if not isinstance(entries, list):
        return Step("engine", "failed", "engine_metadata_invalid",
                    f"ردُّ /api/tags على {base_url} بلا قائمة models ({_shape(entries)})"), None
    models = {m["name"]: m.get("digest") for m in entries if isinstance(m, dict) and isinstance(m.get("name"), str)}
    if engine not in models:
        return Step("engine", "unavailable", "model_missing",
                    f"المحرّك {engine} غيرُ مسحوب — `ollama pull {engine}`؛ الموجود: {_listing(n for n in models if n)}"), None
    digest = models[engine]
    # بصمةُ المحرّك بقاعدة الفحص المسبق في المزوّد المحليّ نفسِه (providers/local_chat._SHA256: ٦٤ محرفًا ست عشريًّا صغيرًا):
    # بصمةٌ غائبة أو مشوّهة يرفضها أولُ جوابٍ في الواجهة، فهي عطبٌ مسمًّى لا محرّكٌ غائب ولا جاهز
    from providers.local_chat import _SHA256 as ARTIFACT_DIGEST
    if not isinstance(digest, str) or not ARTIFACT_DIGEST.fullmatch(digest):
        return Step("engine", "failed", "engine_metadata_invalid",
                    f"Ollama يسرد {engine} ببصمةٍ غيرِ صالحة ({_shape(digest)})؛ "
                    f"المزوّدُ المحليّ يطلب sha256 كاملة — أعد `ollama pull {engine}`"), None
    return Step("engine", "ok", "engine_ready", f"{engine} على {base_url}"), digest


def check_engine(engine: str, base_url: str, *, probe=probe_engine) -> Step:
    return locate_engine(engine, base_url, probe=probe)[0]


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
            return Step("agent_turn", "failed", "agent_turn_raised", _clip(f"{type(exc).__name__}: {exc}"))
        read_calls = [call for step in run.steps for call in step.tool_calls if getattr(call, "name", "") == "read_file"]
        if run.status != "complete":
            return Step("agent_turn", "failed", f"agent_turn_{run.status}", f"{run.code}: {_clip(run.answer, 120)}")
        if not read_calls:
            return Step("agent_turn", "failed", "tool_not_used", "أجاب النموذجُ بلا قراءة الملف بأداة read_file")
        if not run.answer.strip():
            return Step("agent_turn", "failed", "empty_answer", "جولةٌ تمّت بلا جواب")
        code = "agent_turn_live" if live else "mechanism_only"
        return Step("agent_turn", "ok", code, f"{len(run.steps)} خطوات، والجواب: {_clip(run.answer, 80)}")


def check_policies(root: Path) -> Step:
    catalog = root / "corpus" / "maritime" / "_catalog.jsonl"
    if not catalog.is_file():
        return Step("policies", "unavailable", "corpus_missing",
                    "المتنُ غيرُ موضوع محليًّا — `python tools/place_private_stores.py --from ~/diwan-private`")
    try:
        import rebuild_index
        from core.canonical import PayloadRejected
    except Exception as exc:  # noqa: BLE001
        return Step("policies", "failed", "import_failed", _clip(f"{type(exc).__name__}: {exc}"))
    try:
        hits = rebuild_index.search("سفينة", limit=1, match_any=True)
    except PayloadRejected as exc:
        if getattr(exc, "code", "") == "index_missing":
            return Step("policies", "unavailable", "index_missing", "الإسقاطُ غيرُ مبني — `python tools/rebuild_index.py rebuild`")
        return Step("policies", "failed", getattr(exc, "code", "search_refused"), _clip(exc))
    except Exception as exc:  # noqa: BLE001
        return Step("policies", "failed", "search_raised", _clip(f"{type(exc).__name__}: {exc}"))
    if not hits:
        return Step("policies", "failed", "no_evidence", "استعلامٌ بسيط بلا شاهد رغم وجود المتن والإسقاط")
    return Step("policies", "ok", "policies_ready", f"شاهدٌ من {hits[0].get('doc_id', '?')}")


UI_START_TIMEOUT_S = 60.0     # من تشغيل serve_ui.py إلى سطر عنوانه (الاستيرادُ وتهيئةُ المزوّد والجذر)
UI_HTTP_TIMEOUT_S = 10.0      # لكل نداء HTTP
UI_STOP_TIMEOUT_S = 10.0      # بعد إشارة الإيقاف، ثم يُقتل
PLACEHOLDER_DIGEST = "0" * 64  # بلا محرّك: serve_ui.py يطلب بصمة، والصفحةُ ونداءُ القراءة لا ينادون النموذج
UI_ORIGIN = re.compile(r"(http://127\.0\.0\.1:[0-9]{1,5})\s*$")
UI_TOKEN = re.compile(r'<meta name="diwan-token" content="([0-9a-f]{64})">')
UI_RTL = re.compile(r'<html\b[^>]*\bdir="rtl"')


LOOPBACK = frozenset({"127.0.0.1", "localhost"})


def ui_engine_endpoint(source: str | None = None) -> tuple[str, int] | None:
    """العنوانُ الذي يبلغ به المزوّدُ المحليّ (الذي تنشئه serve_ui.py) خادمَ Ollama، مقروءًا من شيفرته نفسِها لا منسوخًا:
    وسيطا `HTTPConnection(<مضيف>, <منفذ>, …)` في providers/local_chat.py. None إن لم يكونا حرفيَّين (صار العنوانُ مضبوطًا،
    #176) فلا يُدّعى أن الواجهة تبلغ المحرّكَ المفحوص."""
    if source is None:
        source = (ROOT / "providers" / "local_chat.py").read_text(encoding="utf-8")
    found = set()
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) == "HTTPConnection"
                and len(node.args) >= 2):
            host, port = node.args[0], node.args[1]
            if not (isinstance(host, ast.Constant) and isinstance(host.value, str)
                    and isinstance(port, ast.Constant) and type(port.value) is int):
                return None
            found.add((host.value, port.value))
    return found.pop() if len(found) == 1 else None


def reaches_ui_engine(base_url: str, endpoint: tuple[str, int] | None = None) -> bool:
    """هل `--base-url` هو العنوانُ نفسُه الذي تبلغه الواجهة؟ مقارنةٌ موجبة كاملة: http، والمضيفُ نفسُه أو مكافئُه على
    loopback، والمنفذُ نفسُه، والمسارُ فارغ أو «/»، ولا مستخدمَ ولا استعلامَ ولا جزء. وأيُّ فرقٍ ليس ui_ready."""
    endpoint = endpoint or ui_engine_endpoint()
    if endpoint is None:
        return False
    host, port = endpoint
    try:
        parts = urllib.parse.urlsplit(base_url)
        given_port = parts.port
    except ValueError:
        return False
    same_host = parts.hostname == host or (parts.hostname in LOOPBACK and host in LOOPBACK)
    return (parts.scheme == "http" and same_host and given_port == port
            and parts.path in ("", "/")
            and not parts.query and not parts.fragment
            and parts.username is None and parts.password is None)


def _drain(stream, lines: "queue.Queue[str | None]") -> None:
    try:
        for line in stream:
            lines.put(line)
    finally:
        lines.put(None)


def _stop(process: subprocess.Popen, readers: tuple[threading.Thread, ...] = ()) -> None:
    """إيقافٌ كما يوقفه المستخدم (Ctrl+C فيُغلق الخادمُ جذرَه)، ثم قتلٌ بعد المهلة؛ ولا يبقى الخادمُ بعد الخطوة.
    وقارئا المخرجات ينتهيان بنهاية الأنبوب قبل أن يُغلق."""
    if process.poll() is None:
        try:
            if os.name == "posix":
                process.send_signal(signal.SIGINT)
            else:
                process.terminate()
            process.wait(UI_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(UI_STOP_TIMEOUT_S)
    for reader in readers:
        reader.join(UI_STOP_TIMEOUT_S)
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def probe_ui(origin: str, timeout_s: float = UI_HTTP_TIMEOUT_S) -> Step | int:
    """`GET /` ثم `projects`؛ يُعيد عددَ المشروعات إن صحّ كلُّه، وإلا خطوةً ساقطة برمزها."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 127.0.0.1 لا يمرّ بوكيل البيئة
    try:
        with opener.open(f"{origin}/", timeout=timeout_s) as response:  # noqa: S310 — عنوانٌ محلي أعلنه الخادم
            status, page = response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return Step("ui", "failed", "ui_http_failed", f"GET / ← {exc.code}")
    except (OSError, urllib.error.URLError) as exc:
        return Step("ui", "failed", "ui_http_failed", f"GET / ← {type(exc).__name__}")
    if status != 200:
        return Step("ui", "failed", "ui_http_failed", f"GET / ← {status}")
    token = UI_TOKEN.search(page)
    if not UI_RTL.search(page) or token is None:
        missing = "dir=\"rtl\"" if not UI_RTL.search(page) else "رمزُ الجلسة"
        return Step("ui", "failed", "ui_page_unexpected", f"GET / ← 200 بلا {missing}")
    request = urllib.request.Request(f"{origin}/api", data=b'{"action":"projects"}', method="POST", headers={
        "Origin": origin, "X-Diwan-CSRF": token.group(1), "Content-Type": "application/json"})
    try:
        with opener.open(request, timeout=timeout_s) as response:  # noqa: S310
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        return Step("ui", "failed", "ui_http_failed", f"POST /api projects ← {exc.code}")
    except (OSError, urllib.error.URLError) as exc:
        return Step("ui", "failed", "ui_http_failed", f"POST /api projects ← {type(exc).__name__}")
    if status != 200:
        return Step("ui", "failed", "ui_http_failed", f"POST /api projects ← {status}")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        return Step("ui", "failed", "ui_page_unexpected", "POST /api projects ← 200 بلا قائمة مشروعات")
    return len(data["projects"])


UI_ENGINE_DEADLINE_S = 120.0   # فحصُ المزوّدَين قبل «جاهز»: البيانات، ثم جوابٌ قصير (قد يُحمَّل النموذجُ أولَ مرّة)


def check_ui_engine(model: str, digest: str) -> Step | None:
    """المحرّكُ كما تستعمله الواجهة، بمزوّدَيها لا بمزوّد الجولة الحيّة (OllamaProvider أرخى)، بجوابين قصيرين عبر
    `complete()` نفسِه: أولُهما عبر مزوّد الأدوات بطلبٍ يحمل أداةً واحدةً فارغة (جلساتُ الواجهة وكيلةٌ افتراضيًّا)، ففيه فحصُه
    المسبق بقدرة tools وتسلسلُ الأدوات ونداءُ /api/chat ومحلّلُ الجواب؛ ثم عبر مزوّد النصّ، وفيه فحصُه المسبق (الحجم والصيغة
    والبُعد وقدراتُ /api/show وسعةُ السياق). None إن مرّا، وإلا عطبٌ مسمًّى `ui_engine_refused` برمز المزوّد: فلا يُقال «جاهز»
    وأولُ جوابٍ في الواجهة يُرفض. والأداةُ لا تُنفَّذ: نداؤها إن جاء جوابٌ فحسب."""
    from core.contracts import Message, Request, ToolSpec
    from providers.base import ProviderError
    from providers.local_chat import LocalChatProvider
    from providers.local_tools import LocalToolProvider
    ask = dict(messages=(Message("user", "أجب بكلمةٍ واحدة: نعم."),), model=model, model_version=digest, max_output=8,
               deadline_s=UI_ENGINE_DEADLINE_S, data_policy="local_only", idempotency_key=None)
    ping = ToolSpec("launch_check_ping", "أداةٌ فارغة لفحص حمل الأدوات؛ لا تُنفَّذ", {"type": "object", "properties": {}})
    try:
        LocalToolProvider(model, digest).complete(Request(**ask, tools=(ping,)))
        LocalChatProvider(model, digest).complete(Request(**ask))
    except ProviderError as exc:
        return Step("ui", "failed", "ui_engine_refused", _clip(f"مزوّدُ الواجهة يرفض {model}: {exc.code} — {exc.reason}"))
    except Exception as exc:  # noqa: BLE001 — أيُّ عطبٍ في المزوّد يُسمّى ولا يقطع التقرير
        return Step("ui", "failed", "ui_engine_refused", _clip(f"مزوّدُ الواجهة تعطّل مع {model}: {type(exc).__name__}: {exc}"))
    return None


def check_ui(root: Path, *, model: str = DEFAULT_ENGINE, digest: str | None = None,
             base_url: str = DEFAULT_BASE_URL, timeout_s: float = UI_START_TIMEOUT_S,
             script: Path | None = None) -> Step:
    """يشغّل `serve_ui.py` بالمزوّد المحليّ على منفذٍ زائل وجذرٍ مؤقّت، ويفحص الصفحةَ ونداءَ قراءة، ثم يوقفه.

    `digest=None` (لا محرّك) يعني بصمةً بديلة معلنة فيُسمّى النجاحُ `ui_ready_without_engine`."""
    script = script or root / "tools" / "serve_ui.py"
    env = {**os.environ, "DIWAN_CHAT_MODEL": model, "DIWAN_CHAT_DIGEST": digest or PLACEHOLDER_DIGEST,
           "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    with tempfile.TemporaryDirectory(prefix="diwan-launch-ui-") as directory:
        ui_root = Path(directory).resolve() / "ui"
        argv = [sys.executable, str(script), "--provider", "local", "--port", "0", "--root", str(ui_root)]
        try:
            process = subprocess.Popen(argv, cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            return Step("ui", "failed", "ui_start_failed", _clip(f"{type(exc).__name__}: {exc}"))
        lines: "queue.Queue[str | None]" = queue.Queue()
        errors: list[str] = []
        readers = (threading.Thread(target=_drain, args=(process.stdout, lines), daemon=True),
                   threading.Thread(target=lambda: errors.extend(process.stderr), daemon=True))
        for reader in readers:
            reader.start()
        try:
            origin, deadline = None, time.monotonic() + timeout_s
            while origin is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return Step("ui", "failed", "ui_start_failed", f"لم يعلن serve_ui.py عنوانَه في {timeout_s:g} ث")
                try:
                    line = lines.get(timeout=min(remaining, 0.5))
                except queue.Empty:
                    continue
                if line is None:
                    process.wait(UI_STOP_TIMEOUT_S)
                    tail = "".join(errors).strip().splitlines()[-1:] or [""]
                    return Step("ui", "failed", "ui_start_failed",
                                _clip(f"خرج serve_ui.py برمز {process.returncode} قبل أن يعلن عنوانه؛ آخرُ سطرٍ من خطئه: {tail[0]}"))
                found = UI_ORIGIN.search(line)
                origin = found.group(1) if found else None
            probed = probe_ui(origin)
        finally:
            _stop(process, readers)
    if isinstance(probed, Step):
        return probed
    if not reaches_ui_engine(base_url):
        return Step("ui", "unavailable", "ui_engine_endpoint_unpassed",
                    f"الواجهةُ تُقلع وتخدم الصفحة، لكن serve_ui.py يبلغ Ollama على 127.0.0.1:11434 وحده والمحرّكُ فُحص على "
                    f"{base_url}: أولُ جوابٍ في الواجهة لن يبلغه")
    if digest:
        refused = check_ui_engine(model, digest)
        if refused is not None:
            return refused
    code = "ui_ready" if digest else "ui_ready_without_engine"
    engine = (f"بالمحرّك {model} (فحصُ مزوّدَي الواجهة المسبق وجوابٌ قصير مرّا)" if digest
              else f"ببصمةٍ بديلة لـ{model} (لا محرّك؛ الجوابُ يحتاجه)")
    return Step("ui", "ok", code, f"serve_ui.py {engine} على منفذٍ زائل: GET / ← 200 وdir=rtl، "
                                  f"وprojects ← 200 ({probed} مشروع)، ثم أُوقف")


def run_checks(root: Path, *, engine: str, base_url: str, probe=probe_engine,
               with_agent: bool = True, with_ui: bool = True, ui_check=check_ui) -> list[Step]:
    steps = [check_runtime(root)]
    if steps[0].status == "failed":
        return steps
    steps.append(check_morphology())
    engine_step, digest = locate_engine(engine, base_url, probe=probe)
    steps.append(engine_step)
    if with_agent:
        steps.append(check_agent_turn(engine, base_url, live=engine_step.status == "ok"))
    steps.append(check_policies(root))
    if with_ui:
        # الواجهةُ بالمحرّك الذي وجدته خطوةُ المحرّك وبصمتِه، كما يشغّلها المستخدم
        steps.append(ui_check(root, model=engine, digest=digest if engine_step.status == "ok" else None,
                              base_url=base_url))
    return steps


# طرفيةُ ويندوز بترميزها المحليّ (cp1256 على Nitro) لا تكتب ✓ ولا ◻ ولا ✗، فانهار الفحصُ بـUnicodeEncodeError
# قبل أول سطر (#64، ٢٥ سبتمبر ٢٠٢٦). فتُختار علاماتٌ يكتبها الترميز، ويُستبدل ما بقي بدل أن ينهار.
MARKS = {"ok": "✓", "unavailable": "◻", "failed": "✗"}
ASCII_MARKS = {"ok": "+", "unavailable": "o", "failed": "x"}


def marks_for(encoding: str | None) -> dict[str, str]:
    try:
        "".join(MARKS.values()).encode(encoding or "utf-8")
    except (UnicodeEncodeError, LookupError):
        return ASCII_MARKS
    return MARKS


def _tolerate_console(stream) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            pass


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
        _tolerate_console(sys.stdout)
        mark = marks_for(getattr(sys.stdout, "encoding", None))
        for s in steps:
            print(f"  {mark[s.status]} {s.step}: {s.code} — {s.detail}")
        print({0: f"\n{mark['ok']} جاهزٌ للإطلاق على هذا الجهاز.",
               3: f"\n{mark['unavailable']} تعذّر بحدٍّ معلن: أكمل ما سُمّي أعلاه ثم أعد الفحص.",
               1: f"\n{mark['failed']} عطب: يُصلَح قبل الإطلاق."}[code])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
