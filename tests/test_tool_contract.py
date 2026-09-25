"""عقدُ الأدوات: إضافةٌ لا تعديل، وفحصٌ لنداءٍ غيرِ موثوق (الحلقة — الشريحة ١)."""
from __future__ import annotations

import pytest

from core.budget import Budget
from core.canonical import PayloadRejected, digest
from core.contracts import Message, Request, Response, ToolCall, ToolSpec, Usage
from core.ledger import Ledger
from core.run import RouteRefused, execute
from core.validate import validated

READ = ToolSpec("read_file", "يقرأ ملفًا نصّيًّا", {"type": "object"}, consent="auto")
WRITE = ToolSpec("write_file", "يكتب ملفًا داخل مساحة العمل", {"type": "object"},
                 consent="logged", reversible=True)


def request(*, tools=(), messages=None, key=None):
    return Request(messages=messages or (Message("user", "اقرأ الملف"),),
                   model="fixture", model_version="v1", max_output=64,
                   deadline_s=5.0, data_policy="local_only", idempotency_key=key,
                   tools=tools)


class Provider:
    """مزوّدٌ مكتوبٌ سلفًا — لا شبكة، فالحلقةُ تُقاس لا تُحاكى."""
    name = "scripted"
    is_local = True

    def __init__(self, *responses):
        self.queue = list(responses)
        self.seen = []

    def estimate_micros(self, req):
        return 0

    def complete(self, req):
        self.seen.append(req)
        return self.queue.pop(0)


def answer(content="تم", *, calls=()):
    return Response(content, Usage(1, 1), "complete", 0, provider="scripted",
                    model_version="v1", tool_calls=tuple(calls))


# ————— الخاصيّةُ الحاكمة: البصمةُ لا تنزاح —————

def test_a_tool_free_request_fingerprints_exactly_as_before():
    """لو انزاحت، بطلت إعادةُ عرضِ كلِّ تشغيلةٍ سابقة وكلُّ قيدٍ مختوم."""
    payload = request().fingerprint_payload()
    assert payload == {
        "messages": [{"role": "user", "content": "اقرأ الملف"}],
        "model": "fixture", "model_version": "v1", "max_output": 64,
        "data_policy": "local_only", "tools": [], "schema_version": 1}
    assert "tool_call_id" not in payload["messages"][0]


def test_declaring_tools_changes_the_fingerprint():
    """وإلّا تبادل تشغيلان بأدواتٍ مختلفة إعادةَ العرض فصار الرقمُ كذبًا."""
    assert digest(request().fingerprint_payload()) != \
        digest(request(tools=(READ,)).fingerprint_payload())


def test_the_consent_grade_is_part_of_the_fingerprint():
    loose = ToolSpec("write_file", WRITE.description, WRITE.parameters,
                     consent="owner", reversible=True)
    assert digest(request(tools=(WRITE,)).fingerprint_payload()) != \
        digest(request(tools=(loose,)).fingerprint_payload())


# ————— الإذنُ تعهّدٌ لا وصف —————

def test_logged_consent_is_refused_for_an_irreversible_tool():
    bad = ToolSpec("send_mail", "يرسل بريدًا", {}, consent="logged", reversible=False)
    with pytest.raises(PayloadRejected) as exc:
        validated(request(tools=(bad,)))
    assert exc.value.code == "tool_consent_requires_reversible"


@pytest.mark.parametrize("spec,code", [
    (ToolSpec("Read_File", "وصف", {}), "tool_name"),
    (ToolSpec("../escape", "وصف", {}), "tool_name"),
    (ToolSpec("ok", "   ", {}), "tool_description"),
    (ToolSpec("ok", "وصف", []), "tool_parameters"),
    (ToolSpec("ok", "وصف", {}, consent="maybe"), "tool_consent"),
    (ToolSpec("ok", "وصف", {}, reversible="yes"), "tool_reversible"),
])
def test_malformed_tool_specs_are_refused_by_name(spec, code):
    with pytest.raises(PayloadRejected) as exc:
        validated(request(tools=(spec,)))
    assert exc.value.code == code


def test_duplicate_tool_names_are_refused():
    with pytest.raises(PayloadRejected) as exc:
        validated(request(tools=(READ, READ)))
    assert exc.value.code == "tool_duplicate"


def test_a_plain_string_is_no_longer_a_tool():
    with pytest.raises(PayloadRejected) as exc:
        validated(request(tools=("read_file",)))
    assert exc.value.code == "tool_spec_type"


# ————— رسالةُ الأداة تُنسب إلى ندائها —————

def test_a_tool_result_without_its_call_is_refused():
    with pytest.raises(PayloadRejected) as exc:
        validated(request(messages=(Message("user", "س"), Message("tool", "نتيجة"))))
    assert exc.value.code == "tool_call_id_required"


def test_only_a_tool_message_carries_a_call_id():
    with pytest.raises(PayloadRejected) as exc:
        validated(request(messages=(Message("user", "س", tool_call_id="c1"),)))
    assert exc.value.code == "tool_call_id_unexpected"


def test_a_well_formed_tool_turn_validates():
    req = request(tools=(READ,), messages=(
        Message("user", "اقرأ"), Message("assistant", "", tool_calls=(ToolCall("c1", "read_file", {}),)),
        Message("tool", '{"ok": true}', tool_call_id="c1")))
    assert validated(req) is req


# ————— نداءُ الأداة مدخلٌ غير موثوق —————

def _run(provider, tmp_path, **kw):
    return execute(request(**kw), provider, Budget(0, 0), Ledger(tmp_path / "l.jsonl"))


def test_a_call_to_an_undeclared_tool_is_refused(tmp_path):
    provider = Provider(answer(calls=[ToolCall("c1", "delete_everything", {})]))
    with pytest.raises(RouteRefused) as exc:
        _run(provider, tmp_path, tools=(READ,))
    assert exc.value.code == "tool_call_undeclared"


def test_calls_without_any_declared_tool_are_refused(tmp_path):
    """مزوّدٌ يردّ أدواتٍ لم تُطلَب منه يخالف عقدَه — فشلٌ مغلق."""
    provider = Provider(answer(calls=[ToolCall("c1", "read_file", {})]))
    with pytest.raises(RouteRefused) as exc:
        _run(provider, tmp_path)
    assert exc.value.code == "tool_calls_unsolicited"


def test_duplicate_call_ids_are_refused(tmp_path):
    provider = Provider(answer(calls=[ToolCall("c1", "read_file", {}),
                                      ToolCall("c1", "read_file", {})]))
    with pytest.raises(RouteRefused) as exc:
        _run(provider, tmp_path, tools=(READ,))
    assert exc.value.code == "tool_call_id_duplicate"


@pytest.mark.parametrize("call,code", [
    (ToolCall("../c1", "read_file", {}), "tool_call_id_invalid"),
    (ToolCall("", "read_file", {}), "tool_call_id_invalid"),
    (ToolCall("c1", "read_file", ["not", "a", "dict"]), "tool_call_arguments"),
])
def test_malformed_calls_are_refused_by_name(tmp_path, call, code):
    with pytest.raises(RouteRefused) as exc:
        _run(Provider(answer(calls=[call])), tmp_path, tools=(READ,))
    assert exc.value.code == code


def test_a_declared_call_passes_and_is_recorded(tmp_path):
    call = ToolCall("c1", "read_file", {"path": "README.md"})
    ledger = Ledger(tmp_path / "l.jsonl")
    outcome = execute(request(tools=(READ,)), Provider(answer(calls=[call])),
                      Budget(0, 0), ledger)
    assert outcome.response.tool_calls == (call,)
    record = ledger.entries()[-1]["record"]
    assert record["response"]["tool_calls"] == [
        {"call_id": "c1", "name": "read_file", "arguments": {"path": "README.md"}}]


def test_a_tool_free_turn_writes_no_tool_key_into_the_ledger(tmp_path):
    """قيودُ ما قبل الأدوات تبقى كما هي — ولا يُضاف حقلٌ فارغ."""
    ledger = Ledger(tmp_path / "l.jsonl")
    execute(request(), Provider(answer()), Budget(0, 0), ledger)
    assert "tool_calls" not in ledger.entries()[-1]["record"]["response"]


# ————— إعادةُ العرض لا تُسقط نداءً —————

def test_replay_restores_the_tool_calls(tmp_path):
    """بلا هذا تُعاد خطوةٌ بلا نداءاتها فتتوقف الحلقةُ صامتةً كأن النموذج اكتفى."""
    call = ToolCall("c1", "read_file", {"path": "a.txt"})
    ledger = Ledger(tmp_path / "l.jsonl")
    provider = Provider(answer(calls=[call]))
    first = execute(request(tools=(READ,), key="k1"), provider, Budget(0, 0), ledger)
    second = execute(request(tools=(READ,), key="k1"), provider, Budget(0, 0), ledger)
    assert second.replayed is True
    assert provider.queue == [], "لم يُنادَ المزوّد مرّتين"
    assert second.response.tool_calls == first.response.tool_calls == (call,)
