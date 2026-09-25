"""عقدة اللغويات/المعرِّب — النواة العربية العاملة (م٥، ق٢٠).

مشغّلان: (١) **البحث المعجمي** استرجاعٌ صرفٌ بلا نموذج — الكلمة تُطبَّع
وتُستفتى فيها المعاجم فتعود صفحاتُها موادَّ **أصلًا** بمواضع المطبوع
(ج/ص) كما وردت في مخزني المعاجم؛ (٢) **التعريب المحكوم بالمسرد** —
نداء نموذجٍ عبر حلقة م٠، والمصطلحات التي يعرفها مسردُ الحوكمة تُفرَض
فرضًا: مكافئٌ أجنبي وقع في النص ولم يُعرَّب بمصطلحه المعتمد = جوابٌ
مرفوض يُصوَّب محاولةً ثم يُرَدّ. المادة الناتجة `translated` بلغة أصلٍ
معلنة ومسردٍ محال إليه — «نقلُ المعارف سليمًا صحيحًا» فرضًا لا وصفًا.
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core.acquisitions import SourceRegister
from core.canonical import PayloadRejected
from core.contracts import Message, Request
from core.corpus import CorpusCatalog, CorpusFile
from core.glossary import Glossary
from core.knowledge import (KnowledgeItem, NodeManifest,
                            policy_within_ceiling)
from core.run import execute

MANIFEST = NodeManifest(
    name="linguistics",
    contract_version=1,
    domains=("arabic", "lexicon", "translation"),
    accepts=("query",),
    data_policy_ceiling="regulated",
)

MAX_ATTEMPTS = 3
LEXICON_STORES = ("lexicons", "lexicons-local")

# فرضُ المسرد فرضُ **معجمةٍ** لا صيغة: المرفوض استبدالُ اللفظ (مرفأ عن
# الميناء)، أما أداة التعريف وحروف الجر والعطف الملتصقة فتصريفٌ سليم —
# «في كل رحلة دولية» وفاءٌ بمصطلح «الرحلة الدولية» (لغم مُصاد حيًّا).
# والمطابقة بعد تطبيعٍ صرفي (تشكيل/تطويل/همزات) — «الربّان» و«رحلةٍ
# دولية» عربيةٌ أمينة لا تُرَدّ (عيب تدقيق م٥). الضميرُ المتصل وجمعُ
# التكسير يبقيان مردودين تشديدًا مقصودًا معلنًا.
_PROCLITIC = r"(?:[وف])?(?:[بكل]?ال|لل|[بكل])?"
_AR_LETTER = "ء-ي"
_AR_DIACRITICS = re.compile(r"[ً-ْٰـ]")
_HAMZA_ALEF = str.maketrans("أإآٱ", "اااا")


def _normalize_ar(s: str) -> str:
    return _AR_DIACRITICS.sub("", s).translate(_HAMZA_ALEF)


def _lexicon_variants(word: str) -> tuple[str, ...]:
    """بدائلُ إضافةٍ محدودة بعد إخفاق المطابقة الدقيقة لكلمة عربية.

    لا نقتطع «ال» من اللفظ: قد تكون أصليةً مثل «التقاء» أو تظهر بعد
    تطبيع «ألف». وللبادئ بها نجرّب العطف على اللفظ كاملًا أيضًا، دون
    افتراض أن الحرفين أداة تعريف. لا اشتقاق جذور ولا توسيع عبارات.
    """
    word = _normalize_ar(word)
    if not re.fullmatch(f"[{_AR_LETTER}]{{2,}}", word):
        return ()
    prefixes = ("و", "ف", "ال", "وال", "فال") \
        if word.startswith("ال") else ("ال", "وال", "فال")
    return tuple(prefix + word for prefix in prefixes)


def _term_pattern(term_ar: str) -> re.Pattern:
    words = []
    for w in _normalize_ar(term_ar).split():
        bare = w[2:] if w.startswith("ال") and len(w) >= 5 else w
        words.append(_PROCLITIC + re.escape(bare))
    return re.compile(f"(?<![{_AR_LETTER}])" + r"\s+".join(words)
                      + f"(?![{_AR_LETTER}])")


def term_in_text(term_ar: str, text_ar: str) -> bool:
    return _term_pattern(term_ar).search(_normalize_ar(text_ar)) is not None


def term_count(term_ar: str, text_ar: str) -> int:
    return len(_term_pattern(term_ar).findall(_normalize_ar(text_ar)))


_LEXICON_QUESTION_STOPWORDS = frozenset({
    "ما", "هو", "هي", "ماذا", "عن", "في", "من", "الى", "إلى", "على", "معنى", "تعريف",
    "اصل", "أصل", "كلمة", "لفظ", "جمع", "مفرد", "معجم", "المعجم", "معاجم", "المعاجم",
    "الوسيط", "المحيط", "القاموس", "لسان", "العرب", "العربية", "الحديثة", "اللغة", "لغويا",
    "لغوية", "وصف", "السفينة", "السفن", "الملاحة", "اجزاء", "أجزاء", "مصطلحات", "او", "أو",
    "بين", "الفرق", "فرق", "تلك", "ذلك", "هذا", "هذه", "شروط", "احكام", "أحكام",
})


def extract_headwords(query: str) -> list[str]:
    """استخراج الكلمات المرشحة للبحث المعجمي من سؤال طبيعي بعد تنقيح الشوائب الإجرائية."""
    clean = re.sub(r'[؟?!.,\"\'():؛]', " ", query)
    tokens = clean.split()
    candidates = []
    for t in tokens:
        norm = _normalize_ar(t)
        if norm not in _LEXICON_QUESTION_STOPWORDS and len(norm) >= 2:
            candidates.append(t)
    return candidates


def extract_lexicon_entry(text: str, headword: str) -> str:
    """استخراج السطر أو الفقرة الحاصرة للمدخل المستهدف من الصفحة المعجمية."""
    norm_hw = _normalize_ar(headword)
    lines = text.split("\n")
    for line in lines:
        if norm_hw in _normalize_ar(line):
            return line.strip()
    return text[:300].strip()


TRANSLATE_SYSTEM = (
    "أنت المعرِّب في نظام «ديوان». تنقل النص الأجنبي إلى عربية فصيحة "
    "سليمة أمينة للمعنى، **وتلتزم حرفيًّا** بجدول المصطلحات المعتمد "
    "المعطى: كل مصطلح أجنبي وارد في الجدول يُعرَّب بمقابله المعتمد "
    "نصًّا لا بغيره. لا تضف ولا تحذف معنى. أخرج الترجمة وحدها."
)


class LinguisticsNode:
    def __init__(self, root: Path, provider=None, budget=None, ledger=None,
                 search_fn=None, glossary: Glossary | None = None):
        self.root = root
        self.provider = provider
        self.budget = budget
        self.ledger = ledger
        if search_fn is None:
            from rebuild_index import search as search_fn
        self.search = search_fn
        self.glossary = glossary or Glossary(root / "glossaries" / "maritime.jsonl")
        self.register = SourceRegister(root / "sources" / "acquisitions.jsonl")
        self._catalogs = {
            s: CorpusCatalog(root / "corpus" / s / "_catalog.jsonl")
            for s in LEXICON_STORES
            if (root / "corpus" / s / "_catalog.jsonl").exists()}
        self._pages_cache: dict[str, dict] = {}

    # — البحث المعجمي: استرجاع صرف، موادُّ أصلٍ بمواضع المطبوع —

    def _store_pages(self, store: str, file: str) -> dict:
        key = f"{store}:{file}"
        if key not in self._pages_cache:
            self._pages_cache[key] = {
                p["item_digest"]: p
                for p in CorpusFile(self.root / file).pages()}
        return self._pages_cache[key]

    def lookup(self, word: str, limit: int = 6) -> list[dict]:
        """مداخل الكلمة في المعاجم — قيود صفحاتٍ أصلًا، مرتبة بالمعاجم."""
        word = word.strip()
        if not word:
            raise LookupError("كلمة فارغة")
        results, seen_pages = [], set()
        for store, catalog in self._catalogs.items():
            try:
                hits = self.search(word, limit=limit, corpus=store)
                if not hits and limit > 0:
                    seen_hits = set()
                    for variant in _lexicon_variants(word):
                        for hit in self.search(variant, limit=limit,
                                               corpus=store):
                            if hit["item_digest"] not in seen_hits:
                                hits.append(hit)
                                seen_hits.add(hit["item_digest"])
                            if len(hits) >= limit:
                                break
                        if len(hits) >= limit:
                            break
            except PayloadRejected as exc:
                if exc.code == "query_empty_after_normalization":
                    raise LookupError(
                        f"كلمة بلا محتوى قابل للبحث: {word!r}") from exc
                raise   # غيابُ الإسقاط (index_missing) يُرفض برمزه —
                        # كان يُبتلع فيُشخَّص كذبًا «لا مدخل للكلمة»
            current = catalog.current()
            for h in hits:
                rec = current.get(h["doc_id"])
                if rec is None:
                    continue
                page = self._store_pages(store, rec["file"]).get(
                    h["item_digest"])
                if page is not None and h["item_digest"] not in seen_pages:
                    results.append({**page, "store": store})
                    seen_pages.add(h["item_digest"])
        if not results:
            raise LookupError(f"لا مدخل للكلمة في المعاجم: {word!r}")
        return results

    def lookup_query(self, query: str, limit: int = 6) -> list[dict]:
        """بحث معجمي ذكي يقبل السؤال الطبيعي أو اللفظ المجرد عبر سلم ترشيح تدريجي."""
        query = query.strip()
        if not query:
            raise LookupError("استعلام فارغ")
        try:
            return self.lookup(query, limit=limit)
        except LookupError:
            pass

        candidates = extract_headwords(query)
        for cand in candidates:
            try:
                return self.lookup(cand, limit=limit)
            except LookupError:
                continue

        raise LookupError(f"لا مدخل لأي من مفردات الاستعلام في المعاجم: {query!r}")

    def explain(self, query: str, limit: int = 3) -> dict:
        """توليد شرح معجمي مكتمل ومسند بشواهد ومواضع المطبوع وفق ق٢٦."""
        candidates = extract_headwords(query) or [query]
        pages = self.lookup_query(query, limit=limit)
        first = pages[0]
        it = first["item"]
        store = first.get("store", "lexicon")
        doc = first.get("doc_id", store)
        locus = it.get("locus", "")
        full_text = it.get("text", "").strip()

        target_hw = candidates[0] if candidates else query
        entry_text = extract_lexicon_entry(full_text, target_hw) or full_text

        answer_text = (
            f"جاء في {doc} ({locus}):\n"
            f"{entry_text} [ش1]\n\n"
            f"الموضع الكامل في المطبوع: {locus}."
        )
        evidence = [{
            "item": {
                "text": full_text,
                "part": it.get("part", doc),
                "locus": locus,
                "domain": "lexicon",
            }
        }]
        return {
            "answer_text": answer_text,
            "pages": pages,
            "evidence": evidence,
            "headword": target_hw,
            "doc_id": doc,
            "locus": locus,
        }

    # — التعريب المحكوم بالمسرد —

    @staticmethod
    def _foreign_pattern(fe: str) -> re.Pattern:
        """مطابقة المكافئ الأجنبي بحدود كلمات — «ship» لا يقع في
        «shipment» ولا «crew» في «screws» (عيب تدقيق م٥: الاحتواء
        الحرفي ولّد إلزاماتٍ زائفة تُفسد الترجمة أو تردّها)."""
        return re.compile(r"(?<![a-z0-9])" + re.escape(fe.lower())
                          + r"(?![a-z0-9])")

    def glossary_bindings(self, text_foreign: str) -> list[tuple[str, str]]:
        """[(مكافئ أجنبي وقع في النص كلمةً، مصطلحه العربي المعتمد)]."""
        low = text_foreign.lower()
        out = []
        for rec in self.glossary.approved("maritime"):
            fe = rec["entry"]["foreign_equiv"].strip()
            if fe and self._foreign_pattern(fe).search(low):
                out.append((fe, rec["entry"]["term"]))
        return sorted(set(out))

    def translate(self, text_foreign: str, source_id: str, locus: str,
                  part: str = "", lang: str = "en",
                  data_policy: str | None = None) -> dict:
        """يعيد {"item": مادة معرَّبة، "bindings", "outcome"} — أو يرفض."""
        if not text_foreign.strip():
            raise ValueError("نص فارغ")
        policy = data_policy or MANIFEST.data_policy_ceiling
        if not policy_within_ceiling(policy, MANIFEST.data_policy_ceiling):
            raise PayloadRejected(
                "translate.data_policy", "policy_exceeds_ceiling",
                f"{policy!r} فوق سقف العقدة "
                f"{MANIFEST.data_policy_ceiling!r}")
        # الحقوق عند الباب لا بعده: قيدُ المصدر يُستفتى قبل أي نداء،
        # والمادة سترث وسومَه هو ثم تمرّ بوابةَ admitted_item الرسمية
        # (كانت الحقوق تُثبَّت يدويًّا بلا فحص — عيب تدقيق م٥)
        src = self.register.get(source_id)
        if src is None:
            raise PayloadRejected("translate.source_id", "source_unacquired",
                                  f"لا قيد استحواذ للمصدر: {source_id!r}")
        acq = src["acquisition"]
        bindings = self.glossary_bindings(text_foreign)
        low = text_foreign.lower()
        # تكافؤ الورود لا مجرد الحضور: كل ورودٍ للمكافئ الأجنبي يقابله
        # ورودٌ للمصطلح المعتمد — فمصطلحٌ ملحقٌ في سطرٍ شكلي لا يغطي
        # نصًّا كرره (عيب تدقيق م٥؛ حدّ معلن: ورودٌ يتيم قد يُحشر)
        required = {ar: len(self._foreign_pattern(fe).findall(low))
                    for fe, ar in bindings}
        table = "\n".join(f"- {fe} ⇐ {ar}" for fe, ar in bindings) \
            or "(لا مصطلحات ملزمة في هذا النص)"
        prompt = (f"جدول المصطلحات المعتمد:\n{table}\n\n"
                  f"النص المطلوب تعريبه:\n{text_foreign}")
        key_base = "tr-" + hashlib.sha256(
            (self.provider.model + "|" + text_foreign + "|" + table)
            .encode()).hexdigest()[:24]
        messages = (Message("system", TRANSLATE_SYSTEM),
                    Message("user", prompt))
        last_flaw = "لم يُنادَ"
        for n in range(1, MAX_ATTEMPTS + 1):
            key = key_base if n == 1 else f"{key_base}-a{n}"
            req = Request(messages=messages, model=self.provider.model,
                          model_version="ollama", max_output=900,
                          deadline_s=240.0, data_policy=policy,
                          idempotency_key=key)
            outcome = execute(req, self.provider, self.budget, self.ledger)
            if outcome.response is None:
                prior = self.ledger.find_by_idempotency_key(key)
                if prior and prior["record"].get("kind") == "error" \
                        and prior["record"].get("retryable") is True:
                    last_flaw = f"عطل قابل للإعادة: {outcome.error_code}"
                    continue
                raise RuntimeError(f"نداء التعريب فشل: {outcome.error_code}")
            text_ar = outcome.response.content.strip()
            missing = [ar for ar, n in required.items()
                       if term_count(ar, text_ar) < n]
            if not missing and outcome.response.stop_reason == "complete":
                item = self.register.admitted_item(KnowledgeItem(
                    text=text_ar, lang=lang, domain="maritime",
                    use_internal=acq["use_internal"],
                    use_distribution=acq["use_distribution"],
                    source_id=source_id, locus=locus,
                    originality="translated", part=part,
                    glossary_ref="maritime",
                ))
                return {"item": item, "bindings": bindings,
                        "outcome": outcome}
            last_flaw = ("ترجمة مبتورة"
                         if outcome.response.stop_reason != "complete"
                         else f"مصطلحات المسرد لم تُفرَض: {missing[:3]}")
            messages = messages + (
                Message("assistant", text_ar),
                Message("user", "ترجمتك مرفوضة: " + last_flaw
                        + ". أعدها كاملة مستعملًا كلَّ مصطلحٍ معتمد في "
                        "موضع مكافئه من النص نفسه — لا مُلحقًا في قائمة "
                        "أو سطر منفصل."))
        raise ValueError(f"استُنفدت المحاولات ({MAX_ATTEMPTS}) — {last_flaw}")
