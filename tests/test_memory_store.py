"""الذاكرةُ المحكومة تجتاز بنكَها المجمَّد بعتبته (ك٥٢، #43).

البنكُ `evaluation/suites/memory_v1.json` جُمّد في ك٤٨ قبل هذا البناء، وعتبتُه مسجَّلةٌ سلفًا.
وهذا الملفُّ يشغّله على `memory.store.MemoryStore` الحقيقيّ بمخازن على القرص. ومعه فحوصُ وحدةٍ
لما لا يبلغه البنك: الإيصالُ بلا نصّ، والاقتراحُ لا يمسّ القرص، والرابطُ يُرفض.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from evaluation.memory_runner import run_memory_bank
from memory.store import MemoryRefused, MemoryStore

ROOT = Path(__file__).resolve().parents[1]
BANK = json.loads((ROOT / "evaluation" / "suites" / "memory_v1.json").read_text(encoding="utf-8"))


def test_the_store_meets_every_pre_registered_threshold():
    report = run_memory_bank(BANK)
    failed = {r["id"]: r["failures"] for r in report["results"] if not r["passed"]}
    assert failed == {}
    assert report["metrics"] == {"forget_rate": 1.0, "leakage": 0, "consent_violations": 0,
                                 "injection_unquarantined": 0}
    assert report["meets_thresholds"] is True and report["passed"] == report["total"] == 30


@pytest.fixture
def store(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    return MemoryStore(project)


def test_a_receipt_holds_the_digest_and_never_the_text(store):
    item = store.remember("رمز الخزنة ٧٧٨٨", consent="owner")
    receipt = store.forget(item, references=["turn:abc"])
    assert set(receipt) == {"schema_version", "item_id", "sha256", "forgotten_at", "references"}
    assert "٧٧٨٨" not in json.dumps(receipt, ensure_ascii=False)
    assert receipt["references"] == ["turn:abc"]


def test_a_proposal_touches_nothing_on_disk_until_approved(store):
    before = sorted(p.name for p in store.root.rglob("*"))
    proposal = store.propose("اقتراحٌ من النموذج")
    assert sorted(p.name for p in store.root.rglob("*")) == before
    item = store.approve(proposal)
    assert item == proposal.proposal_id and store.items()[0]["text"] == "اقتراحٌ من النموذج"


@pytest.mark.parametrize("consent", ["none", "model", "", None])
def test_anything_but_owner_consent_is_refused_by_name(store, consent):
    with pytest.raises(MemoryRefused) as err:
        store.remember("نص", consent=consent)
    assert err.value.code == "consent_required" and store.items() == []


def test_a_symlinked_memory_directory_is_refused(tmp_path):
    project, elsewhere = tmp_path / "p", tmp_path / "elsewhere"
    project.mkdir(), elsewhere.mkdir()
    os.symlink(elsewhere, project / "memory")
    with pytest.raises(MemoryRefused) as err:
        MemoryStore(project)
    assert err.value.code == "memory_path_unsafe"


def test_forgetting_an_unknown_item_is_named(store):
    with pytest.raises(MemoryRefused) as err:
        store.forget("0" * 16)
    assert err.value.code == "item_unknown"
