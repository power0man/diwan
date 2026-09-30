#!/usr/bin/env python3
"""تشغيل المراجعة الخارجية لبنك قياس عبر Ollama أو الواجهات المجانية المتوافقة مع OpenAI (ق٥٠، ق٦٠، #168).

    python3 tools/external_review.py evaluation/banks/kimi_v1
    python3 tools/external_review.py evaluation/banks/kimi_v1 \\
        --reviewer deepseek-v4-flash:cloud --reviewer mistral-large-3:675b-cloud
    OLLAMA_API_KEY=… python3 tools/external_review.py --base-url https://ollama.com --smoke out.json
    GITHUB_TOKEN=… python3 tools/external_review.py --backend github-models --list-catalog catalog.json
    GITHUB_TOKEN=… python3 tools/external_review.py --backend github-models --smoke out.json
    HF_TOKEN=… python3 tools/external_review.py --backend hf-router --smoke out.json --every-family
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
- **نفادُ الحصّة** (`quota_exhausted`، HTTP 429 أو 402) يُستبدل فيه بالمراجع مرشّحٌ من عائلةٍ أخرى مسموحة، وإلا
  فالإخفاقُ المسمّى `quota_exhausted_no_fallback`.
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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.external_review import (AUTHOR_FAMILY, BUDGET_TERMINAL_ERRORS, DEFAULT_REVIEWERS,  # noqa: E402
                                        DEVELOPER_FAMILIES, ENGINE_FAMILY, SUPERSEDED_DIR,
                                        _slug, open_files, review_bank, smoke, summarize)
from evaluation.multi_system_review import AutomaticReviewError, model_family  # noqa: E402

MAX_RESPONSE_BYTES = 8_000_000
LOCAL_PREFIXES = ("http://127.0.0.1:", "http://localhost:", "http://[::1]:")
CLOUD_ENDPOINT = "https://ollama.com"          # النقطةُ السحابية الوحيدة المسموحة (ق٦٠)
CLOUD_KEY_ENV = "OLLAMA_API_KEY"               # يُقرأ من البيئة وحدها، ولا يُطبع ولا يُودَع


class OllamaChat:
    """نداءُ /api/chat على خادمٍ محليّ (بلا وكيلٍ ولا تحويل)، أو على ollama.com بمفتاحٍ من البيئة.

    المفتاحُ لا يُحفظ إلا في ترويسة الطلب، ولا يظهر في أي خطأٍ أو تقرير: الأخطاءُ رموزٌ باسم النموذج.
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

    def __call__(self, model: str, system: str, user: str, schema: dict) -> str:
        payload = {"model": model, "stream": False, "format": schema,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}],
                   "options": {"temperature": 0, "seed": 0, "num_ctx": 65536}}
        request = urllib.request.Request(
            self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=dict(self._headers))
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise AutomaticReviewError(f"http_{exc.code}", model) from exc
        except TimeoutError as exc:
            raise AutomaticReviewError("transport_timeout", model) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AutomaticReviewError("transport_error", model) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AutomaticReviewError("response_too_large", model)
        try:
            content = json.loads(raw)["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise AutomaticReviewError("ollama_response_malformed", model) from exc
        if not isinstance(content, str):
            raise AutomaticReviewError("ollama_response_malformed", model)
        return content


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


def free_limits(base, backend: str | None = None) -> list[str]:
    """حدودُ القياس على الواجهات المجانية من موضعٍ واحد، للتجربة وللبنك وللخلاصة المحفوظة (ملاحظة Codex على #174)."""
    extra = ZERO_SPEND_LIMITS if backend in {"groq", "openrouter"} else ()
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
    if status in (402, 429):
        return "quota_exhausted"          # 429 حدُّ الطلبات، و402 نفادُ رصيد الموجّه
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
    if not isinstance(pricing, dict) or not pricing:
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
        self.failures: dict[str, dict] = {}     # آخرُ شكلٍ فاشل لكل نموذجٍ (وللفهرس): لا نصَّ فيه
        self.last_request: dict[str, dict] = {}  # الطريقةُ والرابطُ المرسَل والنهائيّ (bare_url) لكل نموذجٍ وللفهرس
        self.provider_usage: list[dict] = []     # سجلٌّ مأمون لكل محاولة نموذج: لا رسالةَ ولا مفتاح ولا نصَّ رد
        self.zero_spend_proofs: dict[str, str] = {}
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
        for model in models:
            if model not in by_id:
                raise AutomaticReviewError("free_price_unverified", model)
            self.zero_spend_proofs[model] = openrouter_zero_spend(by_id[model])

    def _guard_zero_spend(self, model: str) -> str | None:
        if self.backend == "groq":
            return self.zero_spend_proofs.get("*")
        if self.backend == "openrouter":
            proof = self.zero_spend_proofs.get(model)
            if proof is None:
                raise AutomaticReviewError("free_price_unverified", model)
            return proof
        return None

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
            except OSError:
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
        except (urllib.error.URLError, OSError):
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
            payload = {"model": model, "stream": False, "temperature": 0, "max_tokens": self.max_tokens,
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if self.backend == "openrouter":
                # لا نماذجَ بديلة ولا انتقالَ مدفوعًا، واطلب الكلفة الفعلية في الردّ حتى لا تُخمَّن صفرًا.
                payload.update(provider={"allow_fallbacks": False}, usage={"include": True})
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
        raw, content_type, status = self._send(self.catalog_url, None, "catalog")
        entries, self.catalog_shape = catalog_shape(raw, content_type, status)
        if entries is None:
            self.failures["catalog"] = {**self.catalog_shape, "request": self.last_request["catalog"]}
            error = AutomaticReviewError("catalog_malformed", self.backend)
            error.shape = self.catalog_shape
            raise error
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
            "tier": entry.get("rate_limit_tier"), "pricing": entry.get("pricing")}


def build_free_transport(backend: str, environ=os.environ, **kw) -> OpenAICompatChat:
    """النقلُ المجانيّ؛ والمفتاحُ من البيئة وحدها (لا خيارَ له في سطر الأوامر)."""
    if backend not in BACKENDS:
        raise AutomaticReviewError("backend_unknown", backend)
    spec = BACKENDS[backend]
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
                  "provider_usage": transport.provider_usage,
                  "last_failure_shapes": transport.failures}
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
    report["last_failure_shapes"] = transport.failures
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
        if path.name == "SUMMARY.json" or path.relative_to(root).parts[0] == SUPERSEDED_DIR:
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
        if path.name in ("SUMMARY.json", RUN_FILE) or relative.parts[0] == SUPERSEDED_DIR:
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
    # ملفّاتُ open/ الحالية: كلُّ قراءةٍ لسجلّات المراجعة بعدها تُقارن بها، فسجلُّ ملفٍّ حُذف أو أُعيدت تسميتُه منذ تشغيلٍ سابق
    # لا يدخل النفادَ ولا الاتفاقَ ولا قائمةَ المالك ولا الأعداد ولا التاريخ (ملاحظة Codex على #174)
    current = {path.relative_to(args.bank / "open").as_posix() for path in open_files(args.bank)}
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
    # النجاحُ والاتفاقُ وقائمةُ المالك من المجموعة الأخيرة وحدها (ملاحظتا Codex على #174): المستبدَلُ نُقلت سجلّاتُه إلى
    # reviews/superseded/ وراجع بديلُه البنكَ كلَّه؛ ونفادُ حصّته تاريخٌ مسمًّى (superseded ومعه البديل) لا خطأٌ يُسقط التشغيل،
    # فإن أخفق البديلُ أيضًا عُدّ الخطآن كلاهما. وأخطاءُ مراجعين خارج هذا التشغيل لا تُحسب عليه، وتُروى عددًا.
    final_models = {c["model"] for c in chosen}
    summary = summarize(args.bank, reviewers=final_models, files=current)
    replaced_by = {model: entry["replacement"] for entry in fallbacks for model in entry["exhausted"]
                   if entry["replacement"] is not None}
    final_errors = summary["errors"]
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
    _persist_provider_usage(args.bank, transport.provider_usage)
    if args.run_id:          # كلُّ سجلٍّ وخلاصةٍ في مجلّد هذا التشغيل يحمل معرّفَه، فيُرفض عند الرفع ما لا يحمله
        stamp_run(args.bank / "reviews", args.run_id)
    final_counts = final_set_counts(args.bank, final_models, pre_existing, current)
    result = {"status": status, **({"code": failure} if failure else {}), **final_counts,
              "attempts": attempts, **({"run_id": args.run_id} if args.run_id else {}),
              "measurement_limits": limits,
              "provider_usage": transport.provider_usage,
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


def _persist_provider_usage(bank: Path, usage: list[dict]) -> None:
    """سجلُّ النداءات في خلاصة التشغيل نفسها؛ لا يُكتب صفرٌ لكلفة لم يبلغها المزوّد."""
    path = bank / "reviews" / "SUMMARY.json"
    if path.is_file():
        summary = json.loads(path.read_text(encoding="utf-8"))
        summary["provider_usage"] = usage
        _write_json(path, summary)


def finish_run(run: Path, status: str, code: str | None = None) -> None:
    """حالةُ التشغيل في RUN.json: reviewed أو failed أو unavailable أو refused برمزه — فإن خرج قبل الخلاصة بقي هذا وحده."""
    path = run / "reviews" / RUN_FILE
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(status=status, finished_at=_utc_now(), **({"code": code} if code else {}))
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
    run = None
    try:
        if args.run_id and args.bank is not None and not (args.smoke or args.list_catalog):
            check_public_bank(args.bank)
            run = args.bank = prepare_run(args.bank, args.run_id)
        transport = build_free_transport(args.backend, max_tokens=args.max_tokens)
        if args.list_catalog:
            assessed = assess_catalog(transport.catalog(), args.backend)
            # حدودُ الجرد من الموضع الواحد (ملاحظة Codex على #174): الهويةُ معرّفُ الفهرس، والعائلةُ مستنتجة، والفهرسُ لحظةٌ واحدة
            limits = free_limits(CATALOG_LIMITS, args.backend)
            _write_json(args.list_catalog, {"schema_version": 1, "backend": transport.describe(), **assessed,
                                            "measurement_limits": limits})
            eligible: dict[str, list[str]] = {}
            for row in assessed["rows"]:
                if row["eligible"]:
                    eligible.setdefault(row["family"], []).append(row["id"])
            print(json.dumps({"status": "listed", "backend": transport.describe(), "shape": transport.catalog_shape,
                              "models": assessed["models"],
                              "eligible": eligible, "refused": assessed["refused"],
                              "candidates": [c["model"] for c in assessed["candidates"][:len(PREFERRED_FAMILIES)]],
                              "measurement_limits": limits},
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
        if run is not None:          # خرج قبل الخلاصة: يُرفع سجلُّ الرفض المسمّى وحده، لا ملفّاتٌ تاريخية
            finish_run(run, "refused", exc.code)
        shape = getattr(exc, "shape", None)
        print(json.dumps({"status": "refused", "code": exc.code, **({"shape": shape} if shape else {})},
                         ensure_ascii=False))
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
    if args.list_catalog or args.every_family or args.fallbacks or args.run_id:
        print(json.dumps({"status": "refused", "code": "free_backend_option_without_free_backend"},
                         ensure_ascii=False))
        return 2
    base_url = args.base_url or "http://127.0.0.1:11434"
    reviewers = args.reviewers or list(DEFAULT_REVIEWERS)
    if args.smoke:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                report = smoke(Path(tmp), reviewers, build_transport(base_url),
                               brief_path=args.brief)
        except AutomaticReviewError as exc:
            print(json.dumps({"status": "refused", "code": exc.code}, ensure_ascii=False))
            return 2
        args.smoke.parent.mkdir(parents=True, exist_ok=True)
        args.smoke.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
        print(json.dumps({"status": report["status"], "reviewers": report["reviewers"],
                          "out": str(args.smoke)}, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1
    if args.bank is None:
        parser.error("مجلّد البنك مطلوب، أو --smoke")
    try:
        counts = review_bank(args.bank, reviewers, build_transport(base_url),
                             brief_path=args.brief)
        summary = summarize(args.bank)
    except AutomaticReviewError as exc:
        print(json.dumps({"status": "refused", "code": exc.code, "detail": str(exc)},
                         ensure_ascii=False))
        return 2
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
