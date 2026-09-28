#!/usr/bin/env python3
"""فحصُ الواجهة في متصفّحٍ حقيقيّ (جديد-ui-browser-audit): رحلةُ مستخدمٍ أوّل بـChromium بلا رأس على الواجهة نفسِها.

الخادمُ هو `webui.server.LocalApp` و`Server` كما يشغّلهما `tools/serve_ui.py`، على 127.0.0.1 بمنفذٍ زائل وجذرٍ مؤقّت،
والمزوّدُ مكتوبٌ سلفًا (`ScriptedProvider`) فزمنُ النموذج صفرٌ وجودتُه لا تُقاس. والمتصفّحَ يقوده
`tools/ui_browser_audit.cjs` بـPlaywright لـNode. ولكل رحلةٍ وعرضٍ خادمٌ بجذرٍ فارغ: مستخدمٌ أوّل. رحلتان:

- **current**: رحلةُ الواجهة القائمة على عرض الحاسوب 1366×768: الحالةُ الفارغة ← سؤالٌ قبل المحادثة ← مشروعٌ ←
  محادثةٌ نصّية ← Enter وCtrl+Enter ← سؤالٌ عربيّ وجوابُه ← مسودةٌ تُراجَع وتُحفظ ← الذاكرة ← جلسةُ أدواتٍ تكتب ملفًّا
  ← وأخرى تطلب موافقةً فتُقبل ← مسارُ خطأ؛ ثم على الجوّال 390×844: الشاشةُ الأولى والتمريرُ الأفقيّ وسؤالٌ وجوابُه.
  مع الاتجاه وترتيب التركيز والأسماء الوصولية من شجرة Chromium ومناطق الإعلان وسجلّ الوحدة، وaxe-core إن أُعطي.
- **single-page**: قبولُ #166 (الصفحةُ الموحّدة): فتحُ الصفحة ← سؤالٌ في أول حقل نصّ ← Enter ← يظهر الجواب، على
  العرضين، بلا إنشاء مشروع. يُشغَّل اليوم خطَّ أساسٍ (ويسقط)، ويُعاد بعد #166 كما هو.

    python tools/ui_browser_audit.py [--journey all|current|single-page] [--axe <axe.min.js>]
        [--out docs/probe/ui-browser-audit-<date>.json] [--shots docs/probe/ui-browser-audit-<date>]
        [--tracked <finding>=<issue> ...]

يلزمه `node` في PATH (أو `--node`) ووحدةُ `playwright` لـNode يجدها `require` (يُضاف `npm root -g` إلى NODE_PATH)
ومتصفّحُها. الخروج: 0 كُتب الدليل؛ 3 تعذّر المتصفّحُ بحدٍّ مسمًّى (`node_missing`، `playwright_missing`،
`chromium_missing`)؛ 1 عطبٌ، أو دليلٌ يخرق حارسَه (`evidence_guard`: مسارٌ مطلق، نصٌّ طويل من الصفحة، لقطةٌ فوق الحدّ)
فلا يُكتب. والمتصفّحُ لا يعمل في CI؛ فاختباراتُ `tests/test_ui_browser_audit.py` للمنطق الخالص وللمزوّد المكتوب وحدهما.
"""
from __future__ import annotations

import argparse
import ast
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCRIPT = ROOT / "tools" / "ui_browser_audit.cjs"
TOOL = "tools/ui_browser_audit.py"
AGENT = "anthropic/claude-opus-5-5"
JOURNEYS = ("current", "single-page")
VIEWPORTS = ("desktop", "mobile")
UI_SOURCES = ("webui/static/index.html", "webui/static/app.js", "webui/static/style.css", "webui/server.py")
# ما يتعلّق به الناتج: الأداةُ وسائقُها (يحرسهما اختبارٌ على الدليل المنشور) وملفّاتُ الواجهة المخدومة (تعرّف النسخةَ المفحوصة،
# ويُنتظر أن تفترق عنها بعد #166 فلا يحرسها اختبار)
IMPLEMENTATION = (TOOL, "tools/ui_browser_audit.cjs")
DIGESTED = (*IMPLEMENTATION, *UI_SOURCES)
MODEL, VERSION = "scripted-audit", "0" * 64

# ما يقوله المزوّدُ المكتوب: جوابٌ عربيّ فيه Markdown وأسماءٌ لاتينية (لفحص العرض والاتجاه)، وأفعالٌ بكلماتٍ مفتاحية
ANSWER_MARKER = "أهلًا بك في ديوان"
TEXT_ANSWER = (f"{ANSWER_MARKER}. هذه خطةٌ قصيرة:\n\n## الخطة\n"
               "- **أولًا:** اجمع ملاحظاتك في ملف `notes.txt`.\n"
               "- **ثانيًا:** راجعها كلَّ أسبوع، وابدأ بالإصدار v0.1.0.")
WRITE_WORD, MEMORY_WORD = "اكتب", "احفظ"
QUESTION = "مرحبًا يا ديوان، اقترح عليّ خطةً قصيرة لتنظيم ملاحظاتي."
WRITE_REQUEST = f"{WRITE_WORD} ملاحظةً قصيرة في ملفٍّ داخل المشروع."
MEMORY_REQUEST = f"{MEMORY_WORD} في ذاكرة المشروع أنني أفضّل الأجوبة الموجزة."
WRITTEN_FILE = "ملاحظة.txt"
WRITTEN_TEXT = "ملاحظةٌ قصيرة كتبها ديوان بطلبك."
MEMORY_TEXT = "يفضّل المالكُ الأجوبةَ الموجزة."
AGENT_DONE = "انتهيتُ: كتبتُ الملفَّ المطلوب في مساحة المشروع."
MEMORY_DONE = "حُفظ ما طلبتَ في ذاكرة المشروع بعد موافقتك."

# حارسُ الدليل: لا مسارَ مطلقًا ولا نصًّا طويلًا من الصفحة؛ واللقطاتُ محدودةٌ عددًا وحجمًا
MAX_TEXT_CHARS = 240
MAX_SHOTS = 10
MAX_SHOT_BYTES = 250 * 1024
# مسارٌ مطلق أيًّا كان جذرُه وما سبقه، بلا عدٍّ للفواصل: «/» لا يسبقها حرفٌ أو رقم (بأيّ أبجدية) ولا «.» ولا «-» ولا «_»
# ولا «/» أخرى (فتلك مقطعٌ في مسارٍ نسبيّ أو «//» عنوانِ URL)، ويتبعها مقطع (ومنه «~/…» فـ«/» فيه بعد «~»)؛ وحرفُ قرصٍ في ويندوز بشرطةٍ أيًّا كانت،
# ومسارُ UNC، و«file://». والمساراتُ النسبية في المستودع (docs/probe/…، webui/static/app.js:57) وعناوينُ http(s) تمرّ.
ABSOLUTE_PATH = re.compile(
    r"(?<![\w.\-/])/(?=[^\s/\"'<>|;,)\]}»])"
    r"|(?<![A-Za-z0-9])[A-Za-z]:[\\/]"
    r"|(?<![\w\\])\\\\[^\\\s]"
    r"|file://", re.IGNORECASE)
SHOT_NAME = re.compile(r"[0-9]{2}-[a-z0-9-]+\.png")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

MEASUREMENT_LIMITS = [
    "scripted_fake_provider_so_model_latency_is_zero_and_answer_quality_is_not_measured",
    "headless_chromium_only_no_safari_no_firefox_no_real_screen_reader",
    "one_scripted_journey_by_the_tool_not_a_user_study_and_not_owner_acceptance",
    "linux_fonts_tahoma_and_arial_absent_arabic_falls_back_to_dejavu_so_glyph_shapes_differ_from_mac_and_windows",
    "axe_core_catches_a_subset_of_wcag_failures_and_a_clean_rule_is_not_an_accessible_page",
    "error_code_coverage_parses_webui_server_py_only_codes_raised_by_other_modules_are_listed_as_dynamic_lines_not_counted",
    "first_run_configuration_only_research_media_and_command_execution_are_disabled_as_serve_ui_starts_without_searxng_vision_or_docker",
    "timings_are_loopback_with_a_fake_provider_they_bound_the_ui_and_http_path_not_a_real_answer",
]


# — المزوّدُ المكتوب —

def _say(content: str, *calls):
    from core.contracts import Response, Usage
    return Response(content, Usage(12, 30), "complete", 0, provider=MODEL, model_version=VERSION,
                    tool_calls=tuple(calls))


def _user_request(message) -> str:
    """طلبُ المستخدم من رسالةٍ قد تحمل كتلةَ ذاكرةٍ ومغلّفَ JSON؛ وإلّا نصُّها كلُّه."""
    content = getattr(message, "content", "") or ""
    match = re.search(r'"user_request":("(?:[^"\\]|\\.)*")', content)
    if match:
        try:
            return json.loads(match.group(1))
        except ValueError:
            pass
    return content


class ScriptedProvider:
    """مزوّدٌ محليّ مكتوبٌ سلفًا: يختار ردَّه من الطلب نفسِه، فلا يتعلّق بترتيب الرحلة.

    بلا أدوات (المحادثة النصّية): `TEXT_ANSWER`. وبأدوات: بعد نتيجة أداةٍ جوابٌ ختاميّ باسمها؛ وإلّا فـ«احفظ» ←
    `propose_memory` (درجةُ المالك: تقف الجولةُ لموافقته)، و«اكتب» ← `write_file` (مسجَّلة: تُنفَّذ ويُرجع عنها)،
    وما سواهما `TEXT_ANSWER`: فسؤالُ الصفحة الموحّدة يُجاب ولو صارت كلُّ جولةٍ وكيلة (#166).
    """
    name, is_local = MODEL, True

    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        from core.contracts import ToolCall
        with self._lock:
            self.calls += 1
        tools = {spec.name for spec in request.tools}
        if not tools:
            return _say(TEXT_ANSWER)
        last = request.messages[-1]
        if last.role == "tool":
            called = next((call.name for message in reversed(request.messages)
                           for call in message.tool_calls), "")
            return _say({"write_file": AGENT_DONE, "propose_memory": MEMORY_DONE}.get(called, TEXT_ANSWER))
        asked = _user_request(last)
        if MEMORY_WORD in asked and "propose_memory" in tools:
            return _say("", ToolCall("memory-1", "propose_memory", {"text": MEMORY_TEXT}))
        if WRITE_WORD in asked and "write_file" in tools:
            return _say("", ToolCall("write-1", "write_file", {"path": WRITTEN_FILE, "content": WRITTEN_TEXT}))
        return _say(TEXT_ANSWER)


@contextmanager
def serving(root: Path):
    """`LocalApp` و`Server` الحقيقيّان على 127.0.0.1 بمنفذٍ زائل؛ يُعيد (الأصل، المزوّد).

    ما يراه مستخدمٌ أوّل بـ`serve_ui.py` ومحرّكٍ محليّ بلا SearXNG ولا نموذج رؤية: النصُّ والأدواتُ والمبرمجُ
    والترجمة متاحة، والبحثُ والوسائطُ معطّلان؛ والجذرُ فارغ."""
    from webui.server import LocalApp, Server
    provider = ScriptedProvider()
    app = LocalApp(root, model=MODEL, model_version=VERSION, provider_factory=lambda: provider,
                   agent_provider_factory=lambda: provider)
    try:
        server = Server(app, 0)
    except BaseException:
        app.close()
        raise
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server.origin, provider
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        app.close()


# — المنطقُ الخالص: التغطية، والمواضع، والنتائج، والحارس —

def _text(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def server_codes(server_source: str) -> tuple[dict[str, int], list[int]]:
    """رموزُ الرفض في الخادم بنيويًّا (`ast`) لا سطرًا سطرًا: `need(…)` برمزه وسيطًا ثانيًا أو باسمه `code` ولو امتدّ
    النداءُ أسطرًا، وإلا فقيمتُه الافتراضية من تعريف `need` نفسِه؛ و`UIError(…)`؛ وقيمُ `"error_code"` الحرفية في القواميس؛
    وبديلُ الرفض `code = getattr(exc, "code", …)` في معالج الطلب (لا حقولُ الحالة مثل `error_code=getattr(…)` في وصف
    القدرات، فتلك لا يعرضها `showError`). يُعيد {الرمز: أولُ سطرٍ له}، وأسطرَ ما رمزُه متغيّرٌ يأتي من وحدةٍ أخرى."""
    tree = ast.parse(server_source)
    default = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "need":
            params = [arg.arg for arg in node.args.args]
            defaults = dict(zip(params[len(params) - len(node.args.defaults):], node.args.defaults))
            default = _text(defaults.get("code"))
    codes: dict[str, int] = {}
    dynamic: list[int] = []
    for node in ast.walk(tree):
        found = []
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("need", "UIError"):
            position = 1 if node.func.id == "need" else 0
            argument = (node.args[position] if len(node.args) > position
                        else next((word.value for word in node.keywords if word.arg == "code"), None))
            if argument is None:
                found.append(default if node.func.id == "need" else None)
            elif _text(argument) is None:
                dynamic.append(node.lineno)
            else:
                found.append(_text(argument))
        elif (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
              and isinstance(node.value.func, ast.Name) and node.value.func.id == "getattr"
              and len(node.value.args) == 3 and _text(node.value.args[1]) == "code"
              and [getattr(target, "id", None) for target in node.targets] == ["code"]):
            found.append(_text(node.value.args[2]))
        elif isinstance(node, ast.Dict):
            found.extend(_text(value) for key, value in zip(node.keys, node.values)
                         if key is not None and _text(key) == "error_code")
        for code in found:
            if code:
                codes.setdefault(code, node.lineno)
    return codes, sorted(set(dynamic))


# فرعٌ في الواجهة يعالج رمزًا بعينه: `["a", "b"].includes(e.code)` أو `x.code === "a"`
BRANCH_LIST = re.compile(r"\[([^\[\]]*)\]\.includes\(\s*[\w.]*\bcode\s*\)")
BRANCH_EQUAL = re.compile(r'\bcode\s*===\s*"([a-z][a-z0-9_]*)"')


def error_code_coverage(server_source: str, app_source: str) -> dict:
    """رموزُ الرفض في الخادم مقابل مفاتيح رسائلها العربية في الواجهة (`const errors = {…}`) وفروعِها الخاصّة.

    ما لا رسالةَ له ولا فرع يعرضه `showError` بصيغة «تعذر إكمال العملية (<الرمز>)…»: رمزٌ خامٌ أمام المستخدم."""
    codes, dynamic = server_codes(server_source)
    block = re.search(r"const errors = \{(.*?)\n\};", app_source, re.DOTALL)
    mapped = set(re.findall(r"^\s*([a-z][a-z0-9_]*):", block.group(1), re.MULTILINE)) if block else set()
    rest = app_source.replace(block.group(0), "") if block else app_source
    branched = {code for match in BRANCH_LIST.finditer(rest) for code in re.findall(r'"([a-z][a-z0-9_]*)"', match.group(1))}
    branched = (branched | set(BRANCH_EQUAL.findall(rest))) & set(codes) - mapped
    raw = sorted(set(codes) - mapped - branched)
    return {"server_codes": len(codes), "with_arabic_message": len(set(codes) & mapped),
            "handled_by_a_branch": sorted(branched), "raw_codes": raw,
            "raw_code_lines": {code: codes[code] for code in raw}, "dynamic_code_lines": dynamic,
            "scope": "need_UIError_error_code_literals_and_the_handler_code_fallback_in_webui_server_py_parsed_with_ast"}


def locate(anchor: str, sources: dict[str, str]) -> str | None:
    """أولُ موضعٍ لنصٍّ في ملفات الواجهة بصيغة `ملف:سطر`، أو لا شيء إن زال (بعد إعادة التصميم مثلًا)."""
    for path, text in sources.items():
        at = text.find(anchor)
        if at >= 0:
            return f"{path}:{text.count(chr(10), 0, at) + 1}"
    return None


# قواعدُ النتائج: (المعرّف، الشدّة، هل تبقى في الصفحة الموحّدة، المرساة في الشيفرة، الخطوة، اللقطة، الملخّص).
# «yes» عيبٌ يبقى بعد #166 ما لم يُصلَح؛ «no» تزيله الصفحةُ الموحّدة بتصميمها؛ «recheck» يُعاد قياسُه عليها.
FINDING_RULES = (
    ("raw-error-codes", "high", "yes", "تعذر إكمال العملية (${error.code", "error_path", "06-raw-error-desktop.png",
     "رموزُ رفضٍ بلا رسالةٍ عربية تظهر خامًا للمستخدم"),
    ("first-message-needs-project-and-session", "high", "no", "اختر محادثة واسترجع حالة", "send_before_session",
     "01-empty-desktop.png", "لا يُرسل السؤالُ الأول قبل إنشاء مشروعٍ ثم محادثةٍ مسمّاة"),
    ("disabled-modes-unexplained", "medium", "no", 'id="research-option"', "empty_state", "01-empty-desktop.png",
     "أنواعُ محادثاتٍ معطّلة بلا تفسيرٍ لسببها ولا لطريق تفعيلها"),
    ("mobile-composer-below-fold", "medium", "no", "@media(max-width:780px)", "mobile_empty", "07-mobile-empty.png",
     "على الجوّال لا يظهر حقلُ السؤال في الشاشة الأولى: الشريطُ الجانبيّ كلُّه قبله"),
    ("no-keyboard-send", "medium", "yes", '<textarea id="message"', "keyboard_send", "02-text-answer-desktop.png",
     "لا اختصارَ لوحة مفاتيح للإرسال: Enter وCtrl+Enter لا يرسلان"),
    ("answers-plain-text", "medium", "yes", 'element("div", turn.content ||', "ask_text", "02-text-answer-desktop.png",
     "الجوابُ نصٌّ خام: علاماتُ Markdown تظهر حرفيًّا"),
    ("technical-details-in-stream", "medium", "yes", "${result.name} · ${result.status}", "agent_write",
     "04-agent-steps-desktop.png",
     "تفاصيلُ تقنية أمام المستخدم: أسماءُ أدواتٍ وحالاتٌ بالإنجليزية في المجرى وزرّ الموافقة، ومدخلاتُ JSON وبصمات"),
    ("answer-not-announced", "medium", "yes", '<section id="messages"', "ask_text", "02-text-answer-desktop.png",
     "الجوابُ الجديد لا يُعلَن لقارئ الشاشة: منطقةُ الرسائل ليست حيّة، والإعلانُ عددُ جولاتٍ لا الجواب"),
    ("unnamed-controls", "high", "yes", "const field = element(\"textarea\")", "memory_dialog", "09-memory-desktop.png",
     "عنصرُ إدخالٍ بلا اسمٍ وصوليّ في شجرة Chromium"),
    ("horizontal-scroll-mobile", "high", "recheck", "@media(max-width:780px)", "mobile_empty", "07-mobile-empty.png",
     "تمريرٌ أفقيّ على عرض 390"),
    ("hash-label-in-ltr-line", "medium", "yes", "بصمة نسخة المدخلات: ${action.input_snapshot_sha256}", "agent_approval",
     "05-approval-desktop.png",
     "بصمةٌ وعنوانُها العربيّ في سطرٍ واحد اتجاهُه LTR: البصمةُ التي تبدأ برقمٍ ينفصل رقمُها الأول إلى طرف العنوان"),
    ("composer-covers-answer", "low", "recheck", "#composer{position:sticky", "ask_text", "02-text-answer-desktop.png",
     "حقلُ الكتابة الملتصق بأسفل الشاشة يغطّي أكثرَ من ثلثها على 1366×768 فيُقصّ الجوابُ خلفه"),
    ("checkbox-full-width", "low", "no", "input,select{width:100%}", "create_agent_session", "06-raw-error-desktop.png",
     "مربّعُ «اطلب تفكير النموذج» بعرض السطر كلِّه فينفصل عن نصّه"),
    ("dialog-focus-lost", "medium", "yes", 'body.replaceChildren(element("h2", title))', "draft_review_apply",
     "03-draft-review-desktop.png", "استبدالُ محتوى نافذةٍ مفتوحة يُسقط التركيزَ إلى body: مستخدمُ لوحة المفاتيح يفقد موضعه"),
    ("focus-not-visible", "medium", "recheck", ":focus-visible", "tab_order", None,
     "عنصرٌ يتلقّى التركيز بلوحة المفاتيح بلا حلقةٍ مرئية"),
    ("console-errors", "medium", "recheck", "boot().catch(showError)", "open", None,
     "أخطاءٌ في سجلّ وحدة المتصفّح أثناء الرحلة (غيرُ سطور الشبكة لردود الرفض المقصودة)"),
)
SEVERITY_BY_IMPACT = {"critical": "high", "serious": "high", "moderate": "medium", "minor": "low"}


def _observed(rule_id: str, checks: dict, coverage: dict) -> bool:
    if rule_id == "raw-error-codes":
        return bool(checks.get("raw_error_codes_visible")) or bool(coverage.get("raw_codes"))
    if rule_id == "first-message-needs-project-and-session":
        return checks.get("send_before_session_refused") is True
    if rule_id == "disabled-modes-unexplained":
        return bool(checks.get("disabled_session_modes")) and checks.get("disabled_modes_explained") is not True
    if rule_id == "mobile-composer-below-fold":
        return checks.get("composer_in_first_screen_mobile") is False
    if rule_id == "no-keyboard-send":
        return checks.get("ctrl_enter_sends") is False and checks.get("enter_sends") is False
    if rule_id == "answers-plain-text":
        return checks.get("answer_markdown_literal") is True
    if rule_id == "technical-details-in-stream":
        return bool(checks.get("agent_stream_latin") or checks.get("agent_stream_hashes")
                    or checks.get("agent_stream_json_blocks") or checks.get("approval_dialog_json_blocks")
                    or re.search(r"[A-Za-z_]{3,}", checks.get("approval_button_label") or ""))
    if rule_id == "answer-not-announced":
        return checks.get("messages_live") is False and checks.get("answer_announced") is False
    if rule_id == "unnamed-controls":
        return bool(checks.get("unnamed_controls") or checks.get("mobile_unnamed_controls"))
    if rule_id == "horizontal-scroll-mobile":
        return bool(checks.get("horizontal_scroll_390") or checks.get("horizontal_overflow_390_after_answer_px"))
    if rule_id == "hash-label-in-ltr-line":
        return any(item.get("arabic_label") and item.get("direction") == "ltr" for item in checks.get("hashes") or ())
    if rule_id == "composer-covers-answer":
        return (checks.get("composer_position") == "sticky"
                and (checks.get("composer_viewport_share") or 0) > 1 / 3)
    if rule_id == "checkbox-full-width":
        return (checks.get("thinking_checkbox_width_px") or 0) > 100
    if rule_id == "dialog-focus-lost":
        return any(value is False for value in (checks.get("dialog_focus") or {}).values())
    if rule_id == "focus-not-visible":
        return any(item.get("focus_visible") is False for item in checks.get("tab_order") or ())
    if rule_id == "console-errors":
        return bool(checks.get("console_errors") or checks.get("mobile_console_errors"))
    raise KeyError(rule_id)


def derive_findings(checks: dict, coverage: dict, axe: dict | None, sources: dict[str, str],
                    tracked: dict[str, str] | None = None) -> list[dict]:
    """نتائجُ الفحص من ملاحظات المتصفّح والتغطية الثابتة وaxe؛ كلٌّ بشدّته وموضعه وبقائه في الصفحة الموحّدة (#166)."""
    tracked = tracked or {}
    findings = []
    for rule_id, severity, survives, anchor, step, shot, summary in FINDING_RULES:
        if not _observed(rule_id, checks, coverage):
            continue
        findings.append({"id": rule_id, "severity": severity, "summary": summary, "step": step,
                         "screenshot": shot, "points_to": locate(anchor, sources),
                         "survives_single_page": survives, "tracked_in": tracked.get(rule_id)})
    for violation in (axe or {}).get("violations", ()):
        rule_id = f"a11y-{violation['id']}"
        findings.append({"id": rule_id, "severity": SEVERITY_BY_IMPACT.get(violation.get("impact"), "low"),
                         "summary": f"axe: {violation['id']} ({violation.get('impact')}) على {violation.get('nodes', 0)} عنصرًا",
                         "step": "axe", "screenshot": None, "points_to": None, "survives_single_page": "recheck",
                         "targets": violation.get("targets") or [], "tracked_in": tracked.get(rule_id)})
    return findings


def _strings(value, path="$"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key, f"{path}.<key>")
            yield from _strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{path}[{index}]")


def evidence_guard(evidence: dict, shots_dir: Path | None = None) -> list[str]:
    """خروقُ الدليل قبل كتابته: مسارٌ مطلق أو بريد، أو نصٌّ أطول من `MAX_TEXT_CHARS` (نصُّ صفحةٍ لا تسميةٌ قصيرة)،
    أو لقطاتٌ فوق العدد أو الحجم أو بأسماءٍ غيرِ نسبية. فراغُ القائمة وحده يسمح بالكتابة."""
    violations = []
    for where, text in _strings(evidence):
        if ABSOLUTE_PATH.search(text):
            violations.append(f"absolute_path:{where}")
        if EMAIL.search(text):
            violations.append(f"email:{where}")
        if len(text) > MAX_TEXT_CHARS:
            violations.append(f"text_too_long:{where}")
    shots = evidence.get("screenshots") or []
    if len(shots) > MAX_SHOTS:
        violations.append(f"too_many_screenshots:{len(shots)}")
    for shot in shots:
        name = shot.get("file", "")
        if not SHOT_NAME.fullmatch(name):
            violations.append(f"screenshot_name:{name[:40]}")
            continue
        size = shot.get("bytes")
        if shots_dir is not None:
            path = shots_dir / name
            size = path.stat().st_size if path.is_file() else None
            if size is None:
                violations.append(f"screenshot_missing:{name}")
                continue
        if not isinstance(size, int) or size > MAX_SHOT_BYTES:
            violations.append(f"screenshot_too_large:{name}")
    return violations


def axe_problem(axe: dict | None, requested: bool, journeys: tuple[str, ...] | list[str] = JOURNEYS) -> str | None:
    """طُلب axe فلم يعمل في كل حالةٍ فُحصت: رمزُه المسمّى (`axe_unavailable` أو `axe_failed`)؛ أو عمل في بعض الرحلات دون
    بعض فبقيت رحلةٌ مطلوبة بلا حالةٍ فُحصت (`axe_journey_unaudited`)، كرحلة الصفحة الموحّدة وهي هدفُ القبول. فلا يُكتب
    دليلٌ يبدو نظيفًا وهو لم يُفحص. وبلا طلبٍ لا حكم: الدليلُ يقول `not_run` صراحةً."""
    if not requested:
        return None
    axe = axe or {}
    if axe.get("status") != "run" or axe.get("errors") or not axe.get("states"):
        return axe.get("code") or "axe_failed"
    audited = {state.get("journey") for state in axe["states"]}
    if any(journey not in audited for journey in journeys):
        return "axe_journey_unaudited"
    return None


def summarize_steps(journeys: dict) -> dict:
    """عددُ الخطوات الناجحة والساقطة لكل رحلة، وأولُ ساقطةٍ باسمها."""
    out = {}
    for name, journey in journeys.items():
        steps = journey.get("steps") or []
        failed = [step["id"] for step in steps if step.get("ok") is not True]
        out[name] = {"steps": len(steps), "passed": len(steps) - len(failed), "failed": len(failed),
                     "first_failed": failed[0] if failed else None}
    return out


def content_digests(paths, root: Path = ROOT) -> dict[str, str]:
    """بصمةُ محتوى كلِّ ملفٍّ يتعلّق به الناتج (sha256، أولُ ١٢ حرفًا): يبقى الدليلُ قابلًا للتحقّق من الملفّات نفسِها وإن زالت
    المراجعةُ المسجَّلة من التاريخ."""
    return {path: hashlib.sha256((root / path).read_bytes()).hexdigest()[:12] for path in paths}


def inputs_committed(paths, root: Path = ROOT) -> bool | None:
    """هل الملفّاتُ كما هي في HEAD (لا تعديلَ ولا ملفَّ غيرَ متتبَّع)؟ فالمراجعةُ المسجَّلة تحمل ما أنتج الدليلَ فعلًا.
    None إن تعذّر git."""
    try:
        result = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", *paths],
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() == ""


def build_evidence(raw: dict, *, commit: str, date: str, coverage: dict, sources: dict[str, str],
                   tracked: dict[str, str] | None = None, shots_rel: str = "",
                   digests: dict[str, str] | None = None, committed: bool | None = None) -> dict:
    """الدليلُ من ناتج المتصفّح الخام: بلا نصّ صفحةٍ غيرِ التسميات القصيرة، وكلُّ رقمٍ بحدوده."""
    journeys = raw.get("journeys") or {}
    checks = (journeys.get("current") or {}).get("checks") or {}
    axe = raw.get("axe")
    return {
        "schema_version": 1,
        "tool": TOOL,
        "agent": AGENT,
        "date": date,
        "commit": commit,
        "commit_contains_inputs": committed,
        "content_sha256_12": digests or {},
        "browser": raw.get("browser"),
        "viewports": raw.get("viewports"),
        "provider": {"kind": "scripted_fake", "model_time": "zero", "quality": "not_measured"},
        "summary": summarize_steps(journeys),
        "journeys": journeys,
        "timings_ms": {"model_time": "zero_by_scripted_provider",
                       **{name: journey.get("timings_ms") for name, journey in journeys.items()}},
        "error_code_coverage": coverage,
        "axe": axe,
        "findings": derive_findings(checks, coverage, axe, sources, tracked),
        "screenshots_dir": shots_rel,
        "screenshots": raw.get("screenshots") or [],
        "measurement_limits": MEASUREMENT_LIMITS,
    }


# — المتصفّح —

def browser_prerequisites(node: str | None = None, *, which=shutil.which, run=subprocess.run):
    """(رمزُ التعذّر أو None، مسارُ node، بيئةٌ يجد فيها `require('playwright')`)."""
    node = node or which("node")
    if not node:
        return "node_missing", None, None
    env = dict(os.environ)
    npm = which("npm")
    if npm:
        try:
            root = run([npm, "root", "-g"], capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            root = ""
        if root:
            env["NODE_PATH"] = os.pathsep.join(p for p in (env.get("NODE_PATH", ""), root) if p)
    try:
        probe = run([node, "-e", "require.resolve('playwright')"], env=env, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "node_missing", None, None
    if probe.returncode != 0:
        return "playwright_missing", node, env
    return None, node, env


def run_browser(node: str, env: dict, config: dict, workdir: Path) -> tuple[int, dict | None, str]:
    config_path, raw_path = workdir / "config.json", workdir / "raw.json"
    config_path.write_text(json.dumps({**config, "raw_path": str(raw_path)}, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run([node, str(SCRIPT), str(config_path)], env=env, capture_output=True, text=True,
                            timeout=900)
    raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.is_file() else None
    return result.returncode, raw, (result.stderr or result.stdout)[-600:]


def previous_shots(out: Path) -> set[str]:
    """أسماءُ اللقطات التي أعلنها الدليلُ السابق نفسُه، لا ما يطابق نمطًا في المجلّد: فلا يُحذف ملفٌّ لم تنتجه الأداة."""
    try:
        listed = json.loads(out.read_text(encoding="utf-8")).get("screenshots") or []
    except (OSError, ValueError, AttributeError):
        return set()
    return {item["file"] for item in listed
            if isinstance(item, dict) and isinstance(item.get("file"), str) and SHOT_NAME.fullmatch(item["file"])}


def _replace(target: Path, data: bytes) -> None:
    staged = target.with_name(f".{target.name}.{os.getpid()}.part")
    staged.write_bytes(data)
    os.replace(staged, target)


def publish(evidence: dict, staging: Path, shots: Path, out: Path) -> None:
    """بعد أن يمرّ الدليلُ حارسَه وحكمَ axe: تُنقل لقطاتُ هذا التشغيل من مجلّدها المؤقّت بأسمائها (استبدالٌ ذرّيٌّ لكل ملف)،
    ثم الدليل، ثم تُحذف لقطاتُ الدليل السابق التي لم يعد يعلنها، بأسمائها منه. فتشغيلٌ ساقطٌ لا يمسّ شيئًا، ولا يُحذف ملفٌّ
    في المجلّد لم يعلنه دليلٌ سابق."""
    before = previous_shots(out)
    fresh = [shot["file"] for shot in evidence["screenshots"]]
    shots.mkdir(parents=True, exist_ok=True)
    for name in fresh:
        _replace(shots / name, (staging / name).read_bytes())
    out.parent.mkdir(parents=True, exist_ok=True)
    _replace(out, (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    for name in sorted(before - set(fresh)):
        (shots / name).unlink(missing_ok=True)


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    today = _dt.date.today().strftime("%Y%m%d")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--journey", choices=("all", *JOURNEYS), default="all")
    parser.add_argument("--out", default=f"docs/probe/ui-browser-audit-{today}.json")
    parser.add_argument("--shots", default=f"docs/probe/ui-browser-audit-{today}")
    parser.add_argument("--axe", help="مسارُ axe.min.js (axe-core) لفحص الوصول؛ بدونه يُسجَّل «لم يُشغَّل»")
    parser.add_argument("--node", help="مسارُ node إن لم يكن في PATH")
    parser.add_argument("--tracked", action="append", default=[], metavar="FINDING=ISSUE",
                        help="يربط نتيجةً بمسألتها في الدليل (مثل raw-error-codes=170)")
    args = parser.parse_args(argv)
    tracked = {}
    for item in args.tracked:
        finding, sep, issue = item.partition("=")
        if not sep or not issue.isdigit():
            parser.error(f"--tracked بصيغة FINDING=ISSUE: {item}")
        tracked[finding] = f"#{issue}"
    if args.axe and not Path(args.axe).is_file():
        print(json.dumps({"status": "unavailable", "code": "axe_unavailable"}))
        return 3
    code, node, env = browser_prerequisites(args.node)
    if code:
        print(json.dumps({"status": "unavailable", "code": code}))
        return 3
    out, shots = (ROOT / args.out), (ROOT / args.shots)
    journeys = list(JOURNEYS) if args.journey == "all" else [args.journey]
    with tempfile.TemporaryDirectory(prefix="diwan-ui-audit-") as tmp:
        tmp = Path(tmp).resolve()
        staging = tmp / "shots"          # اللقطاتُ هنا حتى يمرّ الدليل؛ ولا يُمسّ مجلّدُ --shots قبل ذلك
        staging.mkdir()
        with ExitStack() as stack:
            # لكل رحلةٍ وعرضٍ خادمٌ بجذرٍ فارغ: كلُّ رحلةٍ مستخدمٌ أوّل لا يرث ما أنشأته غيرُها
            urls = {f"{journey}-{viewport}": stack.enter_context(serving(tmp / f"{journey}-{viewport}"))[0]
                    for journey in journeys for viewport in VIEWPORTS}
            config = {"urls": urls, "journeys": journeys, "shots_dir": str(staging),
                      "axe_path": str(Path(args.axe).resolve()) if args.axe else None,
                      "texts": {"question": QUESTION, "answer_marker": ANSWER_MARKER, "write_request": WRITE_REQUEST,
                                "memory_request": MEMORY_REQUEST, "memory_text": MEMORY_TEXT,
                                "agent_done": AGENT_DONE, "memory_done": MEMORY_DONE}}
            status, raw, tail = run_browser(node, env, config, tmp)
        return _validate_and_publish(args, raw, status, tail, journeys, tracked, staging, shots, out)


def _validate_and_publish(args, raw, status, tail, journeys, tracked, staging: Path, shots: Path, out: Path) -> int:
    if raw is None or status not in (0, 3):
        print(json.dumps({"status": "failed", "code": "browser_run_failed", "exit": status, "tail": tail},
                         ensure_ascii=False))
        return 1
    if status == 3:
        print(json.dumps({"status": "unavailable", "code": raw.get("code", "chromium_missing")}))
        return 3
    problem = axe_problem(raw.get("axe"), bool(args.axe), journeys)
    if problem:
        print(json.dumps({"status": "failed", "code": problem, "errors": (raw.get("axe") or {}).get("errors", [])[:3]},
                         ensure_ascii=False))
        return 1
    sources = {path: (ROOT / path).read_text(encoding="utf-8") for path in UI_SOURCES}
    coverage = error_code_coverage(sources["webui/server.py"], sources["webui/static/app.js"])
    shots_rel = shots.relative_to(ROOT).as_posix() if shots.is_relative_to(ROOT) else shots.name
    evidence = build_evidence(raw, commit=_git_commit(), date=_dt.date.today().isoformat(), coverage=coverage,
                              sources=sources, tracked=tracked, shots_rel=shots_rel,
                              digests=content_digests(DIGESTED), committed=inputs_committed(DIGESTED))
    for shot in evidence["screenshots"]:
        path = staging / shot["file"]
        shot["bytes"] = path.stat().st_size if path.is_file() else None
    violations = evidence_guard(evidence, staging)
    if violations:
        print(json.dumps({"status": "failed", "code": "evidence_guard", "violations": violations[:20]},
                         ensure_ascii=False))
        return 1
    publish(evidence, staging, shots, out)
    print(json.dumps({"status": "written", "evidence": args.out, "summary": evidence["summary"],
                      "findings": [f["id"] for f in evidence["findings"]]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
