"""Tests for Philosophy and Logic Node (nodes/philosophy/node.py)."""
import pytest
from pathlib import Path
from core.budget import Budget
from core.contracts import Response, Usage
from core.knowledge import KnowledgeItem
from core.ledger import Ledger
from core.canonical import PayloadRejected
from nodes.philosophy.node import (
    PhilosophyNode, evaluate_conditional_syllogism, MANIFEST,
    MODUS_PONENS, MODUS_TOLLENS, AFFIRMING_CONSEQUENT, DENYING_ANTECEDENT
)

ROOT = Path(__file__).resolve().parent.parent



class MockLogicProvider:
    model = "mock-logic-provider"
    is_local = True

    def __init__(self, response_text="تحليل منطقي برهاني سليم"):
        self.response_text = response_text
        self.calls = 0

    def estimate_micros(self, request):
        return 0

    def complete(self, request):
        self.calls += 1
        return Response(
            self.response_text,
            Usage(20, 10),
            "complete",
            0,
            provider="mock-logic",
            model_version="v1"
        )


def test_modus_ponens_deduction():
    major = "إذا كان الشيء معدناً فإنه يتمدد بالحرارة"
    minor = "هذا الشيء معدن"
    res = evaluate_conditional_syllogism(major, minor)
    assert res["valid"] is True
    assert res["form"] == "modus_ponens"
    assert "يتمدد بالحرارة" in res["conclusion"]
    assert res["rule"] == MODUS_PONENS


def test_modus_tollens_deduction():
    major = "إذا كان الكائن إنساناً فإنه ناطق"
    minor = "ليس ناطقاً"
    res = evaluate_conditional_syllogism(major, minor)
    assert res["valid"] is True
    assert res["form"] == "modus_tollens"
    assert "ليس" in res["conclusion"]
    assert res["rule"] == MODUS_TOLLENS


def test_affirming_the_consequent_fallacy():
    major = "إذا نزل المطر فإن الشارع يبتل"
    minor = "الشارع يبتل"
    res = evaluate_conditional_syllogism(major, minor)
    assert res["valid"] is False
    assert res["form"] == "affirming_the_consequent"
    assert res["rule"] == AFFIRMING_CONSEQUENT


def test_denying_the_antecedent_fallacy():
    major = "إذا كان الطالب مجتهداً فإنه ينجح"
    minor = "ليس الطالب مجتهداً"
    res = evaluate_conditional_syllogism(major, minor)
    assert res["valid"] is False
    assert res["form"] == "denying_the_antecedent"
    assert res["rule"] == DENYING_ANTECEDENT


def test_unknown_major_form():
    major = "الشمس مشرقة والجو جميل"
    minor = "الشمس مشرقة"
    res = evaluate_conditional_syllogism(major, minor)
    assert res["valid"] is False
    assert res["rule"] == "unknown_form"


def test_philosophy_node_manifest():
    assert MANIFEST.name == "philosophy"
    assert "logic" in MANIFEST.domains
    assert MANIFEST.data_policy_ceiling == "regulated"


def _setup_test_register(tmp_path):
    from core.acquisitions import Acquisition, SourceRegister
    reg = SourceRegister(tmp_path / "sources" / "acquisitions.jsonl")
    reg.acquire(Acquisition(
        source_id="src1", title="مصدر تجريبي", origin="test",
        verified_date="2026-09-21", use_internal=True, use_distribution=False,
        license_evidence="ترخيص تجريبي"
    ))
    reg.acquire(Acquisition(
        source_id="nodes.philosophy", title="محرك الفلسفة", origin="nodes/philosophy/node.py",
        verified_date="2026-09-21", use_internal=True, use_distribution=False,
        license_evidence="محلي"
    ))
    return reg


def test_philosophy_node_handle_query_with_witnesses_success(tmp_path):
    reg = _setup_test_register(tmp_path)
    ledger = Ledger(tmp_path / "philo_ledger.jsonl")
    budget = Budget(1000, 1000)
    provider = MockLogicProvider("كل إنسان فان [ش1]")
    node = PhilosophyNode(tmp_path, provider, budget, ledger, register=reg)

    witness = KnowledgeItem(
        text="كل إنسان فان لا محالة", lang="ar", domain="philosophy",
        use_internal=True, use_distribution=False, source_id="src1", locus="p1",
        originality="original", part="كتاب المنطق"
    )

    item = node.handle_query("هل الإنسان فان؟", witnesses=[witness], data_policy="internal")
    assert isinstance(item, KnowledgeItem)
    assert item.domain == "philosophy"
    assert item.originality == "derived"
    assert item.source_id == "src1"
    assert item.part == "كتاب المنطق"
    assert "كل إنسان فان [ش1]" in item.text
    assert provider.calls == 1


def test_philosophy_node_q26_missing_citation_rejection(tmp_path):
    reg = _setup_test_register(tmp_path)
    ledger = Ledger(tmp_path / "philo_ledger.jsonl")
    budget = Budget(1000, 1000)
    # جواب بلا أي رقم شاهد [ش1]
    provider = MockLogicProvider("البرهان: المقدمات تلزم عنها النتيجة لزوماً حتمياً.")
    node = PhilosophyNode(tmp_path, provider, budget, ledger, register=reg)

    witness = KnowledgeItem(
        text="كل إنسان فان", lang="ar", domain="philosophy",
        use_internal=True, use_distribution=False, source_id="src1", locus="p1",
        originality="original", part="كتاب المنطق"
    )

    with pytest.raises(PayloadRejected) as exc:
        node.handle_query("هل سقراط فان؟", witnesses=[witness], data_policy="internal")
    assert exc.value.code == "citation_missing"


def test_philosophy_node_q26_unsupported_number_rejection(tmp_path):
    reg = _setup_test_register(tmp_path)
    ledger = Ledger(tmp_path / "philo_ledger.jsonl")
    budget = Budget(1000, 1000)
    # جواب برقم 99 غير موجود في الشاهد
    provider = MockLogicProvider("هذا البرهان ينطبق على 99 حالة [ش1]")
    node = PhilosophyNode(tmp_path, provider, budget, ledger, register=reg)

    witness = KnowledgeItem(
        text="هذا البرهان ينطبق على جميع الحالات دون استثناء", lang="ar", domain="philosophy",
        use_internal=True, use_distribution=False, source_id="src1", locus="p1",
        originality="original", part="كتاب المنطق"
    )

    with pytest.raises(PayloadRejected) as exc:
        node.handle_query("على كم حالة ينطبق؟", witnesses=[witness], data_policy="internal")
    assert exc.value.code == "citation_number_unsupported"


def test_philosophy_node_q26_low_overlap_rejection(tmp_path):
    reg = _setup_test_register(tmp_path)
    ledger = Ledger(tmp_path / "philo_ledger.jsonl")
    budget = Budget(1000, 1000)
    # جواب بتداخل معجمي منعدم مع الشاهد
    provider = MockLogicProvider("المجرة الكونية تدور حول الثقب الأسود البعيد [ش1]")
    node = PhilosophyNode(tmp_path, provider, budget, ledger, register=reg)

    witness = KnowledgeItem(
        text="كل إنسان ناطق والناطق كائن حي مفكر", lang="ar", domain="philosophy",
        use_internal=True, use_distribution=False, source_id="src1", locus="p1",
        originality="original", part="كتاب المنطق"
    )

    with pytest.raises(PayloadRejected) as exc:
        node.handle_query("ما هو الإنسان؟", witnesses=[witness], data_policy="internal")
    assert exc.value.code == "citation_overlap_low"


def test_philosophy_node_pure_reasoning_exempt_from_q26_and_uses_registered_source(tmp_path):
    reg = _setup_test_register(tmp_path)
    ledger = Ledger(tmp_path / "philo_ledger.jsonl")
    budget = Budget(1000, 1000)
    # استدلال صوري حتمي بلا شواهد خارجية: لا إحالات [شN] ويجتاز
    provider = MockLogicProvider("النتيجة: المقدمات تلزم عنها النتيجة بالضرورة.")
    node = PhilosophyNode(tmp_path, provider, budget, ledger, register=reg)

    item = node.handle_query("ما شروط قياس وضع المقدم؟", witnesses=None, data_policy="internal")
    assert isinstance(item, KnowledgeItem)
    assert item.source_id == "nodes.philosophy"
    assert item.part == "syllogism_analysis"
    assert item.use_internal is True
    assert item.use_distribution is False


def test_philosophy_node_unacquired_source_rejected(tmp_path):
    reg = _setup_test_register(tmp_path)
    ledger = Ledger(tmp_path / "philo_ledger.jsonl")
    budget = Budget(1000, 1000)
    provider = MockLogicProvider("استدلال صحيح [ش1]")
    node = PhilosophyNode(tmp_path, provider, budget, ledger, register=reg)

    # شاهد بمصدر غير مسجل في سجل الأصول
    unregistered_witness = KnowledgeItem(
        text="استدلال صحيح تماماً", lang="ar", domain="philosophy",
        use_internal=True, use_distribution=False, source_id="unregistered_source_xyz", locus="p1",
        originality="original", part="كتاب"
    )

    with pytest.raises(PayloadRejected) as exc:
        node.handle_query("هل الاستدلال صحيح؟", witnesses=[unregistered_witness], data_policy="internal")
    assert exc.value.code == "source_unacquired"


def test_philosophy_node_witnesses_no_common_rights_rejected(tmp_path):
    from core.acquisitions import Acquisition
    reg = _setup_test_register(tmp_path)
    reg.acquire(Acquisition(
        source_id="src_dist_only", title="مصدر توزيع فقط", origin="test",
        verified_date="2026-09-21", use_internal=False, use_distribution=True,
        license_evidence="ترخيص"
    ))
    ledger = Ledger(tmp_path / "philo_ledger.jsonl")
    budget = Budget(1000, 1000)
    provider = MockLogicProvider("استدلال صحيح [ش1]")
    node = PhilosophyNode(tmp_path, provider, budget, ledger, register=reg)

    w1 = KnowledgeItem(
        text="كل إنسان فان", lang="ar", domain="philosophy",
        use_internal=True, use_distribution=False, source_id="src1", locus="p1",
        originality="original", part="كتاب"
    )
    w2 = KnowledgeItem(
        text="سقراط إنسان", lang="ar", domain="philosophy",
        use_internal=False, use_distribution=True, source_id="src_dist_only", locus="p2",
        originality="original", part="كتاب"
    )

    with pytest.raises(PayloadRejected) as exc:
        node.handle_query("هل سقراط فان؟", witnesses=[w1, w2], data_policy="internal")
    assert exc.value.code == "no_common_rights"


def test_philosophy_node_policy_ceiling_rejection(tmp_path):
    ledger = Ledger(tmp_path / "philo_ledger.jsonl")
    budget = Budget(1000, 1000)
    provider = MockLogicProvider()
    node = PhilosophyNode(tmp_path, provider, budget, ledger)

    # local_only is higher than regulated ceiling, must be rejected
    with pytest.raises(PayloadRejected) as exc:
        node.handle_query("سؤال فائق السرية", data_policy="local_only")
    assert exc.value.code == "policy_exceeds_ceiling"


def test_philosophy_registered_in_main_registry():
    from core.registry import NodeRegistry
    ROOT = Path(__file__).resolve().parent.parent
    reg = NodeRegistry(ROOT / "registry" / "nodes.jsonl")
    current = reg.current()
    assert "philosophy" in current
    philo_entry = current["philosophy"]
    assert philo_entry["manifest"]["domains"] == ["philosophy", "logic", "reasoning"]
    assert philo_entry["manifest"]["data_policy_ceiling"] == "regulated"
    assert philo_entry["manifest"]["accepts"] == ["query"]


def test_router_delivers_to_philosophy(tmp_path):
    from core.knowledge import KnowledgeEvent, NodeManifest
    from core.registry import NodeRegistry
    from core.router import route_once
    from core.streams import inbox, outbox

    # Create temporary root with nodes registry including philosophy
    reg = NodeRegistry(tmp_path / "registry" / "nodes.jsonl")
    reg.register(NodeManifest(name="gateway", contract_version=1, domains=("orchestration",), accepts=("answer", "refuse"), data_policy_ceiling="regulated"))
    reg.register(MANIFEST)
    reg.anchor()

    # Emit query from gateway to philosophy
    ev = KnowledgeEvent(
        kind="query",
        from_node="gateway",
        to="philosophy",
        correlation_id="corr-philo-1",
        data_policy="regulated",
        payload={"question": "هل يصح قياس وضع المقدم؟", "domain": "logic"},
        idempotency_key="key-philo-1",
        budget_cap_micros=100
    )
    outbox(tmp_path, "gateway").emit(ev)

    # Route once
    res = route_once(tmp_path, "2026-09-21T12:00:00Z")
    assert res["delivered"] == 1
    assert res["refused"] == 0

    # Verify delivered to philosophy inbox
    delivered_evs = [ev for _, ev, _ in inbox(tmp_path, "philosophy").read_from(0)]
    assert len(delivered_evs) == 1
    assert delivered_evs[0].correlation_id == "corr-philo-1"
    assert delivered_evs[0].payload["domain"] == "logic"


def test_continuity_verb_not_treated_as_negation():
    """عثرة م١٥/ف٥: «المطر ما زال نازلاً» كان يُحكم عليه بمغالطة إنكار المقدم لأن «ما» نفي."""
    major = "إذا نزل المطر فإن الشارع يبتل"
    minor = "المطر ما زال نازلاً"
    res = evaluate_conditional_syllogism(major, minor)
    # يجب أن يكون استدلالاً صحيحاً بوضع المقدم (Modus Ponens) لا إنكار المقدم
    assert res["valid"] is True
    assert res["form"] == "modus_ponens"
    assert "الشارع يبتل" in res["conclusion"]


def test_relative_polarity_negative_antecedent_affirmed():
    """عثرة م١٥/ف٥: «زيد غير حاضر» كان يُحكم عليه بإنكار المقدم لمجرد ورود «غير»."""
    major = "إذا كان زيد غير حاضر فإنه يغيب"
    minor = "زيد غير حاضر"
    res = evaluate_conditional_syllogism(major, minor)
    # الصغرى تطابق قطبية المقدم المنفي => وضع المقدم (Modus Ponens) صحيح
    assert res["valid"] is True
    assert res["form"] == "modus_ponens"
    assert "يغيب" in res["conclusion"]


def test_relative_polarity_negative_antecedent_denied():
    major = "إذا كان زيد غير حاضر فإنه يغيب"
    minor = "زيد حاضر"
    res = evaluate_conditional_syllogism(major, minor)
    # الصغرى تخالف قطبية المقدم المنفي => مغالطة إنكار المقدم
    assert res["valid"] is False
    assert res["form"] == "denying_the_antecedent"


def test_conditional_without_fa_particle():
    """عثرة م١٥/ف٥: «إذا اجتهد الطالب نجح» مجردة من رابط الفاء أو فإن."""
    major = "إذا اجتهد الطالب نجح"
    minor = "اجتهد الطالب"
    res = evaluate_conditional_syllogism(major, minor)
    assert res["valid"] is True
    assert res["form"] == "modus_ponens"
    assert "نجح" in res["conclusion"]


def test_categorical_syllogism_barbara():
    """عثرة م١٥/ف٥: الأقيسة الحملية (كل إنسان فان، سقراط إنسان)."""
    major = "كل إنسان فانٍ"
    minor = "سقراط إنسان"
    node = PhilosophyNode(ROOT)
    res = node.evaluate_logic(major, minor)
    assert res["valid"] is True
    assert res["syllogism_type"] == "categorical"
    assert res["mood"] == "Barbara"
    assert "سقراط فان" in res["conclusion"]


def test_categorical_syllogism_celarent():
    major = "لا شيء من الجماد بحساس"
    minor = "هذا الحجر جماد"
    node = PhilosophyNode(ROOT)
    res = node.evaluate_logic(major, minor)
    assert res["valid"] is True
    assert res["syllogism_type"] == "categorical"
    assert res["mood"] == "Celarent"
    assert "هذا الحجر ليس حساس" in res["conclusion"]


