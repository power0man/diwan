"""Independent temporary-source probes; no model/network or existing root changes."""
from pathlib import Path
import json
import subprocess
import sys

import pytest

from core.canonical import canonical_bytes, digest
from acceptance_m13 import _app, _dispatch, _snapshot, prepare_workspace, SyntheticProvider
from acceptance_m9 import SYNTHETIC_MODEL, SYNTHETIC_VERSION
from workspace_tools.backup import BackupError, export_workspace, restore_workspace
import workspace_tools.backup as backup
from webui.server import UIError


def test_media_profile_must_be_openable_by_media_assistant(tmp_path):
    root = tmp_path.resolve() / 'source'
    app = _app(root, SyntheticProvider)
    try:
        project = _dispatch(app, 'create_project', name='fixture')['id']
        session = _dispatch(app, 'create_session', project=project, name='media', mode='media')['id']
    finally:
        app.close()
    folder = root / 'projects' / project / 'sessions' / session / 'chat' / session
    config = json.loads((folder / 'manifest.json').read_bytes())
    config['max_output'] = 401
    (folder / 'manifest.json').write_bytes(canonical_bytes(config))
    envelope = json.loads((folder / 'state.json').read_bytes())
    envelope['state']['config_sha256'] = digest(config)
    envelope['sha256'] = digest(envelope['state'])
    (folder / 'state.json').write_bytes(canonical_bytes(envelope))
    before = _snapshot(root)
    with pytest.raises(BackupError):
        export_workspace(root, tmp_path / 'profile.backup.json')
    assert _snapshot(root) == before


def test_failure_before_initial_marker_does_not_leave_openable_workspace(tmp_path, monkeypatch):
    root = tmp_path.resolve() / 'source'
    prepare_workspace(root)
    archive = tmp_path / 'archive.json'
    result = export_workspace(root, archive)
    destination = tmp_path / 'restored'
    original = backup._write_file
    def fail_marker(fd, path, raw, **options):
        if path == backup.INCOMPLETE:
            raise OSError('synthetic failure before marker')
        return original(fd, path, raw, **options)
    monkeypatch.setattr(backup, '_write_file', fail_marker)
    with pytest.raises(BackupError):
        restore_workspace(archive, destination, result['sha256'])
    intent = destination.parent / backup.restore_intent_name(destination)
    assert destination.is_dir()
    assert intent.is_file()
    assert backup.restore_pending(destination)
    assert not (destination / backup.INCOMPLETE).exists()
    before = _snapshot(tmp_path)
    with pytest.raises(UIError, match='restore_incomplete'):
        _app(destination, SyntheticProvider)
    assert not (destination / 'app.lock').exists()
    assert _snapshot(tmp_path) == before


def test_parent_intent_blocks_app_before_creating_destination(tmp_path):
    destination = tmp_path.resolve() / 'not-created'
    intent = destination.parent / backup.restore_intent_name(destination)
    intent.write_bytes(b'diwan restore reserved; incomplete\n')
    intent.chmod(0o600)
    before = _snapshot(tmp_path)
    with pytest.raises(UIError, match='restore_incomplete'):
        _app(destination, SyntheticProvider)
    assert not destination.exists()
    assert _snapshot(tmp_path) == before


def test_successful_restore_clears_both_markers_and_opens(tmp_path):
    root = tmp_path.resolve() / 'source'
    fixture = prepare_workspace(root)
    archive = tmp_path / 'archive.json'
    exported = export_workspace(root, archive)
    destination = tmp_path / 'restored'
    result = restore_workspace(archive, destination, exported['sha256'])
    assert result['status'] == 'restored'
    assert not backup.restore_pending(destination)
    assert not (destination / backup.INCOMPLETE).exists()
    assert not (destination.parent / backup.restore_intent_name(destination)).exists()
    def poison_factory():
        pytest.fail('Opening restored state must not construct a provider')
    app = _app(destination, poison_factory)
    try:
        assert _dispatch(app, 'projects') == fixture['expected']['projects']
    finally:
        app.close()


def test_backup_cli_roundtrip_is_honest_and_reports_no_private_identity(tmp_path):
    root = tmp_path.resolve() / 'source'
    prepare_workspace(root)
    source_before = _snapshot(root)
    archive = tmp_path / 'archive.json'
    destination = tmp_path / 'restored'
    cli = Path(backup.__file__).resolve().parent.parent / 'tools' / 'workspace_backup.py'
    success_keys = {'status', 'sha256', 'file_count', 'directory_count', 'total_bytes'}
    def run(*args, expected_code=0):
        result = subprocess.run([sys.executable, str(cli), *map(str, args)],
                                capture_output=True, text=True, timeout=20)
        assert result.returncode == expected_code, result.stderr
        assert result.stderr == ''
        report = json.loads(result.stdout)
        assert set(report) == (success_keys if expected_code == 0 else {'status', 'error_code'})
        for private in (str(tmp_path), str(root), str(destination), SYNTHETIC_MODEL,
                        SYNTHETIC_VERSION, 'source_root', 'data_base64',
                        'ملاحظة مصطنعة خاصة.', 'جواب مصطنع محفوظ للمراجعة'):
            assert private not in result.stdout
        return report
    exported = run('backup', '--root', root, '--output', archive)
    inspected = run('inspect', '--archive', archive, '--sha256', exported['sha256'])
    restored = run('restore', '--archive', archive, '--destination', destination,
                   '--sha256', exported['sha256'])
    assert [r['status'] for r in (exported, inspected, restored)] == ['exported', 'verified', 'restored']
    for key in success_keys - {'status'}:
        assert exported[key] == inspected[key] == restored[key]
    assert exported['file_count'] > 0 and exported['directory_count'] > 0 and exported['total_bytes'] > 0
    restored_before = _snapshot(destination)
    repeated = run('restore', '--archive', archive, '--destination', destination,
                   '--sha256', exported['sha256'], expected_code=2)
    assert repeated == {'status': 'error', 'error_code': 'backup_destination_exists'}
    rejected = run('restore', '--archive', archive, '--destination', tmp_path / 'wrong-hash',
                   '--sha256', '0' * 64, expected_code=2)
    assert rejected['status'] == 'error'
    assert not (tmp_path / 'wrong-hash').exists()
    assert _snapshot(root) == source_before
    assert _snapshot(destination) == restored_before
