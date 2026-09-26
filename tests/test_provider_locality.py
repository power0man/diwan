"""المحليّةُ من الاسم والمضيف لا من الإعلان (جديد-is-local-guard، الفجوة core-g1).

`OllamaProvider` كان يعلن `is_local = True` لكل نموذج، فوصلت حمولةُ `local_only` إلى
`gpt-oss:120b-cloud` فعلًا. وهنا يُثبت كلُّ موضعٍ يقرّر المحليّةَ أنه يقرأ المصدرَ الواحد
(`core/locality.py`): المزوّد، والنواة بفحصٍ ثانٍ على `req.model`، وحارسُ المزوّد في الجلسة
الوكيلة، وبدءُ جولتها واستئنافُها، والمحادثةُ النصية، والواجهة. والمزوّداتُ هنا تُعلن
`is_local = True` عمدًا: الإعلانُ وحده كان يكفي، ولا يكفي بعد اليوم.
"""
from __future__ import annotations

import uuid

import pytest

from agent.registry import Tool, ToolRegistry
from conversation import ChatSession
from conversation.agent_session import AgentSession, _ProviderGuard
from conversation.session import ConversationError
from core.budget import Budget
from core.contracts import Message, Request, Response, ToolCall, ToolSpec, Usage
from core.ledger import Ledger
from core.locality import is_cloud_model, is_local_provider, is_loopback_url
from core.run import RouteRefused, execute
from providers.ollama import BASE_URL, DEFAULT_MODEL, OllamaProvider

CLOUD = "gpt-oss:120b-cloud"          # النموذجُ الذي وصلته الحمولةُ فعلًا
REMOTE = "http://192.168.1.20:11434"  # خادمُ Ollama على جهازٍ آخر في الشبكة


def _say(text="تم", calls=()):
    return Response(text, Usage(1, 1), "complete", 0, tool_calls=tuple(calls))


class Declared:
    """مزوّدٌ يعلن المحليّةَ ويعلن معها نموذجَه ومضيفَه، كما يفعل OllamaProvider."""
    name, is_local = "declared", True

    def __init__(self, *outputs, model=DEFAULT_MODEL, base_url=BASE_URL):
        self.model, self.base_url = model, base_url
        self.outputs, self.requests, self.estimates = list(outputs), [], 0

    def estimate_micros(self, request):
        self.estimates += 1
        return 0

    def complete(self, request):
        self.requests.append(request)
        return self.outputs.pop(0)


LEAKY = [pytest.param({"model": CLOUD}, id="cloud-model"), pytest.param({"base_url": REMOTE}, id="remote-host")]


# ── المصدرُ الواحد ──

@pytest.mark.parametrize("model", [CLOUD, "kimi-k2.6:cloud", "deepseek-v4-flash:cloud",
                                   "mistral-large-3:675b-cloud", "cloud/qwen", "x:CLOUD", "x:cloudy", None, 7])
def test_a_cloud_name_or_a_non_name_is_cloud(model):
    assert is_cloud_model(model) is True


@pytest.mark.parametrize("model", [DEFAULT_MODEL, "qwen3:14b", "gemma4", "soundcloudish", "command-r7b-arabic:7b"])
def test_a_local_name_is_not_cloud(model):
    assert is_cloud_model(model) is False


@pytest.mark.parametrize("url", [BASE_URL, "http://localhost:11434", "http://[::1]:11434",
                                 "http://127.9.0.1:11434", "https://127.0.0.1"])
def test_loopback_hosts_are_local(url):
    assert is_loopback_url(url) is True


@pytest.mark.parametrize("url", [REMOTE, "https://ollama.com", "http://127.0.0.1@ollama.com:11434",
                                 "http://localhost.ollama.com:11434", "ftp://127.0.0.1", "127.0.0.1:11434",
                                 "http://127.0.0.1:99999", "http://0.0.0.0:11434", "", None])
def test_every_other_host_is_not_local(url):
    assert is_loopback_url(url) is False


def test_the_declaration_still_has_to_be_true_itself():
    assert is_local_provider(Declared()) is True
    truthy = Declared()
    truthy.is_local = 1
    assert is_local_provider(truthy) is False


@pytest.mark.parametrize("leak", LEAKY)
def test_a_declaration_contradicted_by_name_or_host_is_not_local(leak):
    assert is_local_provider(Declared(**leak)) is False


# ── المزوّد ──

def test_ollama_derives_locality_from_its_model_and_host():
    assert OllamaProvider().is_local is True
    assert OllamaProvider(DEFAULT_MODEL, "http://localhost:11434").is_local is True
    assert OllamaProvider(CLOUD).is_local is False
    assert OllamaProvider(DEFAULT_MODEL, REMOTE).is_local is False


# ── النواة ──

def _req(model=DEFAULT_MODEL, policy="local_only"):
    return Request(messages=(Message("user", "سرّ"),), model=model, model_version="1", max_output=16,
                   deadline_s=5.0, data_policy=policy, idempotency_key=None)


@pytest.mark.parametrize("policy", ["local_only", "regulated"])
@pytest.mark.parametrize("provider", [lambda: OllamaProvider(CLOUD), lambda: OllamaProvider(DEFAULT_MODEL, REMOTE),
                                      lambda: Declared(model=CLOUD), lambda: Declared(base_url=REMOTE)],
                         ids=["ollama-cloud", "ollama-remote", "declared-cloud", "declared-remote"])
def test_the_core_never_hands_a_local_payload_to_a_cloud_model(tmp_path, policy, provider):
    ledger = Ledger(tmp_path / "l.jsonl")
    with pytest.raises(RouteRefused) as refused:
        execute(_req(policy=policy), provider(), Budget(10_000, 10_000), ledger)
    assert refused.value.code == "policy_requires_local"
    assert ledger.entries()[0]["record"]["error_code"] == "policy_requires_local"


def test_the_core_checks_the_requested_model_even_when_the_provider_names_none(tmp_path):
    class Anonymous:
        name, is_local = "anonymous", True
        def estimate_micros(self, request): raise AssertionError("ما كان ينبغي أن يُقدَّر")
        def complete(self, request): raise AssertionError("ما كان ينبغي أن يُنادى")

    with pytest.raises(RouteRefused) as refused:
        execute(_req(model=CLOUD), Anonymous(), Budget(10_000, 10_000), Ledger(tmp_path / "l.jsonl"))
    assert refused.value.code == "policy_requires_local"


def test_a_public_payload_may_still_reach_a_cloud_model(tmp_path):
    provider = Declared(_say("جواب"), model=CLOUD)
    outcome = execute(_req(model=CLOUD, policy="public"), provider, Budget(10_000, 10_000),
                      Ledger(tmp_path / "l.jsonl"))
    assert outcome.response.content == "جواب" and len(provider.requests) == 1


# ── المحادثة النصية ──

@pytest.mark.parametrize("leak", LEAKY)
def test_a_chat_turn_never_reaches_a_cloud_provider(tmp_path, leak):
    session = ChatSession(tmp_path.resolve() / "chat", "s", model="m", model_version="v")
    provider = Declared(_say("لا يصل"), **leak)
    with pytest.raises(ConversationError, match="policy_requires_local"):
        session.turn("one", "سرّ", provider)
    assert provider.estimates == 0 and not provider.requests and session.history() == []


# ── الجلسة الوكيلة ──

@pytest.fixture
def agent(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def write(args, context):
        action = context.journal.write_file(args["path"], args["content"])
        return {"content": "written", "action_id": action.action_id}
    tools = ToolRegistry(Tool(ToolSpec("write", "write", {}, "owner", True), write))
    return lambda: AgentSession(tmp_path / "control", "session", workspace_root=workspace,
                                project_id="project", registry=tools, model="m", model_version="v")


@pytest.mark.parametrize("leak", LEAKY)
def test_the_provider_guard_reads_the_one_source(leak):
    assert _ProviderGuard(None, None, None, Declared()).is_local is True
    assert _ProviderGuard(None, None, None, Declared(**leak)).is_local is False


@pytest.mark.parametrize("leak", LEAKY)
def test_an_agent_turn_never_starts_on_a_cloud_provider(agent, leak):
    provider = Declared(_say("لا يصل"), **leak)
    with pytest.raises(ConversationError, match="policy_requires_local"):
        agent().start_turn("t1", "سرّ", provider)
    assert provider.estimates == 0 and not provider.requests


@pytest.mark.parametrize("leak", LEAKY)
def test_an_agent_turn_never_resumes_on_a_cloud_provider(agent, leak):
    session = agent()
    pending = session.start_turn("t1", "اكتب", Declared(_say(calls=(
        ToolCall("c1", "write", {"path": "a.txt", "content": "x"}),))))
    assert pending["status"] == "awaiting_owner"
    first = pending["pending"][0]
    session.decide(first["action_id"], first["call_digest"], True, first["revision"])
    provider = Declared(_say("لا يصل"), **leak)
    with pytest.raises(ConversationError, match="policy_requires_local"):
        agent().resume("t1", provider)
    assert provider.estimates == 0 and not provider.requests


# ── الواجهة ──

@pytest.mark.parametrize("leak", LEAKY)
def test_the_web_ui_refuses_before_the_session_is_touched(tmp_path, monkeypatch, leak):
    from tests.test_webui_agent_default import ask_payload, new_session, serving

    started = []
    monkeypatch.setattr(AgentSession, "start_turn", lambda self, *args, **kwargs: started.append(args))
    with serving(tmp_path.resolve() / "ui") as running:
        leaky = Declared(**leak)
        running.providers["agent"] = leaky
        context, _ = new_session(running)
        status, refused, _ = running.request({**ask_payload(context, "agent_ask"), "turn": uuid.uuid4().hex})
        assert refused["error_code"] == "policy_requires_local", (status, refused)
        assert started == [] and leaky.estimates == 0 and not leaky.requests
