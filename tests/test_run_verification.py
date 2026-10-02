"""Real subprocess outcomes must not become green through the public marker."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
import sys

import pytest

from tools import run_verification as runner
from tools.verification_checks import Check, commands

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_MARKER = {"schema_version": 1, "kind": "public_export",
                 "excluded_streams": ["sources/acquisitions.jsonl"]}


def _exit_check(code, *, name="probe", declared=None):
    return Check(name, (sys.executable, "-c", f"raise SystemExit({code})"),
                 public_unavailable_exit_code=declared)


def _later_success(root):
    return Check("later", (sys.executable, "-c",
                           "from pathlib import Path; Path('later-ran').write_text('yes')"))


def _configure(monkeypatch, root, checks, marker=PUBLIC_MARKER):
    monkeypatch.setattr(runner, "__file__", str(root / "tools" / "run_verification.py"))
    monkeypatch.setattr(sys, "argv", ["run_verification.py"])
    monkeypatch.setattr(runner, "commands", lambda *_: tuple(checks))
    if marker is not None:
        text = marker if isinstance(marker, str) else json.dumps(marker)
        (root / "PUBLIC-EXPORT.json").write_text(text, encoding="utf-8")


def test_only_acceptance_seven_declares_the_public_unavailability_exit():
    checks = commands("chosen-python", "chosen-node")
    assert [(c.name, c.public_unavailable_exit_code) for c in checks
            if c.public_unavailable_exit_code is not None] == [("acceptance-7", 3)]
    acceptance = next(c for c in checks if c.name == "acceptance-7")
    assert acceptance.argv == ("chosen-python", "acceptance_m7.py")
    assert Check("arbitrary", ("chosen-python",)).public_unavailable_exit_code is None


@pytest.mark.parametrize("name", ["pytest", "docs", "public-signatures", "arbitrary", "acceptance-7"])
def test_unrelated_exit_three_fails_closed_even_with_a_public_marker(monkeypatch, tmp_path, capfd, name):
    _configure(monkeypatch, tmp_path, [_exit_check(3, name=name), _later_success(tmp_path)])
    assert runner.main() == 1
    output = capfd.readouterr()
    assert f"{name}: failed (3)" in output.err
    assert "status=unavailable" not in output.out
    assert not (tmp_path / "later-ran").exists()


def test_real_pytest_internal_error_fails_before_later_success(monkeypatch, tmp_path, capfd):
    (tmp_path / "conftest.py").write_text(
        'def pytest_configure(config):\n    raise RuntimeError("synthetic verification internal error")\n')
    (tmp_path / "test_probe.py").write_text("def test_probe():\n    assert True\n")
    check = Check("pytest", (sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider",
                              str(tmp_path / "test_probe.py")), 30)
    _configure(monkeypatch, tmp_path, [check, _later_success(tmp_path)])
    assert runner.main() == 1
    output = capfd.readouterr()
    assert "INTERNALERROR" in output.out + output.err
    assert "synthetic verification internal error" in output.out + output.err
    assert "pytest: failed (3)" in output.err
    assert "status=unavailable" not in output.out
    assert not (tmp_path / "later-ran").exists()


def test_declared_acceptance_exit_three_is_unavailable_and_runs_the_next_check(monkeypatch, tmp_path, capfd):
    _configure(monkeypatch, tmp_path, [_exit_check(3, name="acceptance-7", declared=3),
                                      _later_success(tmp_path)])
    assert runner.main() == 0
    output = capfd.readouterr()
    assert "acceptance-7: unavailable by declared limit (public export)" in output.out
    assert "status=unavailable returncode=3" in output.out
    assert (tmp_path / "later-ran").read_text() == "yes"


def test_real_acceptance_seven_preserves_its_missing_sources_contract(monkeypatch, tmp_path, capfd):
    acceptance = next(c for c in commands(sys.executable, "unused-node") if c.name == "acceptance-7")
    # Run the real entry point against a synthetic public root. No owner store is read.
    script = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); import acceptance_m7 as a; "
              f"from pathlib import Path; a.ROOT = Path({str(tmp_path)!r}); raise SystemExit(a.main())")
    _configure(monkeypatch, tmp_path, [replace(acceptance, argv=(sys.executable, "-c", script)),
                                      _later_success(tmp_path)])
    assert runner.main() == 0
    output = capfd.readouterr()
    assert '"code": "sources_missing"' in output.out
    assert "status=unavailable returncode=3" in output.out
    assert (tmp_path / "later-ran").read_text() == "yes"


@pytest.mark.parametrize("marker", [None, "{not json", "[]", {"kind": "private"}])
def test_declared_exit_three_fails_without_a_valid_public_marker(monkeypatch, tmp_path, capfd, marker):
    _configure(monkeypatch, tmp_path, [_exit_check(3, name="acceptance-7", declared=3),
                                      _later_success(tmp_path)], marker=marker)
    assert runner.main() == 1
    output = capfd.readouterr()
    assert "acceptance-7: failed (3)" in output.err
    assert "status=unavailable" not in output.out
    assert not (tmp_path / "later-ran").exists()


@pytest.mark.parametrize("code", [1, 2, 4, 5])
def test_other_nonzero_exits_fail_even_for_a_declared_check(monkeypatch, tmp_path, capfd, code):
    _configure(monkeypatch, tmp_path, [_exit_check(code, name="acceptance-7", declared=3),
                                      _later_success(tmp_path)])
    assert runner.main() == 1
    assert f"status=failed returncode={code}" in capfd.readouterr().err
    assert not (tmp_path / "later-ran").exists()


def test_a_later_failure_cannot_be_masked_by_declared_unavailability(monkeypatch, tmp_path, capfd):
    _configure(monkeypatch, tmp_path, [_exit_check(3, name="acceptance-7", declared=3),
                                      _exit_check(1, name="public-signatures"), _later_success(tmp_path)])
    assert runner.main() == 1
    output = capfd.readouterr()
    assert "status=unavailable returncode=3" in output.out
    assert "public-signatures: failed (1)" in output.err
    assert not (tmp_path / "later-ran").exists()


def test_timeout_has_a_distinct_failure_status_and_stops_the_run(monkeypatch, tmp_path, capfd):
    check = Check("slow", (sys.executable, "-c", "import time; time.sleep(60)"), timeout_s=0.1)
    _configure(monkeypatch, tmp_path, [check, _later_success(tmp_path)])
    assert runner.main() == 1
    output = capfd.readouterr()
    assert "slow: status=timeout limit_s=0.1" in output.err
    assert re.search(r"elapsed_s=\d+\.\d{3}", output.err)
    assert "executable_missing" not in output.err
    assert not (tmp_path / "later-ran").exists()


def test_missing_executable_has_a_distinct_failure_status_and_stops_the_run(monkeypatch, tmp_path, capfd):
    missing = str(tmp_path / "missing-executable")
    _configure(monkeypatch, tmp_path, [Check("missing", (missing,)), _later_success(tmp_path)])
    assert runner.main() == 1
    output = capfd.readouterr()
    assert f"missing: status=executable_missing executable={missing!r}" in output.err
    assert re.search(r"elapsed_s=\d+\.\d{3}", output.err)
    assert "status=timeout" not in output.err
    assert not (tmp_path / "later-ran").exists()


def test_other_oserror_has_a_distinct_failure_status_and_stops_the_run(monkeypatch, tmp_path, capfd):
    # Executing a directory is a real EACCES failure on the supported POSIX runtimes.
    _configure(monkeypatch, tmp_path, [Check("denied", (str(tmp_path),)), _later_success(tmp_path)])
    assert runner.main() == 1
    output = capfd.readouterr()
    assert "denied: status=execution_error errno=13" in output.err
    assert re.search(r"elapsed_s=\d+\.\d{3}", output.err)
    assert "executable_missing" not in output.err
    assert not (tmp_path / "later-ran").exists()


def test_success_reports_status_and_elapsed_time_for_each_check(monkeypatch, tmp_path, capfd):
    _configure(monkeypatch, tmp_path, [_exit_check(0, name="first"), _exit_check(0, name="second")])
    # Keep subprocess timing real; only the runner's elapsed clock is deterministic.
    clock = iter([10.0, 10.5, 11.0, 11.25])
    monkeypatch.setattr(runner, "time", type("Clock", (), {"monotonic": lambda: next(clock)}))
    assert runner.main() == 0
    output = capfd.readouterr()
    assert "first: status=passed returncode=0 elapsed_s=0.500" in output.out
    assert "second: status=passed returncode=0 elapsed_s=0.250" in output.out
    assert output.err == ""
