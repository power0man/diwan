"""عقدة الفلسفة والمنطق — العقدة التخصصية الثالثة في منظومة «ديوان».

تختص بالاستدلال البرهاني، وفحص صحة الأقيسة المنطقية، وكشف المغالطات
الصورية وغير الصورية، وضبط الاصطلاح الفلسفي والمنطقي بالعربية الفصحى.

المخرجات مواد معرفية مشتقة (derived KnowledgeItem) محكومة بحلقة النواة execute()،
أو أحكام استدلالية صورية قطعية مبنية على قواعد المنطق القياسي والشرطي.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from core.acquisitions import SourceRegister
from core.attribution import bind_claims, unsupported
from core.canonical import PayloadRejected
from core.contracts import Message, Request
from core.knowledge import (KnowledgeItem, NodeManifest,
                            policy_within_ceiling, validated_item)
from core.run import execute

MANIFEST = NodeManifest(
    name="philosophy",
    contract_version=1,
    domains=("philosophy", "logic", "reasoning"),
    accepts=("query",),
    data_policy_ceiling="regulated",
)

MAX_ATTEMPTS = 3
INSUFFICIENT = "المقدمات المعطاة غير كافية لإنتاج نتيجة منطقية"

# القواعد الصورية الأساسية للأقيسة الشرطية
MODUS_PONENS = "وضع المقدم (Modus Ponens): استلزام صحيح ينتج عنه إثبات التالي"
MODUS_TOLLENS = "رفع التالي (Modus Tollens): استلزام صحيح ينتج عنه نفي المقدم"
AFFIRMING_CONSEQUENT = "مغالطة إثبات التالي (Affirming the Consequent): استدلال باطل صورياً"
DENYING_ANTECEDENT = "مغالطة إنكار المقدم (Denying the Antecedent): استدلال باطل صورياً"

SYSTEM = (
    "أنت عقدة الفلسفة والمنطق في نظام «ديوان». تفحص الحجج المنطقية، "
    "وتميز القياس الصحيح من الباطل، وتكشف المغالطات الصورية وغير الصورية، "
    "وتستند إلى قواعد البرهان والاستدلال الفلسفي الدقيق بالعربية الفصحى. "
    "إذا احتوى الطلب على شواهد أو مقدمات مرقمة [ش1] [ش2]، فالتزم بنسبتها بدقة، "
    "وإذا لم تكن المعطيات كافية للبرهان فاكتب: «" + INSUFFICIENT + "»."
)


def _norm_ar(text: str) -> str:
    """إزالة علامات التشكيل والتنوين وتوحيد الألفات."""
    t = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", text)
    t = re.sub(r"[إأآٱ]", "ا", t)
    return t.strip()


def _stem_ar(w: str) -> str:
    """تجريد ألف تنوين النصب الزائدة لضبط المقارنة اللفظية (مثل: ناطقاً -> ناطق)."""
    if len(w) > 3 and w.endswith("ا"):
        return w[:-1]
    return w


NEG_PARTICLES = {"ليس", "ليست", "لا", "لم", "لن", "ما", "غير"}
STOP_WORDS = {"هذا", "هذه", "ذلك", "تلك", "كان", "كانت", "هو", "هي", "شيء", "كائن", "ان", "انه", "انها", "قد"}

CONTINUITY_VERBS = {
    ("ما", "زال"), ("ما", "يزال"), ("ما", "فتئ"), ("ما", "يفتأ"),
    ("ما", "برح"), ("ما", "يبرح"), ("ما", "انفك"), ("ما", "ينفك"),
    ("لا", "يزال"), ("لم", "يزل"), ("لن", "يزال")
}

CATEGORICAL_BARBARA = "قياس حملي صحيح من الشكل الأول (Barbara): كل (أ) هو (ب)، و(ج) هو (أ) ينتج عنه أن (ج) هو (ب)"
CATEGORICAL_CELARENT = "قياس حملي صحيح سالب من الشكل الأول (Celarent): لا شيء من (أ) هو (ب)، و(ج) هو (أ) ينتج عنه أن (ج) ليس (ب)"


def detect_polarity(text: str) -> bool:
    """كشف القطبية: يعيد True إذا كانت العبارة منفية، وFalse إذا كانت موجبة.
    يستثني أفعال الاستمرار والتحول (ما زال، ما برح، ما فتئ، ما انفك) من النفي."""
    words = re.findall(r"\b\w+\b", _norm_ar(text))
    i = 0
    while i < len(words):
        w = words[i]
        if w in NEG_PARTICLES:
            if i + 1 < len(words) and (w, words[i + 1]) in CONTINUITY_VERBS:
                i += 2
                continue
            return True
        i += 1
    return False


def _extract_content_words(text: str) -> set[str]:
    """استخراج الكلمات الدلالية بعد استبعاد أدوات النفي وأفعال الاستمرار وكلمات التوقف."""
    words = re.findall(r"\b\w+\b", _norm_ar(text))
    result = set()
    i = 0
    while i < len(words):
        w = words[i]
        if i + 1 < len(words) and (w, words[i + 1]) in CONTINUITY_VERBS:
            # فعل استمرار: نتجاوز الأداة والفعل ونكتفي بالمعنى الخبري
            i += 2
            continue
        if w not in NEG_PARTICLES and w not in STOP_WORDS:
            result.add(_stem_ar(w))
        i += 1
    return result


def evaluate_conditional_syllogism(major: str, minor: str) -> dict:
    """فحص صوري حتمي للأقيسة الشرطية المتصلة:
    إذا كان (أ) فإن (ب).
    الحالات:
    - وضع المقدم (Modus Ponens): الصغرى تطابق المقدم في القطبية -> ينتج التالي (ب)
    - رفع التالي (Modus Tollens): الصغرى تخالف التالي في القطبية -> ينتج نفي المقدم
    - إثبات التالي (Affirming the Consequent): الصغرى تطابق التالي في القطبية -> مغالطة باطلة
    - إنكار المقدم (Denying the Antecedent): الصغرى تخالف المقدم في القطبية -> مغالطة باطلة
    """
    m_norm = _norm_ar(major)
    # ١. نمط الشرط برابط الفاء أو اللام أو مشتقاتها
    m = re.search(r"^(?:اذا|لو|كلما|ان)\s+(?:كان\s+)?(.+?)\s+(?:فانه|فانها|فان|فهو|فهي|لكان|ف|ل)\s+(.+?)[.،,]?$", m_norm)
    
    # ٢. نمط الشرط المقسم بفاصلة: «إذا اجتهد الطالب، نجح»
    if not m:
        m2 = re.search(r"^(?:اذا|لو|كلما|ان)\s+(?:كان\s+)?(.+?)[،,]\s*(.+?)[.،,]?$", m_norm)
        if m2:
            m = m2

    # ٣. نمط الشرط المجرد من الفاء والفاصلة: «إذا اجتهد الطالب نجح»
    if not m:
        m3 = re.search(r"^(?:اذا|لو|كلما|ان)\s+(?:كان\s+)?(.+)\s+(\w+)[.،,]?$", m_norm)
        if m3 and len(m3.group(1).split()) >= 1:
            m = m3

    if not m:
        return {"valid": False, "rule": "unknown_form", "analysis": "ليست صيغة شرطية متصلة قياسية"}

    ant_raw, con_raw = m.group(1).strip(), m.group(2).strip()

    minor_norm = _norm_ar(minor)
    minor_words = _extract_content_words(minor_norm)

    ant_words = _extract_content_words(ant_raw)
    con_words = _extract_content_words(con_raw)

    overlap_ant = len(minor_words & ant_words)
    overlap_con = len(minor_words & con_words)

    # حساب القطبية النسبية (القطبية المحسوبة على الصغرى مقارنة بالقطبية الأصلية للمقدم/التالي)
    ant_is_neg = detect_polarity(ant_raw)
    con_is_neg = detect_polarity(con_raw)
    minor_is_neg = detect_polarity(minor_norm)

    if overlap_ant > overlap_con and overlap_ant > 0:
        # صلة بالمقدم: هل تطابق الصغرى المقدم في قطبيته (إثبات المقدم) أم تخالفه (إنكار المقدم)؟
        affirms_antecedent = (minor_is_neg == ant_is_neg)
        if affirms_antecedent:
            return {"valid": True, "form": "modus_ponens", "rule": MODUS_PONENS, "conclusion": con_raw}
        else:
            return {"valid": False, "form": "denying_the_antecedent", "rule": DENYING_ANTECEDENT, "conclusion": None}
    elif overlap_con > overlap_ant and overlap_con > 0:
        # صلة بالتالي: هل تخالف الصغرى التالي في قطبيته (رفع التالي) أم تطابقه (إثبات التالي)؟
        denies_consequent = (minor_is_neg != con_is_neg)
        if denies_consequent:
            neg_conclusion = f"ليس ({ant_raw})" if not ant_is_neg else ant_raw
            return {"valid": True, "form": "modus_tollens", "rule": MODUS_TOLLENS, "conclusion": neg_conclusion}
        else:
            return {"valid": False, "form": "affirming_the_consequent", "rule": AFFIRMING_CONSEQUENT, "conclusion": None}

    return {"valid": False, "form": "inconclusive", "rule": "undetermined", "conclusion": None}


def _bare_word(w: str) -> str:
    s = _stem_ar(w)
    if s.startswith("ال") and len(s) > 3:
        s = s[2:]
    return s


def evaluate_categorical_syllogism(major: str, minor: str) -> dict:
    """فحص صوري حتمي للأقيسة الحملية (Aristotelian Categorical Syllogism):
    - الشكل الأول الموجب (Barbara): كل (أ) (ب)، و(ج) (أ) -> ينتج (ج) (ب).
    - الشكل الأول السالب (Celarent): لا شيء من (أ) (ب)، و(ج) (أ) -> ينتج (ج) ليس (ب).
    """
    maj_norm = _norm_ar(major)
    min_norm = _norm_ar(minor)

    # ١. الكبرى الكلية الموجبة: «كل إنسان فان» / «جميع الفلزات موصلة»
    m_univ_pos = re.search(r"^(?:كل|جميع|كافة)\s+(.+?)\s+(.+?)[.،,]?$", maj_norm)
    if m_univ_pos:
        middle_raw = m_univ_pos.group(1).strip()
        predicate_raw = m_univ_pos.group(2).strip()
        middle_bare = _bare_word(middle_raw)

        min_words = min_norm.split()
        if len(min_words) >= 2:
            match_idx = [i for i, w in enumerate(min_words) if _bare_word(w) == middle_bare]
            if match_idx:
                subject_words = [w for i, w in enumerate(min_words) if i not in match_idx]
                subject_raw = " ".join(subject_words).strip()
                conclusion = f"{subject_raw} {predicate_raw}".strip()
                return {
                    "valid": True,
                    "syllogism_type": "categorical",
                    "mood": "Barbara",
                    "rule": CATEGORICAL_BARBARA,
                    "conclusion": conclusion,
                    "terms": {"subject": subject_raw, "middle": middle_raw, "predicate": predicate_raw},
                }

    # ٢. الكبرى الكلية السالبة: «لا شيء من الجماد بحساس»
    m_univ_neg = re.search(r"^(?:لا\s+شيء\s+من|لا\s+احد\s+من|ليس\s+اي\s+من)\s+(.+?)\s+(?:بـ?|هو\s+)?(.+?)[.،,]?$", maj_norm)
    if m_univ_neg:
        middle_raw = m_univ_neg.group(1).strip()
        predicate_raw = m_univ_neg.group(2).strip()
        middle_bare = _bare_word(middle_raw)

        min_words = min_norm.split()
        if len(min_words) >= 2:
            match_idx = [i for i, w in enumerate(min_words) if _bare_word(w) == middle_bare]
            if match_idx:
                subject_words = [w for i, w in enumerate(min_words) if i not in match_idx]
                subject_raw = " ".join(subject_words).strip()
                conclusion = f"{subject_raw} ليس {predicate_raw}".strip()
                return {
                    "valid": True,
                    "syllogism_type": "categorical",
                    "mood": "Celarent",
                    "rule": CATEGORICAL_CELARENT,
                    "conclusion": conclusion,
                    "terms": {"subject": subject_raw, "middle": middle_raw, "predicate": predicate_raw},
                }

    return {"valid": False, "syllogism_type": "categorical", "rule": "unrecognized_categorical", "conclusion": None}



class PhilosophyNode:
    """مشغّل عقدة الفلسفة والمنطق في ديوان."""

    def __init__(self, root: Path, provider=None, budget=None, ledger=None,
                 register: SourceRegister | None = None):
        self.root = root
        self.provider = provider
        self.budget = budget
        self.ledger = ledger
        self.register = register or SourceRegister(self.root / "sources" / "acquisitions.jsonl")

    def manifest(self) -> NodeManifest:
        return MANIFEST

    def evaluate_logic(self, major_premise: str, minor_premise: str) -> dict:
        """تحليل منطقي صوري حتمي للأقيسة الشرطية والحملية."""
        res_cond = evaluate_conditional_syllogism(major_premise, minor_premise)
        if res_cond.get("valid") or res_cond.get("rule") != "unknown_form":
            return res_cond
        return evaluate_categorical_syllogism(major_premise, minor_premise)

    def handle_query(self, question: str, *, witnesses: list[KnowledgeItem] | None = None,
                     data_policy: str = "regulated", max_output: int = 800) -> KnowledgeItem:
        """استعلام تحليلي عبر حلقة النواة المحكومة execute().
        
        - عند وجود شواهد: فحص الإسناد (ق٢٦) وحساب الحقوق بالتقاطع وبوابة admitted_item.
        - عند غياب الشواهد (استدلال صوري حتمي محض): معفى صراحة من فحص الإسناد (ق٢٧، ق٢٦)
          ويصدر عن مصدر nodes.philosophy المسجل في سجل الأصول.
        """
        if not policy_within_ceiling(data_policy, MANIFEST.data_policy_ceiling):
            raise PayloadRejected("query.data_policy", "policy_exceeds_ceiling",
                                  f"سياسة {data_policy} تتجاوز سقف {MANIFEST.data_policy_ceiling}")

        witness_texts = []
        if witnesses:
            internal = all(w.use_internal for w in witnesses)
            distribution = all(w.use_distribution for w in witnesses)
            if not internal and not distribution:
                raise PayloadRejected("witnesses.rights", "no_common_rights",
                                      "شواهد بلا حقِّ استخدامٍ مشترك — لا مادة مشتقة")
            for i, w in enumerate(witnesses, 1):
                witness_texts.append(f"[ش{i}] {w.text} ({w.source_id}, {w.locus})")

        prompt_body = question
        if witness_texts:
            joined_witnesses = "\n".join(witness_texts)
            prompt_body = f"الشواهد المتاحة:\n{joined_witnesses}\n\nالمسألة المنطقية/الفلسفية:\n{question}"

        request = Request(
            messages=(
                Message("system", SYSTEM),
                Message("user", prompt_body),
            ),
            model=getattr(self.provider, "model", "default-logic"),
            model_version="unspecified",
            max_output=max_output,
            deadline_s=60,
            data_policy=data_policy,
            idempotency_key=f"philo-{hash(question) & 0xFFFFFFFF:08x}"
        )

        outcome = execute(request, self.provider, self.budget, self.ledger)
        if outcome.response is None or outcome.response.stop_reason != "complete":
            raise PayloadRejected("response", "generation_failed",
                                  outcome.error_code or "فشل إنتاج جواب منطقي متكامل")

        answer_text = outcome.response.content
        if witnesses:
            pages_by_ref = {
                i: {"text": w.text, "part": w.part, "locus": w.locus}
                for i, w in enumerate(witnesses, 1)
            }
            bindings = bind_claims(answer_text, pages_by_ref)
            bad = unsupported(bindings)
            if bad:
                claim, code, reason = bad[0]
                raise PayloadRejected("answer.attribution", code,
                                      f"{reason} — في الادعاء: {claim}")
            item = validated_item(KnowledgeItem(
                text=answer_text,
                lang="ar",
                domain="philosophy",
                use_internal=internal,
                use_distribution=distribution,
                source_id=witnesses[0].source_id,
                locus=f"استدلال منطقي عن {len(witnesses)} شاهدًا",
                originality="derived",
                part=witnesses[0].part,
            ))
            return self.register.admitted_item(item)
        else:
            rec = self.register.get("nodes.philosophy")
            if rec is None:
                raise PayloadRejected("item.source_id", "source_unacquired",
                                      "مصدر nodes.philosophy غير مسجل في سجل الأصول")
            acq = rec["acquisition"]
            item = validated_item(KnowledgeItem(
                text=answer_text,
                lang="ar",
                domain="philosophy",
                use_internal=acq["use_internal"],
                use_distribution=acq["use_distribution"],
                source_id="nodes.philosophy",
                locus="reasoning_engine",
                originality="derived",
                part="syllogism_analysis",
            ))
            return self.register.admitted_item(item)
