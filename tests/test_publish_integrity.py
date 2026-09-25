"""Publication preflight regressions with real synthetic ledgers and corpus files.

No model calls, real source data, signing credentials, or publication trees are used.
"""
from pathlib import Path

import pytest

from core.acquisitions import Acquisition, SourceRegister
from core.canonical import PayloadRejected
from core.corpus import CorpusCatalog, CorpusFile
from core.knowledge import KnowledgeItem
from core.ledger import Ledger, LedgerCorrupt
from tools.publish_projection import build, plan


def _snapshot(root):
    return {
        str(path.relative_to(root)): (
            path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino
        )
        for path in root.rglob("*") if path.is_file()
    }


def _item(*, distributable=True, text="synthetic fixture content"):
    return KnowledgeItem(
        text=text, lang="en", domain="maritime", use_internal=True,
        use_distribution=distributable, source_id="synthetic-source",
        locus="p1", originality="original",
    )


def _add(root, doc_id, filename, *, store="maritime", corrupt=False,
         distributable=True):
    """Use safe distinct corpus filenames independently of document identifiers."""
    directory = root / "corpus" / store
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    registry = SourceRegister(root / "sources" / "acquisitions.jsonl", create=False)
    item = _item(distributable=distributable, text="synthetic content: " + filename)
    if corrupt:
        # A coherent outer ledger does not establish a valid inner material digest.
        ledger = Ledger(path)
        ledger.append({"kind": "page", "doc_id": doc_id,
                       "item": item.fingerprint_payload(), "item_digest": "0" * 64})
        ledger.anchor()
        head = ledger.head()
    else:
        corpus = CorpusFile(path)
        head = corpus.ingest(doc_id, [item], registry)
        corpus.anchor()
    catalog = CorpusCatalog(directory / "_catalog.jsonl")
    catalog.record(doc_id, str(path.relative_to(root)), 1, head)
    catalog.anchor()
    return path


@pytest.fixture
def published_root(tmp_path, monkeypatch):
    monkeypatch.delenv("DIWAN_REQUIRE_SIGNATURE", raising=False)
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "synthetic-unit-test-key")
    root = tmp_path.resolve() / "synthetic-publication"
    registry = SourceRegister(root / "sources" / "acquisitions.jsonl")
    registry.acquire(Acquisition(
        "synthetic-source", "fixture", "https://example.invalid",
        "2026-09-21", True, True, "synthetic fixture permission",
    ))
    _add(root, "prior", "prior.jsonl")
    assert build(root)["published"] == 1
    assert (root / "publish" / "maritime__prior.jsonl").is_file()
    return root


@pytest.mark.parametrize("operation", [plan, build], ids=["plan", "build"])
@pytest.mark.parametrize("doc_id", [
    "bad/name", "bad\\name", "bad\x00name", "bad\u202ename", "x" * 256,
    "ő" * 80,  # NFC fits; NFD exceeds the portable 255-byte filename ceiling.
], ids=["slash", "backslash", "nul", "format-control", "long-ascii", "long-nfd"])
def test_invalid_output_component_preserves_previous_publication(published_root, operation, doc_id):
    root = published_root
    _add(root, doc_id, "new.jsonl")
    before_publication = _snapshot(root / "publish")
    before_inputs = _snapshot(root / "corpus")
    with pytest.raises(PayloadRejected) as error:
        operation(root)
    assert error.value.code == "publish_name_invalid"
    assert _snapshot(root / "publish") == before_publication
    assert _snapshot(root / "corpus") == before_inputs


@pytest.mark.parametrize("operation", [plan, build], ids=["plan", "build"])
@pytest.mark.parametrize("first,second", [("ALPHA", "alpha"), ("café", "cafe\u0301")],
                         ids=["casefold", "normalization"])
def test_colliding_output_names_preserve_previous_publication(published_root, operation, first, second):
    root = published_root
    _add(root, first, "first.jsonl")
    _add(root, second, "second.jsonl")
    before = _snapshot(root / "publish")
    with pytest.raises(PayloadRejected) as error:
        operation(root)
    assert error.value.code == "publish_name_collision"
    assert _snapshot(root / "publish") == before


@pytest.mark.parametrize("operation", [plan, build], ids=["plan", "build"])
@pytest.mark.parametrize("distributable", [True, False], ids=["distributed", "internal"])
def test_incorrect_material_digest_in_later_store_preserves_publication(
        published_root, operation, distributable):
    root = published_root
    path = _add(root, "broken", "broken.jsonl", store="lexicons", corrupt=True,
                distributable=distributable)
    # The supplied catalog and ledger chain are coherent, but the corpus contract fails.
    CorpusCatalog(path.parent / "_catalog.jsonl", create=False).verify(strict=True, root=root)
    with pytest.raises(LedgerCorrupt):
        CorpusFile(path, create=False).verify(strict=True)
    before_publication = _snapshot(root / "publish")
    before_inputs = _snapshot(root / "corpus")
    with pytest.raises(LedgerCorrupt):
        operation(root)
    assert _snapshot(root / "publish") == before_publication
    assert _snapshot(root / "corpus") == before_inputs
