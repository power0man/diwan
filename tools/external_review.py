#!/usr/bin/env python3
"""تشغيل المراجعة الخارجية لبنك قياس عبر Ollama أو الواجهات المجانية المتوافقة مع OpenAI (ق٥٠، ق٦٠، #168).

    python3 tools/external_review.py evaluation/banks/kimi_v1
    python3 tools/external_review.py evaluation/banks/kimi_v1 \\
        --reviewer deepseek-v4-flash:cloud --reviewer mistral-large-3:675b-cloud
    OLLAMA_API_KEY=… python3 tools/external_review.py --base-url https://ollama.com --smoke out.json
    GITHUB_TOKEN=… python3 tools/external_review.py --backend github-models --list-catalog catalog.json
    GITHUB_TOKEN=… python3 tools/external_review.py --backend github-models --smoke out.json
    HF_TOKEN=… python3 tools/external_review.py --backend hf-router --smoke out.json --every-family

**Ollama** (`--backend ollama`، الافتراضيّ): نقطتان لا ثالثَ لهما: خادمُ Ollama المحليّ (127.0.0.1، بلا وكيلٍ ولا
تحويل) يمرّر النداء إلى النماذج السحابية كما على الماك؛ أو واجهةُ `https://ollama.com` مباشرةً بمفتاحٍ يُقرأ من
البيئة `OLLAMA_API_KEY` وحدها — وهي طريقُ الجلسات السحابية التي لا خادمَ فيها.

**الواجهاتُ المجانية** (قرار المالك في ٢٨ سبتمبر ٢٠٢٦ (#168)، يمدّ ق٦٠ إلى نقطتين أخريين):
`--backend github-models` (`models.github.ai` برمز `GITHUB_TOKEN`؛ في Actions رمزُ المهمّة نفسُه بصلاحية
`models: read`، بلا سرٍّ جديد) و`--backend hf-router` (`router.huggingface.co` برمز `HF_TOKEN`). والمفتاحُ من
البيئة وحدها: لا خيارَ له في سطر الأوامر، ولا يُطبع، ولا يُكتب، ولا يُرسل عند التحويل (التحويلُ مرفوض).
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
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.external_review import (AUTHOR_FAMILY, DEFAULT_REVIEWERS,  # noqa: E402
                                        DEVELOPER_FAMILIES, ENGINE_FAMILY, _slug,
                                        review_bank, smoke, summarize)
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


# ————— الواجهاتُ المجانية المتوافقة مع OpenAI (قرار المالك في ٢٨ سبتمبر ٢٠٢٦ (#168)) —————

# قائمةُ النقاط الصريحة: ollama.com (ق٦٠) والنقطتان اللتان يمدّها إليهما قرارُ المالك. https وحدها، بلا منفذٍ ولا هوية.
ALLOWED_HOSTS = ("ollama.com", "models.github.ai", "router.huggingface.co")

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

FREE_LIMITS = (
    "model_identity_is_the_provider_catalog_id_not_a_verified_weight_digest",
    "family_is_derived_from_publisher_and_name_not_attested_by_the_provider",
    "free_tier_rate_limits_and_input_caps_apply",
)


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


def resolve_reviewer(model: str) -> dict:
    """{model, publisher, family, lineage} لمراجعٍ على واجهةٍ مجانية، أو رفضٌ مسمًّى.

    العائلةُ من جدول الناشرين، والاسمُ يجب أن يقول العائلةَ نفسها (الجدولُ الواحد في `multi_system_review`)،
    وكلُّ مقطعٍ في الاسم يدخل السلالة: فالمقطَّرُ من Qwen مرفوضٌ ولو نشرته DeepSeek.
    """
    publisher, sep, name = model.partition("/")
    family = PUBLISHER_FAMILIES.get(publisher.lower()) if sep and name else None
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


class OpenAICompatChat:
    """نداءُ chat/completions المتوافق مع OpenAI على نقطةٍ مسموحة، بمفتاحٍ من البيئة لا يُكتب ولا يُطبع.

    المفتاحُ يُحمل في ترويسةٍ لا تُعاد عند التحويل (والتحويلُ مرفوضٌ أصلًا)، ولا يدخل خطأً ولا تمثيلًا للكائن.
    """

    def __init__(self, backend: str, api_key: str | None, *, chat_url: str | None = None,
                 catalog_url: str | None = None, timeout: int = 300, max_tokens: int = 4000):
        if backend not in BACKENDS:
            raise AutomaticReviewError("backend_unknown", backend)
        spec = BACKENDS[backend]
        self.backend = backend
        self.chat_url = allowed_endpoint(chat_url or spec["chat_url"])
        self.catalog_url = allowed_endpoint(catalog_url or spec["catalog_url"])
        self.key_env = spec["key_env"]
        if not api_key:
            raise AutomaticReviewError("key_missing", f"{self.key_env} غيرُ مضبوطٍ في البيئة")
        self.__key = api_key
        self._headers = {"Content-Type": "application/json", "User-Agent": "diwan-external-review",
                         **spec["headers"]}
        self.timeout, self.max_tokens = timeout, max_tokens
        self.catalog_shape: dict | None = None
        self.failures: dict[str, dict] = {}     # آخرُ شكلٍ فاشل لكل نموذجٍ (وللفهرس): لا نصَّ فيه
        self.opener = urllib.request.build_opener(_RefuseRedirect())

    def __repr__(self) -> str:
        return f"OpenAICompatChat({self.backend!r}, host={self.endpoint_host!r})"

    @property
    def endpoint_host(self) -> str:
        return urllib.parse.urlsplit(self.chat_url).hostname

    def describe(self) -> dict:
        return {"name": self.backend, "endpoint_host": self.endpoint_host, "key_env": self.key_env}

    def _send(self, url: str, payload: dict | None, label: str) -> tuple[bytes, str | None, int | None]:
        """(البايتات، نوعُ المحتوى، حالةُ HTTP). والخطأُ يحمل رمزَ حالته المسمّى وشكلَ جسمه (`shape`) لا نصَّه."""
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="GET" if data is None else "POST")
        for name, value in self._headers.items():
            request.add_header(name, value)
        request.add_unredirected_header("Authorization", f"Bearer {self.__key}")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                headers = getattr(response, "headers", None)
                content_type = headers.get("Content-Type") if headers is not None else None
                status = getattr(response, "status", None)
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(65536)
            except OSError:
                body = b""
            _, self.failures[label] = response_shape(
                exc.code, body, exc.headers.get("Content-Type") if exc.headers is not None else None)
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
        # المخطّطُ موصوفٌ في التكليف نفسِه («JSON فقط»)، ولا يُرسل حقلَ response_format لأن نماذجَ في الفهرس تردّه 400
        payload = {"model": model, "stream": False, "temperature": 0, "max_tokens": self.max_tokens,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        raw, content_type, status = self._send(self.chat_url, payload, model)
        body, shape = response_shape(status, raw, content_type)
        if shape["top"] == "not_json":
            self.failures[model] = shape
            raise AutomaticReviewError("response_not_json", model)
        try:
            choice = body["choices"][0]
            content = choice["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            self.failures[model] = shape
            raise AutomaticReviewError("openai_response_malformed", model) from None
        if isinstance(content, list):          # أجزاءُ نصٍّ عند بعض المزوّدين
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise AutomaticReviewError("reply_empty", model)
        if choice.get("finish_reason") == "length":
            raise AutomaticReviewError("reply_incomplete", model)
        return content

    def catalog(self) -> list[dict]:
        """الفهرسُ مطبَّعًا: {id, chat, reason, tier}. GitHub يردّ قائمةً في أعلاه، والموجّه {data: [...]}.

        وما سواهما `catalog_malformed` ومعه شكلُه وحده (`catalog_shape`) لا نصُّه: قيس في ٢٨ سبتمبر ٢٠٢٦ أنّ
        models.github.ai يردّ `OK` نصًّا عاديًّا بأربعة بايتات لأيّ مسارٍ بلا تفويض، فكان الرمزُ وحده لا يفرّق بينه وبين غلافٍ مجهول.
        """
        raw, content_type, status = self._send(self.catalog_url, None, "catalog")
        entries, self.catalog_shape = catalog_shape(raw, content_type, status)
        if entries is None:
            self.failures["catalog"] = self.catalog_shape
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
            "tier": entry.get("rate_limit_tier")}


def build_free_transport(backend: str, environ=os.environ, **kw) -> OpenAICompatChat:
    """النقلُ المجانيّ؛ والمفتاحُ من البيئة وحدها (لا خيارَ له في سطر الأوامر)."""
    if backend not in BACKENDS:
        raise AutomaticReviewError("backend_unknown", backend)
    return OpenAICompatChat(backend, environ.get(BACKENDS[backend]["key_env"]) or None, **kw)


def assess_catalog(entries: list[dict], backend: str) -> dict:
    """كلُّ نموذجٍ في الفهرس: صالحٌ بعائلته، أو مرفوضٌ برمزه؛ والمرشّحون مرتَّبون بالعائلات المفضَّلة."""
    rows = []
    for index, entry in enumerate(entries):
        row = {"id": entry["id"], "tier": entry.get("tier"), "family": None, "lineage": None,
               "eligible": False, "code": entry.get("reason")}
        if entry.get("chat", True):
            try:
                identity = resolve_reviewer(entry["id"])
                row.update(family=identity["family"], lineage=identity["lineage"], eligible=True)
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


def _quota_models(bank: Path, models: list[str]) -> set[str]:
    """النماذجُ التي سُجّل لها في هذا البنك ملفٌّ خطؤه نفادُ الحصّة."""
    spent = set()
    for model in models:
        for path in (bank / "reviews" / _slug(model)).rglob("*.json"):
            if json.loads(path.read_text(encoding="utf-8")).get("error") == "quota_exhausted":
                spent.add(model)
                break
    return spent


def with_fallback(candidates: list[dict], want: int, run: Callable[[list[dict]], set[str]]) -> tuple:
    """يشغّل `run` على المختارين؛ ومن نفدت حصّتُه يُستبدل بمرشّحٍ من عائلةٍ أخرى حتى لا يبقى بديل.

    يُعيد (المختارين الأخيرين، سجلَّ الاستبدال، رمزَ الإخفاق أو None).
    """
    exhausted: list[dict] = []
    fallbacks: list[dict] = []
    chosen = choose_reviewers(candidates, want)
    while True:
        spent = run(chosen)
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
        chosen = replacement


def _free_candidates(args, transport: OpenAICompatChat) -> tuple[list[dict], dict]:
    """(المرشّحون، مصدرُهم): `--reviewer` ثم `--fallback` كما أُعطيت بصرامة، أو من الفهرس بترتيب العائلات المفضَّلة.

    والفهرسُ الذي لا يُقرأ لا يُسقط التجربة صامتًا ولا يوقفها: المرشّحون من قائمة الواجهة المفضَّلة، ورمزُ الفهرس وشكلُه في التقرير.
    """
    if args.reviewers:
        explicit = [resolve_reviewer(m) for m in args.reviewers]
        check_distinct(explicit)
        return explicit + [resolve_reviewer(m) for m in (args.fallbacks or [])], {"candidates_from": "explicit"}
    if args.fallbacks:
        raise AutomaticReviewError("fallback_without_reviewers")
    try:
        candidates = assess_catalog(transport.catalog(), args.backend)["candidates"]
    except AutomaticReviewError as exc:          # الفهرسُ غيرُ مقروء: القائمةُ المفضَّلة، والسببُ مسمًّى
        preferred = [resolve_reviewer(model) for family in PREFERRED_FAMILIES
                     for model in BACKENDS[args.backend]["preferred"][family]]
        return preferred, {"candidates_from": "preferred_list", "catalog_error": exc.code,
                           "catalog_shape": getattr(exc, "shape", None)}
    return candidates, {"candidates_from": "catalog", "catalog_shape": transport.catalog_shape}


def _smoke_digest(report: dict) -> dict:
    return {model: {k: r[k] for k in ("family", "error", "elapsed_ms", "caught_planted_error", "false_flags")}
            for model, r in report["reviewers"].items()}


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
        with tempfile.TemporaryDirectory() as tmp:
            for index, pair in enumerate(pairs):
                runs.append(smoke(Path(tmp) / f"pair_{index}", [p["model"] for p in pair], transport,
                                  brief_path=args.brief))
        models = {}
        for run in runs:
            for model, result in run["reviewers"].items():
                models.setdefault(model, {**result, "lineage": identities[model]["lineage"],
                                          "reachable": result["error"] not in {"transport_error",
                                                                               "transport_timeout"}})
        report = {"schema_version": 1, "probe": "external_review_smoke_every_family",
                  "status": "passed" if all(r["status"] == "passed" for r in runs) else "failed",
                  "backend": transport.describe(), **source, "models": models,
                  "pairs": [[p["model"] for p in pair] for pair in pairs],
                  "runs": runs, "measurement_limits": sorted(set(runs[0]["measurement_limits"]) | set(FREE_LIMITS)),
                  "last_failure_shapes": transport.failures}
        _mark_unavailable(report, [m["error"] for m in models.values()])
        return report, EXIT_CODES.get(report["status"], 1)
    tmp = tempfile.TemporaryDirectory()
    try:
        reports: list[dict] = []

        def run(chosen: list[dict]) -> set[str]:
            report = smoke(Path(tmp.name), [c["model"] for c in chosen], transport, brief_path=args.brief)
            reports.append(report)
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
    report["measurement_limits"] = sorted(set(report["measurement_limits"]) | set(FREE_LIMITS))
    report["last_failure_shapes"] = transport.failures
    if failure:
        report["status"], report["code"] = "failed", failure
    _mark_unavailable(report, [r["error"] for r in report["reviewers"].values()])
    return report, EXIT_CODES.get(report["status"], 1)


def check_public_bank(bank: Path, root: Path | None = None) -> None:
    """على الواجهات المجانية لا يُرسل إلا بنكٌ تحت `evaluation/banks/` (لا ملفّاتُ المالك ولا ما خارج المستودع)."""
    try:
        bank.resolve().relative_to(((root or ROOT) / "evaluation" / "banks").resolve())
    except ValueError:
        raise AutomaticReviewError("bank_outside_evaluation_banks", str(bank)) from None


def _free_bank(args, transport: OpenAICompatChat) -> tuple[dict, int]:
    check_public_bank(args.bank)
    candidates, source = _free_candidates(args, transport)
    want = max(2, len(args.reviewers or []))
    totals = {"reviewed": 0, "skipped": 0, "failed": 0}

    def run(chosen: list[dict]) -> set[str]:
        models = [c["model"] for c in chosen]
        counts = review_bank(args.bank, models, transport, brief_path=args.brief)
        for key in totals:
            totals[key] += counts[key]
        return _quota_models(args.bank, models)

    chosen, fallbacks, failure = with_fallback(candidates, want, run)
    summary = summarize(args.bank)
    status = "failed" if failure or summary["errors"] else "reviewed"
    result = {"status": status, **({"code": failure} if failure else {}), **totals,
              "backend": transport.describe(), **source,
              "reviewers": {c["model"]: c["family"] for c in chosen}, "fallbacks": fallbacks,
              "pairs": summary["pairs"], "errors": len(summary["errors"]),
              "error_codes": sorted({e["error"] for e in summary["errors"]}),
              "owner_queue": len(summary["owner_queue"]), "last_failure_shapes": transport.failures,
              "summary": str(args.bank / "reviews" / "SUMMARY.json")}
    if not totals["reviewed"] and not totals["skipped"]:        # لم يُجب نموذجٌ واحد في هذا التشغيل
        chosen_models = {c["model"] for c in chosen}
        _mark_unavailable(result, [e["error"] for e in summary["errors"] if e["model"] in chosen_models])
    return result, EXIT_CODES.get(result["status"], 1)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _free_main(args, parser) -> int:
    try:
        transport = build_free_transport(args.backend, max_tokens=args.max_tokens)
        if args.list_catalog:
            assessed = assess_catalog(transport.catalog(), args.backend)
            _write_json(args.list_catalog, {"schema_version": 1, "backend": transport.describe(), **assessed})
            eligible: dict[str, list[str]] = {}
            for row in assessed["rows"]:
                if row["eligible"]:
                    eligible.setdefault(row["family"], []).append(row["id"])
            print(json.dumps({"status": "listed", "backend": transport.describe(), "shape": transport.catalog_shape,
                              "models": assessed["models"],
                              "eligible": eligible, "refused": assessed["refused"],
                              "candidates": [c["model"] for c in assessed["candidates"][:len(PREFERRED_FAMILIES)]]},
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
                              "reviewers": digest,
                              "fallbacks": report.get("fallbacks", []), "out": str(args.smoke)},
                             ensure_ascii=False))
            return code
        if args.bank is None:
            parser.error("مجلّد البنك مطلوب، أو --smoke، أو --list-catalog")
        result, code = _free_bank(args, transport)
    except AutomaticReviewError as exc:
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
                        help="ollama (الافتراضي)، أو github-models بـGITHUB_TOKEN، أو hf-router بـHF_TOKEN من البيئة")
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
    args = parser.parse_args(argv)
    if args.backend != "ollama":
        if args.base_url is not None:
            print(json.dumps({"status": "refused", "code": "base_url_is_ollama_only"}, ensure_ascii=False))
            return 2
        return _free_main(args, parser)
    if args.list_catalog or args.every_family or args.fallbacks:
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
