"""عقدة الحوكمة البحرية — أول عقدة تخصصية (م٤، ق٢٠): إثبات القالب.

العقدة **تجيب موادَّ لا نثرًا**: استرجاعٌ من إسقاط المخزن ← شواهد مرقمة
(بعد تعقيم نمط الاستشهاد من نصوصها فلا يُقتبس زائفًا) ← نداءات نموذجٍ
محكومة عبر `execute()` (تحقق، خصوصية، حجز، تسوية، قيد) في **سلّم
محاولاتٍ محدود واعٍ للسجل** ← مادةُ جوابٍ مشتقة ترث حقوقَها **بتقاطع
وسوم شواهدها** + الشواهد نفسها موادَّ أصلًا بمواضعها وأرقام إحالتها.

سياسة الخصوصية لا تُثبَّت في الكود: الافتراض سقفُ عقد العقدة المسجل
(`regulated`) فتنحجز الحمولة بنيويًّا عن أي مزوّد غير محلي — والوسم
الأدنى قرارُ مستدعٍ صريح لا سهوُ ثابت (عيب تدقيق م٤/٢).

دلالة المفاتيح (عيوب م٤/٣،٤،٩،١٠): مفتاح المحاولة يشمل النموذج —
فنموذجٌ آخر فعلٌ آخر — والسلّم يمشي على المفاتيح `-a2` `-a3`: قيدُ
نجاحٍ يُستهلك نصُّه (ولا يُعاد نداؤه)، وقيدُ خطأٍ قابلٍ للإعادة
(`retryable=true` — المزوّد لم يرَ الطلب) يُجاوَز بمفتاح المحاولة
التالية، وغيرُ القابل يُحترم رفضًا. الرفضُ العقديّ (جواب بلا استشهاد)
ليس فعلًا مسلَّمًا — فالتصويب محاولةٌ تالية لا كسرُ عدم التكرار.

كامل إسناد الجواب المشتق حزمتُه: قائمة الشواهد المرقمة المرافقة —
والمادة نفسها تحمل مصدر أول شاهد وموضعًا وصفيًّا (حدٌّ معلن: عقد
المادة أحاديُّ المصدر، والتعدد في الحزمة).
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core.attribution import auto_repair_attribution, bind_claims, unsupported
from core.canonical import PayloadRejected
from core.contracts import Message, Request
from core.corpus import CorpusCatalog, CorpusFile
from core.knowledge import (KnowledgeItem, NodeManifest,
                            policy_within_ceiling, validated_item)
from core.run import execute

MANIFEST = NodeManifest(
    name="maritime",
    contract_version=1,
    domains=("maritime",),
    accepts=("query",),
    data_policy_ceiling="regulated",
)

MAX_ATTEMPTS = 3
INSUFFICIENT = "الشواهد غير كافية"


class RetrievalFailed(RuntimeError):
    """عطبُ بحثٍ — وليس غيابَ معرفة.

    لا يرث `LookupError` عمدًا: `services/research.py` يبتلع `LookupError`
    بوصفه «عجزًا معلنًا أو استرجاعًا صفريًّا»، فلو ورثه لعاد العطبُ يُقرأ
    امتناعًا. وهو ما يجعل هذا التمييزَ حاملًا لا تجميلًا.
    """

_CITE = re.compile(r"\[\s*ش\s*([\d٠-٩]+)\s*\]")
_CITE_MARKER = re.compile(r"\[\s*ش[^\[\]]*(?:\]|(?=\[|$))")
_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_PUNCT = re.compile(r"[؟?!.,،؛:()«»\"'—_]")
# أدوات الاستفهام والوصل الشائعة — تحضيرُ السؤال فهمٌ يخص العقدة لا النواة
_STOP = frozenset({"ما", "ماذا", "ماهو", "ماهي", "هل", "كيف", "متى", "أين",
                   "اين", "لماذا", "الذي", "التي", "عن", "من", "في", "على",
                   "إلى", "الى", "أو", "او", "ثم", "بين", "عند", "مع", "هو",
                   "هي", "متن", "المقصود", "يقصد", "تعريف", "الفرق", "حيث",
                   "وأين", "وكيف", "ولماذا", "وهل", "وماذا", "وهو", "وهي", "وما",
                   "وفق", "وفقا", "بموجب", "بحسب", "حسب", "تحدد", "المحددة",
                   "المنصوص", "عليها", "المادة", "مادة", "دون", "الحصول", "حصول"})
_PREFIX = re.compile(r"^(?:[وفبك]ال|لل|ل(?=ال))(?=[؀-ۿ]{3,})")

_JURIDICAL_MAP: dict[str, tuple[str, ...]] = {
    "محظورة": ("يحظر",),
    "محظور": ("يحظر",),
    "حظر": ("يحظر",),
    "عقوبة": ("المخالفة الجسيمة", "يعاقب"),
    "عقوبات": ("المخالفة الجسيمة", "يعاقب"),
    "مخالفة": ("المخالفة الجسيمة", "يعاقب"),
    "مخالفات": ("المخالفة الجسيمة", "يعاقب"),
    "شروط": ("يشترط",),
    "شرط": ("يشترط",),
    "التزامات": ("يلتزم",),
    "التزام": ("يلتزم",),
    "المانعة": ("المقاومة", "مقاومة"),
    "مانعة": ("المقاومة", "مقاومة"),
}
_JURIDICAL_PRIORITY = {"المخالفة الجسيمة": 0, "يحظر": 0, "يشترط": 1, "يعاقب": 2, "يلتزم": 3}

SYSTEM = (
    "أنت عقدة الحوكمة البحرية في نظام «ديوان». تجيب حصرًا من الشواهد "
    "المرقمة المعطاة، وتذكر بعد كل معلومة رقمَ شاهدها بالصيغة [ش1] "
    "[ش2]... معلومةٌ بلا شاهد لا تُكتب. إن لم تكفِ الشواهد فاكتب "
    f"حرفيًّا: «{INSUFFICIENT}» بلا أي استشهاد. أجب إجابة وافية ومكتملة "
    "تغطي كافة شقوق السؤال بدقة وتذكر الأرقام والمحددات النصية كما هي في الشواهد دون اختصار مخل.\n"
    "قواعد إلزامية للإسناد وصياغة الجواب:\n"
    "1. ابدأ بالجواب المباشر فوراً دون أي عناوين أو مقدمات إنشائية، وممنوع صياغة جمل تمهيدية تعيد صياغة السؤال "
    "(مثل: الفرق بين كذا وكذا هو، أو ملزمة بتشغيل كذا، أو يكمن الفرق في)، فكل سطر أو مقطع يُعد ادعاءً قانونياً مستقلاً ويجب حتماً أن يُختم برقم شاهده مباشرة.\n"
    "2. التزم بنصوص الشواهد وألفاظها بدقة واقتبس منها نصياً حرفياً وتجنب إعادة الصياغة التعبيرية أو وضع جمل استنتاجية عامة.\n"
    "3. لا تضع أي سطر تمهيدي أو عنوان دون ختمه برقم الشاهد، وابدأ بنص الحكم أو التعريف من الشاهد مباشرة."
)


def _keywords(question: str) -> list[str]:
    """كلماتُ المحتوى: تسقط أدوات الاستفهام والقصار، وتُرَدّ السوابق
    (بال/وال/كال/فال) إلى «ال» فقط حين يبقى بعدها جذرٌ وافٍ — فلا
    تُشوَّه «والي» و«بالغة» (عيب تدقيق م٤/٨)."""
    out = []
    for t in _PUNCT.sub(" ", question).split():
        t = _PREFIX.sub("ال", t)
        if len(t) >= 3 and t not in _STOP:
            out.append(t)
    return out


_WAW_EXEMPT = frozenset({"وزير", "وزارة", "وقود", "وثيقة", "وحدة", "وكيل", "وصول", "وضع", "وزن", "وفاة", "وجوب"})
_BAA_EXEMPT = frozenset({"بحر", "بحري", "بحرية", "بحارة", "بدء", "بناء", "بند", "بيان", "بضائع", "بيع", "بديلة", "بيئة"})
_LAM_EXEMPT = frozenset({"لائحة", "لوائح", "لجنة", "لسان", "لوح", "لوحة", "لحظة", "لازم", "لزوم"})


def _strip_waw(t: str) -> str:
    if t.startswith("و") and len(t) >= 4 and t not in _WAW_EXEMPT:
        return t[1:]
    return t


def _strip_baa(t: str) -> str:
    if t.startswith("ب") and len(t) >= 4 and t not in _BAA_EXEMPT:
        return t[1:]
    return t


def _strip_lam(t: str) -> str:
    if t.startswith("ل") and len(t) >= 4 and t not in _LAM_EXEMPT and not t.startswith("لا"):
        return t[1:]
    return t


def _extract_queries(question: str) -> list[str]:
    """استخراج استعلامات السلم المتدرجة: الموضوع أولاً، ثم الكلمات كاملة، ثم الأثقل دلالة."""
    # استخراج النسخة العربية النقية الخالية من الاختصارات اللاتينية (مثل DOC, SMC)
    clean_q = re.sub(r"[a-zA-Z]+", " ", question)
    raw_tokens = [t for t in _PUNCT.sub(" ", question).split()
                  if t not in _STOP and len(t) >= 3]
    ar_raw = [t for t in _PUNCT.sub(" ", clean_q).split()
              if t not in _STOP and len(t) >= 3]

    stripped_tokens = [_strip_lam(_strip_baa(_strip_waw(t))) for t in raw_tokens
                       if _strip_lam(_strip_baa(_strip_waw(t))) not in _STOP]
    ar_stripped = [_strip_lam(_strip_baa(_strip_waw(t))) for t in ar_raw
                   if _strip_lam(_strip_baa(_strip_waw(t))) not in _STOP]

    norm_tokens = [_PREFIX.sub("ال", t) for t in stripped_tokens
                   if _PREFIX.sub("ال", t) not in _STOP]
    ar_norm = [_PREFIX.sub("ال", t) for t in ar_stripped
               if _PREFIX.sub("ال", t) not in _STOP]

    queries = []

    # التوسع الدلالي القانوني (الأفعال التشريعية أولاً لصيد نصوص الحظر والجزاء)
    added_juridical = []
    for t in norm_tokens + raw_tokens:
        stem = t.removeprefix("ال")
        for k, v_tuple in _JURIDICAL_MAP.items():
            k_stem = k.removeprefix("ال")
            if stem == k_stem or t == k:
                for v in v_tuple:
                    if v not in added_juridical:
                        added_juridical.append(v)

    added_juridical.sort(key=lambda term: _JURIDICAL_PRIORITY.get(term, 10))

    if added_juridical:
        juridical_stems = {k.removeprefix("ال") for k in _JURIDICAL_MAP}
        key_tokens = [t for t in norm_tokens + raw_tokens
                      if t.removeprefix("ال") not in juridical_stems
                      and t not in ("المادة", "مادة", "النظام", "اللائحة", "وفق", "بموجب", "دون", "الحصول", "حصول")][:8]
        key_bigrams = []
        for i in range(len(key_tokens) - 1):
            key_bigrams.append(f"{key_tokens[i]} {key_tokens[i+1]}")

        for j_term in added_juridical:
            for bg in key_bigrams:
                queries.append(f"{j_term} {bg}")
            for kt in key_tokens:
                queries.append(f"{j_term} {kt}")

    # استعلامات عربية نقية إن كان في السؤال كلمات أو اختصارات لاتينية
    if ar_norm and ar_norm != norm_tokens:
        queries.append(" ".join(ar_norm[:3]))
        queries.append(" ".join(ar_norm[:2]))
        queries.append(" ".join(ar_norm))

    if norm_tokens:
        queries.append(" ".join(norm_tokens[:3]))
        queries.append(" ".join(norm_tokens[:4]))
        queries.append(" ".join(norm_tokens[:2]))
        queries.append(" ".join(norm_tokens))
        for i in range(len(norm_tokens) - 1):
            queries.append(f"{norm_tokens[i]} {norm_tokens[i+1]}")
    if stripped_tokens and stripped_tokens != norm_tokens:
        queries.append(" ".join(stripped_tokens[:2]))
        queries.append(" ".join(stripped_tokens[:3]))
        queries.append(" ".join(stripped_tokens[:4]))
        queries.append(" ".join(stripped_tokens))
    if raw_tokens and raw_tokens != stripped_tokens:
        queries.append(" ".join(raw_tokens[:2]))
        queries.append(" ".join(raw_tokens[:3]))
        queries.append(" ".join(raw_tokens[:4]))
        queries.append(" ".join(raw_tokens))

    reg_match = re.search(r"(?:لائحة|نظام|معاهدة|اتفاقية|مدونة)\s+([^\s]+(?:\s+[^\s]+){1,3})", question)
    if reg_match and norm_tokens:
        reg_words = [t for t in _PUNCT.sub(" ", reg_match.group(1)).split() if t not in _STOP][:2]
        if reg_words:
            reg_anchor = " ".join(reg_words)
            queries.append(f"{norm_tokens[0]} {reg_anchor}")
            if len(norm_tokens) > 1:
                queries.append(f"{norm_tokens[0]} {norm_tokens[1]} {reg_anchor}")

    core3 = " ".join(sorted(set(norm_tokens), key=len, reverse=True)[:3]) if norm_tokens else ""
    if core3 and core3 not in queries:
        queries.append(core3)

    seen, unique = set(), []
    for q in queries:
        q_clean = " ".join(q.split())
        if q_clean and q_clean not in seen:
            seen.add(q_clean)
            unique.append(q_clean)
    return unique


class MaritimeNode:
    """مشغّل السؤال/الجواب — كل نداء نموذجٍ عبر الحلقة المحكومة."""

    def __init__(self, root: Path, provider, budget, ledger,
                 search_fn=None, top_k: int = 7, auto_repair: bool = False):
        self.root = root
        # الإصلاحُ الآليّ للإسناد موقوفٌ افتراضيًّا (ق٦٢): يُلحق الإحالةَ
        # بادعاءٍ ينفي شاهدَه فيصير مسنَدًا. ويعود حين يفحص القطبية (غ٦).
        self.auto_repair = auto_repair
        self.provider = provider
        self.budget = budget
        self.ledger = ledger
        if search_fn is None:
            from rebuild_index import search as search_fn  # إسقاط المخزن
        self.search = search_fn
        self.top_k = top_k
        self.catalog = CorpusCatalog(root / "corpus" / "maritime" / "_catalog.jsonl")

    # — الاسترجاع: صفحات الشواهد موادَّ بمواضعها —

    def _search(self, q: str, limit: int, match_any: bool = False):
        """يبحث، أو يرفع `RetrievalFailed` — ولا يُرجع «لا شيء» عن عطب.

        كان هذا الموضع يبتلع **كلَّ** استثناءٍ مرتين ويعود `[]`. وأثرُه أن
        عطبَ بحثٍ (قاعدةٌ مفقودة، فهرسٌ فاسد، استعلامٌ يرفع) يُقرأ «لا شواهد
        في المخزن» فيمتنع النظام — **فيُحسب امتناعًا حكيمًا وهو عطبٌ صامت**.
        وهو فشلٌ مفتوحٌ في قلب المقياس الذي تقوم عليه أرقامُ م١٤: كلُّ خليةٍ
        سقطت بعطبِ بحثٍ دخلت الحساب «امتناعًا».

        والقاعدةُ الصحيحة مكتوبةٌ في المستودع نفسه (`services/research.py`):
        «فسادُ البنية ليس غيابَ معرفة، وإن ورث LookupError». ولذلك **لا
        يُستعمل `LookupError` هنا**: الخدمةُ تبتلعه بوصفه غيابَ معرفة.
        و`RetrievalFailed` يعبُر فيُقيَّد خطأَ تشغيلٍ لا امتناعًا (ق٢٥).

        ويبقى `TypeError` وحده مُلتقَطًا: فهو مُوافِقةُ توقيعٍ لدالّةِ بحثٍ
        لا تعرف `match_any`، لا عطبُ بحث.
        """
        try:
            return list(self.search(q, limit=limit, match_any=match_any))
        except TypeError:
            pass
        except Exception as exc:
            raise RetrievalFailed(f"عطبُ بحثٍ في المخزن: {type(exc).__name__}: "
                                  f"{str(exc)[:200]}") from exc
        try:
            return list(self.search(q, limit=limit))
        except Exception as exc:
            raise RetrievalFailed(f"عطبُ بحثٍ في المخزن: {type(exc).__name__}: "
                                  f"{str(exc)[:200]}") from exc

    def _evidence(self, question: str) -> list[dict]:
        kw = _keywords(question)
        if not kw:
            raise LookupError(f"سؤال بلا كلمات محتوى: {question!r}")
        queries = _extract_queries(question)
        cand_limit = max(self.top_k * 10, 50)
        hits, seen_h = [], set()

        try:
            for q_str in queries:
                for h in self._search(q_str, limit=cand_limit):
                    if h["item_digest"] not in seen_h:
                        seen_h.add(h["item_digest"])
                        hits.append(h)
            if len(hits) < self.top_k and queries:
                for h in self._search(queries[0], limit=cand_limit, match_any=True):
                    if h["item_digest"] not in seen_h:
                        seen_h.add(h["item_digest"])
                        hits.append(h)
        except PayloadRejected as exc:
            raise LookupError(f"استعلام غير قابل للبحث: {exc}") from exc

        pages, seen_files = [], {}
        current = self.catalog.current()
        for h in hits:
            rec = current.get(h["doc_id"])
            if rec is None:
                continue
            if rec["file"] not in seen_files:
                seen_files[rec["file"]] = {
                    p["item_digest"]: p
                    for p in CorpusFile(self.root / rec["file"]).pages()}
            page = seen_files[rec["file"]].get(h["item_digest"])
            if page is not None:
                pages.append(page)
        has_def_indicator = any(w in question for w in ["ما الفرق", "الفرق بين", "تعريف", "المقصود", "يقصد", "ما معنى", "ما هو", "ما هي"])
        has_substantive_indicator = any(w in question for w in [
            "عقوبة", "عقوبات", "شروط", "شرط", "متطلبات", "التزامات",
            "إجراءات", "حالات", "صلاحيات", "مخالفة", "جزاء", "المحظورة", "محظورة"
        ])
        is_def_q = has_def_indicator and not has_substantive_indicator

        def is_definitional_locus(locus: str) -> bool:
            l = locus.strip()
            return (l in ("المادة الأولى", "المادة (1)", "المادة 1", "التمهيد", "المادة الثانية")
                    or "تعريف" in l or "# المادة الأولى" in l)

        DISTINCTIVE_STOP = frozenset({"اللائحة", "لائحة", "البحرية", "بحري", "بحرية", "السفن", "سفينة", "السفينة", "نظام", "النظام"})
        distinctive_kw = [k for k in kw if k not in DISTINCTIVE_STOP]

        def is_relevant_def(p):
            loc = p["item"]["locus"]
            if not is_definitional_locus(loc):
                return False
            txt = p["item"]["text"]
            part = p["item"]["part"]
            doc_id = p["doc_id"]
            return any(k in txt or k in part or k in doc_id for k in distinctive_kw)

        def sort_key(p):
            dom = p["item"]["domain"]
            if dom == "maritime-regulation":
                if is_def_q and is_relevant_def(p):
                    return 0
                return 1
            return 2

        pages.sort(key=sort_key)
        picked, seen_base, doc_counts = [], set(), {}
        for p in pages:
            base = (p["doc_id"], p["item"]["locus"].split(" — ")[0])
            if base in seen_base or doc_counts.get(p["doc_id"], 0) >= 2:
                continue
            seen_base.add(base)
            doc_counts[p["doc_id"]] = doc_counts.get(p["doc_id"], 0) + 1
            picked.append(p)
            if len(picked) == self.top_k:
                break
        if len(picked) < self.top_k:
            for p in pages:
                base = (p["doc_id"], p["item"]["locus"].split(" — ")[0])
                if base in seen_base:
                    continue
                seen_base.add(base)
                picked.append(p)
                if len(picked) == self.top_k:
                    break
        return picked

    # — سلّم النداءات المحكوم الواعي للسجل —

    def _governed_call(self, key: str, messages: tuple[Message, ...],
                       data_policy: str):
        req = Request(messages=messages, model=self.provider.model,
                      model_version="ollama", max_output=1200,
                      deadline_s=240.0, data_policy=data_policy,
                      idempotency_key=key)
        return execute(req, self.provider, self.budget, self.ledger)

    def _prior_retryable(self, key: str) -> bool:
        prior = self.ledger.find_by_idempotency_key(key)
        return (prior is not None
                and prior["record"].get("kind") == "error"
                and prior["record"].get("retryable") is True)

    # — الجواب: مادة مشتقة ملزمة الاستشهاد + شواهدها المرقمة —

    def answer(self, question: str, data_policy: str | None = None) -> dict:
        """يعيد {"answer" مادة مشتقة، "evidence" قيود صفحات مرقمة بـref،
        "outcome"}. جوابٌ بلا استشهاد صحيح، أو مبتور، أو معلنُ العجز —
        لا يُسلَّم مادةً (امتداد «جواب المزود لا يُصدَّق» للعقدة)."""
        policy = data_policy or MANIFEST.data_policy_ceiling
        # سقف عقد العقدة يُفرض على المستدعي المباشر أيضًا لا على مسار
        # الموجّه وحده (تدقيق م٦) — فوق السقف يُرَدّ برمزه قبل أي عمل
        if not policy_within_ceiling(policy, MANIFEST.data_policy_ceiling):
            raise PayloadRejected(
                "answer.data_policy", "policy_exceeds_ceiling",
                f"{policy!r} فوق سقف العقدة "
                f"{MANIFEST.data_policy_ceiling!r}")
        pages = self._evidence(question)
        if not pages:
            raise LookupError(f"لا شواهد في المخزن للسؤال: {question!r}")
        blocks = []
        for i, p in enumerate(pages, 1):
            it = p["item"]
            body = _CITE.sub("", it["text"][:5000])  # تعقيم نمط الاستشهاد
            blocks.append(f"[ش{i}] {it['part']} — {it['locus']}:\n{body}")
        prompt = (f"السؤال: {question}\n\nالشواهد:\n\n" + "\n\n".join(blocks)
                  + "\n\nأجب من الشواهد وحدها إجابة مفصلة كاملة تستوفي جميع أوجه السؤال وأرقامه وشروطه، وابدأ بالجواب مباشرة دون عناوين أو جمل تمهيدية تعيد صياغة السؤال، واقتبس ألفاظ الشواهد نصاً، وكل معلومة أو سطر يُختم برقم شاهده [ش1].")
        key_base = "mq-" + hashlib.sha256(
            (self.provider.model + "|" + question + "|"
             + "".join(p["item_digest"] for p in pages))
            .encode()).hexdigest()[:24]

        messages = (Message("system", SYSTEM), Message("user", prompt))
        last_flaw = "لم يُنادَ"
        last_code = None
        correction_scope = ""
        for n in range(1, MAX_ATTEMPTS + 1):
            key = key_base if n == 1 else f"{key_base}{correction_scope}-a{n}"
            outcome = self._governed_call(key, messages, policy)
            last_code = None
            if outcome.response is None:
                if self._prior_retryable(key):
                    last_flaw = f"عطل قابل للإعادة: {outcome.error_code}"
                    continue      # مفتاحُ محاولةٍ تالٍ = نداء جديد مشروع
                raise RuntimeError(f"نداء النموذج فشل: {outcome.error_code}")
            text = outcome.response.content.strip()

            if INSUFFICIENT in text:
                raise LookupError(f"العقدة أعلنت العجز: {INSUFFICIENT} — "
                                  f"({question!r})")
            cited = {int(c.translate(_AR_DIGITS))
                     for c in _CITE.findall(text)}
            malformed = [m for m in _CITE_MARKER.findall(text)
                         if not _CITE.fullmatch(m)]
            valid = sorted(i for i in cited if 1 <= i <= len(pages))
            invalid = sorted(cited - set(valid))
            weak = []
            if valid and not invalid and not malformed \
                    and outcome.response.stop_reason == "complete":
                used = [{**pages[i - 1], "ref": i} for i in valid]
                # **الإسناد يُفحص لا يُفترض** (ق٢٦): رقمُ الشاهد صالحٌ
                # لا يعني أن الشاهد يقول الكلام — فتُربط الادعاءات
                # بمقتطفاتها حتميًّا، والمختلَقُ يُرَدّ تصويبًا
                bindings = bind_claims(text, {
                    u["ref"]: {"text": u["item"]["text"],
                               "part": u["item"]["part"],
                               "locus": u["item"]["locus"]} for u in used})
                weak = unsupported(bindings)
                if weak and self.auto_repair:
                    # محاولة الإصلاح الآلي التوليدي المعزز قبل إعلان الرفض (م١٥)
                    all_pages_map = {idx: {"text": p["item"]["text"],
                                           "part": p["item"]["part"],
                                           "locus": p["item"]["locus"]}
                                     for idx, p in enumerate(pages, 1)}
                    repaired_text, rep_bindings, repaired = auto_repair_attribution(
                        text, all_pages_map)
                    if repaired and not unsupported(rep_bindings):
                        text = repaired_text
                        bindings = rep_bindings
                        cited = {int(c.translate(_AR_DIGITS)) for c in _CITE.findall(text)}
                        valid = sorted(i for i in cited if 1 <= i <= len(pages))
                        used = [{**pages[i - 1], "ref": i} for i in valid]
                        weak = []
                if not weak:
                    return {"answer": self._derived_item(text, used),
                            "evidence": used,
                            "bindings": [b.payload() for b in bindings],
                            "outcome": outcome}
            if weak:
                last_code = weak[0][1]
                last_flaw = ("ادعاءاتٌ لا تسندها شواهدها: "
                             + "؛ ".join(f"«{c[:60]}» ({d})"
                                         for c, _k, d in weak[:2]))
                correction_scope = "-attribution-v2"
            elif invalid or malformed:
                last_code = "citation_malformed" if malformed else "citation_out_of_range"
                last_flaw = ((f"صيغة إحالة معطوبة: {malformed}؛ " if malformed else "")
                             + (f"إحالات خارج الشواهد: {invalid}؛ " if invalid else "")
                             + f"الأرقام المتاحة من 1 إلى {len(pages)}")
                # التصويب الجديد له مفتاح مستقل عن تصويب قديم كان يعامل
                # الإحالة الملفقة كغياب استشهاد؛ أول جواب يبقى قابلًا للعرض.
                correction_scope = "-citations-v2"
            else:
                last_flaw = ("جواب مبتور (max_output)"
                             if outcome.response.stop_reason != "complete"
                             else "جواب بلا استشهاد صحيح")
            messages = messages + (
                Message("assistant", text),
                Message("user", "جوابك مرفوض: " + last_flaw + ".\n"
                        "أعد الصياغة مع الالتزام التام بالآتي:\n"
                        "1. احذف تماماً أي عنوان أو تمهيد إنشائي غير مسند (مثل: الفرق هو: أو المقصود:).\n"
                        "2. ابدأ بنص الحكم أو المعلومة مباشرة، وكل سطر أو فقرة تُختم برقم شاهدها [ش1].\n"
                        "3. التزم بالألفاظ والعبارات الواردة في الشواهد نصاً وتجنب إعادة الصياغة التعبيرية الفضفاضة.\n"
                        "4. لا تكتب أي معلومة إلا إن كان نصها في الشاهد الذي تحيل إليه، واحذف ما لا تجده."))
        if last_code:
            raise PayloadRejected("answer.citations", last_code,
                                  f"استُنفدت المحاولات ({MAX_ATTEMPTS}) — {last_flaw}")
        raise ValueError(f"استُنفدت المحاولات ({MAX_ATTEMPTS}) — {last_flaw}")

    def _derived_item(self, text: str, used: list[dict]) -> KnowledgeItem:
        # الحقوق بالتقاطع: المشتق لا يملك حقًّا لم تمنحه شواهده كلها
        internal = all(u["item"]["use_internal"] for u in used)
        distribution = all(u["item"]["use_distribution"] for u in used)
        if not internal and not distribution:
            raise ValueError("شواهد بلا حقِّ استخدامٍ مشترك — لا مادة مشتقة")
        first = used[0]["item"]
        return validated_item(KnowledgeItem(
            text=text, lang="ar", domain="maritime",
            use_internal=internal, use_distribution=distribution,
            source_id=first["source_id"],
            locus=f"تأليف عقدة الحوكمة عن {len(used)} شاهدًا مرقمًا",
            originality="derived",
            part=first["part"],
        ))
