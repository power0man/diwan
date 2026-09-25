# -*- coding: utf-8 -*-
"""حزمة الفحوصات المعيارية للنواة اللسانية العربية الأصيلة (tests/test_arabic_linguistic_core.py).

تغطي هذه الحزمة:
1. استخراج الجذور والأوزان ورد جموع التكسير (core/linguistics/morphology.py).
2. الضبط الصواتي وفك لبس المتشابهات رسماً بنقاط الأساس (core/linguistics/tashkeel.py).
3. التدقيق النحوي والتركيبي لمخرجات اللغة العربية (core/linguistics/syntax_guard.py).
4. تكامل الاسترجاع المعرفي الموسع بالمفردات والجذور.
"""
from __future__ import annotations

import pytest

from core.linguistics import (
    Analysis,
    MorphologicalAnalysis,
    analyze,
    extract_pattern,
    extract_root,
    singularize_broken_plural,
    strip_tashkeel,
    has_tashkeel,
    tashkeel_similarity_bp,
    disambiguate_homographs,
    check_syntax,
    SyntaxVerdict,
)
from core.hybrid_retrieval import HybridRetriever
from tests.private_stores import needs_corpus


# ==============================================================================
# ١. فحوصات المحلل الصرفي والجذور والأوزان وجموع التكسير
# ==============================================================================

class TestMorphologyCore:
    @pytest.mark.parametrize("word,expected_root", [
        ("كتب", "كتب"),
        ("كاتب", "كتب"),
        ("مكتوب", "كتب"),
        ("مكتب", "كتب"),
        ("مكتبة", "كتب"),
        ("استكتاب", "كتب"),
        ("مكاتب", "كتب"),
        ("الكتاب", "كتب"),
        ("والكتاب", "كتب"),
        ("وبالكتاب", "كتب"),
        ("الكاتبون", "كتب"),
        ("والمكتبات", "كتب"),
        ("كتبها", "كتب"),
    ])
    def test_trilateral_root_family(self, word: str, expected_root: str):
        """فحص اشتقاق أسرة الجذر الثلاثي كاملة وتفكيك السوابق واللواحق."""
        res = analyze(word)
        assert res.root == expected_root
        assert extract_root(word) == expected_root

    @pytest.mark.parametrize("word,expected_root,expected_pattern", [
        ("زلزل", "زلزل", "فعفع"),
        ("وسوس", "وسوس", "فعفع"),
        ("دمدم", "دمدم", "فعفع"),
        ("قلقل", "قلقل", "فعفع"),
    ])
    def test_reduplicated_quadriliteral_roots(self, word: str, expected_root: str, expected_pattern: str):
        """فحص الرباعي المضاعف (فعفع) وثبات جذره وأوزانه."""
        res = analyze(word)
        assert res.root == expected_root
        assert res.pattern == expected_pattern
        assert res.weak == bool(set("اوي") & set(expected_root))

    @pytest.mark.parametrize("plural,expected_singular", [
        ("سفن", "سفينة"),
        ("السفن", "السفينة"),
        ("موانئ", "ميناء"),
        ("الموانئ", "الميناء"),
        ("أنظمة", "نظام"),
        ("الأنظمة", "النظام"),
        ("لوائح", "لائحة"),
        ("اللوائح", "اللائحة"),
        ("أحكام", "حكم"),
        ("الأحكام", "الحكم"),
        ("مراكب", "مركب"),
        ("المراكب", "المركب"),
        ("شواهد", "شاهد"),
        ("الشواهد", "الشاهد"),
        ("وثائق", "وثيقة"),
        ("الوثائق", "الوثيقة"),
        ("عقود", "عقد"),
        ("العقود", "العقد"),
        ("قوانين", "قانون"),
        ("القوانين", "القانون"),
        ("مكاتب", "مكتب"),
        ("المكاتب", "المكتب"),
        ("بحار", "بحر"),
        ("البحار", "البحر"),
    ])
    def test_broken_plurals_singularization(self, plural: str, expected_singular: str):
        """فحص رد جموع التكسير الشائعة في المتون النظامية والمعجمية إلى المفرد."""
        assert singularize_broken_plural(plural) == expected_singular

    def test_is_plural_flag_in_analysis(self):
        """فحص وسم الجمع في نتيجة التحليل الصرفي."""
        assert analyze("سفن").is_plural is True
        assert analyze("السفينة").is_plural is False
        assert analyze("المهندسون").is_plural is True
        assert analyze("موانئ").is_plural is True
        assert analyze("ميناء").is_plural is False

    def test_lemma_resolution(self):
        """فحص المفرد والـ lemma في التحليل الصرفي."""
        assert analyze("سفن").lemma == "سفينة"
        assert analyze("كاتب").lemma == "كاتب"

    @pytest.mark.parametrize("word", ["راديو", "تلفزيون", "استراتيجيه", "بروتوكول"])
    def test_foreign_words_remain_undetermined(self, word: str):
        """الألفاظ الأعجمية لا تُبتلع بأوزان وهمية."""
        res = analyze(word)
        assert res.root is None

    def test_non_arabic_and_short_words(self):
        """الكلمات غير العربية والقصيرة جداً تعلن سبب التعذر الصريح."""
        assert analyze("API").reason == "not_arabic_script"
        assert analyze("من").reason == "stem_too_short"


# ==============================================================================
# ٢. فحوصات محرك التشكيل الصواتي وفك لبس المتشابهات
# ==============================================================================

class TestTashkeelCore:
    def test_strip_and_has_tashkeel(self):
        """فحص تجريد التشكيل والتحقق من وجوده."""
        vocalized = "السَّفِينَةُ البَحْرِيَّةُ"
        plain = "السفينة البحرية"
        assert has_tashkeel(vocalized) is True
        assert has_tashkeel(plain) is False
        assert strip_tashkeel(vocalized) == plain

    def test_tashkeel_similarity_bp_exact_match(self):
        """تطابق التشكيل التام يعيد 10,000 نقطة أساس."""
        text = "عَقْدُ العَمَلِ البَحْرِيِّ"
        assert tashkeel_similarity_bp(text, text) == 10000

    def test_tashkeel_similarity_bp_different_words(self):
        """اختلاف المتن المجرد يعيد 0 نقطة أساس."""
        assert tashkeel_similarity_bp("سفينة", "باخرة") == 0

    def test_tashkeel_similarity_bp_partial_match(self):
        """التطابق الجزئي يحسب نقاط أساس صحيحة دون كسور."""
        t1 = "سَفِينَة"
        t2 = "سُفِينَة"
        score = tashkeel_similarity_bp(t1, t2)
        assert 0 < score < 10000
        assert isinstance(score, int)

    def test_disambiguate_homograph_aqd(self):
        """فك لبس «عقد»: وثيقة قانونية vs فعل إبرام vs عقد زمني."""
        # 1. وثيقة قانونية
        res1 = disambiguate_homographs("عقد", "إبرام عقد العمل البحري وشروطه بين الطرفين")
        assert res1.vocalized == "عَقْد"
        assert res1.confidence_bp >= 9000
        assert "وثيقة" in res1.sense

        # 2. فعل إبرام
        res2 = disambiguate_homographs("عقد", "عقد المؤتمر جلسته الأولى")
        assert res2.vocalized == "عَقَدَ"
        assert res2.confidence_bp >= 8500

        # 3. عقد زمني
        res3 = disambiguate_homographs("عقد", "مرت عشرون سنة في العقد الماضي")
        assert res3.vocalized == "عِقْد"
        assert "زمني" in res3.sense

    def test_disambiguate_homograph_hukm(self):
        """فك لبس «حكم»: قضاء نظامي vs قاض/محكم."""
        res_legal = disambiguate_homographs("حكم", "صدر حكم قضائي قطعي من محكمة الاستئناف")
        assert res_legal.vocalized == "حُكْم"
        assert res_legal.confidence_bp >= 9000

        res_arbitrator = disambiguate_homographs("حكم", "تم اختيار حكم عدل لفض النزاع بين طرفين")
        assert res_arbitrator.vocalized == "حَكَم"
        assert "مُحَكَّم" in res_arbitrator.sense

    def test_disambiguate_homograph_sufun(self):
        """فك لبس «سفن»: مراكب بحرية جمع سفينة vs سفان."""
        res_vessels = disambiguate_homographs("سفن", "حمولة سفن الصيد والنزهة في الميناء")
        assert res_vessels.vocalized == "سُفُن"
        assert res_vessels.confidence_bp >= 9500

        res_sailor = disambiguate_homographs("سفن", "الربان سفان ماهر في بناء الزوارق")
        assert res_sailor.vocalized == "سَفَّان"

    def test_disambiguate_homograph_bahr(self):
        """فك لبس «بحر»: يم مائي vs بحار ملاح."""
        res_sea = disambiguate_homographs("بحر", "الإبحار في مياه البحر الأحمر الإقليمية")
        assert res_sea.vocalized == "بَحْر"

        res_seaman = disambiguate_homographs("بحر", "إصدار شهادة تأهيل لكل بحار في طاقم السفينة")
        assert res_seaman.vocalized == "بَحَّار"

    def test_disambiguate_homograph_alam(self):
        """فك لبس «علم»: راية السفينة vs معرفة ودراية."""
        res_flag = disambiguate_homographs("علم", "رفع علم المملكة العربية السعودية على السفينة المسجلة")
        assert res_flag.vocalized == "عَلَم"
        assert "راية" in res_flag.sense

        res_knowledge = disambiguate_homographs("علم", "دراسة أصول علم اللسانيات والفقه")
        assert res_knowledge.vocalized == "عِلْم"
        assert "معرفة" in res_knowledge.sense


# ==============================================================================
# ٣. فحوصات المدقق النحوي والتركيبي
# ==============================================================================

class TestSyntaxGuard:
    def test_correct_sentence_passes_with_perfect_score(self):
        """الجملة السليمة نحوياً تجتاز بنسبة 10,000 نقطة أساس وصفر أخطاء."""
        sentence = "لم يفعلوا ذلك ولن يتهاونوا في حماية المياه الإقليمية."
        verdict = check_syntax(sentence)
        assert verdict.valid is True
        assert verdict.score_bp == 10000
        assert len(verdict.issues) == 0

    def test_jazm_five_verbs_catches_retained_noon(self):
        """كشف ثبوت النون بعد أداة الجزم (لم يفعلون)."""
        bad = "المسؤولون لم يلتزمون بأحكام السلامة البحرية."
        verdict = check_syntax(bad)
        assert verdict.valid is False
        assert any(i.rule == "jazm_five_verbs" for i in verdict.issues)
        assert verdict.score_bp < 10000
        issue = next(i for i in verdict.issues if i.rule == "jazm_five_verbs")
        assert "يلتزموا" in (issue.suggested_fix or "")

    def test_nasb_five_verbs_catches_retained_noon(self):
        """كشف ثبوت النون بعد أداة النصب (لن يفعلون)."""
        bad = "يجب أن يتعاونون لحماية البيئة البحرية."
        verdict = check_syntax(bad)
        assert verdict.valid is False
        assert any(i.rule == "nasb_five_verbs" for i in verdict.issues)

    def test_preposition_governs_majroor_catches_waw(self):
        """كشف جمع المذكر السالم المرفوع بعد حرف الجر (في المهندسون)."""
        bad = "أشرف المفتش على المهندسون العاملين في الميناء."
        verdict = check_syntax(bad)
        assert verdict.valid is False
        assert any(i.rule == "preposition_governs_mansoob_majroor" for i in verdict.issues)
        issue = next(i for i in verdict.issues if i.rule == "preposition_governs_mansoob_majroor")
        assert "على المهندسين" in (issue.suggested_fix or "")

    def test_preposition_governs_dual_catches_alef(self):
        """كشف المثنى المرفوع بالألف بعد حرف الجر (بين الفريقان)."""
        bad = "وقع النزاع بين الفريقان في المرفأ."
        verdict = check_syntax(bad)
        assert verdict.valid is False
        assert any(i.rule == "preposition_governs_dual" for i in verdict.issues)

    def test_preposition_dual_ignores_intrinsic_noon(self):
        """حروف الجر لا تلتبس بالأسماء المنتهية بألف ونون أصلية مثل السلطان والإنسان."""
        valid_sentence = "في المكان والزمان والإنسان والبيان."
        verdict = check_syntax(valid_sentence)
        assert verdict.valid is True
        assert len(verdict.issues) == 0

    def test_inna_ism_must_be_mansoob(self):
        """كشف اسم إن المرفوع بالواو (إن المعلمون)."""
        bad = "إنّ الملاحون حريصون على السلامة."
        verdict = check_syntax(bad)
        assert verdict.valid is False
        assert any(i.rule == "inna_ism_must_be_mansoob" for i in verdict.issues)

    def test_kana_khabar_must_be_mansoob(self):
        """كشف خبر كان المرفوع بالواو (كان المهندسون حاضرون)."""
        bad = "كان الملاحون حاضرون في الاجتماع."
        verdict = check_syntax(bad)
        assert verdict.valid is False
        assert any(i.rule == "kana_khabar_must_be_mansoob" for i in verdict.issues)


# ==============================================================================
# ٤. تكامل النواة اللسانية مع الاسترجاع الهجين
# ==============================================================================

class TestHybridRetrievalLinguisticsIntegration:
    @needs_corpus
    def test_hybrid_retriever_uses_lemmas_for_plural_queries(self):
        """المسترجع الهجين يستفيد من المفردات في استرجاع أحكام السفن."""
        retriever = HybridRetriever()
        results = retriever.search("أحكام السفن", limit=3)
        assert len(results) > 0
        # التأكد من رجوع نتائج تحتوي على مواد تخص السفن
        assert any("سفينة" in r.get("text", "") or "سفن" in r.get("text", "") for r in results)
