"""Media policy is enforced before any provider, against fresh locked context."""
import io
import threading
import wave

import pytest

from acceptance_m12 import png_fixture
from core.contracts import Response, Usage
from multimodal.codec import MediaError, pack_media
from services.media_assistant import MediaAssistant


class Provider:
    is_local = True
    def __init__(self):
        self.estimates = self.calls = 0
        self.stop = "complete"
    def estimate_micros(self, request):
        self.estimates += 1
        return 0
    def complete(self, request):
        self.calls += 1
        return Response("ok", Usage(1, 1), self.stop, 0)


def opened(tmp_path):
    provider = Provider()
    assistant = MediaAssistant.open(tmp_path.resolve() / "media", "fixture",
                    model="synthetic", model_version="v1", provider=provider)
    return assistant, provider


def snapshot(assistant):
    return {path.name: path.read_bytes() for path in assistant.session.directory.iterdir()
            if path.is_file()}


def wav(frames):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * frames)
    return pack_media(stream.getvalue(), "fixture.wav")


def test_fifth_media_refused_before_provider_or_pending(tmp_path):
    assistant, provider = opened(tmp_path)
    media = (pack_media(png_fixture(), "fixture.png"),)
    for i in range(4):
        assistant.ask(f"t{i}", "describe", media=media)
    before = snapshot(assistant)
    with pytest.raises(MediaError, match="media_context_limit"):
        assistant.ask("fifth", "describe", media=media)
    assert provider.calls == provider.estimates == 4
    assert snapshot(assistant) == before
    # A rejected new request never disables historical replay.
    assert assistant.ask("t0", "describe", media=media)["replayed"]


def test_total_media_bytes_enforced_without_local_transport(tmp_path):
    assistant, provider = opened(tmp_path)
    media = (wav(90000),)
    for i in range(2):
        assistant.ask(f"t{i}", "describe", media=media)
    before = snapshot(assistant)
    with pytest.raises(MediaError, match="media_context_limit"):
        assistant.ask("third", "describe", media=media)
    assert provider.calls == provider.estimates == 2
    assert snapshot(assistant) == before


def test_text_metadata_limit_is_also_transport_independent(tmp_path):
    assistant, provider = opened(tmp_path)
    for i in range(5):
        assistant.ask(f"t{i}", "x" * 4000)
    before = snapshot(assistant)
    with pytest.raises(MediaError, match="media_text_limit"):
        assistant.ask("sixth", "x" * 4000)
    assert provider.calls == provider.estimates == 5
    assert snapshot(assistant) == before


def test_failed_media_not_counted_in_successful_context(tmp_path):
    assistant, provider = opened(tmp_path)
    media = (wav(120000),)
    provider.stop = "max_output"
    for i in range(5):
        assert assistant.ask(f"t{i}", "describe", media=media)["status"] == "truncated"
    provider.stop = "complete"
    assert assistant.ask("complete", "describe", media=media)["status"] == "complete"
    assert provider.calls == 6


def test_concurrent_admission_uses_fresh_context_under_lock(tmp_path):
    assistant, provider = opened(tmp_path)
    media = (pack_media(png_fixture(), "fixture.png"),)
    for i in range(3):
        assistant.ask(f"seed{i}", "describe", media=media)
    barrier = threading.Barrier(2)
    first_read = threading.Event()
    first_done = threading.Event()
    original_history, original_turn = assistant.session.history, assistant.session.turn
    def history():
        result = original_history()
        first_read.set()
        barrier.wait(5)
        return result
    def turn(turn_id, *args, **kwargs):
        if turn_id == "late":
            assert first_done.wait(5)
        try:
            return original_turn(turn_id, *args, **kwargs)
        finally:
            if turn_id == "early":
                first_done.set()
    assistant.session.history, assistant.session.turn = history, turn
    results = {}
    def run(turn_id):
        try:
            results[turn_id] = assistant.ask(turn_id, "describe", media=media)
        except Exception as exc:
            results[turn_id] = getattr(exc, "code", type(exc).__name__)
    threads = [threading.Thread(target=run, args=(key,)) for key in ("early", "late")]
    threads[0].start()
    assert first_read.wait(5)
    threads[1].start()
    for thread in threads:
        thread.join(8)
        assert not thread.is_alive()
    assistant.session.history, assistant.session.turn = original_history, original_turn
    assert results["early"]["status"] == "complete"
    assert results["late"] == "media_context_limit"
    assert provider.calls == provider.estimates == len(assistant.history()) == 4
