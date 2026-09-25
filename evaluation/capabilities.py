"""تشخيص قدرات عربية خام؛ الفحوص الآلية محدودة ولا تمنح جاهزية نشر.

البنك development مكشوف الأجوبة. تُرسل رسائل الحالة فقط إلى النموذج،
ويظل المرجع والمسرد التقييمي والفحوص خارج الطلب. كل نداء عبر core.execute.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import uuid
from contextlib import contextmanager
from decimal import Decimal, DecimalException
from pathlib import Path

from core import filelock
from core.budget import Budget, BudgetRefused
from core.canonical import PayloadRejected
from core.contracts import Message, Request
from core.quoted import quarantine_quoted
from core.ledger import Ledger, LedgerCorrupt
from core.run import RouteRefused, execute
from core.sandbox import declared_host, run_in_sandbox, sandbox_configuration

SYSTEM = ("أنت مساعد عربي عام. افهم طلب المستخدم وسياق الحوار وأجب بالعربية "
          "ما لم يطلب المستخدم لغة أخرى. أجب بوضوح ودقة والتزم بالقيود التي "
          "يطلبها. صرّح بما لا تعرفه ولا تختلق "
          "معلومات أو مصادر.")
_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
_ROOT_FIELDS = {"schema_version", "suite_id", "split", "description", "cases"}
_CASE_FIELDS = {"case_id", "capability", "messages", "reference", "rubric",
                "checks", "critical"}


class CapabilityError(PayloadRejected):
    """فشل تشخيص مسمى؛ لا يُخفى كحكم على جودة النموذج."""


def _reject(path: str, code: str, reason: str):
    raise CapabilityError(path, code, reason)


def _fields(value, fields: set[str], path: str):
    if not isinstance(value, dict):
        _reject(path, "object_required", "كائن مطلوب")
    if set(value) != fields:
        _reject(path, "schema_fields", "حقول غائبة أو غير معروفة")


def _text(value, path: str):
    if not isinstance(value, str) or not value.strip():
        _reject(path, "text_required", "نص غير فارغ مطلوب")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _reject(path, "invalid_unicode", "محرف لا يُرمز UTF-8")


def _identifier(value, path: str):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _reject(path, "identifier_invalid", "معرف ASCII قصير بلا مسارات مطلوب")


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("json", "duplicate_json_key", "مفتاح JSON مكرر")
        result[key] = value
    return result


def _parse_json(text: str, *, exact_numbers: bool = False):
    def invalid_constant(_):
        _reject("json", "json_nonfinite", "عدد JSON غير منتهٍ")

    def reference_float(token):
        value = float(token)
        try:
            precise = Decimal(token) == Decimal(str(value))
        except DecimalException:
            precise = False
        if not precise:
            _reject("json", "json_number_precision", "عدد مرجعي يفقد دقته؛ لا تقريب صامت")
        return value

    return json.loads(text, object_pairs_hook=_pairs,
                      parse_float=Decimal if exact_numbers else reference_float,
                      parse_constant=invalid_constant)


def _json_equal(actual, expected) -> bool:
    """مساواة JSON بنيوية؛ الأعداد متكافئة القيمة وbool نوع مستقل."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    numbers = (int, float, Decimal)
    if isinstance(actual, numbers) and isinstance(expected, numbers):
        return Decimal(str(actual)) == Decimal(str(expected))
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _json_equal(actual[k], expected[k]) for k in expected)
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _json_equal(a, e) for a, e in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def validate_suite(suite: dict) -> dict:
    _fields(suite, _ROOT_FIELDS, "suite")
    if type(suite["schema_version"]) is not int or suite["schema_version"] != 1:
        _reject("suite.schema_version", "schema_version_invalid", "النسخة 1 مطلوبة")
    _identifier(suite["suite_id"], "suite.suite_id")
    if suite["split"] != "development":
        _reject("suite.split", "development_only", "الأجوبة مكشوفة؛ لا بنك قبول محجوب")
    _text(suite["description"], "suite.description")
    cases = suite["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
        _reject("suite.cases", "cases_invalid", "من 1 إلى 100 حالة مطلوبة")
    seen = set()
    for i, case in enumerate(cases):
        path = f"suite.cases[{i}]"
        _fields(case, _CASE_FIELDS, path)
        _identifier(case["case_id"], path + ".case_id")
        if case["case_id"] in seen:
            _reject(path, "case_id_duplicate", "معرف حالة مكرر")
        seen.add(case["case_id"])
        for field in ("capability", "reference"):
            _text(case[field], path + "." + field)
        if type(case["critical"]) is not bool:
            _reject(path + ".critical", "critical_type", "قيمة منطقية مطلوبة")
        if not isinstance(case["rubric"], list) or not case["rubric"]:
            _reject(path + ".rubric", "rubric_invalid", "معايير بشرية غير فارغة مطلوبة")
        for criterion in case["rubric"]:
            _text(criterion, path + ".rubric")
        messages = case["messages"]
        if not isinstance(messages, list) or not messages:
            _reject(path + ".messages", "messages_invalid", "حوار غير فارغ مطلوب")
        for msg in messages:
            _fields(msg, {"role", "content"}, path + ".messages")
            if msg["role"] not in ("user", "assistant"):
                _reject(path + ".messages.role", "role_invalid", "دور المستخدم أو المساعد فقط")
            _text(msg["content"], path + ".messages.content")
        if messages[0]["role"] != "user" or messages[-1]["role"] != "user":
            _reject(path + ".messages", "dialogue_invalid", "الحوار يبدأ وينتهي بطلب مستخدم")
        if not isinstance(case["checks"], list):
            _reject(path + ".checks", "checks_invalid", "قائمة فحوص مطلوبة ولو فارغة")
        for check in case["checks"]:
            _fields(check, {"kind", "value"}, path + ".checks")
            if check["kind"] not in ("contains", "excludes", "exact", "json_equals", "python_sandbox"):
                _reject(path + ".checks", "check_kind_invalid", "نوع فحص غير معروف")
            if check["kind"] == "json_equals":
                # الكائنُ والقائمة كلاهما JSON صالح، و_json_equal يقارن الاثنين.
                # وكان الاشتراطُ على الكائن وحده يجعل المدقِّق أصرمَ من المقارِن
                # الذي يخدمه، فيُرَدّ استخراجٌ مرجعُه قائمةٌ — وهو أكثرُ ما يُطلب
                # في الاستخراج — بلا شرطٍ في التكليف يسنده.
                if not isinstance(check["value"], (dict, list)):
                    _reject(path + ".checks", "json_container_required",
                            "مرجع JSON كائنٌ أو قائمة")
            else:
                _text(check["value"], path + ".checks.value")
    try:
        _json_bytes(suite)
    except (TypeError, ValueError, UnicodeEncodeError):
        _reject("suite", "json_value_invalid", "قيم غير صالحة لـJSON UTF-8")
    return suite


def load_suite(path: Path | str) -> dict:
    try:
        suite = _parse_json(Path(path).read_text(encoding="utf-8"))
    except CapabilityError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        _reject("suite", "suite_unreadable", "تعذرت قراءة بنك JSON UTF-8")
    return validate_suite(suite)


def _checks(answer: str, checks: list[dict]) -> list[dict]:
    results = []
    for check in checks:
        kind, value = check["kind"], check["value"]
        if kind == "contains":
            passed = value in answer
            results.append({"kind": kind, "value": value, "passed": passed})
        elif kind == "excludes":
            passed = value not in answer
            results.append({"kind": kind, "value": value, "passed": passed})
        elif kind == "exact":
            passed = answer == value
            results.append({"kind": kind, "value": value, "passed": passed})
        elif kind == "json_equals":
            try:
                parsed = _parse_json(answer, exact_numbers=True)
                # الجوابُ وعاءٌ كالمرجع: كائنٌ أو قائمة. وكان يُشترط فيه الكائنُ
                # وحده بعدما رُفع الشرطُ عن المرجع، فصارت كلُّ حالةٍ مرجعُها قائمةٌ
                # رسوبًا مضمونًا — وحالاتُ الاستخراج تطلب المصفوفة صراحةً. ولا
                # يُسجَّل ذلك خطأً بل «جوابًا خاطئًا»، فيخصم من كل محرّكٍ بلا ذنب.
                passed = isinstance(parsed, (dict, list)) and _json_equal(parsed, value)
            except (ValueError, TypeError, UnicodeError, DecimalException):
                passed = False
            results.append({"kind": kind, "value": value, "passed": passed})
        elif kind == "python_sandbox":
            res = run_in_sandbox(answer, value)
            if res.error_code and res.error_code.startswith("execution_"):
                # لا يُحسب «رسوبًا»: لم يُقَس. فالحالة تُعلَن خطأَ تشغيل،
                # ويسقط اكتمالُ الجمع كلُّه — «تعذّر التحقق» ليس «فشل التحقق».
                _reject("checks", "sandbox_" + res.error_code.removeprefix("execution_"),
                        "تعذر إثبات تنفيذ الفحص داخل حاوية موثوقة: " + res.error_code)
            entry = {
                "kind": kind,
                "value": value,
                "passed": res.passed,
                "witness_digest": res.witness_digest,
                "exit_code": res.exit_code,
                "elapsed_ms": res.elapsed_ms,
                "boundary": res.boundary,
            }
            if res.error_code:
                entry["error_code"] = res.error_code
            results.append(entry)
    return results


def _safe_file(path: Path):
    """لا نفتح رابطًا رمزيًا أو صلبًا كملف تشغيل مملوك للتشخيص."""
    if path.is_symlink():
        _reject("output", "unsafe_output_path", "رابط رمزي مرفوض")
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _reject("output", "unsafe_output_path", "ملف غير عادي أو متعدد الروابط")


def _write_new_json(path: Path, value):
    payload = _json_bytes(value) + b"\n"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        _reject("output", "output_write_failed", "تعذرت كتابة ملف جديد دون استبدال")


def _replace_state(path: Path, value):
    _safe_file(path)
    temporary = path.with_name(f"state-{uuid.uuid4().hex}.tmp")
    _write_new_json(temporary, value)
    os.replace(temporary, path)


@contextmanager
def _run_lock(run_dir: Path):
    path = run_dir / "run.lock"
    _safe_file(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as stream:
        if os.fstat(stream.fileno()).st_nlink != 1:
            _reject("run", "unsafe_output_path", "قفل متعدد الروابط")
        try:
            filelock.lock(stream, blocking=False)
        except BlockingIOError:
            _reject("run", "run_busy", "تشغيل آخر يملك هذا السجل")
        try:
            yield
        finally:
            filelock.unlock(stream)


def evaluate_suite(suite: dict, provider, run_root: Path, *, run_id: str | None = None,
                   max_output: int = 800, deadline_s: int = 240,
                   model_version: str = "unspecified",
                   quarantine_quoted_material: bool = False) -> dict:
    """يجمع التشخيص؛ فشل فحص آلي ليس عطل تشغيل ولا ترخيص نشر."""
    validate_suite(suite)
    _text(provider.model, "runtime.model")
    _text(model_version, "runtime.model_version")
    for name, value, ceiling in (("max_output", max_output, 8192),
                                  ("deadline_s", deadline_s, 3600)):
        if type(value) is not int or not 1 <= value <= ceiling:
            _reject("config." + name, "config_invalid", "عدد صحيح موجب ضمن السقف مطلوب")
    if type(quarantine_quoted_material) is not bool:
        _reject("config.quarantine_quoted_material", "config_invalid",
                "علمٌ منطقيّ مطلوب — لا عددًا ولا نصًّا")
    config = {"runner_version": 4, "suite_sha256": _sha(suite),
              "system_sha256": _sha(SYSTEM), "model_sha256": _sha(provider.model),
              "model_version_sha256": _sha(model_version),
              "max_output": max_output, "deadline_s": deadline_s,
              "data_policy": "local_only", "budget_micros": 0,
              # يدخل البصمة: تشغيلان بإعدادين مختلفين لا يتبادلان إعادة العرض
              "quarantine_quoted_material": quarantine_quoted_material,
              # هوية الإعداد الموثوق تدخل البصمة؛ أعلام البيئة لا تمنح حدًا.
              "execution_host": declared_host(),
              "execution_backend": sandbox_configuration()}
    config_hash = _sha(config)
    run_id = run_id or "run-" + config_hash[:24]
    _identifier(run_id, "run_id")
    run_root = Path(run_root).absolute()
    if any(p.is_symlink() for p in (run_root, *run_root.parents)):
        _reject("output", "unsafe_output_path", "مجلد تشغيل عبر رابط رمزي")
    run_root.mkdir(parents=True, exist_ok=True)
    run_dir = run_root / run_id
    manifest = {"run_id": run_id, "suite_id": suite["suite_id"],
                "config_sha256": config_hash, "config": config,
                "dialogue_sha256": {c["case_id"]: _sha(c["messages"])
                                    for c in suite["cases"]}}
    run_dir.mkdir(exist_ok=True)
    if run_dir.is_symlink() or not run_dir.is_dir():
        _reject("output", "unsafe_output_path", "مجلد تشغيل غير صالح")
    with _run_lock(run_dir):
        return _evaluate_locked(suite, provider, run_dir, manifest,
                                max_output, deadline_s, model_version,
                                quarantine_quoted_material)


def _evaluate_locked(suite, provider, run_dir, manifest, max_output,
                     deadline_s, model_version,
                     quarantine_quoted_material=False):
    run_id, config_hash = manifest["run_id"], manifest["config_sha256"]
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        _safe_file(manifest_path)
        try:
            previous = _parse_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            _reject("run", "run_manifest_invalid", "مجلد موجود بلا بيان تشغيل صالح")
        if previous != manifest:
            _reject("run", "run_config_conflict", "معرف التشغيل نفسه لإعداد مختلف")
    else:
        if any(p.name != "run.lock" for p in run_dir.iterdir()):
            _reject("run", "run_manifest_invalid", "مجلد موجود بلا بيان تشغيل")
        _write_new_json(manifest_path, manifest)
    ledger_path = run_dir / "calls.jsonl"
    _safe_file(ledger_path)
    ledger = Ledger(ledger_path)
    _safe_file(ledger.anchor_path)
    anchor_tmp = ledger.anchor_path.with_suffix(".tmp")
    if anchor_tmp.exists() or anchor_tmp.is_symlink():
        _reject("output", "unsafe_output_path", "ملف مؤقت موجود؛ لا يُستبدل")
    try:
        ledger.verify_chain(strict=ledger.anchor_path.exists())
    except (LedgerCorrupt, ValueError, KeyError, TypeError):
        _reject("run.ledger", "run_ledger_corrupt", "السجل غير سليم؛ لا استهلاك عرضًا")
    state_path = run_dir / "state.json"
    _safe_file(state_path)
    if state_path.exists():
        try:
            state = _parse_json(state_path.read_text(encoding="utf-8"))
            _fields(state, {"phase", "config_sha256", "head", "count"}, "run.state")
            if (state["config_sha256"] != config_hash
                    or state["phase"] not in ("running", "sealed")
                    or type(state["count"]) is not int or state["count"] < 0
                    or not isinstance(state["head"], str)):
                raise ValueError("invalid state")
        except (OSError, ValueError, UnicodeError):
            _reject("run", "run_state_invalid", "حالة التشغيل غير صالحة")
        entries = ledger.entries()
        count = state["count"]
        prefix_head = entries[count - 1]["digest"] if 0 < count <= len(entries) else "0" * 64
        if count > len(entries) or prefix_head != state["head"]:
            _reject("run", "run_checkpoint_mismatch", "السجل لا يطابق نقطة الاستئناف")
        if state["phase"] == "sealed" and (
                len(entries) != count or not ledger.anchor_path.exists()
                or ledger.read_anchor() != {"count": count, "head": state["head"]}):
            _reject("run", "run_checkpoint_mismatch", "ذيل غير متوقع بعد ختم التشغيل")
    elif ledger.count() or ledger.anchor_path.exists():
        _reject("run", "run_state_missing", "سجل سابق بلا حالة استئناف؛ لا يُعتمد تلقائيًا")
    _replace_state(state_path, {"phase": "running", "config_sha256": config_hash,
                                "head": ledger.head(), "count": ledger.count()})
    budget = Budget(0, 0)
    results = []
    for case in suite["cases"]:
        quarantined: list[str] = []

        def _content(message):
            if not quarantine_quoted_material or message["role"] != "user":
                return message["content"]
            held = quarantine_quoted(message["content"])
            quarantined.extend(f.code for f in held.findings)
            return held.text

        request = Request(
            messages=(Message("system", SYSTEM),) + tuple(
                Message(m["role"], _content(m)) for m in case["messages"]),
            model=provider.model, model_version=model_version, max_output=max_output,
            deadline_s=deadline_s, data_policy="local_only",
            idempotency_key=f"cap-{run_id}-{config_hash[:24]}-{case['case_id']}")
        result = {"case_id": case["case_id"], "capability": case["capability"],
                  "critical": case["critical"], "status": "error", "answer": None,
                  "error_code": None, "checks": [], "automatic_pass": False,
                  "checks_status": "not_run",
                  "manual_review": "not_required_q49", "reference": case["reference"],
                  "rubric": case["rubric"], "replayed": False,
                  "usage": None, "cost_micros": 0,
                  "quarantined_directives": quarantined}
        start = time.monotonic_ns()
        try:
            outcome = execute(request, provider, budget, ledger)
            result["replayed"] = outcome.replayed
            record = next(e["record"] for e in ledger.entries()
                          if e["digest"] == outcome.ledger_digest)
            result["cost_micros"] = record.get("settled_micros", 0)
            response = outcome.response
            if response is None:
                result["error_code"] = outcome.error_code or "response_missing"
            else:
                result["answer"] = response.content
                result["usage"] = {"input_tokens": response.usage.input_tokens,
                                   "output_tokens": response.usage.output_tokens}
                result["stop_reason"] = response.stop_reason
                if response.stop_reason == "complete":
                    result["checks"] = _checks(response.content, case["checks"])
                    result["checks_status"] = "evaluated" if result["checks"] else "not_evaluated"
                    result["automatic_pass"] = (all(c["passed"] for c in result["checks"])
                                                if result["checks"] else None)
                    result["status"] = "complete"
                else:
                    result["status"] = "truncated" if response.stop_reason == "max_output" else "error"
                    result["error_code"] = "response_" + response.stop_reason
        except (RouteRefused, BudgetRefused, PayloadRejected) as exc:
            result["error_code"] = exc.code
            result.update(status="error", automatic_pass=False,
                          checks=[], checks_status="not_run")
        except Exception:
            result["error_code"] = "execution_failed"
            result.update(status="error", automatic_pass=False,
                          checks=[], checks_status="not_run")
        result["elapsed_ms"] = (time.monotonic_ns() - start) // 1_000_000
        results.append(result)
    errors = sum(r["status"] != "complete" for r in results)
    summary = {"cases": len(results), "collection_complete": errors == 0,
               "execution_errors": errors,
               "automatic_passes": sum(r["automatic_pass"] is True for r in results),
               "automatic_failures": sum(r["status"] == "complete" and r["automatic_pass"] is False
                                         for r in results),
               "without_checks": sum(r["automatic_pass"] is None for r in results),
               "manual_review": "not_required_q49", "release_ready": False}
    report = {"schema_version": 1, "suite_id": suite["suite_id"], "run_id": run_id,
              "metadata": {"runtime_model": provider.model,
                           "runtime_model_version": model_version, "manifest": manifest,
                           "elapsed_semantics": "wall_elapsed_including_replay_not_generation_throughput"},
              "results": results, "summary": summary,
              "manual_review": "not_required_q49", "release_ready": False}
    ledger.anchor()
    _write_new_json(run_dir / f"report-{uuid.uuid4().hex}.json", report)
    _replace_state(state_path, {"phase": "sealed", "config_sha256": config_hash,
                                "head": ledger.head(), "count": ledger.count()})
    return report
