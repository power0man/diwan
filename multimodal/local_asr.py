"""Explicit, optional whisper.cpp pilot. No tool dispatch, downloads or history.

The operator supplies trusted local artifacts. Hashes identify them immediately
before execution; this is not a sandbox against a malicious/replaced executable.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import wave

from multimodal.codec import pack_media

MAX_RESULT_BYTES = 65536
MAX_TEXT_CHARS = 8192


class ASRError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _identity(path: Path, expected: str, limit: int) -> Path:
    if not path.is_absolute() or type(expected) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ASRError("asr_identity_invalid")
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ASRError("asr_artifact_invalid")
        digest = hashlib.sha256()
        with resolved.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ASRError("asr_identity_mismatch")
        return resolved
    except OSError:
        raise ASRError("asr_unavailable") from None


def transcribe(audio: bytes, *, binary: Path, binary_sha256: str,
               model: Path, model_sha256: str, timeout: int = 120,
               threads: int = 2) -> dict:
    """Arabic PCM WAV, at most eight seconds; CPU only, one bounded invocation."""
    if type(timeout) is not int or not 1 <= timeout <= 120 or type(threads) is not int or not 1 <= threads <= 4:
        raise ASRError("asr_limits_invalid")
    document = pack_media(audio, "selected.wav")
    if document["kind"] != "audio":
        raise ASRError("asr_audio_required")
    executable = _identity(binary, binary_sha256, 64 * 1024 * 1024)
    weights = _identity(model, model_sha256, 2 * 1024 * 1024 * 1024)
    with wave.open(io.BytesIO(audio), "rb") as reader:
        digital_silence = not any(reader.readframes(reader.getnframes()))
    if digital_silence:
        return {"status": "no_speech", "text": "", "language": "ar",
                "detector": "digital_silence_only", "audio_sha256": document["sha256"],
                "binary_sha256": binary_sha256, "model_sha256": model_sha256,
                "release_ready": False}
    with tempfile.TemporaryDirectory(prefix="diwan-asr-") as directory:
        work = Path(directory)
        selected = work / "selected.wav"
        selected.write_bytes(audio)
        prefix = work / "result"
        command = [str(executable), "-m", str(weights), "-f", str(selected),
                   "-l", "ar", "-t", str(threads), "-ng", "-nf", "-oj", "-of", str(prefix), "-np"]
        try:
            run = subprocess.run(command, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 cwd=work, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise ASRError("asr_timeout") from None
        except OSError:
            raise ASRError("asr_unavailable") from None
        if run.returncode != 0:
            raise ASRError("asr_process_failed")
        try:
            result_file = prefix.with_suffix(".json")
            if result_file.is_symlink():
                raise ASRError("asr_result_invalid")
            with result_file.open("rb") as source:
                raw = source.read(MAX_RESULT_BYTES + 1)
            if len(raw) > MAX_RESULT_BYTES:
                raise ASRError("asr_result_too_large")
            result = json.loads(raw.decode("utf-8"))
            if type(result) is not dict or result.get("result", {}).get("language") != "ar":
                raise ASRError("asr_result_invalid")
            segments = result["transcription"]
            if type(segments) is not list or any(type(s) is not dict or type(s.get("text")) is not str for s in segments):
                raise ASRError("asr_result_invalid")
            text = "".join(s["text"] for s in segments).strip()
            text.encode("utf-8")  # Reject escaped lone surrogates as well as invalid UTF-8 bytes.
            if len(text) > MAX_TEXT_CHARS:
                raise ASRError("asr_text_too_large")
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
            if isinstance(exc, ASRError):
                raise
            raise ASRError("asr_result_invalid") from None
    return {"status": "ok", "text": text, "language": "ar",
            "audio_sha256": document["sha256"], "binary_sha256": binary_sha256,
            "model_sha256": model_sha256, "release_ready": False}
