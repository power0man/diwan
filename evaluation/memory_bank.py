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

import re

from core.attribution import normalize
from core.canonical import PayloadRejected
from core.quoted import DIRECTIVE_PATTERNS, QUARANTINE_MARK, scan, wrap
from memory.store import HEADER, held_text

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


def contains(haystack: str, needle: str) -> bool:
    """المطابقةُ التي يفحص بها المُشغِّلُ الحضورَ والغياب: حرفيًّا أو بعد التطبيع العربيّ (التشكيل وأشكال الحروف).
    وهي هنا لا في المُشغِّل ليحكم بها المدقّقُ كما يحكم المُشغِّل (ملاحظة Codex على #129)."""
    return needle in haystack or normalize(needle).strip() in normalize(haystack)


def names_every_directive(step: dict, text: str) -> bool:
    """في `absent` لكلّ مقطعٍ آمرٍ يلتقطه الماسحُ في النصّ جزءٌ منه، أربعُ كلماتٍ أو حروفٍ فأكثر. فحجرُ الأول وبقاءُ الثاني
    حرفيًّا يمرّ إن لم يُطلب إلا الأول، و«I» وحده يغيب ولو بقي الأمرُ كلُّه بعده (ملاحظتا Codex على #129).
    ويقرؤها المُشغِّلُ ليطلب في السياق علامةَ كلِّ أمرٍ في العنصر الذي يسمّيه التوقّع."""
    absent = [a for a in step.get("absent") or [] if len(re.findall(r"\w", a)) >= 4]
    spans = [text[f.start:f.end] for f in scan(text)]
    return bool(spans) and all(any(a in span for a in absent) for span in spans)


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

    def active_at(ref, index):
        """العنصرُ قائمٌ في المخزن عند هذه الخطوة: حُفظ قبلها بموافقة المالك أو وُوفق على اقتراحه قبلها، ولم يُنسَ
        قبلها. فاقتراحٌ لم يُوافَق عليه أو حفظٌ بلا موافقة أو منسيٌّ لا يشهد غيابُه ولا حضورُه بشيء (ملاحظات Codex على #129).
        ويُتتبَّع المخزنُ خطوةً خطوة لأن الاستعادةَ تُرجعه إلى ما كان عند نسختها: ما حُفظ أو وُوفق عليه بعد النسخة يزول
        بها، والمنسيُّ قبلها يبقى منسيًّا (`tombstones_from`)، فلا يشهد مصدرٌ محته استعادةٌ بشيء (ملاحظة Codex على #129)."""
        saved, active, forgotten, snapshots = set(), set(), set(), {}
        for s in steps[:index]:
            op = s.get("op")
            if op in ("remember", "propose"):
                saved.add(s["as"])
                if op == "remember" and s.get("consent") == "owner":
                    active.add(s["as"])
            elif op == "approve" and s["ref"] in saved:
                active.add(s["ref"])
            elif op == "forget":
                forgotten.add(s["ref"])
                saved.discard(s["ref"])
                active.discard(s["ref"])
            elif op == "backup":
                snapshots[s["as"]] = (set(saved), set(active))
            elif op == "restore" and s["ref"] in snapshots:
                saved, active = (state - forgotten for state in snapshots[s["ref"]])
        return ref in active

    if category == "backup":
        restore = last("restore")
        if last("backup") is None or restore is None or not checked_after(restore, {"retrieve", "context", "residue"}):
            _reject(path, "backup_semantics_missing", "نسخٌ واستعادةٌ ثم فحصٌ بعد الاستعادة")
        # نسيانٌ بعد آخر استعادة يمحو ما قد تكون أحيته قبل أن يُفحص، فلا يشهد الفحصُ بالاستعادة (ملاحظة Codex على #129)
        if any(s.get("op") == "forget" for s in steps[restore + 1:]):
            _reject(path, "backup_checked_after_a_later_forget", "لا نسيانَ بعد آخر استعادة، فالفحصُ يشهد بما أعادته هي")
    if category in ("forget", "backup"):
        # التوقّعُ يُحسب للمنسيّ إن كان في `absent` جزءٌ من نصّه هو، فغيابُ نصٍّ لم يُحفظ قطّ لا يشهد بالنسيان
        # (ملاحظة Codex على #129)
        # ونافذةُ كلّ منسيٍّ من نسيانه هو، لا من آخر نسيانٍ في السيناريو (ملاحظة Codex على #129)
        restores = [i for i, s in enumerate(steps) if s.get("op") == "restore"]
        for ref in sorted({s["ref"] for s in steps if s.get("op") == "forget"}):
            forgets = [i for i, s in enumerate(steps) if s.get("op") == "forget" and s.get("ref") == ref]
            # وفي النسخ الاحتياطي يُفحص بعد آخر استعادة، فهي التي قد تُحيي المنسيّ
            if category == "backup":
                windows = [(max(forgets[-1], last("restore")) + 1, len(steps))]
            # وفي سيناريو النسيان لكلّ نسيانٍ نافذتُه حتى أول استعادةٍ تليه: الاستعادةُ من نسخةٍ أقدم تمحو ما قد يكون بقي
            # خطأً قبل أن يُفحص، فيمرّ نسيانٌ لم يحذف شيئًا، ولو جاء بعدها نسيانٌ آخر للعنصر نفسِه أو لغيره يفتح نافذةً
            # تُفحص فيها (ملاحظات Codex على #129)
            else:
                windows = [(forgot + 1, next((i for i in restores if i > forgot), len(steps))) for forgot in forgets]
            for start, stop in windows:
                bound = {s["expect"] for s in steps[start:stop] if s.get("expect") in ("retrieve", "context", "residue")
                         and _bound(s, made[ref][1])}
                in_use = bound & {"retrieve", "context"}
                if not in_use or (strict and category == "forget" and in_use != {"retrieve", "context"}):
                    _reject(path, "forget_not_checked_in_use",
                            "نصُّ المنسيّ غائبٌ بعد كلّ نسيانٍ في الاسترجاع والسياق" if strict
                            else "في الاسترجاع أو السياق")
                if "residue" not in bound:
                    _reject(path, "forgotten_value_unchecked_on_disk", "نصُّ المنسيّ غائبٌ عن القرص بعد النسيان")
    if category == "injection":
        # حجرُ نصٍّ لا أمرَ فيه لا يشهد بالحجر: عنصرٌ فيه أمرٌ يلتقطه الماسح، ثم سياقٌ محجورٌ في مشروعه بعد حفظه
        # يطلب في `absent` جزءًا من المقطع الآمر نفسِه، وهو ما يُبدله `quarantine` بعلامته. فإن بلغ الأمرُ السياقَ
        # سقطت الحالة؛ وذكرُ الجزء البريء من العنصر وحده لا يشهد (ملاحظتا Codex على #129). وكلُّ مقطعٍ آمرٍ يلتقطه
        # الماسح في العنصر يُطلب جزءٌ منه ذو شأن (`names_every_directive`)
        # والعنصرُ الآمر قائمٌ في المخزن عند الفحص: اقتراحٌ لم يُوافَق عليه لا يبلغ السياقَ أصلًا (ملاحظة Codex على #129)
        fenced = [(k, s, ref, item) for ref, (i, item) in made.items()
                  for k, s in enumerate(steps) if k > i and s.get("expect") == "context" and s.get("quarantined")
                  and s["project"] == item["project"] and names_every_directive(s, item["text"]) and active_at(ref, k)]
        if not fenced:
            _reject(path, "injection_without_directive",
                    "عنصرٌ فيه أمرٌ مدسوس ثم سياقُ مشروعه محجورًا يطلب غيابَ الأمر نفسِه")
        # وفي البنك المكلَّف يطلب السياقُ نفسُه في `present` جزءًا من نصّ العنصر الآمر لا يحمله عنصرٌ غيرُه في مشروعه
        # حُفظ قبل الفحص، فحضورُه يثبت أن العنصرَ الآمرَ نفسَه بلغ السياقَ المفحوص محجورًا؛ وإلا فقد يُفحص سياقُ عنصرٍ
        # بريءٍ آخر أو نسخةٍ من جزئه البريء والأمرُ غائبٌ عنه طبيعةً أو مقطوعٌ بحدّ السياق (ملاحظتا Codex على #129)
        # والشاهدُ كلماتٌ لا يولّدها غلافُ السياق (رأسُه وسياجُه وعلامةُ الحجر وشرطةُ السطر)، فـ«-» مثلًا لا يشهد
        # (ملاحظة Codex على #129). ومنه العلاماتُ التي يولّدها الحجرُ لكل رمز: «[محتوى محجور: role_override_ar]» يطبعها
        # حجرُ عنصرٍ آمرٍ آخر، فرمزٌ في نصّ العنصر لا يشهد بحضوره (ملاحظة Codex على #129)
        wrapper = " ".join((HEADER, QUARANTINE_MARK, wrap("", nonce="0" * 8)[0], "- ",
                            *(QUARANTINE_MARK.format(code=code) for code, _ in DIRECTIVE_PATTERNS)))

        # والمقارنةُ بمطابقة المُشغِّل نفسِها: «موعدُ التسليم» شاهدًا وفي عنصرٍ آخر «موعد التسليم» يحضر بها في سياقه
        # فيشهد الشاهدُ بعنصرٍ ليس الآمر (ملاحظة Codex على #129). ويُقارن بكلّ عنصرٍ آخر كما يبلغ السياق، محجورًا: فعلامةُ
        # حجرِ عنصرٍ آمرٍ آخر وما يليها تحضر في السياق ولا تحضر في نصّه الخام. ولا يعبر الشاهدُ سطرًا، فشاهدٌ يُجمع من آخر
        # عنصرٍ وأول تاليه يحضر في السياق ولو غاب العنصرُ الآمر عنه (ملاحظة Codex على #129). وشرطةُ السطر يُسقطها التطبيعُ
        # في `contains`، فشاهدٌ يبدأ بها يُقارن بما بعدها
        def shown_only_by_the_item(k, s, ref, item):
            others = [held_text(o["text"]) for r, (j, o) in made.items()
                      if r != ref and j < k and o["project"] == item["project"]]
            return any(p and "\n" not in p and contains(item["text"], p) and len(re.findall(r"\w", p)) >= 4
                       and not contains(wrapper, p) and not any(contains(held, p) for held in others)
                       for p in s.get("present") or [])
        if strict and not any(shown_only_by_the_item(*f) for f in fenced):
            _reject(path, "injection_item_not_shown_in_checked_context",
                    "السياقُ المحجور يحضر فيه جزءٌ من نصّ العنصر الآمر نفسِه")
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
            # ولا يُحسب فحصٌ بعد استعادةٍ تلي الحفظ: استعادةُ نسخةٍ أقدم تمحو ما حُفظ خطأً قبل الموافقة، فيغيب ولو تسرّب
            # (ملاحظة Codex على #129)
            restored = next((j for j in range(i + 1, len(steps)) if steps[j].get("op") == "restore"), len(steps))
            kinds = {s["expect"] for s in steps[i + 1:min(approved, restored)]
                     if s.get("expect") in ("retrieve", "context", "residue") and _bound(s, item)}
            return "residue" in kinds and kinds & {"retrieve", "context"}
        if not any(checked_before_approval(*u) for u in unconsented):
            _reject(path, "consent_unchecked_before_approval",
                    "غيابُ نصّ ما لم يُوافَق عليه في الاستعمال وعلى القرص قبل الموافقة")
    # والفحصُ بعد حفظ العنصر: غيابُه عن مشروعٍ آخر قبل أن يوجد لا يشهد بالعزل (ملاحظة Codex على #129)
    # والمصدرُ قائمٌ في المخزن عند الفحص: غيابُ ما لم يُحفظ عن مشروعٍ آخر غيابٌ طبيعيّ (ملاحظة Codex على #129)
    # والشاهدُ `retrieve` لا `context` وحده: غيابُ الاسترجاع يُفحص في قائمة المشروع كلّها، والسياقُ محدودٌ بـMAX_CONTEXT_ITEMS
    # فيسقط منه عنصرٌ متسرّبٌ خلف خمسين قبله ولا يُرى (ملاحظة Codex على #129)
    if category == "isolation" and not any(
            s.get("expect") == "retrieve" and s["project"] != item["project"] and _names(s, item["text"])
            and active_at(ref, k)
            for ref, (i, item) in made.items() for k, s in enumerate(steps) if k > i):
        _reject(path, "isolation_without_cross_project_absence",
                "غيابُ نصّ عنصرٍ من مشروعٍ في استرجاع مشروعٍ آخر (قائمته كلّها) بعد حفظه")
    if category == "backup":
        at = {s["as"]: i for i, s in enumerate(steps) if s.get("as")}
        # الاستعادةُ التي تسبق الفحوص هي آخرُ استعادة، فهي التي تُستعاد منها نسخةٌ أُخذت والعنصرُ قائم ثم نُسي؛ واستعادةٌ
        # أسبق تمحوها التالية فلا يشهد الفحصُ بها (ملاحظة Codex على #129)
        k = last("restore")
        final = steps[k]
        snapshot_then_forget = any(f.get("op") == "forget" and active_at(f["ref"], at[final["ref"]])
                                   and at[final["ref"]] < j < k for j, f in enumerate(steps))
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
