#!/usr/bin/env python3
"""جولتا ج٥ الحيّتان عبر الواجهة نفسها: النموذجُ يستدعي web_search بنفسه، وrun_command يمرّ بالحاوية.

    python3 tools/probe_j5_agent_round.py --model qwen3.5:9b --web-search-url http://127.0.0.1:8888 \\
        --runtime-receipt <إيصال التشغيل الخاص> --out docs/probe/j5-docker-searxng-<التاريخ>.json

- الطريقُ `webui.server.LocalApp.dispatch` بالمزوّدين اللذين تبنيهما `tools/serve_ui.py`، لا نداءً مباشرًا للأداة.
- البحثُ في جلسة «research»، والأمرُ في جلسة «agent» بموافقة المالك (`agent_decide`) كما في الواجهة.
- يُسجَّل ما نادى به النموذجُ وحالُ كل نتيجة، وعناوينُ المصادر، وشكلُ حدّ التنفيذ لا معرّفُه، من حقل `boundary` في نتيجة
  الأداة نفسِها لا من نصّ الردّ.
- ومسارُ Docker هو الذي تبني به الواجهةُ خلفيّةَ run_command، وإلا فلا قياس.
- البحثُ من SearXNG محليٍّ وحده: الرابطُ على عنوان الجهاز، والحاويةُ التي تنشر منفذَه بالصورة المثبَّتة في G5.
- ولا يُوافَق إلا على الأمر المطلوب نفسِه (argv الأمر المعلَّق)، ويُسجَّل argv في التقرير.
- والبصماتُ، ومنها بصمةُ المجسّ، تُؤخذ قبل الجولة ويُرفض التقريرُ إن تغيّرت قبل كتابته؛ ومثلُها حاويةُ SearXNG بمعرّفها
  ووقتِ تشغيلها، وبصمةُ النموذج.
- ولا يُقبل البحثُ إلا بنتيجة web_search فيها رابطٌ واحدٌ على الأقل، يُعدّ من نتائج الأداة نفسِها لا من جواب النموذج.
- ولا يخرج بـ0 إلا إن تحقّقت شروطُ القبول (`acceptance`)، ويُكتب التقريرُ في الحالين بحكمه.
"""
from __future__ import annotations

import argparse
import atexit
import datetime
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import sys
import subprocess
import tempfile
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
NOT_SOURCE = frozenset({"tests", "__pycache__", "site-packages", "venv"})


def _tree() -> dict[str, str]:
    """بصماتُ كلّ وحدةٍ من المستودع، بلا الاختبارات والحزم المثبَّتة والمجلّدات المخفيّة."""
    out = {}
    for folder, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in NOT_SOURCE and not d.startswith(".")]
        for name in files:
            if name.endswith(".py"):
                path = Path(folder) / name
                out[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


# والشيفرةُ المقيسة تُترجم من مصدرها المبصوم في مخبأ بايتاتٍ جديدٍ فارغ، لا من `.pyc` في `__pycache__`: بايتاتٌ قديمة
# يطابق وقتُها وحجمُها مصدرًا عُدّل تُنفَّذ والبصمةُ للجديد (ملاحظة Codex على #144). ويُضبط حين يُشغَّل المجسُّ وحده،
# فاستيرادُه في الاختبارات لا يغيّر مخبأ العملية، ولا يُقبل تشغيلٌ بغيره (`_cached_elsewhere`)
PYCACHE = None
if __name__ == "__main__":
    PYCACHE = tempfile.mkdtemp(prefix="diwan-probe-pycache-")
    sys.pycache_prefix = PYCACHE
    atexit.register(shutil.rmtree, PYCACHE, True)

# بصماتُ المستودع قبل تحميل الشيفرة المقيسة: وحدةٌ استُبدلت بين تحميلها وأول بصمةٍ تُنفَّذ قديمةً ويُسجَّل جديدُها، ويُبقيه
# الفحصُ بعد الجولة؛ فكلُّ بصمةٍ تُسجَّل تساوي ما قبل التحميل (ملاحظة Codex على #144)
BEFORE_IMPORT = _tree()

from agent.web_search import SearxngBackend  # noqa: E402
from core.execution import DockerExecutionBackend, _clean_env  # noqa: E402
from providers.local_chat import LocalChatProvider  # noqa: E402
from providers.local_tools import LocalToolProvider  # noqa: E402
from webui.server import LocalApp  # noqa: E402

SEARCH_QUESTIONS = (
    "ابحث في الويب: ما أحدث إصدارٍ من لغة بايثون، ومتى صدر؟ اذكر المصادر.",
    "ما أبرز أخبار اليوم في السعودية؟ ابحث في الويب واذكر روابط المصادر.",
    "ابحث في الويب عن موقع المشروع الرسمي لـSearXNG، وأعطني رابطه.",
)
COMMAND_REQUEST = 'شغّل الأمر python3 -c "print(2+2)" وأخبرني بالناتج.'
# ما يُبصم قبل الجولة ويُعاد بصمُه قبل الكتابة: الشيفرةُ المقيسة، والمجسُّ نفسُه (ملاحظة Codex على #136)؛ ومعها كلُّ
# وحدةٍ من المستودع محمَّلةٍ في العملية، فلا يفوت قائمةً منتقاةً ما تمرّ به الجولة، كمهايئَي المزوّد (ملاحظة Codex على #144)
SOURCES = ("core/execution.py", "agent/web_search.py", "agent/builtin_tools.py", "agent/loop.py", "webui/server.py",
           "tools/probe_j5_agent_round.py")
# ما يجوز أن يُركَّب في حاوية SearXNG: إعداداتُها ومخبؤها وحدهما. فتركيبٌ فوق برنامجها أو مدخلها يُبقي `.Path` و`.Args`
# والصورةَ والمنفذَ كما هي ويشغّل غيرَها (ملاحظة Codex على #144)
SEARXNG_MOUNTS = ("/etc/searxng", "/var/cache/searxng")
# وما تكتبه صورةُ SearXNG نفسُها في تشغيلها العاديّ، بعينه لا بمجلّده ولا بجاره ولا بما تحته: حزمةُ الشهادات التي يحدّثها
# مدخلُها عند الإقلاع باسمها كاملًا، ومخبؤها في /tmp قواعدُ SQLite باسمٍ من الصورة المثبَّتة (وملفّا WAL وSHM لكلٍّ). قيس على
# الماك في جولة k المرفوضة على 5023045 (#144): المساراتُ المرفوضةُ كلُّها من هذين، ولا يُقبل في /tmp أو /etc/ssl سواهما؛
# و«ca-certificates.crt.evil» أو ملفٌّ تحت اسم الحزمة لو صارت مجلّدًا ليسا الحزمةَ (ملاحظة Codex على #144)
SEARXNG_RUNTIME_FILES = ("/etc/ssl/certs/ca-certificates.crt",)
SEARXNG_RUNTIME_CACHE = re.compile(r"/tmp/sxng_cache_[A-Z_]+\.db(-wal|-shm)?")
# الصورةُ المثبَّتة بالبصمة في docs/guides/G5.md (الخطوتان ٢١–٢٢)، وقيست بها ج٥ أول مرّة
PINNED_SEARXNG = "searxng/searxng@sha256:5286edb35782454ab8a102c5eff6b54bff745853191b46aeead95f225aa6dfb6"
# عنوانُ الجهاز حرفيًّا لا اسمًا: «localhost» يُحلّ إلى أحدهما، والحاويةُ تنشر على عنوانٍ بعينه (ملاحظة Codex على #144)
LOOPBACK = frozenset({"127.0.0.1", "::1"})
# `LocalApp.agent_workspace` يبني خلفيّةَ run_command بمسار Docker الافتراضي في `DockerExecutionBackend` ولا يمرّر غيرَه،
# وتمريرُه من مسار openai (ق٦٦، #142)؛ فمسارٌ آخر يُرفض قبل الجولة لا يُترك يُسقطها صامتًا (ملاحظة Codex على #136)
EXECUTION_DOCKER = inspect.signature(DockerExecutionBackend).parameters["docker_executable"].default
# الحدُّ كما يصنعه `DockerExecutionBackend`: «docker:» ثم معرّفُ الحاوية كاملًا
BOUNDARY = re.compile(r"docker:[0-9a-f]{64}")


def acceptance(report: dict) -> list[str]:
    """شروطُ ج٥ التي يشهد بها الدليل، وما لم يتحقّق منها بالاسم (ملاحظة Codex على #136)."""
    execution = report["docker_execution"]
    failed = []
    # الموافقةُ على الأمر المطلوب بعينه، لا على أوّل أمرٍ معلَّق (ملاحظة Codex على #136)
    if not requested_command(execution.get("pending_argv")):
        failed.append("run_command_pending_is_not_the_requested_command")
    # نداءٌ ردّه SearXNG بخطأ لا يشهد ببحثٍ حيّ: المقبولُ نتيجةُ web_search بحالة ok (ملاحظة Codex على #136)
    succeeded = [t for attempt in report["web_search"]["attempts"] for t in attempt["tool_calls_by_the_model"]
                 if t["name"] == "web_search" and t["status"] == "ok"]
    if not succeeded:
        failed.append("web_search_never_succeeded")
    # وردٌّ بحالة ok وقائمةٍ فارغة لا يشهد بمصدرٍ واحد: المقبولُ نتيجةٌ بمصدرٍ على الأقل (ملاحظة Codex على #136)
    elif not any(_sourced(t) for t in succeeded):
        failed.append("web_search_returned_no_sourced_result")
    # والنتيجةُ من SearXNG الذي شُهد له بعينه: مصدرُ الأداة نفسُها لا ما ضُبط في المجسّ وحده
    attested = str((report["web_search"].get("backend") or {}).get("url") or "").rstrip("/")
    if succeeded and not any(_sourced(t) and attested and t.get("source_endpoint") == attested for t in succeeded):
        failed.append("web_search_source_is_not_the_attested_searxng")
    if execution["first_status"] != "awaiting_owner" or not execution["owner_approved"]:
        failed.append("run_command_did_not_wait_for_the_owner")
    if execution["final_status"] != "complete" or not any(
            t["name"] == "run_command" and t["status"] == "ok" for t in execution["tool_calls_by_the_model"]):
        failed.append("run_command_did_not_succeed")
    # الحدُّ من حقل نتيجة run_command الناجحة نفسِها، بصيغته كاملةً (ملاحظة Codex على #136): معرّفٌ مبتور أو رمزٌ
    # في نصّ النموذج لا يشهد بحاوية
    if not any(t["name"] == "run_command" and t["status"] == "ok" and t.get("boundary") == "docker:<64hex>"
               for t in execution["tool_calls_by_the_model"]):
        failed.append("run_command_boundary_is_not_docker")
    # والأمرُ نفسُه أدّى ما طُلب: خروجٌ بصفر، وخرجُه «4» من نتيجة الأداة لا من جواب النموذج
    if not any(t["name"] == "run_command" and t["status"] == "ok" and t.get("exit_code") == 0
               and t.get("output") == "4" for t in execution["tool_calls_by_the_model"]):
        failed.append("run_command_output_is_not_4")
    return failed


# الأمرُ المطلوب حرفيًّا: لا python بدل python3، ولا شيفرةٌ تساويه بعد حذف المسافات (ملاحظة Codex على #144)
REQUESTED_ARGV = ["python3", "-c", "print(2+2)"]


def requested_command(argv) -> bool:
    """argv الأمر المعلَّق هو ما طُلب بعينه."""
    return argv == REQUESTED_ARGV


def _repo_modules(modules=None):
    """وحداتُ المستودع المحمَّلة: (مسارُها النسبيّ، الوحدة)، بلا الاختبارات والحزم المثبَّتة والمجلّدات المخفيّة."""
    for module in list((sys.modules if modules is None else modules).values()):
        file = getattr(module, "__file__", None)
        if not isinstance(file, str) or not file.endswith(".py"):
            continue
        try:
            rel = Path(file).resolve().relative_to(ROOT)
        except ValueError:
            continue
        if not NOT_SOURCE.intersection(rel.parts) and not any(part.startswith(".") for part in rel.parts):
            yield rel.as_posix(), module


def _sources() -> dict[str, str]:
    paths = set(SOURCES) | {path for path, _ in _repo_modules()}
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in sorted(paths)}


def _cached_elsewhere(modules=None) -> list[str]:
    """وحداتُ المستودع المحمَّلة التي لم تُترجم في مخبأ المجسّ الجديد (`PYCACHE`)، فقد تُنفَّذ من بايتاتٍ قديمة.
    والمجسُّ نفسُه (`__main__`) يُترجم من مصدره حين يُشغَّل."""
    fresh = lambda cached: (PYCACHE is not None and isinstance(cached, str)
                            and Path(cached).resolve().is_relative_to(Path(PYCACHE).resolve()))
    return sorted(path for path, module in _repo_modules(modules)
                  if module.__name__ != "__main__" and not fresh(getattr(module, "__cached__", None)))


def _inspect(docker: str, target: str, fmt: str, kind: str = "container") -> str:
    """حاويةٌ لا صورة: `docker inspect` وحده يقبل اسمَ صورةٍ أيضًا. وبالبيئة النظيفة نفسِها التي يشغّل بها
    `DockerExecutionBackend` Docker: `DOCKER_HOST` أو `DOCKER_CONTEXT` الموروثان يشهدان لحاويةٍ على خادمٍ غيرِ خادم
    الجولة (ملاحظة Codex على #144)."""
    return subprocess.run([docker, kind, "inspect", "--format", fmt, target], capture_output=True, text=True,
                          timeout=30, env=_clean_env()).stdout.strip()


def _changes(docker: str, container_id: str) -> list[tuple[str, str]] | None:
    """ما تغيّر في طبقة الحاوية القابلة للكتابة (`docker container diff`) نوعًا ومسارًا، أو None إن تعذّرت قراءتُه."""
    done = subprocess.run([docker, "container", "diff", container_id], capture_output=True, text=True, timeout=30,
                          env=_clean_env())
    if done.returncode != 0:
        return None
    return [tuple(line.split(" ", 1)) for line in done.stdout.splitlines() if " " in line]


def _foreign_changes(changes: list[tuple[str, str]]) -> list[str]:
    """ما تغيّر في الطبقة القابلة للكتابة خارج إعدادات SearXNG ومخبئها: كلُّ تبديلٍ في غيرهما يُرفض، لا في مجلّد برنامجها
    وحده، فمفسّرٌ أو مكتبةٌ أو صدفةٌ مُبدَلة تشغّل غيرَها بالعملية نفسِها (ملاحظة Codex على #144). وتغيُّرُ مجلّدٍ أبٍ لهما
    («C /etc») هو أثرُ إنشاء نقطة التركيب فيه لا غير."""
    allowed = lambda path: (any(path == m or path.startswith(m + "/") for m in SEARXNG_MOUNTS)
                            or path in SEARXNG_RUNTIME_FILES or SEARXNG_RUNTIME_CACHE.fullmatch(path) is not None)
    roots = (*SEARXNG_MOUNTS, *SEARXNG_RUNTIME_FILES, "/tmp/sxng_cache_")
    parent = lambda kind, path: kind == "C" and any(root.startswith(path.rstrip("/") + "/") for root in roots)
    return sorted(path for kind, path in changes if not (allowed(path) or parent(kind, path)))


def _serving(url: str, container_id: str, docker: str) -> dict:
    """حالُ الحاوية بمعرّفها الثابت: صورتُها، وهل تعمل ومنذ متى، وهل تنشر منفذَ الرابط على عنوان الجهاز. فحاويةٌ أُعيد
    تشغيلُها أو أُزيلت تختلف حالُها، ولا يُسأل الاسمُ الذي قد تحمله حاويةٌ أخرى (ملاحظة Codex على #144)."""
    parts = urllib.parse.urlsplit(url)
    host, port = parts.hostname, str(parts.port)
    ports = json.loads(_inspect(docker, container_id, "{{json .NetworkSettings.Ports}}") or "null") or {}
    # والبروتوكولُ من مفتاح المنفذ («8080/tcp»): نشرُ UDP على العنوان والمنفذ لا يشهد لرابط HTTP قد يخدمه غيرُها
    # (ملاحظة Codex على #144)
    published = {(key, b.get("HostIp"), b.get("HostPort")) for key, bindings in ports.items() for b in bindings or []}
    image_id = _inspect(docker, container_id, "{{.Image}}")
    # وما يعمل في الحاوية عمليةُ صورتها نفسُها (مدخلُها وأمرُها الافتراضيّان)، والمنفذُ الداخليّ منفذٌ تعلنه الصورة: حاويةٌ من
    # الصورة المثبَّتة بمدخلٍ أو أمرٍ مُبدَل تشغّل خادمًا آخر يُجيب بـJSON ولا يُقاس SearXNG (ملاحظة Codex على #144)
    loads = lambda text: json.loads(text or "null")
    process = [loads(_inspect(docker, container_id, "{{json .Path}}")), *(loads(_inspect(docker, container_id,
                                                                                          "{{json .Args}}")) or [])]
    image = lambda fmt: loads(_inspect(docker, image_id, fmt, "image")) if image_id else None
    default = [*(image("{{json .Config.Entrypoint}}") or []), *(image("{{json .Config.Cmd}}") or [])]
    exposed = image("{{json .Config.ExposedPorts}}") or {}
    mounts = sorted(str(m.get("Destination")) for m in loads(_inspect(docker, container_id, "{{json .Mounts}}")) or [])
    # وطبقتُها القابلة للكتابة كصورتها إلا إعداداتِها ومخبأها: حاويةٌ من الصورة المثبَّتة بُدّل فيها المدخلُ أو التطبيقُ أو
    # مكتبةٌ بعد إنشائها تُبقي الصورةَ والعمليةَ والتركيبَ والمنفذ كما هي ويخدم غيرُها (ملاحظتا Codex على #144). ويُقارن ما
    # تغيّر بعد الجولة كغيره
    changed = _changes(docker, container_id)
    return {"id": container_id,
            "image": _inspect(docker, container_id, "{{.Config.Image}}"),
            "image_id": image_id,
            "running": _inspect(docker, container_id, "{{.State.Running}}") == "true",
            "started_at": _inspect(docker, container_id, "{{.State.StartedAt}}"),
            "runs_the_image_default": process == default,
            "mounts": mounts,
            "writable_layer_changes": None if changed is None else _foreign_changes(changed),
            "overlays_the_image": any(not any(m == allowed or m.startswith(allowed + "/") for allowed in SEARXNG_MOUNTS)
                                      for m in mounts),
            # والعنوانُ المنشور هو عنوانُ الرابط نفسُه، لا أيُّ عنوانٍ للجهاز: حاويةٌ على 127.0.0.1 لا تشهد لرابطٍ على ::1
            # قد يخدمه غيرُها (ملاحظة Codex على #144)؛ وبروتوكولُه TCP ومنفذُه الداخليّ ممّا تعلنه الصورة
            "serves_url": any(key.endswith("/tcp") and key in exposed and ip == host and p == port
                              for key, ip, p in published)}


def _searxng(url: str, container: str, docker: str) -> tuple[dict, dict]:
    """الرابطُ على عنوان الجهاز، والحاويةُ تعمل وتنشر منفذَه على عنوان الجهاز، وصورتُها المثبَّتة؛ وإلا فلا قياس.
    ويُعاد معها حالُها بمعرّفها، فتُفحص بعد الجولة."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in LOOPBACK or not parts.port:
        raise SystemExit(json.dumps({"status": "refused", "code": "web_search_url_not_loopback"}))
    # الاسمُ يُحلّ إلى معرّفٍ مرّةً واحدة، وكلُّ ما بعده بالمعرّف
    container_id = _inspect(docker, container, "{{.Id}}")
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise SystemExit(json.dumps({"status": "refused", "code": "searxng_container_not_found"}))
    pinned = _serving(url, container_id, docker)
    if pinned["image"] != PINNED_SEARXNG:
        raise SystemExit(json.dumps({"status": "refused", "code": "searxng_image_not_pinned"}))
    if not pinned["runs_the_image_default"]:
        raise SystemExit(json.dumps({"status": "refused", "code": "searxng_container_process_overridden"}))
    if pinned["overlays_the_image"]:
        raise SystemExit(json.dumps({"status": "refused", "code": "searxng_container_overlays_the_image"}))
    if pinned["writable_layer_changes"] is None:
        raise SystemExit(json.dumps({"status": "refused", "code": "searxng_container_writable_layer_unreadable"}))
    if pinned["writable_layer_changes"]:
        raise SystemExit(json.dumps({"status": "refused", "code": "searxng_container_writable_layer_changed",
                                     "paths": pinned["writable_layer_changes"]}))
    if not (pinned["running"] and pinned["serves_url"]):
        raise SystemExit(json.dumps({"status": "refused", "code": "searxng_container_does_not_serve_the_url"}))
    return {"url": url, "container": container, "image": pinned["image"], "image_id": pinned["image_id"]}, pinned


def _digest(model: str, base: str = "http://127.0.0.1:11434") -> str:
    """بصمةُ النموذج من Ollama على الجهاز نفسِه، بلا وسيط البيئة: `HTTP_PROXY` بلا `NO_PROXY` كان يُرسل الطلبَ إلى الوسيط
    فيُجيب ببصمةٍ لأوزانٍ لم تُشغَّل، والمزوّدُ يتّصل بـOllama مباشرةً (ملاحظة Codex على #144)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(base + "/api/tags", timeout=10) as response:
        models = json.loads(response.read().decode("utf-8"))["models"]
    return next(m["digest"] for m in models if m["name"] == model)


def _source_url(value) -> bool:
    """رابطٌ http(s) له مضيف، لا بادئةٌ وحدها مثل «https://» (ملاحظة Codex على #144)."""
    if not isinstance(value, str):
        return False
    try:
        parts = urllib.parse.urlsplit(value)
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False


def _sourced(call: dict) -> bool:
    """نتيجةُ web_search ناجحةٌ وفيها نتيجةٌ واحدةٌ على الأقل برابط."""
    count = call.get("sourced_results")
    return call["name"] == "web_search" and call["status"] == "ok" and type(count) is int and count >= 1


def _tools(result: dict) -> list[dict]:
    calls = []
    for step in result.get("steps", []):
        for r in step.get("tool_results", []):
            call = {"name": r.get("name"), "status": r.get("status")}
            if r.get("name") == "web_search":
                # يُعدّ من نتائج الأداة نفسِها ما له رابط، لا من روابط جواب النموذج
                results = r.get("results")
                call["sourced_results"] = sum(1 for item in results if isinstance(item, dict)
                                              and _source_url(item.get("url"))) if isinstance(results, list) else 0
                source = r.get("source")
                call["source_endpoint"] = source.get("endpoint") if isinstance(source, dict) else None
            if r.get("name") == "run_command":
                # رمزُ الخروج والخرجُ المقصوص من نتيجة الأداة نفسِها، لا من جواب النموذج
                call["exit_code"] = r.get("exit_code")
                content = r.get("content")
                call["output"] = content.strip()[:40] if isinstance(content, str) else None
            if "boundary" in r:
                boundary = r["boundary"]
                call["boundary"] = ("docker:<64hex>" if isinstance(boundary, str) and BOUNDARY.fullmatch(boundary)
                                    else "malformed")
            calls.append(call)
    return calls


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", required=True)
    parser.add_argument("--web-search-url", required=True, help="SearXNG على عنوان الجهاز، مثل http://127.0.0.1:8888")
    parser.add_argument("--searxng-container", default="searxng", help="اسمُ حاوية SearXNG التي تنشر منفذ الرابط")
    parser.add_argument("--docker", default=EXECUTION_DOCKER)
    parser.add_argument("--runtime-receipt", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"التقريرُ قائم: {args.out}")
    if args.docker != EXECUTION_DOCKER:
        print(json.dumps({"status": "refused", "code": "run_command_backend_uses_the_default_docker_path",
                          "expected": EXECUTION_DOCKER}))
        return 2
    if stale := _cached_elsewhere():
        print(json.dumps({"status": "refused", "code": "measured_code_not_compiled_from_its_hashed_source",
                          "modules": stale}))
        return 2
    sources = _sources()
    # والإيصالُ الذي تبني به الواجهةُ خلفيّةَ run_command يُقرأ قبل الجولة ويُعاد قبل الكتابة، ومنه وحده تُسجَّل الصورة،
    # كما في مجسّ الحدّ: إيصالٌ استُبدل في أثنائها ينسب الدليلَ إلى صورةٍ لم تُشغَّل
    try:
        receipt_bytes = args.runtime_receipt.read_bytes()
        receipt = json.loads(receipt_bytes.decode("utf-8"))
    except (OSError, ValueError):
        print(json.dumps({"status": "refused", "code": "runtime_receipt_unreadable"}))
        return 2
    searxng, pinned = _searxng(args.web_search_url, args.searxng_container, args.docker)
    version = _digest(args.model)
    root = Path(tempfile.mkdtemp(prefix="diwan-j5-ui-")).resolve()
    # والواجهةُ تفتح الإيصالَ حين تُنشئ أولَ مساحة، أي في أثناء الجولة؛ فتُعطى نسخةً خاصّةً من البايتات المبصومة لا مسارَه:
    # إيصالٌ استُبدل مؤقتًا في أثنائها ثم أُعيد قبل إعادة القراءة يُشغّل صورةً غيرَ المسجَّلة (ملاحظة Codex على #144)
    pin = Path(tempfile.mkdtemp(prefix="diwan-j5-receipt-")).resolve()
    try:
        pinned_receipt = pin / "runtime-receipt.json"
        with os.fdopen(os.open(pinned_receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(receipt_bytes)
        app = LocalApp(root, model=args.model, model_version=version,
                       provider_factory=lambda: LocalChatProvider(args.model, version),
                       agent_provider_factory=lambda: LocalToolProvider(args.model, version),
                       runtime_receipt=pinned_receipt,
                       web_search=SearxngBackend(args.web_search_url),
                       docker_executable=args.docker)
        api = lambda action, **values: app.dispatch({"action": action, **values})
        project = api("create_project", name="ج٥")["id"]

        attempts = []
        for question in SEARCH_QUESTIONS:
            research = api("create_session", project=project, name="بحث", mode="research")["id"]
            searched = api("agent_ask", project=project, session=research, turn=uuid.uuid4().hex,
                           message=question, files=[])
            content = searched.get("content") or ""
            attempts.append({"question": question, "status": searched.get("status"),
                             "steps": len(searched.get("steps", [])), "tool_calls_by_the_model": _tools(searched),
                             "called_web_search": any(t["name"] == "web_search" for t in _tools(searched)),
                             "citations_passed": (searched.get("citations") or {}).get("passed"),
                             "answer_urls": sorted(set(re.findall(r"https?://[^\s\"\\)\]]+", content)))[:10],
                             "answer_chars": len(content)})
            if any(_sourced(t) for t in attempts[-1]["tool_calls_by_the_model"]):
                break

        agent = api("create_session", project=project, name="أمر", mode="agent")["id"]
        turn = uuid.uuid4().hex
        asked = api("agent_ask", project=project, session=agent, turn=turn, message=COMMAND_REQUEST, files=[])
        decided = resumed = None
        pending = [a for a in asked.get("pending", []) if a.get("name") == "run_command"]
        pending_argv = (pending[0].get("arguments") or {}).get("argv") if pending else None
        # لا يُوافَق إلا على الأمر المطلوب: أمرٌ آخر يُترك معلَّقًا ويُسجَّل argv، فلا يُنشر كأنه ما طُلب
        if asked.get("status") == "awaiting_owner" and pending and requested_command(pending_argv):
            action = pending[0]
            decided = api("agent_decide", project=project, session=agent, action_id=action["action_id"],
                          call_digest=action["call_digest"], expected_revision=action["revision"], approve=True)
            resumed = api("agent_resume", project=project, session=agent, turn=turn)
        final = resumed or asked
        app.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(pin, ignore_errors=True)
    # ملفٌّ تغيّر في أثناء الجولة يجعل البصمةَ تشهد لبايتاتٍ لم تُنفَّذ، فلا يُكتب تقرير (ملاحظة Codex على #136)
    # وحدةٌ حُمّلت أولَ مرّةٍ في أثناء الجولة تُبصم بعدها، فلا بصمةَ لها قبلها تُقارن بها
    after = _sources()
    changed = sorted(path for path, digest in sources.items() if after.get(path) != digest)
    changed += sorted(path for path, digest in after.items() if BEFORE_IMPORT.get(path) != digest and path not in changed)
    # وحاويةُ SearXNG التي شُهد لها، والنموذجُ الذي بُصم: حاويةٌ أُعيد تشغيلُها أو استُبدلت أو كفّت عن نشر المنفذ، أو نموذجٌ
    # سُحب من جديد، في أثناء الجولة يجعلان التقريرَ يشهد لما لم يُقَس (ملاحظة Codex على #144)
    if _serving(args.web_search_url, pinned["id"], args.docker) != pinned:
        changed.append("searxng_container")
    if _digest(args.model) != version:
        changed.append("engine_digest")
    try:
        receipt_now = args.runtime_receipt.read_bytes()
    except OSError:
        receipt_now = None
    if receipt_now != receipt_bytes:
        changed.append("runtime_receipt")
    if changed:
        print(json.dumps({"status": "refused", "code": "sources_changed_during_the_run", "changed": changed}))
        return 2
    # ووحدةٌ حُمّلت أولَ مرّةٍ في أثناء الجولة تُترجم في المخبأ نفسِه، وإلا فلا تقرير
    if stale := _cached_elsewhere():
        print(json.dumps({"status": "refused", "code": "measured_code_not_compiled_from_its_hashed_source",
                          "modules": stale}))
        return 2

    report = {
        "schema_version": 1, "date": datetime.date.today().isoformat(), "task": "ج٥", "issue": "power0man/diwan#23",
        "author": "anthropic/claude-opus-5-5",
        "host": {"machine": os.uname().machine, "os": os.uname().sysname + " " + os.uname().release},
        "engine": {"provider": "ollama-local", "model": args.model, "digest": version},
        "runtime": {"image_id": receipt.get("image_id") if isinstance(receipt, dict) else None,
                    "lock_sha256": receipt.get("lock_sha256") if isinstance(receipt, dict) else None,
                    "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest()},
        "source_sha256": {**after, **sources},
        "web_search": {"via": "webui.server.LocalApp.dispatch agent_ask (research session)", "backend": searxng,
                       "called_web_search": any(a["called_web_search"] for a in attempts),
                       "attempts": attempts, "attempts_until_called": len(attempts)},
        "docker_execution": {"via": "webui.server.LocalApp.dispatch agent_ask → agent_decide(approve) → agent_resume",
                             "request": COMMAND_REQUEST, "first_status": asked.get("status"),
                             "pending_argv": pending_argv,
                             # الموافقةُ ما ردّه مخزنُ الأفعال بحالة approved، لا أيُّ ردٍّ على الطلب ولو خطأً
                             "decision_state": decided.get("state") if isinstance(decided, dict) else None,
                             "owner_approved": isinstance(decided, dict) and decided.get("state") == "approved",
                             "final_status": final.get("status"),
                             "tool_calls_by_the_model": _tools(final),
                             "answer": (final.get("content") or "")[:300]},
        "measurement_limits": [
            "one_live_round_each_on_one_local_model_no_variance_estimate",
            "the_model_decides_whether_to_call_the_tool_every_attempt_is_recorded_including_those_without_a_call",
            "search_results_come_from_public_engines_through_a_local_searxng_and_change_over_time",
            "container_ids_recorded_by_shape_not_value",
            "boundary_read_from_the_run_command_result_field_not_from_the_serialized_reply",
            "web_search_accepted_only_with_a_result_carrying_a_url_counted_from_the_tool_result_not_the_answer",
            "only_the_requested_command_is_approved_its_pending_argv_recorded",
            "sources_and_probe_hashed_before_the_round_and_rechecked_before_writing",
            "every_repo_module_loaded_is_hashed_those_first_imported_during_the_round_only_after_it",
            "runtime_receipt_read_before_the_round_and_rechecked_before_writing",
            "the_ui_runs_against_a_private_copy_of_the_hashed_receipt_bytes_not_the_receipt_path",
            "searxng_container_pinned_by_id_and_start_time_and_engine_digest_rechecked_after_the_round",
            "searxng_container_runs_its_image_default_entrypoint_and_command_on_a_port_the_image_exposes",
            "searxng_container_mounts_only_its_configuration_and_cache",
            "searxng_writable_layer_unchanged_outside_its_mounts_and_its_named_runtime_writes_before_and_after_the_round",
            "the_searxng_cache_files_and_the_ca_bundle_are_allowed_by_their_exact_names_not_inspected",
            "every_loaded_repo_module_is_hashed_before_the_measured_code_is_imported_and_must_match_after_the_round",
            "every_loaded_repo_module_is_compiled_from_its_source_into_a_fresh_empty_bytecode_cache",
            "a_deliberate_same_user_process_rewriting_files_or_containers_between_checks_is_out_of_scope",
            "run_command_accepted_only_with_exit_code_0_and_output_4_from_the_tool_result",
            "web_search_accepted_only_from_the_attested_searxng_endpoint_with_a_url_that_has_a_host",
        ],
    }
    failed = acceptance(report)
    report["acceptance"] = {"passed": not failed, "failed": failed}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"web_search_called": report["web_search"]["called_web_search"],
                      "attempts": [(a["called_web_search"], a["answer_chars"], len(a["answer_urls"]))
                                   for a in attempts],
                      "command": [report["docker_execution"]["first_status"], report["docker_execution"]["final_status"]],
                      "tools": report["docker_execution"]["tool_calls_by_the_model"],
                      "acceptance": report["acceptance"]}, ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
