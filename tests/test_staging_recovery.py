import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from webui.server import LocalApp
from workspace_tools.backup import BackupError, export_workspace
from workspace_tools.recovery import (
    RecoveryError, inspect_staging, quarantine_staging, error_code,
)


@pytest.fixture
def stranded(tmp_path):
    root = tmp_path.resolve() / 'ui'
    app = LocalApp(root, model='synthetic', model_version='a' * 64, provider_factory=lambda: None)
    # Real create failure, before publication. No fabricated published project.
    with patch('webui.server._write_json', side_effect=OSError('interrupted')):
        with pytest.raises(OSError):
            app.dispatch({'action': 'create_project', 'name': 'مشروع'})
    app.close()
    stage = next((root / 'staging').iterdir())
    data = stage / 'partial.txt'
    data.write_bytes(b'private original')
    data.chmod(0o600)
    return root, stage


def checked(root):
    return inspect_staging(root)['entries'][0]


def test_staging_can_be_quarantined_without_data_loss_then_backed_up(stranded):
    root, stage = stranded
    with pytest.raises(BackupError, match='backup_staging_not_empty'):
        export_workspace(root, root.parent / 'before.json')
    original = (stage / 'partial.txt').stat()
    before = checked(root)
    dest = root.parent / 'quarantine'
    result = quarantine_staging(root, stage.name, before['sha256'], dest)
    assert result['status'] == 'quarantined'
    moved = dest / 'data/partial.txt'
    assert moved.read_bytes() == b'private original'
    assert (moved.stat().st_ino, moved.stat().st_mtime_ns) == (original.st_ino, original.st_mtime_ns)
    assert json.loads((dest / 'receipt.json').read_text())['sha256'] == before['sha256']
    assert inspect_staging(root)['entries'] == []
    assert export_workspace(root, root.parent / 'after.json')['status'] == 'exported'


def test_inspect_does_not_mutate_and_never_exposes_content(stranded):
    root, stage = stranded
    def snapshot():
        return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
    before = snapshot()
    report = inspect_staging(root)
    assert snapshot() == before
    assert 'private original' not in json.dumps(report)


def test_live_workspace_cannot_be_recovered(stranded):
    root, stage = stranded
    app = LocalApp(root, model='synthetic', model_version='a' * 64, provider_factory=lambda: None)
    try:
        with pytest.raises(BackupError, match='backup_busy'):
            inspect_staging(root)
    finally:
        app.close()


def test_changed_entry_rejects_before_destination_creation(stranded):
    root, stage = stranded
    before = checked(root)
    (stage / 'partial.txt').write_bytes(b'changed')
    dest = root.parent / 'quarantine'
    with pytest.raises(RecoveryError, match='recovery_digest_mismatch'):
        quarantine_staging(root, stage.name, before['sha256'], dest)
    assert not dest.exists() and stage.exists()


def test_existing_destination_never_overwritten(stranded):
    root, stage = stranded
    dest = root.parent / 'existing'
    dest.mkdir(mode=0o700)
    (dest / 'keep').write_text('owned')
    with pytest.raises(FileExistsError):
        quarantine_staging(root, stage.name, checked(root)['sha256'], dest)
    assert (dest / 'keep').read_text() == 'owned' and stage.exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'public_file', 'public_directory'])
def test_unsafe_entries_never_moved(stranded, kind):
    root, stage = stranded
    path = stage / 'partial.txt'
    if kind == 'symlink':
        (stage / 'link').symlink_to(path)
    elif kind == 'hardlink':
        os.link(path, stage / 'link')
    elif kind == 'public_file':
        path.chmod(0o644)
    else:
        stage.chmod(0o755)
    with pytest.raises((BackupError, OSError)):
        inspect_staging(root)
    assert stage.exists()


@pytest.mark.parametrize('name', ['../outside', '/', 'bad', 'A' * 32])
def test_entry_identifier_cannot_select_other_paths(stranded, name):
    root, _ = stranded
    with pytest.raises(RecoveryError, match='recovery_entry_invalid'):
        quarantine_staging(root, name, 'a' * 64, root.parent / 'dest')


def test_destination_inside_workspace_rejected(stranded):
    root, stage = stranded
    with pytest.raises(RecoveryError, match='recovery_destination_inside_workspace'):
        quarantine_staging(root, stage.name, checked(root)['sha256'], root / 'quarantine')
    assert stage.exists() and not (root / 'quarantine').exists()


def test_before_move_failure_keeps_source_and_durable_receipt(stranded):
    root, stage = stranded
    before = checked(root)
    dest = root.parent / 'failed'
    with patch('workspace_tools.recovery.os.rename', side_effect=OSError('interrupted')):
        with pytest.raises(OSError):
            quarantine_staging(root, stage.name, before['sha256'], dest)
    assert (dest / 'receipt.json').is_file() and stage.exists()
    assert not (dest / 'data').exists()
    assert quarantine_staging(root, stage.name, before['sha256'], root.parent / 'retry')['status'] == 'quarantined'


def test_after_move_failure_keeps_all_data_and_does_not_republish(stranded):
    root, stage = stranded
    before = checked(root)
    dest = root.parent / 'failed'
    real_fsync = os.fsync
    def fsync(fd):
        if (dest / 'data').exists():
            raise OSError('interrupted after rename')
        return real_fsync(fd)
    with patch('workspace_tools.recovery.os.fsync', side_effect=fsync):
        with pytest.raises(OSError):
            quarantine_staging(root, stage.name, before['sha256'], dest)
    assert (dest / 'data/partial.txt').read_bytes() == b'private original'
    assert (dest / 'receipt.json').is_file() and not stage.exists()
    assert not list((root / 'projects').iterdir())


def test_missing_workspace_not_initialized(tmp_path):
    root = tmp_path / 'absent'
    with pytest.raises(FileNotFoundError):
        inspect_staging(root)
    assert not root.exists()


def test_empty_workspace_inspection(tmp_path):
    root = tmp_path.resolve() / 'ui'
    app = LocalApp(root, model='synthetic', model_version='a' * 64, provider_factory=lambda: None)
    app.close()
    assert inspect_staging(root) == {'status': 'inspected', 'entries': []}
    assert not (root / 'staging').exists()


def test_error_codes_do_not_expose_exception_messages():
    assert error_code(ValueError('private content')) == 'recovery_failed'
    assert error_code(OSError('private content')) == 'recovery_io_error'
