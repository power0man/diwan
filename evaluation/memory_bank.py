"""مدقّقُ بنك الذاكرة المحكومة (ك٤٨): شكلُ السيناريوهات وعتبتُها المسجَّلة سلفًا.

البنكُ مجمَّدٌ قبل البناء (ك٥٢)، فهذا المدقّقُ يفحص البنكَ لا النظام. يتحقّق من ستة أمور:
- العملياتُ والتوقّعات من مفرداتٍ معلنة.
- كلُّ مرجعٍ معرَّفٌ قبل استعماله، وفي مشروعه.
- كلُّ عنصرٍ يُنسى في سيناريو نسيانٍ أو استعادة يُفحص فيه الإيصالُ وبقايا القرص.
- كلُّ سيناريو عزلٍ يمسّ مشروعين على الأقل.
- كلُّ سيناريو حجرٍ يطلب السياقَ محجورًا.
- كلُّ سيناريو يؤدّي ما تسمّيه فئتُه: الاستعادةُ فيها نسخٌ واستعادةٌ يُفحص بعدها، وكلُّ منسيٍّ يُفحص بعد النسيان
  غيابُ نصِّه نفسِه (لا نصٍّ آخر) في مشروعه هو، في الاسترجاع أو السياق وعلى القرص، والحقنُ فيه أمرٌ يلتقطه
  `core.quoted.scan` ثم يُطلب سياقُ مشروعه محجورًا وغائبًا عنه جزءٌ من الأمر نفسِه. وللبنك المكلَّف (`strict`) شرطان أشدّ: النسيانُ يُفحص في الاثنين، والموافقةُ فيها حفظٌ بلا موافقة
  أو اقتراح (ملاحظة Codex على #129: فئةٌ تُسمّى ولا تُؤدّى تُنتج رقمًا أقوى من دليله).
- العتبةُ هي المسجَّلة في `docs/MEMORY-DESIGN.md` §٦ لا غيرها.
"""
from __future__ import annotations

from core.canonical import PayloadRejected
from core.quoted import scan

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


def validate_memory_bank(bank: dict, *, strict: bool = False) -> dict:
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
        _validate_semantics(scenario, path, strict)
    return bank


def _names(step: dict, text: str, fields=("absent",)) -> bool:
    """في حقول التوقّع جزءٌ من هذا النصّ نفسِه، لا نصٌّ آخر."""
    return any(a and a in text for field in fields for a in step.get(field) or [])


def _bound(step: dict, item: dict) -> bool:
    """التوقّعُ يخصّ عنصرًا إن كان في `absent` جزءٌ من نصّه، وفي مشروعه هو: غيابُه عن مشروعٍ آخر غيابٌ طبيعيّ
    لا يشهد بالنسيان ولا بالموافقة (ملاحظة Codex على #129). والعزلُ وحده يفحص مشروعًا آخر عمدًا."""
    return step["project"] == item["project"] and _names(step, item["text"])


def _validate_semantics(scenario: dict, path: str, strict: bool) -> None:
    steps = scenario["steps"]
    last = lambda op: max((i for i, s in enumerate(steps) if s.get("op") == op), default=None)
    checked_after = lambda i, kinds: {s["expect"] for s in steps[i + 1:] if s.get("expect") in kinds}
    category = scenario["category"]
    made = {s["as"]: (i, s) for i, s in enumerate(steps) if s.get("op") in ("remember", "propose")}
    if category == "backup":
        restore = last("restore")
        if last("backup") is None or restore is None or not checked_after(restore, {"retrieve", "context", "residue"}):
            _reject(path, "backup_semantics_missing", "نسخٌ واستعادةٌ ثم فحصٌ بعد الاستعادة")
    if category in ("forget", "backup"):
        # التوقّعُ يُحسب للمنسيّ إن كان في `absent` جزءٌ من نصّه هو، فغيابُ نصٍّ لم يُحفظ قطّ لا يشهد بالنسيان
        # (ملاحظة Codex على #129)
        # وفي النسخ الاحتياطي يُفحص بعد الاستعادة، فهي التي قد تُحيي المنسيّ
        after = steps[max(last("forget"), last("restore") if category == "backup" else -1) + 1:]
        for ref in sorted({s["ref"] for s in steps if s.get("op") == "forget"}):
            bound = {s["expect"] for s in after if s.get("expect") in ("retrieve", "context", "residue")
                     and _bound(s, made[ref][1])}
            in_use = bound & {"retrieve", "context"}
            if not in_use or (strict and category == "forget" and in_use != {"retrieve", "context"}):
                _reject(path, "forget_not_checked_in_use",
                        "نصُّ المنسيّ غائبٌ بعد النسيان في الاسترجاع والسياق" if strict else "في الاسترجاع أو السياق")
            if "residue" not in bound:
                _reject(path, "forgotten_value_unchecked_on_disk", "نصُّ المنسيّ غائبٌ عن القرص بعد النسيان")
    if category == "injection":
        # حجرُ نصٍّ لا أمرَ فيه لا يشهد بالحجر: عنصرٌ فيه أمرٌ يلتقطه الماسح، ثم سياقٌ محجورٌ في مشروعه بعد حفظه
        # يطلب في `absent` جزءًا من المقطع الآمر نفسِه، وهو ما يُبدله `quarantine` بعلامته. فإن بلغ الأمرُ السياقَ
        # سقطت الحالة؛ وذكرُ الجزء البريء من العنصر وحده لا يشهد (ملاحظتا Codex على #129)
        def names_the_directive(step, item):
            spans = [item["text"][f.start:f.end] for f in scan(item["text"])]
            return any(a and any(a in span for span in spans) for a in step.get("absent") or [])
        if not any(s.get("expect") == "context" and s.get("quarantined") and s["project"] == item["project"]
                   and names_the_directive(s, item)
                   for i, item in made.values() if scan(item["text"]) for s in steps[i + 1:]):
            _reject(path, "injection_without_directive",
                    "عنصرٌ فيه أمرٌ مدسوس ثم سياقُ مشروعه محجورًا يطلب غيابَ الأمر نفسِه")
    if not strict:
        return
    # شروطُ البنك المكلَّف (ملاحظات Codex على #129): كلُّ فئةٍ تختبر ما تسمّيه لا ما يشبهه
    if category == "consent":
        unconsented = [(i, ref, s) for ref, (i, s) in made.items()
                       if s["op"] == "propose" or s["consent"] == "none"]
        if not unconsented:
            _reject(path, "consent_without_unconsented_save", "اقتراحٌ أو حفظٌ بلا موافقة")
        def checked_before_approval(i, ref, item):
            approved = next((j for j, s in enumerate(steps) if s.get("op") == "approve" and s.get("ref") == ref),
                            len(steps))
            kinds = {s["expect"] for s in steps[i + 1:approved] if s.get("expect") in ("retrieve", "context", "residue")
                     and _bound(s, item)}
            return "residue" in kinds and kinds & {"retrieve", "context"}
        if not any(checked_before_approval(*u) for u in unconsented):
            _reject(path, "consent_unchecked_before_approval",
                    "غيابُ نصّ ما لم يُوافَق عليه في الاستعمال وعلى القرص قبل الموافقة")
    # والفحصُ بعد حفظ العنصر: غيابُه عن مشروعٍ آخر قبل أن يوجد لا يشهد بالعزل (ملاحظة Codex على #129)
    if category == "isolation" and not any(
            s.get("expect") in ("retrieve", "context") and s["project"] != item["project"] and _names(s, item["text"])
            for i, item in made.values() for s in steps[i + 1:]):
        _reject(path, "isolation_without_cross_project_absence", "غيابُ نصّ عنصرٍ من مشروعٍ في مشروعٍ آخر بعد حفظه")
    if category == "backup":
        at = {s["as"]: i for i, s in enumerate(steps) if s.get("as")}
        snapshot_then_forget = any(
            r.get("op") == "restore" and any(f.get("op") == "forget" and at[f["ref"]] < at[r["ref"]] < j < k
                                             for j, f in enumerate(steps))
            for k, r in enumerate(steps))
        if not snapshot_then_forget:
            _reject(path, "backup_without_prior_snapshot", "نسخةٌ فيها العنصر، ثم نسيانُه، ثم استعادتُها")


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
