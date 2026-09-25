"""Acceptance test for Milestone 15 (Retrieval Ladder & Completeness)."""
from pathlib import Path
from acceptance_m15 import run_acceptance_m15
from tests.private_stores import needs_corpus


@needs_corpus
def test_m15_acceptance(tmp_path):
    res = run_acceptance_m15(tmp_path)
    assert res["status"] == "passed"
    assert all(res["checks"].values()), res
