"""Synthetic acceptance test for Milestone 14 (Benchmark and Review Gate)."""
from pathlib import Path
from acceptance_m14 import run_synthetic_m14
from tests.private_stores import needs_m14_suite


@needs_m14_suite
def test_m14_synthetic_acceptance(tmp_path):
    res = run_synthetic_m14(tmp_path)
    assert res["suite_cases"] == 40
    assert all(res["checks"].values()), res
    assert res["synthetic_calls"] > 0
