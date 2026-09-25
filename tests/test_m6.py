"""اختبارات م٦: خدمة البحث (تخطيط/جبهات/تأليف/فاتورة/استئناف)،
بوابة هرمس، وخدمة الترجمة — كلها بمزوّد صوري بلا نموذج حي."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core.acquisitions import Acquisition, SourceRegister
from core.budget import Budget
from core.canonical import PayloadRejected
from core.contracts import Response, Usage
from core.corpus import CorpusCatalog, CorpusFile
from core.knowledge import KnowledgeItem
from core.ledger import Ledger, LedgerCorrupt
from core.run import RouteRefused
from providers.base import ProviderError
from services.hermes_gate import SOURCE_ID, review_finding
from services.research import ResearchService
from services.translate import TranslateService

PLAN = '{"maritime": ["ما شهادة الصابورة؟"], "lexicon": ["صبر"]}'


class SequencedProvider:
    """مزوّد حتمي بسلسلة أجوبة — يفشل صاخبًا إن سُئل فوقها."""

    def __init__(self, contents, cut_at=None):
        self.model = "seq"
        self.is_local = True
        self.calls = 0
        self._estimates = 0
        self._contents = list(contents)
        self._cut_at = cut_at

    def estimate_micros(self, request):
        # القطع هنا يحاكي موت العملية قبل الحجز: لا قيد يُكتب أصلًا —
        # أما استثناء داخل complete فيقيَّد «مجهول التصنيف» غير قابل
        # للإعادة عمدًا (نتيجة غير مؤكدة، مذهب م٠)
        self._estimates += 1
        if self._cut_at is not None and self._estimates >= self._cut_at:
            raise RuntimeError("قطعٌ مصطنع للخدمة")
        return 0

    def complete(self, request):
        self.calls += 1
        if not self._contents:
            raise AssertionError("نداء فوق السيناريو المكتوب")
        return Response(self._contents.pop(0), Usage(10, 5), "complete", 0,
                        provider="seq", model_version="1")


class StubLing:
    def lookup(self, word, limit=6):
        item = {"text": f"مدخل {word} في المعجم", "lang": "ar",
                "domain": "arabic-lexicon", "use_internal": True,
                "use_distribution": False, "source_id": "src",
                "locus": "ج1 ص9", "originality": "original",
                "part": "معجم صوري", "glossary_ref": "",
                "schema_version": 1}
        return [{"item": item, "item_digest": "0" * 64}]


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
    CorpusCatalog(d / "_catalog.jsonl").record(
        "001__لائحة", "corpus/maritime/001__لائحة.jsonl", 1, h)
    return tmp_path, cf.pages()[0]["item_digest"], reg


def _service(root, dg, provider, ledger):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "m6_mar", ROOT / "nodes" / "maritime" / "node.py")
    mar_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mar_mod)

    def fake_search(q, limit=10):
        return [{"doc_id": "001__لائحة", "part": "لائحة الصابورة",
                 "locus": "المادة (5)", "item_digest": dg}]
    budget = Budget(1000, 10000)
    # مصنعُ عقدةٍ يستلم سجل التشغيلة — يعمل في وضعي الحقن والاشتقاق
    factory = lambda led: mar_mod.MaritimeNode(root, provider, budget, led,
                                               search_fn=fake_search)
    return ResearchService(root, provider, budget, ledger=ledger,
                           mar_node=factory, ling_node=StubLing())


SCRIPT = [PLAN, "تلتزم السفينة بشهادة إدارة مياه الصابورة [ش1].", "تلتزم السفينة بشهادة إدارة مياه الصابورة [م1]، "
          "ومدخل صبر في المعجم [م2]."]


def test_research_composes_two_nodes_with_full_bill(tmp_path):
    root, dg, _ = _mini_root(tmp_path)
    p = SequencedProvider(SCRIPT)
    led = Ledger(root / "run.jsonl")
    r = _service(root, dg, p, led).run("ما التزامات الصابورة ومعناها؟")
    nodes = {m["node"] for m in r["materials"]}
    assert nodes == {"maritime", "linguistics"}
    assert r["status"] == "complete"
    assert r["coverage"] == {"planned": 2, "completed": 2, "failed": 0}
    assert not r["failed_fronts"]
    assert r["brief_item"].text == r["brief"]
    from acceptance_m6 import coverage_is_consistent
    assert coverage_is_consistent(r)
    assert r["materials"][0]["evidence"][0]["locus"] == "المادة (5)"
    assert "[م1]" in r["brief"] and p.calls == 3
    recs = [e["record"] for e in led.entries()]
    assert recs[0]["kind"] == "service_run"
    assert recs[-1]["kind"] == "service_bill"
    oks = [x for x in recs if x["kind"] == "ok"]
    assert recs[-1]["calls"] == len(oks) == 3
    assert recs[-1]["settled_micros"] == sum(x["settled_micros"]
                                             for x in oks)


def test_research_resume_replays_without_new_calls(tmp_path):
    root, dg, _ = _mini_root(tmp_path)
    led = Ledger(root / "run.jsonl")
    _service(root, dg, SequencedProvider(SCRIPT), led) \
        .run("ما التزامات الصابورة ومعناها؟")
    p2 = SequencedProvider([])          # أي نداء حي سيفشل صاخبًا
    r2 = _service(root, dg, p2, led).run("ما التزامات الصابورة ومعناها؟")
    assert p2.calls == 0 and "[م1]" in r2["brief"]
    assert r2["status"] == "complete"
    assert r2["coverage"] == {"planned": 2, "completed": 2, "failed": 0}


def test_research_interrupt_then_resume_from_last_record(tmp_path):
    root, dg, _ = _mini_root(tmp_path)
    led = Ledger(root / "run.jsonl")
    p1 = SequencedProvider(SCRIPT, cut_at=2)   # المخطط يمر ثم قطع
    with pytest.raises(RuntimeError):
        _service(root, dg, p1, led).run("ما التزامات الصابورة ومعناها؟")
    p2 = SequencedProvider(SCRIPT[1:])         # بلا خطة: تُستهلك عرضًا
    r2 = _service(root, dg, p2, led).run("ما التزامات الصابورة ومعناها؟")
    assert p2.calls == 2                       # جبهة وتأليف فقط — لا مخطط
    assert {m["node"] for m in r2["materials"]} == {"maritime",
                                                    "linguistics"}


def test_research_failed_front_recorded_not_silent(tmp_path):
    root, dg, _ = _mini_root(tmp_path)

    class DeafLing:
        def lookup(self, word, limit=6):
            raise LookupError("لا مدخل")
    led = Ledger(root / "run.jsonl")
    # مادة واحدة فقط ستبقى — فالمؤلف يستشهد بها وحدها وإلا فُلفق
    p = SequencedProvider([PLAN, "تلتزم السفينة بشهادة إدارة مياه الصابورة [ش1].",
                           "تلتزم السفينة بشهادة إدارة مياه الصابورة [م1]."])
    svc = _service(root, dg, p, led)
    svc._ling = DeafLing()
    r = svc.run("ما التزامات الصابورة ومعناها؟")
    assert r["failed_fronts"] and r["failed_fronts"][0]["node"] == "linguistics"
    assert r["status"] == "partial"
    assert r["coverage"] == {"planned": 2, "completed": 1, "failed": 1}
    assert r["brief"].startswith("تنبيه: تغطية الاسترجاع جزئية")
    assert "صبر" in r["brief"] and "لا مدخل" in r["brief"]
    assert r["brief_item"].text == r["brief"]
    assert any(e["record"].get("kind") == "front_failed"
               for e in led.entries())


@pytest.mark.parametrize("empty_result", [False, True])
@pytest.mark.parametrize("interrupt_composer", [False, True])
def test_partial_word_front_with_both_nodes_survives_resume_and_replay(
        tmp_path, empty_result, interrupt_composer):
    """بقاء العقدتين وثلاث مواد لا يخفي تعذر الكلمة الثانية في الخطة."""
    root, dg, _ = _mini_root(tmp_path)
    plan = '{"maritime": ["ما شهادة الصابورة؟"], "lexicon": ["صبر", "صابورة"]}'

    class PartialLing(StubLing):
        def lookup(self, word, limit=6):
            if word == "صابورة":
                if empty_result:
                    return []
                raise LookupError("لا مدخل للكلمة في المعاجم: 'صابورة'")
            # صفحتان للجبهة نفسها؛ التغطية تُحصي الجبهات لا المواد.
            return super().lookup(word) + super().lookup(word)

    def service(provider):
        s = _service(root, dg, provider, led)
        s._ling = PartialLing()
        return s

    led = Ledger(root / "run.jsonl")
    brief = ("تلتزم السفينة بشهادة إدارة مياه الصابورة [م1]، "
             "ومدخل صبر في المعجم [م2].")
    provider = SequencedProvider([plan, "تلتزم السفينة بشهادة إدارة مياه الصابورة [ش1].", brief],
                                 cut_at=3 if interrupt_composer else None)
    if interrupt_composer:
        with pytest.raises(RuntimeError, match="قطع"):
            service(provider).run("شهادة الصابورة ومعنى صبر وصابورة؟")
        provider = SequencedProvider([brief])
    r = service(provider).run("شهادة الصابورة ومعنى صبر وصابورة؟")
    assert provider.calls == (1 if interrupt_composer else 3)
    assert {m["node"] for m in r["materials"]} == {"maritime", "linguistics"}
    assert len(r["materials"]) == 3
    assert r["status"] == "partial"
    assert r["coverage"] == {"planned": 3, "completed": 2, "failed": 1}
    assert len(r["failed_fronts"]) == 1
    assert r["failed_fronts"][0]["question"] == "صابورة"
    assert r["brief"].startswith("تنبيه: تغطية الاسترجاع جزئية")
    assert "«صابورة»" in r["brief"] and "لا مدخل للكلمة" in r["brief"]
    assert r["brief"].endswith(brief)   # التنبيه لا يعتمد على جواب المؤلف
    assert r["brief_item"].text == r["brief"]

    from acceptance_m6 import coverage_is_consistent
    assert coverage_is_consistent(r)
    assert not coverage_is_consistent({**r, "status": "complete"})
    assert not coverage_is_consistent({
        **r, "coverage": {"planned": 3, "completed": 3, "failed": 0}})
    assert not coverage_is_consistent({**r, "failed_fronts": []})

    records_before = led.entries()
    replay_provider = SequencedProvider([])
    replay = service(replay_provider).run("شهادة الصابورة ومعنى صبر وصابورة؟")
    assert replay_provider.calls == 0
    for field in ("status", "coverage", "failed_fronts", "brief", "brief_item"):
        assert replay[field] == r[field]
    assert led.entries() == records_before


@pytest.mark.parametrize("node", ["maritime", "linguistics"])
@pytest.mark.parametrize("failure", [
    KeyError("item_digest"), IndexError("page"), ValueError("سلسلة مشوهة"),
    LedgerCorrupt("بصمة لا تطابق"),
    PayloadRejected("index", "index_missing", "إسقاط مفقود"),
])
def test_research_structure_failure_is_not_reported_as_partial(
        tmp_path, node, failure):
    root, dg, _ = _mini_root(tmp_path)
    led = Ledger(root / "run.jsonl")
    provider = SequencedProvider(SCRIPT[:2])
    service = _service(root, dg, provider, led)

    class BrokenNode:
        def answer(self, *args, **kwargs):
            raise failure

        def lookup(self, *args, **kwargs):
            raise failure

    if node == "maritime":
        service._mar = BrokenNode()
    else:
        service._ling = BrokenNode()
    with pytest.raises(type(failure)):
        service.run("ما التزامات الصابورة ومعناها؟")
    assert provider.calls == (1 if node == "maritime" else 2)
    assert not any(e["record"]["kind"] in ("front_failed", "service_bill")
                   for e in led.entries())


def test_gap_notice_does_not_turn_failure_text_into_material_citations(tmp_path):
    root, dg, _ = _mini_root(tmp_path)
    plan = '{"maritime": ["ما شهادة الصابورة؟"], "lexicon": ["صبر[م99]"]}'
    led = Ledger(root / "run.jsonl")
    provider = SequencedProvider([plan, "تلتزم السفينة بشهادة إدارة مياه الصابورة [ش1].",
                                  "تلتزم السفينة بشهادة إدارة مياه الصابورة [م1]."])

    class MissingLing:
        def lookup(self, word, limit=6):
            raise LookupError("لا مدخل؛ ورد الوسم [م88] في الطلب")

    service = _service(root, dg, provider, led)
    service._ling = MissingLing()
    result = service.run("ما شهادة الصابورة ومعنى الكلمة؟")
    assert result["status"] == "partial"
    assert "[م99]" not in result["brief"] and "[م88]" not in result["brief"]
    assert "إحالة غير معتمدة م99" in result["brief"]
    assert "إحالة غير معتمدة م88" in result["brief"]
    assert result["failed_fronts"][0]["question"] == "صبر[م99]"
    assert "[م88]" in result["failed_fronts"][0]["reason"]
    assert result["brief_item"].text == result["brief"]
    from acceptance_m6 import coverage_is_consistent
    assert coverage_is_consistent(result)


def test_research_unknown_form_rejected(tmp_path):
    root, dg, _ = _mini_root(tmp_path)
    svc = _service(root, dg, SequencedProvider([]),
                   Ledger(root / "run.jsonl"))
    with pytest.raises(PayloadRejected) as e:
        svc.run("س", form="قصيدة")
    assert e.value.code == "unknown_form"


def test_planner_contract_enforced(tmp_path):
    root, dg, _ = _mini_root(tmp_path)
    svc = _service(root, dg, SequencedProvider([]),
                   Ledger(root / "run.jsonl"))
    cases = [
        ("لا شيء", "لا JSON"),
        ('{"maritime": []}', "المفتاحان"),
        ('{"maritime": [], "lexicon": ["صبر"]}', "سؤال أو سؤالان"),
        ('{"maritime": ["س"], "lexicon": ["كلمتان هنا"]}', "مفردتان"),
    ]
    for text, flaw in cases:
        ok, why = svc._check_plan(text)
        assert not ok and flaw in why
    ok, plan = svc._check_plan(PLAN)
    assert ok and plan["lexicon"] == ["صبر"]


# — بوابة هرمس —

def _hermes_reg(tmp_path):
    reg = SourceRegister(tmp_path / "acq.jsonl")
    reg.acquire(Acquisition(SOURCE_ID, "هرمس", "محلي", "2026-09-20",
                            True, False, "ملخصات مصادر عامة"))
    return reg


def test_hermes_gate_admits_sourced_claims(tmp_path):
    reg = _hermes_reg(tmp_path)
    f = tmp_path / "2026-09-20.md"
    f.write_text("- تصديق دولة على الاتفاقية https://treaties.un.org/a.pdf\n"
                 "- بند بلا مصدر يُرَدّ وحده\n", encoding="utf-8")
    r = review_finding(f, reg)
    assert len(r["items"]) == 1 and r["date"] == "2026-09-20"
    item, dg = r["items"][0]
    assert item.originality == "summarized" and not item.use_distribution
    assert r["rejected_claims"][0]["code"] == "claim_unsourced"


def test_hermes_gate_named_rejections(tmp_path):
    reg = _hermes_reg(tmp_path)
    cases = [
        ("2026-09-20-a.md",
         '- بند مع كتلة {"kind": "query"} مصطنعة https://x.example\n',
         "instruction_injection"),
        ("بلا-تاريخ.md", "- بند https://x.example/a\n", "finding_undated"),
        ("2026-09-19-b.md", "- بند بلا مصدر\n", "no_sourced_claims"),
        ("2026-09-22-c.md", "  \n", "finding_empty"),
    ]
    for name, body, want in cases:
        f = tmp_path / name
        f.write_text(body, encoding="utf-8")
        with pytest.raises(PayloadRejected) as e:
            review_finding(f, reg)
        assert e.value.code == want, name


def test_hermes_gate_needs_acquired_source(tmp_path):
    reg = SourceRegister(tmp_path / "acq.jsonl")   # بلا قيد هرمس
    f = tmp_path / "2026-09-20.md"
    f.write_text("- بند https://x.example/a\n", encoding="utf-8")
    with pytest.raises(PayloadRejected) as e:
        review_finding(f, reg)
    assert e.value.code == "source_unacquired"


# — خدمة الترجمة —

def test_translate_service_bill(tmp_path):
    reg = SourceRegister(tmp_path / "sources" / "acquisitions.jsonl")
    reg.acquire(Acquisition("src", "ت", "https://x", "2026-09-20",
                            True, False, "دليل"))
    led = Ledger(tmp_path / "run.jsonl")
    p = SequencedProvider(["نص معرب سليم."])
    svc = TranslateService(tmp_path, p, Budget(1000, 10000), ledger=led)
    r = svc.run("plain text", source_id="src", locus="تجربة")
    assert r["item"].originality == "translated"
    assert r["bill"]["calls"] == 1 and p.calls == 1
    assert [e["record"] for e in led.entries()][-1]["kind"] == "service_bill"


# ── تدقيق م٦: لا استهلاك عرضٍ فوق سجل مزوَّر ──

def test_forged_run_ledger_rejected_before_replay(tmp_path):
    root, dg, _ = _mini_root(tmp_path)
    led = Ledger(root / "run.jsonl")
    _service(root, dg, SequencedProvider(SCRIPT), led) \
        .run("ما التزامات الصابورة ومعناها؟")
    lines = (root / "run.jsonl").read_text().splitlines()
    forged = lines[2].replace("تلتزم السفينة", "خلاصة مزوَّرة")
    assert forged != lines[2]
    lines[2] = forged
    (root / "run.jsonl").write_text("\n".join(lines) + "\n")
    from core.ledger import LedgerCorrupt
    with pytest.raises(LedgerCorrupt):
        _service(root, dg, SequencedProvider([]), Ledger(root / "run.jsonl")) \
            .run("ما التزامات الصابورة ومعناها؟")


# ── تدقيق م٦: الفاتورة تشغيلية بمفتاحها لا تراكمية ──

def test_bill_scoped_per_run_key(tmp_path):
    root, dg, _ = _mini_root(tmp_path)

    def svc(provider):
        s = _service(root, dg, provider, None)
        s.runs_dir = root / "runs"     # الوضع المشتق: سجل لكل تشغيلة
        return s
    r1 = svc(SequencedProvider(SCRIPT)) \
        .run("ما التزامات الصابورة ومعناها؟")
    script_b = ['{"maritime": ["ما عقوبة الإبحار بلا شهادة؟"], '
                '"lexicon": ["بحر"]}',
                "تلتزم السفينة بشهادة سارية المفعول [ش1].", "تلتزم السفينة بشهادة سارية [م1]، ومدخل بحر في المعجم [م2]."]
    r2 = svc(SequencedProvider(script_b)) \
        .run("ما عقوبة الإبحار بلا شهادة ومعنى البحر؟")
    assert r1["bill"]["calls"] == 3 and r2["bill"]["calls"] == 3
    assert r1["bill"]["run_key"] != r2["bill"]["run_key"]
    ledgers = sorted((root / "runs").glob("research-*.jsonl"))
    assert len(ledgers) == 2       # سجلٌّ لكل تشغيلة — الحصر بنيوي
    for lp in ledgers:
        recs = [e["record"] for e in Ledger(lp).entries()]
        assert sum(1 for r in recs if r["kind"] == "service_run") == 1
    # واستئنافُ عرضٍ محض لا يكرر فاتورة مطابقة
    svc(SequencedProvider([])).run("ما التزامات الصابورة ومعناها؟")
    key1 = r1["bill"]["run_key"]
    recs = [e["record"] for e in Ledger(
        root / "runs" / f"research-{key1}.jsonl").entries()]
    assert sum(1 for r in recs if r["kind"] == "service_bill") == 1


# ── تدقيق م٦: الاستئناف يستهلك نجاح -aN ولا يدفع ثانية ──

def test_resume_consumes_success_recorded_under_attempt_key(tmp_path):
    root, dg, _ = _mini_root(tmp_path)
    led = Ledger(root / "run.jsonl")

    class FlakyThenCut(SequencedProvider):
        """عطل عابر في نداء المخطط الأول ثم نجاح بمفتاح -a2 ثم قطع."""
        def __init__(self):
            super().__init__([PLAN])
            self._first = True
        def complete(self, request):
            if self._first:
                self._first = False
                from providers.base import ProviderError
                raise ProviderError("unreachable", "انقطاع", retryable=True)
            return super().complete(request)
        def estimate_micros(self, request):
            self._estimates += 1
            if self._estimates >= 3:   # مخطط (خطأ) + مخطط -a2 (نجاح) + قطع
                raise RuntimeError("قطع مصطنع")
            return 0

    with pytest.raises(RuntimeError):
        _service(root, dg, FlakyThenCut(), led) \
            .run("ما التزامات الصابورة ومعناها؟")
    p2 = SequencedProvider(SCRIPT[1:])   # لا خطة: نجاح -a2 يُستهلك عرضًا
    r = _service(root, dg, p2, led).run("ما التزامات الصابورة ومعناها؟")
    assert p2.calls == 2 and r["plan"]["lexicon"] == ["صبر"]


# ── تدقيق م٦: الاستشهاد الملفق يُرَدّ والخلاصة مادة بحقوق التقاطع ──

def test_fabricated_citation_remediated_and_brief_item_rights(tmp_path):
    root, dg, _ = _mini_root(tmp_path)
    led = Ledger(root / "run.jsonl")
    p = SequencedProvider([PLAN, "تلتزم السفينة بشهادة إدارة مياه الصابورة [ش1].",
                           "تلتزم السفينة بشهادة [م1] [م9].",   # يُرَدّ ملفقًا
                           "تلتزم السفينة بشهادة إدارة مياه الصابورة [م1]، "
                           "ومدخل صبر في المعجم [م2]."])
    r = _service(root, dg, p, led).run("ما التزامات الصابورة ومعناها؟")
    assert p.calls == 4
    bi = r["brief_item"]
    assert bi.originality == "derived" and bi.use_internal \
        and not bi.use_distribution
    records = led.entries()
    replay_provider = SequencedProvider([])
    replay = _service(root, dg, replay_provider, led) \
        .run("ما التزامات الصابورة ومعناها؟")
    assert replay_provider.calls == 0
    assert replay["brief"] == r["brief"] and replay["bill"] == r["bill"]
    assert led.entries() == records


class MeteredSequence(SequencedProvider):
    """نصوص/أعطال صورية بكلفة غير صفرية لكشف إعادة دفع التصويب."""

    def estimate_micros(self, request):
        super().estimate_micros(request)
        return 7

    def complete(self, request):
        if self._contents and isinstance(self._contents[0], ProviderError):
            self.calls += 1
            raise self._contents.pop(0)
        response = super().complete(request)
        return Response(response.content, response.usage, "complete", 7,
                        provider="seq", model_version="1")


@pytest.mark.parametrize("phase", ["planner", "composer"])
@pytest.mark.parametrize("transient_at", [None, 0, 1])
def test_corrected_research_replay_preserves_calls_ledger_and_budget(
        tmp_path, phase, transient_at):
    root, dg, _ = _mini_root(tmp_path)
    led = Ledger(root / "run.jsonl")
    attempts = ["لا خطة" if phase == "planner" else "خلاصة [م1] [م9].",
                PLAN if phase == "planner" else SCRIPT[-1]]
    if transient_at is not None:
        attempts.insert(transient_at,
                        ProviderError("unreachable", "انقطاع", retryable=True))
    script = attempts + SCRIPT[1:] if phase == "planner" else SCRIPT[:2] + attempts
    provider = MeteredSequence(script)
    service = _service(root, dg, provider, led)
    result = service.run("ما التزامات الصابورة ومعناها؟")
    calls = provider.calls
    assert calls == (4 if transient_at is None else 5)
    assert result["bill"]["settled_micros"] == 7 * calls
    budget_before = (service.budget.day_remaining_micros,
                     service.budget.month_remaining_micros,
                     service.budget.reservations)
    records_before = led.path.read_bytes()
    anchor_before = led.anchor_path.read_bytes()
    replay = service.run("ما التزامات الصابورة ومعناها؟")
    assert provider.calls == calls
    assert replay == result
    assert led.path.read_bytes() == records_before
    assert led.anchor_path.read_bytes() == anchor_before
    assert (service.budget.day_remaining_micros,
            service.budget.month_remaining_micros,
            service.budget.reservations) == budget_before
    # عملية جديدة كذلك: لا اعتماد على messages أو حالة محفوظة في الذاكرة.
    fresh_provider = MeteredSequence([])
    fresh_service = _service(root, dg, fresh_provider, led)
    assert fresh_service.run("ما التزامات الصابورة ومعناها؟") == result
    assert fresh_provider.calls == 0 and fresh_provider._estimates == 0
    assert fresh_service.budget.day_remaining_micros == 1000
    assert fresh_service.budget.month_remaining_micros == 10000
    assert not fresh_service.budget.reservations
    assert led.path.read_bytes() == records_before


@pytest.mark.parametrize("change", ["policy", "output", "older_conflict"])
def test_retryable_skip_preserves_idempotency_digest_checks(tmp_path, change):
    root, dg, _ = _mini_root(tmp_path)
    led = Ledger(root / "run.jsonl")
    provider = MeteredSequence([
        ProviderError("unreachable", "انقطاع", retryable=True),
        "خلاصة [م1] [م9].", "خلاصة [م1] [م2]."])
    service = _service(root, dg, provider, led)
    check = service._check_brief(2)
    service._governed("composer", "مؤلف", "مواد", "regulated", check)
    if change == "older_conflict":
        transient = next(e["record"] for e in led.entries()
                         if e["record"]["kind"] == "error")
        # سلسلة سليمة تحمل تعارض طلب قديم للمفتاح نفسه: لا يُتخطى.
        led.append({**transient, "request_digest": "0" * 64})
        led.append(transient)   # حتى لو عاد آخر قيد إلى البصمة الصحيحة
    calls_before = provider.calls
    records_before = led.entries()
    balance_before = service.budget.day_remaining_micros
    with pytest.raises(RouteRefused) as exc:
        service._governed("composer", "مؤلف", "مواد",
                          "internal" if change == "policy" else "regulated",
                          check, max_output=901 if change == "output" else 900)
    assert exc.value.code == "idempotency_key_conflict"
    assert provider.calls == calls_before and led.entries() == records_before
    assert service.budget.day_remaining_micros == balance_before


def test_retryable_without_later_success_still_executes_on_resume(tmp_path):
    root, dg, _ = _mini_root(tmp_path)
    led = Ledger(root / "run.jsonl")
    provider = MeteredSequence([
        ProviderError("unreachable", "انقطاع", retryable=True)], cut_at=2)
    service = _service(root, dg, provider, led)
    with pytest.raises(RuntimeError, match="قطع"):
        service._governed("composer", "مؤلف", "مواد", "regulated",
                          service._check_brief(2))
    fresh_provider = MeteredSequence(["خلاصة [م1] [م2]."])
    resumed = _service(root, dg, fresh_provider, led)
    text, outcome = resumed._governed("composer", "مؤلف", "مواد", "regulated",
                                      resumed._check_brief(2))
    assert text == "خلاصة [م1] [م2]." and not outcome.replayed
    assert fresh_provider.calls == 1 and fresh_provider._estimates == 1


# ── تدقيق م٦: سقف سياسة العقدة يُفرض على المستدعي المباشر ──

def test_direct_caller_cannot_exceed_node_ceiling(tmp_path):
    root, dg, _ = _mini_root(tmp_path)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "m6_mar2", ROOT / "nodes" / "maritime" / "node.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    node = mod.MaritimeNode(root, SequencedProvider([]), Budget(1000, 1000),
                            Ledger(root / "l.jsonl"),
                            search_fn=lambda q, limit=10: [])
    with pytest.raises(PayloadRejected) as e:
        node.answer("سؤال", data_policy="local_only")
    assert e.value.code == "policy_exceeds_ceiling"


# ── تدقيق م٦: تقوية بوابة هرمس ──

def test_hermes_gate_hardening(tmp_path):
    reg = _hermes_reg(tmp_path)
    # ترميز غير UTF-8 → رفض مسمى لا انهيار
    bad = tmp_path / "2026-09-20-enc.md"
    bad.write_bytes(b"- \xff\xfe bytes https://x.example/a\n")
    with pytest.raises(PayloadRejected) as e:
        review_finding(bad, reg)
    assert e.value.code == "finding_not_utf8"
    # تاريخ غير تقويمي أو مستقبلي → بلا تاريخ
    for name in ("2026-13-45.md", "2099-01-01.md"):
        f = tmp_path / name
        f.write_text("- بند https://x.example/a\n", encoding="utf-8")
        with pytest.raises(PayloadRejected) as e2:
            review_finding(f, reg)
        assert e2.value.code == "finding_undated", name
    # التاريخ لا يُلتقط من داخل رابط يتحكم به الخارج
    f = tmp_path / "من-الرابط.md"
    f.write_text("- بند https://x.example/2026-01-01/a.pdf\n",
                 encoding="utf-8")
    with pytest.raises(PayloadRejected) as e3:
        review_finding(f, reg)
    assert e3.value.code == "finding_undated"
    # محارف تحكم/اتجاه في البند → يُرَدّ بندًا برمزه
    f = tmp_path / "2026-09-19.md"
    f.write_text("- بند فيه ‮ قلب https://x.example/a\n"
                 "- بند سليم https://x.example/b\n", encoding="utf-8")
    r = review_finding(f, reg)
    assert r["rejected_claims"][0]["code"] == "claim_control_chars"
    assert len(r["items"]) == 1 and "finding_digest" in r
    # بند مفرط الطول → يُرَدّ بندًا
    f = tmp_path / "2026-09-18.md"
    f.write_text("- " + "ط" * 3000 + " https://x.example/a\n"
                 "- سليم https://x.example/b\n", encoding="utf-8")
    r2 = review_finding(f, reg)
    assert r2["rejected_claims"][0]["code"] == "claim_too_long"
    # ملف فوق سقف الحجم → رفض مسمى
    f = tmp_path / "2026-09-17.md"
    f.write_text("- سليم https://x.example/a\n" * 50000, encoding="utf-8")
    with pytest.raises(PayloadRejected) as e4:
        review_finding(f, reg)
    assert e4.value.code == "finding_too_large"
    # http غير المؤمَّن ومضيف بلا نقطة لا يوثقان
    f = tmp_path / "2026-09-16.md"
    f.write_text("- بند http://x.example/a\n- بند https://localhost/a\n",
                 encoding="utf-8")
    with pytest.raises(PayloadRejected) as e5:
        review_finding(f, reg)
    assert e5.value.code == "no_sourced_claims"
