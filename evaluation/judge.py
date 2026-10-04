"""المحكِّمُ ومعايرتُه، وحسابُ تقرير المحجوب: بروتوكولٌ مبصومٌ قبل أيّ تشغيل (#288، ك٤٥ #30).

`evaluation/protocols/judge_v1.json` يُسجَّل قبل القياس، فلا تتبع العتبةُ النتيجة. وهذه الوحدةُ لا تنادي نموذجًا
ولا تقرأ محجوبًا؛ هي القواعدُ التي يطبّقها `tools/evaluate_sealed.py` على الماك ومُشغِّلُ المعايرة في #30:

- البروتوكولُ يُقرأ ببصمته المثبَّتة، وتغيُّرُها رفضٌ مسمًّى (`judge_protocol_changed`).
- المحكِّمُ من العائلات المسموحة وحدها؛ والمستبعَدةُ والمجهولةُ تُردّ باسمها.
- المعايرةُ على الشطر المفتوح وحده، والتسميةُ الغائبة (حكمُ المالك المنتظَر) رفضٌ لا تخمين.
- κ كوهين والدقّة على الاتحاد، ولكل مصدرٍ على حدة.
- المحكِّمُ على المحجوب يُقبل بدليل معايرةٍ ناجحٍ لنموذجه نفسِه على البروتوكول نفسِه، وتُعاد الأرقامُ لا يُصدَّق العَلَم:
  التسميةُ الآليّة تُعاد من جوابها بفحوص حالتها، وجوابُ حالة ك١١ هو مرجعُها في البنك، والدليلُ كلُّه بتوقيع المالك.
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

from core.ledger import LedgerCorrupt
from core.signing import ED25519, SigningRefused, load_trusted_public_key, verify_signature_bytes
from evaluation.capabilities import CapabilityError, _checks, _json_bytes, load_suite
from evaluation.multi_system_review import model_family
from evaluation.retrieval_general import wilson

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "evaluation" / "protocols" / "judge_v1.json"
K11_EVIDENCE = ROOT / "docs" / "probe" / "k11-owner-queue-triage-20260925.json"
OPEN_BANK = ROOT / "evaluation" / "banks" / "kimi_v1" / "open"
PROTOCOL_SHA256 = "70610581fbf677a1d25c585b36cf115a12dbc189894f0e2e948d9bb03bd59bd4"
VERDICTS = ("correct", "incorrect")
OUTCOMES = ("pass", "fail", "without_checks", "error")
# نصٌّ أقصرُ من هذا لا يُبحث عنه في التقرير: كلمةٌ قصيرة كـ«نعم» تقع في أيّ تقرير ولا تدلّ على حالة.
LEAK_MIN_CHARS = 8
_PINNED = re.compile(r"[^\s:]+:[^\s:]+")
CALIBRATION_DOMAIN = b"diwan-judge-calibration-v1\x00"
SIGNATURE_FIELD = "owner_signature"


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


def _kappa(truth: list[str], predicted: list[str]) -> float | None:
    """κ كوهين لحكمين ثنائيين بلا تقريب؛ وإن اتّفقا صدفةً تمامًا (pe = 1) فلا κ، لا واحد."""
    n = len(truth)
    if n == 0 or n != len(predicted):
        return None
    observed = sum(a == b for a, b in zip(truth, predicted)) / n
    expected = sum((truth.count(v) / n) * (predicted.count(v) / n) for v in VERDICTS)
    if expected >= 1:
        return None
    return (observed - expected) / (1 - expected)


def cohen_kappa(truth: list[str], predicted: list[str]) -> float | None:
    """κ المنشورةُ بأربع منازل؛ والعتبةُ تُقارَن بغير المقرَّبة (`_raw_scores`)."""
    kappa = _kappa(truth, predicted)
    return None if kappa is None else round(kappa, 4)


def _raw_scores(items: list[dict]) -> dict:
    truth = [i["label"] for i in items]
    predicted = [i["verdict"] for i in items]
    accuracy = sum(a == b for a, b in zip(truth, predicted)) / len(items) if items else None
    return {"n": len(items), "kappa": _kappa(truth, predicted), "accuracy": accuracy}


def _published(scores: dict) -> dict:
    return {key: round(value, 4) if isinstance(value, float) else value for key, value in scores.items()}


def calibration_result(items: list[dict], protocol: dict) -> dict:
    """أحكامُ المحكِّم على عيّنة المعايرة مقابل تسمياتها؛ والنجاحُ بالعتبة المسجَّلة على الاتحاد."""
    for item in items:
        if item.get("split") != "open":
            raise JudgeRefused("judge_open_only", "المعايرة على الشطر المفتوح وحده")
        if item.get("label") not in VERDICTS:
            raise JudgeRefused("calibration_label_missing", str(item.get("source")))
        if item.get("verdict") not in VERDICTS:
            raise JudgeRefused("calibration_verdict_invalid", str(item.get("source")))
    # العتبةُ تُقارَن بالقيمة غير المقرَّبة، والتقريبُ للنشر وحده: κ = 0.59996 لا تصير 0.6 فتنجح (ملاحظة Codex على #289)
    union = _raw_scores(items)
    sources = sorted({i["source"] for i in items})
    thresholds = protocol["thresholds"]
    passed = (union["kappa"] is not None and union["kappa"] >= thresholds["kappa_min"]
              and union["accuracy"] is not None and union["accuracy"] >= thresholds["accuracy_min"])
    return {**_published(union), "passed": passed,
            "by_source": {s: _published(_raw_scores([i for i in items if i["source"] == s])) for s in sources}}


def open_bank_digest(open_bank: Path = OPEN_BANK) -> str:
    """بصمةُ الشطر المفتوح كلِّه: كلُّ ملفٍّ فيه بمساره النسبيّ وبصمتِه، مسلسلةً بترتيبٍ ثابت."""
    root = Path(open_bank)
    files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(root.rglob("*")) if path.is_file()}
    return hashlib.sha256(_json_bytes(files)).hexdigest()


def _open_suites(open_bank: Path, expected_sha256: str):
    """حزمُ الشطر المفتوح بطبقاتها، بعد أن يطابق الشطرُ بصمتَه المسجَّلة: فبنكٌ استُبدل (v1.2) يُردّ ولا يُقاس باسم
    judge_v1 (ملاحظة Codex على #289). وملفّاتُ المهامّ الوكيلة ليست حزمَ حالاتٍ بفحوص فلا تُعدّ."""
    if open_bank_digest(open_bank) != expected_sha256:
        raise JudgeRefused("calibration_bank_changed", "الشطرُ المفتوح غيرُ المسجَّل في judge_v1؛ يُسجَّل judge_v2")
    for path in sorted(Path(open_bank).glob("*/*.json")):
        if path.name.endswith(".meta.json"):
            continue
        try:
            yield path.parent.name, load_suite(path)
        except CapabilityError:
            continue


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
    for tier, suite in _open_suites(open_bank, protocol["calibration"]["open_bank_sha256"]):
        pool.setdefault(tier, []).extend(
            f"{suite['suite_id']}/{case['case_id']}" for case in suite["cases"]
            if case["checks"] and all(check["kind"] in kinds for check in case["checks"]))
    wanted = sources["automatic_checked"]["cases"]
    picked = select(pool, allocate({t: len(ids) for t, ids in pool.items()}, wanted), f"{protocol_sha256}:calibration")
    automatic = sorted(case for ids in picked.values() for case in ids)
    if len(owner) != sources["k11_owner_ruled"]["cases"] or len(set(automatic)) != wanted:
        raise JudgeRefused("calibration_sample_unavailable", "العيّنةُ المسجَّلة لا تُبنى من البنك المفتوح كما هو")
    return {"k11_owner_ruled": owner, "automatic_checked": automatic}


def calibration_truth(protocol: dict, sample: dict[str, list[str]], *, open_bank: Path = OPEN_BANK) -> dict[str, dict]:
    """ما يُقابَل به كلُّ صفّ: لحالة ك١١ مرجعُها في البنك (جوابُها المراجَع)، وللحالة الآليّة فحوصُها الحتميّة
    التي تُعاد بها تسميتُها من جوابها. فلا يُصدَّق ما كتبه الدليلُ عن حقيقة حالة (ملاحظة Codex على #289)."""
    owner, automatic = set(sample["k11_owner_ruled"]), set(sample["automatic_checked"])
    truth: dict[str, dict] = {"k11_owner_ruled": {}, "automatic_checked": {}}
    for _, suite in _open_suites(open_bank, protocol["calibration"]["open_bank_sha256"]):
        for case in suite["cases"]:
            qid = f"{suite['suite_id']}/{case['case_id']}"
            if case["case_id"] in owner:
                truth["k11_owner_ruled"][case["case_id"]] = case["reference"]
            if qid in automatic:
                truth["automatic_checked"][qid] = case["checks"]
    if set(truth["k11_owner_ruled"]) != owner or set(truth["automatic_checked"]) != automatic:
        raise JudgeRefused("calibration_sample_unavailable", "حالةٌ من العيّنة ليست في البنك المفتوح")
    return truth


def calibration_message(evidence: dict) -> bytes:
    """ما يوقّعه المالك: الدليلُ كلُّه بلا حقل التوقيع، بمجالٍ مخصوص فلا يُنقل إليه توقيعُ مرساةٍ أو حكم."""
    return CALIBRATION_DOMAIN + _json_bytes({k: v for k, v in evidence.items() if k != SIGNATURE_FIELD})


def sign_calibration(evidence: dict, *, private_seed: bytes) -> dict:
    """يوقّع المالكُ الدليلَ ببذرته (من سلسلة مفاتيح الماك، لا من ملف): أن الأجوبةَ أجوبةُ المحرّك المجمَّد،
    والأحكامَ أحكامُ المحكِّم، وتسمياتِ ك١١ أحكامُه هو."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(private_seed)
    return {**evidence, SIGNATURE_FIELD: ED25519 + ":" + key.sign(calibration_message(evidence)).hex()}


def _owner_signed(evidence: dict, public_key: bytes | None) -> bool:
    """توقيعُ المالك على الدليل؛ وتعذُّرُ التحقّق (مفتاحٌ أو مكتبةٌ غائبة) رفضٌ مسمًّى غيرُ «لم يُوقَّع» (ق٣٩)."""
    raw = evidence.get(SIGNATURE_FIELD)
    if not isinstance(raw, str):
        return False
    try:
        key = load_trusted_public_key() if public_key is None else public_key
        return verify_signature_bytes((raw + "\n").encode("ascii"), calibration_message(evidence), public_key=key)
    except SigningRefused as exc:
        if exc.code != "signature_mismatch":
            raise JudgeRefused("calibration_signature_unverifiable", exc.code) from None
        return False
    except (LedgerCorrupt, UnicodeError, ValueError):
        return False


def _row_bound(row: dict, truth: dict[str, dict]) -> bool:
    """الحالةُ الآليّة: تسميتُها هي ما تقوله فحوصُها في جوابها. وحالةُ ك١١: جوابُها مرجعُها، وتسميتُها حكمُ المالك."""
    candidate, ground = row.get("candidate"), truth[row["source"]][row["case"]]
    if not isinstance(candidate, str):
        return False
    if row["source"] == "automatic_checked":
        passed = all(result["passed"] for result in _checks(candidate, ground))
        return row.get("label") == ("correct" if passed else "incorrect")
    return candidate == ground


def accept_sealed_judge(evidence: dict | None, model: str, protocol: dict, protocol_sha256: str,
                        truth: dict[str, dict], *, judge_digest: str | None,
                        public_key: bytes | None = None) -> str:
    """المحكِّمُ على المحجوب هو المسجَّلُ في البروتوكول وحده، بدليل معايرةٍ موقَّعٍ من المالك على نموذجه وبروتوكوله،
    صفوفُه هي العيّنةُ المجمَّدة حالةً حالة، وكلُّ صفٍّ مربوطٌ بحقيقة حالته، وκ والدقّةُ تُعادان منها؛ فلا يُصدَّق
    عَلَمٌ ولا رقمٌ ولا تسميةٌ مكتوبةٌ في الدليل (ملاحظات Codex على #289). والدليلُ يسمّي بصمتَي المحرّك والمحكِّم:
    المحرّكُ بصمتُه المسجَّلة في البروتوكول، والمحكِّمُ بصمتُه المحلولة الآن؛ فوسمٌ أُعيد توجيهُه بعد المعايرة يُردّ."""
    if model != protocol["sealed"]["judge"]["model"]:
        raise JudgeRefused("judge_not_registered", "محكِّمُ المحجوب هو المسجَّلُ في judge_v1 وحده")
    family = judge_family(model, protocol)
    expected = sorted((source, case) for source, cases in truth.items() for case in cases)
    try:
        same = (bool(judge_digest) and evidence["protocol_sha256"] == protocol_sha256
                and evidence["judge"] == {"model": model, "digest": judge_digest}
                and evidence["engine"]["digest"] == protocol["sealed"]["engine_digest"])
        rows = evidence["rows"]
        bound = (sorted((row["source"], row["case"]) for row in rows) == expected
                 and all(_row_bound(row, truth) for row in rows))
        signed = same and bound and _owner_signed(evidence, public_key)
        result = calibration_result(rows, protocol) if signed else None
    except (KeyError, TypeError, AttributeError):
        result = None
    except JudgeRefused as exc:
        if exc.code == "calibration_signature_unverifiable":
            raise
        result = None
    if not (result and result["passed"]):
        raise JudgeRefused("judge_uncalibrated", "لا يحكم على المحجوب محكِّمٌ بلا دليل معايرة موقَّعٍ ناجح (OD3)")
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
