#!/usr/bin/env python3
"""تشغيل المراجعة الخارجية لبنك قياس عبر Ollama أو الواجهات المجانية المتوافقة مع OpenAI (ق٥٠، ق٦٠، #168).

    python3 tools/external_review.py evaluation/banks/kimi_v1
    python3 tools/external_review.py evaluation/banks/kimi_v1 \\
        --reviewer deepseek-v4.1-flash:cloud --reviewer mistral-large-3:675b-cloud
    OLLAMA_API_KEY=… python3 tools/external_review.py --base-url https://ollama.com --smoke out.json
    GITHUB_TOKEN=… python3 tools/external_review.py --backend github-models --list-catalog catalog.json
    GITHUB_TOKEN=… python3 tools/external_review.py --backend github-models --smoke out.json
    HF_TOKEN=… python3 tools/external_review.py --backend hf-router --max-usd 0.50 --smoke out.json --every-family
    GROQ_API_KEY=… DIWAN_GROQ_FREE_TIER_CONFIRMED=confirmed python3 tools/external_review.py --backend groq --smoke out.json
    OPENROUTER_API_KEY=… python3 tools/external_review.py --backend openrouter --smoke out.json

**Ollama** (`--backend ollama`، الافتراضيّ): نقطتان لا ثالثَ لهما: خادمُ Ollama المحليّ (127.0.0.1، بلا وكيلٍ ولا
تحويل) يمرّر النداء إلى النماذج السحابية كما على الماك؛ أو واجهةُ `https://ollama.com` مباشرةً بمفتاحٍ يُقرأ من
البيئة `OLLAMA_API_KEY` وحدها — وهي طريقُ الجلسات السحابية التي لا خادمَ فيها.

**الواجهاتُ المجانية** (قرار المالك في ٢٨ سبتمبر ٢٠٢٦ (#168)، وموافقةُ Groq/OpenRouter المجانية في PR #197):
`--backend github-models` (`models.github.ai` برمز `GITHUB_TOKEN`؛ في Actions رمزُ المهمّة نفسُه بصلاحية
`models: read`، بلا سرٍّ جديد) و`--backend hf-router` (`router.huggingface.co` برمز `HF_TOKEN`). والمفتاحُ من
البيئة وحدها: لا خيارَ له في سطر الأوامر، ولا يُطبع، ولا يُكتب، ولا يُرسل عند التحويل (التحويلُ مرفوض).
- `--backend groq` يرفض **قبل الشبكة** ما لم تكن `DIWAN_GROQ_FREE_TIER_CONFIRMED=confirmed` بعد تحقق المشغّل من
  طبقة الحساب المجانية. لا يدّعي النقلُ كلفة صفر إن لم يبلّغها الرد.
- `--backend openrouter` لا يرسل إلا معرّفًا ينتهي بـ`:free` موجودًا في فهرس اللحظة وكلُّ بنود `pricing` فيه صفر؛
  ويطلب إبلاغ الاستخدام والكلفة ويمنع fallback المزوّد. غياب الكلفة أو ظهور غير الصفر إخفاقٌ مسمّى.
- **قائمةُ نقاطٍ صريحة** (`ALLOWED_HOSTS`): ما سواها `endpoint_not_allowed`.
- **العائلةُ من الناشر بجدولٍ صريح** (`PUBLISHER_FAMILIES`) وتطابقُ ما يقوله الاسم؛ والناشرُ المجهول
  `publisher_unknown`. المفضَّلون DeepSeek وMistral وMeta Llama وCohere وAI21 وMicrosoft Phi؛ ولا Qwen (المحرّك)
  ولا Moonshot/Kimi (المؤلّف) ولا OpenAI وGoogle وAnthropic (المطوّرون)؛ ولا مراجعان تتقاطع سلالتاهما.
- **نفادُ الحصّة** (`quota_exhausted`، HTTP 429) يُستبدل فيه بالمراجع مرشّحٌ من عائلةٍ أخرى مسموحة، وإلا
  فالإخفاقُ المسمّى `quota_exhausted_no_fallback`.
- **طلبُ الدفع** (`payment_required`، HTTP 402) حالةُ فوترةٍ في الحساب لا حدُّ طلبات: يُوقف التشغيلَ كلَّه باسمه
  بلا بديل، كإخفاقات الإنفاق الأخرى (ق٧١-٢ وق٧١-٥).
- **الردُّ الفارغ أو المبتور** (`reply_empty`، `reply_incomplete`) يُعاد مرّةً ثم يُسجَّل برمزه (ق٥٠).
- المحجوبُ لا يُرسل، والبنكُ على هذه الواجهات من `evaluation/banks/` وحدها (لا ملفّاتُ المالك).

ولا تطبع الأداةُ محتوى الحالات أبدًا: الخلاصةُ أعدادٌ وκ وطولُ قائمة المالك، والتقاريرُ تحمل الواجهةَ ومضيفَ
النقطة ومعرّفَ النموذج وعائلتَه لا المفتاح.

**الحدُّ المعلَن:** هويةُ النموذج هي معرّفُه في فهرس المزوّد لا بصمةُ أوزانٍ مُتحقَّقٌ منها، والعائلةُ مستنتجةٌ من
الناشر والاسم لا يشهد بها المزوّد؛ والطبقاتُ المجانية تحدّ عددَ الطلبات وحجمَ المدخل (فملفُّ بنكٍ كبير قد يُردّ
`request_too_large`).
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.external_review import (AUTHOR_FAMILY, BUDGET_TERMINAL_ERRORS, DEFAULT_REVIEWERS,  # noqa: E402
                                        DEVELOPER_FAMILIES, ENGINE_FAMILY, LEDGER_FILE, SUPERSEDED_DIR,
                                        _slug, open_files, review_bank, smoke, summarize)
from evaluation.multi_system_review import AutomaticReviewError, model_family  # noqa: E402
from core.budget import Budget, BudgetRefused  # noqa: E402
from core.canonical import SAFE_INT  # noqa: E402
from core.locality import is_cloud_model  # noqa: E402

MAX_RESPONSE_BYTES = 8_000_000
LOCAL_PREFIXES = ("http://127.0.0.1:", "http://localhost:", "http://[::1]:")
CLOUD_ENDPOINT = "https://ollama.com"          # النقطةُ السحابية الوحيدة المسموحة (ق٦٠)
CLOUD_KEY_ENV = "OLLAMA_API_KEY"               # يُقرأ من البيئة وحدها، ولا يُطبع ولا يُودَع
USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


def ollama_usage(body: object) -> dict | None:
    """عدّادا Ollama (`prompt_eval_count` و`eval_count`) بأسماء سجلّ النداءات المشترك، أو None إن لم يُبلغ أيًّا منهما.

    عددٌ صحيحٌ غيرُ سالب لا يتجاوز `SAFE_INT` وحده يُقبل (لا منطقيّ ولا نصّ ولا ما يُقرَّب في قارئ JSON)، كحدّ مفكّك Ollama
    في المزوّد (`providers/ollama_codec.py`)؛ والمجموعُ لا يُكتب إلا من العدّادين كليهما وفي الحدّ نفسِه (جديد-spend-ledger)."""
    if not isinstance(body, dict):
        return None
    usage = {}
    for source, field in (("prompt_eval_count", "prompt_tokens"), ("eval_count", "completion_tokens")):
        value = body.get(source)
        if type(value) is int and 0 <= value <= SAFE_INT:
            usage[field] = value
    if not usage:
        return None
    if len(usage) == 2 and usage["prompt_tokens"] + usage["completion_tokens"] <= SAFE_INT:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return usage


def token_totals(rows: list[dict]) -> dict:
    """مجموعُ التوكنات لكل نموذج من سجلّ النداءات، ومعه عددُ ما أُرسل باستهلاكٍ ناقص فلا يُقرأ المجموعُ كاملًا وهو ناقص.

    والناقصُ ما غاب عنه أيُّ حقلٍ من الثلاثة، ومنه ما لم يُبلَغ استهلاكُه أصلًا وما أُبلغ عدّادٌ واحدٌ منه (ملاحظة Codex على #298).
    صفوفُ الفهرس لا نموذجَ لها ولا استهلاك فلا تدخل، وما لم يُرسل لا يُعدّ (جديد-spend-ledger)."""
    totals: dict[str, dict] = {}
    for row in rows:
        if row.get("kind") == "catalog" or not row.get("request_sent"):
            continue
        entry = totals.setdefault(row["model"], {"calls": 0, "calls_with_incomplete_usage": 0,
                                                 **{field: 0 for field in USAGE_FIELDS}})
        entry["calls"] += 1
        usage = row.get("usage") or {}
        if any(field not in usage for field in USAGE_FIELDS):
            entry["calls_with_incomplete_usage"] += 1
        for field in USAGE_FIELDS:
            entry[field] += usage.get(field, 0)
    # مجموعٌ فوق SAFE_INT يُقرَّب في قارئ JSON ولو كان كلُّ نداءٍ في الحدّ: فلا يُكتب رقمًا بل يُعلَن اسمُه (ملاحظة Codex على #298)
    for entry in totals.values():
        beyond = [field for field in USAGE_FIELDS if entry[field] > SAFE_INT]
        for field in beyond:
            entry[field] = None
        if beyond:
            entry["fields_beyond_safe_integer"] = beyond
    return dict(sorted(totals.items()))


def prior_provider_usage(bank: Path) -> list[dict]:
    """سجلُّ النداءات الذي كتبته تشغيلاتٌ سابقة في خلاصة البنك، ليُضاف إليه لا ليُستبدل (ملاحظة Codex على #298).

    إعادةُ التشغيل على بنكٍ مراجَع تتخطّى سجلّاته فلا يُرسل نداءٌ؛ وكتابةُ سجلّ هذا التشغيل وحده كانت تمحو أدلّةَ الإنفاق
    للمراجعات التي ما زالت الخلاصةُ تمثّلها. وخلاصةٌ مفقودةٌ أو بلا سجلٍّ قائمةٌ فارغة، لا خطأ.

    والمصدرُ الأول `reviews/PROVIDER_USAGE.json` الذي يُكتب عند كلِّ خروجٍ ومنه الرفض؛ فتشغيلٌ رُفض بعد أن أرسل لا تضيع
    نداءاتُه حين تتخطّى الإعادةُ سجلّاتِه (ملاحظة Codex على #298). والخلاصةُ مصدرٌ ثانٍ لبنكٍ لم يُكتب له الملفّ."""
    ledger = _read_json_object(bank / "reviews" / LEDGER_FILE)
    rows = ledger.get("provider_usage") if "provider_usage" in ledger else _prior_summary(bank).get("provider_usage")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def write_ledger(bank: Path, rows: list[dict], evidence: dict, spend_caps: list[dict] | None = None) -> None:
    """السجلُّ الدائم كاملًا (السابقُ وما أُلحق به)، ومعه دليلُ المجانية الذي تقوم عليه نداءاتُه: فتشغيلٌ سقط بعد الإرسال
    وقبل الخلاصة لا تعود نداءاتُه في الإعادة بلا بنود السعر ولحظة قراءتها (ملاحظة Codex على #298). ولا يُكتب في تشغيلٍ
    بمعرّف: مجلّدُه جديدٌ لا يُعاد، وخلاصتُه وRUN.json يحملان نداءاتِه، وما يُرفع منه عقدُه ثابت (`check_artifact`)."""
    _write_json(bank / "reviews" / LEDGER_FILE, {"schema_version": 1, "provider_usage": rows,
                                                 "zero_spend_evidence": dict(sorted(evidence.items())),
                                                 **({"spend_caps": spend_caps} if spend_caps else {})})


def merged_zero_spend_evidence(prior: dict, transport) -> dict:
    """دليلُ المجانية المحفوظ، وفوقه قراءةُ هذا التشغيل لكل نموذجٍ أرسل إليه نداءً أو لا دليلَ سابقًا له. فإعادةٌ تتخطّى ما
    رُوجع تقرأ الفهرسَ ولا ترسل، فلا تستبدل بقراءتها الجديدة الدليلَ الذي قامت عليه نداءاتٌ سابقة (اختبارٌ كان يسقط حين تعبر
    القراءتان حدَّ ثانية)."""
    sent = {row.get("model") for row in getattr(transport, "provider_usage", [])
            if row.get("kind") != "catalog" and row.get("request_sent")}
    fresh = {model: entry for model, entry in getattr(transport, "zero_spend_evidence", {}).items()
             if model in sent or model not in prior}
    return dict(sorted({**prior, **fresh}.items()))


def prior_spend_caps(bank: Path) -> list[dict]:
    """سقوفُ التشغيلات المدفوعة السابقة وإنفاقُ كلٍّ منها، من السجلّ الدائم (ملاحظة Codex على #308)."""
    caps = _read_json_object(bank / "reviews" / LEDGER_FILE).get("spend_caps")
    return [cap for cap in caps if isinstance(cap, dict)] if isinstance(caps, list) else []


def _run_spend_cap(transport) -> list[dict]:
    report = _spend_report(transport).get("spend_cap")
    return [{key: report[key] for key in ("cap_usd", "spent_usd", "exceeded_reservation_usd") if key in report}] \
        if isinstance(report, dict) else []


def prior_zero_spend_evidence(bank: Path) -> dict:
    """دليلُ المجانية الذي حفظته تشغيلاتٌ سابقة لكل نموذج، فلا تمحوه إعادةٌ لم تقرأ الفهرسَ لذلك النموذج (ملاحظة Codex على #298).
    من السجلّ الدائم أولًا كسجلّ النداءات، ثم من الخلاصة لبنكٍ لم يُكتب له السجلّ بهذا الحقل."""
    ledger = _read_json_object(bank / "reviews" / LEDGER_FILE)
    evidence = ledger["zero_spend_evidence"] if "zero_spend_evidence" in ledger \
        else _prior_summary(bank).get("zero_spend_evidence")
    if not isinstance(evidence, dict):
        return {}
    return {model: entry for model, entry in evidence.items() if isinstance(model, str) and isinstance(entry, dict)}


def _prior_summary(bank: Path) -> dict:
    return _read_json_object(bank / "reviews" / "SUMMARY.json")


def _read_json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def cost_unconfirmed_attempts(rows: list[dict]) -> int:
    """نداءاتُ OpenRouter التي أُرسلت ولم تثبت كلفتُها (#285)؛ تُعدّ من السجلّ الذي تُعطاه، تشغيلًا كان أو البنكَ كلَّه."""
    return sum(1 for row in rows if row.get("kind") != "catalog" and row.get("provider") == "openrouter"
               and row.get("request_sent") and row.get("cost_status") != "reported")


class OllamaChat:
    """نداءُ /api/chat على خادمٍ محليّ (بلا وكيلٍ ولا تحويل)، أو على ollama.com بمفتاحٍ من البيئة.

    المفتاحُ لا يُحفظ إلا في ترويسة الطلب، ولا يظهر في أي خطأٍ أو تقرير: الأخطاءُ رموزٌ باسم النموذج.
    وكلُّ محاولةٍ صفٌّ في `provider_usage` بشكل سجلّ الواجهات المجانية نفسِه، فيه توكناتُ الردّ (جديد-spend-ledger).
    وOllama لا يُبلغ كلفةً في ردّه، والنموذجُ السحابيُّ يُحاسَب باشتراك الحساب؛ فالكلفةُ `not_reported` لا صفرٌ مفترض.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: int = 900,
                 api_key: str | None = None):
        base = base_url.rstrip("/")
        self._headers = {"Content-Type": "application/json"}
        if base_url.startswith(LOCAL_PREFIXES):
            self.cloud = False
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        elif base == CLOUD_ENDPOINT:
            if not api_key:
                raise AutomaticReviewError("cloud_key_missing", f"{CLOUD_KEY_ENV} غيرُ مضبوطٍ في البيئة")
            self.cloud = True
            self.opener = urllib.request.build_opener()      # يمرّ بوكيل البيئة إن وُجد
            self._headers["Authorization"] = f"Bearer {api_key}"
        else:
            raise AutomaticReviewError("local_endpoint_required", base_url)
        self.url = base + "/api/chat"
        self.timeout = timeout
        self.provider_usage: list[dict] = []     # صفٌّ لكل محاولة: لا رسالةَ ولا مفتاحَ ولا نصَّ ردّ

    def __call__(self, model: str, system: str, user: str, schema: dict) -> str:
        started, sent, usage = time.monotonic(), False, None
        try:
            payload = {"model": model, "stream": False, "format": schema,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}],
                       "options": {"temperature": 0, "seed": 0, "num_ctx": 65536}}
            request = urllib.request.Request(
                self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=dict(self._headers))
            sent = True
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
            except urllib.error.HTTPError as exc:
                raise AutomaticReviewError(f"http_{exc.code}", model) from exc
            except TimeoutError as exc:
                raise AutomaticReviewError("transport_timeout", model) from exc
            except (urllib.error.URLError, OSError) as exc:
                raise AutomaticReviewError("transport_error", model) from exc
            except http.client.HTTPException as exc:      # ردٌّ مبتور (IncompleteRead) لا يرث OSError (ملاحظة Codex على #298)
                raise AutomaticReviewError("transport_error", model) from exc
            if len(raw) > MAX_RESPONSE_BYTES:
                raise AutomaticReviewError("response_too_large", model)
            try:
                body = json.loads(raw)
                usage = ollama_usage(body)
                content = body["message"]["content"]
            except (ValueError, KeyError, TypeError) as exc:
                raise AutomaticReviewError("ollama_response_malformed", model) from exc
            if not isinstance(content, str):
                raise AutomaticReviewError("ollama_response_malformed", model)
        except AutomaticReviewError as exc:
            self._record(model, started, "error", sent, usage, exc.code)
            raise
        self._record(model, started, "succeeded", sent, usage, None)
        return content

    def _record(self, model: str, started: float, status: str, sent: bool, usage: dict | None,
                error: str | None) -> None:
        self.provider_usage.append({
            "provider": "ollama", "model": model, "family": model_family(model),
            "cloud": self.cloud or is_cloud_model(model),
            "at": _utc_now(), "elapsed_ms": round((time.monotonic() - started) * 1000),
            "status": status, "error": error, "request_sent": sent,
            "usage": usage, "cost_usd": None, "cost_status": "not_reported", "zero_spend_proof": None,
        })

    def spend_report(self) -> dict:
        """مجموعُ التوكنات لكل نموذج؛ ولا دليلَ مجانيةٍ هنا، فالاشتراكُ لا يُقرأ من الردّ."""
        return {"token_totals": token_totals(self.provider_usage)}


def build_transport(base_url: str, environ=os.environ) -> OllamaChat:
    """النقلُ من النقطة المطلوبة؛ والمفتاحُ من البيئة وحدها (لا خيارَ له في سطر الأوامر)."""
    return OllamaChat(base_url, api_key=environ.get(CLOUD_KEY_ENV) or None)


# ————— الواجهاتُ المجانية المتوافقة مع OpenAI (#168، وموافقة Groq/OpenRouter الموثقة في #197) —————

# قائمةُ النقاط الصريحة: https وحدها، بلا منفذٍ ولا هوية. الإضافة لا تعني التفعيل: المفاتيح والحراس شروطٌ مستقلة.
ALLOWED_HOSTS = ("ollama.com", "models.github.ai", "router.huggingface.co",
                 "api.groq.com", "openrouter.ai")

BACKENDS: dict[str, dict] = {
    "github-models": {
        "chat_url": "https://models.github.ai/inference/chat/completions",
        "catalog_url": "https://models.github.ai/catalog/models",
        "key_env": "GITHUB_TOKEN",
        "headers": {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        # أوّلُ ما يُجرَّب من كل عائلة إن كان في الفهرس؛ وإلا فما في الفهرس بترتيبه (بعد النماذج الاستدلالية والصغيرة)
        "preferred": {"deepseek": ("deepseek/DeepSeek-V3-0324",),
                      "mistral": ("mistral-ai/mistral-medium-2505", "mistral-ai/Mistral-Large-2411"),
                      "meta": ("meta/Llama-4-Maverick-17B-128E-Instruct-FP8", "meta/Llama-3.3-70B-Instruct"),
                      "cohere": ("cohere/cohere-command-a",),
                      "ai21": ("ai21-labs/AI21-Jamba-1.5-Large",),
                      "microsoft": ("microsoft/Phi-4",)},
    },
    "hf-router": {
        "chat_url": "https://router.huggingface.co/v1/chat/completions",
        "catalog_url": "https://router.huggingface.co/v1/models",
        "key_env": "HF_TOKEN",
        "headers": {},
        "preferred": {"deepseek": ("deepseek-ai/DeepSeek-V3-0324", "deepseek-ai/DeepSeek-V3.1"),
                      "mistral": ("mistralai/Mistral-Small-3.1-24B-Instruct-2503",),
                      "meta": ("meta-llama/Llama-3.3-70B-Instruct",),
                      "cohere": ("CohereLabs/c4ai-command-a-03-2025",),
                      "ai21": (),
                      "microsoft": ("microsoft/phi-4",)},
    },
    "groq": {
        "chat_url": "https://api.groq.com/openai/v1/chat/completions",
        "catalog_url": "https://api.groq.com/openai/v1/models",
        "key_env": "GROQ_API_KEY",
        "headers": {},
        # لا يمكن استنتاج طبقة الحساب من فهرس النماذج؛ لذلك يرفض النقل قبل الشبكة ما لم يؤكد المشغّل الطبقة المجانية.
        "free_tier_env": "DIWAN_GROQ_FREE_TIER_CONFIRMED",
        "preferred": {"deepseek": (), "mistral": ("mistral-saba-24b",),
                      "meta": ("llama-3.3-70b-versatile",), "cohere": (), "ai21": (), "microsoft": ()},
    },
    "openrouter": {
        "chat_url": "https://openrouter.ai/api/v1/chat/completions",
        "catalog_url": "https://openrouter.ai/api/v1/models",
        "key_env": "OPENROUTER_API_KEY",
        "headers": {},
        # لا تكفي اللاحقة وحدها: يثبت الفهرسُ أن كل بنود pricing صفر قبل أول نداء نموذج.
        "catalog_zero_spend": True,
        "preferred": {"deepseek": ("deepseek/deepseek-r1:free",),
                      "mistral": ("mistralai/mistral-small-3.1-24b-instruct:free",),
                      "meta": ("meta-llama/llama-3.3-70b-instruct:free",),
                      "cohere": (), "ai21": (), "microsoft": ()},
    },
}

# الناشرُ (ما قبل «/» في معرّف النموذج) ← العائلة. الممنوعون مسمَّون صراحةً فيكون رفضُهم بقاعدة العائلة لا بالجهالة.
PUBLISHER_FAMILIES = {
    "deepseek": "deepseek", "deepseek-ai": "deepseek",
    "mistral-ai": "mistral", "mistralai": "mistral",
    "meta": "meta", "meta-llama": "meta",
    "cohere": "cohere", "coherelabs": "cohere", "cohereforai": "cohere",
    "ai21-labs": "ai21", "ai21labs": "ai21",
    "microsoft": "microsoft",
    "qwen": "qwen",
    "moonshotai": "kimi", "moonshot": "kimi",
    "openai": "openai", "azure-openai": "openai",
    "google": "google",
    "anthropic": "anthropic",
}
PREFERRED_FAMILIES = ("deepseek", "mistral", "meta", "cohere", "ai21", "microsoft")
# مقاطعُ في الاسم تؤخّر النموذجَ داخل عائلته: الاستدلاليّ يسبق JSON بنصٍّ طويل، والصغيرُ والمرئيُّ ليسا مراجعَين
_DEMOTED_TOKENS = {"r1", "reasoning", "thinking", "think", "mini", "tiny", "3b", "8b", "vision", "embed"}

CATALOG_LIMITS = ("catalog_is_one_listing_at_one_moment_not_a_guarantee_of_availability",)
FREE_LIMITS = (
    "model_identity_is_the_provider_catalog_id_not_a_verified_weight_digest",
    "family_is_derived_from_publisher_and_name_not_attested_by_the_provider",
    "free_tier_rate_limits_and_input_caps_apply",
)

ZERO_SPEND_LIMITS = (
    "zero_spend_guard_is_provider_specific_and_not_a_general_price_attestation",
    "provider_usage_is_reported_when_available_and_missing_cost_is_never_assumed_zero",
)
# سقفُ السعر داخل طلب OpenRouter نفسِه (#285): `provider.max_price` بوحدات OpenRouter (دولار لكل مليون رمزٍ للمدخل والمخرج،
# ودولار لكل طلبٍ وصورة)، وكلُّه صفر، فيُلزَم المزوّدَ بألّا يوجّه إلى نقطةٍ مدفوعة إن تغيّر السعرُ بعد لقطة الفهرس.
# ليس بديلًا عن فحص `usage.cost` بعد النداء: لم يُجرَّب حيًّا، فالفحصُ اللاحق يبقى الحارسَ المُلزم (الحدُّ أدناه).
OPENROUTER_MAX_PRICE = {"prompt": 0, "completion": 0, "request": 0, "image": 0}
OPENROUTER_LIMITS = ZERO_SPEND_LIMITS + (
    "the_openrouter_request_price_cap_max_price_zero_was_not_exercised_live_a_price_that_rises_between_the_catalog"
    "_snapshot_and_the_call_is_still_detected_only_after_the_call_from_usage_cost",
)
# سقفُ الموجّه المدفوع حدٌّ على الحجز بسعر الفهرس، لا ضمانٌ لما يفوتره المزوّد (ملاحظة Codex على #308)
HF_ROUTER_LIMITS = (
    "the_cap_bounds_reservations_at_the_catalog_price_read_at_run_time_a_charge_reported_above_its_reservation"
    "_is_already_billed_so_it_is_named_and_stops_the_run_and_the_hard_ceiling_is_the_prepaid_hfd2_credit",
)


def free_limits(base, backend: str | None = None) -> list[str]:
    """حدودُ القياس على الواجهات المجانية من موضعٍ واحد، للتجربة وللبنك وللخلاصة المحفوظة (ملاحظة Codex على #174)."""
    extra = (OPENROUTER_LIMITS if backend == "openrouter" else ZERO_SPEND_LIMITS if backend == "groq"
             else HF_ROUTER_LIMITS if backend == "hf-router" else ())
    return sorted(set(base) | set(FREE_LIMITS) | set(extra))


def _forbidden() -> dict[str, str]:
    codes = {family: "reviewer_is_developer_family" for family in DEVELOPER_FAMILIES}
    codes[AUTHOR_FAMILY] = "reviewer_is_author_family"
    codes[ENGINE_FAMILY] = "reviewer_is_engine_family"
    return codes


def allowed_endpoint(url: str) -> str:
    """https إلى مضيفٍ في القائمة بالضبط، بلا منفذٍ ولا هويةٍ في الرابط؛ وإلا `endpoint_not_allowed`."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:                       # رابطٌ لا يُحلَّل (IPv6 مبتورٌ ونحوه)
        raise AutomaticReviewError("endpoint_not_allowed", url) from None
    # netloc هو المضيفُ وحده: فلا منفذَ (ولو غيرَ عدديّ) ولا هويةَ قبل @
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.netloc.lower() != parts.hostname:
        raise AutomaticReviewError("endpoint_not_allowed", url)
    return url


def _lineage_tokens(name: str) -> set[str]:
    """العائلاتُ التي يسمّيها أيُّ مقطعٍ في الاسم: «DeepSeek-R1-Distill-Qwen» سلالتُه deepseek وqwen معًا."""
    return {family for token in re.split(r"[^a-z0-9.]+", name.lower())
            if token and (family := model_family(token)) is not None}


def resolve_reviewer(model: str, backend: str | None = None) -> dict:
    """{model, publisher, family, lineage} لمراجعٍ على واجهةٍ مجانية، أو رفضٌ مسمًّى.

    العائلةُ من جدول الناشرين، والاسمُ يجب أن يقول العائلةَ نفسها (الجدولُ الواحد في `multi_system_review`)،
    وكلُّ مقطعٍ في الاسم يدخل السلالة: فالمقطَّرُ من Qwen مرفوضٌ ولو نشرته DeepSeek.
    """
    publisher, sep, name = model.partition("/")
    family = PUBLISHER_FAMILIES.get(publisher.lower()) if sep and name else None
    if family is None and backend == "groq" and not sep:
        # فهرسُ Groq يسمّي بعض النماذج بلا ناشر (مثل llama-* وmistral-*). تُقبل فقط إن اشتقّ الجدولُ الحاكم
        # عائلةً معروفة من الاسم نفسه؛ لا نخمن ناشرًا مجهولًا ولا نعمّم هذا الاستثناء على واجهة أخرى.
        family = model_family(model)
        publisher, name = family or "", model
    if family is None:
        raise AutomaticReviewError("publisher_unknown", model)
    forbidden = _forbidden()
    if family in forbidden:
        raise AutomaticReviewError(forbidden[family], model)
    named = model_family(model)
    if named is None:
        raise AutomaticReviewError("reviewer_family_unknown", model)
    if named != family:
        raise AutomaticReviewError("family_mismatch", model)
    lineage = {family} | _lineage_tokens(name)
    for member in sorted(lineage):
        if member in forbidden:
            raise AutomaticReviewError(forbidden[member], model)
    return {"model": model, "publisher": publisher, "family": family, "lineage": sorted(lineage)}


def check_distinct(identities: list[dict]) -> None:
    """لا مراجعان من عائلةٍ واحدة، ولا تتقاطع سلالتاهما."""
    seen: set[str] = set()
    for identity in identities:
        if seen & set(identity["lineage"]):
            raise AutomaticReviewError("duplicate_reviewer_family", identity["model"])
        seen |= set(identity["lineage"])


def choose_reviewers(candidates: list[dict], want: int = 2, exhausted: list[dict] = ()) -> list[dict]:
    """أوّلُ `want` مرشّحين بالترتيب لا تتقاطع سلالاتُهم، بلا نموذجٍ نفدت حصّتُه ولا عائلتِه.

    بلا نفادٍ ولا عددٍ كافٍ: `reviewers_unavailable`؛ وبعد نفادٍ: `quota_exhausted_no_fallback`.
    """
    spent_models = {item["model"] for item in exhausted}
    spent_families = {family for item in exhausted for family in item["lineage"]}
    chosen: list[dict] = []
    taken: set[str] = set()
    for identity in candidates:
        lineage = set(identity["lineage"])
        if identity["model"] in spent_models or lineage & spent_families or lineage & taken:
            continue
        chosen.append(identity)
        taken |= lineage
        if len(chosen) == want:
            return chosen
    raise AutomaticReviewError("quota_exhausted_no_fallback" if exhausted else "reviewers_unavailable",
                               f"{len(chosen)}/{want}")


def bare_url(url: str) -> str:
    """المخطّطُ والمضيفُ والمسارُ وحدها: لا هويةَ ولا منفذَ ولا استعلامَ ولا جزء — فلا يدخل التقريرَ ما قد يحمل رمزًا."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}{parts.path}"


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """التحويلُ مرفوض: لا يتبع النقلُ مضيفًا لم يُسمَّ، فلا يخرج المفتاحُ إلى غير نقطته."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_code(status: int) -> str:
    """رمزُ حالة HTTP على الواجهات المجانية: الشائعُ مسمًّى، وما سواه http_<الرمز>."""
    if status == 429:
        return "quota_exhausted"          # حدُّ الطلبات: يُستبدل المراجعُ بمرشّحٍ من عائلةٍ أخرى
    if status == 402:
        return "payment_required"         # حالةُ فوترةٍ في الحساب لا حدُّ طلبات: تُوقف التشغيلَ كلَّه باسمها (ق٧١-٢ وق٧١-٥)
    if 300 <= status < 400:
        return "redirected"               # التحويلُ مرفوضٌ ولا يُتبع؛ ووجهتُه (مضيفٌ ومسار) في الشكل
    return {401: "unauthorized", 403: "forbidden", 404: "not_found",
            413: "request_too_large"}.get(status, f"http_{status}")    # 413 حدُّ المدخل في الطبقة المجانية


# رفضُ الخدمة نفسِها لا خطأُ نموذج: إن لم يُجب نموذجٌ واحد وكانت الأخطاءُ كلُّها من هذه فالحالةُ «unavailable» لا «failed».
# قيس في ٢٨ سبتمبر ٢٠٢٦: models.github.ai يردّ 200 و`OK` نصًّا عاديًّا (٤ بايتات) على كل طلب، ومن Actions برمز المهمّة أيضًا.
SERVICE_UNAVAILABLE = frozenset({"response_not_json", "unauthorized", "forbidden"})
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


def unavailable_codes(errors: list) -> list[str] | None:
    """رموزُ رفض الخدمة إن كانت كلُّ الأخطاء منها (ولا نجاحَ بينها)، وإلا None."""
    if errors and all(error in SERVICE_UNAVAILABLE for error in errors):
        return sorted(set(errors))
    return None


def response_shape(status: int | None, raw: bytes, content_type: str | None) -> tuple[object, dict]:
    """(الجسمُ المحلَّل، الشكل): الحالةُ ونوعُ المحتوى والحجمُ والنوعُ الأعلى وأسماءُ المفاتيح، ومن جسم الخطأ `error.code`
    و`error.type` وحدهما إن كانا معرّفين قصيرين — لا رسالةٌ ولا نصُّ نموذجٍ ولا مفتاح."""
    shape: dict = {"status": status, "content_type": content_type, "bytes": len(raw)}
    try:
        body = json.loads(raw)
    except ValueError:
        return None, {**shape, "top": "not_json"}
    shape["top"] = type(body).__name__
    if isinstance(body, dict):
        shape["keys"] = sorted(str(key) for key in body)[:20]
        error = body.get("error")
        if isinstance(error, dict):
            for field in ("code", "type"):
                value = error.get(field)
                if isinstance(value, str) and _SAFE_TOKEN.fullmatch(value):
                    shape[f"error_{field}"] = value
    return body, shape


def _decimal(value) -> Decimal | None:
    """عددٌ عشري محدود وغير سالب، أو None لما لا يصلح دليلَ كلفة."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def openrouter_zero_spend(entry: dict) -> str:
    """دليلُ أن معرّف OpenRouter مجانيّ وكلَّ بنود سعره المنشورة صفر، أو رفضٌ قبل نداء النموذج."""
    model = entry.get("id")
    if not isinstance(model, str) or not model.endswith(":free"):
        raise AutomaticReviewError("free_model_required", str(model))
    pricing = entry.get("pricing")
    # بندا المدخل والمخرج لازمان: سعرٌ غائبٌ غيرُ مُثبَت، فلا يُعدّ صفرًا لأن ما حضر من البنود صفر
    if not isinstance(pricing, dict) or not {"prompt", "completion"} <= pricing.keys():
        raise AutomaticReviewError("free_price_unverified", model)
    values = [_decimal(value) for value in pricing.values()]
    if any(value is None for value in values) or any(value != 0 for value in values):
        raise AutomaticReviewError("free_price_unverified", model)
    return "catalog_free_suffix_and_all_pricing_zero"


def safe_usage(body: object) -> tuple[dict | None, Decimal | None, bool]:
    """(الاستهلاك المأمون، الكلفة، وهل أبلغ المزوّد الكلفة) بلا نصوصٍ حرّة من الرد."""
    if not isinstance(body, dict) or not isinstance(body.get("usage"), dict):
        return None, None, False
    raw = body["usage"]
    usage = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw.get(field)
        if type(value) is int and value >= 0:
            usage[field] = value
    if "cost" not in raw:
        return usage, None, False
    cost = _decimal(raw.get("cost"))
    if cost is None:
        raise AutomaticReviewError("usage_cost_invalid")
    return usage, cost, True


class OpenAICompatChat:
    """نداءُ chat/completions المتوافق مع OpenAI على نقطةٍ مسموحة، بمفتاحٍ من البيئة لا يُكتب ولا يُطبع.

    المفتاحُ يُحمل في ترويسةٍ لا تُعاد عند التحويل (والتحويلُ مرفوضٌ أصلًا)، ولا يدخل خطأً ولا تمثيلًا للكائن.
    """

    def __init__(self, backend: str, api_key: str | None, *, chat_url: str | None = None,
                 catalog_url: str | None = None, timeout: int = 300, max_tokens: int = 4000,
                 free_tier_confirmation: str | None = None):
        if backend not in BACKENDS:
            raise AutomaticReviewError("backend_unknown", backend)
        spec = BACKENDS[backend]
        self.backend = backend
        self.chat_url = allowed_endpoint(chat_url or spec["chat_url"])
        self.catalog_url = allowed_endpoint(catalog_url or spec["catalog_url"])
        self.key_env = spec["key_env"]
        if not api_key:
            raise AutomaticReviewError("key_missing", f"{self.key_env} غيرُ مضبوطٍ في البيئة")
        if spec.get("free_tier_env") and free_tier_confirmation != "confirmed":
            # فهرس Groq لا يثبت طبقة الحساب. الرفض هنا قبل بناء opener أو أي اتصال؛ والقيمة نفسها لا تُطبع ولا تُحفظ.
            raise AutomaticReviewError("free_tier_unverified", spec["free_tier_env"])
        self.__key = api_key
        self._headers = {"Content-Type": "application/json", "User-Agent": "diwan-external-review",
                         **spec["headers"]}
        self.timeout, self.max_tokens = timeout, max_tokens
        self.catalog_shape: dict | None = None
        self.catalog_read_at: str | None = None   # وقتُ آخر قراءةٍ للفهرس: لحظةُ دليل السعر
        self.failures: dict[str, dict] = {}     # آخرُ شكلٍ فاشل لكل نموذجٍ (وللفهرس): لا نصَّ فيه
        self.last_request: dict[str, dict] = {}  # الطريقةُ والرابطُ المرسَل والنهائيّ (bare_url) لكل نموذجٍ وللفهرس
        self.provider_usage: list[dict] = []     # سجلٌّ مأمون لكل محاولة نموذج: لا رسالةَ ولا مفتاح ولا نصَّ رد
        self.zero_spend_proofs: dict[str, str] = {}
        # دليلُ المجانية نفسُه لا عبارتُه (ق٧١-٦، #285): بنودُ السعر كما قُرئت من الفهرس ووقتُ قراءتها، لكل نموذجٍ أُثبت
        self.zero_spend_evidence: dict[str, dict] = {}
        if backend == "groq":
            self.zero_spend_proofs["*"] = "operator_confirmed_account_free_tier"
        self.opener = urllib.request.build_opener(_RefuseRedirect())

    def __repr__(self) -> str:
        return f"OpenAICompatChat({self.backend!r}, host={self.endpoint_host!r})"

    @property
    def endpoint_host(self) -> str:
        return urllib.parse.urlsplit(self.chat_url).hostname

    def describe(self) -> dict:
        return {"name": self.backend, "endpoint_host": self.endpoint_host, "key_env": self.key_env}

    def approve_zero_spend(self, entries: list[dict], models: list[str]) -> None:
        """يُثبت نماذج OpenRouter من فهرس اللحظة؛ وما لم يثبت لا يصل إلى chat/completions."""
        if self.backend != "openrouter":
            return
        by_id = {entry.get("id"): entry for entry in entries if isinstance(entry, dict)}
        observed_at = self.catalog_read_at or _utc_now()
        for model in models:
            if model not in by_id:
                raise AutomaticReviewError("free_price_unverified", model)
            self.zero_spend_proofs[model] = openrouter_zero_spend(by_id[model])
            # البنودُ بأسمائها الآمنة وقيمِها العشرية المطبَّعة وحدها، لا نصٌّ حرٌّ من الفهرس
            self.zero_spend_evidence[model] = {
                "proof": self.zero_spend_proofs[model], "catalog": bare_url(self.catalog_url),
                "observed_at": observed_at, "request_price_cap": dict(OPENROUTER_MAX_PRICE),
                "pricing": {key: str(_decimal(value)) for key, value in sorted(by_id[model]["pricing"].items())
                            if isinstance(key, str) and _SAFE_TOKEN.fullmatch(key)}}

    def _wire_model(self, model: str) -> str:
        """المعرّفُ كما يُرسل في الحمولة: هو نفسُه هنا، ومثبَّتٌ بمزوّده في موجّه HF (`PricedRouterChat`)."""
        return model

    def _guard_zero_spend(self, model: str) -> str | None:
        if self.backend == "groq":
            return self.zero_spend_proofs.get("*")
        if self.backend == "openrouter":
            proof = self.zero_spend_proofs.get(model)
            if proof is None:
                raise AutomaticReviewError("free_price_unverified", model)
            return proof
        return None

    def spend_report(self) -> dict:
        """ما يُضاف إلى كلّ تقريرٍ وخلاصة: دليلُ المجانية المحفوظ، وعددُ ما أُرسل ولم تثبت كلفتُه.

        نداءٌ أُرسل إلى OpenRouter ثم انقطع أو عاد بلا JSON يُعاد ولا يوقف التشغيل، وكلفتُه مجهولة؛ فلا يُبلَّغ التشغيلُ نظيفًا
        وهو فيه (#285): يُعدّ هنا باسمه، والمالكُ يقرأ العددَ بجانب `provider_usage`."""
        return {"zero_spend_evidence": dict(sorted(self.zero_spend_evidence.items())),
                "cost_unconfirmed_attempts": cost_unconfirmed_attempts(self.provider_usage),
                "token_totals": token_totals(self.provider_usage)}

    def _record_provider_usage(self, model: str, started: float, status: str, *,
                               usage: dict | None = None, cost: Decimal | None = None,
                               cost_reported: bool = False, error: str | None = None,
                               proof: str | None = None) -> None:
        family = model_family(model)
        self.provider_usage.append({
            "provider": self.backend, "model": model, "family": family,
            "at": _utc_now(), "elapsed_ms": round((time.monotonic() - started) * 1000),
            "status": status, "error": error, "request_sent": model in self.last_request,
            "usage": usage, "cost_usd": str(cost) if cost_reported and cost is not None else None,
            "cost_status": "reported" if cost_reported else "not_reported",
            "zero_spend_proof": proof,
        })

    def _record_catalog_call(self, started: float, status: str, error: str | None) -> None:
        """نداءُ الفهرس في سجلّ النداءات أيضًا (#285): صفٌّ `kind: catalog` بلا نموذجٍ ولا استهلاك، فيُرى كلُّ ما خرج."""
        self.provider_usage.append({
            "kind": "catalog", "provider": self.backend, "model": None, "family": None,
            "at": _utc_now(), "elapsed_ms": round((time.monotonic() - started) * 1000),
            "status": status, "error": error, "request_sent": True, "usage": None, "cost_usd": None,
            "cost_status": "not_billed_listing", "zero_spend_proof": None})

    def _send(self, url: str, payload: dict | None, label: str) -> tuple[bytes, str | None, int | None]:
        """(البايتات، نوعُ المحتوى، حالةُ HTTP). والخطأُ يحمل رمزَ حالته المسمّى وشكلَ جسمه (`shape`) لا نصَّه."""
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="GET" if data is None else "POST")
        for name, value in self._headers.items():
            request.add_header(name, value)
        request.add_unredirected_header("Authorization", f"Bearer {self.__key}")
        # ما خرج فعلًا وما عاد منه: يُسجَّل لكل نداءٍ (بلا استعلامٍ ولا ترويسة) فيُرى المسارُ لا يُفترض
        where = self.last_request[label] = {"method": request.get_method(), "sent": bare_url(request.full_url),
                                            "final": None}
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                headers = getattr(response, "headers", None)
                content_type = headers.get("Content-Type") if headers is not None else None
                status = getattr(response, "status", None)
                where["final"] = bare_url(response.geturl()) if hasattr(response, "geturl") else None
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(65536)
            except (OSError, http.client.HTTPException):
                body = b""
            where["final"] = bare_url(exc.geturl()) if exc.geturl() else None
            _, shape = response_shape(
                exc.code, body, exc.headers.get("Content-Type") if exc.headers is not None else None)
            self.failures[label] = {**shape, "request": where}
            if 300 <= exc.code < 400 and exc.headers is not None and exc.headers.get("Location"):
                # وجهةُ التحويل: المضيفُ والمسارُ وحدهما (لا استعلامَ قد يحمل رمزًا)؛ ولا يُتبع ولا يُعاد المفتاحُ إليها
                target = urllib.parse.urlsplit(urllib.parse.urljoin(url, exc.headers["Location"]))
                self.failures[label]["redirect_to"] = f"{target.hostname}{target.path}"
            exc.close()
            error = AutomaticReviewError(http_code(exc.code), label)
            error.shape = self.failures[label]
            raise error from None
        except TimeoutError:
            raise AutomaticReviewError("transport_timeout", label) from None
        except urllib.error.URLError as exc:
            code = "transport_timeout" if isinstance(exc.reason, TimeoutError) else "transport_error"
            raise AutomaticReviewError(code, label) from None
        except (OSError, http.client.HTTPException):
            raise AutomaticReviewError("transport_error", label) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AutomaticReviewError("response_too_large", label)
        return raw, content_type, status

    def __call__(self, model: str, system: str, user: str, schema: dict) -> str:
        started, proof = time.monotonic(), None
        usage, cost, cost_reported = None, None, False
        try:
            proof = self._guard_zero_spend(model)
            # المخطّطُ موصوفٌ في التكليف نفسِه («JSON فقط»)، ولا يُرسل response_format لأن نماذجَ في الفهرس تردّه 400.
            payload = {"model": self._wire_model(model), "stream": False, "temperature": 0, "max_tokens": self.max_tokens,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if self.backend == "openrouter":
                # لا نماذجَ بديلة ولا انتقالَ مدفوعًا، وسقفُ السعر صفرٌ في الطلب نفسِه (#285)، واطلب الكلفة الفعلية في
                # الردّ حتى لا تُخمَّن صفرًا.
                payload.update(provider={"allow_fallbacks": False, "max_price": dict(OPENROUTER_MAX_PRICE)},
                               usage={"include": True})
            raw, content_type, status = self._send(self.chat_url, payload, model)
            body, shape = response_shape(status, raw, content_type)
            if shape["top"] == "not_json":
                self.failures[model] = {**shape, "request": self.last_request[model]}
                raise AutomaticReviewError("response_not_json", model)
            usage, cost, cost_reported = safe_usage(body)
            if self.backend in {"groq", "openrouter"} and usage is None:
                self.failures[model] = {**shape, "request": self.last_request[model]}
                raise AutomaticReviewError("usage_unavailable", model)
            if self.backend == "openrouter":
                if not cost_reported:
                    self.failures[model] = {**shape, "request": self.last_request[model]}
                    raise AutomaticReviewError("usage_cost_unavailable", model)
                if cost != 0:
                    self.failures[model] = {**shape, "request": self.last_request[model]}
                    raise AutomaticReviewError("zero_spend_breach", model)
            try:
                choice = body["choices"][0]
                content = choice["message"].get("content")
            except (KeyError, IndexError, TypeError, AttributeError):
                self.failures[model] = {**shape, "request": self.last_request[model]}
                raise AutomaticReviewError("openai_response_malformed", model) from None
            if isinstance(content, list):          # أجزاءُ نصٍّ عند بعض المزوّدين
                # جزءٌ ليس قاموسًا أو نصُّه ليس نصًّا رمزُه response_part_invalid فيُعاد مرّةً ويُسجَّل (ملاحظة Codex على #174)
                if not all(isinstance(part, dict) and isinstance(part.get("text", ""), str) for part in content):
                    self.failures[model] = {**shape, "request": self.last_request[model]}
                    raise AutomaticReviewError("response_part_invalid", model)
                content = "".join(part.get("text", "") for part in content)
            if not isinstance(content, str) or not content.strip():
                self.failures[model] = {**shape, "request": self.last_request[model]}
                raise AutomaticReviewError("reply_empty", model)
            if choice.get("finish_reason") == "length":
                self.failures[model] = {**shape, "request": self.last_request[model]}
                raise AutomaticReviewError("reply_incomplete", model)
        except AutomaticReviewError as exc:
            self._record_provider_usage(model, started, "error", usage=usage, cost=cost,
                                        cost_reported=cost_reported, error=exc.code, proof=proof)
            raise
        self._record_provider_usage(model, started, "succeeded", usage=usage, cost=cost,
                                    cost_reported=cost_reported, proof=proof)
        return content

    def catalog(self) -> list[dict]:
        """الفهرسُ مطبَّعًا: {id, chat, reason, tier}. GitHub يردّ قائمةً في أعلاه، والموجّه {data: [...]}.

        وما سواهما `catalog_malformed` ومعه شكلُه وحده (`catalog_shape`) لا نصُّه: قيس في ٢٨ سبتمبر ٢٠٢٦ أنّ
        models.github.ai يردّ `OK` نصًّا عاديًّا بأربعة بايتات لأيّ مسارٍ بلا تفويض، فكان الرمزُ وحده لا يفرّق بينه وبين غلافٍ مجهول.
        """
        started = time.monotonic()
        try:
            raw, content_type, status = self._send(self.catalog_url, None, "catalog")
        except AutomaticReviewError as exc:
            self._record_catalog_call(started, "error", exc.code)
            raise
        entries, self.catalog_shape = catalog_shape(raw, content_type, status)
        if entries is None:
            # ردُّ 200 بغلافٍ لا يُقرأ نداءٌ فاشل في السجلّ أيضًا، لا ناجحٌ ثم رفض (ملاحظة Codex على #290)
            self._record_catalog_call(started, "error", "catalog_malformed")
            self.failures["catalog"] = {**self.catalog_shape, "request": self.last_request["catalog"]}
            error = AutomaticReviewError("catalog_malformed", self.backend)
            error.shape = self.catalog_shape
            raise error
        self.catalog_read_at = _utc_now()
        self._record_catalog_call(started, "succeeded", None)
        return [_catalog_entry(entry) for entry in entries
                if isinstance(entry, dict) and isinstance(entry.get("id"), str)]


def catalog_shape(raw: bytes, content_type: str | None, status: int | None = None) -> tuple[list | None, dict]:
    """(المدخلات أو None، الشكل): شكلُ الردّ (`response_shape`) ومعه العددُ وأسماءُ حقول المدخلات — لا نصُّ نموذجٍ ولا مفتاح."""
    body, shape = response_shape(status, raw, content_type)
    if isinstance(body, dict):
        body = body.get("data")
    if shape["top"] == "not_json" or not isinstance(body, list):
        return None, shape
    shape["count"] = len(body)
    shape["item_keys"] = sorted({str(key) for item in body[:50] if isinstance(item, dict) for key in item})[:40]
    return body, shape


def _catalog_entry(entry: dict) -> dict:
    outputs = entry.get("supported_output_modalities")
    if outputs is None and isinstance(entry.get("architecture"), dict):
        outputs = entry["architecture"].get("output_modalities")
    reason = None
    if isinstance(outputs, list) and "text" not in outputs:
        reason = "not_a_chat_model"
    elif "embed" in entry["id"].lower():
        reason = "not_a_chat_model"
    elif isinstance(entry.get("providers"), list) and not any(
            isinstance(p, dict) and p.get("status") == "live" for p in entry["providers"]):
        reason = "no_live_provider"
    return {"id": entry["id"], "chat": reason is None, "reason": reason,
            "tier": entry.get("rate_limit_tier"), "pricing": entry.get("pricing"),
            "router_prices": router_prices(entry)}


def router_prices(entry: dict) -> dict[str, dict]:
    """أسعارُ موجّه HF لكل مزوّدٍ حيّ أعلن سعرَيه: {المزوّد: {input, output}} بالدولار لكل مليون توكن، أي ميكرو-دولار
    لكل توكن (`providers[].pricing` في `/v1/models`، `hf://docs/inference-providers/hub-api.md`). وما غاب أحدُ سعرَيه أو
    لم يكن حيًّا أو لم يكن اسمُه آمنًا لا يدخل: السعرُ الغائب لا يُعدّ صفرًا."""
    prices: dict[str, dict] = {}
    for provider in entry.get("providers") or ():
        if not isinstance(provider, dict) or provider.get("status") != "live":
            continue
        name, pricing = provider.get("provider"), provider.get("pricing")
        if not isinstance(name, str) or not _SAFE_TOKEN.fullmatch(name) or not isinstance(pricing, dict):
            continue
        values = {side: _decimal(pricing.get(side)) for side in ("input", "output")}
        if all(value is not None for value in values.values()):
            # نافذةُ السياق التي يعلنها المزوّد (`context_length`)، ليُستبعد ما لا يتّسع للطلب (ملاحظة Codex على #308)
            context = provider.get("context_length")
            prices.setdefault(name, {**values, **({"context_length": context}
                                                  if type(context) is int and context > 0 else {})})
    return prices


# سقفُ HFD2 (ق٦٤، `docs/PLAN-20260926.md` §٧): ‎$20 شحنًا مسبقًا بلا شحنٍ تلقائي. فلا يُعطى تشغيلٌ واحد سقفًا فوقه.
HFD2_CAP_USD = Decimal("20")
MICROS_PER_USD = 1_000_000
# تأطيرُ المحادثة (علاماتُ الأدوار وقالبُ النموذج) توكناتٌ يعدّها المزوّد في prompt_tokens ولا تقابلها بايتاتٌ في التكليف
# (ملاحظة Codex على #308)؛ فيُحجز لها حدٌّ ثابتٌ يفوق قوالبَ المحادثة المعروفة لرسالتين أضعافًا.
FRAMING_TOKENS = 512


def _ceil_micros(value: Decimal) -> int:
    """ميكرو-دولار صحيح، مقرَّبٌ إلى أعلى: التقديرُ والتسويةُ لا يُنقصان كسرًا من الكلفة."""
    return int(value.to_integral_value(rounding=ROUND_CEILING))


class PricedRouterChat(OpenAICompatChat):
    """موجّه HF مدفوعٌ بالتوكن، فلا يخرج نداءٌ إليه إلا بسعرٍ مقروءٍ وسقفٍ محجوز (جديد-spend-ledger، البندان ٢ و٣ من #295).

    - **السعر:** من `providers[].pricing` في فهرس الموجّه نفسِه ساعةَ التشغيل، لكل نموذجٍ أرخصُ مزوّدٍ حيٍّ أعلن سعرَيه لحدّ كلِّ طلبٍ
      له موزونًا (المدخلُ بسعره و`max_tokens` بسعر المخرج)، ويُثبَّت
      المزوّدُ في الحمولة (`<model>:<provider>`) فلا يختار الموجّهُ غيرَه. والنموذجُ بلا سعرٍ مقروء `price_unknown` قبل الشبكة.
    - **السقف:** `core.budget.Budget` بسقف التشغيل (`--max-usd`، لازمٌ وموجبٌ ولا يتجاوز HFD2). يُحجز قبل كل نداءٍ تقديرٌ أعلى:
      بايتاتُ التكليف (كلُّ توكنٍ بايتٌ على الأقل) وحدُّ التأطير (`FRAMING_TOKENS`) بسعر المدخل، و`max_tokens` بسعر المخرج.
      وما لا يتّسع `spend_cap_reached` قبل الشبكة.
    - **التسوية:** بالكلفة التي أبلغها الموجّه إن أبلغها، وإلا بتوكنات الردّ بالسعر نفسِه (`estimated_from_prices`). وما أُرسل بلا
      توكناتٍ يُسوّى بالمحجوز كلِّه (`reserved_upper_bound`)، لأن المزوّد قد يكون نفّذ.
    - **حدُّه:** كلفةٌ مبلَّغةٌ فوق محجوزها (إعادةُ تسعيرٍ أو رسمٌ إضافي) فوترها المزوّدُ فلا تُمحى: تُسمّى `exceeded_reservation_usd`
      في سطرها وفي `spend_cap`، ويقف التشغيلُ بـ`price_exceeded_reservation`. فالسقفُ حدٌّ على الحجز بسعر الفهرس، والضامنُ رصيدُ HFD2.
    """

    def __init__(self, api_key: str | None, *, spend_cap_usd: Decimal | None, **kw):
        super().__init__("hf-router", api_key, **kw)
        if spend_cap_usd is None:
            raise AutomaticReviewError("spend_cap_required", "--max-usd")
        cap = _decimal(str(spend_cap_usd))
        # وما لا يُكتب ميكرو-دولاراتٍ صحيحة يُردّ ولا يُقرَّب، فلا يتّسع السقفُ فوق المعلَن (ملاحظة Codex على #308)
        if cap is None or cap <= 0 or cap > HFD2_CAP_USD or (cap * MICROS_PER_USD) % 1:
            raise AutomaticReviewError("spend_cap_invalid", f"0 < --max-usd <= {HFD2_CAP_USD}, in whole micro-dollars")
        self.spend_cap_usd = cap
        cap_micros = _ceil_micros(cap * MICROS_PER_USD)
        self.budget = Budget(day_remaining_micros=cap_micros, month_remaining_micros=cap_micros)
        self.spent_micros = 0
        self.exceeded_micros = 0
        self.router_price_table: dict[str, dict] | None = None
        # كلُّ مزوّدٍ نُودي لكل نموذج بسعره، فالمزوّدُ يُختار لكل طلبٍ بحدّه (ملاحظة Codex على #308)
        self.price_pins: dict[str, dict[str, dict]] = {}
        self._request_pin: tuple[str, dict] | None = None

    def catalog(self) -> list[dict]:
        entries = super().catalog()
        self.router_price_table = {entry["id"]: entry["router_prices"] for entry in entries}
        return entries

    def _pin(self, model: str, input_tokens: int = FRAMING_TOKENS) -> dict:
        if self.router_price_table is None:
            try:
                self.catalog()
            except AutomaticReviewError as exc:
                # فهرسٌ لا يُقرأ سعرٌ مجهول؛ أمّا خطأ النقل (401 و403 والمهلة…) فيبقى برمزه، ولا نداءَ للنموذج في الحالين
                # (ملاحظة Codex على #308)
                if exc.code == "catalog_malformed":
                    raise AutomaticReviewError("price_unknown", model) from exc
                raise
        offers = (self.router_price_table or {}).get(model) or {}
        if not offers:
            raise AutomaticReviewError("price_unknown", model)
        # مزوّدٌ أعلن نافذةً دون حدّ الطلب لا يُختار ما دام غيرُه يتّسع، والنافذةُ غيرُ المعلنة لا تُستبعد. وإن لم يتّسع
        # أحدٌ بالحدّ (والحدُّ بالبايتات يفوق التوكنات) فالأرخصُ، ويحكم المزوّدُ بـ400/413 (ملاحظة Codex على #308)
        need = input_tokens + self.max_tokens
        offers = {name: offer for name, offer in offers.items() if offer.get("context_length", need) >= need} or offers
        # أرخصُ مزوّدٍ لحدّ الطلب الموزون: المدخلُ بسعره و`max_tokens` بسعر المخرج، لا مجموعُ السعرين (ملاحظة Codex على #308)
        provider = min(offers, key=lambda name: (input_tokens * offers[name]["input"]
                                                 + self.max_tokens * offers[name]["output"], name))
        pin = {"provider": provider, "input": offers[provider]["input"], "output": offers[provider]["output"],
               "read_at": self.catalog_read_at}
        self.price_pins.setdefault(model, {})[provider] = pin
        return pin

    def _wire_model(self, model: str) -> str:
        # المزوّدُ الذي حُجز له هذا الطلب نفسُه، لا اختيارٌ ثانٍ بحدٍّ آخر
        request = self._request_pin
        pin = request[1] if request is not None and request[0] == model else self._pin(model)
        return f"{model}:{pin['provider']}"

    def __call__(self, model: str, system: str, user: str, schema: dict) -> str:
        if self.exceeded_micros:
            # كلفةٌ مبلَّغةٌ فوق محجوزها تعني أن سعرَ الفهرس لم يعد حدًّا أعلى، فلا نداءَ بعدها (ملاحظة Codex على #308)
            raise AutomaticReviewError("price_exceeded_reservation", model)
        bound = len((system + user).encode("utf-8")) + FRAMING_TOKENS
        pin = self._pin(model, bound)
        self._request_pin = (model, pin)
        estimate = _ceil_micros(bound * pin["input"] + self.max_tokens * pin["output"])
        handle = f"{model}#{len(self.provider_usage)}"
        try:
            self.budget.reserve(handle, estimate)
        except BudgetRefused as exc:
            raise AutomaticReviewError("spend_cap_reached", model) from exc
        rows = len(self.provider_usage)
        try:
            content = super().__call__(model, system, user, schema)
        finally:
            row = self.provider_usage[-1] if len(self.provider_usage) > rows else None
            self._settle(handle, pin, row, estimate)
        return content

    def _settle(self, handle: str, pin: dict, row: dict | None, reserved: int) -> None:
        usage = (row or {}).get("usage") or {}
        if row is not None and row.get("cost_status") == "reported":
            spent = self.budget.settle(handle, _ceil_micros(Decimal(row["cost_usd"]) * MICROS_PER_USD))
        elif {"prompt_tokens", "completion_tokens"} <= usage.keys():
            spent = self.budget.settle(handle, _ceil_micros(usage["prompt_tokens"] * pin["input"]
                                                            + usage["completion_tokens"] * pin["output"]))
            row.update(cost_usd=str(Decimal(spent) / MICROS_PER_USD), cost_status="estimated_from_prices")
        else:
            spent = self.budget.settle_unknown(handle)
            if row is not None:
                row.update(cost_usd=str(Decimal(spent) / MICROS_PER_USD), cost_status="reserved_upper_bound")
        self.spent_micros += spent
        if spent > reserved:
            # كلفةٌ فوق محجوزها، مبلَّغةً أو محسوبةً من توكنات الردّ، وقعت فلا تُمحى: تُسمّى في سطرها وفي التقرير، ويُوقف ما
            # بعدها (ملاحظتا Codex على #308)
            self.exceeded_micros += spent - reserved
            if row is not None:
                row["exceeded_reservation_usd"] = str(Decimal(spent - reserved) / MICROS_PER_USD)
        if row is not None:
            row["price"] = {"provider": pin["provider"], "input": str(pin["input"]), "output": str(pin["output"]),
                            "unit": "usd_per_million_tokens", "read_at": pin["read_at"]}

    def spend_report(self) -> dict:
        return {**super().spend_report(), "spend_cap": {
            "cap_usd": str(self.spend_cap_usd), "spent_usd": str(Decimal(self.spent_micros) / MICROS_PER_USD),
            **({"exceeded_reservation_usd": str(Decimal(self.exceeded_micros) / MICROS_PER_USD)}
               if self.exceeded_micros else {}),
            "prices": {model: {provider: {"input": str(pin["input"]), "output": str(pin["output"]),
                                          "unit": "usd_per_million_tokens", "read_at": pin["read_at"],
                                          "catalog": bare_url(self.catalog_url)}
                               for provider, pin in sorted(providers.items())}
                       for model, providers in sorted(self.price_pins.items())}}}


def build_free_transport(backend: str, environ=os.environ, *, spend_cap_usd: Decimal | None = None,
                         **kw) -> OpenAICompatChat:
    """النقلُ المجانيّ؛ والمفتاحُ من البيئة وحدها (لا خيارَ له في سطر الأوامر). وموجّهُ HF مدفوعٌ فنقلُه مسعَّرٌ بسقف."""
    if backend not in BACKENDS:
        raise AutomaticReviewError("backend_unknown", backend)
    spec = BACKENDS[backend]
    if backend == "hf-router":
        return PricedRouterChat(environ.get(spec["key_env"]) or None, spend_cap_usd=spend_cap_usd, **kw)
    if spend_cap_usd is not None:
        raise AutomaticReviewError("max_usd_is_hf_router_only", backend)
    confirmation = environ.get(spec.get("free_tier_env", "")) if spec.get("free_tier_env") else None
    return OpenAICompatChat(backend, environ.get(spec["key_env"]) or None,
                            free_tier_confirmation=confirmation, **kw)


def assess_catalog(entries: list[dict], backend: str) -> dict:
    """كلُّ نموذجٍ في الفهرس: صالحٌ بعائلته، أو مرفوضٌ برمزه؛ والمرشّحون مرتَّبون بالعائلات المفضَّلة."""
    rows = []
    for index, entry in enumerate(entries):
        row = {"id": entry["id"], "tier": entry.get("tier"), "family": None, "lineage": None,
               "eligible": False, "code": entry.get("reason")}
        if entry.get("chat", True):
            try:
                identity = resolve_reviewer(entry["id"], backend)
                zero_spend = openrouter_zero_spend(entry) if backend == "openrouter" else None
                if backend == "hf-router" and not entry.get("router_prices"):
                    # مرشّحٌ بلا سعرٍ مقروء يوقف التشغيلَ كلَّه بـprice_unknown عند أوّل نداء، فلا يُرشَّح (ملاحظة Codex على #308)
                    raise AutomaticReviewError("price_unknown", entry["id"])
                row.update(family=identity["family"], lineage=identity["lineage"], eligible=True,
                           zero_spend_proof=zero_spend)
            except AutomaticReviewError as exc:
                row["code"] = exc.code
        row["order"] = index
        rows.append(row)
    preferred = BACKENDS[backend]["preferred"]

    def rank(row: dict) -> tuple:
        wanted = preferred.get(row["family"], ())
        tokens = set(re.split(r"[^a-z0-9]+", row["id"].lower()))
        return (PREFERRED_FAMILIES.index(row["family"]),
                wanted.index(row["id"]) if row["id"] in wanted else len(wanted),
                bool(tokens & _DEMOTED_TOKENS), row["order"])

    eligible = sorted((r for r in rows if r["eligible"]), key=rank)
    best: dict[str, dict] = {}
    for row in eligible:
        best.setdefault(row["family"], row)
    ordered = list(best.values()) + [r for r in eligible if best[r["family"]] is not r]
    refused: dict[str, int] = {}
    for row in rows:
        if not row["eligible"]:
            refused[row["code"]] = refused.get(row["code"], 0) + 1
    for row in rows:
        del row["order"]
    return {"models": len(rows), "rows": rows,
            "candidates": [{"model": r["id"], "publisher": r["id"].split("/", 1)[0],
                            "family": r["family"], "lineage": r["lineage"]} for r in ordered],
            "refused": dict(sorted(refused.items()))}


def _quota_models(bank: Path, models: list[str], current: set[str]) -> set[str]:
    """النماذجُ التي سُجّل لها في هذا البنك، لملفٍّ من ملفّات open/ الحالية، سجلٌّ خطؤه نفادُ الحصّة."""
    spent = set()
    for model in models:
        root = bank / "reviews" / _slug(model)
        for path in root.rglob("*.json"):
            if path.relative_to(root).as_posix() not in current:      # سجلُّ ملفٍّ لم يعد في open/ تاريخٌ لا يُحكم به
                continue
            error = json.loads(path.read_text(encoding="utf-8")).get("error")
            if error in BUDGET_TERMINAL_ERRORS:
                raise AutomaticReviewError(error)
            if error == "quota_exhausted":
                spent.add(model)
    return spent


def with_fallback(candidates: list[dict], want: int, run: Callable[[list[dict]], set[str]],
                  on_replaced: Callable[[dict, list[str]], None] | None = None) -> tuple:
    """يشغّل `run` على المختارين؛ ومن نفدت حصّتُه يُستبدل بمرشّحٍ من عائلةٍ أخرى حتى لا يبقى بديل، ويُبلَّغ `on_replaced`
    بكل مستبدَلٍ وبديله قبل التشغيل التالي.

    يُعيد (المختارين الأخيرين، سجلَّ الاستبدال، رمزَ الإخفاق أو None).
    """
    exhausted: list[dict] = []
    fallbacks: list[dict] = []
    chosen = choose_reviewers(candidates, want)
    while True:
        try:
            spent = run(chosen)
        except AutomaticReviewError as exc:
            if exc.code not in BUDGET_TERMINAL_ERRORS:
                raise
            return chosen, fallbacks, exc.code
        if not spent:
            return chosen, fallbacks, None
        for identity in chosen:
            if identity["model"] in spent:
                exhausted.append(identity)
        try:
            replacement = choose_reviewers(candidates, want, exhausted)
        except AutomaticReviewError as exc:
            fallbacks.append({"exhausted": sorted(spent), "code": "quota_exhausted", "replacement": None})
            return chosen, fallbacks, exc.code
        fallbacks.append({"exhausted": sorted(spent), "code": "quota_exhausted",
                          "replacement": [i["model"] for i in replacement if i not in chosen]})
        if on_replaced is not None:
            for identity in chosen:
                if identity["model"] in spent:
                    on_replaced(identity, fallbacks[-1]["replacement"])
        chosen = replacement


def _free_candidates(args, transport: OpenAICompatChat) -> tuple[list[dict], dict]:
    """(المرشّحون، مصدرُهم): `--reviewer` ثم `--fallback` كما أُعطيت بصرامة، أو من الفهرس بترتيب العائلات المفضَّلة.

    والفهرسُ الذي لا يُقرأ لا يُسقط التجربة صامتًا ولا يوقفها: المرشّحون من قائمة الواجهة المفضَّلة، ورمزُ الفهرس وشكلُه في التقرير.
    """
    if args.reviewers:
        explicit = [resolve_reviewer(m, args.backend) for m in args.reviewers]
        check_distinct(explicit)
        candidates = explicit + [resolve_reviewer(m, args.backend) for m in (args.fallbacks or [])]
        if args.backend == "openrouter":
            # المعرّف الصريح لا يتجاوز فحص السعر: يُقرأ الفهرس قبل أول نداء chat، وكل غياب/سعر مجهول رفضٌ.
            entries = transport.catalog()
            transport.approve_zero_spend(entries, [candidate["model"] for candidate in candidates])
        return candidates, {"candidates_from": "explicit"}
    if args.fallbacks:
        raise AutomaticReviewError("fallback_without_reviewers")
    try:
        entries = transport.catalog()
        candidates = assess_catalog(entries, args.backend)["candidates"]
    except AutomaticReviewError as exc:          # الفهرسُ غيرُ مقروء: القائمةُ المفضَّلة، والسببُ مسمًّى
        if args.backend == "openrouter":
            # لا قائمةَ مفضلة تتجاوز دليل السعر الصفري؛ غياب الفهرس يعني أن مجانية الطلب لم تُثبت.
            raise AutomaticReviewError("free_price_unverified", "catalog") from exc
        preferred = [resolve_reviewer(model, args.backend) for family in PREFERRED_FAMILIES
                     for model in BACKENDS[args.backend]["preferred"][family]]
        return preferred, {"candidates_from": "preferred_list", "catalog_error": exc.code,
                           "catalog_shape": getattr(exc, "shape", None)}
    transport.approve_zero_spend(entries, [candidate["model"] for candidate in candidates])
    return candidates, {"candidates_from": "catalog", "catalog_shape": transport.catalog_shape}


def _smoke_digest(report: dict) -> dict:
    return {model: {k: r[k] for k in ("family", "error", "elapsed_ms", "caught_planted_error", "false_flags")}
            for model, r in report["reviewers"].items()}


def _smoke_budget_error(report: dict) -> str | None:
    return next((r["error"] for r in report["reviewers"].values()
                 if r["error"] in BUDGET_TERMINAL_ERRORS), None)


EXIT_CODES = {"passed": 0, "reviewed": 0, "unavailable": 3}     # وما سواها 1؛ و3 «الواجهةُ غيرُ متاحة» لا «المراجعُ أخطأ»


def _mark_unavailable(report: dict, errors: list) -> dict:
    """إن لم يُجب نموذجٌ واحد وكانت الأخطاءُ كلُّها رفضَ خدمة: الحالةُ unavailable برمزٍ مسمًّى لا failed."""
    codes = unavailable_codes(errors)
    if report["status"] != "passed" and "code" not in report and codes:
        report.update(status="unavailable", code="service_unavailable", unavailable_codes=codes)
    return report


def _free_smoke(args, transport: OpenAICompatChat) -> tuple[dict, int]:
    candidates, source = _free_candidates(args, transport)
    identities = {c["model"]: c for c in candidates}
    want = max(2, len(args.reviewers or []))
    if args.every_family:
        best = []
        for identity in candidates:
            if not any(set(identity["lineage"]) & set(b["lineage"]) for b in best):
                best.append(identity)
        if len(best) < 2:
            raise AutomaticReviewError("reviewers_unavailable", f"{len(best)}/2")
        pairs = [best[i:i + 2] for i in range(0, len(best) - 1, 2)]
        if len(best) % 2:
            pairs.append([best[-1], best[0]])
        runs = []
        budget_error = None
        with tempfile.TemporaryDirectory() as tmp:
            for index, pair in enumerate(pairs):
                runs.append(smoke(Path(tmp) / f"pair_{index}", [p["model"] for p in pair], transport,
                                  brief_path=args.brief))
                budget_error = _smoke_budget_error(runs[-1])
                if budget_error:
                    break
        models = {}
        for run in runs:
            for model, result in run["reviewers"].items():
                models.setdefault(model, {**result, "lineage": identities[model]["lineage"],
                                          "reachable": result["attempts"] > 0 and result["error"] not in {
                                              "transport_error", "transport_timeout"}})
        report = {"schema_version": 1, "probe": "external_review_smoke_every_family",
                  "status": "passed" if all(r["status"] == "passed" for r in runs) else "failed",
                  "backend": transport.describe(), **source, "models": models,
                  "pairs": [[p["model"] for p in pair] for pair in pairs[:len(runs)]],
                  "not_attempted_pairs": [[p["model"] for p in pair] for pair in pairs[len(runs):]],
                  "runs": runs, "measurement_limits": free_limits(runs[0]["measurement_limits"], args.backend),
                  "provider_usage": transport.provider_usage, **_spend_report(transport),
                  "last_failure_shapes": transport.failures}
        exceeded = _cap_exceeded(transport)
        if exceeded:
            report["status"], budget_error = "failed", budget_error or exceeded
        if budget_error:
            report["code"] = budget_error
        _mark_unavailable(report, [m["error"] for m in models.values()])
        return report, EXIT_CODES.get(report["status"], 1)
    tmp = tempfile.TemporaryDirectory()
    try:
        reports: list[dict] = []

        def run(chosen: list[dict]) -> set[str]:
            report = smoke(Path(tmp.name), [c["model"] for c in chosen], transport, brief_path=args.brief)
            reports.append(report)
            budget_error = _smoke_budget_error(report)
            if budget_error:
                raise AutomaticReviewError(budget_error)
            return {m for m, r in report["reviewers"].items() if r["error"] == "quota_exhausted"}

        chosen, fallbacks, failure = with_fallback(candidates, want, run)
    finally:
        tmp.cleanup()
    report = reports[-1]
    for model, result in report["reviewers"].items():
        result["lineage"] = identities[model]["lineage"]
    report["backend"] = transport.describe()
    report.update(source)
    report["fallbacks"] = fallbacks
    report["measurement_limits"] = free_limits(report["measurement_limits"], args.backend)
    report["provider_usage"] = transport.provider_usage
    report.update(_spend_report(transport))
    report["last_failure_shapes"] = transport.failures
    failure = failure or _cap_exceeded(transport)
    if failure:
        report["status"], report["code"] = "failed", failure
    # نفادُ حصّة مستبدَلٍ خطأٌ لا رفضُ خدمة: فالإخفاقُ المختلط يبقى failed ولا يصير unavailable (ملاحظة Codex على #174)
    _mark_unavailable(report, [r["error"] for r in report["reviewers"].values()]
                      + ["quota_exhausted" for entry in fallbacks for _model in entry["exhausted"]])
    return report, EXIT_CODES.get(report["status"], 1)


def sealed_digests(root: Path | None = None) -> dict[str, str]:
    """بصماتُ المحجوب كما تعلنها بياناتُه (`evaluation/banks/*/sealed/MANIFEST.json`، الشكلان: قاموسٌ أو قائمة) ← وسمُها.

    هي البصماتُ نفسُها التي يقارن بها حارسُ المستودع (tests/test_sealed_banks_stay_sealed.py)؛ ولا يُقرأ من المحجوب غيرُ بيانه.
    """
    digests: dict[str, str] = {}
    for manifest in sorted(((root or ROOT) / "evaluation" / "banks").glob("*/sealed/MANIFEST.json")):
        files = json.loads(manifest.read_text(encoding="utf-8")).get("files") or {}
        entries = files.items() if isinstance(files, dict) else ((e.get("path"), e) for e in files)
        for path, entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("sha256"), str):
                digests[entry["sha256"]] = f"{manifest.parent.parent.name}/{path}"
    return digests


def inspect_open_split(bank: Path) -> dict[str, str]:
    """قبل أيّ نسخٍ أو إرسال (ملاحظة Codex على #174): تُمشى `open/` بـlstat بلا اتّباع روابط — رابطٌ رمزيّ (لملفٍّ أو مجلّد) أو ملفٌّ
    غيرُ عاديّ `open_split_link_refused`؛ وكلُّ ملفٍّ يُبصم فإن طابقت بصمتُه محجوبًا (ولو غيّر اسمه) `sealed_digest_refused`.
    يُعيد {المسارُ النسبيّ: البصمة}."""
    source = bank / "open"
    if not source.exists() or stat.S_ISLNK(os.lstat(source).st_mode):
        raise AutomaticReviewError("open_split_link_refused" if source.is_symlink() else "open_split_missing", "open")
    sealed = sealed_digests()
    digests: dict[str, str] = {}
    for directory, dirnames, filenames in os.walk(source, followlinks=False):
        for name in sorted(dirnames) + sorted(filenames):
            path = Path(directory) / name
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise AutomaticReviewError("open_split_link_refused", path.relative_to(source).as_posix())
        for name in sorted(filenames):
            path = Path(directory) / name
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in sealed:
                raise AutomaticReviewError("sealed_digest_refused", path.relative_to(source).as_posix())
            digests[path.relative_to(source).as_posix()] = digest
    return digests


def check_public_bank(bank: Path, root: Path | None = None) -> None:
    """على الواجهات المجانية لا يُرسل إلا بنكٌ تحت `evaluation/banks/` (لا ملفّاتُ المالك ولا ما خارج المستودع)."""
    try:
        bank.resolve().relative_to(((root or ROOT) / "evaluation" / "banks").resolve())
    except ValueError:
        raise AutomaticReviewError("bank_outside_evaluation_banks", str(bank)) from None


def supersede_records(bank: Path, model: str, replacement: list[str]) -> Path:
    """سجلّاتُ مراجعٍ استُبدل به تُنقل إلى `reviews/superseded/<النموذج>/` تاريخًا مسمًّى، ومعها SUPERSEDED.json باسم بديله.

    فلا تدخل زوجًا ولا κ ولا قائمةَ المالك، ولا يرثها البديل: يراجع البديلُ البنكَ كلَّه — ومنه ما أتمّه المستبدَل — فيُحسب
    الاتفاقُ على زوجٍ واحدٍ متّسق (ملاحظة Codex على #174).
    """
    source = bank / "reviews" / _slug(model)
    destination = bank / "reviews" / SUPERSEDED_DIR / _slug(model)
    if destination.is_dir():                  # تاريخُ استبدالٍ سابق لا يُخلط بهذا (ملاحظة Codex على #174): يُنشأ من جديد
        shutil.rmtree(destination)
    for path in sorted(source.rglob("*.json")) if source.is_dir() else []:
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, target)
    if source.is_dir():                       # المجلّداتُ التي فرغت تُزال، وما بقي فيه شيءٌ (غيرُ سجلّ) يبقى
        for directory in sorted((d for d in source.rglob("*") if d.is_dir()), reverse=True) + [source]:
            if not any(directory.iterdir()):
                directory.rmdir()
    _write_json(destination / "SUPERSEDED.json", {"model": model, "superseded_by": replacement,
                                                  "code": "quota_exhausted"})
    return destination


def superseded_history(bank: Path, replaced_by: dict[str, list[str] | None],
                       current: set[str]) -> tuple[list[dict], dict[str, int]]:
    """(أخطاءُ المستبدَلين، عددُ ما أتمّه كلٌّ منهم) من `reviews/superseded/`: تاريخٌ يُروى ولا يُحكم به."""
    errors: list[dict] = []
    completed: dict[str, int] = {}
    for model in sorted(replaced_by):
        root = bank / "reviews" / SUPERSEDED_DIR / _slug(model)
        for path in sorted(root.rglob("*.json")) if root.is_dir() else []:
            if path.name == "SUPERSEDED.json":
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("file") not in current:
                continue
            if record.get("error"):
                errors.append({"model": record["model"], "file": record["file"], "error": record["error"]})
            else:
                completed[model] = completed.get(model, 0) + 1
    return errors, completed


def other_reviewer_errors(bank: Path, final_models: set[str], current: set[str]) -> int:
    """أخطاءُ سجلّاتٍ في `reviews/` لمراجعين خارج المجموعة الأخيرة ولا المستبدَلين (تشغيلاتٌ سابقة): تُروى عددًا ولا تُسقط."""
    root = bank / "reviews"
    count = 0
    for path in sorted(root.rglob("*.json")) if root.is_dir() else []:
        if path.name == "SUMMARY.json" or path == root / LEDGER_FILE or path.relative_to(root).parts[0] == SUPERSEDED_DIR:
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("error") and record.get("model") not in final_models and record.get("file") in current:
            count += 1
    return count


def _successful_records(bank: Path) -> dict[str, str]:
    """{المسارُ النسبيّ: بصمةُ بايتات السجلّ} للسجلّات الناجحة في reviews/ (خارج superseded/) قبل هذا الاستدعاء.

    السجلُّ «skipped» إن بقي بعده ببايتاته نفسِها (أُعيد استعمالُه)؛ فإن أُعيدت مراجعتُه لتغيّر الملفّ أو التكليف كُتب من جديد
    فتغيّرت بصمتُه وعُدّ «reviewed» (ملاحظة Codex على #174)."""
    root = bank / "reviews"
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*.json")) if root.is_dir() else []:
        relative = path.relative_to(root)
        if path.name in ("SUMMARY.json", RUN_FILE) or relative == Path(LEDGER_FILE) or relative.parts[0] == SUPERSEDED_DIR:
            continue
        raw = path.read_bytes()
        if json.loads(raw).get("error") is None:
            found[relative.as_posix()] = hashlib.sha256(raw).hexdigest()
    return found


def final_set_counts(bank: Path, final_models: set[str], pre_existing: dict[str, str], current: set[str]) -> dict:
    """أعدادُ المجموعة الأخيرة وحدها (ملاحظة Codex على #174): ناجحٌ أُنتج في هذا الاستدعاء reviewed، وناجحٌ سبقه skipped،
    وفاشلٌ failed. ومجاميعُ المحاولات كلِّها (ومنها المستبدَلون) في حقلٍ مسمًّى منفصل."""
    counts = {"reviewed": 0, "skipped": 0, "failed": 0}
    for model in sorted(final_models):
        root = bank / "reviews" / _slug(model)
        for path in sorted(root.rglob("*.json")) if root.is_dir() else []:
            if path.relative_to(root).as_posix() not in current:
                continue
            relative = path.relative_to(bank / "reviews").as_posix()
            if json.loads(path.read_text(encoding="utf-8")).get("error"):
                counts["failed"] += 1
            elif pre_existing.get(relative) == hashlib.sha256(path.read_bytes()).hexdigest():
                counts["skipped"] += 1
            else:
                counts["reviewed"] += 1
    return counts


def _free_bank(args, transport: OpenAICompatChat) -> tuple[dict, int]:
    check_public_bank(args.bank)
    inspect_open_split(args.bank)              # لا رابطَ ولا محجوبَ بالبصمة قبل أوّل نداء
    # سجلُّ الخلاصة ودليلُ مجانيتها قبل أن تُعاد كتابتُها، فيُلحَق بهما ولا يُستبدلان (ملاحظة Codex على #298)
    prior, prior_evidence = prior_provider_usage(args.bank), prior_zero_spend_evidence(args.bank)
    # ملفّاتُ open/ الحالية: كلُّ قراءةٍ لسجلّات المراجعة بعدها تُقارن بها، فسجلُّ ملفٍّ حُذف أو أُعيدت تسميتُه منذ تشغيلٍ سابق
    # لا يدخل النفادَ ولا الاتفاقَ ولا قائمةَ المالك ولا الأعداد ولا التاريخ (ملاحظة Codex على #174)
    current = {path.relative_to(args.bank / "open").as_posix() for path in open_files(args.bank)}
    try:
        candidates, source = _free_candidates(args, transport)
        want = max(2, len(args.reviewers or []))
        attempts = {"reviewed": 0, "skipped": 0, "failed": 0}
        pre_existing = _successful_records(args.bank)

        def run(chosen: list[dict]) -> set[str]:
            models = [c["model"] for c in chosen]
            # كلُّ سجلٍّ يحمل واجهتَه، ولا يُعاد استعمالُ سجلِّ واجهةٍ أخرى بمعرّف النموذج نفسِه (ملاحظة Codex على #174)
            counts = review_bank(args.bank, models, transport, brief_path=args.brief,
                                 stamp={"backend": transport.backend, "endpoint_host": transport.endpoint_host},
                                 reusable=lambda prior: prior.get("backend") == transport.backend)
            for key in attempts:
                attempts[key] += counts[key]
            return _quota_models(args.bank, models, current)

        def replaced(identity: dict, replacement: list[str]) -> None:
            supersede_records(args.bank, identity["model"], replacement)

        chosen, fallbacks, failure = with_fallback(candidates, want, run, on_replaced=replaced)
    finally:
        # ما أُرسل (ومنه نداءُ الفهرس) يبقى في السجلّ الدائم على أيّ خروج، قبل أن تُقرأ سجلّاتُ المراجعة في الخلاصة
        # (ملاحظتا Codex على #298). أمّا تشغيلٌ بمعرّفٍ فـRUN.json وخلاصتُه يحملان نداءاتِه، ومجلّدُه جديد
        evidence = merged_zero_spend_evidence(prior_evidence, transport)
        if not args.run_id and transport.provider_usage:
            write_ledger(args.bank, prior + transport.provider_usage, evidence,
                         prior_spend_caps(args.bank) + _run_spend_cap(transport))
    # النجاحُ والاتفاقُ وقائمةُ المالك من المجموعة الأخيرة وحدها (ملاحظتا Codex على #174): المستبدَلُ نُقلت سجلّاتُه إلى
    # reviews/superseded/ وراجع بديلُه البنكَ كلَّه؛ ونفادُ حصّته تاريخٌ مسمًّى (superseded ومعه البديل) لا خطأٌ يُسقط التشغيل،
    # فإن أخفق البديلُ أيضًا عُدّ الخطآن كلاهما. وأخطاءُ مراجعين خارج هذا التشغيل لا تُحسب عليه، وتُروى عددًا.
    final_models = {c["model"] for c in chosen}
    summary = summarize(args.bank, reviewers=final_models, files=current)
    replaced_by = {model: entry["replacement"] for entry in fallbacks for model in entry["exhausted"]
                   if entry["replacement"] is not None}
    final_errors = summary["errors"]
    failure = failure or _cap_exceeded(transport)
    history_errors, completed = superseded_history(args.bank, replaced_by, current)
    quota_errors = [e for e in history_errors if e["error"] == "quota_exhausted"]
    if failure or final_errors:
        counted, superseded = final_errors + quota_errors, []
    else:
        counted = []  # المستبدَلُ ناجحًا تاريخٌ لا خطأ
        superseded = [{**e, "superseded_by": replaced_by[e["model"]]} for e in quota_errors]
    status = "failed" if failure or counted else "reviewed"
    limits = free_limits(summary["measurement_limits"], args.backend)
    _persist_limits(args.bank, limits)
    # الخلاصةُ للبنك كلِّه: السجلُّ والمجموعُ وعدُّ ما لم تثبت كلفتُه من السجلّ المُلحَق، ودليلُ المجانية الجديدُ فوق السابق.
    # أمّا المطبوعُ أدناه فتقريرُ هذا التشغيل وحده.
    ledger = prior + transport.provider_usage
    # سقفُ التشغيل المدفوع وإنفاقُه في الخلاصة أيضًا، فيُرى في مجلّد التشغيل المرفوع لا في المطبوع وحده (ملاحظة Codex على #308)
    cap = _spend_report(transport).get("spend_cap")
    _persist_provider_usage(args.bank, ledger, {
        "zero_spend_evidence": evidence,
        "cost_unconfirmed_attempts": cost_unconfirmed_attempts(ledger), "token_totals": token_totals(ledger),
        **({"spend_cap": cap} if cap else {})})
    if args.run_id:          # كلُّ سجلٍّ وخلاصةٍ في مجلّد هذا التشغيل يحمل معرّفَه، فيُرفض عند الرفع ما لا يحمله
        stamp_run(args.bank / "reviews", args.run_id)
    final_counts = final_set_counts(args.bank, final_models, pre_existing, current)
    result = {"status": status, **({"code": failure} if failure else {}), **final_counts,
              "attempts": attempts, **({"run_id": args.run_id} if args.run_id else {}),
              "measurement_limits": limits,
              "provider_usage": transport.provider_usage, **_spend_report(transport),
              "backend": transport.describe(), **source,
              "reviewers": {c["model"]: c["family"] for c in chosen}, "fallbacks": fallbacks,
              "pairs": summary["pairs"], "errors": len(counted),
              "error_codes": sorted({e["error"] for e in counted}), "superseded": superseded,
              "superseded_completed": completed,
              "errors_of_other_reviewers": other_reviewer_errors(args.bank, final_models, current),
              **({"artifact": args.artifact_name} if args.artifact_name else {}),
              "owner_queue": len(summary["owner_queue"]), "last_failure_shapes": transport.failures,
              "summary": str(args.bank / "reviews" / "SUMMARY.json")}
    if not attempts["reviewed"] and not attempts["skipped"]:        # لم يُجب نموذجٌ واحد في هذا التشغيل
        _mark_unavailable(result, [e["error"] for e in counted])      # كلُّ ما عُدّ، ومنه نفادُ حصّة المستبدَل
    if args.run_id:
        finish_run(args.bank, result["status"], result.get("code"))
    return result, EXIT_CODES.get(result["status"], 1)


# ————— مجلّدُ تشغيلٍ نظيف (ملاحظة Codex على #174): لا يُرفع باسم تشغيلٍ ما لم يولّده هو —————

RUN_FILE = "RUN.json"
_RUN_ID = re.compile(r"(?!\.{1,2}$)[A-Za-z0-9_.-]{1,64}")


def prepare_run(bank: Path, run_id: str) -> Path:
    """`<البنك>/runs/<run_id>/`: نسخةٌ من الشطر المفتوح وحده، ومراجعاتٌ تبدأ فارغة، وRUN.json بحالة started.

    فالخلاصةُ المتتبَّعة في `<البنك>/reviews/` لا تُقرأ ولا تُرفع باسم هذا التشغيل، وما يُرفع وُلد فيه وحده.
    """
    if not _RUN_ID.fullmatch(run_id):
        raise AutomaticReviewError("run_id_invalid", run_id)
    run = bank / "runs" / run_id
    if run.exists():
        raise AutomaticReviewError("run_dir_exists", run_id)
    expected = inspect_open_split(bank)        # قبل أيّ نسخ: لا رابطَ ولا محجوبَ بالبصمة
    source = bank / "open"
    # والنسخُ لا يتبع رابطًا (symlinks=True ينسخه رابطًا فيُرفض بعده)، ثم يُتحقّق من كل منسوخ: ليس رابطًا، ومسارُه الحقيقيّ داخل
    # نسخة التشغيل، وبصمتُه بصمةُ مصدره المفحوص، ولا زائدَ ولا ناقص
    shutil.copytree(source, run / "open", symlinks=True)
    copied = run / "open"
    found: dict[str, str] = {}
    for directory, dirnames, filenames in os.walk(copied, followlinks=False):
        for name in sorted(dirnames) + sorted(filenames):
            path = Path(directory) / name
            relative = path.relative_to(copied).as_posix()
            if stat.S_ISLNK(os.lstat(path).st_mode):
                raise AutomaticReviewError("open_split_link_refused", relative)
            try:
                path.resolve().relative_to(copied.resolve())
            except ValueError:
                raise AutomaticReviewError("open_split_copy_mismatch", relative) from None
        for name in sorted(filenames):
            path = Path(directory) / name
            found[path.relative_to(copied).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if found != expected:
        raise AutomaticReviewError("open_split_copy_mismatch", run_id)
    _write_json(run / "reviews" / RUN_FILE, {"run_id": run_id, "started_at": _utc_now(), "status": "started"})
    return run


def _persist_limits(bank: Path, limits: list[str]) -> None:
    """الخلاصةُ المحفوظة تحمل حدودَ الواجهات المجانية نفسَها التي في النتيجة المطبوعة."""
    path = bank / "reviews" / "SUMMARY.json"
    if path.is_file():
        summary = json.loads(path.read_text(encoding="utf-8"))
        summary["measurement_limits"] = limits
        _write_json(path, summary)


def _cap_exceeded(transport) -> str | None:
    """كلفةٌ أبلغها المزوّدُ فوق محجوزها تُفشل التشغيلَ باسمها ولو كانت آخرَ نداء (ملاحظة Codex على #308)."""
    cap = _spend_report(transport).get("spend_cap")
    return "price_exceeded_reservation" if isinstance(cap, dict) and "exceeded_reservation_usd" in cap else None


def _spend_report(transport) -> dict:
    """مجموعُ التوكنات لكل نموذج، ودليلُ المجانية وعدُّ ما لم تثبت كلفتُه من نقلٍ يعرفهما؛ والنقلُ المزيَّف بلا تقريرٍ لا يُنسب إليه شيء."""
    report = getattr(transport, "spend_report", None)
    return report() if callable(report) else {}


def _persist_provider_usage(bank: Path, usage: list[dict], spend: dict | None = None) -> None:
    """سجلُّ النداءات في خلاصة التشغيل نفسها؛ لا يُكتب صفرٌ لكلفة لم يبلغها المزوّد. ومعه دليلُ المجانية (#285)."""
    path = bank / "reviews" / "SUMMARY.json"
    if path.is_file():
        summary = json.loads(path.read_text(encoding="utf-8"))
        summary["provider_usage"] = usage
        summary.update(spend or {})
        _write_json(path, summary)


def finish_run(run: Path, status: str, code: str | None = None, usage: list[dict] | None = None,
               spend_cap: dict | None = None) -> None:
    """حالةُ التشغيل في RUN.json: reviewed أو failed أو unavailable أو refused برمزه — فإن خرج قبل الخلاصة بقي هذا وحده،
    ومعه سجلُّ ما خرج من نداءاتٍ قبل الرفض إن أُعطي (ملاحظة Codex على #290)، وسقفُ التشغيل المدفوع وإنفاقُه، فلا يبقى دليلٌ
    مدفوعٌ بلا سقفه المعلن (ملاحظة Codex على #308)."""
    path = run / "reviews" / RUN_FILE
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(status=status, finished_at=_utc_now(), **({"code": code} if code else {}),
                  **({"provider_usage": usage} if usage else {}), **({"spend_cap": spend_cap} if spend_cap else {}))
    _write_json(path, record)


def stamp_run(reviews: Path, run_id: str) -> None:
    """كلُّ ملفّ JSON في مراجعات هذا التشغيل (السجلّاتُ والمستبدَلون والخلاصة) يحمل run_id."""
    for path in sorted(reviews.rglob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(record, dict) and record.get("run_id") != run_id:
            record["run_id"] = run_id
            _write_json(path, record)


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# نمطُ الرمز نفسُه في tools/export_public.py::PERSONAL_PATTERNS["token"] (اختبارٌ يربطهما، فلا يُستورد هنا ما يجرّ core)
_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}"
                            r"|(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"
                            r"|(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])")


def check_artifact(root: Path, environ=os.environ, run_id: str | None = None) -> dict:
    """ما يُرفع من `reviews/` في Actions (ملاحظة Codex على #174): سجلّاتُ JSON لشطرٍ مفتوح وحدها — لا مسارَ فيه «sealed» بأيّ
    حالة أحرف، ولا سجلَّ ملفُّه محجوب، ولا نمطَ رمز، ولا قيمةَ مفتاح واجهةٍ من البيئة. يُعيد {status, files, owner_queue} أو
    يرفض برمزٍ مسمًّى باسم الملفّ وحده (لا بمحتواه)."""
    if not root.is_dir():
        raise AutomaticReviewError("artifact_missing", str(root))
    keys = [environ.get(name) for name in [CLOUD_KEY_ENV, *(spec["key_env"] for spec in BACKENDS.values())]]
    keys = [key for key in keys if key]
    files = 0
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if "sealed" in relative.lower():
            raise AutomaticReviewError("artifact_sealed_path", relative)
        try:
            text = path.read_text(encoding="utf-8")
            record = json.loads(text)
        except (UnicodeDecodeError, ValueError):
            raise AutomaticReviewError("artifact_not_json", relative) from None
        if isinstance(record, dict) and "sealed" in str(record.get("file", "")).lower():
            raise AutomaticReviewError("artifact_sealed_path", relative)
        # مربوطٌ بهذا التشغيل: خلاصةٌ أو سجلٌّ لا يحمل معرّفَه تاريخٌ لا يُرفع باسمه (ملاحظة Codex على #174)
        if run_id is not None and (not isinstance(record, dict) or record.get("run_id") != run_id):
            raise AutomaticReviewError(
                "artifact_stale_summary" if path.name == "SUMMARY.json" else "artifact_stale_record", relative)
        if _TOKEN_PATTERN.search(text):
            raise AutomaticReviewError("artifact_token_pattern", relative)
        if any(key in text for key in keys):
            raise AutomaticReviewError("artifact_key_value", relative)
        files += 1
    summary = root / "SUMMARY.json"
    queue = len(json.loads(summary.read_text(encoding="utf-8"))["owner_queue"]) if summary.is_file() else None
    run = root / RUN_FILE
    run_state = json.loads(run.read_text(encoding="utf-8")) if run.is_file() else None
    return {"status": "clean", "files": files, "owner_queue": queue,
            **({"run": {k: run_state.get(k) for k in ("run_id", "status", "code") if run_state.get(k)}}
               if isinstance(run_state, dict) else {})}


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _free_main(args, parser) -> int:
    run = transport = None
    try:
        if args.run_id and args.bank is not None and not (args.smoke or args.list_catalog):
            check_public_bank(args.bank)
            run = args.bank = prepare_run(args.bank, args.run_id)
        transport = build_free_transport(args.backend, max_tokens=args.max_tokens, spend_cap_usd=args.max_usd)
        if args.list_catalog:
            assessed = assess_catalog(transport.catalog(), args.backend)
            # حدودُ الجرد من الموضع الواحد (ملاحظة Codex على #174): الهويةُ معرّفُ الفهرس، والعائلةُ مستنتجة، والفهرسُ لحظةٌ واحدة
            limits = free_limits(CATALOG_LIMITS, args.backend)
            _write_json(args.list_catalog, {"schema_version": 1, "backend": transport.describe(), **assessed,
                                            "provider_usage": transport.provider_usage, "measurement_limits": limits})
            eligible: dict[str, list[str]] = {}
            for row in assessed["rows"]:
                if row["eligible"]:
                    eligible.setdefault(row["family"], []).append(row["id"])
            print(json.dumps({"status": "listed", "backend": transport.describe(), "shape": transport.catalog_shape,
                              "models": assessed["models"],
                              "eligible": eligible, "refused": assessed["refused"],
                              "candidates": [c["model"] for c in assessed["candidates"][:len(PREFERRED_FAMILIES)]],
                              "provider_usage": transport.provider_usage, "measurement_limits": limits},
                             ensure_ascii=False))
            return 0
        if args.smoke:
            report, code = _free_smoke(args, transport)
            _write_json(args.smoke, report)
            digest = report.get("models") or _smoke_digest(report)
            print(json.dumps({"status": report["status"], **({"code": report["code"]} if "code" in report else {}),
                              "backend": report["backend"], "candidates_from": report["candidates_from"],
                              **({"catalog_error": report["catalog_error"]} if "catalog_error" in report else {}),
                              **({"unavailable_codes": report["unavailable_codes"]}
                                 if "unavailable_codes" in report else {}),
                              "last_failure_shapes": report["last_failure_shapes"],
                              "measurement_limits": report["measurement_limits"],
                              "reviewers": digest,
                              "fallbacks": report.get("fallbacks", []), "out": str(args.smoke)},
                             ensure_ascii=False))
            return code
        if args.bank is None:
            parser.error("مجلّد البنك مطلوب، أو --smoke، أو --list-catalog")
        result, code = _free_bank(args, transport)
    except AutomaticReviewError as exc:
        # ما خرج من نداءاتٍ قبل الرفض (ومنه نداءُ الفهرس الفاشل) يبقى في المطبوع وفي RUN.json (ملاحظة Codex على #290)
        usage = getattr(transport, "provider_usage", None) or []
        cap = _spend_report(transport).get("spend_cap")
        # وفي السجلّ الدائم للبنك يكتبه `_free_bank` على أيّ خروجٍ بعد الإرسال (ملاحظتا Codex على #298)
        if run is not None:          # خرج قبل الخلاصة: يُرفع سجلُّ الرفض المسمّى وحده، لا ملفّاتٌ تاريخية
            finish_run(run, "refused", exc.code, usage, cap if usage else None)
        shape = getattr(exc, "shape", None)
        print(json.dumps({"status": "refused", "code": exc.code, **({"shape": shape} if shape else {}),
                          **({"provider_usage": usage} if usage else {}),
                          **({"spend_cap": cap} if cap and usage else {})}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("bank", type=Path, nargs="?", help="مجلّد البنك، وفيه open/")
    parser.add_argument("--smoke", type=Path, metavar="OUT.json",
                        help="تجربةٌ حيّة على ثلاث حالاتٍ مصطنعة فيها خطأٌ مزروع، "
                             "ويُكتب تقريرها في OUT.json")
    parser.add_argument("--reviewer", action="append", dest="reviewers",
                        help="نموذجُ مراجعٍ (يتكرّر)؛ وعلى الواجهات المجانية معرّفُه في الفهرس «ناشر/اسم»")
    parser.add_argument("--brief", type=Path, default=ROOT / "docs" / "REVIEWER-BRIEF.md")
    parser.add_argument("--backend", choices=("ollama", *BACKENDS), default="ollama",
                        help="ollama (الافتراضي)، أو إحدى الواجهات المسماة في BACKENDS بمفتاحها من البيئة")
    parser.add_argument("--base-url", default=None,
                        help=f"لـOllama وحده: الخادمُ المحلي، أو {CLOUD_ENDPOINT} بمفتاح {CLOUD_KEY_ENV} من البيئة")
    parser.add_argument("--fallback", action="append", dest="fallbacks",
                        help="مرشّحٌ احتياطيّ من عائلةٍ أخرى إن نفدت حصّةُ مراجع (يتكرّر؛ للواجهات المجانية)")
    parser.add_argument("--list-catalog", type=Path, metavar="OUT.json",
                        help="يكتب فهرسَ الواجهة المجانية وحكمَ كل نموذجٍ فيه (صالحٌ بعائلته أو مرفوضٌ برمزه)")
    parser.add_argument("--every-family", action="store_true",
                        help="مع --smoke على واجهةٍ مجانية: يجرّب أفضلَ نموذجٍ من كل عائلةٍ مسموحة، أزواجًا")
    parser.add_argument("--max-usd", type=Decimal, default=None, metavar="USD",
                        help="سقفُ إنفاق التشغيل على موجّه HF المدفوع، لازمٌ معه ولا يتجاوز HFD2 (‎$20)؛ ولا يُقبل مع غيره")
    parser.add_argument("--max-tokens", type=int, default=4000,
                        help="حدُّ مخرج الردّ على الواجهات المجانية")
    parser.add_argument("--check-artifact", type=Path, metavar="REVIEWS_DIR",
                        help="يفحص سجلّاتِ المراجعة قبل رفعها أثرًا (لا محجوب ولا رمز) ويطبع عددَها وطولَ قائمة المالك")
    parser.add_argument("--run-id", default=None,
                        help="لمراجعة البنك على واجهةٍ مجانية: تُكتب في <البنك>/runs/<run-id>/ نظيفًا، ويحمل كلُّ سجلٍّ معرّفَه؛ "
                             "ومع --check-artifact يُرفض ما لا يحمله")
    parser.add_argument("--artifact-name", default=None,
                        help="اسمُ الأثر الذي تُرفع فيه السجلّات، يُروى في الخلاصة ليعرف المالكُ أين يحكم")
    args = parser.parse_args(argv)
    if args.check_artifact:
        try:
            checked = check_artifact(args.check_artifact, run_id=args.run_id)
        except AutomaticReviewError as exc:
            print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
            return 2
        print(json.dumps({**checked, **({"artifact": args.artifact_name} if args.artifact_name else {})},
                         ensure_ascii=False))
        return 0
    if args.backend != "ollama":
        if args.base_url is not None:
            print(json.dumps({"status": "refused", "code": "base_url_is_ollama_only"}, ensure_ascii=False))
            return 2
        return _free_main(args, parser)
    if args.list_catalog or args.every_family or args.fallbacks or args.run_id or args.max_usd is not None:
        print(json.dumps({"status": "refused", "code": "free_backend_option_without_free_backend"},
                         ensure_ascii=False))
        return 2
    base_url = args.base_url or "http://127.0.0.1:11434"
    reviewers = args.reviewers or list(DEFAULT_REVIEWERS)
    transport = None
    if args.smoke:
        try:
            transport = build_transport(base_url)
            with tempfile.TemporaryDirectory() as tmp:
                report = smoke(Path(tmp), reviewers, transport, brief_path=args.brief)
        except AutomaticReviewError as exc:
            usage = getattr(transport, "provider_usage", None) or []
            print(json.dumps({"status": "refused", "code": exc.code,
                              **({"provider_usage": usage} if usage else {})}, ensure_ascii=False))
            return 2
        # سجلُّ النداءات وتوكناتُها في دليل الدخان كما في الواجهات المجانية (جديد-spend-ledger)
        report.update(provider_usage=getattr(transport, "provider_usage", []), **_spend_report(transport))
        args.smoke.parent.mkdir(parents=True, exist_ok=True)
        args.smoke.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
        print(json.dumps({"status": report["status"], "reviewers": report["reviewers"],
                          "out": str(args.smoke)}, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1
    if args.bank is None:
        parser.error("مجلّد البنك مطلوب، أو --smoke")
    # ودليلُ مجانيةٍ حفظته الواجهاتُ المجانية على البنك نفسِه يبقى مع نداءاته في السجلّ والخلاصة (ملاحظة Codex على #298)
    prior, prior_evidence = prior_provider_usage(args.bank), prior_zero_spend_evidence(args.bank)
    try:
        transport = build_transport(base_url)
        try:
            counts = review_bank(args.bank, reviewers, transport, brief_path=args.brief)
        finally:
            # ما أُرسل يبقى في السجلّ الدائم للبنك على أيّ خروج: نجاحٍ، أو رفضٍ، أو سجلِّ مراجعةٍ تالفٍ يُسقط قراءتَه.
            # فيُكتب قبل أن تُقرأ سجلّاتُ المراجعة في الخلاصة (ملاحظتا Codex على #298)
            usage = list(getattr(transport, "provider_usage", []))
            if usage:
                write_ledger(args.bank, prior + usage, prior_evidence)
        summary = summarize(args.bank)
    except AutomaticReviewError as exc:
        usage = getattr(transport, "provider_usage", None) or []
        print(json.dumps({"status": "refused", "code": exc.code, "detail": str(exc),
                          **({"provider_usage": usage} if usage else {})}, ensure_ascii=False))
        return 2
    # السجلُّ يُلحَق بما كتبته التشغيلاتُ السابقة ولا يستبدله، والمجموعُ من السجلّ كلِّه (ملاحظة Codex على #298). وعدُّ نداءات
    # OpenRouter التي لم تثبت كلفتُها من السجلّ كلِّه كذلك، فلا تُسقط خلاصةُ Ollama عددًا كتبته الواجهاتُ المجانية (#295)
    ledger = prior + list(getattr(transport, "provider_usage", []))
    _persist_provider_usage(args.bank, ledger, {"token_totals": token_totals(ledger),
                                                "cost_unconfirmed_attempts": cost_unconfirmed_attempts(ledger),
                                                **({"zero_spend_evidence": prior_evidence} if prior_evidence else {})})
    print(json.dumps({
        "status": "failed" if counts["failed"] else "reviewed",
        **counts,
        "pairs": summary["pairs"],
        "errors": len(summary["errors"]),
        "owner_queue": len(summary["owner_queue"]),
        "summary": str(args.bank / "reviews" / "SUMMARY.json"),
    }, ensure_ascii=False, indent=2))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
