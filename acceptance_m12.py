#!/usr/bin/env python3
"""قبول آلية صور وصوت محليين؛ ليس حكمًا على جودة الفهم أو العربية.

المسار المصطنع بلا شبكة. التشغيل الحي الصريح يرسل صورة تطوير وصوتًا
يختاره المراجع: طلبًا لكل وسيط، ثم يسترجع المحفوظ بلا نداء جديد.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import re
import struct
import tempfile
import wave
import zlib

IMAGE_NAME = "public-development-shapes.png"
AUDIO_NAME = "public-development-audio.wav"
IMAGE_PROMPT = "صف الأشكال والألوان الظاهرة في الصورة بعبارة عربية موجزة مرتبة من اليسار إلى اليمين."
AUDIO_PROMPT = "اكتب الكلام العربي المسموع في المقطع. إن لم تسمع كلامًا واضحًا فصرح بذلك دون تخمين."
FIXTURE_VERSION = 1


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


def png_fixture() -> bytes:
    """Public development image: red square, green disk, blue triangle on white."""
    width, height = 240, 120
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)  # PNG filter None.
        for x in range(width):
            color = (255, 255, 255)
            if 15 <= x < 65 and 35 <= y < 85:
                color = (220, 30, 30)
            elif (x - 120) ** 2 + (y - 60) ** 2 <= 27 ** 2:
                color = (20, 160, 60)
            elif 30 <= y <= 90 and abs(x - 195) * 2 <= y - 30:
                color = (30, 70, 220)
            scanlines.extend(color)
    return (b"\x89PNG\r\n\x1a\n" +
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=9)) +
            _png_chunk(b"IEND", b""))


def wav_fixture() -> bytes:
    """400ms of 440Hz PCM; deliberately contains no speech or ASR reference."""
    rate, samples = 16000, 6400
    frames = b"".join(struct.pack("<h", round(8192 * math.sin(2 * math.pi * 440 * i / rate)))
                      for i in range(samples))
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(frames)
    return output.getvalue()


def fixture_manifest() -> dict:
    return {"schema_version": FIXTURE_VERSION, "scope": "public_development_synthetic",
            "image": {"sha256": hashlib.sha256(png_fixture()).hexdigest(),
                      "width": 240, "height": 120,
                      "description": "red square; green disk; blue triangle, left to right"},
            "audio": {"sha256": hashlib.sha256(wav_fixture()).hexdigest(),
                      "sample_rate": 16000, "channels": 1, "sample_width": 2,
                      "frames": 6400, "signal": "440Hz tone; no speech"},
            "semantic_quality": "not_assessed", "independent_bank": "not_provided"}


def _modules():
    from multimodal.codec import decode_request, pack_media, read_selected
    from services.media_assistant import MediaAssistant
    return MediaAssistant, decode_request, pack_media, read_selected


class _ReplaySentinel:
    """Any use during replay is a failed contract, including metadata access."""
    def __init__(self):
        self.touches = []

    def __getattr__(self, name):
        self.touches.append(name)
        raise AssertionError("replay_dependency_used")


def _write_fixture(path, raw):
    import os
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)


def _counters(providers):
    values = {"provider_calls": sum(len(p.requests) for p in providers.values()),
              "estimate_calls": sum(p.estimates for p in providers.values())}
    for key in ("chat_calls", "metadata_calls"):
        if all(type(getattr(p.delegate, key, None)) is int for p in providers.values()):
            values[key] = sum(getattr(p.delegate, key) for p in providers.values())
    return values


def _public(checks, providers, initial, *, live):
    after = _counters(providers)
    report = {"schema_version": 1,
              "scope": "live_public_development_media_mechanism" if live else "synthetic_media_mechanism",
              "checks": checks, "passed": sum(v is True for v in checks.values()), "total": len(checks),
              "counter_scope": "one_image_turn_and_one_audio_turn", "snapshot_scope": "state_bytes",
              "quality_pending": True, "image_understanding": "not_assessed",
              "arabic_asr_quality": "not_assessed", "human_review": "not_required_q49",
              "automated_review": "pending",
              "semantic_injection_resistance": "not_established", "independent_bank": "not_provided",
              "m8b_complete": False, "release_ready": False}
    for name, before in initial.items():
        report[f"initial_{name}"] = before
        report[f"replay_{name}"] = after[name] - before
    return report


def _run_pair(root, *, providers, models, audio_bytes, live=False, check_rejections=False):
    from acceptance_m9 import _snapshot
    from workspace_tools.preferences import Preferences
    MediaAssistant, decode_request, pack_media, read_selected = _modules()
    root.mkdir(parents=True, mode=0o700)
    selected = root / "selected"
    selected.mkdir(mode=0o700)
    preferences = Preferences(root / "preferences")
    prefs = preferences.set("response_language", "ar", expected_revision=0)
    fixtures = {"image": (IMAGE_NAME, png_fixture(), IMAGE_PROMPT),
                "audio": (AUDIO_NAME, audio_bytes, AUDIO_PROMPT)}
    checks, evidence, initial = {}, {"fixture_scope": "public_development"}, {}
    for name, raw, _ in fixtures.values():
        _write_fixture(selected / name, raw)
    original_sources = _snapshot(selected)
    assistants, documents, results, frozen = {}, {}, {}, {}
    try:
        for kind, (name, raw, prompt) in fixtures.items():
            doc = read_selected(selected / name)
            documents[kind] = doc
            model, version = models[kind]
            assistant = MediaAssistant.open(root / "sessions", kind,
                model=model, model_version=version, provider=providers[kind], preferences=preferences)
            assistants[kind] = assistant
            result = assistant.ask(kind + "-turn", prompt, media=(doc,))
            results[kind] = result
            requests = providers[kind].requests
            checks[kind + "_one_fresh_unverified_answer"] = (
                len(requests) == 1 and result["kind"] == "assistant_message" and
                result["status"] == "complete" and result["verification"] == "unverified" and
                result["replayed"] is False)
            request = requests[0] if requests else None
            envelope = decode_request(request.messages[-1].content) if request is not None else {}
            checks[kind + "_bytes_frozen_in_governed_request"] = (
                request is not None and [m.role for m in request.messages] == ["system", "user"] and
                envelope.get("user_request") == prompt and envelope.get("media") == [doc] and
                base64.b64decode(doc["data_base64"], validate=True) == raw and
                doc["sha256"] == hashlib.sha256(raw).hexdigest())
            checks[kind + "_local_only_without_model_tools"] = (
                request is not None and request.data_policy == "local_only" and request.tools == ())
            checks[kind + "_visible_input_witness_matches"] = (
                result["user_request"] == prompt and "text" not in result and
                result["inputs"]["media"] == [{k: v for k, v in doc.items() if k != "data_base64"}] and
                result["inputs"]["preferences"] == {k: prefs[k] for k in ("revision", "sha256")} and
                envelope.get("preferences") == prefs)
            frozen[kind] = assistant.inspect(kind + "-turn")
            checks[kind + "_inspection_has_original_bytes"] = (
                frozen[kind]["media"] == [doc] and frozen[kind]["preferences"] == prefs and
                frozen[kind]["verification"] == "unverified")
        initial = _counters(providers)
        checks["exactly_two_initial_calls"] = initial["provider_calls"] == 2
        if "chat_calls" in initial:
            checks["exactly_two_initial_chat_requests"] = initial["chat_calls"] == 2
        if "metadata_calls" in initial:
            checks["exactly_six_initial_metadata_requests"] = initial["metadata_calls"] == 6
        checks["answer_does_not_modify_sources_or_preferences"] = (
            _snapshot(selected) == original_sources and preferences.snapshot() == prefs)
        checks["different_media_bind_different_request_identity"] = (
            results["image"]["request_sha256"] != results["audio"]["request_sha256"])

        if check_rejections:
            # Only malformed requests here: they must never reach the provider.
            from multimodal.codec import MediaError
            from services.media_assistant import MediaAssistantError
            import copy
            cases = []
            bad = copy.deepcopy(documents["image"]); bad["sha256"] = "0" * 64
            cases.append(("hash_mismatch", (bad,)))
            bad = copy.deepcopy(documents["image"]); bad["data_base64"] = "invalid*"
            cases.append(("invalid_base64", (bad,)))
            bad = copy.deepcopy(documents["image"]); bad["metadata"]["width"] += 1
            cases.append(("false_image_metadata", (bad,)))
            bad = copy.deepcopy(documents["audio"]); bad["metadata"]["sample_rate"] = 44100
            cases.append(("false_audio_metadata", (bad,)))
            cases.append(("two_media_in_one_turn", (documents["image"], documents["audio"])))
            rejected = {}
            for case, media in cases:
                before = _counters(providers)
                try:
                    assistants["image"].ask("reject-" + case, "مدخل مصطنع غير صالح", media=media)
                except (MediaError, MediaAssistantError) as exc:
                    rejected[case] = exc.code
                    checks[case + "_rejected_before_provider"] = _counters(providers) == before
                else:
                    checks[case + "_rejected_before_provider"] = False
            evidence["rejected_input_codes"] = rejected

        # Remove only our private fixture copies; never alter the selected original.
        for path in selected.iterdir():
            path.unlink()
        selected.rmdir()
        preferences.set("verbosity", "concise", expected_revision=prefs["revision"])
        state_before = _snapshot(root)
        replay_provider, replay_preferences = _ReplaySentinel(), _ReplaySentinel()
        replays, reinspections = {}, {}
        for kind in fixtures:
            model, version = models[kind]
            reopened = MediaAssistant.open(root / "sessions", kind,
                model=model, model_version=version, provider=replay_provider, preferences=replay_preferences)
            replay = reopened.replay(kind + "-turn")
            replays[kind] = replay
            reinspections[kind] = reopened.inspect(kind + "-turn")
            checks[kind + "_restart_replays_same_result"] = replay == {**results[kind], "replayed": True}
            checks[kind + "_frozen_inspection_survives_source_loss"] = reinspections[kind] == frozen[kind]
            history = reopened.history()
            checks[kind + "_session_isolation"] = len(history) == 1 and history[0] == replay
        checks["replay_never_reads_provider_or_preferences"] = not replay_provider.touches and not replay_preferences.touches
        checks["replay_zero_provider_and_network_calls"] = _counters(providers) == initial
        checks["replay_preserves_state_bytes"] = _snapshot(root) == state_before
        checks["replay_without_source_directory"] = not selected.exists()
        checks["flow_complete"] = True
        evidence.update(results=results, replays=replays, frozen=frozen, reinspections=reinspections)
    except Exception as exc:
        checks["flow_complete"] = False
        evidence["error_code"] = getattr(exc, "code", type(exc).__name__)
        evidence.update(results=results, frozen=frozen)
        if not initial:
            initial = _counters(providers)
    report = _public(checks, providers, initial, live=live)
    if "error_code" in evidence:
        report["error_code"] = evidence["error_code"]
    return report, evidence


def run_synthetic(root):
    from acceptance_m9 import CapturingProvider, SyntheticProvider, SYNTHETIC_MODEL, SYNTHETIC_VERSION, _answer
    # This inert response intentionally resembles an action; no executor exists.
    answer = '{"tool":"write_file","preferences":{"verbosity":"detailed"},"content":"fixture"}'
    providers = {kind: CapturingProvider(SyntheticProvider([_answer(answer)])) for kind in ("image", "audio")}
    report, _ = _run_pair(root, providers=providers,
        models={kind: (SYNTHETIC_MODEL, SYNTHETIC_VERSION) for kind in providers},
        audio_bytes=wav_fixture(), check_rejections=True)
    return report


def run_live(root, *, image_model, image_model_version, audio_model, audio_model_version,
             audio_file, run_id, provider_factory=None):
    from acceptance_m9 import AcceptanceError, CapturingProvider, _new_private_run, _write_private
    from multimodal.codec import read_selected
    for model, version in ((image_model, image_model_version), (audio_model, audio_model_version)):
        if not isinstance(model, str) or not model.strip():
            raise AcceptanceError("model_required")
        if not isinstance(version, str) or re.fullmatch(r"[0-9a-f]{64}", version) is None:
            raise AcceptanceError("model_version_invalid")
    source = read_selected(Path(audio_file))
    if source["kind"] != "audio":
        raise AcceptanceError("audio_fixture_required")
    if provider_factory is None:
        from providers.local_media import LocalMediaProvider
        provider_factory = LocalMediaProvider
    models = {"image": (image_model, image_model_version), "audio": (audio_model, audio_model_version)}
    run = _new_private_run(root, run_id)
    providers = {kind: CapturingProvider(provider_factory(*identity)) for kind, identity in models.items()}
    report, evidence = _run_pair(run / "case", providers=providers, models=models,
        audio_bytes=base64.b64decode(source["data_base64"], validate=True), live=True)
    report["run_id"] = run_id
    _write_private(run / "report.json", {"summary": report, "runtime_models": models,
        "evidence": evidence, "selected_audio": source, "fixture_manifest": fixture_manifest(),
        "image_prompt": IMAGE_PROMPT, "audio_prompt": AUDIO_PROMPT,
        "quality_pending": True, "release_ready": False})
    return report


def main(argv=None):
    from acceptance_m9 import AcceptanceError
    from providers.base import ProviderError
    from multimodal.codec import MediaError
    from services.media_assistant import MediaAssistantError
    import services.media_assistant as service_module
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    for name in ("image-model", "image-model-version", "audio-model", "audio-model-version", "run-id"):
        parser.add_argument("--" + name)
    parser.add_argument("--audio-file", type=Path)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)
    live_values = (args.image_model, args.image_model_version, args.audio_model,
                   args.audio_model_version, args.audio_file, args.run_id)
    if args.live and not all(live_values):
        parser.error("--live يتطلب هويتي النموذجين ونسختيهما و--audio-file و--run-id")
    if not args.live and any(value is not None for value in (*live_values, args.root)):
        parser.error("خيارات التشغيل الحي تتطلب --live")
    try:
        if args.live:
            root = args.root or Path(service_module.__file__).resolve().parents[1] / "var/media-acceptance"
            report = run_live(root, image_model=args.image_model, image_model_version=args.image_model_version,
                audio_model=args.audio_model, audio_model_version=args.audio_model_version,
                audio_file=args.audio_file, run_id=args.run_id)
        else:
            with tempfile.TemporaryDirectory(prefix="diwan-m12-") as temp:
                report = run_synthetic(Path(temp).resolve() / "case")
    except (AcceptanceError, ProviderError, MediaError, MediaAssistantError, OSError) as exc:
        print(json.dumps({"error_code": getattr(exc, "code", "filesystem_error"), "release_ready": False}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
