"""مراجعة محلية صريحة؛ الإقرار بهوية المراجع ليس توثيقًا لهويته.

لا يستدعي هذا المسار نموذجًا، ولا يغير التقرير الخام أو يمنح جاهزية نشر.
البصمات تربط القرار بنسخة التقرير المعطاة؛ ليست توقيعًا موثوقًا على التقرير.
"""
from __future__ import annotations

import html
import json
import os
import re
import secrets
import stat
from pathlib import Path

from core.canonical import PayloadRejected
from evaluation.capabilities import _json_bytes, _parse_json, _sha, validate_suite

_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_REPORT = {"schema_version", "suite_id", "run_id", "metadata", "results",
           "summary", "manual_review", "release_ready"}
_RESULT = {"case_id", "capability", "critical", "status", "answer", "error_code",
           "checks", "automatic_pass", "checks_status", "manual_review",
           "reference", "rubric", "replayed", "usage", "cost_micros", "elapsed_ms"}
_REVIEW = {"schema_version", "kind", "suite_id", "run_id", "suite_sha256",
           "config_sha256", "report_sha256", "reviewer", "decisions", "release_ready"}
_DECISION = {"case_id", "answer_sha256", "verdict", "reason", "severity", "review_seconds"}
# يحفظ الإصدار ٣ المضيف المعلن تاريخيًا؛ الإصدار ٤ يربط الصورة والحاوية.
_SANDBOX_CHECK_FIELDS = frozenset({"witness_digest", "exit_code", "elapsed_ms", "boundary"})


class ReviewError(PayloadRejected):
    """رفض مسمى للمراجعة أو ارتباطها بالتقرير."""


def _reject(path, code, reason):
    raise ReviewError(path, code, reason)


def _fields(value, required, path, optional=frozenset()):
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        _reject(path, "schema_fields", "كائن بحقوله المعلنة فقط مطلوب")


def _text(value, path, *, blank=False):
    if not isinstance(value, str) or (not blank and not value.strip()):
        _reject(path, "text_required", "نص غير فارغ مطلوب")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _reject(path, "invalid_unicode", "نص UTF-8 صالح مطلوب")


def _int(value, path, low=0, high=2**53 - 1):
    if type(value) is not int or not low <= value <= high:
        _reject(path, "integer_invalid", "عدد صحيح ضمن الحدود مطلوب")


def _id(value, path):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _reject(path, "identifier_invalid", "معرف قصير بلا مسارات مطلوب")


def _hash(value, path):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        _reject(path, "hash_invalid", "بصمة SHA256 مطلوبة")


def load_json(path: Path | str):
    """JSON بلا مفاتيح مكررة أو أعداد غير منتهية."""
    try:
        return _parse_json(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        _reject("input", getattr(exc, "code", "json_unreadable"), "تعذرت قراءة JSON صالح")


def validate_report(report: dict) -> dict:
    """يتحقق من عقد تقرير م٨-أ؛ لا يثبت أن الملف أصيل أو أن الإجابة صحيحة."""
    _fields(report, _REPORT, "report")
    _int(report["schema_version"], "report.schema_version", 1, 1)
    for key in ("suite_id", "run_id"):
        _id(report[key], "report." + key)
    if report["manual_review"] not in ("pending", "not_required_q49") or report["release_ready"] is not False:
        _reject("report", "readiness_invalid", "تقرير خام بلا قبول بشري أو جاهزية مطلوب")
    metadata = report["metadata"]
    _fields(metadata, {"runtime_model", "runtime_model_version", "manifest", "elapsed_semantics"}, "metadata")
    for key in ("runtime_model", "runtime_model_version", "elapsed_semantics"):
        _text(metadata[key], "metadata." + key)
    manifest = metadata["manifest"]
    _fields(manifest, {"run_id", "suite_id", "config_sha256", "config", "dialogue_sha256"}, "manifest")
    if any(manifest[key] != report[key] for key in ("suite_id", "run_id")):
        _reject("manifest", "report_binding_mismatch", "هوية التقرير لا تطابق البيان")
    config = manifest["config"]
    _fields(config, {"runner_version", "suite_sha256", "system_sha256", "model_sha256",
                     "model_version_sha256", "max_output", "deadline_s", "data_policy",
                     "budget_micros"}, "config",
            {"quarantine_quoted_material", "execution_host", "execution_backend"})
    # النسخة ٢ أضافت علم حَجر الأوامر المقتبسة؛ والنسخة ١ سابقةٌ له فتُقبل
    # بلا العلم حتى لا تُبطَل تشغيلاتٌ مدفوعة قبله. والنسخة ٣ أضافت المضيفَ
    # الزائل المُعلَن (ق٤٤) — يُقبل null صراحةً: يعني «لم يُعلَن مضيف».
    _int(config["runner_version"], "config.runner_version", 1, 4)
    if config["runner_version"] >= 2:
        if type(config.get("quarantine_quoted_material")) is not bool:
            _reject("config.quarantine_quoted_material", "schema_fields",
                    "علمٌ منطقيّ مطلوب")
    if config["runner_version"] >= 3:
        if "execution_host" not in config:
            _reject("config.execution_host", "schema_fields", "حقلُ المضيف مطلوب")
        if config["execution_host"] is not None:
            _text(config["execution_host"], "config.execution_host")
    elif "execution_host" in config:
        _reject("config.execution_host", "schema_fields",
                "المضيف لا يُذكر في نسخةٍ سابقة لق٤٤")
    if config["runner_version"] == 4:
        if "execution_backend" not in config:
            _reject("config.execution_backend", "schema_fields", "تهيئة منفذ التنفيذ مطلوبة ولو null")
        backend = config["execution_backend"]
        if backend is not None:
            _fields(backend, {"backend", "image_id", "lock_sha256", "python_version", "snapshot_files"},
                    "config.execution_backend")
            if (backend["backend"] != "docker"
                    or not isinstance(backend["image_id"], str)
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", backend["image_id"])
                    or not isinstance(backend["python_version"], str)
                    or not re.fullmatch(r"3\.(?:1[2-9]|[2-9][0-9])\.[0-9]+", backend["python_version"])
                    or backend["snapshot_files"] != []):
                _reject("config.execution_backend", "sandbox_configuration_invalid",
                        "صورة ثابتة ونسخة Python ولقطة فارغة مطلوبة للتقييم")
            _hash(backend["lock_sha256"], "config.execution_backend.lock_sha256")
        expected_host = None if backend is None else "docker:" + backend["image_id"]
        if config["execution_host"] != expected_host:
            _reject("config.execution_host", "sandbox_configuration_mismatch",
                    "هوية الصورة لا تطابق تهيئة المنفذ")
    elif "execution_backend" in config:
        _reject("config.execution_backend", "schema_fields", "تهيئة الحاوية تخص نسخة المشغل ٤")
    for key in ("suite_sha256", "system_sha256", "model_sha256", "model_version_sha256"):
        _hash(config[key], "config." + key)
    _int(config["max_output"], "config.max_output", 1, 8192)
    _int(config["deadline_s"], "config.deadline_s", 1, 3600)
    _int(config["budget_micros"], "config.budget_micros", 0, 0)
    if config["data_policy"] != "local_only":
        _reject("config", "data_policy_invalid", "التشخيص محلي فقط")
    if (manifest["config_sha256"] != _sha(config)
            or config["model_sha256"] != _sha(metadata["runtime_model"])
            or config["model_version_sha256"] != _sha(metadata["runtime_model_version"])):
        _reject("config", "config_hash_mismatch", "الإعداد لا يطابق بصمته")
    dialogues = manifest["dialogue_sha256"]
    if not isinstance(dialogues, dict) or not dialogues:
        _reject("manifest.dialogue_sha256", "dialogues_invalid", "بصمات حوار غير فارغة مطلوبة")
    for case_id, digest in dialogues.items():
        _id(case_id, "manifest.dialogue_sha256")
        _hash(digest, "manifest.dialogue_sha256")
    results = report["results"]
    if not isinstance(results, list) or not 1 <= len(results) <= 100:
        _reject("report.results", "results_invalid", "من 1 إلى 100 نتيجة مطلوبة")
    seen = set()
    for result in results:
        _fields(result, _RESULT, "result", {"stop_reason", "quarantined_directives"})
        held = result.get("quarantined_directives", [])
        if not isinstance(held, list) or not all(isinstance(c, str) for c in held):
            _reject("result.quarantined_directives", "schema_fields",
                    "قائمةُ رموزٍ نصّية مطلوبة ولو فارغة")
        _id(result["case_id"], "result.case_id")
        if result["case_id"] in seen:
            _reject("result.case_id", "duplicate_case", "معرف حالة مكرر")
        seen.add(result["case_id"])
        for key in ("capability", "reference"):
            _text(result[key], "result." + key)
        if not isinstance(result["rubric"], list) or not result["rubric"]:
            _reject("result.rubric", "rubric_invalid", "معايير غير فارغة مطلوبة")
        for item in result["rubric"]:
            _text(item, "result.rubric")
        for key in ("critical", "replayed"):
            if type(result[key]) is not bool:
                _reject("result." + key, "boolean_required", "قيمة منطقية مطلوبة")
        if result["manual_review"] != report["manual_review"]:
            _reject("result", "readiness_invalid", "نتيجة خام مطلوبة")
        if result["status"] not in ("complete", "error", "truncated"):
            _reject("result.status", "status_invalid", "حالة تنفيذ غير معروفة")
        if result["answer"] is not None:
            _text(result["answer"], "result.answer", blank=True)
        if result["error_code"] is not None:
            _text(result["error_code"], "result.error_code")
        if "stop_reason" in result and result["stop_reason"] not in ("complete", "max_output", "error", "deadline", "refused"):
            _reject("result.stop_reason", "status_invalid", "سبب توقف غير معروف")
        for key in ("cost_micros", "elapsed_ms"):
            _int(result[key], "result." + key)
        if result["usage"] is not None:
            _fields(result["usage"], {"input_tokens", "output_tokens"}, "result.usage")
            for key, value in result["usage"].items():
                _int(value, "result.usage." + key)
        checks = result["checks"]
        if not isinstance(checks, list):
            _reject("result.checks", "checks_invalid", "قائمة فحوص مطلوبة")
        for check in checks:
            _fields(check, {"kind", "value", "passed"}, "result.check",
                    _SANDBOX_CHECK_FIELDS | {"error_code"})
            if (check["kind"] not in ("contains", "excludes", "exact", "json_equals",
                                      "python_sandbox")
                    or type(check["passed"]) is not bool):
                _reject("result.check", "checks_invalid", "فحص جزئي صالح مطلوب")
            if check["kind"] == "json_equals":
                # وعاءٌ لا كائنٌ وحده — مطابقًا لما يقبله validate_suite و_checks
                if not isinstance(check["value"], (dict, list)):
                    _reject("result.check.value", "checks_invalid", "وعاء JSON مطلوب")
            else:
                _text(check["value"], "result.check.value")
            if check["kind"] == "python_sandbox":
                # القراءة التاريخية لا تحول إعلان الإصدار ٣ إلى إثبات عزل.
                if not _SANDBOX_CHECK_FIELDS <= check.keys():
                    _reject("result.check", "checks_invalid",
                            "فحص تنفيذ الكود يلزمه شاهدٌ ومضيفٌ مُعلَنان [ق٤٤]")
                _hash(check["witness_digest"], "result.check.witness_digest")
                for key in ("exit_code", "elapsed_ms"):
                    if type(check[key]) is not int:
                        _reject("result.check." + key, "checks_invalid", "عدد صحيح مطلوب")
                if config["runner_version"] == 4:
                    _int(check["exit_code"], "result.check.exit_code", 0, 255)
                    _int(check["elapsed_ms"], "result.check.elapsed_ms")
                    # الحكمُ حكمُ المدقّق لا رمزُ الخروج (ك١٣): النجاحُ بلا رمز خطأ أيًّا كان
                    # الخروج، والرسوبُ برمزٍ يسمّي سببه: `verdict_missing` مع خروجٍ صفر،
                    # و`exit_<n>` مع خروجٍ غير صفر. وما عدا ذلك تقريرٌ متعارض.
                    expected_error = (None if check["passed"]
                                      else "verdict_missing" if check["exit_code"] == 0
                                      else f"exit_{check['exit_code']}")
                    if check.get("error_code") != expected_error:
                        _reject("result.check", "sandbox_result_inconsistent", "الخروج ونتيجة الفحص متعارضان")
                    backend = config["execution_backend"]
                    expected_prefix = None if backend is None else "docker:" + backend["image_id"] + "@"
                    boundary = check["boundary"]
                    if (expected_prefix is None or not isinstance(boundary, str)
                            or not boundary.startswith(expected_prefix)
                            or not _HASH.fullmatch(boundary[len(expected_prefix):])):
                        _reject("result.check.boundary", "sandbox_boundary_mismatch",
                                "حاوية موثقة الهوية وصورة تطابق إعداد التشغيل مطلوبتان")
                elif check["boundary"] is None or check["boundary"] != config.get("execution_host"):
                    _reject("result.check.boundary", "sandbox_boundary_undeclared",
                            "مضيفُ التنفيذ غائبٌ أو يخالف بصمةَ الإعداد [ق٤٤]")
        if result["status"] == "complete":
            expected_pass = all(c["passed"] for c in checks) if checks else None
            if (result["answer"] is None or result["error_code"] is not None
                    or result.get("stop_reason") != "complete"
                    or result["automatic_pass"] is not expected_pass
                    or result["checks_status"] != ("evaluated" if checks else "not_evaluated")):
                _reject("result", "result_inconsistent", "نتيجة التنفيذ الكامل غير متسقة")
        elif (result["automatic_pass"] is not False or checks
              or result["checks_status"] != "not_run" or result["error_code"] is None):
            _reject("result", "result_inconsistent", "نتيجة الخطأ لا يجوز أن تجتاز الفحوص")
    if seen != dialogues.keys():
        _reject("results", "report_binding_mismatch", "الحالات لا تطابق بيان التشغيل")
    errors = sum(r["status"] != "complete" for r in results)
    expected = {"cases": len(results), "collection_complete": errors == 0,
                "execution_errors": errors,
                "automatic_passes": sum(r["automatic_pass"] is True for r in results),
                "automatic_failures": sum(r["status"] == "complete" and r["automatic_pass"] is False for r in results),
                "without_checks": sum(r["automatic_pass"] is None for r in results),
                "manual_review": report["manual_review"], "release_ready": False}
    # JSON encoding keeps bool distinct from int; ordinary dict equality does not.
    try:
        if _json_bytes(report["summary"]) != _json_bytes(expected):
            _reject("report.summary", "summary_inconsistent", "ملخص لا يطابق النتائج")
        _json_bytes(report)
    except (TypeError, UnicodeError, ValueError) as exc:
        if isinstance(exc, ReviewError):
            raise
        _reject("report", "json_invalid", "تقرير غير قابل للبصم")
    return report


def review_binding(report: dict) -> dict:
    validate_report(report)
    manifest = report["metadata"]["manifest"]
    return {"schema_version": 1, "kind": "diwan-human-review",
            "suite_id": report["suite_id"], "run_id": report["run_id"],
            "suite_sha256": manifest["config"]["suite_sha256"],
            "config_sha256": manifest["config_sha256"], "report_sha256": _sha(report),
            "release_ready": False}


def validate_review(report: dict, review: dict) -> dict:
    binding = review_binding(report)
    # التوقيعُ حقلٌ اختياريّ هنا عمدًا: هذه الدالّة عقدٌ بنيويّ لا تُثبت
    # هوية. والتحقّقُ منه في evaluation/verdict_signature.py::certify_review،
    # وهو الطريقُ الوحيد إلى reviewer_identity_verified: True (ق٤٥).
    _fields(review, _REVIEW, "review", {"owner_signature"})
    for key, expected in binding.items():
        if type(review[key]) is not type(expected) or review[key] != expected:
            _reject("review." + key, "review_binding_mismatch", "القرار لا يطابق التقرير المحدد")
    _fields(review["reviewer"], {"identity", "attestation"}, "review.reviewer")
    _text(review["reviewer"]["identity"], "review.reviewer.identity")
    if review["reviewer"]["attestation"] is not True:
        _reject("review.reviewer", "attestation_required", "إقرار المراجع الصريح مطلوب")
    decisions = review["decisions"]
    results = {r["case_id"]: r for r in report["results"]}
    if not isinstance(decisions, list) or not 1 <= len(decisions) <= len(results):
        _reject("review.decisions", "decisions_invalid", "قرار واحد على الأقل دون تجاوز عدد الحالات")
    seen = set()
    for decision in decisions:
        _fields(decision, _DECISION, "decision")
        case_id = decision["case_id"]
        _id(case_id, "decision.case_id")
        if case_id not in results:
            _reject("decision.case_id", "unknown_case", "حالة غير موجودة في التقرير")
        if case_id in seen:
            _reject("decision.case_id", "duplicate_case", "قرار مكرر للحالة")
        seen.add(case_id)
        if decision["answer_sha256"] != _sha(results[case_id]["answer"]):
            _reject("decision.answer_sha256", "answer_hash_mismatch", "الجواب تغير بعد إعداد المراجعة")
        if decision["verdict"] not in ("pass", "fail", "unsure"):
            _reject("decision.verdict", "verdict_invalid", "نجاح أو إخفاق أو غير محسوم مطلوب")
        _text(decision["reason"], "decision.reason")
        _int(decision["review_seconds"], "decision.review_seconds", 1)
        if decision["verdict"] == "fail":
            if decision["severity"] not in ("critical", "material", "minor"):
                _reject("decision.severity", "severity_invalid", "درجة الخطأ مطلوبة عند الإخفاق")
        elif decision["severity"] is not None:
            _reject("decision.severity", "severity_invalid", "لا تُسند درجة خطأ إلى النجاح أو عدم الحسم")
        if decision["verdict"] == "pass" and results[case_id]["status"] != "complete":
            _reject("decision.verdict", "incomplete_cannot_pass", "إجابة مبتورة أو فشل تنفيذ لا يجتازان المراجعة")
    return review


def summarize_review(report: dict, review: dict) -> dict:
    validate_review(report, review)
    decisions = review["decisions"]
    results = {r["case_id"]: r for r in report["results"]}
    counts = {v: sum(d["verdict"] == v for d in decisions) for v in ("pass", "fail", "unsure")}
    complete = len(decisions) == len(results)
    judgment = ("failed" if counts["fail"] else "incomplete" if not complete
                else "unresolved" if counts["unsure"] else "passed")
    return {"suite_id": report["suite_id"], "run_id": report["run_id"],
            "scope": "public_development_diagnostic", "review_status": "complete" if complete else "partial",
            "judgment": judgment, "cases": len(results), "reviewed": len(decisions),
            "counts": counts, "reviewer_identity_claim": review["reviewer"]["identity"],
            "reviewer_identity_verified": False, "review_seconds": sum(d["review_seconds"] for d in decisions),
            "critical_findings": [{"case_id": d["case_id"], "severity": d["severity"],
                                   "critical_case": results[d["case_id"]]["critical"], "reason": d["reason"]}
                                  for d in decisions if d["verdict"] == "fail"
                                  and (d["severity"] == "critical" or results[d["case_id"]]["critical"])],
            "release_ready": False}


def _script_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def generate_review(report: dict, suite: dict) -> str:
    """ينتج صفحة مستقلة؛ النص غير الموثوق escaped، وبيانات JS لا تحتوي HTML خامًا."""
    binding = review_binding(report)
    validate_suite(suite)
    if suite["suite_id"] != report["suite_id"] or _sha(suite) != binding["suite_sha256"]:
        _reject("suite", "suite_hash_mismatch", "البنك لا يطابق نسخة التشغيل")
    cases = {c["case_id"]: c for c in suite["cases"]}
    if cases.keys() != {r["case_id"] for r in report["results"]}:
        _reject("suite", "suite_cases_mismatch", "الحالات لا تطابق التشغيل")
    sections, identities = [], []
    for index, result in enumerate(report["results"]):
        case = cases[result["case_id"]]
        if (any(result[key] != case[key] for key in ("capability", "reference", "rubric", "critical"))
                or report["metadata"]["manifest"]["dialogue_sha256"][case["case_id"]] != _sha(case["messages"])):
            _reject("suite", "suite_case_mismatch", "الحوار أو معايير المراجعة لا تطابق التشغيل")
        identities.append({"case_id": result["case_id"], "answer_sha256": _sha(result["answer"]),
                           "status": result["status"]})
        task = "\n\n".join(("المستخدم: " if m["role"] == "user" else "المساعد السابق: ") + m["content"] for m in case["messages"])
        criteria = "".join("<li>" + html.escape(item) + "</li>" for item in case["rubric"])
        sections.append(f'''<section class="case" data-index="{index}">
<h2>{index + 1}. {html.escape(case['capability'])} — <bdi>{html.escape(case['case_id'])}</bdi></h2>
<p>حالة التنفيذ: <bdi>{html.escape(result['status'])}</bdi>؛ حالة حرجة: {'نعم' if case['critical'] else 'لا'}</p>
<h3>المهمة وسياقها</h3><pre>{html.escape(task)}</pre>
<h3>جواب النموذج</h3><pre>{html.escape(result['answer'] if result['answer'] is not None else 'لم يُنتج جوابًا.')}</pre>
<details><summary>المرجع ومعايير الحكم</summary><pre>{html.escape(case['reference'])}</pre><ul>{criteria}</ul></details>
<label>الحكم <select class="verdict"><option value="">لم أراجع بعد</option value="pass">ناجح</option><option value="fail">مخفق</option><option value="unsure">غير محسوم</option></select></label>
<label>سبب الحكم أو الخطأ <textarea class="reason" rows="3"></textarea></label>
<label>درجة الخطأ عند الإخفاق <select class="severity"><option value="">لا ينطبق</option value="critical">حرج</option><option value="material">مؤثر</option><option value="minor">طفيف</option></select></label>
<label>وقت المراجعة الفعلي بالثواني <input class="seconds" type="number" min="1" step="1" inputmode="numeric"></label>
</section>''')
    nonce = secrets.token_urlsafe(24)
    payload = {"binding": binding, "cases": identities}
    return f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'none'; form-action 'none'; base-uri 'none'">
<title>ديوان — مراجعة بشرية محلية</title><style nonce="{nonce}">
body{{font-family:system-ui,sans-serif;background:#f4f4ef;color:#182a2c;margin:auto;max-width:1000px;padding:24px;line-height:1.7}}section,header,footer{{background:white;border:1px solid #ccd4d0;border-radius:12px;padding:22px;margin-bottom:20px}}h1,h2{{line-height:1.4}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f5;padding:16px;border-radius:8px;font:inherit}}label{{display:block;margin:14px 0}}textarea,input,select,button{{font:inherit;padding:8px;max-width:100%;box-sizing:border-box}}textarea{{display:block;width:100%}}button{{background:#155e63;color:white;border:0;border-radius:6px;cursor:pointer}}#message{{white-space:pre-wrap}}summary{{cursor:pointer;font-weight:bold}}bdi{{overflow-wrap:anywhere}}
</style></head><body><header><h1>مراجعة بشرية محلية — ديوان</h1>
<p>بنك تطوير علني؛ الحكم يخص هذه الأجوبة فقط. لا تُمنح جاهزية نشر مهما كانت النتائج. لا يستدعي هذا الملف نموذجًا أو خدمة شبكة.</p>
<p>مراجعتك مستقلة عن الفحوص الجزئية. افحص الطلب الأصلي ومعايير الحكم. اختر «غير محسوم» عند تعذر الحكم، واترك الحالات غير المراجعة بلا اختيار.</p>
<p>الهوية أدناه إقرار محلي غير موثق. لا تُحفظ قراراتك تلقائيًا؛ صدّرها قبل إغلاق الصفحة.</p>
<p>التشغيل: <bdi>{html.escape(report['run_id'])}</bdi>؛ البنك: <bdi>{html.escape(report['suite_id'])}</bdi></p></header>
{''.join(sections)}
<footer><label>هوية المراجع كما يعرّف نفسه <input id="identity" type="text" autocomplete="off"></label>
<label><input id="attestation" type="checkbox"> أقر بأنني راجعت شخصيًا الحالات التي سجلت لها قرارًا، وأن الأسباب والأوقات تعبّر عن مراجعتي.</label>
<button id="export" type="button">تصدير القرارات إلى JSON</button><p id="message" role="status" aria-live="polite"></p></footer>
<script nonce="{nonce}">
"use strict";
const packet = {_script_json(payload)};
document.getElementById('export').addEventListener('click', () => {{
 const message = document.getElementById('message');
 try {{
  const identity = document.getElementById('identity').value.trim();
  if (!identity || !document.getElementById('attestation').checked) throw new Error('أدخل هويتك وفعّل الإقرار الشخصي قبل التصدير.');
  const decisions = [];
  document.querySelectorAll('.case').forEach((section, index) => {{
   const verdict = section.querySelector('.verdict').value;
   if (!verdict) return;
   const reason = section.querySelector('.reason').value.trim();
   const severity = section.querySelector('.severity').value || null;
   const rawSeconds = section.querySelector('.seconds').value;
   const seconds = Number(rawSeconds);
   if (!reason || !/^\\d+$/.test(rawSeconds) || !Number.isSafeInteger(seconds) || seconds < 1) throw new Error('أكمل السبب ووقت المراجعة الصحيح للحالة ' + (index + 1));
   if ((verdict === 'fail' && !severity) || (verdict !== 'fail' && severity !== null)) throw new Error('درجة الخطأ مطلوبة للإخفاق فقط، في الحالة ' + (index + 1));
   if (verdict === 'pass' && packet.cases[index].status !== 'complete') throw new Error('لا يمكن اجتياز جواب مبتور أو فشل تنفيذ، في الحالة ' + (index + 1));
   decisions.push({{case_id: packet.cases[index].case_id, answer_sha256: packet.cases[index].answer_sha256, verdict, reason, severity, review_seconds: seconds}});
  }});
  if (!decisions.length) throw new Error('سجّل قرارًا واحدًا على الأقل.');
  const review = {{...packet.binding, reviewer: {{identity, attestation: true}}, decisions}};
  const blob = new Blob([JSON.stringify(review, null, 2) + '\\n'], {{type:'application/json;charset=utf-8'}});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = packet.binding.run_id + '-human-review.json';
  document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  message.textContent = 'صُدّرت ' + decisions.length + ' من ' + packet.cases.length + ' حالة. الملف يحتاج استيرادًا والتحقق من بصماته؛ لا يثبت جاهزية نشر.';
 }} catch (error) {{ message.textContent = error.message; }}
}});
</script></body></html>'''


def write_review_html(report: dict, suite: dict, output_dir: Path, *, root: Path) -> Path:
    """كتابة جديدة فقط تحت جذر المراجعات؛ يمنع الروابط والخروج والاستبدال."""
    content = generate_review(report, suite)
    root, output_dir = Path(root).absolute(), Path(output_dir).absolute()
    if (any(p.is_symlink() for p in (root, *root.parents, output_dir, *output_dir.parents))
            or not output_dir.is_relative_to(root) or ".." in output_dir.parts):
        _reject("output", "unsafe_output_path", "المخرج يجب أن يكون داخل var/reviews بلا روابط")
    root.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        _reject("output", "unsafe_output_path", "مجلد مخرجات مطلوب")
    path = output_dir / "review.html"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                _reject("output", "unsafe_output_path", "ملف عادي مطلوب")
            stream.write(content)
    except OSError:
        _reject("output", "output_write_failed", "تعذرت الكتابة الجديدة؛ لا يُستبدل ملف موجود")
    return path
