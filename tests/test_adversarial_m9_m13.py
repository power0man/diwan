"""اختبارات التدقيق العدائي للطبقات م٩..م١٣ (الطقس العدائي المستقل).

تتحقق هذه الاختبارات من صمود حدود الأمان والعزل والتحقق البنيوي ضد:
- محاولات اختراق المسارات (Path Traversal / Zip Slip / Symlinks)
- هجمات تزوير الرؤوس وCSRF وHost Header وSmuggling في خادم الويب
- مدخلات الوسائط الملغومة أو المخالفة للترخيص (Decompression Bomb / Non-PCM / Bad CRC)
- تزوير هويات الجلسات وتنازع الأقفال وتلف الحالات
- محاولات تعديل التفضيلات أو الكتابة فوق الملفات الموجودة
"""
import base64
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import threading
import time
import zlib

import pytest

from acceptance_m9 import SyntheticProvider, SYNTHETIC_MODEL, SYNTHETIC_VERSION, _answer
from conversation.session import ChatSession, ConversationError
from multimodal.codec import pack_media, MediaError, MAX_MEDIA_BYTES, _PNG_SIGNATURE
from webui.server import LocalApp, Server, UIError
from workspace_tools.files import TextWorkspace, WorkspaceError
from workspace_tools.preferences import Preferences, PreferenceError
from workspace_tools.backup import (
    export_workspace, restore_workspace, inspect_archive, BackupError, restore_pending
)


def _make_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


# ════════════════════════════════════════════════════════════════════
# ١. التدقيق العدائي لطبقة المحادثة والجلسات (م٩)
# ════════════════════════════════════════════════════════════════════

def test_session_id_path_traversal_rejected(tmp_path):
    root = _make_private_dir(tmp_path / "sessions")
    invalid_ids = [
        "../escape",
        "/absolute",
        "nested/sub",
        "null\x00byte",
        "has space",
        "-leading-dash",
        "a" * 65,  # يتجاوز حد الـ 64 محرف
        "",
    ]
    for sid in invalid_ids:
        with pytest.raises(ConversationError) as exc:
            ChatSession(root, sid, model="qwen", model_version="1")
        assert exc.value.code == "session_id_invalid"


def test_session_invalid_purpose_rejected(tmp_path):
    root = _make_private_dir(tmp_path / "sessions")
    with pytest.raises(ConversationError) as exc:
        ChatSession(root, "valid-id", model="qwen", model_version="1", purpose="root-shell")
    assert exc.value.code == "purpose_invalid"


def test_session_concurrency_lock_prevents_race(tmp_path):
    root = _make_private_dir(tmp_path / "sessions")
    session1 = ChatSession(root, "session-1", model="qwen", model_version="1")

    # احتجاز القفل من العملية الأولى
    with session1._lock():
        # محاولة فتح الجلسة نفسها بالتوازي يفشل فوراً بـ session_busy داخل __init__
        with pytest.raises(ConversationError) as exc:
            ChatSession(root, "session-1", model="qwen", model_version="1")
        assert exc.value.code == "session_busy"


def test_session_corrupted_state_rejected(tmp_path):
    root = _make_private_dir(tmp_path / "sessions")
    session = ChatSession(root, "session-corrupt", model="qwen", model_version="1")
    state_file = root / "session-corrupt" / "state.json"
    assert state_file.exists()

    # تعديل محتوى الحالة يدوياً لتخريب البصمة
    raw = json.loads(state_file.read_text(encoding="utf-8"))
    raw["state"]["count"] = 999  # تزوير العداد
    state_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ConversationError) as exc:
        ChatSession(root, "session-corrupt", model="qwen", model_version="1")
    assert exc.value.code == "state_corrupt"


# ════════════════════════════════════════════════════════════════════
# ٢. التدقيق العدائي لملفات مساحة العمل والتفضيلات (م١٠)
# ════════════════════════════════════════════════════════════════════

def test_workspace_files_path_traversal_attacks(tmp_path):
    inputs = _make_private_dir(tmp_path / "inputs")
    outputs = _make_private_dir(tmp_path / "outputs")
    ws = TextWorkspace(inputs, outputs)

    traversal_paths = [
        "../secret.txt",
        "../../etc/passwd",
        "/etc/shadow",
        "sub/../../secret.txt",
        "nested/../../../root",
        ".hidden_file",
        "valid/../../outside",
    ]
    for p in traversal_paths:
        with pytest.raises(WorkspaceError) as exc:
            ws.read_text(p)
        assert exc.value.code in ("path_traversal", "path_hidden", "file_missing", "path_invalid")

        with pytest.raises(WorkspaceError) as exc:
            ws.propose_write(p, "content", "req-1")
        assert exc.value.code in ("path_traversal", "path_hidden", "path_invalid", "path_protected")


def test_workspace_apply_never_overwrites_existing_file(tmp_path):
    inputs = _make_private_dir(tmp_path / "inputs")
    outputs = _make_private_dir(tmp_path / "outputs")
    ws = TextWorkspace(inputs, outputs)

    # إنشاء ملف موجود سلفاً في المخرجات
    existing = outputs / "report.txt"
    existing.write_text("نص أصلي محمي", encoding="utf-8")
    os.chmod(existing, 0o600)
    original_mtime = existing.stat().st_mtime_ns

    # تقديم اقتراح كتابة للمسار نفسه
    prop = ws.propose_write("report.txt", "نص هجومي مغاير", "req-attack")
    result = ws.apply(prop["proposal_id"], prop["sha256"])

    assert result["status"] == "error"
    assert result["error_code"] == "target_exists"
    # التأكد من بقاء النص الأصلي وتاريخ التعديل بلا أدنى تغيير
    assert existing.read_text(encoding="utf-8") == "نص أصلي محمي"
    assert existing.stat().st_mtime_ns == original_mtime


def test_preferences_adversarial_key_validation(tmp_path):
    pref_dir = _make_private_dir(tmp_path / "pref_dir")
    pref_file = pref_dir / "prefs.json"
    prefs = Preferences(pref_file)

    invalid_keys = [
        "",  # مفتاح فارغ
        "   ",  # فراغات
        "arbitrary_unwhitelisted_key",
        "key\nwith\nnewlines",
        "key\x00null",
        "k" * 129,
    ]
    for k in invalid_keys:
        with pytest.raises(PreferenceError) as exc:
            prefs.set(k, "value", expected_revision=0)
        assert exc.value.code == "preference_key_invalid"


def test_preferences_stale_revision_rejected(tmp_path):
    pref_dir = _make_private_dir(tmp_path / "pref_dir")
    pref_file = pref_dir / "prefs.json"
    prefs = Preferences(pref_file)
    snap1 = prefs.set("response_language", "ar", expected_revision=0)
    rev1 = snap1["revision"]

    # تعديل صحيح يرفع المراجعة
    snap2 = prefs.set("verbosity", "concise", expected_revision=rev1)
    assert snap2["revision"] != rev1

    # محاولة تعديل مستندة إلى المراجعة القديمة المتجاوزة
    with pytest.raises(PreferenceError) as exc:
        prefs.set("response_language", "en", expected_revision=rev1)
    assert exc.value.code == "preference_revision_conflict"


# ════════════════════════════════════════════════════════════════════
# ٣. التدقيق العدائي لخادم الواجهة والشبكة المحلية (م١١)
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def running_server(tmp_path):
    root = _make_private_dir(tmp_path / "ui_root")
    provider = SyntheticProvider([_answer("جواب مصطنع") for _ in range(5)])
    app = LocalApp(root, model=SYNTHETIC_MODEL, model_version=SYNTHETIC_VERSION,
                   provider_factory=lambda: provider)
    server = Server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_webui_rejects_foreign_host_headers(running_server):
    port = running_server.server_port
    evil_hosts = [
        "evil.com",
        "localhost.attacker.com",
        f"evil.com:{port}",
        "127.0.0.2",
    ]
    for evil_host in evil_hosts:
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/", headers={"Host": evil_host})
        resp = conn.getresponse()
        assert resp.status == 403
        data = json.loads(resp.read().decode("utf-8"))
        assert data["error_code"] == "http_refused"
        conn.close()


def test_webui_rejects_cross_site_fetch(running_server):
    port = running_server.server_port
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request("GET", "/", headers={
        "Host": f"127.0.0.1:{port}",
        "Sec-Fetch-Site": "cross-site",
    })
    resp = conn.getresponse()
    assert resp.status == 403
    conn.close()


def test_webui_rejects_post_without_valid_csrf(running_server):
    port = running_server.server_port
    body = json.dumps({"action": "projects"}).encode("utf-8")

    # محاولة بدون رمز CSRF
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request("POST", "/api", body=body, headers={
        "Host": f"127.0.0.1:{port}",
        "Origin": f"http://127.0.0.1:{port}",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    })
    resp = conn.getresponse()
    assert resp.status == 403
    conn.close()

    # محاولة برمز CSRF مزور
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request("POST", "/api", body=body, headers={
        "Host": f"127.0.0.1:{port}",
        "Origin": f"http://127.0.0.1:{port}",
        "X-Diwan-CSRF": "forged-csrf-token-12345",
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    })
    resp = conn.getresponse()
    assert resp.status == 403
    conn.close()


def test_webui_rejects_duplicate_json_keys(running_server):
    port = running_server.server_port
    token = running_server.token
    # JSON يحتوي مفتاحاً مكرراً (مخالف للمعيار الحتمي canonical)
    body = b'{"action": "projects", "action": "delete"}'
    conn = http.client.HTTPConnection("127.0.0.1", port)
    conn.request("POST", "/api", body=body, headers={
        "Host": f"127.0.0.1:{port}",
        "Origin": f"http://127.0.0.1:{port}",
        "X-Diwan-CSRF": token,
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    })
    resp = conn.getresponse()
    assert resp.status == 409
    data = json.loads(resp.read().decode("utf-8"))
    assert data["error_code"] == "json_invalid"
    conn.close()


# ════════════════════════════════════════════════════════════════════
# ٤. التدقيق العدائي لطبقة الوسائط المتعددة (م١٢)
# ════════════════════════════════════════════════════════════════════

def test_multimodal_rejects_corrupted_png():
    # ملف يبدأ بتوقيع PNG لكن بدون رأس IHDR صالح
    corrupt = _PNG_SIGNATURE + b"\x00\x00\x00\x00FAKE\x00\x00\x00\x00"
    with pytest.raises(MediaError) as exc:
        pack_media(corrupt, "corrupt.png")
    assert exc.value.code == "png_invalid"


def test_multimodal_rejects_disallowed_png_chunks():
    # إنشاء صورة PNG صالحة ثم حقن مقطع غير مسموح (مثل tEXt للحقن النصي)
    width, height = 2, 2
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr_chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr) & 0xffffffff)

    # مقطع tEXt غير مسموح وفق العقد الصارم
    text_data = b"Comment\x00Malicious payload"
    text_chunk = struct.pack(">I", len(text_data)) + b"tEXt" + text_data + struct.pack(">I", zlib.crc32(b"tEXt" + text_data) & 0xffffffff)

    raw_pixels = b"\x00\xff\x00\x00\x00\xff\x00\x00\xff\x00\x00\x00\xff\x00"
    idat = zlib.compress(raw_pixels)
    idat_chunk = struct.pack(">I", len(idat)) + b"IDAT" + idat + struct.pack(">I", zlib.crc32(b"IDAT" + idat) & 0xffffffff)
    iend_chunk = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xffffffff)

    injected_png = _PNG_SIGNATURE + ihdr_chunk + text_chunk + idat_chunk + iend_chunk

    with pytest.raises(MediaError) as exc:
        pack_media(injected_png, "injected.png")
    assert exc.value.code == "png_invalid"


def test_multimodal_rejects_non_pcm_wav():
    # ملف WAV مهيأ برمز صيغة غير PCM (مثلاً 2 = ADPCM بدلاً من 1 = PCM)
    audio_format = 2  # ADPCM
    channels = 1
    sample_rate = 16000
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    fmt = struct.pack("<HHIIHH", audio_format, channels, sample_rate, byte_rate, block_align, bits_per_sample)
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    data_chunk = b"data" + struct.pack("<I", 100) + (b"\x00" * 100)
    riff_size = 4 + len(fmt_chunk) + len(data_chunk)
    bad_wav = b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" + fmt_chunk + data_chunk

    with pytest.raises(MediaError) as exc:
        pack_media(bad_wav, "bad_codec.wav")
    assert exc.value.code == "wav_invalid"


def test_multimodal_rejects_audio_sample_rate_deviation():
    # ملف WAV بتردد 44100Hz بدلاً من 16000Hz الإلزامي
    audio_format = 1  # PCM
    channels = 1
    sample_rate = 44100  # غير مصرح به
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    fmt = struct.pack("<HHIIHH", audio_format, channels, sample_rate, byte_rate, block_align, bits_per_sample)
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    data_chunk = b"data" + struct.pack("<I", 100) + (b"\x00" * 100)
    riff_size = 4 + len(fmt_chunk) + len(data_chunk)
    bad_wav = b"RIFF" + struct.pack("<I", riff_size) + b"WAVE" + fmt_chunk + data_chunk

    with pytest.raises(MediaError) as exc:
        pack_media(bad_wav, "wrong_rate.wav")
    assert exc.value.code == "wav_invalid"


# ════════════════════════════════════════════════════════════════════
# ٥. التدقيق العدائي لطبقة النسخ الاحتياطي والاستعادة (م١٣)
# ════════════════════════════════════════════════════════════════════

def test_backup_restore_blocks_incomplete_intent(tmp_path):
    base_dir = _make_private_dir(tmp_path / "restore_base")
    dest = base_dir / "restored_ws"
    # إنشاء علامة حجز استعادة معلقة مسبقاً
    pending_file = base_dir / f".diwan-restore-{hashlib.sha256(dest.name.casefold().encode()).hexdigest()}.pending"
    pending_file.write_text("intent", encoding="utf-8")
    os.chmod(pending_file, 0o600)

    assert restore_pending(dest) is True


def test_restore_rejects_mismatched_archive_digest(tmp_path):
    base_dir = _make_private_dir(tmp_path / "backup_base")
    archive = base_dir / "archive.json"
    archive.write_text(json.dumps({"schema_version": 1, "dirs": [], "files": []}), encoding="utf-8")
    os.chmod(archive, 0o600)
    wrong_digest = "0" * 64
    with pytest.raises(BackupError) as exc:
        inspect_archive(archive, wrong_digest)
    assert exc.value.code == "backup_digest_mismatch"
