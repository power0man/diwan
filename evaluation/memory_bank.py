"""مدقّقُ بنك الذاكرة المحكومة (ك٤٨): شكلُ السيناريوهات وعتبتُها المسجَّلة سلفًا.

البنكُ مجمَّدٌ قبل البناء (ك٥٢)، فهذا المدقّقُ يفحص البنكَ لا النظام. يتحقّق من ستة أمور:
- العملياتُ والتوقّعات من مفرداتٍ معلنة.
- كلُّ مرجعٍ معرَّفٌ قبل استعماله، وفي مشروعه.
- كلُّ عنصرٍ يُنسى في سيناريو نسيانٍ أو استعادة يُفحص فيه الإيصالُ وبقايا القرص.
- كلُّ سيناريو عزلٍ يمسّ مشروعين على الأقل.
- كلُّ سيناريو حجرٍ يطلب السياقَ محجورًا.
- العتبةُ هي المسجَّلة في `docs/MEMORY-DESIGN.md` §٦ لا غيرها.
"""
from __future__ import annotations

from core.canonical import PayloadRejected

THRESHOLDS = {"forget_rate": 1.0, "leakage": 0, "consent_violations": 0, "injection_unquarantined": 0}
CATEGORIES = frozenset({"forget", "backup", "isolation", "consent", "injection"})
OPS = {
    "remember": {"op", "project", "text", "consent", "as"},
    "propose": {"op", "project", "text", "as"},
    "approve": {"op", "project", "ref"},
    "forget": {"op", "project", "ref"},
    "backup": {"op", "project", "as"},
    "restore": {"op", "project", "ref"},
}
EXPECTS = {
    "retrieve": {"expect", "project", "query", "absent", "present"},
    "context": {"expect", "project", "question", "absent", "present"},
    "residue": {"expect", "project", "absent"},
    "receipt": {"expect", "project", "ref", "count"},
}
CONSENTS = frozenset({"owner", "none"})


def _reject(path: str, code: str, reason: str):
    raise PayloadRejected(path, code, reason)


def _texts(value, path):
    if not isinstance(value, list) or not all(isinstance(t, str) and t.strip() for t in value):
        _reject(path, "texts_invalid", "قائمةُ نصوصٍ غير فارغة")


def validate_memory_bank(bank: dict) -> dict:
    if not isinstance(bank, dict) or set(bank) != {"schema_version", "suite_id", "kind", "description", "projects",
                                                    "thresholds", "scenarios"}:
        _reject("bank", "schema_fields", "حقولُ البنك المعلنة وحدها")
    if bank["schema_version"] != 1 or bank["kind"] != "memory_scenarios":
        _reject("bank.kind", "kind_invalid", "memory_scenarios بالنسخة 1")
    if bank["thresholds"] != THRESHOLDS:
        _reject("bank.thresholds", "thresholds_changed", "العتبةُ المسجَّلة سلفًا لا تتغيّر")
    projects = set(bank["projects"])
    seen = set()
    for index, scenario in enumerate(bank["scenarios"]):
        path = f"bank.scenarios[{index}]"
        if set(scenario) != {"id", "category", "note", "steps"} or scenario["category"] not in CATEGORIES:
            _reject(path, "scenario_invalid", "حقولُ السيناريو وفئتُه المعلنة")
        if scenario["id"] in seen:
            _reject(path + ".id", "scenario_id_duplicate", "معرّفٌ مكرّر")
        seen.add(scenario["id"])
        _validate_steps(scenario, path, projects)
    return bank


def _validate_steps(scenario: dict, path: str, projects: set[str]) -> None:
    refs: dict[str, str] = {}            # المرجع ← مشروعه
    kinds: dict[str, str] = {}           # المرجع ← نوعه (عنصر، اقتراح، نسخة)
    forgotten, checked_residue, receipted, touched = set(), set(), set(), set()
    expects = 0
    for i, step in enumerate(scenario["steps"]):
        where = f"{path}.steps[{i}]"
        if step.get("project") not in projects:
            _reject(where + ".project", "project_unknown", "مشروعٌ غير معلن")
        touched.add(step["project"])
        if "op" in step:
            op = step["op"]
            if op not in OPS or set(step) != OPS[op]:
                _reject(where, "op_invalid", f"عمليةٌ بحقولها المعلنة: {op!r}")
            if op in ("remember", "propose"):
                if not isinstance(step["text"], str) or not step["text"].strip():
                    _reject(where + ".text", "text_invalid", "نصٌّ غير فارغ")
                if op == "remember" and step["consent"] not in CONSENTS:
                    _reject(where + ".consent", "consent_invalid", "owner أو none")
            if "as" in step:
                if step["as"] in refs:
                    _reject(where + ".as", "ref_reused", "مرجعٌ معرَّفٌ سابقًا")
                refs[step["as"]] = step["project"]
                kinds[step["as"]] = {"backup": "backup", "propose": "proposal"}.get(op, "item")
            if "ref" in step:
                ref = step["ref"]
                if ref not in refs or refs[ref] != step["project"]:
                    _reject(where + ".ref", "ref_unknown", "مرجعٌ غير معرَّف في مشروعه")
                wanted = {"approve": ("proposal",), "forget": ("item", "proposal"), "restore": ("backup",)}[op]
                if kinds[ref] not in wanted:
                    _reject(where + ".ref", "ref_kind", f"{op} لا يقع على {kinds[ref]}")
                if op == "forget":
                    forgotten.add(ref)
        elif "expect" in step:
            kind = step["expect"]
            allowed = EXPECTS.get(kind)
            extra = {"quarantined"} if kind == "context" else set()
            if allowed is None or not allowed <= set(step) <= allowed | extra:
                _reject(where, "expect_invalid", f"توقّعٌ بحقوله المعلنة: {kind!r}")
            for key in ("absent", "present"):
                if step.get(key):
                    _texts(step[key], f"{where}.{key}")
            if kind in ("retrieve", "context") and not (step["absent"] or step["present"]):
                _reject(where, "expect_empty", "توقّعٌ بلا نصٍّ غائبٍ ولا حاضر")
            if kind == "residue":
                _texts(step["absent"], f"{where}.absent")
                checked_residue.add(step["project"])
            if kind == "receipt":
                if step["ref"] not in refs or type(step["count"]) is not int or step["count"] < 1:
                    _reject(where, "receipt_invalid", "إيصالٌ لمرجعٍ معرَّف بعددٍ موجب")
                receipted.add(step["ref"])
            if step.get("quarantined") not in (None, True):
                _reject(where + ".quarantined", "quarantined_invalid", "True وحدها")
            expects += 1
        else:
            _reject(where, "step_invalid", "عمليةٌ أو توقّع")
    category = scenario["category"]
    if not expects:
        _reject(path, "no_expectation", "سيناريو بلا توقّع")
    if category in ("forget", "backup"):
        if not forgotten:
            _reject(path, "forget_missing", "سيناريو نسيانٍ بلا نسيان")
        if {refs[r] for r in forgotten} - checked_residue:
            _reject(path, "residue_unchecked", "منسيٌّ لم تُفحص بقاياه على القرص")
        if category == "forget" and forgotten - receipted:
            _reject(path, "receipt_unchecked", "منسيٌّ بلا فحص إيصال")
    if category == "isolation" and len(touched) < 2:
        _reject(path, "isolation_single_project", "العزلُ يُقاس بمشروعين على الأقل")
    if category == "injection" and not any(s.get("quarantined") for s in scenario["steps"]):
        _reject(path, "quarantine_unchecked", "سيناريو حجرٍ لا يطلب السياقَ محجورًا")
