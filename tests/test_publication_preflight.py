from pathlib import Path
import pytest

from core.acquisitions import Acquisition,SourceRegister
from core.corpus import CorpusCatalog,CorpusFile
from core.knowledge import KnowledgeItem
from core.ledger import LedgerCorrupt
from tools.publish_projection import build


def seed(root, distributable=True):
    registry=SourceRegister(root/'sources'/'acquisitions.jsonl')
    registry.acquire(Acquisition('synthetic-source','fixture','https://example.invalid',
                                '2026-09-21',True,distributable,'synthetic permission fixture'))
    folder=root/'corpus'/'maritime'
    folder.mkdir(parents=True)
    corpus=CorpusFile(folder/'fixture.jsonl')
    item=KnowledgeItem(text='synthetic distributable content',lang='en',domain='maritime',
                       use_internal=True,use_distribution=distributable,source_id='synthetic-source',
                       locus='p1',originality='original',part='fixture')
    head=corpus.ingest('fixture',[item],registry)
    corpus.anchor()
    catalog=CorpusCatalog(folder/'_catalog.jsonl')
    catalog.record('fixture','corpus/maritime/fixture.jsonl',1,head)
    catalog.anchor()


def test_late_source_failure_preserves_previous_publication(tmp_path,monkeypatch):
    monkeypatch.delenv('DIWAN_REQUIRE_SIGNATURE',raising=False)
    monkeypatch.setenv('DIWAN_ANCHOR_KEY','synthetic-unit-test-key')
    root=tmp_path.resolve()/'root'
    seed(root)
    first=build(root)
    assert first['published']==1
    output=root/'publish'
    prior={p.name:p.read_bytes() for p in output.iterdir() if p.is_file()}
    assert 'maritime__fixture.jsonl' in prior
    # The first store is valid; the following store fails verification late.
    late=root/'corpus'/'lexicons'
    late.mkdir()
    (late/'_catalog.jsonl').write_bytes(b'not-json\n')
    with pytest.raises((LedgerCorrupt,ValueError)):
        build(root)
    after={p.name:p.read_bytes() for p in output.iterdir() if p.is_file()}
    assert after==prior, {'prior_files':sorted(prior),'remaining_files':sorted(after)}


def snapshot(root):
    return {str(p.relative_to(root)):(p.read_bytes(),p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def test_late_failure_after_rights_refusal_does_not_append_manifest(tmp_path,monkeypatch):
    monkeypatch.delenv('DIWAN_REQUIRE_SIGNATURE',raising=False)
    monkeypatch.setenv('DIWAN_ANCHOR_KEY','synthetic-unit-test-key')
    root=tmp_path.resolve()/'root'
    seed(root)
    build(root)
    registry=SourceRegister(root/'sources'/'acquisitions.jsonl')
    registry.acquire(Acquisition('synthetic-source','fixture','https://example.invalid',
                                '2026-09-21',True,False,'synthetic rights revocation'))
    late=root/'corpus'/'lexicons'
    late.mkdir()
    (late/'_catalog.jsonl').write_bytes(b'not-json\n')
    prior=snapshot(root/'publish')
    with pytest.raises((LedgerCorrupt,ValueError)):
        build(root)
    assert snapshot(root/'publish')==prior


def test_plan_preserves_all_input_bytes_mtime_and_creates_nothing(tmp_path,monkeypatch):
    from tools import publish_projection
    monkeypatch.delenv('DIWAN_REQUIRE_SIGNATURE',raising=False)
    monkeypatch.setenv('DIWAN_ANCHOR_KEY','synthetic-unit-test-key')
    root=tmp_path.resolve()/'root'
    seed(root)
    prior=snapshot(root)
    report=publish_projection.plan(root)['report']
    assert report['published']==1
    assert snapshot(root)==prior
    assert not (root/'publish').exists()
    assert not (root/'corpus'/'lexicons').exists()
    assert not (root/'corpus'/'lexicons-local').exists()


def test_missing_signing_key_preserves_previous_data(tmp_path,monkeypatch):
    from core.ledger import Ledger
    from core.signing import SigningRefused,sign_anchor
    import core.signing as signing
    monkeypatch.delenv('DIWAN_REQUIRE_SIGNATURE',raising=False)
    monkeypatch.setenv('DIWAN_ANCHOR_KEY','synthetic-unit-test-key')
    root=tmp_path.resolve()/'root'
    seed(root)
    build(root)
    sign_anchor(Ledger(root/'publish'/'_manifest.jsonl',create=False),b'synthetic-unit-test-key')
    prior={p.name:p.read_bytes() for p in (root/'publish').iterdir() if p.is_file()}
    monkeypatch.delenv('DIWAN_ANCHOR_KEY',raising=False)
    monkeypatch.setattr(signing,'_keychain_key',lambda:None)
    with pytest.raises(SigningRefused,match='signing_key_missing'):
        build(root)
    assert {p.name:p.read_bytes() for p in (root/'publish').iterdir() if p.is_file()}==prior


def test_plan_must_validate_registry_even_when_nothing_is_distributable(tmp_path,monkeypatch):
    from tools import publish_projection
    monkeypatch.delenv('DIWAN_REQUIRE_SIGNATURE',raising=False)
    monkeypatch.setenv('DIWAN_ANCHOR_KEY','synthetic-unit-test-key')
    root=tmp_path.resolve()/'root'
    seed(root,distributable=False)
    source=root/'sources'/'acquisitions.jsonl'
    source.write_bytes(b'corrupt rights registry\n')
    prior=snapshot(root)
    with pytest.raises((LedgerCorrupt,ValueError)):
        publish_projection.plan(root)
    assert snapshot(root)==prior


@pytest.mark.parametrize('doc_id', ['bad/name', 'long-' + 'x'*260, 'Fixture'])
def test_invalid_projected_filename_preserves_previous_publication(tmp_path,monkeypatch,doc_id):
    monkeypatch.delenv('DIWAN_REQUIRE_SIGNATURE',raising=False)
    monkeypatch.setenv('DIWAN_ANCHOR_KEY','synthetic-unit-test-key')
    root=tmp_path.resolve()/'root'
    seed(root)
    build(root)
    registry=SourceRegister(root/'sources'/'acquisitions.jsonl')
    corpus=CorpusFile(root/'corpus'/'maritime'/'extra.jsonl')
    item=KnowledgeItem(text='second distributable page',lang='en',domain='maritime',
                       use_internal=True,use_distribution=True,source_id='synthetic-source',
                       locus='p2',originality='original',part='fixture')
    head=corpus.ingest(doc_id,[item],registry)
    corpus.anchor()
    catalog=CorpusCatalog(root/'corpus'/'maritime'/'_catalog.jsonl')
    catalog.record(doc_id,'corpus/maritime/extra.jsonl',1,head)
    catalog.anchor()
    before=snapshot(root/'publish')
    with pytest.raises((OSError,ValueError)):
        build(root)
    assert snapshot(root/'publish')==before


def test_wrong_item_digest_in_valid_chain_is_refused_before_publication(tmp_path,monkeypatch):
    from core.ledger import Ledger
    monkeypatch.delenv('DIWAN_REQUIRE_SIGNATURE',raising=False)
    monkeypatch.setenv('DIWAN_ANCHOR_KEY','synthetic-unit-test-key')
    root=tmp_path.resolve()/'root'
    seed(root)
    build(root)
    item=KnowledgeItem(text='page carrying an invalid provenance digest',lang='en',domain='maritime',
                       use_internal=True,use_distribution=True,source_id='synthetic-source',
                       locus='p2',originality='original',part='fixture')
    ledger=Ledger(root/'corpus'/'maritime'/'extra.jsonl')
    ledger.append({'kind':'page','doc_id':'extra','item':item.fingerprint_payload(),'item_digest':'0'*64})
    ledger.anchor()
    catalog=CorpusCatalog(root/'corpus'/'maritime'/'_catalog.jsonl')
    catalog.record('extra','corpus/maritime/extra.jsonl',1,ledger.head())
    catalog.anchor()
    with pytest.raises(LedgerCorrupt):
        CorpusFile(ledger.path,create=False).verify(strict=True)
    before=snapshot(root/'publish')
    with pytest.raises(LedgerCorrupt):
        build(root)
    assert snapshot(root/'publish')==before
