"""Tests use public fixture bytes and synthetic providers; never a live model."""
import hashlib
import io
import struct
import wave
import zlib

from acceptance_m12 import fixture_manifest, png_fixture, wav_fixture


def test_png_fixture_has_valid_crc_geometry_and_expected_pixels():
    raw = png_fixture()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    offset, chunks = 8, []
    while offset < len(raw):
        length = struct.unpack_from(">I", raw, offset)[0]
        kind, payload = raw[offset + 4:offset + 8], raw[offset + 8:offset + 8 + length]
        crc = struct.unpack_from(">I", raw, offset + 8 + length)[0]
        assert crc == zlib.crc32(kind + payload) & 0xffffffff
        chunks.append((kind, payload))
        offset += 12 + length
    assert offset == len(raw)
    assert [kind for kind, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    assert struct.unpack(">IIBBBBB", chunks[0][1]) == (240, 120, 8, 2, 0, 0, 0)
    pixels = zlib.decompress(chunks[1][1])
    assert len(pixels) == 120 * (1 + 240 * 3)
    assert all(pixels[y * 721] == 0 for y in range(120))
    def pixel(x, y):
        start = y * 721 + 1 + x * 3
        return tuple(pixels[start:start + 3])
    assert pixel(0, 0) == (255, 255, 255)
    assert pixel(40, 60) == (220, 30, 30)
    assert pixel(120, 60) == (20, 160, 60)
    assert pixel(195, 60) == (30, 70, 220)
    assert pixel(80, 60) == (255, 255, 255)
    assert pixel(160, 60) == (255, 255, 255)
    assert pixel(210, 40) == (255, 255, 255)  # outside triangle
    assert pixel(210, 80) == (30, 70, 220)   # inside triangle


def test_wav_fixture_is_bounded_mono_pcm_tone_not_speech():
    with wave.open(io.BytesIO(wav_fixture()), "rb") as stream:
        assert (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(),
                stream.getnframes(), stream.getcomptype()) == (1, 2, 16000, 6400, "NONE")
        frames = stream.readframes(stream.getnframes())
    values = struct.unpack("<6400h", frames)
    assert len(frames) == 12800 and values[0] == 0
    assert max(values) == 8192 and min(values) == -8192
    upward_crossings = sum(a <= 0 < b for a, b in zip(values, values[1:]))
    assert upward_crossings == 176  # 440 cycles/s * 0.4s


def test_fixture_manifest_is_deterministic_and_does_not_claim_quality():
    manifest = fixture_manifest()
    assert png_fixture() == png_fixture() and wav_fixture() == wav_fixture()
    assert manifest["image"]["sha256"] == hashlib.sha256(png_fixture()).hexdigest()
    assert manifest["audio"]["sha256"] == hashlib.sha256(wav_fixture()).hexdigest()
    assert manifest["semantic_quality"] == "not_assessed"
    assert manifest["independent_bank"] == "not_provided"
    assert "no speech" in manifest["audio"]["signal"]


def test_synthetic_roundtrip_replays_frozen_media_without_dependencies(tmp_path):
    from acceptance_m12 import run_synthetic
    report = run_synthetic(tmp_path.resolve() / 'pair')
    assert all(report['checks'].values()), report
    assert report['initial_provider_calls'] == 2 and report['replay_provider_calls'] == 0
    assert report['replay_estimate_calls'] == 0
    assert report['checks']['replay_never_reads_provider_or_preferences']
    assert report['checks']['replay_without_source_directory']
    assert report['quality_pending'] is True and report['release_ready'] is False
    assert report['human_review'] == 'not_required_q49'
    assert report['automated_review'] == 'pending'
    assert report['image_understanding'] == report['arabic_asr_quality'] == 'not_assessed'


def test_live_entrypoint_fixture_counts_and_private_evidence_without_real_network(tmp_path):
    import json
    import stat
    from acceptance_m12 import run_live
    from core.contracts import Response, Usage
    made = []
    class FixtureProvider:
        is_local = True
        name = 'synthetic-media-provider'
        def __init__(self, model, version):
            self.model, self.version = model, version
            self.chat_calls = self.metadata_calls = 0
            made.append(self)
        def estimate_micros(self, request):
            return 0
        def complete(self, request):
            self.chat_calls += 1
            self.metadata_calls += 3
            return Response('private fixture answer', Usage(20, 4), 'complete', 0,
                            provider=self.name, model_version=self.version)
    source = tmp_path.resolve() / 'selected.wav'
    source.write_bytes(wav_fixture())
    before = source.read_bytes(), source.stat().st_mtime_ns
    report = run_live(tmp_path.resolve() / 'private-runs',
        image_model='private-fixture-image-name', image_model_version='b' * 64,
        audio_model='private-fixture-audio-name', audio_model_version='c' * 64,
        audio_file=source, run_id='fixture-run', provider_factory=FixtureProvider)
    assert all(report['checks'].values()), report
    assert len(made) == 2 and [p.chat_calls for p in made] == [1, 1]
    assert report['initial_chat_calls'] == 2 and report['initial_metadata_calls'] == 6
    assert report['replay_chat_calls'] == report['replay_metadata_calls'] == 0
    assert before == (source.read_bytes(), source.stat().st_mtime_ns)
    public = json.dumps(report)
    assert 'private-fixture-image-name' not in public and 'private-fixture-audio-name' not in public
    assert 'private fixture answer' not in public and str(source) not in public
    saved = tmp_path / 'private-runs/fixture-run/report.json'
    evidence = json.loads(saved.read_text())
    assert evidence['summary']['human_review'] == report['human_review'] == 'not_required_q49'
    assert evidence['summary']['automated_review'] == report['automated_review'] == 'pending'
    assert evidence['evidence']['results']['image']['content'] == 'private fixture answer'
    assert evidence['runtime_models']['audio'] == ['private-fixture-audio-name', 'c' * 64]
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved.parent.stat().st_mode) == 0o700
    assert not (saved.parent / 'case/selected').exists()


def test_truncation_is_not_counted_as_complete_acceptance(tmp_path, monkeypatch):
    import acceptance_m9
    from acceptance_m12 import run_synthetic
    original = acceptance_m9._answer
    monkeypatch.setattr(acceptance_m9, '_answer', lambda text: original(text, 'max_output'))
    report = run_synthetic(tmp_path.resolve() / 'truncated')
    assert report['checks']['image_one_fresh_unverified_answer'] is False
    assert report['checks']['audio_one_fresh_unverified_answer'] is False
    assert report['checks']['image_restart_replays_same_result'] is True
    assert report['checks']['audio_restart_replays_same_result'] is True
    assert report['passed'] < report['total'] and report['release_ready'] is False


def test_live_rejects_non_audio_fixture_before_provider_creation(tmp_path):
    import pytest
    from acceptance_m12 import run_live
    from acceptance_m9 import AcceptanceError
    selected = tmp_path.resolve() / 'selected.png'
    selected.write_bytes(png_fixture())
    def no_provider(*args):
        raise AssertionError('must not construct a provider')
    with pytest.raises(AcceptanceError, match='audio_fixture_required'):
        run_live(tmp_path.resolve() / 'runs', image_model='fixture', image_model_version='a' * 64,
                 audio_model='fixture', audio_model_version='a' * 64, audio_file=selected,
                 run_id='invalid-audio', provider_factory=no_provider)
    assert not (tmp_path / 'runs').exists()


def test_service_to_actual_media_provider_preserves_selected_bytes_with_fake_http(tmp_path, monkeypatch):
    """Bridge service/codec/provider with real fixtures; HTTP itself stays in memory."""
    import base64
    import json
    from acceptance_m12 import run_live
    import providers.local_chat as transport
    identities = {'fixture-image:1': 'b' * 64, 'fixture-audio:1': 'c' * 64}
    calls = []
    class Socket:
        def shutdown(self, *args):
            pass
    class Reply:
        status = 200
        def __init__(self, data):
            self.body = json.dumps(data).encode()
        def getheader(self, key, default=None):
            return {'Content-Type': 'application/json', 'Content-Length': str(len(self.body))}.get(key, default)
        def read(self, count):
            return self.body[:count]
        def close(self):
            pass
    class Connection:
        def __init__(self, host, port, *, timeout):
            assert (host, port) == ('127.0.0.1', 11434)
            self.sock = Socket()
        def connect(self):
            pass
        def request(self, method, path, *, body, headers):
            self.path, self.payload = path, None if body is None else json.loads(body)
            calls.append((method, path, self.payload))
        def getresponse(self):
            if self.path == '/api/version':
                return Reply({'version': '0.34.2'})
            if self.path == '/api/tags':
                return Reply({'models': [{'name': name, 'model': name, 'digest': version,
                    'size': 1000, 'details': {'format': 'gguf'}} for name, version in identities.items()]})
            if self.path == '/api/show':
                assert self.payload['model'] in identities
                return Reply({'details': {'format': 'gguf'}, 'capabilities': ['completion', 'vision', 'audio'],
                    'model_info': {'general.architecture': 'fixture', 'fixture.context_length': 8192}})
            assert self.path == '/api/chat'
            return Reply({'model': self.payload['model'], 'done': True, 'done_reason': 'stop',
                'message': {'role': 'assistant', 'content': 'جواب اختبار مصطنع'},
                'prompt_eval_count': 32, 'eval_count': 4})
        def close(self):
            pass
    monkeypatch.setattr(transport.http.client, 'HTTPConnection', Connection)
    source = tmp_path.resolve() / 'selected.wav'
    source.write_bytes(wav_fixture())
    report = run_live(tmp_path.resolve() / 'runs', image_model='fixture-image:1', image_model_version='b' * 64,
        audio_model='fixture-audio:1', audio_model_version='c' * 64, audio_file=source, run_id='fake-http')
    assert all(report['checks'].values()), report
    assert [path for _, path, _ in calls] == ['/api/version', '/api/tags', '/api/show', '/api/chat'] * 2
    for (_, _, payload), expected, kind in zip([c for c in calls if c[1] == '/api/chat'],
                                               [png_fixture(), wav_fixture()], ['image', 'audio']):
        assert payload['truncate'] is False and payload['shift'] is False and payload['stream'] is False
        assert payload['options']['num_ctx'] == 8192 and payload['options']['num_predict'] == 400
        assert len(payload['messages']) == 2
        wire = payload['messages'][-1]
        assert [base64.b64decode(value, validate=True) for value in wire['images']] == [expected]
        metadata = json.loads(wire['content'])
        assert metadata['media'][0]['kind'] == kind
        assert 'data_base64' not in metadata['media'][0]
        assert metadata['media'][0]['sha256'] == hashlib.sha256(expected).hexdigest()
    assert report['replay_chat_calls'] == report['replay_metadata_calls'] == 0
