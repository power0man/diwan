"""Collect the offline deployment packet tests in the canonical pytest suite.

The standalone tests keep their sibling ``import check`` without changing the
process import path or retaining a generic ``check`` module in sys.modules.
"""
from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch


_ROOT = Path(__file__).resolve().parents[1] / "deployment/cloudflare-hf/staging-evidence"


def _module(name, filename):
    spec = importlib.util.spec_from_file_location(name, _ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_check = _module("diwan_staging_evidence_check", "check.py")
with patch.dict(sys.modules, {"check": _check}):
    _tests = _module("diwan_staging_evidence_tests", "test_check.py")

class PacketTests(_tests.PacketTests):
    # The mutation runner attributes FAILED node IDs, not pytest SUBFAILED
    # events. Keep all successful cases and fail the method on its first error.
    def subTest(self, **params):
        return nullcontext()
