"""Core morphology must work without the persistence layer being importable."""
import subprocess
import sys
from pathlib import Path


def test_pure_analysis_has_no_projection_or_sqlite_dependency():
    script = '''
import builtins
real_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "sqlite3" or name == "projections" or name.startswith("projections."):
        raise AssertionError("persistence imported by core")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
from core.linguistics import morphology
assert morphology.analyze("كتاب").normalized
assert not hasattr(morphology, "build_projection")
assert not hasattr(morphology, "search_by_root")
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_projection_still_builds_and_searches(tmp_path):
    from projections.morphology import build_projection, search_by_root
    path = tmp_path / 'morph.db'
    assert build_projection(['كاتب'], path) == 1
    assert search_by_root(path, 'كتب')[0]['word'] == 'كاتب'
