"""المراجعة الخارجية لبنوك القياس: تُختبر بمراجعَين مزيَّفين، بلا شبكة.

التشغيلُ الحيّ على الماك (AGENTS.md، المهمة ك٥). وهنا يُثبت ما يجب أن يصمد أيًّا كان
المراجع: لا يسقط ملفٌّ صمتًا، ولا يُرسل المحجوب، ولا يُقبل مراجعٌ من عائلةٍ ممنوعة،
وκ صحيحةٌ حسابًا.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from evaluation.external_review import (ENGINE_FAMILY, check_reviewers, cohen_kappa,
                                        review_bank, reviewer_family, summarize)
from evaluation.multi_system_review import AutomaticReviewError

ROOT = Path(__file__).resolve().parent.parent
BRIEF = ROOT / "docs" / "REVIEWER-BRIEF.md"
REVIEWERS = ["deepseek-v4-flash:cloud", "mistral-large-3:675b-cloud"]


def _case(case_id: str) -> dict:
    return {"case_id": case_id, "capability": "arithmetic",
            "messages": [{"role": "user", "content": "كم 2+2؟"}],
            "reference": "4", "rubric": ["الجواب 4"], "checks": [], "critical": False}


def _bank(tmp_path: Path, ids=("c1", "c2", "c3")) -> Path:
    bank = tmp_path / "bank"
    suite = {"schema_version": 1, "suite_id": "t", "split": "development",
             "description": "d", "cases": [_case(i) for i in ids]}
    path = bank / "open" / "tier_a" / "kimi_t_a_001.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
    (bank / "open" / "tier_a" / "kimi_t_a_001.meta.json").write_text("{}", encoding="utf-8")
    return bank


def _judgment(i, reference="correct", rubric="sufficient"):
    return {"id": i, "reference": reference, "rubric": rubric,
            "my_answer": "4", "reason": "حسبتُها", "fix": None}


class Fake:
    """يردّ لكل نموذجٍ بما كُتب له، ويعدّ النداءات."""

    def __init__(self, replies: dict[str, list]):
        self.replies = {m: list(r) for m, r in replies.items()}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, model, system, user, schema):
        self.calls.append((model, user))
        reply = self.replies[model].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply, ensure_ascii=False) if isinstance(reply, dict) else reply


def _ok(ids, **kw):
    return {"judgments": [_judgment(i, **kw) for i in ids]}


# ————— الطريق الصادق —————

def test_two_reviewers_are_called_independently_and_recorded(tmp_path):
    bank = _bank(tmp_path)
    fake = Fake({REVIEWERS[0]: [_ok(["c1", "c2", "c3"])],
                 REVIEWERS[1]: [_ok(["c1", "c2", "c3"])]})
    counts = review_bank(bank, REVIEWERS, fake, brief_path=BRIEF)
    assert counts == {"reviewed": 2, "skipped": 0, "failed": 0}
    assert [m for m, _ in fake.calls] == REVIEWERS, "نداءٌ لكل مراجعٍ لملفّ البنك وحده"
    for _model, user in fake.calls:
        assert json.loads(user)["suite_id"] == "t", "لا يُرسل إلا ملفُّ البنك نفسه"
    record = json.loads((bank / "reviews" / "deepseek-v4-flash_cloud" / "tier_a"
                         / "kimi_t_a_001.json").read_text(encoding="utf-8"))
    assert record["error"] is None and len(record["judgments"]) == 3
    assert record["family"] == "deepseek"


def test_the_brief_sent_is_the_reviewer_part_without_the_owner_note(tmp_path):
    bank = _bank(tmp_path)
    seen = []

    def spy(model, system, user, schema):
        seen.append(system)
        return json.dumps(_ok(["c1", "c2", "c3"]))
    review_bank(bank, REVIEWERS, spy, brief_path=BRIEF)
    assert seen[0].startswith("## من أنت")
    assert "للمالك" not in seen[0]


def test_a_second_run_does_not_call_again(tmp_path):
    bank = _bank(tmp_path)
    review_bank(bank, REVIEWERS, Fake({m: [_ok(["c1", "c2", "c3"])] for m in REVIEWERS}),
                brief_path=BRIEF)
    again = Fake({m: [] for m in REVIEWERS})
    counts = review_bank(bank, REVIEWERS, again, brief_path=BRIEF)
    assert counts == {"reviewed": 0, "skipped": 2, "failed": 0} and again.calls == []


# ————— لا يسقط ملفٌّ صمتًا —————

def test_a_reply_missing_an_id_is_retried_once_then_recorded_as_error(tmp_path):
    bank = _bank(tmp_path)
    partial = _ok(["c1", "c2"])
    fake = Fake({REVIEWERS[0]: [partial, partial], REVIEWERS[1]: [_ok(["c1", "c2", "c3"])]})
    counts = review_bank(bank, REVIEWERS, fake, brief_path=BRIEF)
    assert counts["failed"] == 1
    record = json.loads((bank / "reviews" / "deepseek-v4-flash_cloud" / "tier_a"
                         / "kimi_t_a_001.json").read_text(encoding="utf-8"))
    assert record["judgments"] is None, "لا يُكتب حكمٌ ناقص"
    assert record["error"] == "judgment_ids_mismatch"
    assert len(record["attempts"]) == 2
    assert "judgment_ids_mismatch" in fake.calls[1][1], "المحاولة الثانية تحمل رمز الرفض"


def test_a_retry_that_succeeds_is_accepted(tmp_path):
    bank = _bank(tmp_path)
    fake = Fake({REVIEWERS[0]: ["ليس JSON", _ok(["c1", "c2", "c3"])],
                 REVIEWERS[1]: [_ok(["c1", "c2", "c3"])]})
    assert review_bank(bank, REVIEWERS, fake, brief_path=BRIEF)["failed"] == 0


@pytest.mark.parametrize("judgments", [
    [_judgment("c1"), _judgment("c2"), _judgment("c3"), _judgment("c4")],   # زائد
    [_judgment("c1"), _judgment("c1"), _judgment("c2"), _judgment("c3")],   # مكرّر
    [_judgment("c1"), _judgment("c2"), _judgment("c3", reference="maybe")],  # قيمة
    [{**_judgment("c1"), "reason": " "}, _judgment("c2"), _judgment("c3")],  # سببٌ فارغ
])
def test_malformed_judgments_are_refused(tmp_path, judgments):
    bank = _bank(tmp_path)
    bad = {"judgments": judgments}
    fake = Fake({REVIEWERS[0]: [bad, bad], REVIEWERS[1]: [_ok(["c1", "c2", "c3"])]})
    assert review_bank(bank, REVIEWERS, fake, brief_path=BRIEF)["failed"] == 1


def test_a_transport_failure_is_an_error_not_a_verdict(tmp_path):
    bank = _bank(tmp_path)
    down = AutomaticReviewError("transport_error")
    fake = Fake({REVIEWERS[0]: [down, down], REVIEWERS[1]: [_ok(["c1", "c2", "c3"])]})
    assert review_bank(bank, REVIEWERS, fake, brief_path=BRIEF)["failed"] == 1
    summary = summarize(bank)
    assert summary["errors"] == [{"model": REVIEWERS[0], "file": "tier_a/kimi_t_a_001.json",
                                  "error": "transport_error"}]


# ————— المحجوب لا يُرسل —————

def test_a_sealed_file_is_never_sent(tmp_path):
    bank = _bank(tmp_path)
    leaked = bank / "open" / "sealed" / "kimi_x.json"
    leaked.parent.mkdir(parents=True)
    leaked.write_text((bank / "open" / "tier_a" / "kimi_t_a_001.json").read_text(
        encoding="utf-8"), encoding="utf-8")
    fake = Fake({m: [] for m in REVIEWERS})
    with pytest.raises(AutomaticReviewError) as exc:
        review_bank(bank, REVIEWERS, fake, brief_path=BRIEF)
    assert exc.value.code == "sealed_never_reviewed_externally"
    assert fake.calls == [], "لا نداءَ واحدًا قبل الرفض"


# ————— قاعدة العائلات —————

@pytest.mark.parametrize("model,code", [
    ("qwen3.5:9b", "reviewer_is_engine_family"),
    ("kimi-k2.6:cloud", "reviewer_is_author_family"),
    ("gpt-oss:120b-cloud", "reviewer_is_developer_family"),
    ("gemini-3-pro", "reviewer_is_developer_family"),
    ("gemma4:latest", "reviewer_is_developer_family"),
    ("claude-opus", "reviewer_is_developer_family"),
    ("mystery-model:cloud", "reviewer_family_unknown"),
])
def test_forbidden_reviewer_families_are_refused(model, code):
    with pytest.raises(AutomaticReviewError) as exc:
        check_reviewers(["deepseek-v4-flash:cloud", model])
    assert exc.value.code == code


def test_two_reviewers_of_one_family_are_refused():
    with pytest.raises(AutomaticReviewError) as exc:
        check_reviewers(["mistral-large-3:675b-cloud", "ministral-3:14b"])
    assert exc.value.code == "duplicate_reviewer_family"


def test_the_engine_family_rule_follows_the_real_engine():
    """إن تغيّر المحرّكُ الافتراضيّ سقط هذا الاختبار حتى تتبعه القاعدة."""
    from providers.ollama import OllamaProvider
    default = inspect.signature(OllamaProvider.__init__).parameters["model"].default
    assert reviewer_family(default) == ENGINE_FAMILY


# ————— κ والقائمة المرفوعة للمالك —————

def test_cohen_kappa_matches_a_hand_computed_table():
    # 10 أحكام: اتفاقٌ في 7. المراجع الأول: 6 صحيح و4 خاطئ، والثاني: 5 و5.
    # التوقّع بالمصادفة = 0.6×0.5 + 0.4×0.5 = 0.5، فκ = (0.7−0.5)/(1−0.5) = 0.4
    first = ["c"] * 6 + ["i"] * 4
    second = ["c"] * 4 + ["i"] * 2 + ["i"] * 3 + ["c"]
    assert sum(a == b for a, b in zip(first, second)) == 7
    assert cohen_kappa(first, second) == 0.4


def test_kappa_is_null_when_every_verdict_is_one_category():
    assert cohen_kappa(["c"] * 5, ["c"] * 5) is None


def test_disagreements_and_flags_reach_the_owner_queue_and_agreement_does_not(tmp_path):
    bank = _bank(tmp_path)
    a = {"judgments": [_judgment("c1"), _judgment("c2"),
                       _judgment("c3", reference="incorrect")]}
    b = {"judgments": [_judgment("c1"), _judgment("c2", rubric="insufficient"),
                       _judgment("c3", reference="incorrect")]}
    review_bank(bank, REVIEWERS, Fake({REVIEWERS[0]: [a], REVIEWERS[1]: [b]}),
                brief_path=BRIEF)
    summary = summarize(bank)
    queued = {entry["id"] for entry in summary["owner_queue"]}
    assert queued == {"c2", "c3"}, "c1 متّفقٌ على صحّته فلا يُرفع"
    assert summary["pairs"][0]["items"] == 3
    assert summary["pairs"][0]["reference"]["observed_agreement"] == 1.0
    assert "llm_reviewers_not_human" in summary["measurement_limits"]


# ————— التجربة الحيّة الصغيرة —————

def test_the_smoke_probe_passes_only_when_the_planted_error_is_caught(tmp_path):
    from evaluation.external_review import SMOKE_PLANTED, smoke
    ids = ["smoke_1", SMOKE_PLANTED, "smoke_3"]
    catches = {"judgments": [_judgment("smoke_1"), _judgment(SMOKE_PLANTED, reference="incorrect"),
                             _judgment("smoke_3")]}
    report = smoke(tmp_path, REVIEWERS, Fake({m: [catches] for m in REVIEWERS}), brief_path=BRIEF)
    assert report["status"] == "passed"
    assert all(r["caught_planted_error"] for r in report["reviewers"].values())
    # مراجعٌ يوافق على كل شيء يُسقط التجربة، ولو عمل النداء سليمًا
    yes_man = _ok(ids)
    fake = Fake({REVIEWERS[0]: [catches], REVIEWERS[1]: [yes_man]})
    report = smoke(tmp_path / "again", REVIEWERS, fake, brief_path=BRIEF)
    assert report["status"] == "failed"
    assert report["reviewers"][REVIEWERS[1]]["caught_planted_error"] is False


# ————— الواجهاتُ المجانية: GitHub Models وموجّه HF (#168، قرار المالك في ٢٨ سبتمبر ٢٠٢٦) —————
# بلا شبكة: المُفتِّحُ محقونٌ يلتقط كلَّ طلبٍ قبل أن يخرج، ويردّ بما كُتب لكل نموذج.

import io  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import external_review as cli  # noqa: E402

# مفتاحٌ مصطنع لا يطابق أنماطَ الأسرار في tools/export_public.py (لا `sk-` ولا `gh?_` ولا `AKIA`)
KEY = "test-key-not-a-secret-0123456789"
DS, MI, LL = "deepseek/DeepSeek-V3-0324", "mistral-ai/mistral-medium-2505", "meta/Llama-3.3-70B-Instruct"
GH_CHAT = "https://models.github.ai/inference/chat/completions"
GH_CATALOG = "https://models.github.ai/catalog/models"
SMOKE_IDS = ("smoke_1", "smoke_2", "smoke_3")
CATCH = {"judgments": [_judgment("smoke_1"), _judgment("smoke_2", reference="incorrect"), _judgment("smoke_3")]}
YES_MAN = _ok(SMOKE_IDS)


def _gh(model, outputs=("text",)):
    return {"id": model, "publisher": model.split("/")[0], "rate_limit_tier": "low",
            "supported_output_modalities": list(outputs)}


# شكلُ مدخلٍ في فهرس GitHub Models كما توثّقه GitHub: قائمةٌ في أعلى الردّ، ولكل نموذجٍ هذه الحقول
GH_FIELDS = ("capabilities", "html_url", "id", "limits", "name", "publisher", "rate_limit_tier", "registry",
             "summary", "supported_input_modalities", "supported_output_modalities", "tags", "version")


def _gh_full(model, outputs=("text",)):
    return {"id": model, "name": model.split("/")[1], "publisher": model.split("/")[0].title(), "registry": "azureml",
            "summary": "synthetic fixture", "html_url": "https://github.com/marketplace/models/azureml/fixture",
            "version": "1", "capabilities": ["streaming"], "limits": {"max_input_tokens": 8000, "max_output_tokens": 4000},
            "rate_limit_tier": "low", "supported_input_modalities": ["text"],
            "supported_output_modalities": list(outputs), "tags": ["multilingual"]}


class _Reply(io.BytesIO):
    def __init__(self, raw: bytes, content_type: str, url: str):
        super().__init__(raw)
        self.headers = {"Content-Type": content_type}
        self.status = 200
        self.url = url

    def geturl(self) -> str:
        return self.url


class _Fail:
    """ردُّ HTTP فاشلٌ بجسمٍ ونوع محتوى."""

    def __init__(self, code: int, body: bytes = b"", content_type: str = "application/json", location: str | None = None):
        self.code, self.body, self.content_type, self.location = code, body, content_type, location


class FreeOpener:
    """مُفتِّحٌ محقون: الفهرسُ لطلب GET (وبايتاتٌ خامٌ = نصٌّ عاديّ)، ولكل نموذجٍ ردودُه بالترتيب (والأخيرُ يتكرّر). عددٌ =
    خطأ HTTP بذلك الرمز، وصفٌّ (نصّ، سببُ الانتهاء) = ردٌّ خام، وقاموسٌ = أحكامٌ تُرمَّز JSON."""

    def __init__(self, catalog=None, replies=None, default=None):
        self.catalog = catalog
        self.replies = {m: list(r) for m, r in (replies or {}).items()}
        self.default = default
        self.requests: list[urllib.request.Request] = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if request.data is None:
            reply = self.catalog
        else:
            model = json.loads(request.data)["model"]
            queue = self.replies[model] if self.default is None else self.replies.setdefault(model, list(self.default))
            reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, int):
            raise urllib.error.HTTPError(request.full_url, reply, "refused", {}, None)
        if isinstance(reply, _Fail):
            headers = {"Content-Type": reply.content_type, **({"Location": reply.location} if reply.location else {})}
            raise urllib.error.HTTPError(request.full_url, reply.code, "refused", headers, io.BytesIO(reply.body))
        if isinstance(reply, bytes):
            return _Reply(reply, "text/plain", request.full_url)
        if request.data is not None:
            content, finish = reply if isinstance(reply, tuple) else (json.dumps(reply, ensure_ascii=False), "stop")
            reply = {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}]}
        return _Reply(json.dumps(reply, ensure_ascii=False).encode("utf-8"), "application/json", request.full_url)

    def chat_models(self):
        return [json.loads(r.data)["model"] for r in self.requests if r.data is not None]


def _free(monkeypatch, opener):
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: opener)
    monkeypatch.setenv("GITHUB_TOKEN", KEY)
    monkeypatch.setenv("HF_TOKEN", KEY)
    return opener


def _printed(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_free_backend_key_rides_only_the_request_never_the_report_output_or_errors(tmp_path, monkeypatch, capsys):
    opener = _free(monkeypatch, FreeOpener(catalog=[_gh(DS), _gh(MI)], replies={DS: [CATCH], MI: [CATCH]}))
    out = tmp_path / "smoke.json"
    assert cli.main(["--backend", "github-models", "--smoke", str(out)]) == 0
    assert [r.full_url for r in opener.requests] == [GH_CATALOG, GH_CHAT, GH_CHAT]
    for request in opener.requests:
        # في ترويسةٍ لا تُعاد عند التحويل، ومع ترويستَي واجهة GitHub
        assert request.unredirected_hdrs.get("Authorization") == f"Bearer {KEY}"
        assert "Authorization" not in request.headers
        assert request.get_header("Accept") == "application/vnd.github+json"
        assert request.get_header("X-github-api-version") == "2022-11-28"
    body = json.loads(opener.requests[1].data)
    assert body["model"] == DS and body["temperature"] == 0 and body["stream"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"].startswith("## من أنت")
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["backend"] == {"name": "github-models", "endpoint_host": "models.github.ai", "key_env": "GITHUB_TOKEN"}
    assert {m: r["family"] for m, r in report["reviewers"].items()} == {DS: "deepseek", MI: "mistral"}
    assert "free_tier_rate_limits_and_input_caps_apply" in report["measurement_limits"]
    printed = capsys.readouterr().out
    assert KEY not in printed and KEY not in out.read_text(encoding="utf-8")
    chat = cli.build_free_transport("github-models", environ={"GITHUB_TOKEN": KEY})
    assert KEY not in repr(chat) and KEY not in json.dumps(chat.describe())
    chat.opener = FreeOpener(replies={DS: [401]})
    with pytest.raises(AutomaticReviewError) as failed:
        chat(DS, "s", "u", {})
    assert failed.value.code == "unauthorized"
    assert KEY not in str(failed.value) and KEY not in repr(failed.value) and failed.value.__cause__ is None
    assert KEY not in json.dumps(chat.failures) and chat.failures[DS]["status"] == 401


def test_the_free_key_comes_from_the_environment_only(tmp_path, monkeypatch, capsys):
    options = [c for c in cli.main.__code__.co_consts if isinstance(c, str) and c.startswith("--")]
    assert options and not any("key" in o or "api" in o or o.endswith("-token") for o in options)
    for backend, env in (("github-models", "GITHUB_TOKEN"), ("hf-router", "HF_TOKEN")):
        with pytest.raises(AutomaticReviewError) as refused:
            cli.build_free_transport(backend, environ={})
        assert refused.value.code == "key_missing" and env in str(refused.value)
        assert cli.build_free_transport(backend, environ={env: KEY}).key_env == env
    opener = FreeOpener()
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: opener)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli.main(["--backend", "github-models", "--smoke", str(tmp_path / "s.json")]) == 2
    assert _printed(capsys)["code"] == "key_missing" and opener.requests == []
    assert not (tmp_path / "s.json").exists()


@pytest.mark.parametrize("url", [
    "http://models.github.ai/inference/chat/completions",        # بلا تشفير
    "https://evilmodels.github.ai/inference/chat/completions",   # مضيفٌ يشبه المسموح
    "https://example.com/v1/chat/completions",
    "https://models.github.ai:8443/inference/chat/completions",  # منفذٌ صريح
    "https://user@models.github.ai/inference/chat/completions",  # هويةٌ في الرابط
    "https://models.github.ai:abc/inference/chat/completions",   # منفذٌ غيرُ عدديّ
    "https://[models.github.ai/inference/chat/completions",      # رابطٌ لا يُحلَّل
], ids=["plain_http", "lookalike_host", "other_host", "explicit_port", "userinfo", "non_numeric_port", "unparsable"])
def test_only_the_listed_endpoints_are_allowed(url):
    with pytest.raises(AutomaticReviewError) as refused:
        cli.allowed_endpoint(url)
    assert refused.value.code == "endpoint_not_allowed"
    with pytest.raises(AutomaticReviewError) as refused:
        cli.OpenAICompatChat("github-models", KEY, chat_url=url)
    assert refused.value.code == "endpoint_not_allowed"
    for spec in cli.BACKENDS.values():
        assert cli.allowed_endpoint(spec["chat_url"]) and cli.allowed_endpoint(spec["catalog_url"])
    assert cli.allowed_endpoint(cli.CLOUD_ENDPOINT + "/api/chat")


@pytest.mark.parametrize("model,code", [
    ("Qwen/Qwen3-235B-A22B-Instruct-2507", "reviewer_is_engine_family"),
    ("moonshotai/Kimi-K2-Instruct", "reviewer_is_author_family"),
    ("openai/gpt-4.1", "reviewer_is_developer_family"),
    ("google/gemma-3-27b-it", "reviewer_is_developer_family"),
    ("anthropic/claude-sonnet", "reviewer_is_developer_family"),
    ("deepseek-ai/DeepSeek-R1-Distill-Qwen-32B", "reviewer_is_engine_family"),   # المقطَّرُ من Qwen سلالتُه Qwen
    ("xai/grok-3", "publisher_unknown"),
    ("deepseek", "publisher_unknown"),                                            # بلا ناشر
    ("meta/DeepSeek-V3-0324", "family_mismatch"),                                 # الاسمُ يكذّب الناشر
    ("microsoft/MAI-DS-R1", "reviewer_family_unknown"),                           # الاسمُ لا يقول عائلة
], ids=["qwen_engine", "kimi_author", "openai_developer", "google_developer", "anthropic_developer", "qwen_distilled",
        "unknown_publisher", "no_publisher", "name_contradicts_publisher", "name_says_no_family"])
def test_free_reviewer_families_are_refused_by_name(model, code):
    with pytest.raises(AutomaticReviewError) as refused:
        cli.resolve_reviewer(model)
    assert refused.value.code == code


@pytest.mark.parametrize("model,family", [
    ("deepseek/DeepSeek-V3-0324", "deepseek"), ("deepseek-ai/DeepSeek-V3.1", "deepseek"),
    ("mistral-ai/mistral-medium-2505", "mistral"), ("mistralai/Mistral-Small-3.1-24B-Instruct-2503", "mistral"),
    ("meta/Meta-Llama-3.1-405B-Instruct", "meta"), ("meta-llama/Llama-3.3-70B-Instruct", "meta"),
    ("cohere/cohere-command-a", "cohere"), ("CohereLabs/c4ai-command-a-03-2025", "cohere"),
    ("ai21-labs/AI21-Jamba-1.5-Large", "ai21"), ("microsoft/Phi-4", "microsoft"),
], ids=["github_deepseek", "hf_deepseek", "github_mistral", "hf_mistral", "github_meta_llama", "hf_meta_llama",
        "github_cohere", "hf_cohere", "github_ai21", "github_microsoft"])
def test_preferred_free_families_resolve_from_publisher_and_name(model, family):
    identity = cli.resolve_reviewer(model)
    assert identity["family"] == family and identity["lineage"] == [family]
    assert identity["publisher"] == model.split("/")[0]
    # والقاعدةُ في `evaluation/external_review.py` تقرأ العائلةَ نفسَها من المعرّف نفسِه (الجدولُ الواحد)
    assert check_reviewers([model, "granite-4:probe"]) == {model: family, "granite-4:probe": "ibm"}


def test_two_free_reviewers_never_share_a_family_or_a_lineage(tmp_path, monkeypatch, capsys):
    same = [cli.resolve_reviewer(DS), cli.resolve_reviewer("deepseek-ai/DeepSeek-V3.1")]
    with pytest.raises(AutomaticReviewError) as refused:
        cli.check_distinct(same)
    assert refused.value.code == "duplicate_reviewer_family"
    distill = cli.resolve_reviewer("deepseek-ai/DeepSeek-R1-Distill-Llama-70B")
    assert distill["lineage"] == ["deepseek", "meta"]
    with pytest.raises(AutomaticReviewError) as refused:
        cli.check_distinct([distill, cli.resolve_reviewer("meta-llama/Llama-3.3-70B-Instruct")])
    assert refused.value.code == "duplicate_reviewer_family"
    chosen = cli.choose_reviewers(same + [cli.resolve_reviewer(MI)])
    assert [c["model"] for c in chosen] == [DS, MI], "المرشّحُ من عائلةٍ مختارة يُتخطّى"
    opener = _free(monkeypatch, FreeOpener(replies={DS: [CATCH], "deepseek-ai/DeepSeek-V3.1": [CATCH]}))
    code = cli.main(["--backend", "github-models", "--smoke", str(tmp_path / "s.json"),
                     "--reviewer", DS, "--reviewer", "deepseek-ai/DeepSeek-V3.1"])
    assert code == 2 and _printed(capsys)["code"] == "duplicate_reviewer_family" and opener.requests == []


def test_quota_exhaustion_falls_back_to_a_reviewer_of_another_family(tmp_path, monkeypatch, capsys):
    opener = _free(monkeypatch, FreeOpener(catalog=[_gh(DS), _gh(MI), _gh(LL)],
                                           replies={DS: [429], MI: [CATCH], LL: [CATCH]}))
    out = tmp_path / "smoke.json"
    assert cli.main(["--backend", "github-models", "--smoke", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "passed" and set(report["reviewers"]) == {MI, LL}
    assert report["fallbacks"] == [{"exhausted": [DS], "code": "quota_exhausted", "replacement": [LL]}]
    assert opener.chat_models() == [DS, MI, LL], "النافدُ لا يعاد فورًا؛ يُستعمل البديل المسموح"
    assert _printed(capsys)["fallbacks"] == report["fallbacks"]


def test_payment_required_is_named_on_its_own_and_stops_the_run_without_a_fallback(tmp_path, monkeypatch, capsys):
    """HTTP 402 حالةُ فوترةٍ لا حدُّ طلبات: لا يُسمّى `quota_exhausted` ولا يُستبدل فيه المراجعُ ببديل (ق٧١-٢ وق٧١-٥)."""
    opener = _free(monkeypatch, FreeOpener(catalog=[_gh(DS), _gh(MI), _gh(LL)],
                                           replies={DS: [402], MI: [CATCH], LL: [CATCH]}))
    out = tmp_path / "smoke.json"
    assert cli.main(["--backend", "github-models", "--smoke", str(out)]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["code"] == "payment_required"
    assert report["fallbacks"] == [], "طلبُ الدفع لا يُعالَج باستبدال المراجع"
    assert LL not in opener.chat_models(), "لا نداءَ لبديلٍ بعد طلب الدفع"
    assert opener.chat_models().count(DS) == 1, "طلبُ الدفع لا يُعاد"


def test_quota_with_no_other_family_left_is_a_named_failure(tmp_path, monkeypatch, capsys):
    assert cli.http_code(429) == "quota_exhausted" and cli.http_code(402) == "payment_required"
    assert cli.http_code(413) == "request_too_large" and cli.http_code(500) == "http_500"
    other_deepseek = "deepseek/DeepSeek-R1-0528"
    _free(monkeypatch, FreeOpener(catalog=[_gh(DS), _gh(other_deepseek), _gh(MI)],
                                  replies={DS: [429], other_deepseek: [CATCH], MI: [CATCH]}))
    out = tmp_path / "smoke.json"
    assert cli.main(["--backend", "github-models", "--smoke", str(out)]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["code"] == "quota_exhausted_no_fallback"
    assert report["fallbacks"] == [{"exhausted": [DS], "code": "quota_exhausted", "replacement": None}]
    assert _printed(capsys)["code"] == "quota_exhausted_no_fallback"
    with pytest.raises(AutomaticReviewError) as unavailable:
        cli.choose_reviewers([cli.resolve_reviewer(DS)])
    assert unavailable.value.code == "reviewers_unavailable"


def test_an_empty_or_truncated_free_reply_is_retried_once_then_named(tmp_path):
    ids = ["c1", "c2", "c3"]
    hf_ds, hf_ll = "deepseek-ai/DeepSeek-V3-0324", "meta-llama/Llama-3.3-70B-Instruct"
    chat = cli.OpenAICompatChat("hf-router", KEY)
    assert chat.chat_url == "https://router.huggingface.co/v1/chat/completions"
    chat.opener = FreeOpener(replies={hf_ds: [("  ", "stop")],
                                      hf_ll: [(json.dumps(_ok(ids)), "length")]})
    bank = _bank(tmp_path)
    assert review_bank(bank, [hf_ds, hf_ll], chat, brief_path=BRIEF)["failed"] == 2
    records = {m: json.loads((bank / "reviews" / m.replace("/", "_") / "tier_a" / "kimi_t_a_001.json")
                             .read_text(encoding="utf-8")) for m in (hf_ds, hf_ll)}
    assert records[hf_ds]["error"] == "reply_empty" and len(records[hf_ds]["attempts"]) == 2
    assert records[hf_ll]["error"] == "reply_incomplete" and len(records[hf_ll]["attempts"]) == 2
    assert records[hf_ds]["family"] == "deepseek" and records[hf_ll]["family"] == "meta"
    chat.opener = FreeOpener(replies={hf_ds: [("", "stop"), _ok(ids)], hf_ll: [{"no": "choices"}, _ok(ids)]})
    again = _bank(tmp_path / "again")
    assert review_bank(again, [hf_ds, hf_ll], chat, brief_path=BRIEF)["failed"] == 0

    class NoChoices:
        def open(self, request, timeout=None):
            return io.BytesIO(b'{"choices": []}')

    chat.opener = NoChoices()
    with pytest.raises(AutomaticReviewError) as malformed:
        chat(hf_ds, "s", "u", {})
    assert malformed.value.code == "openai_response_malformed"


def test_free_backends_send_no_sealed_file_and_no_bank_outside_evaluation_banks(tmp_path, monkeypatch, capsys):
    opener = _free(monkeypatch, FreeOpener(replies={DS: [_ok(["c1", "c2", "c3"])], MI: [_ok(["c1", "c2", "c3"])]}))
    outside = _bank(tmp_path / "owner")
    args = ["--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--brief", str(BRIEF)]
    assert cli.main([str(outside), *args]) == 2
    assert _printed(capsys)["code"] == "bank_outside_evaluation_banks" and opener.requests == []
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    bank = tmp_path / "evaluation" / "banks" / "b"
    source = _bank(tmp_path / "src") / "open" / "tier_a" / "kimi_t_a_001.json"
    for relative in ("a/kimi_x.json", "sealed/kimi_y.json"):
        (bank / "open" / relative).parent.mkdir(parents=True)
        (bank / "open" / relative).write_bytes(source.read_bytes())
    assert cli.main([str(bank), *args]) == 2
    assert _printed(capsys)["code"] == "sealed_never_reviewed_externally"
    assert opener.requests == [], "لا نداءَ واحدًا قبل الرفض، ولو سبق الملفُّ المفتوحُ المحجوبَ في الترتيب"


def test_the_free_catalog_is_assessed_and_ranked_by_preferred_family():
    catalog = [_gh("openai/gpt-4.1"), _gh("deepseek/DeepSeek-R1"), _gh(DS),
               _gh("meta/Llama-3.2-90B-Vision-Instruct"), _gh("meta/Meta-Llama-3.1-405B-Instruct"), _gh(LL),
               _gh("cohere/Cohere-rerank-v3", outputs=("embeddings",)), _gh("cohere/cohere-command-a"),
               _gh("microsoft/Phi-4-reasoning"), _gh("microsoft/Phi-4-multimodal-instruct"), _gh("microsoft/MAI-DS-R1"),
               _gh("xai/grok-3"), _gh("mistral-ai/Codestral-2501")]
    assessed = cli.assess_catalog([cli._catalog_entry(e) for e in catalog], "github-models")
    assert [c["model"] for c in assessed["candidates"]] == [
        DS, "mistral-ai/Codestral-2501", LL, "cohere/cohere-command-a", "microsoft/Phi-4-multimodal-instruct",
        "deepseek/DeepSeek-R1", "meta/Meta-Llama-3.1-405B-Instruct", "meta/Llama-3.2-90B-Vision-Instruct",
        "microsoft/Phi-4-reasoning"]
    assert assessed["refused"] == {"not_a_chat_model": 1, "publisher_unknown": 1,
                                   "reviewer_family_unknown": 1, "reviewer_is_developer_family": 1}
    assert assessed["models"] == len(catalog)
    hf = cli.OpenAICompatChat("hf-router", KEY)
    hf.opener = FreeOpener(catalog={"object": "list", "data": [
        {"id": "deepseek-ai/DeepSeek-V3-0324", "providers": [{"provider": "p", "status": "live"}]},
        {"id": "meta-llama/Llama-3.3-70B-Instruct", "providers": [{"provider": "p", "status": "staging"}]},
        {"id": "CohereLabs/c4ai-command-a-03-2025", "architecture": {"output_modalities": ["text"]}}]})
    entries = hf.catalog()
    assert hf.opener.requests[0].full_url == "https://router.huggingface.co/v1/models"
    assert [(e["id"], e["chat"], e["reason"]) for e in entries] == [
        ("deepseek-ai/DeepSeek-V3-0324", True, None), ("meta-llama/Llama-3.3-70B-Instruct", False, "no_live_provider"),
        ("CohereLabs/c4ai-command-a-03-2025", True, None)]


def test_every_family_mode_probes_each_allowed_family_in_pairs(tmp_path, monkeypatch, capsys):
    catalog = [_gh(DS), _gh(MI), _gh(LL)]
    _free(monkeypatch, FreeOpener(catalog=catalog, replies={DS: [CATCH], MI: [CATCH], LL: [CATCH]}))
    out = tmp_path / "every.json"
    assert cli.main(["--backend", "github-models", "--smoke", str(out), "--every-family"]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "passed" and report["pairs"] == [[DS, MI], [LL, DS]]
    assert {m: (r["family"], r["caught_planted_error"], r["reachable"]) for m, r in report["models"].items()} == {
        DS: ("deepseek", True, True), MI: ("mistral", True, True), LL: ("meta", True, True)}
    capsys.readouterr()
    _free(monkeypatch, FreeOpener(catalog=catalog, replies={DS: [CATCH], MI: [CATCH], LL: [YES_MAN]}))
    assert cli.main(["--backend", "github-models", "--smoke", str(out), "--every-family"]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "failed" and report["models"][LL]["caught_planted_error"] is False


def test_free_options_leave_the_ollama_path_as_it_was(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli.main(["--list-catalog", str(tmp_path / "c.json")]) == 2
    assert _printed(capsys)["code"] == "free_backend_option_without_free_backend"
    assert cli.main(["--backend", "github-models", "--base-url", cli.CLOUD_ENDPOINT, "--smoke", str(tmp_path / "s.json")]) == 2
    assert _printed(capsys)["code"] == "base_url_is_ollama_only"


def test_free_transport_refuses_redirects():
    chat = cli.OpenAICompatChat("github-models", KEY)
    assert any(isinstance(h, cli._RefuseRedirect) for h in chat.opener.handlers)
    request = urllib.request.Request(GH_CHAT, data=b"{}")
    request.add_unredirected_header("Authorization", f"Bearer {KEY}")
    assert cli._RefuseRedirect().redirect_request(request, None, 302, "Found", {}, "https://evil.example/") is None


def test_the_free_llm_workflow_is_least_privilege_pinned_and_guarded():
    workflows = ROOT / ".github" / "workflows"
    text = (workflows / "free-llm-review.yml").read_text(encoding="utf-8")
    pin = __import__("re").search(r"actions/checkout@([0-9a-f]{40})", (workflows / "verify.yml").read_text(encoding="utf-8"))
    assert "\npermissions:\n  contents: read\n  models: read\n\n" in text
    assert ": write" not in text and "secrets." not in text
    assert [line.strip() for line in text.splitlines() if "uses:" in line] == [
        f"- uses: actions/checkout@{pin.group(1)} # v4",
        "uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2"]
    # أحكامُ البنك وقائمةُ المالك تُحفظ أثرًا مثبَّتًا، ولو أخفقت المراجعة، بعد فحصٍ أنها سجلّاتُ شطرٍ مفتوح بلا رمز (ملاحظة Codex على #174)
    check = text.split("- name: Check the bank review records before upload", 1)[1].split("- name:", 1)[0]
    assert "if: always() && env.MODE == 'bank'" in check and "id: artifact_check" in check
    assert '--check-artifact "evaluation/banks/kimi_v1/runs/$RUN_KEY/reviews"' in check and "set -euo pipefail" in check
    assert '--run-id "$RUN_KEY"' in check, "الفحصُ يرفض ما لا يحمل معرّفَ هذا التشغيل"
    upload = text.split("- name: Upload the bank review records for the owner", 1)[1]
    assert "if: always() && env.MODE == 'bank' && steps.artifact_check.outcome == 'success'" in upload
    assert "path: evaluation/banks/kimi_v1/runs/${{ env.RUN_KEY }}/reviews/" in upload and "if-no-files-found: error" in upload
    assert "RUN_KEY: ${{ github.run_id }}-${{ github.run_attempt }}" in text
    assert "retention-days: 30" in upload and "name: ${{ env.ARTIFACT_NAME }}" in upload
    bank_run = text.split("- name: Review the open part of kimi_v1", 1)[1].split("- name:", 1)[0]
    assert '--run-id "$RUN_KEY" --artifact-name "$ARTIFACT_NAME"' in bank_run
    assert "persist-credentials: false" in text
    assert "      - tools/external_review.py\n" in text and "      - .github/workflows/free-llm-review.yml\n" in text
    assert "options: [smoke, bank]" in text and "default: smoke" in text
    assert "github.event.pull_request.head.repo.full_name == github.repository" in text
    catalog_step = text.split("- name: List the GitHub Models catalog", 1)[1].split("- name:", 1)[0]
    smoke_step = text.split("- name: Planted-error smoke with two reviewers", 1)[1].split("- name:", 1)[0]
    assert "continue-on-error: true" in catalog_step, "الفهرسُ جردٌ لا بوّابة"
    assert "continue-on-error" not in smoke_step, "تجربةُ الخطأ المزروع هي البوّابة"
    bank_step = text.split("- name: Review the open part of kimi_v1", 1)[1]
    for step in (smoke_step, bank_step):
        assert 'if [ "$status" -eq 3 ]; then' in step and "status=0; fi" in step, "الواجهةُ غيرُ المتاحة تنبيهٌ لا سقوط"
    install = text.split("- name: Install the locked runtime", 1)[1].split("- name:", 1)[0]
    assert "GITHUB_TOKEN" not in install and "'uv==0.8.17'" in install and "--frozen" in install
    assert text.count("GITHUB_TOKEN: ${{ github.token }}") == 5
    assert "tools/external_review.py evaluation/banks/kimi_v1 --backend github-models" in text


def test_the_github_catalog_array_is_parsed_and_its_shape_named(tmp_path, monkeypatch, capsys):
    """الشكلُ الموثَّق: قائمةٌ في أعلى الردّ. والخلاصةُ تحمل شكلَه (النوعُ والعددُ وأسماءُ الحقول) لا نصَّه."""
    catalog = [_gh_full(DS), _gh_full(MI), _gh_full("openai/text-embedding-3-small", outputs=("embeddings",))]
    _free(monkeypatch, FreeOpener(catalog=catalog))
    out = tmp_path / "catalog.json"
    assert cli.main(["--backend", "github-models", "--list-catalog", str(out)]) == 0
    printed = _printed(capsys)
    size = len(json.dumps(catalog, ensure_ascii=False).encode("utf-8"))
    assert printed["shape"] == {"status": 200, "content_type": "application/json", "bytes": size, "top": "list",
                                "count": 3, "item_keys": list(GH_FIELDS)}
    assert printed["eligible"] == {"deepseek": [DS], "mistral": [MI]} and printed["candidates"] == [DS, MI]
    assert printed["refused"] == {"not_a_chat_model": 1}
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["backend"]["endpoint_host"] == "models.github.ai" and written["models"] == 3


def test_a_malformed_catalog_is_recorded_as_a_failed_call_not_a_success():
    """ملاحظةُ Codex على #290: ردُّ 200 بغلافٍ لا يُقرأ كان يُسجَّل في provider_usage ناجحًا ثم يُرفض بـcatalog_malformed."""
    chat = cli.OpenAICompatChat("github-models", KEY)
    chat.opener = FreeOpener(catalog=b"OK\r\n")
    with pytest.raises(AutomaticReviewError):
        chat.catalog()
    assert [(row["kind"], row["status"], row["error"]) for row in chat.provider_usage] == [
        ("catalog", "error", "catalog_malformed")]
    assert chat.catalog_read_at is None


def test_a_catalog_that_is_not_json_is_named_by_shape_and_the_smoke_uses_the_preferred_list(tmp_path, monkeypatch, capsys):
    """قيس في ٢٨ سبتمبر ٢٠٢٦: models.github.ai يردّ «OK» نصًّا عاديًّا (٤ بايتات) لأيّ مسارٍ بلا تفويض."""
    ok = b"OK\r\n"
    chat = cli.OpenAICompatChat("github-models", KEY)
    chat.opener = FreeOpener(catalog=ok)
    with pytest.raises(AutomaticReviewError) as malformed:
        chat.catalog()
    assert malformed.value.code == "catalog_malformed"
    assert malformed.value.shape == {"status": 200, "content_type": "text/plain", "bytes": 4, "top": "not_json"}
    wrapped = json.dumps({"models": [_gh_full(DS)]}).encode("utf-8")
    assert cli.catalog_shape(wrapped, "application/json", 200) == (
        None, {"status": 200, "content_type": "application/json", "bytes": len(wrapped), "top": "dict", "keys": ["models"]})
    _free(monkeypatch, FreeOpener(catalog=ok, replies={DS: [CATCH], MI: [CATCH]}))
    assert cli.main(["--backend", "github-models", "--list-catalog", str(tmp_path / "c.json")]) == 2
    refused = _printed(capsys)
    usage = refused.pop("provider_usage")          # نداءُ الفهرس الفاشل في المطبوع لا في الذاكرة وحدها (ملاحظة Codex على #290)
    assert refused == {"status": "refused", "code": "catalog_malformed",
                       "shape": {"status": 200, "content_type": "text/plain", "bytes": 4, "top": "not_json"}}
    assert [(row["kind"], row["status"], row["error"]) for row in usage] == [("catalog", "error", "catalog_malformed")]
    out = tmp_path / "smoke.json"
    assert cli.main(["--backend", "github-models", "--smoke", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "passed" and set(report["reviewers"]) == {DS, MI}
    assert report["candidates_from"] == "preferred_list" and report["catalog_error"] == "catalog_malformed"
    assert _printed(capsys)["catalog_error"] == "catalog_malformed"


def test_http_statuses_are_named_and_only_a_safe_shape_of_the_body_is_recorded():
    assert [cli.http_code(c) for c in (401, 402, 403, 404, 413, 429, 500)] == [
        "unauthorized", "payment_required", "forbidden", "not_found", "request_too_large", "quota_exhausted", "http_500"]
    secret_text = "PRIVATE-MESSAGE-TEXT"
    body = json.dumps({"error": {"code": "no_access", "type": "invalid_request_error", "message": secret_text}}).encode()
    chat = cli.OpenAICompatChat("github-models", KEY)
    chat.opener = FreeOpener(replies={DS: [_Fail(403, body)], MI: [b"OK\r\n"],
                                      LL: [_Fail(404, b"<html>nope</html>", "text/html")]})
    with pytest.raises(AutomaticReviewError) as forbidden:
        chat(DS, "s", "u", {})
    assert forbidden.value.code == "forbidden"
    assert chat.failures[DS] == {"status": 403, "content_type": "application/json", "bytes": len(body), "top": "dict",
                                 "keys": ["error"], "error_code": "no_access", "error_type": "invalid_request_error",
                                 "request": {"method": "POST", "sent": GH_CHAT, "final": GH_CHAT}}
    assert forbidden.value.shape == chat.failures[DS]
    with pytest.raises(AutomaticReviewError) as stub:
        chat(MI, "s", "u", {})
    assert stub.value.code == "response_not_json"
    assert chat.failures[MI] == {"status": 200, "content_type": "text/plain", "bytes": 4, "top": "not_json",
                                 "request": {"method": "POST", "sent": GH_CHAT, "final": GH_CHAT}}
    with pytest.raises(AutomaticReviewError) as missing:
        chat(LL, "s", "u", {})
    assert missing.value.code == "not_found" and chat.failures[LL]["content_type"] == "text/html"
    recorded = json.dumps(chat.failures)
    assert secret_text not in recorded and "nope" not in recorded and KEY not in recorded
    loose = json.dumps({"error": {"code": "a code with spaces", "type": "x" * 100}}).encode()
    assert cli.response_shape(400, loose, "application/json")[1] == {
        "status": 400, "content_type": "application/json", "bytes": len(loose), "top": "dict", "keys": ["error"]}


def test_a_backend_that_answers_ok_to_everything_is_unavailable_not_failed(tmp_path, monkeypatch, capsys):
    """قيس في ٢٨ سبتمبر ٢٠٢٦ من Actions ومن صندوق HF: models.github.ai يردّ 200 و«OK» على كل طلب."""
    ok = b"OK\r\n"
    stub = {"status": 200, "content_type": "text/plain", "bytes": 4, "top": "not_json"}
    _free(monkeypatch, FreeOpener(catalog=ok, replies={DS: [ok], MI: [ok]}))
    out = tmp_path / "smoke.json"
    assert cli.main(["--backend", "github-models", "--smoke", str(out)]) == 3
    report = json.loads(out.read_text(encoding="utf-8"))
    assert (report["status"], report["code"], report["unavailable_codes"]) == (
        "unavailable", "service_unavailable", ["response_not_json"])
    sent = {"catalog": {"method": "GET", "sent": GH_CATALOG, "final": GH_CATALOG},
            DS: {"method": "POST", "sent": GH_CHAT, "final": GH_CHAT}, MI: {"method": "POST", "sent": GH_CHAT, "final": GH_CHAT}}
    assert report["last_failure_shapes"] == {label: {**stub, "request": sent[label]} for label in sent}
    printed = _printed(capsys)
    assert printed["status"] == "unavailable" and printed["unavailable_codes"] == ["response_not_json"]
    _free(monkeypatch, FreeOpener(catalog=ok, replies={DS: [ok], MI: [CATCH]}))
    assert cli.main(["--backend", "github-models", "--smoke", str(out)]) == 1, "نموذجٌ أجاب فالإخفاقُ إخفاق"
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "failed"
    _free(monkeypatch, FreeOpener(catalog=ok, default=[ok]))
    assert cli.main(["--backend", "github-models", "--smoke", str(out), "--every-family"]) == 3
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "unavailable"
    capsys.readouterr()
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    bank = tmp_path / "evaluation" / "banks" / "b"
    source = _bank(tmp_path / "src") / "open" / "tier_a" / "kimi_t_a_001.json"
    (bank / "open" / "a").mkdir(parents=True)
    (bank / "open" / "a" / "kimi_x.json").write_bytes(source.read_bytes())
    _free(monkeypatch, FreeOpener(default=[ok]))
    assert cli.main([str(bank), "--backend", "github-models", "--reviewer", DS, "--reviewer", MI,
                     "--brief", str(BRIEF)]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unavailable" and result["unavailable_codes"] == ["response_not_json"]


def test_a_redirect_is_refused_by_name_with_its_target_and_the_key_is_not_resent(tmp_path, monkeypatch, capsys):
    """فرضيّةُ التحويل تُحسم بالتسجيل: 3xx رمزُه redirected، ووجهتُه مضيفًا ومسارًا (بلا استعلام) في الشكل، ولا يُتبع."""
    assert [cli.http_code(c) for c in (301, 302, 307, 308)] == ["redirected"] * 4
    chat = cli.OpenAICompatChat("github-models", KEY)
    chat.opener = FreeOpener(replies={DS: [_Fail(307, b"", "text/html", location="https://elsewhere.example/v2/chat?token=abc")],
                                      MI: [_Fail(301, b"", "text/html", location="/inference/chat/completions/")]})
    with pytest.raises(AutomaticReviewError) as moved:
        chat(DS, "s", "u", {})
    assert moved.value.code == "redirected"
    assert chat.failures[DS]["status"] == 307 and chat.failures[DS]["redirect_to"] == "elsewhere.example/v2/chat"
    with pytest.raises(AutomaticReviewError):
        chat(MI, "s", "u", {})
    assert chat.failures[MI]["redirect_to"] == "models.github.ai/inference/chat/completions/"
    assert len(chat.opener.requests) == 2, "لا طلبَ ثانٍ إلى الوجهة"
    assert "token=abc" not in json.dumps(chat.failures)


def test_the_exact_url_and_method_go_out_and_are_recorded_for_chat_and_catalog():
    """الردُّ «OK» لكل طلبٍ ليس مسارًا ضاع في العميل: الرابطُ والطريقةُ كما وُثّقا، ومسجَّلان مرسَلًا ونهائيًّا."""
    ok = b"OK\r\n"
    for backend, catalog_url, chat_url in (
            ("github-models", GH_CATALOG, GH_CHAT),
            ("hf-router", "https://router.huggingface.co/v1/models", "https://router.huggingface.co/v1/chat/completions")):
        chat = cli.OpenAICompatChat(backend, KEY)
        chat.opener = FreeOpener(catalog=ok, replies={DS: [ok]})
        with pytest.raises(AutomaticReviewError):
            chat.catalog()
        with pytest.raises(AutomaticReviewError):
            chat(DS, "s", "u", {})
        catalog_request, chat_request = chat.opener.requests
        assert (catalog_request.get_method(), catalog_request.full_url) == ("GET", catalog_url)
        assert (chat_request.get_method(), chat_request.full_url) == ("POST", chat_url)
        assert chat.failures["catalog"]["request"] == {"method": "GET", "sent": catalog_url, "final": catalog_url}
        assert chat.failures[DS]["request"] == {"method": "POST", "sent": chat_url, "final": chat_url}
        assert chat.last_request[DS] == chat.failures[DS]["request"]
    assert cli.bare_url("https://user:pw@models.github.ai:443/inference/chat/completions?token=abc#frag") == GH_CHAT


def _public_bank(root: Path, name: str) -> Path:
    bank = root / "evaluation" / "banks" / name
    source = _bank(root / "src" / name) / "open" / "tier_a" / "kimi_t_a_001.json"
    (bank / "open" / "a").mkdir(parents=True)
    (bank / "open" / "a" / "kimi_x.json").write_bytes(source.read_bytes())
    return bank


def test_a_bank_quota_superseded_by_a_successful_fallback_is_history_not_failure(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #174: نفادُ حصّة مراجعٍ أتمّ بديلُه من عائلةٍ أخرى عملَه لا يُسقط التشغيل، ويبقى مسمًّى."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ids = ["c1", "c2", "c3"]
    args = ["--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--fallback", LL, "--brief", str(BRIEF)]
    bank = _public_bank(tmp_path, "b1")
    stale = bank / "reviews" / "old-reviewer" / "a" / "kimi_x.json"          # سجلٌّ قديم لمراجعٍ خارج هذا التشغيل
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"model": "old-reviewer", "file": "a/kimi_x.json", "error": "transport_error"}),
                     encoding="utf-8")
    _free(monkeypatch, FreeOpener(replies={DS: [429], MI: [_ok(ids)], LL: [_ok(ids)]}))
    assert cli.main([str(bank), *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "reviewed" and result["errors"] == 0 and result["error_codes"] == []
    assert result["reviewers"] == {MI: "mistral", LL: "meta"}
    assert result["superseded"] == [{"model": DS, "file": "a/kimi_x.json", "error": "quota_exhausted",
                                     "superseded_by": [LL]}]
    assert result["fallbacks"] == [{"exhausted": [DS], "code": "quota_exhausted", "replacement": [LL]}]
    assert result["errors_of_other_reviewers"] == 1
    # البديلُ أخفق أيضًا: الخطآن كلاهما يُعدّان، ولا «مستبدَل»
    _free(monkeypatch, FreeOpener(replies={DS: [429], MI: [_ok(ids)], LL: [("", "stop")]}))
    assert cli.main([str(_public_bank(tmp_path, "b2")), *args]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed" and result["error_codes"] == ["quota_exhausted", "reply_empty"]
    assert result["errors"] == 2 and result["superseded"] == []
    # لا بديلَ من عائلةٍ أخرى: إخفاقٌ مسمًّى
    _free(monkeypatch, FreeOpener(replies={DS: [429], MI: [_ok(ids)]}))
    assert cli.main([str(_public_bank(tmp_path, "b3")), "--backend", "github-models", "--reviewer", DS,
                     "--reviewer", MI, "--brief", str(BRIEF)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed" and result["code"] == "quota_exhausted_no_fallback"
    assert result["error_codes"] == ["quota_exhausted"]


def _record(bank: Path, model: str, relative: str, judgments=None, error=None, *, under: str = "") -> None:
    path = bank / "reviews" / under / model.replace("/", "_") / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "family": "x", "file": relative, "judgments": judgments,
                                "error": error}, ensure_ascii=False), encoding="utf-8")


def test_summarize_counts_only_the_final_reviewers_and_never_the_superseded_history(tmp_path):
    """ملاحظة Codex على #174: سجلُّ مراجعٍ خارج المجموعة الأخيرة، أو في reviews/superseded/، لا يدخل زوجًا ولا κ ولا قائمةَ المالك."""
    from evaluation.external_review import SUPERSEDED_DIR
    bank = _bank(tmp_path)
    relative = "tier_a/kimi_t_a_001.json"
    agree = [_judgment("c1"), _judgment("c2"), _judgment("c3")]
    _record(bank, MI, relative, agree)
    _record(bank, LL, relative, agree)
    _record(bank, DS, relative, [_judgment("c1", reference="incorrect"), _judgment("c2"), _judgment("c3")])
    _record(bank, "cohere/cohere-command-a", relative, [_judgment("c1"), _judgment("c2", reference="incorrect"),
                                                        _judgment("c3")], under=SUPERSEDED_DIR)
    final = summarize(bank, reviewers={MI, LL})
    assert [p["reviewers"] for p in final["pairs"]] == [[LL, MI]] and final["owner_queue"] == []
    assert final["reviewers"] == [LL, MI]
    everyone = summarize(bank)
    assert everyone["reviewers"] == sorted([DS, LL, MI]), "التاريخُ المستبدَل لا يُقرأ ولو بلا مجموعة"
    assert [entry["id"] for entry in everyone["owner_queue"]] == ["c1"]


def test_a_superseded_reviewer_leaves_one_final_pair_and_the_fallback_covers_every_file(tmp_path, monkeypatch, capsys):
    """سيناريو Codex على #174: ملفّان، والمراجعُ الأول يُتمّ الأوّلَ ثم تنفد حصّتُه في الثاني."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ids = ["c1", "c2", "c3"]
    bank = _public_bank(tmp_path, "two")
    (bank / "open" / "a" / "kimi_y.json").write_bytes((bank / "open" / "a" / "kimi_x.json").read_bytes())
    dispute = {"judgments": [_judgment("c1", reference="incorrect"), _judgment("c2"), _judgment("c3")]}
    _free(monkeypatch, FreeOpener(replies={DS: [dispute, 429], MI: [_ok(ids)], LL: [_ok(ids)]}))
    assert cli.main([str(bank), "--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--fallback", LL,
                     "--brief", str(BRIEF), "--artifact-name", "art-2"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["artifact"] == "art-2", "المالكُ يعرف من الخلاصة أين يحكم"
    assert [p["reviewers"] for p in result["pairs"]] == [[LL, MI]] and result["pairs"][0]["items"] == 6
    assert result["owner_queue"] == 0, "خلافُ المستبدَل لا يُرفع إلى المالك"
    assert result["superseded"] == [{"model": DS, "file": "a/kimi_y.json", "error": "quota_exhausted",
                                     "superseded_by": [LL]}]
    assert result["superseded_completed"] == {DS: 1}
    for name in ("kimi_x.json", "kimi_y.json"):
        record = json.loads((bank / "reviews" / LL.replace("/", "_") / "a" / name).read_text(encoding="utf-8"))
        assert record["model"] == LL and record["error"] is None, "البديلُ راجع الملفَّ الذي أتمّه المستبدَل أيضًا"
    history = bank / "reviews" / "superseded" / DS.replace("/", "_")
    assert json.loads((history / "SUPERSEDED.json").read_text(encoding="utf-8"))["superseded_by"] == [LL]
    assert not (bank / "reviews" / DS.replace("/", "_")).exists()


def test_the_uploaded_review_records_are_open_split_json_without_tokens(tmp_path, monkeypatch, capsys):
    """ما يُرفع أثرًا من Actions يُفحص قبل الرفع (ملاحظة Codex على #174)."""
    from export_public import PERSONAL_PATTERNS
    assert cli._TOKEN_PATTERN.pattern == PERSONAL_PATTERNS["token"].pattern, "نمطُ الرمز نفسُه في المصدِّر"
    bank = _bank(tmp_path)
    _record(bank, MI, "tier_a/kimi_t_a_001.json", [_judgment("c1")])
    reviews = bank / "reviews"
    (reviews / "SUMMARY.json").write_text(json.dumps({"owner_queue": [{"id": "c1"}]}), encoding="utf-8")
    assert cli.check_artifact(reviews, environ={}) == {"status": "clean", "files": 2, "owner_queue": 1}
    assert cli.main(["--check-artifact", str(reviews), "--artifact-name", "art-1"]) == 0
    assert _printed(capsys) == {"status": "clean", "files": 2, "owner_queue": 1, "artifact": "art-1"}
    fake_token = "gh" + "p_" + "A" * 36                      # يُبنى عند التشغيل فلا يقع نمطُ رمزٍ في المصدر
    cases = [
        ("Sealed/kimi.json", json.dumps({"file": "a.json"}), "artifact_sealed_path"),
        ("m/tier_a/x.json", json.dumps({"file": "sealed/kimi_x.json"}), "artifact_sealed_path"),
        ("m/tier_a/y.json", json.dumps({"file": "a.json", "raw_output": fake_token}), "artifact_token_pattern"),
        ("m/tier_a/z.json", json.dumps({"file": "a.json", "raw_output": KEY}), "artifact_key_value"),
        ("m/notes.txt", "not json", "artifact_not_json"),
    ]
    for relative, content, code in cases:
        root = tmp_path / code / relative.replace("/", "_")
        (root / relative).parent.mkdir(parents=True)
        (root / relative).write_text(content, encoding="utf-8")
        with pytest.raises(AutomaticReviewError) as refused:
            cli.check_artifact(root, environ={"GITHUB_TOKEN": KEY})
        assert refused.value.code == code and KEY not in str(refused.value), relative
    with pytest.raises(AutomaticReviewError) as missing:
        cli.check_artifact(tmp_path / "absent", environ={})
    assert missing.value.code == "artifact_missing"
    assert cli.main(["--check-artifact", str(tmp_path / "absent")]) == 2
    assert _printed(capsys)["code"] == "artifact_missing"


def test_the_bank_run_writes_a_clean_run_directory_and_the_upload_refuses_anything_else(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #174: الخلاصةُ المتتبَّعة لا تُرفع باسم تشغيلٍ لم يولّدها، ولو خرج قبل الخلاصة."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ids = ["c1", "c2", "c3"]
    bank = _public_bank(tmp_path, "tracked")
    (bank / "reviews").mkdir()
    (bank / "reviews" / "SUMMARY.json").write_text(json.dumps({"owner_queue": [{"id": "old"}]}), encoding="utf-8")
    # خروجٌ قبل الخلاصة: فهرسٌ فيه عائلةٌ صالحةٌ واحدة فلا زوج
    _free(monkeypatch, FreeOpener(catalog=[_gh(DS)]))
    assert cli.main([str(bank), "--backend", "github-models", "--run-id", "R1", "--brief", str(BRIEF)]) == 2
    assert _printed(capsys)["code"] == "reviewers_unavailable"
    run_reviews = bank / "runs" / "R1" / "reviews"
    assert sorted(p.name for p in run_reviews.rglob("*")) == ["RUN.json"], "سجلُّ الرفض المسمّى وحده"
    state = json.loads((run_reviews / "RUN.json").read_text(encoding="utf-8"))
    assert (state["run_id"], state["status"], state["code"]) == ("R1", "refused", "reviewers_unavailable")
    assert cli.check_artifact(run_reviews, environ={}, run_id="R1") == {
        "status": "clean", "files": 1, "owner_queue": None,
        "run": {"run_id": "R1", "status": "refused", "code": "reviewers_unavailable"}}
    with pytest.raises(AutomaticReviewError) as stale:
        cli.check_artifact(bank / "reviews", environ={}, run_id="R1")
    assert stale.value.code == "artifact_stale_summary", "الخلاصةُ المتتبَّعة تُرفض باسمها"
    assert cli.main(["--check-artifact", str(bank / "reviews"), "--run-id", "R1"]) == 2
    assert _printed(capsys)["code"] == "artifact_stale_summary"
    # تشغيلٌ ناجح: كلُّ سجلٍّ والخلاصةُ يحملان المعرّف، والخلاصةُ المتتبَّعة لم تُمسّ
    _free(monkeypatch, FreeOpener(replies={DS: [_ok(ids)], MI: [_ok(ids)]}))
    args = [str(bank), "--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--run-id", "R2",
            "--brief", str(BRIEF)]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["run_id"] == "R2" and result["summary"].endswith("runs/R2/reviews/SUMMARY.json")
    run_reviews = bank / "runs" / "R2" / "reviews"
    assert cli.check_artifact(run_reviews, environ={}, run_id="R2")["files"] == 4
    assert json.loads((run_reviews / "RUN.json").read_text(encoding="utf-8"))["status"] == "reviewed"
    assert json.loads((bank / "reviews" / "SUMMARY.json").read_text(encoding="utf-8")) == {"owner_queue": [{"id": "old"}]}
    with pytest.raises(AutomaticReviewError) as mixed:
        cli.check_artifact(run_reviews, environ={}, run_id="R3")
    assert mixed.value.code in ("artifact_stale_record", "artifact_stale_summary")
    stray = run_reviews / "zz_old" / "a" / "kimi_x.json"
    stray.parent.mkdir(parents=True)
    stray.write_text(json.dumps({"model": "old", "file": "a/kimi_x.json", "error": None}), encoding="utf-8")
    with pytest.raises(AutomaticReviewError) as record:
        cli.check_artifact(run_reviews, environ={}, run_id="R2")
    assert record.value.code == "artifact_stale_record"
    # التشغيلُ لا يُعاد في مجلّدٍ قائم، ومعرّفُه لا يصعد
    assert cli.main(args) == 2 and _printed(capsys)["code"] == "run_dir_exists"
    for bad in ("..", "../x", "a/b"):
        assert cli.main([str(bank), "--backend", "github-models", "--run-id", bad]) == 2
        assert _printed(capsys)["code"] == "run_id_invalid"


def test_bank_counts_come_from_the_final_set_and_attempts_are_named_apart(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #174: A تنفد حصّتُه وB ينجح وC يحلّ محلّ A — الأعدادُ النهائية بلا فشل، ومجاميعُ المحاولات منفصلة."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ids = ["c1", "c2", "c3"]
    bank = _public_bank(tmp_path, "counts")
    args = [str(bank), "--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--fallback", LL,
            "--brief", str(BRIEF)]
    _free(monkeypatch, FreeOpener(replies={DS: [429], MI: [_ok(ids)], LL: [_ok(ids)]}))
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert {k: result[k] for k in ("reviewed", "skipped", "failed")} == {"reviewed": 2, "skipped": 0, "failed": 0}
    assert result["attempts"] == {"reviewed": 2, "skipped": 1, "failed": 1}
    # استدعاءٌ ثانٍ: ما سبق يُعدّ skipped لا reviewed
    _free(monkeypatch, FreeOpener(replies={MI: [_ok(ids)], LL: [_ok(ids)]}))
    assert cli.main([str(bank), "--backend", "github-models", "--reviewer", MI, "--reviewer", LL,
                     "--brief", str(BRIEF)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert {k: result[k] for k in ("reviewed", "skipped", "failed")} == {"reviewed": 0, "skipped": 2, "failed": 0}


def _sealed_fixture(root: Path, bank: Path, *, as_list: bool = False) -> bytes:
    """ملفٌّ محجوبٌ مصطنع (ليس من بنكٍ حقيقي) وبيانُه ببصمته، كما يعلنه بيانُ المحجوب."""
    import hashlib
    sealed = b'{"synthetic": "sealed fixture, not bank content", "form": "%s"}' % (b"list" if as_list else b"dict")
    (bank / "sealed").mkdir(parents=True, exist_ok=True)
    (bank / "sealed" / "x.json").write_bytes(sealed)
    digest = hashlib.sha256(sealed).hexdigest()
    files = [{"path": "x.json", "sha256": digest}] if as_list else {"x.json": {"sha256": digest}}
    (bank / "sealed" / "MANIFEST.json").write_text(json.dumps({"generated_at": "t", "files": files}), encoding="utf-8")
    return sealed


def test_links_and_renamed_sealed_copies_in_the_open_split_are_refused_before_any_call(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #174 (أمنية): رابطٌ في open/ إلى المحجوب، أو نسخةٌ محجوبةٌ باسمٍ آخر، لا تُنسخ ولا تُرسل."""
    import hashlib
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    bank = _public_bank(tmp_path, "linked")
    sealed = _sealed_fixture(tmp_path, bank)
    assert cli.sealed_digests() == {hashlib.sha256(sealed).hexdigest(): "linked/x.json"}
    listed = tmp_path / "evaluation" / "banks" / "listed"
    _sealed_fixture(tmp_path, listed, as_list=True)
    assert set(cli.sealed_digests().values()) == {"linked/x.json", "listed/x.json"}, "شكلا البيان كلاهما"
    base = ["--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--brief", str(BRIEF)]
    cases = [("a/link.json", Path("../../sealed/x.json"), "open_split_link_refused"),      # رابطٌ لملفّ
             ("linked_dir", Path("../sealed"), "open_split_link_refused"),                # رابطٌ لمجلّد
             ("a/innocent.json", None, "sealed_digest_refused")]                          # نسخةٌ عاديّةٌ باسمٍ آخر
    for index, (relative, target, code) in enumerate(cases):
        entry = bank / "open" / relative
        if target is None:
            entry.write_bytes(sealed)
        else:
            os.symlink(target, entry)
        for extra in ([], ["--run-id", f"L{index}"]):
            opener = _free(monkeypatch, FreeOpener(replies={DS: [_ok(["c1", "c2", "c3"])], MI: [_ok(["c1", "c2", "c3"])]}))
            assert cli.main([str(bank), *base, *extra]) == 2, (relative, extra)
            assert _printed(capsys)["code"] == code and opener.requests == [], (relative, extra)
            assert not (bank / "runs" / f"L{index}").exists(), "لا نسخَ قبل الرفض"
        entry.unlink()


def test_a_link_or_changed_file_after_inspection_is_still_caught_after_the_copy(tmp_path, monkeypatch):
    """النسخُ لا يتبع الروابط، والمنسوخُ يُطابَق ببصمة مصدره المفحوص (دفاعٌ بعد الفحص الأول)."""
    bank = _public_bank(tmp_path, "late")
    _sealed_fixture(tmp_path, bank)
    inspected = cli.inspect_open_split(bank)
    assert list(inspected) == ["a/kimi_x.json"]
    os.symlink(Path("../../sealed/x.json"), bank / "open" / "a" / "late.json")    # ظهر بعد الفحص
    monkeypatch.setattr(cli, "inspect_open_split", lambda _bank: dict(inspected))
    with pytest.raises(AutomaticReviewError) as linked:
        cli.prepare_run(bank, "T1")
    assert linked.value.code == "open_split_link_refused"
    (bank / "open" / "a" / "late.json").unlink()
    monkeypatch.setattr(cli, "inspect_open_split", lambda _bank: {"a/kimi_x.json": "0" * 64})
    with pytest.raises(AutomaticReviewError) as changed:
        cli.prepare_run(bank, "T2")
    assert changed.value.code == "open_split_copy_mismatch"


def test_bank_results_and_the_saved_summary_carry_the_free_backend_limits(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #174: حدودُ الواجهات المجانية من موضعٍ واحد في التجربة وفي نتيجة البنك وفي خلاصته المحفوظة."""
    from evaluation.external_review import MEASUREMENT_LIMITS
    assert cli.free_limits(["x"]) == sorted(["x", *cli.FREE_LIMITS])
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ids = ["c1", "c2", "c3"]
    bank = _public_bank(tmp_path, "limits")
    _free(monkeypatch, FreeOpener(replies={DS: [_ok(ids)], MI: [_ok(ids)]}))
    assert cli.main([str(bank), "--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--run-id", "Q1",
                     "--brief", str(BRIEF)]) == 0
    result = json.loads(capsys.readouterr().out)
    expected = sorted(set(MEASUREMENT_LIMITS) | set(cli.FREE_LIMITS))
    assert result["measurement_limits"] == expected
    saved = json.loads((bank / "runs" / "Q1" / "reviews" / "SUMMARY.json").read_text(encoding="utf-8"))
    assert saved["measurement_limits"] == expected and saved["run_id"] == "Q1"


def test_the_catalog_listing_and_its_saved_file_carry_the_free_backend_limits(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #174: جردُ الفهرس ينشر أعدادًا وعائلاتٍ ومرشّحين، فمعه حدودُه من الموضع الواحد."""
    _free(monkeypatch, FreeOpener(catalog=[_gh_full(DS), _gh_full(MI)]))
    out = tmp_path / "catalog.json"
    assert cli.main(["--backend", "github-models", "--list-catalog", str(out)]) == 0
    expected = cli.free_limits(cli.CATALOG_LIMITS)
    assert set(cli.FREE_LIMITS) <= set(expected) and set(cli.CATALOG_LIMITS) <= set(expected)
    assert _printed(capsys)["measurement_limits"] == expected
    assert json.loads(out.read_text(encoding="utf-8"))["measurement_limits"] == expected


def test_the_catalog_call_is_persisted_in_the_listing_and_in_a_refused_run(tmp_path, monkeypatch, capsys):
    """ملاحظةُ Codex على #290: صفُّ نداء الفهرس كان في الذاكرة وحدها؛ فالجردُ الناجح لا يحمله، والتشغيلُ المرفوض قبل الخلاصة
    لا يحفظ إلا حالتَه. صار في ملفّ الجرد ومطبوعه، وفي RUN.json للتشغيل المرفوض."""
    _free(monkeypatch, FreeOpener(catalog=[_gh_full(DS), _gh_full(MI)]))
    out = tmp_path / "catalog.json"
    assert cli.main(["--backend", "github-models", "--list-catalog", str(out)]) == 0
    printed, saved = _printed(capsys), json.loads(out.read_text(encoding="utf-8"))
    for report in (printed, saved):
        assert [(row["kind"], row["status"]) for row in report["provider_usage"]] == [("catalog", "succeeded")]

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    bank = _public_bank(tmp_path, "usage")
    _free(monkeypatch, FreeOpener(catalog=[_gh(DS)]))           # عائلةٌ صالحةٌ واحدة فلا زوج: رفضٌ قبل الخلاصة
    assert cli.main([str(bank), "--backend", "github-models", "--run-id", "U1", "--brief", str(BRIEF)]) == 2
    assert [row["kind"] for row in _printed(capsys)["provider_usage"]] == ["catalog"]
    state = json.loads((bank / "runs" / "U1" / "reviews" / "RUN.json").read_text(encoding="utf-8"))
    assert state["status"] == "refused" and [row["kind"] for row in state["provider_usage"]] == ["catalog"]


def test_a_quota_history_keeps_a_service_refusal_failed_not_unavailable(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #174: مراجعٌ نفدت حصّتُه فاستُبدل، ثم رفضت الخدمةُ المجموعةَ الأخيرة كلَّها — إخفاقٌ مختلط يبقى failed."""
    ok = b"OK\r\n"
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    bank = _public_bank(tmp_path, "mixed")
    _free(monkeypatch, FreeOpener(replies={DS: [429], MI: [ok], LL: [ok]}))
    assert cli.main([str(bank), "--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--fallback", LL,
                     "--brief", str(BRIEF)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "failed" and "unavailable_codes" not in result
    assert result["error_codes"] == ["quota_exhausted", "response_not_json"]
    out = tmp_path / "smoke.json"
    _free(monkeypatch, FreeOpener(catalog=[_gh(DS), _gh(MI), _gh(LL)], replies={DS: [429], MI: [ok], LL: [ok]}))
    assert cli.main(["--backend", "github-models", "--smoke", str(out), "--brief", str(BRIEF)]) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "failed"


def test_the_compact_smoke_lines_carry_the_limits(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #174: السطرُ المضغوط هو ما يصل ملخّصَ المهمّة، فمعه حدودُه كما في التقرير المحفوظ."""
    _free(monkeypatch, FreeOpener(catalog=[_gh(DS), _gh(MI), _gh(LL)], replies={DS: [CATCH], MI: [CATCH], LL: [CATCH]}))
    for extra in ([], ["--every-family"]):
        out = tmp_path / f"smoke{len(extra)}.json"
        assert cli.main(["--backend", "github-models", "--smoke", str(out), *extra]) == 0
        printed = _printed(capsys)
        saved = json.loads(out.read_text(encoding="utf-8"))
        assert printed["measurement_limits"] == saved["measurement_limits"]
        assert set(cli.FREE_LIMITS) <= set(printed["measurement_limits"])


def test_a_record_reviewed_again_after_its_file_changed_counts_as_reviewed_not_skipped(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #174: السجلُّ القديم الذي أُعيدت مراجعتُه لتغيّر ملفّه يُعدّ reviewed لا skipped."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ids = ["c1", "c2", "c3"]
    bank = _public_bank(tmp_path, "rereview")
    args = [str(bank), "--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--brief", str(BRIEF)]
    _free(monkeypatch, FreeOpener(replies={DS: [_ok(ids)], MI: [_ok(ids)]}))
    assert cli.main(args) == 0
    capsys.readouterr()
    source = bank / "open" / "a" / "kimi_x.json"
    source.write_text(json.dumps(json.loads(source.read_text(encoding="utf-8")), ensure_ascii=False, indent=1),
                      encoding="utf-8")                       # المحتوى نفسُه بتنسيقٍ آخر: بصمةٌ أخرى فمراجعةٌ جديدة
    opener = _free(monkeypatch, FreeOpener(replies={DS: [_ok(ids)], MI: [_ok(ids)]}))
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert opener.chat_models() == [DS, MI], "أُعيدت المراجعةُ فعلًا"
    assert {k: result[k] for k in ("reviewed", "skipped", "failed")} == {"reviewed": 2, "skipped": 0, "failed": 0}


def test_a_multipart_reply_with_a_non_text_part_is_a_named_error_retried_once(tmp_path):
    """ملاحظة Codex على #174: جزءٌ نصُّه null لا يُسقط التشغيلَ بـTypeError، بل رمزٌ مسمًّى يُعاد مرّةً ويُسجَّل."""
    ids = ["c1", "c2", "c3"]
    chat = cli.OpenAICompatChat("github-models", KEY)
    chat.opener = FreeOpener(replies={DS: [([{"type": "text", "text": None}], "stop")]})
    with pytest.raises(AutomaticReviewError) as invalid:
        chat(DS, "s", "u", {})
    assert invalid.value.code == "response_part_invalid" and chat.failures[DS]["status"] == 200
    chat.opener = FreeOpener(replies={DS: [([{"type": "text", "text": None}], "stop")],
                                      MI: [([{"type": "text", "text": json.dumps(_ok(ids))}], "stop")]})
    bank = _bank(tmp_path)
    assert review_bank(bank, [DS, MI], chat, brief_path=BRIEF)["failed"] == 1
    record = json.loads((bank / "reviews" / DS.replace("/", "_") / "tier_a" / "kimi_t_a_001.json").read_text(encoding="utf-8"))
    assert record["error"] == "response_part_invalid" and len(record["attempts"]) == 2
    good = json.loads((bank / "reviews" / MI.replace("/", "_") / "tier_a" / "kimi_t_a_001.json").read_text(encoding="utf-8"))
    assert good["error"] is None, "الأجزاءُ النصيّةُ الصالحة تُجمع"


def test_a_rerun_recreates_the_superseded_history_instead_of_mixing_stale_records(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #174: تاريخُ استبدالٍ سابق (لملفٍّ حُذف منذئذٍ) لا يُعدّ في استبدال هذا التشغيل."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ids = ["c1", "c2", "c3"]
    bank = _public_bank(tmp_path, "rerun")
    first, second = bank / "open" / "a" / "kimi_x.json", bank / "open" / "a" / "kimi_y.json"
    second.write_bytes(first.read_bytes())
    args = [str(bank), "--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--fallback", LL,
            "--brief", str(BRIEF)]
    _free(monkeypatch, FreeOpener(replies={DS: [_ok(ids), 429], MI: [_ok(ids)], LL: [_ok(ids)]}))
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["superseded_completed"] == {DS: 1}
    second.rename(bank / "open" / "a" / "kimi_z.json")          # الملفُّ الثاني أُعيدت تسميتُه
    _free(monkeypatch, FreeOpener(replies={DS: [429], MI: [_ok(ids)], LL: [_ok(ids)]}))
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert sorted(entry["file"] for entry in result["superseded"]) == ["a/kimi_x.json", "a/kimi_z.json"]
    assert result["superseded_completed"] == {}, "ما أتمّه في التشغيل السابق ليس من هذا الاستبدال"
    history = bank / "reviews" / "superseded" / DS.replace("/", "_")
    assert sorted(p.relative_to(history).as_posix() for p in history.rglob("*.json")) == [
        "SUPERSEDED.json", "a/kimi_x.json", "a/kimi_z.json"], "التاريخُ على القرص أُنشئ من جديد، بلا سجلّ الاستبدال السابق"


def test_a_record_from_another_backend_is_not_reused_under_this_backend(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #174: معرّفُ النموذج نفسُه على واجهةٍ أخرى مراجعةٌ أخرى؛ والسجلُّ يحمل واجهتَه."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ids = ["c1", "c2", "c3"]
    bank = _public_bank(tmp_path, "backends")
    common = [str(bank), "--reviewer", DS, "--reviewer", MI, "--brief", str(BRIEF)]
    _free(monkeypatch, FreeOpener(replies={DS: [_ok(ids)], MI: [_ok(ids)]}))
    assert cli.main([*common, "--backend", "github-models"]) == 0
    capsys.readouterr()
    record_path = bank / "reviews" / DS.replace("/", "_") / "a" / "kimi_x.json"
    assert json.loads(record_path.read_text(encoding="utf-8"))["backend"] == "github-models"
    opener = _free(monkeypatch, FreeOpener(replies={DS: [_ok(ids)], MI: [_ok(ids)]}))
    assert cli.main([*common, "--backend", "hf-router"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert opener.chat_models() == [DS, MI], "سجلُّ github-models لا يُعاد استعمالُه تحت hf-router"
    assert {k: result[k] for k in ("reviewed", "skipped")} == {"reviewed": 2, "skipped": 0}
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert (record["backend"], record["endpoint_host"]) == ("hf-router", "router.huggingface.co")


def test_empty_and_truncated_replies_leave_their_shape_in_the_failures(tmp_path):
    """ملاحظة Codex على #174: الردُّ الفارغ والمبتور يتركان شكلَهما وطلبَهما كسائر الإخفاقات."""
    chat = cli.OpenAICompatChat("github-models", KEY)
    chat.opener = FreeOpener(replies={DS: [("  ", "stop")], MI: [(json.dumps(_ok(["c1"])), "length")]})
    for model, code in ((DS, "reply_empty"), (MI, "reply_incomplete")):
        with pytest.raises(AutomaticReviewError) as failed:
            chat(model, "s", "u", {})
        assert failed.value.code == code
        shape = chat.failures[model]
        assert (shape["status"], shape["content_type"], shape["top"]) == (200, "application/json", "dict")
        assert shape["request"] == {"method": "POST", "sent": GH_CHAT, "final": GH_CHAT}


def test_records_of_files_no_longer_in_the_open_split_are_ignored_everywhere(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #174: ملفٌّ أُعيدت تسميتُه أو حُذف بين تشغيلين لا يبقى سجلُّه في الاتفاق ولا قائمة المالك ولا الأعداد
    ولا النفاد ولا التاريخ."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ids = ["c1", "c2", "c3"]
    dispute = {"judgments": [_judgment("c1", reference="incorrect"), _judgment("c2"), _judgment("c3")]}

    def two_files(name: str) -> Path:
        bank = _public_bank(tmp_path, name)
        (bank / "open" / "a" / "kimi_y.json").write_bytes((bank / "open" / "a" / "kimi_x.json").read_bytes())
        return bank

    def run(bank: Path, replies: dict, *extra: str) -> tuple[int, dict]:
        _free(monkeypatch, FreeOpener(replies=replies))
        code = cli.main([str(bank), "--backend", "github-models", "--reviewer", DS, "--reviewer", MI, *extra,
                         "--brief", str(BRIEF)])
        return code, json.loads(capsys.readouterr().out)

    # ١) الاتفاقُ وقائمةُ المالك والأعداد
    bank = two_files("renamed")
    assert run(bank, {DS: [_ok(ids), dispute], MI: [_ok(ids)]})[1]["owner_queue"] == 1
    (bank / "open" / "a" / "kimi_y.json").rename(bank / "open" / "a" / "kimi_z.json")
    code, result = run(bank, {DS: [_ok(ids)], MI: [_ok(ids)]})
    assert code == 0 and result["pairs"][0]["items"] == 6 and result["owner_queue"] == 0, "خلافُ الملفّ القديم لا يُرفع"
    assert {k: result[k] for k in ("reviewed", "skipped", "failed")} == {"reviewed": 2, "skipped": 2, "failed": 0}
    # ٢) نفادُ حصّةٍ على ملفٍّ حُذف لا يستبدل المراجعَ في تشغيلٍ لاحق
    bank = two_files("quota")
    assert run(bank, {DS: [_ok(ids), 429], MI: [_ok(ids)]})[0] == 1
    (bank / "open" / "a" / "kimi_y.json").unlink()
    code, result = run(bank, {DS: [_ok(ids)], MI: [_ok(ids)]})
    assert code == 0 and result["status"] == "reviewed" and result["fallbacks"] == []
    # ٣) تاريخُ المستبدَل لا يحمل ملفًّا لم يعد في open/
    bank = two_files("history")
    assert run(bank, {DS: [_ok(ids)], MI: [_ok(ids)]})[0] == 0
    (bank / "open" / "a" / "kimi_y.json").rename(bank / "open" / "a" / "kimi_z.json")
    code, result = run(bank, {DS: [429], MI: [_ok(ids)], LL: [_ok(ids)]}, "--fallback", LL)
    assert code == 0 and result["superseded_completed"] == {DS: 1}
    assert [entry["file"] for entry in result["superseded"]] == ["a/kimi_z.json"]
    # ٤) أخطاءُ مراجعين آخرين تُروى لملفّات open/ الحالية وحدها
    _record(bank, "old-reviewer", "a/kimi_x.json", error="transport_error")
    _record(bank, "old-reviewer", "a/kimi_y.json", error="transport_error")
    assert cli.other_reviewer_errors(bank, {MI, LL}, {"a/kimi_x.json", "a/kimi_z.json"}) == 1


# ————— Groq وOpenRouter: سقفُ إنفاق صفر بلا شبكة —————

OR_DS = "deepseek/deepseek-r1:free"
OR_MI = "mistralai/mistral-small-3.1-24b-instruct:free"


def _priced(model, **pricing):
    return {"id": model, "architecture": {"output_modalities": ["text"]},
            "pricing": pricing or {"prompt": "0", "completion": "0", "request": "0"}}


class UsageOpener:
    """فهرسٌ وردود OpenAI كاملة، كي لا تُستعمل شبكة أو مفتاح حقيقي في حراس الإنفاق."""

    def __init__(self, catalog=None, replies=None):
        self.catalog = catalog or []
        self.replies = {model: list(values) for model, values in (replies or {}).items()}
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        if request.data is None:
            body = {"data": self.catalog}
        else:
            model = json.loads(request.data)["model"]
            values = self.replies[model]
            body = values.pop(0) if len(values) > 1 else values[0]
        return _Reply(json.dumps(body, ensure_ascii=False).encode(), "application/json", request.full_url)


def _completion(content, *, usage=True, cost="0"):
    body = {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}
    if usage:
        body["usage"] = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        if cost is not None:
            body["usage"]["cost"] = cost
    return body


def test_groq_refuses_before_network_without_an_explicit_free_tier_confirmation(monkeypatch):
    opener = UsageOpener()
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: opener)
    with pytest.raises(AutomaticReviewError) as refused:
        cli.build_free_transport("groq", environ={"GROQ_API_KEY": KEY})
    assert refused.value.code == "free_tier_unverified" and opener.requests == []
    chat = cli.build_free_transport(
        "groq", environ={"GROQ_API_KEY": KEY, "DIWAN_GROQ_FREE_TIER_CONFIRMED": "confirmed"})
    assert chat.endpoint_host == "api.groq.com"
    assert cli.resolve_reviewer("mistral-saba-24b", "groq")["family"] == "mistral"
    assert "confirmed" not in repr(chat) and "confirmed" not in json.dumps(chat.describe())


def test_openrouter_requires_a_free_suffix_and_all_catalog_prices_to_be_zero_before_chat():
    chat = cli.OpenAICompatChat("openrouter", KEY)
    for entry, code in [
        (_priced("deepseek/deepseek-r1", prompt="0", completion="0"), "free_model_required"),
        (_priced(OR_DS, prompt="0", completion="0.000001"), "free_price_unverified"),
        ({"id": OR_DS, "pricing": {}}, "free_price_unverified"),
        # بندٌ غائب غيرُ مُثبَت: ما حضر صفرًا لا يشهد لسعر المدخل أو المخرج
        ({"id": OR_DS, "pricing": {"request": "0"}}, "free_price_unverified"),
        ({"id": OR_DS, "pricing": {"prompt": "0", "request": "0"}}, "free_price_unverified"),
        ({"id": OR_DS, "pricing": {"completion": "0"}}, "free_price_unverified"),
    ]:
        with pytest.raises(AutomaticReviewError) as refused:
            chat.approve_zero_spend([entry], [entry["id"]])
        assert refused.value.code == code
    with pytest.raises(AutomaticReviewError) as refused:
        chat(OR_DS, "s", "u", {})
    assert refused.value.code == "free_price_unverified" and chat.provider_usage[-1]["request_sent"] is False


def test_openrouter_mock_sends_no_paid_fallback_and_persists_zero_cost_usage(tmp_path, monkeypatch):
    catalog = [_priced(OR_DS), _priced(OR_MI)]
    opener = UsageOpener(catalog, {OR_DS: [_completion(json.dumps(CATCH, ensure_ascii=False))],
                                           OR_MI: [_completion(json.dumps(CATCH, ensure_ascii=False))]})
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: opener)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    out = tmp_path / "openrouter.json"
    assert cli.main(["--backend", "openrouter", "--smoke", str(out),
                     "--reviewer", OR_DS, "--reviewer", OR_MI]) == 0
    sent = [json.loads(request.data) for request in opener.requests if request.data is not None]
    assert all(payload["provider"] == {"allow_fallbacks": False} for payload in sent)
    assert all(payload["usage"] == {"include": True} and "models" not in payload for payload in sent)
    report = json.loads(out.read_text(encoding="utf-8"))
    # نداءُ الفهرس صفٌّ في سجلّ النداءات أيضًا، ودليلُ المجانية بنودُ السعر ولحظتُها لا عبارتُه وحدها (#285)
    catalog_rows = [row for row in report["provider_usage"] if row.get("kind") == "catalog"]
    chat_rows = [row for row in report["provider_usage"] if row.get("kind") != "catalog"]
    assert len(catalog_rows) == 1 and catalog_rows[0]["status"] == "succeeded" and catalog_rows[0]["model"] is None
    assert len(chat_rows) == 2
    assert all(row["status"] == "succeeded" and row["cost_usd"] == "0" for row in chat_rows)
    assert all(row["zero_spend_proof"] == "catalog_free_suffix_and_all_pricing_zero" for row in chat_rows)
    evidence = report["zero_spend_evidence"]
    assert sorted(evidence) == sorted([OR_DS, OR_MI]) and report["cost_unconfirmed_attempts"] == 0
    assert all(e["pricing"] and set(e["pricing"].values()) == {"0"} and e["observed_at"] == catalog_rows[0]["at"]
               and e["catalog"] == "https://openrouter.ai/api/v1/models" for e in evidence.values())
    assert "zero_spend_guard_is_provider_specific_and_not_a_general_price_attestation" in report["measurement_limits"]
    assert KEY not in out.read_text(encoding="utf-8")


def test_openrouter_missing_or_nonzero_reported_cost_is_a_named_failure():
    chat = cli.OpenAICompatChat("openrouter", KEY)
    chat.approve_zero_spend([_priced(OR_DS)], [OR_DS])
    chat.opener = UsageOpener(replies={OR_DS: [_completion("{}", cost=None), _completion("{}", cost="0.01")]})
    with pytest.raises(AutomaticReviewError) as missing:
        chat(OR_DS, "s", "u", {})
    with pytest.raises(AutomaticReviewError) as breached:
        chat(OR_DS, "s", "u", {})
    assert missing.value.code == "usage_cost_unavailable"
    assert breached.value.code == "zero_spend_breach"
    assert [row["status"] for row in chat.provider_usage] == ["error", "error"]
    assert chat.provider_usage[0]["cost_usd"] is None
    assert chat.provider_usage[1]["cost_usd"] == "0.01"


def test_an_openrouter_attempt_sent_without_a_confirmed_cost_is_counted_not_reported_clean():
    """تدقيقٌ لاحق (#285): انقطاعٌ أو ردٌّ ليس JSON بعد إرسال الطلب يُعاد ولا يوقف التشغيل، وكلفتُه مجهولة؛ فيُعدّ باسمه."""
    chat = cli.OpenAICompatChat("openrouter", KEY)
    chat.approve_zero_spend([_priced(OR_DS)], [OR_DS])
    assert chat.zero_spend_evidence[OR_DS]["pricing"] and chat.spend_report()["cost_unconfirmed_attempts"] == 0

    def fail(*args, **kwargs):
        chat.last_request[OR_DS] = {"method": "POST", "sent": "https://openrouter.ai/api/v1/chat/completions",
                                    "final": None}
        raise AutomaticReviewError("transport_timeout", OR_DS)
    chat._send = fail
    with pytest.raises(AutomaticReviewError):
        chat(OR_DS, "s", "u", {})
    assert chat.provider_usage[-1]["request_sent"] is True
    assert chat.spend_report()["cost_unconfirmed_attempts"] == 1


def test_groq_mock_logs_tokens_but_never_invents_an_unreported_zero_cost():
    model = "mistral-saba-24b"
    chat = cli.OpenAICompatChat("groq", KEY, free_tier_confirmation="confirmed")
    chat.opener = UsageOpener(replies={model: [_completion("{}", cost=None)]})
    assert chat(model, "s", "u", {}) == "{}"
    assert chat.provider_usage == [{
        "provider": "groq", "model": model, "family": "mistral",
        "at": chat.provider_usage[0]["at"], "elapsed_ms": chat.provider_usage[0]["elapsed_ms"],
        "status": "succeeded", "error": None, "request_sent": True,
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        "cost_usd": None, "cost_status": "not_reported",
        "zero_spend_proof": "operator_confirmed_account_free_tier",
    }]


def test_a_free_rerun_on_a_reviewed_bank_keeps_the_bank_ledger(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #298: إعادةُ واجهةٍ مجانية على بنكٍ مراجَع تتخطّى سجلّاته فلا تُرسل نداءً، وكانت تستبدل سجلَّ
    الخلاصة ومجموعَها بسجلّها الفارغ. والآن يُلحَق بهما؛ والمطبوعُ تقريرُ هذا التشغيل وحده."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ids = ["c1", "c2", "c3"]
    args = ["--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--brief", str(BRIEF)]
    bank = _public_bank(tmp_path, "rerun")
    _free(monkeypatch, FreeOpener(replies={DS: [_ok(ids)], MI: [_ok(ids)]}))
    assert cli.main([str(bank), *args]) == 0
    capsys.readouterr()
    summary_path = bank / "reviews" / "SUMMARY.json"
    first = json.loads(summary_path.read_text(encoding="utf-8"))
    assert sorted(first["token_totals"]) == sorted([DS, MI])
    # ما سجّله تشغيلٌ سابقٌ على OpenRouter: دليلُ مجانيته في الخلاصة، ونداءٌ لم تثبت كلفتُه في السجلّ الدائم؛ يبقيان للبنك
    ledger_path = bank / "reviews" / cli.LEDGER_FILE
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert ledger["provider_usage"] == first["provider_usage"], "السجلُّ الدائم والخلاصةُ سجلٌّ واحد"
    first["zero_spend_evidence"] = {"earlier/model:free": {"proof": "catalog_free_suffix_and_all_pricing_zero"}}
    first["provider_usage"].append({"provider": "openrouter", "model": "earlier/model:free", "request_sent": True,
                                    "usage": None, "cost_status": "not_reported"})
    summary_path.write_text(json.dumps(first), encoding="utf-8")
    ledger_path.write_text(json.dumps({**ledger, "provider_usage": first["provider_usage"],
                                       "zero_spend_evidence": first["zero_spend_evidence"]}), encoding="utf-8")
    again = _free(monkeypatch, FreeOpener(replies={DS: [_ok(ids)], MI: [_ok(ids)]}))
    assert cli.main([str(bank), *args]) == 0
    printed = json.loads(capsys.readouterr().out)
    second = json.loads((bank / "reviews" / "SUMMARY.json").read_text(encoding="utf-8"))
    assert again.chat_models() == [], "البنكُ مراجَعٌ فلا نداء"
    assert printed["token_totals"] == {}, "المطبوعُ لهذا التشغيل وحده"
    assert second["provider_usage"][:len(first["provider_usage"])] == first["provider_usage"]
    assert second["token_totals"] == {**first["token_totals"], "earlier/model:free": {
        "calls": 1, "calls_with_incomplete_usage": 1, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
    assert second["zero_spend_evidence"] == first["zero_spend_evidence"]
    assert second["cost_unconfirmed_attempts"] == 1 and printed["cost_unconfirmed_attempts"] == 0


def test_free_calls_reach_the_bank_ledger_when_an_unnamed_failure_follows_them(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #298: خطأٌ غيرُ مسمًّى بعد الإرسال (سجلٌّ لم يعد يُقرأ) كان يُسقط التشغيلَ قبل كتابة السجلّ الدائم،
    والمعالجُ يلتقط الرفضَ المسمّى وحده. والآن يُكتب على أيّ خروج."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ids = ["c1", "c2", "c3"]
    args = ["--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--brief", str(BRIEF)]
    bank = _public_bank(tmp_path, "unnamed")
    _free(monkeypatch, FreeOpener(replies={DS: [_ok(ids)], MI: [_ok(ids)]}))

    def unreadable(*args, **kwargs):
        raise json.JSONDecodeError("truncated record", "{", 1)

    monkeypatch.setattr(cli, "_quota_models", unreadable)
    with pytest.raises(json.JSONDecodeError):
        cli.main([str(bank), *args])
    ledger = json.loads((bank / "reviews" / cli.LEDGER_FILE).read_text(encoding="utf-8"))
    assert sorted(row["model"] for row in ledger["provider_usage"] if row.get("kind") != "catalog") == sorted([DS, MI])


def test_an_openrouter_run_that_fails_after_sending_keeps_its_zero_spend_evidence_for_the_rerun(
        tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #298: تشغيلُ OpenRouter أرسل ثم سقط قبل الخلاصة؛ فبنودُ السعر ولحظةُ قراءتها في السجلّ الدائم مع
    نداءاته، والإعادةُ التي تتخطّى ما رُوجع تنشرها معها، لا عبارةَ zero_spend_proof وحدها."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    reply = _completion(json.dumps(_ok(["c1", "c2", "c3"]), ensure_ascii=False))
    args = ["--backend", "openrouter", "--reviewer", OR_DS, "--reviewer", OR_MI, "--brief", str(BRIEF)]
    bank = _public_bank(tmp_path, "evidence")
    opener = UsageOpener([_priced(OR_DS), _priced(OR_MI)], {OR_DS: [reply], OR_MI: [reply]})
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: opener)
    real = cli._quota_models

    def unreadable(*args, **kwargs):
        raise json.JSONDecodeError("truncated record", "{", 1)

    monkeypatch.setattr(cli, "_quota_models", unreadable)
    with pytest.raises(json.JSONDecodeError):
        cli.main([str(bank), *args])
    ledger = json.loads((bank / "reviews" / cli.LEDGER_FILE).read_text(encoding="utf-8"))
    catalog_at = [row["at"] for row in ledger["provider_usage"] if row.get("kind") == "catalog"]
    assert sorted(ledger["zero_spend_evidence"]) == sorted([OR_DS, OR_MI])
    monkeypatch.setattr(cli, "_quota_models", real)
    again = UsageOpener([_priced(OR_DS), _priced(OR_MI)], {OR_DS: [reply], OR_MI: [reply]})
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: again)
    assert cli.main([str(bank), *args]) == 0
    summary = json.loads((bank / "reviews" / "SUMMARY.json").read_text(encoding="utf-8"))
    assert [r for r in again.requests if r.data is not None] == [], "البنكُ مراجَعٌ فلا نداءَ مراجعة"
    assert summary["zero_spend_evidence"] == ledger["zero_spend_evidence"]
    assert all(e["observed_at"] in catalog_at and set(e["pricing"].values()) == {"0"}
               for e in summary["zero_spend_evidence"].values())


def test_a_free_refusal_after_sending_keeps_its_calls_in_the_bank_ledger(tmp_path, monkeypatch, capsys):
    """ملاحظة Codex على #298: رفضٌ بعد الإرسال يطبع نداءاتِه ولا يكتب خلاصة؛ فهي في السجلّ الدائم، والتشغيلُ التالي يُلحق بها."""
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    ids = ["c1", "c2", "c3"]
    args = ["--backend", "github-models", "--reviewer", DS, "--reviewer", MI, "--brief", str(BRIEF)]
    bank = _public_bank(tmp_path, "refused")
    # رفضٌ بعد نداء الفهرس: عائلةٌ صالحةٌ واحدة فلا زوج (كما في اختبار مجلّد التشغيل)، ونداءُ الفهرس خرج
    _free(monkeypatch, FreeOpener(catalog=[_gh(DS)]))
    assert cli.main([str(bank), "--backend", "github-models", "--brief", str(BRIEF)]) == 2
    refused = _printed(capsys)
    assert refused["code"] == "reviewers_unavailable" and [r["kind"] for r in refused["provider_usage"]] == ["catalog"]
    ledger = json.loads((bank / "reviews" / cli.LEDGER_FILE).read_text(encoding="utf-8"))
    assert ledger["provider_usage"] == refused["provider_usage"]
    _free(monkeypatch, FreeOpener(replies={DS: [_ok(ids)], MI: [_ok(ids)]}))
    assert cli.main([str(bank), *args]) == 0
    summary = json.loads((bank / "reviews" / "SUMMARY.json").read_text(encoding="utf-8"))
    assert summary["provider_usage"][:len(refused["provider_usage"])] == refused["provider_usage"]
