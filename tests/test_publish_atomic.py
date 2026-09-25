"""Atomic publication staging, locking, and crash recovery tests (M13-B).

Ensures that publication builds write into isolated staging directories, that
interrupted builds clean up atomically without corrupting published files, and
that recovery purges orphan staging folders.
"""
import os
import shutil
import pytest
from unittest.mock import patch

from core.acquisitions import Acquisition, SourceRegister
from core.knowledge import KnowledgeItem
from core.corpus import CorpusCatalog, CorpusFile
from tools.publish_projection import build, recover_staging, _publish_lock


def _add_page(root, doc_id="doc1", store="maritime"):
    dir_path = root / "corpus" / store
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"{doc_id}.jsonl"
    reg = SourceRegister(root / "sources" / "acquisitions.jsonl")
    item = KnowledgeItem(
        text=f"Content for {doc_id}", lang="ar", domain="maritime",
        use_internal=True, use_distribution=True, source_id="src-pub",
        locus="p1", originality="original",
    )
    cf = CorpusFile(file_path)
    head = cf.ingest(doc_id, [item], reg)
    cf.anchor()
    cat = CorpusCatalog(dir_path / "_catalog.jsonl")
    cat.record(doc_id, str(file_path.relative_to(root)), 1, head)
    cat.anchor()


@pytest.fixture
def pub_env(tmp_path, monkeypatch):
    monkeypatch.delenv("DIWAN_REQUIRE_SIGNATURE", raising=False)
    monkeypatch.setenv("DIWAN_ANCHOR_KEY", "test-key-for-pub")
    root = tmp_path.resolve() / "pub-atomic"
    root.mkdir(parents=True, exist_ok=True)
    reg = SourceRegister(root / "sources" / "acquisitions.jsonl")
    reg.acquire(Acquisition("src-pub", "src-name", "https://example.com", "2026-09-21", True, True, "perm"))
    _add_page(root, "doc1")
    return root


def test_recover_staging_removes_stale_directories(pub_env):
    pub_dir = pub_env / "publish"
    pub_dir.mkdir(parents=True, exist_ok=True)
    stale_1 = pub_dir / ".staging-11111111111111111111111111111111"
    stale_2 = pub_dir / ".staging-22222222222222222222222222222222"
    stale_1.mkdir()
    stale_2.mkdir()
    (stale_1 / "junk.txt").write_text("orphan")
    keep_file = pub_dir / "valid.jsonl"
    keep_file.write_text("keep me")

    cleaned = recover_staging(pub_dir)
    assert len(cleaned) == 2
    assert not stale_1.exists()
    assert not stale_2.exists()
    assert keep_file.is_file()


def test_crash_during_staging_cleans_up_and_preserves_published(pub_env):
    root = pub_env
    res = build(root)
    assert res["published"] == 1
    published_file = root / "publish" / "maritime__doc1.jsonl"
    assert published_file.is_file()
    prior_content = published_file.read_text(encoding="utf-8")

    # Add a second document, but simulate failure during writing pages
    _add_page(root, "doc2")
    with patch("json.dumps", side_effect=RuntimeError("simulated crash during write")):
        with pytest.raises(RuntimeError, match="simulated crash during write"):
            build(root)

    # Published file from prior run must remain untouched and intact
    assert published_file.is_file()
    assert published_file.read_text(encoding="utf-8") == prior_content
    # No staging folder left behind
    staging_folders = [p for p in (root / "publish").iterdir() if p.name.startswith(".staging-")]
    assert len(staging_folders) == 0


def test_atomic_build_succeeds_and_replaces(pub_env):
    root = pub_env
    res1 = build(root)
    assert res1["published"] == 1
    pub1 = root / "publish" / "maritime__doc1.jsonl"
    assert pub1.is_file()

    # Successful build with doc2
    _add_page(root, "doc2")
    res2 = build(root)
    assert res2["published"] == 2
    pub2 = root / "publish" / "maritime__doc2.jsonl"
    assert pub1.is_file()
    assert pub2.is_file()
    staging_folders = [p for p in (root / "publish").iterdir() if p.name.startswith(".staging-")]
    assert len(staging_folders) == 0
