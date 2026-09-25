"""اختبارات م٤: مزوّد Ollama (بمحاكاة الشبكة) وعقدة الحوكمة البحرية."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nodes" / "maritime"))

from core.acquisitions import Acquisition, SourceRegister
from core.budget import Budget
from core.corpus import CorpusCatalog, CorpusFile
from core.knowledge import KnowledgeItem
from core.ledger import Ledger
from node import MANIFEST, MaritimeNode
from providers.base import ProviderError
from providers.ollama import OllamaProvider


# — المزوّد: تحويل جواب Ollama إلى عقد Response —

def test_ollama_response_mapping():
    p = OllamaProvider("m")
    r = p._to_response({"message": {"role": "assistant", "content": "جواب"},
                        "done": True, "done_reason": "stop",
                        "prompt_eval_count": 12, "eval_count": 5,
                        "model": "m:latest"})
    assert (r.content, r.stop_reason, r.cost_micros) == ("جواب", "complete", 0)
    assert (r.usage.input_tokens, r.usage.output_tokens) == (12, 5)
    assert p.is_local is True and p.estimate_micros(None) == 0


def test_ollama_length_and_unknown_done_reason():
    p = OllamaProvider("m")
    payload = {"message": {"role": "assistant", "content": ""}, "done": True,
               "model": "m", "prompt_eval_count": 0, "eval_count": 0,
               "done_reason": "length"}
    assert p._to_response(payload).stop_reason == "max_output"
    with pytest.raises(ProviderError, match="malformed"):
        p._to_response({**payload, "done_reason": "غريب"})


def test_ollama_malformed_raises_provider_error():
    with pytest.raises(ProviderError) as e:
        OllamaProvider("m")._to_response({"no_message": 1})
    assert e.value.code == "malformed"


# — العقدة فوق مخزن مصغّر ومزوّد صوري —

class ScriptedProvider:
    """مزوّد حتمي بجواب مكتوب سلفًا — لعزل منطق العقدة عن النموذج."""
    def __init__(self, content):
        self.model = "scripted"
        self.name = "scripted"
        self.is_local = True
        self._content = content
    def estimate_micros(self, request):
        return 0
    def complete(self, request):
        from core.contracts import Response, Usage
        return Response(self._content, Usage(10, 5), "complete", 0,
                        provider=self.name, model_version="1")


def _mini_root(tmp_path):
    reg = SourceRegister(tmp_path / "sources" / "acquisitions.jsonl")
    reg.acquire(Acquisition("src", "ت", "https://x", "2026-09-20",
                            True, False, "دليل"))
    d = tmp_path / "corpus" / "maritime"
    d.mkdir(parents=True)
    cf = CorpusFile(d / "001__لائحة.jsonl")
    item = KnowledgeItem(
        text="تلتزم السفينة بشهادة إدارة مياه الصابورة سارية المفعول.",
        lang="ar", domain="maritime-regulation", use_internal=True,
        use_distribution=False, source_id="src", locus="المادة (5)",
        originality="original", part="لائحة الصابورة")
    h = cf.ingest("001__لائحة", [item], reg)
    cf.anchor()
    cat = CorpusCatalog(d / "_catalog.jsonl")
    cat.record("001__لائحة", "corpus/maritime/001__لائحة.jsonl", 1, h)
    digest_ = cf.pages()[0]["item_digest"]
    return tmp_path, digest_


def _node(tmp_path, content, digest_):
    def fake_search(q, limit=10):
        return [{"doc_id": "001__لائحة", "part": "لائحة الصابورة",
                 "locus": "المادة (5)", "item_digest": digest_}]
    return MaritimeNode(tmp_path, ScriptedProvider(content),
                        Budget(1000, 10000),
                        Ledger(tmp_path / "var-ledger.jsonl"),
                        search_fn=fake_search)


def test_node_answer_is_cited_item_with_evidence(tmp_path):
    root, dg = _mini_root(tmp_path)
    node = _node(root, "تلتزم السفينة بشهادة سارية [ش1].", dg)
    r = node.answer("ما شهادة الصابورة؟")
    assert r["answer"].originality == "derived"
    assert r["answer"].use_distribution is False
    assert [(p["item_digest"], p["ref"]) for p in r["evidence"]] == [(dg, 1)]
    # النداء مقيد في السجل بحجز مسوّى وبسياسة سقف العقدة (regulated)
    rec = node.ledger.entries()[-1]["record"]
    assert rec["kind"] == "ok" and rec["settled_micros"] == 0
    assert rec["data_policy"] == "regulated"


def test_node_policy_blocks_nonlocal_provider(tmp_path):
    # سقف العقدة regulated: مزود غير محلي يُحجب بنيويًّا ويقيَّد الرفض
    from core.run import RouteRefused
    root, dg = _mini_root(tmp_path)
    node = _node(root, "جواب [ش1].", dg)
    node.provider.is_local = False
    with pytest.raises(RouteRefused) as e:
        node.answer("ما شهادة الصابورة؟")
    assert e.value.code == "policy_requires_local"


def test_transient_error_bumps_attempt_key(tmp_path):
    # عطل قابل للإعادة لا يسمّم السؤال: المحاولة التالية بمفتاح جديد
    root, dg = _mini_root(tmp_path)
    node = _node(root, "تلتزم السفينة بشهادة الصابورة [ش1].", dg)
    calls = {"n": 0}
    good_complete = node.provider.complete
    def flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError("unreachable", "قطع مؤقت", retryable=True)
        return good_complete(request)
    node.provider.complete = flaky
    r = node.answer("ما شهادة الصابورة؟")
    assert r["answer"].text.startswith("تلتزم السفينة")
    # وعلى سجل قائم: قيدُ العطل العابر في المحاولة الأولى ليس فعلًا
    # يُعاد عرضه (تدقيق م٥ — كان يسمم المفتاح عبر التشغيلات): يُنادى
    # حيًّا مرة واحدة فيشفى المفتاح
    calls["n"] = 99
    r2 = node.answer("ما شهادة الصابورة؟")
    assert calls["n"] == 100 and not r2["outcome"].replayed
    # ثم النجاحُ المقيد يُستهلك replay بلا نداء ثالث
    r3 = node.answer("ما شهادة الصابورة؟")
    assert calls["n"] == 100 and r3["outcome"].replayed


def test_nonretryable_error_respected(tmp_path):
    root, dg = _mini_root(tmp_path)
    node = _node(root, "لن يصل.", dg)
    def dead(request):
        raise ProviderError("timeout", "غير مؤكد", retryable=False)
    node.provider.complete = dead
    with pytest.raises(RuntimeError):
        node.answer("ما شهادة الصابورة؟")
    # المحاولة الثانية replay للخطأ غير القابل — بلا نداء جديد
    node.provider.complete = lambda r: (_ for _ in ()).throw(AssertionError)
    with pytest.raises(RuntimeError):
        node.answer("ما شهادة الصابورة؟")


def test_insufficient_evidence_sentinel_not_delivered(tmp_path):
    root, dg = _mini_root(tmp_path)
    node = _node(root, "الشواهد غير كافية", dg)
    with pytest.raises(LookupError):
        node.answer("ما شهادة الصابورة؟")


def test_rights_intersection_on_derived(tmp_path):
    # شاهد توزيع-فقط ∩ شاهد داخلي-فقط = لا حق مشترك → لا مادة
    reg = SourceRegister(tmp_path / "sources" / "acquisitions.jsonl")
    reg.acquire(Acquisition("s2", "ت", "https://x", "2026-09-20",
                            True, True, "دليل"))
    d = tmp_path / "corpus" / "maritime"
    d.mkdir(parents=True)
    cf = CorpusFile(d / "001__لائحة.jsonl")
    items = [
        KnowledgeItem(text="نص أول للتوزيع فقط.", lang="ar",
                      domain="maritime-regulation", use_internal=False,
                      use_distribution=True, source_id="s2",
                      locus="المادة (1)", originality="original", part="ل"),
        KnowledgeItem(text="نص ثانٍ داخلي فقط.", lang="ar",
                      domain="maritime-regulation", use_internal=True,
                      use_distribution=False, source_id="s2",
                      locus="المادة (2)", originality="original", part="ل"),
    ]
    h = cf.ingest("001__لائحة", items, reg)
    cf.anchor()
    from core.corpus import CorpusCatalog
    CorpusCatalog(d / "_catalog.jsonl").record(
        "001__لائحة", "corpus/maritime/001__لائحة.jsonl", 2, h)
    dgs = [p["item_digest"] for p in cf.pages()]
    def fake_search(q, limit=10, match_any=False):
        return [{"doc_id": "001__لائحة", "part": "ل", "locus": l,
                 "item_digest": g} for l, g in
                zip(("المادة (1)", "المادة (2)"), dgs)]
    node = MaritimeNode(tmp_path,
                        ScriptedProvider("نص أول للتوزيع [ش1] ونص ثانٍ داخلي [ش2]."),
                        Budget(1000, 10000),
                        Ledger(tmp_path / "var-ledger.jsonl"),
                        search_fn=fake_search)
    with pytest.raises(ValueError) as e:
        node.answer("ما النصان؟")
    assert "حقِّ استخدامٍ مشترك" in str(e.value)


def test_keywords_do_not_maim_words():
    from node import _keywords
    assert "والي" in _keywords("من هو والي الميناء")
    assert "بالغة" in _keywords("سفينة بالغة الطول")
    assert _keywords("بالسلطة البحرية") == ["السلطة", "البحرية"]
    assert _keywords("ما هو؟") == []


def test_node_rejects_uncited_answer(tmp_path):
    root, dg = _mini_root(tmp_path)
    node = _node(root, "جواب مرسل بلا أي استشهاد.", dg)
    with pytest.raises(ValueError):
        node.answer("ما شهادة الصابورة؟")


def test_node_rejects_out_of_range_citation(tmp_path):
    root, dg = _mini_root(tmp_path)
    node = _node(root, "جواب [ش9] برقم خارج الشواهد.", dg)
    with pytest.raises(ValueError):
        node.answer("ما شهادة الصابورة؟")


@pytest.mark.parametrize("bad_ref", ["[ش14]", "[ش١٥]", "[ش0]"])
def test_node_rejects_mixed_valid_and_invalid_citations(tmp_path, bad_ref):
    from core.canonical import PayloadRejected
    root, dg = _mini_root(tmp_path)
    node = _node(root, f"تلتزم السفينة بشهادة [ش1] ومعلومة بإحالة "
                       f"غير موجودة {bad_ref}.", dg)
    with pytest.raises(PayloadRejected) as exc:
        node.answer("ما شهادة الصابورة؟")
    assert exc.value.code == "citation_out_of_range"
    assert len(node.ledger.entries()) == 3
    assert node.budget.outstanding_micros == 0


def test_node_corrects_mixed_citations_and_replays_corrected_answer(tmp_path):
    root, dg = _mini_root(tmp_path)
    node = _node(root, "تلتزم السفينة بشهادة إدارة مياه الصابورة [ش١].", dg)
    calls = []
    complete = node.provider.complete

    def first_bad_then_good(request):
        from core.contracts import Response, Usage
        calls.append(request)
        if len(calls) == 1:
            return Response("تلتزم السفينة بشهادة [ش1] وخاطئ [ش14] [ش15].",
                            Usage(10, 5),
                            "complete", 0, provider="scripted", model_version="1")
        return complete(request)

    node.provider.complete = first_bad_then_good
    result = node.answer("ما شهادة الصابورة؟")
    assert len(calls) == 2
    assert "14" in calls[1].messages[-1].content
    assert "15" in calls[1].messages[-1].content
    assert result["answer"].text == "تلتزم السفينة بشهادة إدارة مياه الصابورة [ش١]."
    assert [p["ref"] for p in result["evidence"]] == [1]
    repeated = node.answer("ما شهادة الصابورة؟")
    assert repeated["outcome"].replayed and len(calls) == 2
    assert repeated["answer"] == result["answer"]


@pytest.mark.parametrize("bad_ref", ["[ش-1]", "[ش1.5]", "[ش؟]", "[ش15"])
def test_node_rejects_malformed_citation_next_to_valid_one(tmp_path, bad_ref):
    from core.canonical import PayloadRejected
    root, dg = _mini_root(tmp_path)
    node = _node(root, f"تلتزم السفينة بشهادة [ش1] وادعاء {bad_ref}", dg)
    with pytest.raises(PayloadRejected) as exc:
        node.answer("ما شهادة الصابورة؟")
    assert exc.value.code == "citation_malformed"
    assert len(node.ledger.entries()) == 3
    assert node.budget.outstanding_micros == 0


@pytest.mark.parametrize("ref", ["[ش۱]", "[ش１]"])
def test_node_supports_unicode_decimal_citation_digits(tmp_path, ref):
    root, dg = _mini_root(tmp_path)
    result = _node(root, f"تلتزم السفينة بشهادة إدارة مياه الصابورة {ref}",
                   dg).answer("ما شهادة الصابورة؟")
    assert [p["ref"] for p in result["evidence"]] == [1]


def test_node_no_evidence_raises(tmp_path):
    root, dg = _mini_root(tmp_path)
    node = _node(root, "لن يصل.", dg)
    node.search = lambda q, limit=10, match_any=False: []
    with pytest.raises(LookupError):
        node.answer("سؤال خارج المخزن كليًّا")


def test_manifest_is_valid_and_registrable(tmp_path):
    from core.registry import NodeRegistry
    reg = NodeRegistry(tmp_path / "nodes.jsonl")
    d = reg.register(MANIFEST)
    assert reg.get("maritime")["manifest_digest"] == d
    assert reg.verify(strict=True)
