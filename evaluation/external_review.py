"""المراجعة الخارجية لبنوك القياس: DeepSeek وMistral يحكمان على صحة البنك آليًّا.

كان المالك يلصق التكليفَ والملفاتِ في نوافذ محادثتهما بيده، ثم يعيد الأحكام.
وهنا نستدعيهما نحن: نداءٌ مستقلٌّ لكل ملفٍّ ومراجع، لا يحمل إلا ما نرسله، فلا يرى
مراجعٌ حكمَ الآخر أبدًا، ولا يرى أيٌّ منهما شيفرةَ ديوان.

هذه الأداة **لبنوك القياس وحدها** (ق٥٠). مراجعةُ جودة ديوان نفسه (ق٤٩) تبقى محليةً
ببصمة GGUF في `tools/review_automatically.py`، ولا يُرخى حارسُها هنا.

وثلاثُ قواعد تُفرض في الشيفرة لا في النثر:
- الشطرُ المحجوب لا يُرسل إلى أحد: يراجعه المالك وحده (AGENTS.md §٤).
- المراجعُ ليس من عائلة المحرّك ولا المؤلّف ولا المطوِّرين، ولا مراجعان من عائلة.
- لا يسقط ملفٌّ صمتًا: ردٌّ غيرُ صالح يُعاد مرةً، ثم يُسجَّل خطؤه برمزه.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Callable

from evaluation.multi_system_review import AutomaticReviewError, model_family, parse_json

REFERENCE_VERDICTS = ("correct", "incorrect", "ambiguous")
RUBRIC_VERDICTS = ("sufficient", "insufficient")
JUDGMENT_FIELDS = {"id", "reference", "rubric", "my_answer", "reason", "fix"}

# العائلة تُستخرج من اسم النموذج ببادئاتٍ صريحة، بالجدول الواحد في `evaluation/multi_system_review.py`.
# واسمٌ لا تعرفه القائمة يُرفض: استقلالٌ لا يُعرف مصدرُه لا يُثبَت.

# المحرّكُ اليوم qwen3.5:9b (providers/ollama.py::DEFAULT_MODEL، ق٥٤) وعائلتُه Qwen. واختبارٌ يربط هذه القيمة بالمحرّك
# الفعليّ، فإن تغيّر المحرّكُ سقط حتى تتبعه القاعدة.
ENGINE_FAMILY = "qwen"
AUTHOR_FAMILY = "kimi"
_REGISTRY = Path(__file__).resolve().parents[1] / "registry" / "agents.json"


def developer_families(registry_path: Path = _REGISTRY) -> tuple[str, ...]:
    """عائلاتُ العملاء المطوِّرين من `registry/agents.json` (ك٤٠): تسجيلُ عميلٍ من عائلةٍ جديدة يُخرجها من مراجعة البنوك
    آليًّا، فلا يراجع بنكًا نموذجٌ من عائلةٍ تطوّر ديوان. المالكُ (`human/*`) ليس عائلةَ نموذج."""
    agents = json.loads(registry_path.read_text(encoding="utf-8"))["agents"]
    return tuple(sorted({agent.split("/", 1)[0] for agent in agents if not agent.startswith("human/")}))


DEVELOPER_FAMILIES = developer_families()

DEFAULT_REVIEWERS = ("deepseek-v4-flash:cloud", "mistral-large-3:675b-cloud")

MEASUREMENT_LIMITS = (
    "cloud_model_identity_is_the_service_name_not_a_verified_weight_digest",
    "llm_reviewers_not_human",
    "open_split_only_sealed_is_owner_reviewed",
    "single_call_per_file_no_variance_estimate",
)

Transport = Callable[[str, str, str, dict], str]


def reviewer_family(model: str) -> str:
    # الجدولُ الواحد في `evaluation/multi_system_review.py::FAMILY_PREFIXES` (ق٤٩ وق٥٠ بصرامةٍ واحدة)
    family = model_family(model)
    if family is None:
        raise AutomaticReviewError("reviewer_family_unknown", model)
    return family


def check_reviewers(models: list[str]) -> dict[str, str]:
    """يُعيد {النموذج: العائلة} أو يرفض المجموعة كلها برمز."""
    if len(models) < 2:
        raise AutomaticReviewError("two_reviewers_required")
    families: dict[str, str] = {}
    for model in models:
        family = reviewer_family(model)
        if family == ENGINE_FAMILY:
            raise AutomaticReviewError("reviewer_is_engine_family", model)
        if family == AUTHOR_FAMILY:
            raise AutomaticReviewError("reviewer_is_author_family", model)
        if family in DEVELOPER_FAMILIES:
            raise AutomaticReviewError("reviewer_is_developer_family", model)
        if family in families.values():
            raise AutomaticReviewError("duplicate_reviewer_family", model)
        families[model] = family
    return families


def response_schema() -> dict:
    """مخطط JSON يُمرَّر إلى Ollama في حقل format، مطابقٌ لشكل الحكم في التكليف."""
    judgment = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "reference": {"type": "string", "enum": list(REFERENCE_VERDICTS)},
            "rubric": {"type": "string", "enum": list(RUBRIC_VERDICTS)},
            "my_answer": {"type": "string"},
            "reason": {"type": "string"},
            "fix": {"type": ["string", "null"]},
        },
        "required": sorted(JUDGMENT_FIELDS),
    }
    return {"type": "object",
            "properties": {"reviewer": {"type": "string"}, "file": {"type": "string"},
                           "judgments": {"type": "array", "items": judgment}},
            "required": ["judgments"]}


def brief_text(brief_path: Path) -> str:
    """ما يراه المراجع من التكليف: من «من أنت» فما بعده، بلا ملاحظة المالك."""
    text = brief_path.read_text(encoding="utf-8")
    marker = "## من أنت"
    if marker not in text:
        raise AutomaticReviewError("brief_marker_missing", str(brief_path))
    return text[text.index(marker):]


def item_ids(bank_file: dict) -> list[str]:
    if isinstance(bank_file.get("cases"), list):
        return [c["case_id"] for c in bank_file["cases"]]
    if isinstance(bank_file.get("tasks"), list):
        return [t["task_id"] for t in bank_file["tasks"]]
    raise AutomaticReviewError("bank_file_without_items")


def json_object_in(raw: str) -> str:
    """نصُّ أوّلِ كائنٍ متوازنٍ في ردّ نموذج، وإلا فالردُّ كما هو.

    بعضُ المراجعين يتجاهل حقلَ المخطّط في Ollama ويردّ نثرًا داخله كتلةُ JSON
    بين سياج ```. وردُّه مرفوضٌ اليوم بـinvalid_json، فيُقصى المراجعُ لعيبٍ في
    **النقل** لا في حكمه — وذلك يحصر مجموعةَ المراجعين في النماذج ذات المخرجِ
    البنيويِّ الأصيل، وهو انحيازٌ لا علاقة له بجودة المراجعة.

    والاستخراجُ لا يُرخّص شيئًا: النصُّ المستخرَج يمرّ بـparse_json الصارمة
    نفسِها (ومنها كشفُ المفاتيح المكرّرة) ثم بفحص الحقول. فما لا يُستخرج منه
    كائنٌ صالحٌ يبقى خطأً برمزه، كما تقضي ق٥٠.
    """
    text = raw.strip()
    if text.startswith("{"):
        return raw
    start = text.find("{")
    if start < 0:
        return raw
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return raw


def validate_response(raw: str, expected_ids: list[str]) -> list[dict]:
    """ردٌّ صالح يغطي الحالاتِ بالضبط: لا ناقص ولا زائد ولا مكرّر."""
    parsed = parse_json(json_object_in(raw))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("judgments"), list):
        raise AutomaticReviewError("judgments_missing")
    seen: list[str] = []
    for judgment in parsed["judgments"]:
        if not isinstance(judgment, dict) or set(judgment) != JUDGMENT_FIELDS:
            raise AutomaticReviewError("judgment_fields")
        if judgment["reference"] not in REFERENCE_VERDICTS:
            raise AutomaticReviewError("reference_verdict_invalid", str(judgment["id"]))
        if judgment["rubric"] not in RUBRIC_VERDICTS:
            raise AutomaticReviewError("rubric_verdict_invalid", str(judgment["id"]))
        for field in ("id", "my_answer", "reason"):
            if not isinstance(judgment[field], str) or not judgment[field].strip():
                raise AutomaticReviewError(f"{field}_empty", str(judgment["id"]))
        # صرامةٌ حيث يُحكَم، وتسامحٌ حيث يُشار: reference وrubric هما الحكم،
        # وقد فُحصا أعلاه بقائمةٍ مغلقة. أمّا fix فاقتراحٌ حرٌّ لا يُبنى عليه قرار.
        # وقيس في ٢٤ سبتمبر ٢٠٢٦ أنّ mistral-large-3 يردّ fix كائنًا لا نصًّا،
        # فكانت مراجعةٌ **صحيحةُ الحكم** تُطرح كلُّها لشكل حقلٍ استشاريّ. وإلزامُه
        # بالشكل بالمخطّط لا يُجدي لأنّه يتجاهل حقل format أصلًا (مقيس).
        if judgment["fix"] is not None and not isinstance(judgment["fix"], str):
            try:
                judgment["fix"] = json.dumps(judgment["fix"], ensure_ascii=False,
                                             sort_keys=True, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise AutomaticReviewError("fix_invalid", judgment["id"]) from exc
        seen.append(judgment["id"])
    if len(seen) != len(set(seen)):
        raise AutomaticReviewError("judgment_id_duplicate")
    if set(seen) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(seen))
        extra = sorted(set(seen) - set(expected_ids))
        raise AutomaticReviewError("judgment_ids_mismatch",
                                   f"missing={missing[:5]} extra={extra[:5]}")
    return parsed["judgments"]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _slug(model: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-." else "_" for ch in model)


def open_files(bank_dir: Path) -> list[Path]:
    root = bank_dir / "open"
    if not root.is_dir():
        raise AutomaticReviewError("open_split_missing", str(root))
    files = sorted(p for p in root.rglob("*.json") if not p.name.endswith(".meta.json"))
    for path in files:
        if "sealed" in path.relative_to(bank_dir).parts:
            raise AutomaticReviewError("sealed_never_reviewed_externally", str(path))
    return files


def review_file(path: Path, bank_dir: Path, model: str, family: str, transport: Transport,
                *, brief: str, brief_sha: str) -> dict:
    """نداءٌ لملفٍّ واحدٍ ومراجعٍ واحد. يُعيد السجلّ المحفوظ."""
    if "sealed" in path.resolve().relative_to(bank_dir.resolve()).parts:
        raise AutomaticReviewError("sealed_never_reviewed_externally", str(path))
    raw_file = path.read_bytes()
    bank_file = parse_json(raw_file)
    expected = item_ids(bank_file)
    relative = path.relative_to(bank_dir / "open").as_posix()
    user = raw_file.decode("utf-8")
    record = {"model": model, "family": family, "file": relative,
              "file_sha256": _sha(raw_file), "brief_sha256": brief_sha,
              "started_at": _now(), "attempts": [], "judgments": None, "error": None}
    start = time.monotonic()
    for attempt in (1, 2):
        try:
            raw = transport(model, brief, user, response_schema())
        except AutomaticReviewError as exc:
            record["attempts"].append({"attempt": attempt, "raw_output": None,
                                       "error": exc.code})
            continue
        try:
            record["judgments"] = validate_response(raw, expected)
            record["attempts"].append({"attempt": attempt, "raw_output": raw, "error": None})
            break
        except AutomaticReviewError as exc:
            record["attempts"].append({"attempt": attempt, "raw_output": raw,
                                       "error": exc.code})
            # المحاولة الثانية تحمل رمزَ الرفض، فيصحّح المراجع شكلَ ردّه لا حكمَه.
            user = (raw_file.decode("utf-8") + "\n\n---\nردُّك السابق رُفض بالرمز "
                    f"«{exc.code}». أعد الحكم على كل حالةٍ في الملف، حكمًا واحدًا لكل "
                    "معرّف، بالشكل المطلوب تمامًا.")
    if record["judgments"] is None:
        record["error"] = record["attempts"][-1]["error"]
    record["elapsed_ms"] = int((time.monotonic() - start) * 1000)
    return record


def review_bank(bank_dir: Path, reviewers: list[str], transport: Transport, *,
                brief_path: Path) -> dict:
    families = check_reviewers(reviewers)
    brief = brief_text(brief_path)
    brief_sha = _sha(brief.encode("utf-8"))
    done = skipped = failed = 0
    for path in open_files(bank_dir):
        for model in reviewers:
            relative = path.relative_to(bank_dir / "open").as_posix()
            out = bank_dir / "reviews" / _slug(model) / relative
            if out.exists():
                prior = json.loads(out.read_text(encoding="utf-8"))
                if (prior.get("error") is None
                        and prior.get("file_sha256") == _sha(path.read_bytes())
                        and prior.get("brief_sha256") == brief_sha):
                    skipped += 1
                    continue
            record = review_file(path, bank_dir, model, families[model], transport,
                                 brief=brief, brief_sha=brief_sha)
            _write(out, record)
            if record["error"]:
                failed += 1
            else:
                done += 1
    return {"reviewed": done, "skipped": skipped, "failed": failed}


def cohen_kappa(first: list[str], second: list[str]) -> float | None:
    """κ لكوهين. None حين يكون التوافقُ بالمصادفة كاملًا، بدل القسمة على صفر."""
    if len(first) != len(second) or not first:
        raise AutomaticReviewError("kappa_inputs_invalid")
    n = len(first)
    observed = sum(a == b for a, b in zip(first, second)) / n
    categories = set(first) | set(second)
    expected = sum((first.count(c) / n) * (second.count(c) / n) for c in categories)
    if expected == 1:
        return None
    return round((observed - expected) / (1 - expected), 4)


def _flagged(judgment: dict) -> bool:
    return judgment["reference"] != "correct" or judgment["rubric"] != "sufficient"


def summarize(bank_dir: Path) -> dict:
    """يجمع أحكام كل المراجعين: الاتفاق وκ لكل زوج، وقائمة ما يعرض على المالك."""
    reviews_root = bank_dir / "reviews"
    by_model: dict[str, dict[tuple[str, str], dict]] = {}
    errors = []
    for path in sorted(reviews_root.rglob("*.json")):
        if path.name == "SUMMARY.json":
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("error"):
            errors.append({"model": record["model"], "file": record["file"],
                           "error": record["error"]})
            continue
        table = by_model.setdefault(record["model"], {})
        for judgment in record["judgments"]:
            table[(record["file"], judgment["id"])] = judgment
    models = sorted(by_model)
    pairs = []
    queue: dict[tuple[str, str], dict] = {}
    for a, b in combinations(models, 2):
        shared = sorted(set(by_model[a]) & set(by_model[b]))
        pair = {"reviewers": [a, b], "items": len(shared)}
        for dimension in ("reference", "rubric"):
            left = [by_model[a][k][dimension] for k in shared]
            right = [by_model[b][k][dimension] for k in shared]
            pair[dimension] = {
                "observed_agreement": (round(sum(x == y for x, y in zip(left, right))
                                             / len(shared), 4) if shared else None),
                "kappa": cohen_kappa(left, right) if shared else None,
            }
        pairs.append(pair)
    for model in models:
        for key, judgment in by_model[model].items():
            others = [by_model[m].get(key) for m in models if m != model]
            disagree = any(o is not None and (o["reference"] != judgment["reference"]
                                              or o["rubric"] != judgment["rubric"])
                           for o in others)
            if disagree or _flagged(judgment):
                entry = queue.setdefault(key, {"file": key[0], "id": key[1], "verdicts": {}})
                entry["verdicts"][model] = judgment
    summary = {
        "schema_version": 1, "generated_at": _now(), "reviewers": models,
        "pairs": pairs, "errors": errors,
        "owner_queue": [queue[k] for k in sorted(queue)],
        "measurement_limits": list(MEASUREMENT_LIMITS),
    }
    _write(reviews_root / "SUMMARY.json", summary)
    return summary


# ————— تجربةٌ حيّةٌ صغيرة قبل أن يصل البنك الحقيقي —————

# ثلاث حالاتٍ مصطنعة، في الثانية منها خطأٌ مزروع عمدًا (7×8 = 56 لا 54). فالتجربة لا
# تتحقق من أن النداء يعمل فحسب، بل من أن المراجع يلتقط الخطأ فعلًا ولا يوافق على كل شيء.
SMOKE_PLANTED = "smoke_2"
_SMOKE_CASES = (
    ("smoke_1", "arithmetic", "كم يساوي ٢ + ٢؟ أجب بالرقم وحده.", "4"),
    (SMOKE_PLANTED, "arithmetic", "كم يساوي ٧ × ٨؟ أجب بالرقم وحده.", "54"),
    ("smoke_3", "arabic_plural", "ما جمع التكسير لكلمة «كتاب»؟", "كُتُب"),
)


def smoke_bank(root: Path) -> Path:
    """يبني بنكًا مصطنعًا من ثلاث حالات خارج المستودع، ويعيد مجلّده."""
    bank = root / "smoke_bank"
    suite = {"schema_version": 1, "suite_id": "smoke", "split": "development",
             "description": "تجربة حيّة للمراجعة الخارجية، وفيها خطأٌ مزروع",
             "cases": [{"case_id": cid, "capability": cap,
                        "messages": [{"role": "user", "content": question}],
                        "reference": answer, "rubric": [f"الجواب {answer}"],
                        "checks": [], "critical": False}
                       for cid, cap, question, answer in _SMOKE_CASES]}
    path = bank / "open" / "smoke.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(suite, ensure_ascii=False, indent=2), encoding="utf-8")
    return bank


def smoke(root: Path, reviewers: list[str], transport: Transport, *,
          brief_path: Path) -> dict:
    """يشغّل المراجعين على البنك المصطنع، ويعيد تقريرًا يصلح ملفَّ قياس."""
    bank = smoke_bank(root)
    counts = review_bank(bank, reviewers, transport, brief_path=brief_path)
    summary = summarize(bank)
    per_reviewer = {}
    for model in reviewers:
        record = json.loads((bank / "reviews" / _slug(model) / "smoke.json")
                            .read_text(encoding="utf-8"))
        verdicts = {j["id"]: j["reference"] for j in (record["judgments"] or [])}
        per_reviewer[model] = {
            "family": record["family"], "error": record["error"],
            "attempts": len(record["attempts"]), "elapsed_ms": record["elapsed_ms"],
            "caught_planted_error": (verdicts.get(SMOKE_PLANTED) == "incorrect"
                                     if verdicts else None),
            "false_flags": sorted(i for i, v in verdicts.items()
                                  if i != SMOKE_PLANTED and v != "correct"),
        }
    passed = all(r["error"] is None and r["caught_planted_error"] and not r["false_flags"]
                 for r in per_reviewer.values())
    return {"schema_version": 1, "probe": "external_review_smoke",
            "generated_at": _now(), "status": "passed" if passed else "failed",
            "reviewers": per_reviewer, "counts": counts, "pairs": summary["pairs"],
            "planted_error": {"case": SMOKE_PLANTED, "reference_given": "54",
                              "correct": "56"},
            "measurement_limits": list(MEASUREMENT_LIMITS) + [
                "three_synthetic_cases_prove_the_path_not_reviewer_quality"]}
