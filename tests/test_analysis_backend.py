"""ج٨ (#44): حدُّ المحلّل بمحرّك Docker مزيّف — ما يُرسل إلى الحاوية، وما يُقبل من تقريرها.

لا يشهد هذا الملفُّ بحدٍّ حيّ؛ ذاك في `tests/test_analysis_live.py` على صورةٍ حقيقية بإيصالها.
- لا يُرسل إلى الحاوية إلا الملفّاتُ المسمّاة، ولا يُقبل منها إلا المخرجاتُ المعلنة.
- كلُّ مدخلٍ أو مخرجٍ أو كودٍ أو مهلةٍ غيرِ صالحة يُرفض باسمه قبل أيّ نداءٍ لـDocker.
- التقريرُ بياناتٌ تُفحص: سطرٌ واحد بمفاتيحه، وكلُّ مخرجٍ معلنٍ محسوبٌ مرّةً، ولا حجمَ فوق حدّ الدفتر.
- وحاويةٌ لم تخرج بصفرٍ لا يُقبل تقريرُها.
"""
from __future__ import annotations

import ast
import base64
import json
import os

import pytest

from agent import journal
from analysis import backend as analysis
from core import execution

IMAGE = "sha256:" + "a" * 64


@pytest.fixture(autouse=True)
def no_configured_backends(monkeypatch):
    monkeypatch.setattr(analysis, "_BACKENDS", {})


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "sales.csv").write_text("الفرع,الكمية\nالرياض,3\n", encoding="utf-8")
    (root / "private-notes.txt").write_text("لا تدخل الحاوية", encoding="utf-8")
    receipt = tmp_path / "analysis-receipt.json"
    receipt.write_text(json.dumps({"schema_version": 1, "image_id": IMAGE, "lock_sha256": "b" * 64,
                                   "python_version": "3.12.14"}), encoding="utf-8")
    receipt.chmod(0o600)
    return root, receipt


def _report(**changes):
    report = {"exit": 0, "timed_out": False, "stdout": "تم\n", "stderr": "",
              "outputs": {"answer.json": base64.b64encode(b'{"n": 3}').decode()}, "missing": [], "oversized": []}
    report.update(changes)
    return (json.dumps(report) + "\n").encode()


class FakeDocker:
    def __init__(self, backend):
        self.backend, self.calls, self.payloads = backend, [], []
        self.stdout, self.exit_code = _report(), 0
        self.driver = self.name = None

    def container(self):
        exit_code = 0 if self.driver == execution._RUNTIME_PREFLIGHT else self.exit_code
        return {"Id": "c" * 64, "Name": "/" + self.name, "Image": IMAGE,
                "State": {"Running": False, "Status": "exited", "ExitCode": exit_code, "Error": "",
                          "OOMKilled": False},
                "Config": {"User": "1000:1000", "Entrypoint": ["/opt/venv/bin/python"],
                           "Cmd": ["-I", "-c", self.driver], "WorkingDir": "/workspace"},
                "HostConfig": {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
                               "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"], "PidsLimit": 128,
                               "Memory": execution.MEMORY_BYTES, "MemorySwap": execution.MEMORY_BYTES,
                               "NanoCpus": 2_000_000_000, "IpcMode": "private", "CgroupnsMode": "private",
                               "LogConfig": {"Type": "none"}, "Tmpfs": dict(execution.TMPFS)},
                "Mounts": []}

    def __call__(self, *args, data=b"", timeout=20, limit=execution.MAX_OUTPUT_BYTES):
        self.calls.append(args[0] if args[0] != "image" else "image")
        if args[:2] == ("image", "inspect"):
            return 0, json.dumps([{"Id": IMAGE, "Os": "linux", "Config": {
                "Labels": {"diwan.lock-sha256": "b" * 64}}}]).encode(), b""
        if args[0] == "create":
            self.name, self.driver = args[args.index("--name") + 1], args[-1]
            return 0, ("c" * 64).encode(), b""
        if args[0] == "inspect":
            return 0, json.dumps([self.container()]).encode(), b""
        if args[0] == "start":
            self.payloads.append((self.driver, json.loads(data), limit))
            return (0, b"", b"") if self.driver == execution._RUNTIME_PREFLIGHT else (0, self.stdout, b"")
        if args[0] == "rm":
            return 0, b"", b""
        raise AssertionError(args)


@pytest.fixture
def daemon(workspace, monkeypatch):
    root, receipt = workspace
    backend = analysis.configure_analysis_backend(receipt, root)
    fake = FakeDocker(backend)
    monkeypatch.setattr(backend, "_docker", fake)
    return backend, fake, root


def test_only_named_inputs_enter_and_only_declared_outputs_return(daemon):
    backend, fake, _ = daemon
    result = backend.analyze("print(1)", ["sales.csv"], ["answer.json"])
    assert result.succeeded and result.outputs == {"answer.json": b'{"n": 3}'} and result.stdout == "تم\n"
    assert result.boundary == "docker:" + "c" * 64
    (preflight, lock_payload, _), (driver, payload, limit) = fake.payloads
    assert preflight == execution._RUNTIME_PREFLIGHT and lock_payload["lock"] == "/opt/analysis.lock"
    assert driver == analysis._ANALYSIS_DRIVER and limit == analysis.REPORT_LIMIT
    assert [item["path"] for item in payload["files"]] == ["sales.csv"]        # لا private-notes.txt
    assert payload["outputs"] == ["answer.json"] and payload["code"] == "print(1)"
    assert payload["output_file_bytes"] == analysis.MAX_OUTPUT_FILE_BYTES


@pytest.mark.parametrize("code, inputs, outputs, timeout, expected", [
    ("", [], ["a.csv"], 10, "analysis_code_invalid"),
    ("print(1)\x00", [], ["a.csv"], 10, "analysis_code_invalid"),
    ("#" * (analysis.MAX_CODE_BYTES + 1), [], ["a.csv"], 10, "analysis_code_invalid"),
    ("print(1)", [], [], 10, "analysis_outputs_invalid"),
    ("print(1)", [], [f"o{i}.csv" for i in range(analysis.MAX_OUTPUTS + 1)], 10, "analysis_outputs_invalid"),
    ("print(1)", [], ["run.sh"], 10, "analysis_output_type"),
    ("print(1)", [], ["A.csv", "a.csv"], 10, "analysis_outputs_invalid"),
    ("print(1)", [], [".hidden.csv"], 10, "execution_snapshot_invalid"),
    ("print(1)", [], ["secrets/leak.csv"], 10, "execution_snapshot_sensitive"),
    ("print(1)", ["keys/id.pem"], ["a.csv"], 10, "execution_snapshot_sensitive"),
    ("print(1)", ["../outside.csv"], ["a.csv"], 10, "execution_snapshot_invalid"),
    ("print(1)", [], ["a.csv"], 0, "analysis_timeout_invalid"),
    ("print(1)", [], ["a.csv"], analysis.MAX_TIMEOUT_S + 1, "analysis_timeout_invalid"),
    ("print(1)", [], ["a.csv"], "5", "analysis_timeout_invalid"),
    ("print(1)", ["absent.csv"], ["a.csv"], 10, "execution_snapshot_invalid"),
])
def test_an_invalid_call_is_refused_by_name_before_docker(daemon, code, inputs, outputs, timeout, expected):
    backend, fake, _ = daemon
    with pytest.raises(execution.ExecutionRefused) as err:
        backend.analyze(code, inputs, outputs, timeout_s=timeout)
    assert err.value.code == expected and fake.calls == []


def test_a_linked_input_is_refused_before_docker(daemon):
    backend, fake, root = daemon
    os.symlink(root / "sales.csv", root / "linked.csv")
    with pytest.raises(execution.ExecutionRefused) as err:
        backend.analyze("print(1)", ["linked.csv"], ["a.csv"])
    assert err.value.code == "execution_snapshot_invalid" and "start" not in fake.calls


def _undeclared():
    return _report(outputs={"answer.json": "e30=", "extra.csv": "YQ=="})


@pytest.mark.parametrize("stdout", [
    _report() + _report(),                                                    # سطران
    b"not json\n",
    _report()[:-1],                                                           # بلا نهاية سطر
    _undeclared(),                                                            # مخرجٌ غيرُ معلن
    _report(outputs={}),                                                      # معلنٌ بلا حكم
    _report(outputs={}, missing=["answer.json", "answer.json"]),              # محسوبٌ مرّتين
    _report(outputs={"answer.json": "!!!"}),                                  # base64 غير صالح
    _report(outputs={"answer.json": base64.b64encode(b"x" * (analysis.MAX_OUTPUT_FILE_BYTES + 1)).decode()}),
    _report(exit=None),                                                       # بلا رمزٍ ولا مهلة
    _report(exit=0, timed_out=True),
    _report(exit=300),
    _report(stdout="x" * (analysis.STDOUT_TAIL + 1)),
    _report(extra=True),
    b'{"exit": 0, "exit": 0, "timed_out": false, "stdout": "", "stderr": "", "outputs": {}, '
    b'"missing": ["answer.json"], "oversized": []}\n',                        # مفتاحٌ مكرَّر
], ids=["two_lines", "not_json", "no_newline", "undeclared", "unaccounted", "counted_twice", "bad_base64",
        "oversized_bytes", "exit_none", "exit_and_timeout", "exit_range", "long_stdout", "extra_key", "duplicate"])
def test_a_report_that_is_not_exactly_the_contract_is_refused(daemon, stdout):
    backend, fake, _ = daemon
    fake.stdout = stdout
    with pytest.raises(execution.ExecutionRefused) as err:
        backend.analyze("print(1)", ["sales.csv"], ["answer.json"])
    assert err.value.code == "analysis_report_invalid"


def test_a_failed_script_and_missing_outputs_are_reported_not_hidden(daemon):
    backend, fake, _ = daemon
    fake.stdout = _report(exit=1, stderr="Traceback: KeyError", outputs={}, missing=["answer.json"])
    result = backend.analyze("print(1)", ["sales.csv"], ["answer.json"])
    assert not result.succeeded and result.exit_code == 1 and result.missing == ("answer.json",)
    assert "KeyError" in result.stderr


def test_a_container_that_did_not_exit_cleanly_is_not_believed(daemon):
    backend, fake, _ = daemon
    fake.exit_code = 125
    with pytest.raises(execution.ExecutionRefused) as err:
        backend.analyze("print(1)", ["sales.csv"], ["answer.json"])
    assert err.value.code == "analysis_outcome_unverified"


def test_every_output_fits_the_journal_that_will_write_it():
    assert analysis.MAX_OUTPUT_FILE_BYTES == journal.MAX_BYTES


def test_the_drivers_are_valid_python():
    ast.parse(analysis._ANALYSIS_DRIVER)
    ast.parse(execution._RUNTIME_PREFLIGHT)


def test_without_a_configured_image_there_is_no_analysis(tmp_path):
    with pytest.raises(execution.ExecutionRefused) as err:
        analysis.analysis_backend(tmp_path)
    assert err.value.code == "analysis_backend_unavailable"
