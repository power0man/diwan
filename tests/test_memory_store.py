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


def test_approving_a_forgotten_proposal_again_is_refused(store):
    """قبولُ الاقتراح نفسِه بعد نسيانه كان يُحيي العنصرَ بمعرّفه القديم، فلا يُنسى ثانيةً."""
    proposal = store.propose("عنوان البيت القديم")
    item = store.approve(proposal)
    store.forget(item)
    with pytest.raises(MemoryRefused) as err:
        store.approve(proposal)
    assert err.value.code == "item_forgotten" and store.items() == []


@pytest.mark.parametrize("item_id", ["../../escaped", "0" * 15, "A" * 16])
def test_a_snapshot_whose_id_leaves_the_store_is_refused_before_anything_changes(store, item_id):
    kept = store.remember("عنصرٌ قائم", consent="owner")
    text = "مدسوس"
    payload = json.dumps({"item_id": item_id, "text": text,
                          "sha256": __import__("hashlib").sha256(text.encode()).hexdigest()}).encode()
    with pytest.raises(MemoryRefused) as err:
        store.restore({f"items/{item_id}.json": payload})
    assert err.value.code == "snapshot_invalid"
    assert [it["item_id"] for it in store.items()] == [kept]
    assert not (store.root.parent / "escaped.json").exists()


def test_a_snapshot_item_that_does_not_match_its_digest_is_refused(store):
    snapshot = {f"items/{'1' * 16}.json": json.dumps({"item_id": "1" * 16, "text": "نص",
                                                     "sha256": "0" * 64}).encode()}
    with pytest.raises(MemoryRefused) as err:
        store.restore(snapshot)
    assert err.value.code == "snapshot_invalid"


def test_a_malformed_receipt_in_a_snapshot_is_refused(store):
    with pytest.raises(MemoryRefused) as err:
        store.restore({"receipts.jsonl": b'{"item_id": "../x"}\n'})
    assert err.value.code == "snapshot_invalid"
