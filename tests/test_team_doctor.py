"""الطبيب: الأداةُ غيرُ المثبَّتة تُسمّى، والتغيّرُ في الإصدار أو الثنائي يُسمّى، والغائبُ لا يُثبَّت."""
from __future__ import annotations

from pathlib import Path

from team import doctor
from tests.team_fakes import FakeAdapter


def _runner_version(version: str):
    class Done:
        def __init__(self):
            self.stdout, self.stderr = version + "\n", ""
    return lambda argv, **kw: Done()


def _adapter(tmp_path: Path, name="claude", content=b"#!/bin/sh\necho 1\n"):
    binary = tmp_path / name
    binary.write_bytes(content)
    return FakeAdapter(name=name, binary=binary)


def test_unpinned_is_refused_then_pin_passes(tmp_path):
    adapter = _adapter(tmp_path)
    pins = tmp_path / "doctor.json"
    first = doctor.check([adapter], pins, runner=_runner_version("1.0"))
    assert first["status"] == "refused" and first["findings"] == ["unpinned:claude"]
    assert doctor.check([adapter], pins, pin=True, runner=_runner_version("1.0"))["status"] == "passed"
    assert doctor.check([adapter], pins, runner=_runner_version("1.0"))["status"] == "passed"


def test_changed_version_or_binary_is_named(tmp_path):
    adapter = _adapter(tmp_path)
    pins = tmp_path / "doctor.json"
    doctor.check([adapter], pins, pin=True, runner=_runner_version("1.0"))
    assert doctor.check([adapter], pins, runner=_runner_version("2.0"))["findings"] == ["version_changed:claude"]
    adapter.binary.write_bytes(b"#!/bin/sh\necho 2\n")
    assert doctor.check([adapter], pins, runner=_runner_version("1.0"))["findings"] == ["binary_changed:claude"]


def test_missing_binary_is_named_and_never_pinned(tmp_path):
    adapter = FakeAdapter(name="codex", binary=tmp_path / "absent")
    pins = tmp_path / "doctor.json"
    report = doctor.check([adapter], pins, pin=True, runner=_runner_version("1.0"))
    assert report["findings"] == ["binary_missing:codex"] and not pins.exists()


def test_missing_optional_tool_does_not_erase_or_block_other_pins(tmp_path):
    import json
    pins = tmp_path / "doctor.json"
    preserved = {"version": "old", "sha256": "kept"}
    pins.write_text(json.dumps({"opencode": preserved, "unselected": preserved}))
    present = _adapter(tmp_path, "claude")
    absent = FakeAdapter(name="opencode", binary=tmp_path / "absent")
    report = doctor.check([present, absent], pins, pin=True, runner=_runner_version("1.0"))
    assert report["findings"] == ["binary_missing:opencode"]
    actual = json.loads(pins.read_text())
    assert actual["opencode"] == actual["unselected"] == preserved
    assert actual["claude"]["version"] == "1.0"
    assert doctor.check([present], pins, runner=_runner_version("1.0"))["status"] == "passed"
