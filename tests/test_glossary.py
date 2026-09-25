"""اختبارات م٣: المسارد المحكومة وسجل السوابق والاستخلاص."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from core.canonical import PayloadRejected, digest
from core.glossary import Glossary, GlossaryEntry, validated_entry
from core.ledger import Ledger, LedgerCorrupt
from core.rulings import Ruling, Rulings, validated_ruling
from extract_terms import extract_from_page

EV = "a" * 64
CORPUS = frozenset({EV})


def entry(**over) -> GlossaryEntry:
    base = dict(glossary="maritime", term="السلطة البحرية",
                definition="الجهة المختصة بشؤون النقل البحري.",
                evidence_digest=EV, status="approved")
    base.update(over)
    return GlossaryEntry(**base)


def rejects(fn, code, *args):
    with pytest.raises(PayloadRejected) as e:
        fn(*args)
    assert e.value.code == code, f"{e.value.code!r} != {code!r}"


# — المدخل —

def test_entry_valid_passes():
    assert validated_entry(entry()) is not None


def test_entry_bad_evidence_rejected():
    rejects(validated_entry, "evidence_invalid", entry(evidence_digest="xyz"))


def test_entry_unknown_status_rejected():
    rejects(validated_entry, "status_unknown", entry(status="final"))


def test_entry_empty_definition_rejected():
    rejects(validated_entry, "definition_missing", entry(definition="  "))


# — المسرد المحكوم —

def test_add_requires_corpus_membership(tmp_path):
    g = Glossary(tmp_path / "g.jsonl")
    rejects(g.add, "evidence_unknown", entry(evidence_digest="b" * 64), CORPUS)
    assert g._ledger.count() == 0


def test_add_versioning(tmp_path):
    g = Glossary(tmp_path / "g.jsonl")
    g.add(entry(), CORPUS)
    n = g._ledger.count()
    g.add(entry(), CORPUS)                          # مطابق: لا تكرار
    assert g._ledger.count() == n
    g.add(entry(definition="تعريف منقح بعد المراجعة."), CORPUS)
    assert g._ledger.count() == n + 1
    assert g.get("maritime", "السلطة البحرية")["entry"]["definition"].startswith("تعريف منقح")


def test_deep_verify_catches_bypass_with_foreign_evidence(tmp_path):
    g = Glossary(tmp_path / "g.jsonl")
    g.add(entry(), CORPUS)
    p = entry(term="دخيل", evidence_digest="c" * 64).fingerprint_payload()
    Ledger(tmp_path / "g.jsonl").append(
        {"kind": "term", "glossary": "maritime", "term": "دخيل",
         "entry": p, "entry_digest": digest(p)})
    with pytest.raises(LedgerCorrupt):
        g.verify(CORPUS)


def test_deep_verify_catches_key_mismatch(tmp_path):
    g = Glossary(tmp_path / "g.jsonl")
    p = entry().fingerprint_payload()
    Ledger(tmp_path / "g.jsonl").append(
        {"kind": "term", "glossary": "maritime", "term": "اسم-آخر",
         "entry": p, "entry_digest": digest(p)})
    with pytest.raises(LedgerCorrupt):
        g.verify(CORPUS)


def test_approved_filters_status(tmp_path):
    g = Glossary(tmp_path / "g.jsonl")
    g.add(entry(), CORPUS)
    g.add(entry(term="مقترح", status="proposed"), CORPUS)
    assert [r["term"] for r in g.approved("maritime")] == ["السلطة البحرية"]


# — السوابق —

def test_ruling_valid_and_versioned(tmp_path):
    r = Rulings(tmp_path / "r.jsonl")
    r.rule(Ruling("موضوع", "نص الحكم.", "السند.", "2026-09-20"))
    n = r._ledger.count()
    r.rule(Ruling("موضوع", "نص الحكم.", "السند.", "2026-09-20"))
    assert r._ledger.count() == n
    r.rule(Ruling("موضوع", "نص معدل بحكم جديد.", "سند جديد.", "2026-09-21"))
    assert r.get("موضوع")["ruling"]["text"].startswith("نص معدل")
    assert r.verify()


def test_ruling_bad_date_rejected():
    rejects(validated_ruling, "decided_on_invalid",
            Ruling("م", "ن", "س", "20260920"))


def test_ruling_forgery_detected(tmp_path):
    r = Rulings(tmp_path / "r.jsonl")
    r.rule(Ruling("موضوع", "نص.", "سند.", "2026-09-20"))
    Ledger(tmp_path / "r.jsonl").append(
        {"kind": "ruling", "topic": "مزوّر",
         "ruling": {"topic": "مزوّر"}, "ruling_digest": "0" * 64})
    with pytest.raises(LedgerCorrupt):
        r.verify()


# — الاستخلاص —

def test_extract_terms_from_definitions_page():
    text = ("في تطبيق أحكام هذه اللائحة يقصد بالمصطلحات التالية:\n"
            "السلطة البحرية: الجهة المختصة بشؤون النقل البحري في المملكة.\n"
            "| الميناء | ليس سطر تعريف |\n"
            "| السفينة: كل منشأة عائمة مسجلة تعمل في البيئة البحرية. |\n"
            "المادة (2): لا يُلتقط رأس المادة مصطلحًا.\n")
    got = dict(extract_from_page(text))
    assert "السلطة البحرية" in got
    assert got["السفينة"].startswith("كل منشأة")
    assert all("المادة" not in t for t in got)


def test_extract_skips_non_definition_pages():
    assert extract_from_page("نص عادي: بلا افتتاحية تعريفات.") == []
