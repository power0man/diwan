"""اختبارات م٢: المخزن القانوني والتقطيع والتطبيع.

الكتابة الدفعية يجب أن تكون **مطابقة بايتات السلسلة** لما يكتبه
Ledger.append قيدًا قيدًا — وإلا صار للمخزن صيغة ثانية خفية.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from core.acquisitions import Acquisition, SourceRegister
from core.canonical import PayloadRejected, digest
from core.corpus import CorpusCatalog, CorpusFile
from core.knowledge import KnowledgeItem
from core.ledger import Ledger, LedgerCorrupt
from ingest_regulations import chunk_document
from rebuild_index import normalize


def _register(tmp_path, **acq_over) -> SourceRegister:
    reg = SourceRegister(tmp_path / "src" / "acq.jsonl")
    base = dict(source_id="src", title="ت", origin="https://x",
                verified_date="2026-09-20", use_internal=True,
                use_distribution=False, license_evidence="دليل")
    base.update(acq_over)
    reg.acquire(Acquisition(**base))
    return reg


def _item(**over) -> KnowledgeItem:
    base = dict(text="تنطبق هذه اللائحة على السفن.", lang="ar",
                domain="maritime-regulation", use_internal=True,
                use_distribution=False, source_id="src", locus="المادة (1)",
                originality="original")
    base.update(over)
    return KnowledgeItem(**base)


# — المخزن —

def test_bulk_ingest_matches_ledger_append_bytes(tmp_path):
    items = [_item(locus=f"المادة ({i})") for i in range(1, 4)]
    reg = _register(tmp_path)
    cf = CorpusFile(tmp_path / "bulk.jsonl")
    cf.ingest("doc-1", items, reg)

    led = Ledger(tmp_path / "seq.jsonl")
    for it in items:
        payload = it.fingerprint_payload()
        led.append({"kind": "page", "doc_id": "doc-1",
                    "item": payload, "item_digest": digest(payload)})
    assert (tmp_path / "bulk.jsonl").read_bytes() == \
           (tmp_path / "seq.jsonl").read_bytes()


def test_ingest_verifiable_by_plain_ledger(tmp_path):
    reg = _register(tmp_path)
    cf = CorpusFile(tmp_path / "c.jsonl")
    cf.ingest("doc-1", [_item()], reg)
    assert Ledger(tmp_path / "c.jsonl").verify_chain()
    assert cf.verify(register=reg)


def test_ingest_gate_blocks_excess_rights(tmp_path):
    reg = _register(tmp_path)  # المصدر داخلي فقط
    cf = CorpusFile(tmp_path / "c.jsonl")
    with pytest.raises(PayloadRejected) as e:
        cf.ingest("doc-1", [_item(use_distribution=True)], reg)
    assert e.value.code == "distribution_exceeds_source"
    assert cf.count() == 0  # البوابة قبل الكتابة — لا وثيقة نصفها داخل


def test_corpus_deep_verify_catches_forged_page(tmp_path):
    reg = _register(tmp_path)
    cf = CorpusFile(tmp_path / "c.jsonl")
    cf.ingest("doc-1", [_item()], reg)
    Ledger(tmp_path / "c.jsonl").append(
        {"kind": "page", "doc_id": "doc-1",
         "item": {"text": "مزوّر"}, "item_digest": "0" * 64})
    with pytest.raises(LedgerCorrupt):
        cf.verify()


def test_catalog_idempotent_and_detects_head_drift(tmp_path):
    reg = _register(tmp_path)
    cf = CorpusFile(tmp_path / "d.jsonl")
    head = cf.ingest("doc-1", [_item()], reg)
    cat = CorpusCatalog(tmp_path / "_catalog.jsonl")
    cat.record("doc-1", "d.jsonl", 1, head)
    n = cat._ledger.count()
    cat.record("doc-1", "d.jsonl", 1, head)
    assert cat._ledger.count() == n
    assert cat.verify(root=tmp_path)
    cf.ingest("doc-1", [_item(locus="المادة (2)")], reg)  # الرأس تغيّر
    with pytest.raises(LedgerCorrupt):
        cat.verify(root=tmp_path)


# — التقطيع (حتميّ، بالمواد ثم بالفقرات) —

def test_chunking_by_articles_and_preamble():
    text = "تمهيد اللائحة.\n\nالمادة (1): تعريف.\nنصها.\n\nالمادة (2): نطاق.\nنصها."
    chunks = chunk_document(text)
    assert [l for l, _ in chunks] == ["التمهيد", "المادة (1)", "المادة (2)"]
    assert chunks == chunk_document(text)  # حتمية


def test_chunking_splits_oversized_article():
    text = "المادة (1): " + "كلمة " * 3000
    loci = [l for l, _ in chunk_document(text)]
    assert loci[0] == "المادة (1)" and any("تتمة" in l for l in loci[1:])


def test_chunking_paragraph_fallback():
    text = "\n\n".join(f"فقرة رقم {i} " + "نص " * 200 for i in range(5))
    loci = [l for l, _ in chunk_document(text)]
    assert all(l.startswith("المقطع") for l in loci) and len(loci) >= 2


# — التطبيع (قواعده معلنة حصرًا) —

def test_normalize_rules():
    assert normalize("إِنْشَاءٌ") == "انشاء"
    assert normalize("الصابورة") == "الصابوره"
    assert normalize("هُدًى") == "هدي"
    assert normalize("مـــادة") == "ماده"
    assert normalize("نص بلا تغيير") == "نص بلا تغيير"
