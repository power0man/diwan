import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import wave

import pytest

from multimodal import local_asr as asr
from multimodal.codec import MediaError
from tools import transcribe_audio as cli


def wav(silence=False):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes((b"\0\0" if silence else b"\1\0") * 160)
    return buffer.getvalue()


@pytest.fixture
def artifacts(tmp_path):
    binary, model = tmp_path / "whisper-cli", tmp_path / "model.bin"
    binary.write_bytes(b"trusted binary fixture")
    model.write_bytes(b"local weights fixture")
    return {"binary": binary, "model": model,
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest()}


def fake_run(monkeypatch, payload=None, *, code=0):
    calls = []
    def run(command, **options):
        calls.append((command, options))
        output = Path(command[command.index("-of") + 1]).with_suffix(".json")
        output.write_bytes(payload if payload is not None else json.dumps({
            "result": {"language": "ar"}, "transcription": [{"text": " مرحبا"}, {"text": " بالعالم "}]
        }).encode())
        return SimpleNamespace(returncode=code)
    monkeypatch.setattr(asr.subprocess, "run", run)
    return calls


def test_success_is_arabic_cpu_text_with_private_temporary_input(monkeypatch, artifacts):
    calls = fake_run(monkeypatch)
    result = asr.transcribe(wav(), **artifacts, timeout=9, threads=1)
    command, options = calls[0]
    assert command[command.index("-l") + 1] == "ar"
    assert command[command.index("-t") + 1] == "1"
    assert "-ng" in command and "-nf" in command and "-oj" in command
    assert options["timeout"] == 9 and options["stdin"] == subprocess.DEVNULL
    assert options["stdout"] == options["stderr"] == subprocess.DEVNULL
    assert not Path(command[command.index("-f") + 1]).exists()
    assert result["text"] == "مرحبا بالعالم" and result["language"] == "ar"
    assert result["audio_sha256"] == hashlib.sha256(wav()).hexdigest()
    assert result["release_ready"] is False


def test_digital_silence_never_calls_the_model(monkeypatch, artifacts):
    calls = fake_run(monkeypatch)
    result = asr.transcribe(wav(silence=True), **artifacts)
    assert not calls and result["status"] == "no_speech" and result["text"] == ""
    assert result["detector"] == "digital_silence_only" and result["release_ready"] is False


@pytest.mark.parametrize("field", ["binary_sha256", "model_sha256"])
def test_wrong_identity_never_runs(monkeypatch, artifacts, field):
    calls = fake_run(monkeypatch)
    artifacts[field] = "0" * 64
    with pytest.raises(asr.ASRError, match="asr_identity_mismatch"):
        asr.transcribe(wav(), **artifacts)
    assert not calls


@pytest.mark.parametrize("override", [{"binary": Path("relative")}, {"binary_sha256": "bad"}])
def test_identity_requires_absolute_path_and_sha256(monkeypatch, artifacts, override):
    fake_run(monkeypatch)
    with pytest.raises(asr.ASRError, match="asr_identity_invalid"):
        asr.transcribe(wav(), **{**artifacts, **override})


def test_artifact_must_be_a_nonempty_regular_file(monkeypatch, artifacts):
    fake_run(monkeypatch)
    artifacts["model"].write_bytes(b"")
    with pytest.raises(asr.ASRError, match="asr_artifact_invalid"):
        asr.transcribe(wav(), **artifacts)


@pytest.mark.parametrize("override", [{"timeout": 0}, {"timeout": 121}, {"timeout": True},
                                      {"threads": 0}, {"threads": 5}, {"threads": True}])
def test_limits_cannot_be_disabled(monkeypatch, artifacts, override):
    fake_run(monkeypatch)
    with pytest.raises(asr.ASRError, match="asr_limits_invalid"):
        asr.transcribe(wav(), **artifacts, **override)


def test_invalid_audio_never_runs(monkeypatch, artifacts):
    calls = fake_run(monkeypatch)
    with pytest.raises(MediaError):
        asr.transcribe(b"not a wave", **artifacts)
    assert not calls


def test_process_failure_is_not_a_transcript(monkeypatch, artifacts):
    fake_run(monkeypatch, code=4)
    with pytest.raises(asr.ASRError, match="asr_process_failed"):
        asr.transcribe(wav(), **artifacts)


def test_timeout_is_named_and_input_is_removed(monkeypatch, artifacts):
    selected = []
    def expire(command, **options):
        selected.append(Path(command[command.index("-f") + 1]))
        raise subprocess.TimeoutExpired(command, options["timeout"])
    monkeypatch.setattr(asr.subprocess, "run", expire)
    with pytest.raises(asr.ASRError, match="asr_timeout"):
        asr.transcribe(wav(), **artifacts, timeout=1)
    assert not selected[0].exists()


def test_real_timeout_reaps_the_trusted_process(artifacts, tmp_path):
    pidfile = tmp_path / "pid"
    binary = artifacts["binary"]
    binary.write_text(f"#!{sys.executable}\nimport os, time\n"
                      f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(3)\n")
    binary.chmod(0o700)
    artifacts["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    started = time.monotonic()
    with pytest.raises(asr.ASRError, match="asr_timeout"):
        asr.transcribe(wav(), **artifacts, timeout=1)
    assert time.monotonic() - started < 2.5
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


@pytest.mark.parametrize("payload", [b"not json", b'{"result": {"language":"en"}, "transcription":[]}',
                                      b'{"result": {"language":"ar"}, "transcription":{}}'],
                         ids=["invalid-json", "wrong-language", "invalid-text"])
def test_malformed_or_wrong_language_result_is_refused(monkeypatch, artifacts, payload):
    fake_run(monkeypatch, payload)
    with pytest.raises(asr.ASRError, match="asr_result_invalid"):
        asr.transcribe(wav(), **artifacts)


def test_result_read_is_bounded(monkeypatch, artifacts):
    payload = json.dumps({"result": {"language": "ar"}, "transcription": [],
                          "padding": "x" * asr.MAX_RESULT_BYTES}).encode()
    fake_run(monkeypatch, payload)
    with pytest.raises(asr.ASRError, match="asr_result_too_large"):
        asr.transcribe(wav(), **artifacts)


def test_text_is_bounded(monkeypatch, artifacts):
    fake_run(monkeypatch, json.dumps({"result": {"language": "ar"},
        "transcription": [{"text": "x" * (asr.MAX_TEXT_CHARS + 1)}]}).encode())
    with pytest.raises(asr.ASRError, match="asr_text_too_large"):
        asr.transcribe(wav(), **artifacts)


def test_cli_outputs_selected_transcript(monkeypatch, artifacts, tmp_path, capsys):
    fake_run(monkeypatch)
    selected = tmp_path / "selected.wav"
    selected.write_bytes(wav())
    arguments = ["--file", str(selected)]
    for key, value in artifacts.items():
        arguments += ["--" + key.replace("_", "-"), str(value)]
    assert cli.main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["text"] == "مرحبا بالعالم"


def test_cli_failure_never_reports_success(monkeypatch, artifacts, tmp_path, capsys):
    fake_run(monkeypatch, code=1)
    selected = tmp_path / "selected.wav"
    selected.write_bytes(wav())
    arguments = ["--file", str(selected)]
    for key, value in artifacts.items():
        arguments += ["--" + key.replace("_", "-"), str(value)]
    assert cli.main(arguments) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "asr_process_failed" and result["release_ready"] is False


@pytest.mark.parametrize("entry", ["binary", "model"])
def test_artifact_directory_is_refused_before_execution(monkeypatch, artifacts, tmp_path, entry):
    calls = fake_run(monkeypatch)
    directory = tmp_path / "directory"
    directory.mkdir()
    artifacts[entry] = directory
    with pytest.raises(asr.ASRError, match="asr_artifact_invalid"):
        asr.transcribe(wav(), **artifacts)
    assert not calls


def test_artifact_size_limit_includes_boundary_and_rejects_excess(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"1234")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert asr._identity(artifact, digest, 4) == artifact
    with pytest.raises(asr.ASRError, match="asr_artifact_invalid"):
        asr._identity(artifact, digest, 3)


def test_cli_refuses_valid_png_before_artifact_access(monkeypatch, artifacts, tmp_path, capsys):
    from tests.test_multimodal_codec import png
    selected = tmp_path / "selected.png"
    selected.write_bytes(png())
    calls = fake_run(monkeypatch)
    def unexpected_identity(*args):
        pytest.fail("non-audio must be rejected before artifact access")
    monkeypatch.setattr(asr, "_identity", unexpected_identity)
    arguments = ["--file", str(selected)]
    for key, value in artifacts.items():
        arguments += ["--" + key.replace("_", "-"), str(value)]
    assert cli.main(arguments) == 2
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "error", "error_code": "asr_audio_required", "release_ready": False}
    assert not calls


def test_result_symlink_is_refused(monkeypatch, artifacts, tmp_path):
    target = tmp_path / "external.json"
    target.write_text(json.dumps({"result": {"language": "ar"}, "transcription": [{"text": "مرحبا"}]}))
    def run(command, **options):
        Path(command[command.index("-of") + 1]).with_suffix(".json").symlink_to(target)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(asr.subprocess, "run", run)
    with pytest.raises(asr.ASRError, match="asr_result_invalid"):
        asr.transcribe(wav(), **artifacts)


@pytest.mark.parametrize("text_bytes", [b"\xed\xa0\x80", b"\\ud800", b"\\udfff"],
                         ids=["invalid-utf8", "escaped-high", "escaped-low"])
def test_cli_refuses_lone_surrogates(monkeypatch, artifacts, tmp_path, capsys, text_bytes):
    fake_run(monkeypatch, b'{"result":{"language":"ar"},"transcription":[{"text":"' + text_bytes + b'"}]}')
    selected = tmp_path / "selected.wav"
    selected.write_bytes(wav())
    arguments = ["--file", str(selected)]
    for key, value in artifacts.items():
        arguments += ["--" + key.replace("_", "-"), str(value)]
    assert cli.main(arguments) == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "error", "error_code": "asr_result_invalid", "release_ready": False}


def test_pilot_evidence_conforms_to_public_schema():
    from tools.probe_evidence import validate_payload
    path = Path(__file__).resolve().parents[1] / "docs/probe/arabic-whisper-pilot-20261003.json"
    assert validate_payload(json.loads(path.read_text())) == []
