"""خدمة البحث — أول تركيبٍ فوق العقد (م٦، ق٢٠).

الخدمة **لا تملك معرفة ولا مسردًا**: تسأل العقد عبر النواة المحكومة
وتؤلف. تعددُ الوكلاء مُدَّخرٌ لهذه الخدمة وحدها خلف حجز الميزانية:
نداءُ **مخطِّطٍ** يقترح جبهات (أسئلة حوكمة + كلمات معجم) في عقدٍ
مغلق يُفرض بنيويًّا، ثم تُنفَّذ الجبهات عبر مشغّلات العقد بميزانية
الخدمة وسجلها، ثم نداءُ **مؤلِّفٍ** يركّب الخلاصة ملزمةَ الاستشهاد
[م1] [م2]… من المواد المرقمة وحدها.

**الفاتورة كاملة في السجل**: كل نداءات التشغيلة — مخطِّطًا وجبهاتٍ
ومؤلِّفًا — في سجلِّ تشغيلةٍ واحد، وتُختم بقيد `service_bill` يجمع
المُسوَّى. **والاستئناف من آخر قيد**: مفاتيح عدم التكرار حتمية من
السؤال والمواد، فقطعُ التشغيلة وإعادتُها تستهلك المنجز عرضًا
(replay) ولا تدفع ثمنه مرتين — نقاطُ الاستئناف هي قيود السجل نفسها.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core.acquisitions import SourceRegister
from core.attribution import bind_claims, unsupported
from core.canonical import PayloadRejected, digest
from core.contracts import Message, Request
from core.knowledge import KnowledgeItem
from core.run import execute
from run_node import load_node_module

MAX_ATTEMPTS = 3
NODES = ("maritime", "linguistics")

_CITE_M = re.compile(r"\[\s*م\s*([\d٠-٩]+)\s*\]")
_CITE_M_MARKER = re.compile(r"\[\s*م[^\[\]]*(?:\]|(?=\[|$))")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_AR_LETTER = "ء-ي"

PLANNER_SYSTEM = (
    "أنت مخطط خدمة البحث في نظام «ديوان». تُخرج JSON فقط بلا أي نص "
    "آخر، بهذه البنية حرفيًّا: "
    '{"maritime": ["سؤال حوكمة بحرية دقيق", ...], '
    '"lexicon": ["كلمة مفردة للمعاجم", ...]} — '
    "سؤالان بحريان على الأكثر يلتصقان بألفاظ اللوائح، وكلمة أو كلمتان "
    "مفردتان (جذر أو مصطلح واحد بلا أل التعريف إن أمكن) لمعناهما أثر "
    "في السؤال. لا تشرح."
)

COMPOSER_SYSTEM = {
    "بحث": (
        "أنت مؤلف خدمة البحث في نظام «ديوان». تكتب خلاصة بحثية موجزة "
        "بالعربية الفصحى من المواد المرقمة المعطاة **وحدها**، وتذكر بعد "
        "كل معلومة رقم مادتها بالصيغة [م1] [م2]… معلومة بلا مادة لا "
        "تُكتب، ولا تضف علمًا من عندك."
    ),
    "عرض": (
        "أنت مؤلف عروض في نظام «ديوان». تكتب مخطط عرضٍ تقديمي: عنوان، "
        "ثم شرائح مرقمة (شريحة 1: …) لكل شريحة نقاط موجزة، كل نقطة "
        "تُختم برقم مادتها [م1] [م2]… من المواد المعطاة وحدها."
    ),
}


def front_notice_text(text: str) -> str:
    """سبب التعذر/سؤاله وصف تشغيل، وأي وسم مادة فيه ليس شاهدًا."""
    return _CITE_M.sub(
        lambda m: f"(إحالة غير معتمدة م{m.group(1)})", text)


class ResearchService:
    """تشغيلةُ بحثٍ محكومة قابلة للاستئناف — النموذج عبر حلقة م٠ فقط."""

    def __init__(self, root: Path, provider, budget, runs_dir=None,
                 ledger=None, mar_node=None, ling_node=None):
        self.root = root
        self.provider = provider
        self.budget = budget
        # **سجلٌّ لكل تشغيلة بمفتاحها** تصنعه الخدمة: فاتورةُ السجل كله
        # = فاتورةُ التشغيلة بنيويًّا — الحصرُ الموضعي على سجلٍّ مشترك
        # انكسر بتداخل تشغيلتين (قاتل تدقيق م٦). حقنُ ledger صريحًا
        # يجعل حصرَ التشغيلات عقدَ المستدعي (وضع الاختبار).
        self.runs_dir = Path(runs_dir) if runs_dir \
            else root / "var" / "services"
        self.ledger = ledger
        self._mar = mar_node      # حقن للاختبار الصوري — الافتراض حي
        self._ling = ling_node
        self.register = SourceRegister(root / "sources"
                                       / "acquisitions.jsonl")

    @staticmethod
    def run_key_of(model: str, question: str, form: str = "بحث") -> str:
        return hashlib.sha256(
            (model + "|" + form + "|" + question).encode()).hexdigest()[:24]

    def run_ledger(self, question: str, form: str = "بحث"):
        from core.ledger import Ledger
        key = self.run_key_of(self.provider.model, question, form)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        return Ledger(self.runs_dir / f"research-{key}.jsonl")

    # — النداء المحكوم بسلّم المحاولات (نمط العقد نفسه) —

    def _attempt_key(self, key: str, n: int, correction_scope: str = "") -> str:
        return key if n == 1 else f"{key}{correction_scope}-a{n}"

    def _resume_attempt(self, key: str, correction_scope: str = "") -> int:
        """أعلى محاولة لها جواب مقيد؛ تثبت جواز تخطي عطل عابر سبقها.

        ليست نقطة بدء: رسائل التصويب السابقة تُستعاد عبر execute.
        """
        for n in range(MAX_ATTEMPTS, 1, -1):
            prior = self.ledger.find_last_by_idempotency_key(
                self._attempt_key(key, n, correction_scope))
            if prior and prior["record"].get("kind") == "ok":
                return n
        return 1

    def _governed(self, key: str, system: str, user: str,
                  data_policy: str, check, max_output: int = 900,
                  correction_scope: str = ""):
        """ينادي ويُحكِّم `check(text)`: يعيد قيمته إن قبل، وإلا صوّب
        محاولةً تالية حتى النفاد. يعيد بناء رسائل التصويب عند الاستئناف."""
        messages = (Message("system", system), Message("user", user))
        last_flaw = "لم يُنادَ"
        recorded_attempt = self._resume_attempt(key, correction_scope)
        for n in range(1, MAX_ATTEMPTS + 1):
            k = self._attempt_key(key, n, correction_scope)
            req = Request(messages=messages, model=self.provider.model,
                          model_version="ollama", max_output=max_output,
                          deadline_s=240.0, data_policy=data_policy,
                          idempotency_key=k)
            if n < recorded_attempt:
                prior_calls = [e["record"] for e in self.ledger.entries()
                               if e["record"].get("idempotency_key") == k
                               and e["record"].get("kind") in ("ok", "error")]
                # لا نعيد دفع عطل عابر تجاوزه السجل بنجاح لاحق، لكنه
                # لا يُتخطى إن تعارضت بصمة أي طلب قديم أو كان له أثر
                # آخر: يبقى execute وحده صاحب العرض ورفض التنازع.
                if prior_calls and all(
                        r["kind"] == "error" and r.get("retryable") is True
                        and r.get("request_digest") == digest(req.fingerprint_payload())
                        for r in prior_calls):
                    continue
            outcome = execute(req, self.provider, self.budget, self.ledger)
            if outcome.response is None:
                # التشخيص بآخر قيدٍ للمفتاح لا أوله — عطل عابر قديم كان
                # يقنّع عطلًا لاحقًا غير قابل فيحرق محاولات (تدقيق م٦)
                prior = self.ledger.find_last_by_idempotency_key(k)
                if prior and prior["record"].get("kind") == "error" \
                        and prior["record"].get("retryable") is True:
                    last_flaw = f"عطل قابل للإعادة: {outcome.error_code}"
                    continue
                raise RuntimeError(f"نداء الخدمة فشل: {outcome.error_code}")
            text = outcome.response.content.strip()
            if outcome.response.stop_reason != "complete":
                last_flaw = "جواب مبتور (max_output)"
            else:
                ok, value_or_flaw = check(text)
                if ok:
                    return value_or_flaw, outcome
                last_flaw = value_or_flaw
            messages = messages + (
                Message("assistant", text),
                Message("user", f"جوابك مرفوض: {last_flaw}. أعده كاملًا "
                        "ملتزمًا العقد حرفيًّا."))
        raise ValueError(f"استُنفدت المحاولات ({MAX_ATTEMPTS}) — {last_flaw}")

    # — المخطط: جبهات في عقد مغلق يُفرض —

    def _check_plan(self, text: str):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return False, "لا JSON في الجواب"
        try:
            plan = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            return False, f"JSON فاسد: {exc}"
        if set(plan) != {"maritime", "lexicon"}:
            return False, "المفتاحان maritime وlexicon لا غير"
        mar = plan["maritime"]
        lex = plan["lexicon"]
        if not (isinstance(mar, list) and 1 <= len(mar) <= 2
                and all(isinstance(q, str) and q.strip()
                        and len(q) <= 200 for q in mar)):
            return False, "maritime: سؤال أو سؤالان نصيان (≤200 حرفًا)"
        if len({q.strip() for q in mar}) != len(mar):
            return False, "maritime: سؤالان متطابقان — مادة مكررة تضخم "\
                          "الفاتورة وتوهم تعدد الشواهد"
        if not (isinstance(lex, list) and 1 <= len(lex) <= 2
                and all(isinstance(w, str) and w.strip()
                        and len(w.split()) == 1 for w in lex)):
            return False, "lexicon: كلمة أو كلمتان مفردتان"
        if not all(re.search(f"[{_AR_LETTER}]", w) for w in lex):
            return False, "lexicon: كلمات عربية — المعاجم عربية"
        if len({w.strip() for w in lex}) != len(lex):
            return False, "lexicon: كلمتان متطابقتان"
        return True, {"maritime": [q.strip() for q in mar],
                      "lexicon": [w.strip() for w in lex]}

    # — التأليف: استشهاد [مN] ملزم —

    def _check_brief(self, materials_n: int, materials: list | None = None):
        def check(text: str):
            malformed = [m for m in _CITE_M_MARKER.findall(text)
                         if not _CITE_M.fullmatch(m)]
            if malformed:
                return False, "citation_malformed: صيغة إحالة معطوبة"
            cited = {int(c.translate(_AR_DIGITS))
                     for c in _CITE_M.findall(text)}
            fabricated = sorted(i for i in cited
                                if not 1 <= i <= materials_n)
            if fabricated:   # إسنادٌ إلى مادة لا وجود لها — أخطر توثيقيًّا
                return False, (f"استشهاد ملفق خارج المواد: "
                               f"{fabricated[:3]} والمواد {materials_n}")
            valid = sorted(cited)
            if len(valid) < min(2, materials_n):
                return False, "استشهاد بمادتين مختلفتين فأكثر مطلوب"
            # لا عناوين ولا فقرات قصيرة معفاة: الحارس نفسه للبحرية
            # والبحث. غياب حمولة المواد في فحص بنيوي لا يعفي أي مقطع.
            by_ref = {i: {"text": m["item"]["text"],
                          "part": m["item"]["part"],
                          "locus": m["item"]["locus"]}
                      for i, m in enumerate(materials or [], 1)}
            bindings = bind_claims(text, by_ref, marker="م")
            weak = unsupported(bindings)
            if materials is None:
                weak = [w for w in weak if w[1] == "citation_missing"]
            if weak:
                return False, ("ادعاءاتٌ لا تسندها موادها: "
                               + "؛ ".join(f"«{c[:50]}» ({k}: {d})"
                                           for c, k, d in weak[:2]))
            return True, text
        return check

    # — التشغيلة —

    def run(self, question: str, data_policy: str = "regulated",
            form: str = "بحث") -> dict:
        """status/coverage يصفان استرجاع جبهات الخطة فقط، لا جودة المعنى.

        كل سؤال بحري أو كلمة معجمية جبهةٌ واحدة مهما تعددت موادها.
        complete يعني أن كل جبهة أعادت مادة؛ partial يعني تعذر بعضها.
        """
        if form not in COMPOSER_SYSTEM:
            raise PayloadRejected("service.form", "unknown_form",
                                  f"قالب غير مسجل: {form!r}")
        if self.ledger is None:
            self.ledger = self.run_ledger(question, form)
        # السجل يُتحقق قبل أي استهلاكٍ عرضًا — قيد مزوَّر كان يصير خلاصة
        # وفاتورة (قاتل تدقيق م٦). الذيل بلا مرساة حالُ تشغيلةٍ مقطوعة
        # مشروعة، أما كسر السلسلة فيغلق.
        if self.ledger.count():
            self.ledger.verify_chain()

        # هوية التشغيلة: القالب يغيّر التأليفَ لا التخطيط — فمفتاح
        # المخطط بلا قالبٍ كي لا يُدفع التخطيط مرتين (تدقيق م٦)
        run_key = self.run_key_of(self.provider.model, question, form)
        plan_key = hashlib.sha256(
            (self.provider.model + "|plan|" + question)
            .encode()).hexdigest()[:24]
        if not any(r.get("kind") == "service_run"
                   and r.get("run_key") == run_key
                   for r in (e["record"] for e in self.ledger.entries())):
            self.ledger.append({"kind": "service_run", "service": "research",
                                "run_key": run_key, "form": form,
                                "question": question})

        plan, _ = self._governed(
            f"rp-{plan_key}", PLANNER_SYSTEM, f"السؤال: {question}",
            data_policy, self._check_plan, max_output=300)

        # الجبهات: مشغّلات العقد بميزانية الخدمة وسجلها — فالفاتورة واحدة.
        # جبهةٌ أخفقت (عجزٌ معلن، استرجاعٌ صفري) تُقيَّد `front_failed`
        # برمزها وتستمر البقية — لا صمت ولا انهيار للتشغيلة كلها
        materials, failed = [], []
        completed = 0
        prior_failed = [e["record"] for e in self.ledger.entries()
                        if e["record"].get("kind") == "front_failed"]

        def front_failed(node, q, exc):
            rec = {"kind": "front_failed", "node": node, "question": q,
                   "code": "front_no_evidence", "reason": str(exc)[:200]}
            if rec not in prior_failed:   # العرض المحض لا يكرر نفس الفجوة
                self.ledger.append(rec)
                prior_failed.append(rec)
            failed.append(rec)

        # يُبتلع غيابُ المعرفة وحده (LookupError: عجز معلن/استرجاع صفري)
        # — أما سقوط البنية والرفض الحوكمي واستنفاد المحاولات فتُوقف
        # التشغيلةَ توقفًا قابلًا للاستئناف (كان ValueError يُبتلع فتُسلَّم
        # خلاصةٌ فقدت نصفها الحوكمي «مكتملةً» — تدقيق م٦)
        mar_node = self._mar(self.ledger) \
            if callable(self._mar) and not hasattr(self._mar, "answer") \
            else self._mar
        mar_node = mar_node or load_node_module("maritime").MaritimeNode(
            self.root, self.provider, self.budget, self.ledger)
        for q in plan["maritime"]:
            try:
                r = mar_node.answer(q, data_policy=data_policy)
            except (KeyError, IndexError):
                raise   # فساد البنية ليس غياب معرفة، وإن ورث LookupError
            except LookupError as exc:
                front_failed("maritime", q, exc)
                continue
            materials.append({
                "question": q, "node": "maritime",
                "item": r["answer"].fingerprint_payload(),
                "evidence": [{"locus": u["item"]["locus"],
                              "part": u["item"]["part"],
                              "ref": u["ref"]} for u in r["evidence"]],
            })
            completed += 1
        ling_node = self._ling or \
            load_node_module("linguistics").LinguisticsNode(self.root)
        for w in plan["lexicon"]:
            try:
                pages = ling_node.lookup(w, limit=2)[:2]
                if not pages:
                    raise LookupError(f"لا مدخل للكلمة في المعاجم: {w!r}")
            except (KeyError, IndexError):
                raise   # فشل قراءة بنيوي يُوقف التشغيلة ولا يصبح فجوة
            except LookupError as exc:
                front_failed("linguistics", w, exc)
                continue
            for page in pages:
                materials.append({"question": w, "node": "linguistics",
                                  "item": dict(page["item"]),
                                  "evidence": []})
            completed += 1
        if not materials:
            raise LookupError(f"كل الجبهات أخفقت ({len(failed)}) — "
                              "لا مواد للتأليف")

        coverage = {"planned": len(plan["maritime"]) + len(plan["lexicon"]),
                    "completed": completed, "failed": len(failed)}
        status = "partial" if failed else "complete"

        blocks = []
        for i, m in enumerate(materials, 1):
            body = _CITE_M.sub("", m["item"]["text"][:1500])  # تعقيم
            blocks.append(f"[م{i}] ({m['node']}: {m['question']}) "
                          f"{m['item']['part']} — {m['item']['locus']}:\n"
                          f"{body}")
        mat_digest = hashlib.sha256("".join(
            digest(m["item"]) for m in materials).encode()).hexdigest()[:16]
        brief, _ = self._governed(
            f"rc-{run_key}-{mat_digest}", COMPOSER_SYSTEM[form],
            f"السؤال: {question}\n\nالمواد:\n\n" + "\n\n".join(blocks)
            + "\n\nألّف من المواد وحدها مع أرقامها.",
            data_policy, self._check_brief(len(materials), materials),
            max_output=1100, correction_scope="-attribution-v2")

        # إعلان الفجوات حتمي خارج النموذج، ويُضاف أيضًا عند استهلاك
        # جواب قديم عرضًا. لا يتغير prompt ولا مفتاح المؤلف: الطلب
        # نفسه يعيد جوابه المحفوظ، ثم يُكسى بإعلان التغطية الحالي.
        if failed:
            gaps = "؛ ".join(
                f"تعذّر {'السؤال البحري' if f['node'] == 'maritime' else 'الاسترجاع المعجمي للكلمة'} "
                f"«{front_notice_text(f['question'])}»: "
                f"{front_notice_text(f['reason'])}" for f in failed)
            brief = (f"تنبيه: تغطية الاسترجاع جزئية؛ أُنجزت "
                     f"{completed} من {coverage['planned']} جبهات الخطة. "
                     f"{gaps}.\n\n{brief}")

        # الخلاصة مادةٌ بوعاء حقوق لا نصًّا سائبًا: ترث **بتقاطع** وسوم
        # موادها كلها وتمرّ بوابةَ الأصول — فلا تُوزَّع خلاصةٌ فوق موادَّ
        # داخلية-فقط (قاتل تدقيق م٦). عقد المادة أحاديُّ المصدر: مصدرُ
        # أول مادة، والتعدد في حزمة المواد المرافقة (حد م٤ المعلن).
        internal = all(m["item"]["use_internal"] for m in materials)
        distribution = all(m["item"]["use_distribution"] for m in materials)
        if not internal and not distribution:
            raise ValueError("مواد بلا حقِّ استخدامٍ مشترك — لا خلاصة")
        first = materials[0]["item"]
        brief_item = self.register.admitted_item(KnowledgeItem(
            text=brief, lang="ar", domain="research",
            use_internal=internal, use_distribution=distribution,
            source_id=first["source_id"],
            locus=f"تأليف خدمة البحث ({form}) عن {len(materials)} مادة",
            originality="derived", part=first["part"]))

        # الفاتورة **تشغيلية** بمفتاحها: تجمع قيود هذه التشغيلة (بعد
        # قيد service_run الخاص بها) لا تاريخ السجل كله — وكانت تراكمية
        # تنسب نداءات غيرها وتتضاعف عند الاستئناف (قاتل تدقيق م٦)
        recs_all = [e["record"] for e in self.ledger.entries()]
        calls = [r for r in recs_all
                 if r.get("kind") in ("ok", "error")]
        bill = {"kind": "service_bill", "service": "research",
                "run_key": run_key, "calls": len(calls),
                "settled_micros": sum(c.get("settled_micros", 0)
                                      for c in calls)}
        last_bill = next((r for r in reversed(recs_all)
                          if r.get("kind") == "service_bill"
                          and r.get("run_key") == run_key), None)
        if last_bill != bill:   # استئنافُ عرضٍ محض لا يكرر فاتورة مطابقة
            self.ledger.append(bill)
        self.ledger.anchor()   # رسوُّ التشغيلة عند ختامها
        return {"brief": brief, "brief_item": brief_item, "plan": plan,
                "materials": materials, "failed_fronts": failed,
                "status": status, "coverage": coverage,
                "bill": bill}
