"""Bounded automatic review, never a human verdict or product release.

Reviewers judge a frozen artifact against a frozen rubric independently. Their
agreement cannot override deterministic failures. Model metadata is evidence
reported by the local Ollama server, not an attestation of vendor independence.
JSON hashes bind content; they do not authenticate the person producing a file.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any


class AutomaticReviewError(ValueError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def _fail(code: str, detail: str = "") -> None:
    raise AutomaticReviewError(code, detail)


def _fields(value: Any, fields: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        _fail("schema_fields", label)


def _text(value: Any, label: str, *, limit: int = 16000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _fail("text_invalid", label)


def _integer(value: Any, label: str, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= 2**53 - 1:
        _fail("integer_invalid", label)


def _timestamp(value: Any) -> None:
    _text(value, "started_at", limit=64)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AutomaticReviewError("timestamp_invalid") from exc
    if parsed.tzinfo is None or "T" not in value:
        _fail("timestamp_invalid")


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key", key)
        result[key] = value
    return result


def parse_json(raw: str | bytes) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: _fail("nonfinite_json"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, AutomaticReviewError):
            raise
        raise AutomaticReviewError("invalid_json") from exc


def canonical_bytes(value: Any) -> bytes:
    """Canonical JSON for review artifacts, including finite metric floats."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        # Disallow values Python's encoder silently coerces (e.g. integer keys).
        if parse_json(encoded) != value:
            _fail("non_json_value")
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, AutomaticReviewError):
            raise
        raise AutomaticReviewError("non_json_value") from exc


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def pointer_value(artifact: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        _fail("pointer_invalid")
    node = artifact
    if not pointer:
        return node
    for raw in pointer[1:].split("/"):
        if re.search(r"~(?![01])", raw):
            _fail("pointer_invalid")
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", part):
                _fail("pointer_missing")
            index = int(part)
            if index >= len(node):
                _fail("pointer_missing")
            node = node[index]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            _fail("pointer_missing")
    return node


def validate_rubric(rubric: dict) -> dict:
    _fields(rubric, {"schema_version", "rubric_id", "criteria", "deterministic_rules"}, "rubric")
    if type(rubric["schema_version"]) is not int or rubric["schema_version"] != 1:
        _fail("schema_version")
    _text(rubric["rubric_id"], "rubric_id", limit=128)
    for name in ("criteria", "deterministic_rules"):
        if not isinstance(rubric[name], list) or not 1 <= len(rubric[name]) <= 100:
            _fail("rubric_entries_invalid", name)
        seen = set()
        for entry in rubric[name]:
            fields = ({"id", "instruction"} if name == "criteria" else
                      {"id", "pointer", "operator", "expected"})
            _fields(entry, fields, name)
            _text(entry["id"], "id", limit=128)
            if entry["id"] in seen:
                _fail("duplicate_rubric_id", name)
            seen.add(entry["id"])
            if name == "criteria":
                _text(entry["instruction"], "instruction")
            else:
                if entry["operator"] not in ("equals", "nonempty"):
                    _fail("rule_operator_invalid")
                p = entry["pointer"]
                if not isinstance(p, str) or (p and not p.startswith("/")) or re.search(r"~(?![01])", p):
                    _fail("pointer_invalid")
                if entry["operator"] == "nonempty" and entry["expected"] is not None:
                    _fail("rule_expected_invalid")
    canonical_bytes(rubric)
    return rubric


def review_binding(artifact: dict, rubric: dict, *, run_id: str | None = None) -> dict:
    if not isinstance(artifact, dict):
        _fail("artifact_invalid")
    actual_run = artifact.get("run_id")
    _text(actual_run, "artifact.run_id", limit=256)
    if run_id is not None and actual_run != run_id:
        _fail("run_mismatch")
    validate_rubric(rubric)
    return {"artifact_sha256": sha256(artifact), "rubric_sha256": sha256(rubric),
            "run_id": actual_run}


def deterministic_checks(artifact: dict, rubric: dict) -> list[dict]:
    """Evaluate explicit rules locally. No reviewer supplies these outcomes.

    Rule adequacy is a separate rubric-design question: checking a stored boolean
    does not prove its underlying claim. Prefer output invariants over assertions.
    """
    validate_rubric(rubric)
    checks = []
    for rule in rubric["deterministic_rules"]:
        try:
            actual = pointer_value(artifact, rule["pointer"])
            passed = (canonical_bytes(actual) == canonical_bytes(rule["expected"])
                      if rule["operator"] == "equals" else
                      isinstance(actual, (str, list, dict)) and len(actual) > 0)
            check = {"id": rule["id"], "status": "pass" if passed else "fail",
                     "reason": "rule_satisfied" if passed else "rule_unsatisfied",
                     "observed_sha256": sha256(actual)}
        except AutomaticReviewError as exc:
            check = {"id": rule["id"], "status": "fail", "reason": exc.code,
                     "observed_sha256": None}
        checks.append(check)
    return checks


# عائلةُ النموذج بمورِّده (AGENTS §٤): Gemini وGemma عائلةٌ واحدة، فإن صار Gemma محرّكًا خرج Gemini من التحكيم.
# وتُشتقّ من اسم النموذج بهذا الجدول الواحد في المراجعتين (هنا، و`evaluation/external_review.py`)، ولا يُكتفى بما يُعلَن.
FAMILY_PREFIXES = (
    ("deepseek", "deepseek"),
    ("mistral", "mistral"), ("ministral", "mistral"), ("magistral", "mistral"),
    ("devstral", "mistral"), ("codestral", "mistral"),
    ("qwen", "qwen"), ("qwq", "qwen"),
    ("kimi", "kimi"),
    ("gpt", "openai"),
    ("gemini", "google"), ("gemma", "google"),
    ("claude", "anthropic"),
    ("llama", "meta"), ("phi", "microsoft"), ("granite", "ibm"),
    ("glm", "zhipu"), ("minimax", "minimax"), ("jais", "inception"),
    ("falcon", "tii"), ("nemotron", "nvidia"),
)


def model_family(name: str) -> str | None:
    """المورِّدُ من اسم النموذج أو عائلته المعلَنة (آخرُ مقطعٍ بعد «/»)؛ وما لا يُعرف None."""
    base = name.lower().strip().rsplit("/", 1)[-1]
    return next((family for prefix, family in FAMILY_PREFIXES if base.startswith(prefix)), None)


def identity_family(identity: dict, role: str) -> str:
    """العائلةُ تُشتقّ من اسم النموذج، والمجهولُ مرفوض؛ والمعلَنةُ إن عُرفت تطابقها (ق٤٩ وق٥٠ بصرامةٍ واحدة)."""
    derived = model_family(identity["model"])
    if derived is None:
        _fail(f"{role}_family_unknown", identity["model"])
    declared = model_family(identity["family"])
    if declared is not None and declared != derived:
        _fail(f"{role}_family_mismatch", identity["model"])
    return derived


def family_group(family: str) -> str:
    _text(family, "family", limit=128)
    normalized = family.lower().strip()
    # Architecture versions/quantizations of one lineage are not two reviewers.
    for prefix in ("qwen", "gemma", "llama", "mistral", "phi", "deepseek"):
        if normalized.startswith(prefix):
            return prefix
    return normalized


def validate_identity(identity: dict) -> dict:
    _fields(identity, {"provider", "model", "digest", "family"}, "identity")
    if identity["provider"] != "ollama":
        _fail("provider_unsupported")
    _text(identity["model"], "model", limit=256)
    if not isinstance(identity["digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", identity["digest"]):
        _fail("model_digest_invalid")
    family_group(identity["family"])
    identity_family(identity, "review")
    return identity


def validate_engine(engine: dict) -> dict:
    """هويةُ المحرّك الذي أنتج المادة المُراجَعة — تُعلَن ولا تُستنتج.

    شكلُها شكلُ هوية المراجع لأنها تُقارن بها، لكنّ مزوّدها حرٌّ: المحرّكُ قد
    يكون محليًّا عبر MLX بينما المراجعون عبر Ollama.
    """
    _fields(engine, {"provider", "model", "digest", "family"}, "engine")
    _text(engine["provider"], "engine.provider", limit=128)
    _text(engine["model"], "engine.model", limit=256)
    if not isinstance(engine["digest"], str) or not re.fullmatch(r"[0-9a-f]{64}", engine["digest"]):
        _fail("engine_digest_invalid")
    family_group(engine["family"])
    identity_family(engine, "engine")
    return engine


def validate_identities(identities: list[dict], engine: dict) -> None:
    """المراجعُ ليس من عائلة المحرّك المُراجَع — والمحرّكُ لا يُترك مجهولًا.

    المحكِّمُ من عائلة المحكوم عليه يرفع درجاتِ عائلته؛ وهذا مقيسٌ لا مظنون
    (AGENTS.md §٤). فلا سبيلَ إلى بناء حزمة مراجعةٍ بلا إعلان المحرّك: لو كان
    المحرّك اختياريًّا لصار تركُه هو الطريقَ إلى تعطيل الحارس.
    """
    validate_engine(engine)
    if not isinstance(identities, list) or not 2 <= len(identities) <= 8:
        _fail("insufficient_review_systems")
    engine_family = identity_family(engine, "engine")
    seen_models, seen_digests, seen_families = set(), set(), set()
    for identity in identities:
        validate_identity(identity)
        model = identity["model"].casefold()
        digest = identity["digest"]
        family = identity_family(identity, "review")
        if family == engine_family:
            _fail("reviewer_shares_engine_family")
        if digest == engine["digest"] or model == engine["model"].casefold():
            _fail("reviewer_is_the_engine")
        if model in seen_models or digest in seen_digests:
            _fail("duplicate_review_model")
        if family in seen_families:
            _fail("duplicate_review_family")
        seen_models.add(model)
        seen_digests.add(digest)
        seen_families.add(family)


def validate_model_response(response: dict, artifact: dict, rubric: dict, binding: dict) -> dict:
    _fields(response, {"binding", "judgments"}, "model_response")
    if response["binding"] != binding:
        _fail("review_binding_mismatch")
    judgments = response["judgments"]
    expected = {c["id"] for c in rubric["criteria"]}
    if not isinstance(judgments, list) or len(judgments) != len(expected):
        _fail("judgment_coverage")
    seen = set()
    for judgment in judgments:
        _fields(judgment, {"criterion_id", "verdict", "reason", "evidence"}, "judgment")
        cid = judgment["criterion_id"]
        if not isinstance(cid, str) or cid not in expected or cid in seen:
            _fail("judgment_coverage")
        seen.add(cid)
        if judgment["verdict"] not in ("pass", "fail", "inconclusive"):
            _fail("judgment_verdict")
        _text(judgment["reason"], "reason")
        evidence = judgment["evidence"]
        if not isinstance(evidence, list) or len(evidence) > 100 or (judgment["verdict"] == "pass" and not evidence):
            _fail("judgment_evidence")
        for pointer in evidence:
            pointer_value(artifact, pointer)
    return response


def _validate_record(record: dict, artifact: dict, rubric: dict, binding: dict) -> None:
    _fields(record, {"identity", "binding", "settings", "started_at", "elapsed_ms",
                     "raw_output", "response", "error"}, "review_record")
    validate_identity(record["identity"])
    if record["binding"] != binding:
        _fail("review_binding_mismatch")
    _fields(record["settings"], {"temperature", "seed", "num_ctx", "num_predict"}, "settings")
    for key, value in record["settings"].items():
        _integer(value, "settings." + key, 1 if key in ("num_ctx", "num_predict") else 0)
    _timestamp(record["started_at"])
    _integer(record["elapsed_ms"], "elapsed_ms")
    if not isinstance(record["raw_output"], str):
        _fail("raw_output_invalid")
    if record["error"] is not None:
        _text(record["error"], "error", limit=128)
        if record["response"] is not None:
            _fail("error_with_response")
    else:
        parsed = parse_json(record["raw_output"])
        if parsed != record["response"]:
            _fail("raw_response_mismatch")
        validate_model_response(parsed, artifact, rubric, binding)


def build_review_package(artifact: dict, rubric: dict, reviews: list[dict], *,
                         engine: dict, run_id: str | None = None) -> dict:
    binding = review_binding(artifact, rubric, run_id=run_id)
    if not isinstance(reviews, list):
        _fail("reviews_invalid")
    for record in reviews:
        _validate_record(record, artifact, rubric, binding)
    validate_identities([r["identity"] for r in reviews], engine)
    checks = deterministic_checks(artifact, rubric)
    disagreements = []
    for criterion in rubric["criteria"]:
        cid = criterion["id"]
        votes = [next(j["verdict"] for j in r["response"]["judgments"] if j["criterion_id"] == cid)
                 for r in reviews if r["error"] is None]
        if len(set(votes)) > 1:
            disagreements.append(cid)
    incomplete = any(r["error"] is not None or any(j["verdict"] == "inconclusive"
                     for j in r["response"]["judgments"]) for r in reviews)
    failed = any(c["status"] != "pass" for c in checks)
    judgments_failed = any(r["error"] is None and any(j["verdict"] == "fail"
                          for j in r["response"]["judgments"]) for r in reviews)
    status = ("rejected" if failed else "inconclusive" if disagreements or incomplete
              else "rejected" if judgments_failed else "accepted")
    return {"schema_version": 1, "kind": "multi_system_automatic_review",
            "binding": binding, "engine": engine,
            "deterministic_checks": checks, "reviews": reviews,
            "disagreements": disagreements, "status": status, "human_review": False,
            "file_integrity": "content_bound", "product_readiness": "not_assessed",
            "limitations": ["automatic_not_human_review", "server_reported_model_identity",
                            "different_families_do_not_prove_independent_training",
                            "content_hash_is_not_authentication", "rubric_adequacy_not_certified",
                            "engine_identity_is_declared_not_verified"]}


def validate_review_package(package: dict, artifact: dict, rubric: dict, *,
                            run_id: str | None = None, engine: dict | None = None) -> dict:
    """Recompute all derived fields; never trust stored accepted/check flags.

    المحرّكُ يُقرأ من الحزمة فتُعاد عليه قاعدةُ العائلة عند كل تحقّق — فحزمةٌ
    مخزَّنةٌ راجعها قريبُ محرّكها تُردّ ولو لم يعرف الفاحصُ المحرّكَ سلفًا. ومن
    عرفه يمرّره فيُثبَّت عليه، ولا يُقبل اختلافُه عمّا في الحزمة.
    """
    if not isinstance(package, dict) or not isinstance(package.get("reviews"), list):
        _fail("review_package_invalid")
    stored = package.get("engine")
    if not isinstance(stored, dict):
        _fail("engine_missing")
    if engine is not None and canonical_bytes(engine) != canonical_bytes(stored):
        _fail("engine_mismatch")
    expected = build_review_package(artifact, rubric, package["reviews"],
                                    engine=stored, run_id=run_id)
    if canonical_bytes(package) != canonical_bytes(expected):
        _fail("review_package_mismatch")
    return expected
