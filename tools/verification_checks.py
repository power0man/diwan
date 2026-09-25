"""One ordered verification contract, shared by CI and the trusted push gate.

The caller chooses explicit runtimes and working directory. No installation,
key lookup or shell parsing occurs here. A gate must use its trusted copy of
this module, not load check definitions supplied by a candidate commit.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    argv: tuple[str, ...]
    timeout_s: int = 300


def commands(python: str, node: str) -> tuple[Check, ...]:
    return (
        Check("pytest", (python, "-m", "pytest", "tests/", "-q"), 900),
        Check("docs", (python, "tools/check_docs.py", "--check")),
        Check("acceptance-0", (python, "acceptance.py")),
        *(Check(f"acceptance-{milestone}", (python, f"acceptance_m{milestone}.py"))
          for milestone in ("8b", "9", "10", "11", "12", "13")),
        Check("frontend-syntax", (node, "--check", "webui/static/app.js")),
        Check("frontend-behavior", (node, "tests/webui_frontend.cjs", "webui/static/app.js")),
        Check("acceptance-7", (python, "acceptance_m7.py")),
        Check("public-signatures", (python, "tools/sign_anchors.py", "verify", "--public-only")),
    )
