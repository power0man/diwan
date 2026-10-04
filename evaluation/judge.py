"""المحكِّمُ ومعايرتُه، وحسابُ تقرير المحجوب: بروتوكولٌ مبصومٌ قبل أيّ تشغيل (#288، ك٤٥ #30).

`evaluation/protocols/judge_v1.json` يُسجَّل قبل القياس، فلا تتبع العتبةُ النتيجة. وهذه الوحدةُ لا تنادي نموذجًا
ولا تقرأ محجوبًا؛ هي القواعدُ التي يطبّقها `tools/evaluate_sealed.py` على الماك ومُشغِّلُ المعايرة في #30:

- البروتوكولُ يُقرأ ببصمته المثبَّتة، وتغيُّرُها رفضٌ مسمًّى (`judge_protocol_changed`).
- المحكِّمُ من العائلات المسموحة وحدها؛ والمستبعَدةُ والمجهولةُ تُردّ باسمها.
- المعايرةُ على الشطر المفتوح وحده، والتسميةُ الغائبة (حكمُ المالك المنتظَر) رفضٌ لا تخمين.
- κ كوهين والدقّة على الاتحاد، ولكل مصدرٍ على حدة.
- المحكِّمُ على المحجوب يُقبل بدليل معايرةٍ ناجحٍ لنموذجه نفسِه على البروتوكول نفسِه، وتُعاد الأرقامُ لا يُصدَّق العَلَم.
- المحاولاتُ المعدودة تُوزَّع على الطبقات بنسبة حجمها، وتُختار ببذرةٍ هي بصمةُ البروتوكول.
- تقريرُ المحجوب نسبةٌ وWilson 95٪ لكل طبقة، ويُفحص قبل الكتابة فلا يحمل معرّفًا ولا نصًّا.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Iterable
from pathlib import Path

from evaluation.capabilities import CapabilityError, load_suite
from evaluation.multi_system_review import model_family
from evaluation.retrieval_general import wilson

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "evaluation" / "protocols" / "judge_v1.json"
K11_EVIDENCE = ROOT / "docs" / "probe" / "k11-owner-queue-triage-20260925.json"
OPEN_BANK = ROOT / "evaluation" / "banks" / "kimi_v1" / "open"
PROTOCOL_SHA256 = "c2425d24938971aadac78f8a33e6be686c88dcda03824bd9cd6056c936441feb"
VERDICTS = ("correct", "incorrect")
OUTCOMES = ("pass", "fail", "without_checks", "error")
# نصٌّ أقصرُ من هذا لا يُبحث عنه في التقرير: كلمةٌ قصيرة كـ«نعم» تقع في أيّ تقرير ولا تدلّ على حالة.
LEAK_MIN_CHARS = 8
_PINNED = re.compile(r"[^\s:]+:[^\s:]+")


class JudgeRefused(ValueError):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def load_protocol(path: Path = PROTOCOL, expected_sha256: str = PROTOCOL_SHA256) -> dict:
    """البروتوكولُ ببصمته المسجَّلة؛ فإن تغيّر بايتٌ فيه بعد التسجيل رُدّ قبل أيّ استعمال."""
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise JudgeRefused("judge_protocol_changed", "البروتوكول المسجَّل لا يُعدَّل؛ يُسجَّل judge_v2")
    return json.loads(raw.decode("utf-8"))


def judge_family(model: str, protocol: dict) -> str:
    """عائلةُ المحكِّم من اسم نموذجه (قبل «:» المزوّد)، ولا يُقبل إلا ما سمّاه البروتوكول."""
    family = model_family(model.split(":", 1)[0]) if isinstance(model, str) else None
    families = protocol["families"]
    if family is None:
        raise JudgeRefused("judge_family_unknown", str(model))
    if family in families["excluded"]:
        raise JudgeRefused("judge_family_excluded", family)
    if family not in families["allowed"]:
        raise JudgeRefused("judge_family_not_allowed", family)
    return family


def open_judge(transport: str, model: str, protocol: dict) -> dict:
    """محكِّمُ المفتوح: HF بمزوّدٍ مثبَّت <model>:<provider>، أو ollama.com؛ وعائلتُه مسموحة."""
    if transport == "hf_inference_providers":
        if not isinstance(model, str) or not _PINNED.fullmatch(model):
            raise JudgeRefused("judge_provider_unpinned", "HF يُسمّى <model>:<provider>")
    elif transport != "ollama_com":
        raise JudgeRefused("judge_transport_unsupported", str(transport))
    return {"transport": transport, "model": model, "family": judge_family(model, protocol)}


def cohen_kappa(truth: list[str], predicted: list[str]) -> float | None:
    """κ كوهين لحكمين ثنائيين؛ وإن اتّفقا صدفةً تمامًا (pe = 1) فلا κ، لا واحد."""
    n = len(truth)
    if n == 0 or n != len(predicted):
        return None
    observed = sum(a == b for a, b in zip(truth, predicted)) / n
    expected = sum((truth.count(v) / n) * (predicted.count(v) / n) for v in VERDICTS)
    if expected >= 1:
        return None
    return round((observed - expected) / (1 - expected), 4)


def _scores(items: list[dict]) -> dict:
    truth = [i["label"] for i in items]
    predicted = [i["verdict"] for i in items]
    accuracy = round(sum(a == b for a, b in zip(truth, predicted)) / len(items), 4) if items else None
    return {"n": len(items), "kappa": cohen_kappa(truth, predicted), "accuracy": accuracy}


def calibration_result(items: list[dict], protocol: dict) -> dict:
    """أحكامُ المحكِّم على عيّنة المعايرة مقابل تسمياتها؛ والنجاحُ بالعتبة المسجَّلة على الاتحاد."""
    for item in items:
        if item.get("split") != "open":
            raise JudgeRefused("judge_open_only", "المعايرة على الشطر المفتوح وحده")
        if item.get("label") not in VERDICTS:
            raise JudgeRefused("calibration_label_missing", str(item.get("source")))
        if item.get("verdict") not in VERDICTS:
            raise JudgeRefused("calibration_verdict_invalid", str(item.get("source")))
    union = _scores(items)
    sources = sorted({i["source"] for i in items})
    thresholds = protocol["thresholds"]
    passed = (union["kappa"] is not None and union["kappa"] >= thresholds["kappa_min"]
              and union["accuracy"] is not None and union["accuracy"] >= thresholds["accuracy_min"])
    return {**union, "passed": passed,
            "by_source": {s: _scores([i for i in items if i["source"] == s]) for s in sources}}


def calibration_sample(protocol: dict, protocol_sha256: str, *, open_bank: Path = OPEN_BANK,
                       k11_evidence: Path = K11_EVIDENCE) -> dict[str, list[str]]:
    """عيّنةُ المعايرة المجمَّدة بالبروتوكول: حالاتُ ك١١ بمعرّفاتها، ومئةُ حالةٍ مفتوحةٍ ذاتِ فحصٍ حتميّ تُختار طبقيًّا
    ببذرة البصمة. فدليلُ المعايرة يُقابَل بها صفًّا صفًّا، ولا يُقبل دليلٌ اختار حالاتِه بنفسه (ملاحظة Codex على #289).
    وملفّاتُ المهامّ الوكيلة ليست حزمَ حالاتٍ بفحوص، فلا تدخل المجمع."""
    sources = {source["name"]: source for source in protocol["calibration"]["sources"]}
    triage = json.loads(Path(k11_evidence).read_text(encoding="utf-8"))
    owner = sorted(row["id"] for row in triage["real"] + triage["false_positives"])
    kinds = set(sources["automatic_checked"]["check_kinds"])
    pool: dict[str, list[str]] = {}
    for path in sorted(Path(open_bank).glob("*/*.json")):
        if path.name.endswith(".meta.json"):
            continue
        try:
            suite = load_suite(path)
        except CapabilityError:
            continue
        pool.setdefault(path.parent.name, []).extend(
            f"{suite['suite_id']}/{case['case_id']}" for case in suite["cases"]
            if case["checks"] and all(check["kind"] in kinds for check in case["checks"]))
    wanted = sources["automatic_checked"]["cases"]
    picked = select(pool, allocate({t: len(ids) for t, ids in pool.items()}, wanted), f"{protocol_sha256}:calibration")
    automatic = sorted(case for ids in picked.values() for case in ids)
    if len(owner) != sources["k11_owner_ruled"]["cases"] or len(set(automatic)) != wanted:
        raise JudgeRefused("calibration_sample_unavailable", "العيّنةُ المسجَّلة لا تُبنى من البنك المفتوح كما هو")
    return {"k11_owner_ruled": owner, "automatic_checked": automatic}


def accept_sealed_judge(evidence: dict | None, model: str, protocol: dict, protocol_sha256: str,
                        sample: dict[str, list[str]]) -> str:
    """المحكِّمُ على المحجوب هو المسجَّلُ في البروتوكول وحده، بدليل معايرةٍ على نموذجه وبروتوكوله، صفوفُه هي العيّنةُ
    المجمَّدة حالةً حالة، وκ والدقّةُ تُعادان منها؛ فلا يُصدَّق عَلَمٌ ولا رقمٌ مكتوبٌ في الدليل (ملاحظتا Codex على #289)."""
    if model != protocol["sealed"]["judge"]["model"]:
        raise JudgeRefused("judge_not_registered", "محكِّمُ المحجوب هو المسجَّلُ في judge_v1 وحده")
    family = judge_family(model, protocol)
    expected = sorted((source, case) for source, cases in sample.items() for case in cases)
    try:
        same = evidence["protocol_sha256"] == protocol_sha256 and evidence["judge"]["model"] == model
        rows = evidence["rows"]
        bound = sorted((row["source"], row["case"]) for row in rows) == expected
        result = calibration_result(rows, protocol) if same and bound else None
    except (KeyError, TypeError, AttributeError, JudgeRefused):
        result = None
    if not (result and result["passed"]):
        raise JudgeRefused("judge_uncalibrated", "لا يحكم على المحجوب محكِّمٌ بلا دليل معايرة ناجح (OD3)")
    return family


def allocate(tier_counts: dict[str, int], attempts: int) -> dict[str, int]:
    """المحاولاتُ على الطبقات بنسبة حجمها (أكبرُ البواقي، والتعادلُ باسم الطبقة)، ولا تزيد طبقةٌ على حجمها."""
    total = sum(tier_counts.values())
    if total <= 0 or attempts <= 0:
        return {tier: 0 for tier in tier_counts}
    attempts = min(attempts, total)
    exact = {tier: attempts * count / total for tier, count in tier_counts.items()}
    shares = {tier: int(value) for tier, value in exact.items()}
    order = sorted(tier_counts, key=lambda t: (-(exact[t] - shares[t]), t))
    for tier in order[:attempts - sum(shares.values())]:
        shares[tier] += 1
    return {tier: min(shares[tier], tier_counts[tier]) for tier in sorted(tier_counts)}


def select(ids_by_tier: dict[str, list[str]], allocation: dict[str, int], seed: str) -> dict[str, list[str]]:
    """اختيارٌ حتميّ: البذرةُ بصمةُ البروتوكول، والترتيبُ قبل السحب، فلا يغيّر ترتيبُ القراءة العيّنة."""
    chosen = {}
    for tier in sorted(ids_by_tier):
        rng = random.Random(f"{seed}:{tier}")
        chosen[tier] = sorted(rng.sample(sorted(ids_by_tier[tier]), allocation.get(tier, 0)))
    return chosen


def tier_report(rows: Iterable[dict]) -> dict:
    """لكل طبقة: المحاولاتُ ونتائجُها، والنسبةُ على المحاولات كلِّها، ومجالُ Wilson 95٪ بجانبها."""
    tiers: dict[str, dict] = {}
    for row in rows:
        if row["outcome"] not in OUTCOMES:
            raise JudgeRefused("sealed_outcome_invalid", str(row["outcome"]))
        c = tiers.setdefault(row["tier"], {"attempted": 0, "passes": 0, "failures": 0,
                                           "without_checks": 0, "judged": 0, "errors": 0})
        c["attempted"] += 1
        c[{"pass": "passes", "fail": "failures", "without_checks": "without_checks",
           "error": "errors"}[row["outcome"]]] += 1
        c["judged"] += bool(row.get("judged"))
    out = {}
    for tier in sorted(tiers):
        c = tiers[tier]
        out[tier] = {**c, "rate": round(c["passes"] / c["attempted"], 4),
                     "wilson95": wilson(c["passes"], c["attempted"])}
    attempted = sum(c["attempted"] for c in tiers.values())
    passes = sum(c["passes"] for c in tiers.values())
    overall = {"attempted": attempted, "passes": passes,
               "rate": round(passes / attempted, 4) if attempted else None,
               "wilson95": wilson(passes, attempted)}
    return {"by_tier": out, "overall": overall}


def assert_clean(report: dict, identifiers: Iterable[str] = (), texts: Iterable[str] = ()) -> None:
    """التقريرُ لا يحمل معرّفًا ولا نصًّا من المحجوب: يُفحص مسلسلًا قبل أن يُكتب أو يُطبع."""
    serialized = json.dumps(report, ensure_ascii=False)
    leaked = (any(i and len(i) >= 4 and i in serialized for i in identifiers)
              or any(t and len(t) >= LEAK_MIN_CHARS and t in serialized for t in texts))
    if leaked:
        raise JudgeRefused("sealed_output_leak", "تقريرُ المحجوب يحمل معرّفًا أو نصًّا منه")
