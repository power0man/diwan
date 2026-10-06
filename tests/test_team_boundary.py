"""حدُّ الاستخراج (ق٧٦ البند ٩): العامُّ في `team/` لا يستورد ما يخصّ ديوان؛ ولا شيءَ في `team/` يدمج أو يدفع قسرًا."""
from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path

from team.projects import base as project_base
from team.projects.diwan import DiwanProject

ROOT = Path(__file__).resolve().parents[1]
TEAM = ROOT / "team"
DIWAN_SPECIFIC = TEAM / "projects" / "diwan.py"
FORBIDDEN_ROOTS = ("tools", "registry", "evaluation", "agent", "conversation", "services", "webui", "providers", "nodes")
ALLOWED_CORE = {"core.ledger", "core.quoted", "core.canonical"}
MERGE_WORDS = re.compile(r"pr merge|release create|push --force|push -f\b|merge --no-verify")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_generic_team_modules_do_not_import_diwan_specifics():
    offenders = {}
    for path in sorted(TEAM.rglob("*.py")):
        if path == DIWAN_SPECIFIC:
            continue
        bad = {m for m in _imports(path) if m.split(".")[0] in FORBIDDEN_ROOTS or (m.startswith("core") and m not in ALLOWED_CORE)}
        if bad or 'ROOT / "tools"' in path.read_text(encoding="utf-8"):
            offenders[str(path.relative_to(ROOT))] = sorted(bad) or ["sys.path tools"]
    assert offenders == {}


def test_nothing_in_team_merges_tags_or_force_pushes():
    hits = {str(p.relative_to(ROOT)): MERGE_WORDS.findall(p.read_text(encoding="utf-8")) for p in TEAM.rglob("*.py")}
    assert {k: v for k, v in hits.items() if v} == {}


def test_diwan_implements_every_project_method():
    abstract = {name for name, member in vars(project_base.ProjectAdapter).items()
                if callable(member) and "NotImplementedError" in ast.dump(ast.parse(textwrap.dedent(inspect.getsource(member))))}
    missing = {name for name in abstract if vars(DiwanProject).get(name) is None}
    assert abstract and missing == set()
