"""تدقيقُ ميزانية السياق (ECC ٥، ق٦٧-٦): كم رمزًا يكلّف ما يُقرأ قبل أول كلمةٍ من المستخدم — مقيسًا بمرمِّزٍ مسمًّى لا مقدَّرًا.

ميزانيتان تُقاسان معًا لأنهما تختلفان قارئًا:
- **مجموعةُ قراءة §٠** (`AGENTS.md` و`docs/INDEX.md` و`docs/VISION.md` كما يسمّيها `tools/context_index.py`): ما يقرؤه العميلُ
  في بداية كل جلسة. مرمِّزاتُ Claude وCodex وGemini ليست عامّة، فيُقاس بمرمِّز العامل المحلّي المسجَّل (`openai/gpt-oss-20b`)
  وبمرمِّز المحرّك المعتمَد؛ والتقديرُ القائم في الفهرس (الأحرفُ على ٢٫٤، القاسمُ المعايَر بقياس ٢٧ سبتمبر) يُقابَل بالمقيس فيُعرف انحرافُه.
- **سابقةُ التشغيل**: ما يبلغ المحرّكَ (qwen3.5:9b عبر Ollama) قبل رسالة المستخدم في كل جولة — نصُّ النظام الوكيل والنصّي
  وأنماطُه، ومخطّطاتُ الأدوات كما يسلسلها `providers/ollama_codec.py`، وغلافُ المدخل الوكيل — ونصيبُها من نافذة السياق
  المضبوطة في المزوّد (`CONTEXT_TOKENS`).

القاعدة: لا رقمَ بلا مرمِّز. بلا `--tokenizer` أو `--tokenizer-file` يُرفض التشغيل برمزٍ مسمًّى، وغيابُ حزمة `tokenizers` أو
تعذّرُ تحميل المرمِّز رفضٌ مسمًّى لا سقوطٌ إلى التقدير. والدالّةُ `audit()` تقبل عدّاداتٍ محقونة فتُختبر بلا الحزمة وبلا شبكة.

    python tools/context_budget.py --tokenizer qwen3.5-9b=Qwen/Qwen3.5-9B@c202236235762e1c871ad0ccb60c8ee5ba337b9a \\
        --tokenizer gpt-oss-20b=openai/gpt-oss-20b@6cee5e81ee83917806bbde320786a8fb61efebee \\
        --report docs/probe/context-budget-<التاريخ>.json                                     # repo@<بصمةُ إيداعٍ كاملة>
    python tools/context_budget.py --tokenizer-file qwen3.5-9b=/path/tokenizer.json --report out.json   # بلا شبكة

الخرجُ JSON: لكل نصٍّ بايتاتُه وأحرفُه وتقديرُ الفهرس ورموزُه بكل مرمِّز، ونسبةُ التقدير إلى المقيس، ومجاميعُ المجموعتين،
ونصيبُ سابقة التشغيل من النافذة، وضريبةُ الرمز العربي على عيّنةٍ ثابتة، ونتائجُ مسمّاة، وحدودُ القياس.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import context_index  # noqa: E402  (مجموعةُ قراءة §٠ وتقديرُها من مصدرٍ واحد)

SCHEMA_VERSION = 1
TOOL = "tools/context_budget.py"
Counter = Callable[[str], int]

# عيّنةٌ ثابتة لضريبة الرمز العربي: الفقرةُ نفسُها بالعربية وبالإنجليزية، فالفرقُ فرقُ المرمِّز لا فرقُ المعنى
ARABIC_SAMPLE = (
    "ديوان مساعدٌ ذكيٌّ عام محوره العربية: يحادث، ويكتب، ويبرمج، ويبحث، ويحلّل، ويعمل على الملفات، وينفّذ المهام بالأدوات، "
    "ويتذكّر. والعربية محور جودة فهمه وتعبيره واستدلاله. القاعدة الثابتة: ديوان عام، والسياسات عقدة."
)
ENGLISH_SAMPLE = (
    "Diwan is a general intelligent assistant centred on Arabic: it converses, writes, programs, searches, analyses, works on "
    "files, carries out tasks with tools, and remembers. Arabic is the axis of the quality of its understanding, expression and "
    "reasoning. The fixed rule: Diwan is general, and policies are a node."
)

# عتباتُ النتائج المسمّاة؛ أرقامٌ معلَنة لا مخفيّة
ESTIMATE_DRIFT = 0.20          # انحرافُ تقدير الفهرس عن المقيس الذي يستحق نتيجةً مسمّاة
PREFIX_SHARE_WARNING = 0.10    # نصيبُ سابقة التشغيل من النافذة الذي يستحق نتيجةً مسمّاة

LIMITS = [
    "the_tokenizers_named_are_public_stand_ins_for_the_agents_that_read_section_0_since_claude_codex_and_gemini_tokenizers_are_not_public",
    "the_runtime_prefix_counts_fixed_texts_only_the_memory_block_attachments_quarantine_findings_and_history_vary_per_turn_and_are_not_counted",
    "tool_registries_are_built_by_the_web_app_itself_per_named_configuration_of_execution_search_analysis_and_mode_the_headline_is_the_minimal_one_ci_and_the_sandbox_see",
    "reading_set_entries_carry_sha256_12_so_a_report_can_be_tied_to_the_exact_texts_it_measured",
    "tool_schemas_are_counted_as_the_json_the_provider_serializes_not_as_the_engine_s_own_chat_template_renders_them",
    "special_tokens_and_chat_template_framing_are_excluded_so_measured_totals_are_lower_bounds_of_what_the_engine_sees",
    "the_arabic_token_tax_is_one_fixed_paragraph_pair_not_a_corpus_statistic",
    "the_context_window_is_the_provider_constant_context_tokens_not_a_measurement_of_the_served_model",
    "the_hub_tokenizer_named_for_the_engine_is_a_proxy_the_ollama_tag_qwen3_5_9b_is_a_service_name_not_an_artifact_digest_so_the_pinned_hub_revision_is_not_bound_to_the_served_model_s_embedded_tokenizer",
]


class Refused(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


def _text_entry(text: str, counters: dict[str, Counter]) -> dict:
    """بايتاتُ نصٍّ وأحرفُه وتقديرُ الفهرس ورموزُه بكل مرمِّز مع الأحرف لكل رمز ونسبة التقدير إلى المقيس."""
    estimate = context_index.tokens_estimate(text)
    tokens = {name: int(count(text)) for name, count in counters.items()}
    return {"bytes": len(text.encode("utf-8")), "chars": len(text), "tokens_estimate": estimate, "tokens": tokens,
            "chars_per_token": {name: (round(len(text) / n, 2) if n else None) for name, n in tokens.items()},
            "estimate_ratio": {name: (round(estimate / n, 3) if n else None) for name, n in tokens.items()}}


def _sum(entries: dict[str, dict], counters: dict[str, Counter]) -> dict:
    return {"bytes": sum(e["bytes"] for e in entries.values()),
            "tokens_estimate": sum(e["tokens_estimate"] for e in entries.values()),
            "tokens": {name: sum(e["tokens"][name] for e in entries.values()) for name in counters}}


def reading_set(root: Path, counters: dict[str, Counter]) -> dict[str, dict]:
    """مجموعةُ قراءة §٠ كما يسمّيها الفهرس، بنصّها على القرص عند التشغيل. ملفٌّ غائب منها رفضٌ مسمًّى لا تخطٍّ صامت: مجموعٌ
    جزئيّ يُنشر بصفة `reading_set_totals` قياسٌ كاذب (ملاحظة Codex على #157)."""
    missing = [path for path in context_index.READING_SET if not (root / path).is_file()]
    if missing:
        raise Refused("reading_set_incomplete", "مجموعةُ القراءة ناقصة فلا يُنشر مجموعٌ جزئيّ: " + ", ".join(missing))
    found = {}
    for path in context_index.READING_SET:
        text = (root / path).read_text(encoding="utf-8")
        found[path] = {**_text_entry(text, counters), "sha256_12": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]}
    return found


CONFIGURATIONS = (
    # (الاسم، التنفيذُ مفعّل، البحثُ مضبوط، التحليلُ مفعّل، النمط) — كلُّ تهيئةٍ يبنيها التطبيقُ نفسُه (`LocalApp.mode_registry`)
    ("agent_minimal", False, False, False, "agent"),
    ("agent_execution", True, False, False, "agent"),
    ("agent_search", False, True, False, "agent"),
    ("agent_analysis", False, False, True, "agent"),
    ("agent_full", True, True, True, "agent"),
    ("coder", False, False, False, "coder"),
    ("coder_execution", True, False, False, "coder"),
    ("research", False, True, False, "research"),
    ("translate", False, False, False, "translate"),
)
HEADLINE = "agent_minimal"      # التهيئةُ التي يراها CI والصندوق (بلا Docker ولا محرّك بحث)؛ الأثقلُ agent_full


def _registries() -> dict[str, tuple]:
    """مخطّطاتُ الأدوات كما يبنيها التطبيقُ نفسُه لكل تهيئة: `LocalApp` حقيقيّ في مجلّدٍ مؤقّت، ومشروعٌ يُنشأ بواجهته، ثم
    `mode_registry` بعد ضبط أعلام الخُلفيّات (التنفيذُ والتحليل) في سجلّ المشروع ومحرّكِ البحث في التطبيق — لا نسخةٌ من
    قائمة الأدوات تُكتب هنا (ملاحظة Codex على #157)."""
    import tempfile
    from webui.server import LocalApp

    def provider():
        raise RuntimeError("لا يُنادى مزوّدٌ في القياس")

    found: dict[str, tuple] = {}
    with tempfile.TemporaryDirectory(prefix="diwan-context-budget-") as tmp:
        apps = {searched: LocalApp(Path(tmp) / ("s" if searched else "n"), model="context-budget", model_version="0" * 64,
                                   provider_factory=provider, agent_provider_factory=provider,
                                   web_search=object() if searched else None)
                for searched in (False, True)}
        try:
            for name, execution, searched, analysis, mode in CONFIGURATIONS:
                app = apps[searched]
                project = app.project(app.dispatch({"action": "create_project", "name": f"budget-{name}"})["id"])
                app.agent_workspace(project)
                app.agent_backends[project.name].update(execution_enabled=execution, analysis_enabled=analysis)
                found[name] = app.mode_registry(project, mode).specs()
        finally:
            # كلُّ تطبيقٍ يمسك واصفَ جذرٍ وقفلًا؛ بلا إغلاقٍ يزيد كلُّ قياسٍ في عمليةٍ طويلة أربعةَ واصفات (ملاحظة Codex على #157)
            for app in apps.values():
                app.close()
    return found


def runtime_texts() -> dict:
    """النصوصُ الثابتة التي تسبق رسالةَ المستخدم على الطريقين، من وحدات المنتج نفسِها لا من نسخٍ مكتوبة هنا: نصوصُ النظام
    بأنماطها، ومخطّطاتُ الأدوات لكل تهيئةٍ كما يبنيها التطبيق، وغلافُ المدخل."""
    from agent.coder import CODER_SYSTEM
    from agent.loop import SYSTEM as AGENT_SYSTEM
    from agent.research import RESEARCH_SYSTEM
    from agent.translation import TRANSLATE_SYSTEM
    from conversation.session import SYSTEM as TEXT_SYSTEM
    from providers.ollama_codec import serialize_tools
    from services.agent_workspace import encode_input

    systems = {"agent": AGENT_SYSTEM, "coder": CODER_SYSTEM, "research": RESEARCH_SYSTEM, "translate": TRANSLATE_SYSTEM}
    configurations = {}
    for name, specs in _registries().items():
        mode = next(c[4] for c in CONFIGURATIONS if c[0] == name)
        serialized = serialize_tools(specs)
        configurations[name] = {"mode": mode, "system": systems[mode], "tools": json.dumps(serialized, ensure_ascii=False),
                                "tool_names": [spec.name for spec in specs],
                                "each_tool": {spec.name: json.dumps(one, ensure_ascii=False) for spec, one in zip(specs, serialized, strict=True)}}
    return {"configurations": configurations, "envelope": encode_input("", [], None), "text_system": TEXT_SYSTEM}


def context_window() -> int:
    from providers.ollama import CONTEXT_TOKENS
    return int(CONTEXT_TOKENS)


def _findings(report: dict) -> list[dict]:
    """نتائجُ مسمّاة تُشتقّ من الأرقام لا تُكتب بيدٍ؛ كلٌّ برمزٍ وقيمته وعتبته."""
    found = []
    agents = report["reading_set"].get(context_index.AGENTS)
    if agents and agents["bytes"] > context_index.CODEX_PROJECT_DOC_MAX_BYTES:
        found.append({"code": "agents_md_exceeds_codex_cap", "bytes": agents["bytes"],
                      "cap": context_index.CODEX_PROJECT_DOC_MAX_BYTES})
    totals = report["reading_set_totals"]
    for name, measured in totals["tokens"].items():
        if measured and abs(totals["tokens_estimate"] / measured - 1) > ESTIMATE_DRIFT:
            found.append({"code": "index_estimate_drifts_from_measured", "tokenizer": name,
                          "estimate": totals["tokens_estimate"], "measured": measured,
                          "ratio": round(totals["tokens_estimate"] / measured, 3), "threshold": ESTIMATE_DRIFT})
    for configuration, entry in report["runtime_prefix"]["configurations"].items():
        for name, share in entry["share_of_context_window"].items():
            if share > PREFIX_SHARE_WARNING:
                found.append({"code": "prefix_share_of_window_high", "configuration": configuration, "tokenizer": name,
                              "share": share, "threshold": PREFIX_SHARE_WARNING})
    tools = report["runtime_prefix"]["configurations"][HEADLINE]["each_tool"]
    for name in report["tokenizers"]:
        costliest = max(tools, key=lambda t: tools[t]["tokens"][name]) if tools else None
        if costliest:
            found.append({"code": "costliest_tool_schema", "configuration": HEADLINE, "tokenizer": name, "tool": costliest,
                          "tokens": tools[costliest]["tokens"][name],
                          "share_of_tools": round(tools[costliest]["tokens"][name]
                                                  / max(1, report["runtime_prefix"]["configurations"][HEADLINE]["tools"]["tokens"][name]), 3)})
    return found


def audit(root: Path, counters: dict[str, Counter], tokenizer_sources: dict[str, dict] | None = None,
          window: int | None = None) -> dict:
    """التقريرُ كاملًا بعدّاداتٍ محقونة (اسمٌ ← دالّةٌ تعدّ رموزَ نصّ). بلا عدّادٍ يُرفض: لا رقمَ بلا مرمِّز."""
    if not counters:
        raise Refused("no_tokenizer_named", "لا عدّادَ رموزٍ مسمًّى؛ التقديرُ وحده لا يُنشر رقمًا")
    if Path(root).resolve() != ROOT:
        # نصوصُ التشغيل (نصوصُ النظام ومخطّطاتُ الأدوات والغلاف) تُستورد من شجرة الأداة نفسِها، فجذرٌ آخر ينسب أرقامَ تشغيلٍ
        # حديثة إلى إيداعٍ غيرِ إيداعها؛ الأداةُ تقيس النسخةَ التي تعمل منها وحدها (ملاحظة Codex على #157)
        raise Refused("root_is_not_this_checkout", f"الأداةُ تقيس النسخةَ التي تعمل منها ({ROOT})، لا {root}")
    window = context_window() if window is None else window
    texts = runtime_texts()
    reading = reading_set(root, counters)
    envelope = _text_entry(texts["envelope"], counters)
    configurations = {}
    for name, entry in texts["configurations"].items():
        system, tools = _text_entry(entry["system"], counters), _text_entry(entry["tools"], counters)
        total = {tok: system["tokens"][tok] + tools["tokens"][tok] + envelope["tokens"][tok] for tok in counters}
        configurations[name] = {"mode": entry["mode"], "tool_names": entry["tool_names"], "system": system, "tools": tools,
                                "each_tool": {tool: _text_entry(text, counters) for tool, text in entry["each_tool"].items()},
                                "total_tokens": total,
                                "share_of_context_window": {tok: round(n / window, 4) for tok, n in total.items()}}
    report = {
        "schema_version": SCHEMA_VERSION, "tool": TOOL,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "commit": _commit(root), "tree_state": tree_state(root),
        "tokenizers": tokenizer_sources or {name: {"source": "injected"} for name in counters},
        "context_window_tokens": window,
        "reading_set": reading, "reading_set_totals": _sum(reading, counters),
        "runtime_prefix": {"headline": HEADLINE, "envelope": envelope, "configurations": configurations,
                           "text": {"system": _text_entry(texts["text_system"], counters)}},
        "arabic_token_tax": {name: _token_tax(count) for name, count in counters.items()},
        "thresholds": {"estimate_drift": ESTIMATE_DRIFT, "prefix_share_warning": PREFIX_SHARE_WARNING},
        "measurement_limits": list(LIMITS),
    }
    report["findings"] = _findings(report)
    return report


def _token_tax(count: Counter) -> dict:
    arabic, english = count(ARABIC_SAMPLE), count(ENGLISH_SAMPLE)
    return {"arabic_tokens": arabic, "english_tokens": english,
            "arabic_chars_per_token": round(len(ARABIC_SAMPLE) / arabic, 2) if arabic else None,
            "english_chars_per_token": round(len(ENGLISH_SAMPLE) / english, 2) if english else None,
            "tokens_ratio_arabic_to_english": round(arabic / english, 2) if english else None}


def _commit(root: Path) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _hub_tokenizer_file(name: str, source: str) -> tuple[str, dict]:
    """ملفُّ tokenizer.json من الـHub ببصمة إيداعٍ كاملة `repo@<40 حرفًا ستّ‌عشريًّا>` وحدها: الفرعُ الافتراضيّ يتحرّك، وكذا فرعٌ
    مسمًّى مثل `main` ووسمٌ يُعاد وضعُه، فيعطي أمرُ إعادة القياس نفسُه مرمِّزًا آخر؛ وبصمةُ الملف في التقرير تكشف الاختلافَ بعد وقوعه
    ولا تجعل الأمرَ المسجَّل قابلًا للإعادة (ملاحظتا Codex على #157)."""
    repo, at, revision = source.partition("@")
    if not at or not repo or not revision:
        raise Refused("tokenizer_revision_unpinned", f"{name}: {source}: سمِّ المستودع ببصمة إيداعٍ كاملة repo@<commit>")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise Refused("tokenizer_revision_unpinned",
                      f"{name}: {source}: المراجعةُ بصمةُ إيداعٍ كاملة (٤٠ حرفًا ستّ‌عشريًّا) لا فرعٌ ولا وسمٌ يتحرّكان")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        raise Refused("huggingface_hub_unavailable",
                      "حزمةُ huggingface_hub غيرُ مركَّبة؛ يُنزَّل tokenizer.json بها أو يُعطى بـ--tokenizer-file") from None
    try:
        path = hf_hub_download(repo, "tokenizer.json", revision=revision)
    except Exception as exc:                                           # noqa: BLE001 -- أيُّ تعذّرٍ في التنزيل يُسمّى برمزه ولا يُبتلع
        raise Refused("tokenizer_unavailable", f"{name}: {source}: {type(exc).__name__}: {exc}"[:400]) from None
    return str(path), {"source": repo, "revision": revision, "loaded_from": "hub"}


def load_tokenizers(named: list[str], files: list[str]) -> tuple[dict[str, Counter], dict[str, dict]]:
    """يحمّل المرمِّزات المسمّاة (`اسم=repo@revision` من الـHub أو `اسم=مسارُ tokenizer.json`) بحزمة `tokenizers`؛ كلُّ تعذّرٍ رفضٌ
    مسمًّى، وكلُّ مرمِّزٍ يُسجَّل ببصمة ملفه المحمَّل أيًّا كان مصدرُه."""
    try:
        from tokenizers import Tokenizer
    except ImportError:
        raise Refused("tokenizers_unavailable", "حزمةُ tokenizers غيرُ مركَّبة؛ لا يُقاس بلا مرمِّز") from None
    counters: dict[str, Counter] = {}
    sources: dict[str, dict] = {}
    for spec, from_file in [(s, False) for s in named] + [(s, True) for s in files]:
        name, sep, source = spec.partition("=")
        if not sep or not name or not source:
            raise Refused("tokenizer_spec_invalid", f"الصيغةُ اسم=مصدر: {spec!r}")
        if name in counters:
            raise Refused("tokenizer_spec_invalid", f"الاسمُ مكرَّر: {name!r}")
        path, origin = (source, {"source": source, "loaded_from": "file"}) if from_file else _hub_tokenizer_file(name, source)
        try:
            tokenizer = Tokenizer.from_file(path)
        except Exception as exc:                                       # noqa: BLE001 -- أيُّ تعذّرٍ في التحميل يُسمّى برمزه ولا يُبتلع
            raise Refused("tokenizer_unavailable", f"{name}: {source}: {type(exc).__name__}: {exc}"[:400]) from None
        counters[name] = lambda text, _t=tokenizer: len(_t.encode(text, add_special_tokens=False).ids)
        sources[name] = {**origin, "file_sha256_12": hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]}
    return counters, sources


def tree_state(root: Path) -> dict:
    """حالةُ شجرة العمل: نصوصُ التشغيل تُستورد من الشجرة لا من HEAD، فتعديلٌ غيرُ مودَع يُنسب إلى إيداعٍ ليس إيداعَه
    (ملاحظة Codex على #157). الملفّاتُ غيرُ المتتبَّعة لا تُعدّ لأنها لا تُستورد إلا بالاسم؛ وتعذُّرُ git حالةٌ مجهولة لا نظيفة."""
    try:
        out = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"dirty": None, "changed_paths": [], "error": type(exc).__name__}
    paths = sorted(line[3:] for line in out.splitlines() if line.strip())
    return {"dirty": bool(paths), "changed_paths": paths}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tokenizer", action="append", default=[], metavar="NAME=HF_REPO@REVISION")
    parser.add_argument("--tokenizer-file", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.tokenizer and not args.tokenizer_file:
            raise Refused("no_tokenizer_named", "سمِّ مرمِّزًا بـ--tokenizer أو --tokenizer-file؛ لا رقمَ بلا مرمِّز")
        counters, sources = load_tokenizers(args.tokenizer, args.tokenizer_file)
        state = tree_state(ROOT)
        if state["dirty"] is not False:
            # تقريرٌ يُنشر باسم إيداعٍ يقيس شجرةً غيرَ شجرته كذبٌ مسمًّى؛ الشجرةُ المتّسخة أو المجهولة تُرفض (ملاحظة Codex على #157)
            raise Refused("worktree_dirty" if state["dirty"] else "worktree_state_unknown",
                          "الشجرةُ فيها تعديلٌ غيرُ مودَع: " + ", ".join(state["changed_paths"][:8]) if state["dirty"]
                          else "تعذّر git status فلا تُعرف حالةُ الشجرة")
        report = audit(ROOT, counters, sources)
    except Refused as exc:
        print(json.dumps({"status": "refused", "code": exc.code, "detail": exc.detail}, ensure_ascii=False))
        return 2
    text = json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    summary = {"status": "measured", "tokenizers": sorted(counters), "reading_set_tokens": report["reading_set_totals"]["tokens"],
               "prefix_tokens": {name: c["total_tokens"] for name, c in report["runtime_prefix"]["configurations"].items()},
               "findings": [f["code"] for f in report["findings"]], "report": str(args.report) if args.report else None}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
