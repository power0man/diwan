"""Required contexts are unique and execute the real upstream-result gates.

The small block reader follows the checked-in indentation, not general YAML.
Each gate is intentionally one unconditional shell step; unsupported structure
fails the contract instead of being silently ignored by a partial YAML parser.
No Actions runner, credentials, or external service is used here.
"""
import os
from pathlib import Path
import re
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HOSTED = ROOT / ".github/workflows/verify-hosted.yml"
ISOLATED = ROOT / ".github/workflows/verify.yml"
GATES = (("verify-hosted", "verify"), ("verify-compat", "verify-hosted"))


def _jobs(path):
    text = path.read_text(encoding="utf-8").split("\njobs:\n", 1)[1]
    matches = list(re.finditer(r"^  ([\w-]+):\s*$", text, re.M))
    return {
        match[1]: text[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        for i, match in enumerate(matches)
    }


def _field(block, name, indent=4):
    values = re.findall(rf"^{' ' * indent}{re.escape(name)}: (.+)$", block, re.M)
    assert len(values) == 1, f"Expected one {name} at indentation {indent}: {values}"
    return values[0]


def test_required_check_names_are_unique_across_hosted_and_isolated_workflows():
    hosted, isolated = _jobs(HOSTED), _jobs(ISOLATED)
    names = [_field(job, "name") for job in (*hosted.values(), *isolated.values())]
    assert len(names) == len(set(names)), names
    assert _field(hosted["verify-hosted"], "name") == "verify-hosted"
    assert _field(hosted["verify-compat"], "name") == "verify"
    assert _field(isolated["verify"], "name") == "verify-isolated"
    # A future Python matrix keeps its entries distinct from both stable gates.
    assert _field(hosted["verify"], "name") == "verify-runtime (${{ matrix.python-version || '3.14' }})"


def test_no_job_in_any_workflow_shares_a_check_name():
    """Post-merge audit (#285): the test above reads the two verify workflows only.

    A job in any other workflow named ``verify`` (or reusing another job's name)
    would post a second check run under a required context. GitHub names a check
    run after the job's ``name`` or, without one, its id; both are read here.
    """
    names = []
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        for job_id, block in _jobs(path).items():
            declared = re.findall(r"^    name: (.+)$", block, re.M)
            assert len(declared) <= 1, (path.name, job_id, declared)
            names.append(declared[0] if declared else job_id)
    assert len(names) == len(set(names)), sorted(n for n in names if names.count(n) > 1)
    assert names.count("verify") == 1 and names.count("verify-hosted") == 1


def test_runtime_cannot_ignore_a_failed_check_or_matrix_entry():
    runtime = _jobs(HOSTED)["verify"]
    assert not re.search(r"^\s+continue-on-error:", runtime, re.M)
    assert "tools/run_verification.py" in runtime
    assert "tools/mutation_check.py" in runtime


@pytest.mark.parametrize("gate,upstream", GATES, ids=[gate for gate, _ in GATES])
def test_gates_always_wait_for_their_real_dependency(gate, upstream):
    block = _jobs(HOSTED)[gate]
    assert _field(block, "needs") == upstream
    assert _field(block, "if") == "always()"
    assert _field(block, "runs-on") == "ubuntu-latest"
    assert not re.search(r"^\s+continue-on-error:", block, re.M)
    # Extra/conditional steps cannot create an untested path to a green check.
    keys = re.findall(r"^    ([\w-]+):", block, re.M)
    assert sorted(keys) == sorted(["name", "needs", "if", "runs-on", "timeout-minutes", "steps"])
    assert len(re.findall(r"^      - name:", block, re.M)) == 1
    assert re.findall(r"^        ([\w-]+):", block, re.M) == ["env", "run"]
    expression = "needs.verify.result" if upstream == "verify" else "needs['verify-hosted'].result"
    assert _field(block, "UPSTREAM_RESULT", 10) == "${{ " + expression + " }}"


@pytest.mark.parametrize("gate", [gate for gate, _ in GATES])
@pytest.mark.parametrize("result", ["success", "failure", "cancelled", "skipped", "", "neutral"],
                         ids=["success", "failure", "cancelled", "skipped", "empty", "neutral"])
def test_real_gate_shell_requires_success(gate, result):
    block = _jobs(HOSTED)[gate]
    assert _field(block, "run", 8) == "|"
    lines = block.split("        run: |\n", 1)[1].splitlines()
    assert lines and all(not line.strip() or line.startswith("          ") for line in lines)
    script = "\n".join(line[10:] for line in lines)
    run = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                         env={**os.environ, "UPSTREAM_RESULT": result}, capture_output=True, text=True)
    assert (run.returncode == 0) is (result == "success"), (gate, result, run.stderr)
