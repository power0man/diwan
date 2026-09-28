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
    assert opener.chat_models() == [DS, DS, MI, LL], "النافدُ يُعاد مرّةً، والباقي لا يُعاد نداؤه"
    assert _printed(capsys)["fallbacks"] == report["fallbacks"]


def test_quota_with_no_other_family_left_is_a_named_failure(tmp_path, monkeypatch, capsys):
    assert cli.http_code(429) == cli.http_code(402) == "quota_exhausted"
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
        f"- uses: actions/checkout@{pin.group(1)} # v4"]
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
    assert text.count("GITHUB_TOKEN: ${{ github.token }}") == 4
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
    assert _printed(capsys) == {"status": "refused", "code": "catalog_malformed",
                                "shape": {"status": 200, "content_type": "text/plain", "bytes": 4, "top": "not_json"}}
    out = tmp_path / "smoke.json"
    assert cli.main(["--backend", "github-models", "--smoke", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "passed" and set(report["reviewers"]) == {DS, MI}
    assert report["candidates_from"] == "preferred_list" and report["catalog_error"] == "catalog_malformed"
    assert _printed(capsys)["catalog_error"] == "catalog_malformed"


def test_http_statuses_are_named_and_only_a_safe_shape_of_the_body_is_recorded():
    assert [cli.http_code(c) for c in (401, 402, 403, 404, 413, 429, 500)] == [
        "unauthorized", "quota_exhausted", "forbidden", "not_found", "request_too_large", "quota_exhausted", "http_500"]
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
