"""المسقط الصرفي (م١٤ — الخطوة ١.٣): يربط المشتقَّ بجذره، ولا يبتلع ما ليس عربيًّا.

وشرطُ نقض الخطوة ١.٢ مقيَّدٌ هنا اختبارًا لا نثرًا: زمنُ الاستعلام، وسلامةُ
فهرسة `maritime` — فمن ادّعى بعد اليوم أن المسقط حياديُّ الأثر، كسَّرت دعواه
اختباراتٌ خضراء.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from projections.morphology import (BARE_TEMPLATES, NOT_ARABIC, TOO_SHORT,
                                    UNDETERMINED, analyze, build_projection,
                                    expand_query, normalize, search_by_root,
                                    segment)

ROOT = Path(__file__).resolve().parent.parent


# ————— تفكيك المشتقّات إلى جذرٍ واحد —————

@pytest.mark.parametrize("word", [
    "كتب", "كاتب", "كتاب", "مكتوب", "مكتب", "مكتبة", "استكتاب", "مكاتب",
    "الكتاب", "والكتاب", "وبالكتاب", "الكاتبون", "والمكتبات", "كتبها",
])
def test_the_whole_family_resolves_to_one_root(word):
    """أسرةُ «كتب» كلُّها جذرٌ واحد — وهذا هو نفعُ المسقط كلُّه."""
    assert analyze(word).root == "كتب"


@pytest.mark.parametrize("word,root", [
    ("أقلام", "قلم"), ("رسائل", "رسل"), ("شواهد", "شهد"), ("بحور", "بحر"),
    ("مدارس", "درس"), ("سفن", "سفن"), ("موانئ", "ونئ"),
])
def test_broken_plurals_reach_their_singular_root(word, root):
    assert analyze(word).root == root


def test_a_single_letter_prefix_does_not_eat_a_root_letter():
    """«ك» في «كاتب» حرفُ جذرٍ لا سابقة.

    أولُ صياغةٍ لهذه الوحدة اقتطعت السوابقَ جشِعًا فأعطت «كاتب» الجذرَ
    «اتب» و«كتاب» الجذرَ «تاب» و«بحور» الجذرَ «حور». والحارسُ هو ترتيبُ
    القراءات: الكلمةُ كما وردت قبل أيِّ اقتطاع.
    """
    for word in ("كاتب", "كتاب", "بحور", "لعب", "سفن"):
        assert analyze(word).prefixes == (), f"{word} اقتُطعت سابقتُها زورًا"


def test_definite_article_is_stripped_but_recorded():
    result = analyze("والمكتبات")
    assert result.root == "كتب"
    assert result.prefixes == ("وال",) and result.suffixes == ("ات",)


# ————— لا ابتلاع لما ليس عربيًّا قياسيًّا —————

def test_latin_script_is_refused_by_name_not_guessed():
    result = analyze("radio")
    assert result.root is None and result.reason == NOT_ARABIC


@pytest.mark.parametrize("word", ["راديو", "تلفزيون", "استراتيجيه"])
def test_foreign_words_in_arabic_letters_stay_undetermined(word):
    assert analyze(word).root is None
    assert analyze(word).reason == UNDETERMINED


def test_a_four_letter_foreign_word_is_not_given_a_confident_root():
    """«لندن» لا تُبتلع بجذرٍ مقطوعٍ به.

    القالبُ الرباعيُّ العامّ كان يعطيها «لندن» جذرًا، فقُصر على الرباعيِّ
    المضاعف. وما بقي من قراءةٍ لها يمرّ باقتطاع سابقةٍ مفردةٍ على قالبٍ
    مجرّد — فيُعلَن `weak` ولا يُقطع به.
    """
    result = analyze("لندن")
    assert result.root is None or result.weak is True


def test_particles_shorter_than_a_root_are_refused():
    for word in ("من", "في", "او"):
        assert analyze(word).reason == TOO_SHORT


def test_reduplicated_quadriliteral_is_kept_whole():
    assert analyze("زلزل").root == "زلزل" and analyze("زلزل").pattern == "فعفع"


def test_hollow_roots_are_flagged_weak_not_asserted():
    """«مدير» يعطي «دير» لا «دور» — الحدُّ مُعلَنٌ وموسوم."""
    result = analyze("مدير")
    assert result.root == "دير" and result.weak is True


def test_bare_template_after_a_single_prefix_is_flagged_weak():
    assert BARE_TEMPLATES == {"فعل", "فعله"}
    result = analyze("وكتب")
    assert result.root == "كتب" and result.weak is True


# ————— التطبيع والتفكيك —————

def test_normalization_matches_the_declared_projection_rules():
    assert normalize("الْمَكْتَبَةُ") == "المكتبه"
    assert normalize("إسلام") == "اسلام" and normalize("مصطفى") == "مصطفي"


def test_segment_reports_the_chosen_reading():
    assert segment("والمكتبات") == (("وال",), "مكتب", ("ات",))


# ————— المسقط نفسه —————

def test_projection_indexes_only_determined_roots(tmp_path):
    db = tmp_path / "morph.sqlite"
    indexed = build_projection(["كاتب", "مكتوب", "radio", "من", "راديو"], db)
    assert indexed == 2
    rows = search_by_root(db, "كتب")
    assert {r["word"] for r in rows} == {"كاتب", "مكتوب"}


def test_projection_is_rebuilt_not_appended(tmp_path):
    """مسقطٌ مشتقٌّ يُعاد بناؤه بلا خسارة — ولا يتراكم."""
    db = tmp_path / "morph.sqlite"
    build_projection(["كاتب", "مكتوب"], db)
    build_projection(["كاتب"], db)
    assert len(search_by_root(db, "كتب")) == 1


def test_searching_a_missing_projection_returns_empty_not_raises(tmp_path):
    assert search_by_root(tmp_path / "absent.sqlite", "كتب") == []


def test_query_expansion_keeps_unrooted_tokens_as_they_came(tmp_path):
    db = tmp_path / "morph.sqlite"
    build_projection(["كاتب", "مكتوب", "مكتبة"], db)
    expanded = expand_query("الكتاب radio", db)
    assert "radio" in expanded
    assert "كاتب" in expanded and "مكتوب" in expanded


def test_the_projection_table_is_fts5_as_the_plan_requires(tmp_path):
    db = tmp_path / "morph.sqlite"
    build_projection(["كاتب"], db)
    connection = sqlite3.connect(db)
    try:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='morphology'").fetchone()[0]
    finally:
        connection.close()
    assert "fts5" in sql.lower()


# ————— شرط النقض مقيَّدًا اختبارًا —————

def test_analysis_stays_far_below_the_fifteen_millisecond_ceiling():
    """سقفُ الخطة ١٥ms للاستعلام الواحد؛ التحليلُ وحده يجب أن يكون أرخصَ بكثير."""
    words = ["والمكتبات", "استكتاب", "مكتوب", "راديو", "لندن"] * 40
    start = time.perf_counter()
    for word in words:
        analyze(word)
    per_word_ms = (time.perf_counter() - start) * 1000 / len(words)
    assert per_word_ms < 1.0, f"{per_word_ms:.3f}ms للكلمة"


def test_root_search_stays_under_the_repeal_ceiling(tmp_path):
    """شرطُ النقض: فوق 30ms يُلغى المسقط. يُقاس هنا لا يُوعد به."""
    db = tmp_path / "morph.sqlite"
    vocabulary = [f"مكتب{chr(0x0628 + i % 20)}" for i in range(500)]
    build_projection(["كاتب", "مكتوب", "مكتبة", "استكتاب", *vocabulary], db)
    start = time.perf_counter()
    for _ in range(20):
        search_by_root(db, "كتب")
    per_query_ms = (time.perf_counter() - start) * 1000 / 20
    assert per_query_ms < 15.0, f"{per_query_ms:.3f}ms للاستعلام"


def _executing_and_wrapper_sources() -> list[str]:
    import inspect
    impl_file = Path(inspect.getsourcefile(analyze))
    wrapper_file = ROOT / "projections" / "morphology.py"
    files = {impl_file, wrapper_file}
    return [p.read_text(encoding="utf-8") for p in files if p.exists()]


def test_the_morphology_projection_does_not_touch_the_maritime_index():
    """شرطُ النقض الثاني: ألّا يكسر المسقطُ فهرسةَ المخزن البحري.

    الحارسُ بنيويّ: الوحدة المنفذة وغلافها لا يعرفان مسار `maritime-fts.sqlite` ولا يفتحانه.
    فلو استوردته يومًا سقط هذا الاختبار قبل أن يسقط الفهرس.
    """
    for source in _executing_and_wrapper_sources():
        assert "maritime" not in source.replace("maritime`", "")
    maritime = ROOT / "projections" / "maritime-fts.sqlite"
    if maritime.exists():
        before = maritime.stat().st_mtime_ns
        analyze("والمكتبات")
        assert maritime.stat().st_mtime_ns == before


def test_the_module_reaches_no_network_and_no_model():
    """«يعمل محليًّا بالكامل» تُقيَّد اختبارًا لا تُترك دعوى.

    يفحص الوحدة المنفذة فعلًا وغلافها لضمان خلوهما التام من منافذ الشبكة ونماذج التوليد.
    """
    for source in _executing_and_wrapper_sources():
        for forbidden in ("import socket", "import requests", "urllib", "http",
                          "providers", "ollama"):
            assert forbidden not in source
