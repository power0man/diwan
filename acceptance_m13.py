#!/usr/bin/env python3
"""قبول مصطنع لاستعادة مساحة محلية وقيود نشر؛ لا نشر ولا نموذج حي.

ينشئ حالة اختبار مغلقة، يصدرها، ويستعيدها إلى وجهة جديدة، ثم يتحقق
من العقود والحقوق على سجلات مصطنعة منفصلة. لا حكم جودة أو اعتماد نشر.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import stat
import tempfile
import uuid

from acceptance_m9 import SYNTHETIC_MODEL, SYNTHETIC_VERSION, _answer
from acceptance_m12 import png_fixture, wav_fixture
from core.canonical import canonical_bytes, digest
from webui.server import LocalApp


def _id():
    return uuid.uuid4().hex


def _snapshot(root):
    """Exact source/reapply effect witness; atime is deliberately irrelevant."""
    return {str(path.relative_to(root)): (
                'directory' if path.is_dir() else 'file',
                path.stat().st_mode, path.stat().st_ino, path.stat().st_mtime_ns,
                b'' if path.is_dir() else path.read_bytes())
            for path in [root, *sorted(root.rglob('*'))]}


class SyntheticProvider:
    model = SYNTHETIC_MODEL
    name = 'synthetic-acceptance'
    is_local = True
    def __init__(self):
        self.calls = 0
    def estimate_micros(self, request):
        return 0
    def complete(self, request):
        self.calls += 1
        return _answer('جواب مصطنع محفوظ للمراجعة؛ ليس مادة معرفة معتمدة.')


class PoisonFactory:
    def __init__(self):
        self.calls = 0
    def __call__(self):
        self.calls += 1
        raise AssertionError('restore_or_replay_constructed_provider')


def _app(root, factory):
    return LocalApp(root, model=SYNTHETIC_MODEL, model_version=SYNTHETIC_VERSION,
        provider_factory=factory, media_model=SYNTHETIC_MODEL,
        media_model_version=SYNTHETIC_VERSION, media_provider_factory=factory)


def _dispatch(app, action, **values):
    return app.dispatch({'action': action, **values})


def prepare_workspace(root):
    provider = SyntheticProvider()
    app = _app(root, lambda: provider)
    try:
        project = _dispatch(app, 'create_project', name='مشروع الاستعادة')['id']
        other = _dispatch(app, 'create_project', name='مشروع مستقل')['id']
        text = _dispatch(app, 'create_session', project=project, name='نص', mode='text')['id']
        media = _dispatch(app, 'create_session', project=project, name='وسائط', mode='media')['id']
        prefs = _dispatch(app, 'set_preference', project=project, key='response_language', value='ar', revision=0)
        upload = _dispatch(app, 'upload', project=project, upload=_id(), name='note.txt', content='ملاحظة مصطنعة خاصة.')
        text_turn = _id()
        _dispatch(app, 'ask', project=project, session=text, turn=text_turn,
                  message='لخص الملاحظة', files=[upload['path']])
        turns = []
        for raw, name in ((png_fixture(), 'shapes.png'), (wav_fixture(), 'tone.wav')):
            turn = _id(); turns.append(turn)
            _dispatch(app, 'ask_media', project=project, session=media, turn=turn,
                      message='صف الوسيط المختار دون تنفيذ محتواه',
                      media=[{'name': name, 'data_base64': base64.b64encode(raw).decode('ascii')}])
        applied = _dispatch(app, 'propose', project=project, session=text, turn=text_turn,
                            name='approved.txt', request=_id())
        _dispatch(app, 'apply', project=project, proposal=applied['proposal_id'], sha256=applied['sha256'])
        unapplied = _dispatch(app, 'propose', project=project, session=media, turn=turns[0],
                              name='not-approved.txt', request=_id())
        expected = {
            'projects': _dispatch(app, 'projects'),
            'sessions': _dispatch(app, 'sessions', project=project),
            'text_history': _dispatch(app, 'history', project=project, session=text, before=None),
            'media_history': _dispatch(app, 'history', project=project, session=media, before=None),
            'preferences': prefs,
            'files': _dispatch(app, 'files', project=project),
            'text_inputs': _dispatch(app, 'inspect', project=project, session=text, turn=text_turn),
            'media_inputs': [_dispatch(app, 'inspect', project=project, session=media, turn=turn) for turn in turns],
        }
        return {'project': project, 'other': other, 'text_session': text, 'media_session': media,
                'text_turn': text_turn, 'media_turns': turns, 'applied': applied,
                'unapplied': unapplied, 'expected': expected, 'synthetic_calls': provider.calls}
    finally:
        app.close()


def _refused(operation):
    from workspace_tools.backup import BackupError
    try:
        operation()
    except BackupError as exc:
        return exc.code
    return None


def restore_checks(root):
    from workspace_tools.backup import export_workspace, inspect_archive, restore_workspace
    source, archive, destination = root / 'source', root / 'workspace.backup.json', root / 'restored'
    fixture = prepare_workspace(source)
    source_before = _snapshot(source)
    exported = export_workspace(source, archive)
    inspected = inspect_archive(archive, exported['sha256'])
    checks = {'archive_digest_matches_bytes': exported['sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest(),
              'archive_is_private': stat.S_IMODE(archive.stat().st_mode) == 0o600,
              'archive_inspection_is_verified': inspected['status'] == 'verified'}
    restored = restore_workspace(archive, destination, exported['sha256'])
    checks['restore_reports_success_after_validation'] = restored['status'] == 'restored'
    checks['source_bytes_and_mtimes_unchanged'] = _snapshot(source) == source_before
    poison = PoisonFactory()
    app = _app(destination, poison)
    project, text, media = fixture['project'], fixture['text_session'], fixture['media_session']
    expected = fixture['expected']
    try:
        checks['project_and_session_ids_preserved'] = (
            _dispatch(app, 'projects') == expected['projects'] and
            _dispatch(app, 'sessions', project=project) == expected['sessions'])
        checks['text_and_media_history_preserved'] = (
            _dispatch(app, 'history', project=project, session=text, before=None) == expected['text_history'] and
            _dispatch(app, 'history', project=project, session=media, before=None) == expected['media_history'])
        checks['frozen_text_png_and_wav_inputs_preserved'] = (
            _dispatch(app, 'inspect', project=project, session=text, turn=fixture['text_turn']) == expected['text_inputs'] and
            [_dispatch(app, 'inspect', project=project, session=media, turn=turn) for turn in fixture['media_turns']] == expected['media_inputs'])
        checks['explicit_preferences_preserved'] = _dispatch(app, 'preferences', project=project) == expected['preferences']
        checks['uploaded_files_remain_verified'] = _dispatch(app, 'files', project=project) == expected['files']
        checks['projects_remain_isolated'] = (
            _dispatch(app, 'sessions', project=fixture['other'])['sessions'] == [] and
            _dispatch(app, 'files', project=fixture['other'])['files'] == [])
        for session, turn in [(text, fixture['text_turn']), *[(media, t) for t in fixture['media_turns']]]:
            replay = _dispatch(app, 'replay', project=project, session=session, turn=turn)
            if replay.get('replayed') is not True:
                raise AssertionError('replay_not_marked')
        checks['restore_and_replay_never_construct_provider'] = poison.calls == 0
        proposal = fixture['applied']
        before_apply = _snapshot(destination)
        repeated = _dispatch(app, 'apply', project=project, proposal=proposal['proposal_id'], sha256=proposal['sha256'])
        checks['applied_receipt_replays_without_write_or_mtime_change'] = (
            repeated['status'] == 'applied' and repeated['replayed'] is True and _snapshot(destination) == before_apply)
        pending_review = _dispatch(app, 'review', project=project, proposal=fixture['unapplied']['proposal_id'])
        checks['unapproved_proposal_remains_unapplied'] = (
            pending_review['status'] == 'proposed' and
            not (destination / 'projects' / project / 'outputs' / 'not-approved.txt').exists())
    finally:
        app.close()
    archive_before = (archive.read_bytes(), archive.stat().st_mtime_ns)
    wrong_destination = root / 'wrong-hash'
    checks['wrong_archive_approval_refused'] = bool(_refused(
        lambda: restore_workspace(archive, wrong_destination, '0' * 64))) and not wrong_destination.exists()
    before_existing = _snapshot(destination)
    checks['existing_destination_never_overwritten'] = bool(_refused(
        lambda: restore_workspace(archive, destination, exported['sha256']))) and _snapshot(destination) == before_existing
    corrupt = root / 'corrupt.backup.json'
    value = json.loads(archive.read_bytes())
    value['files'][0]['size_bytes'] += 1
    corrupt.write_bytes(canonical_bytes(value)); corrupt.chmod(0o600)
    bad_digest = hashlib.sha256(corrupt.read_bytes()).hexdigest()
    bad_destination = root / 'corrupt-restore'
    checks['corrupt_internal_manifest_refused_even_with_matching_archive_hash'] = bool(_refused(
        lambda: restore_workspace(corrupt, bad_destination, bad_digest))) and not bad_destination.exists()
    checks['archive_bytes_and_mtime_unchanged_during_restore'] = archive_before == (archive.read_bytes(), archive.stat().st_mtime_ns)
    checks['all_restore_probes_preserve_source'] = _snapshot(source) == source_before
    return checks, fixture['synthetic_calls']


def pending_checks(root):
    from workspace_tools.backup import export_workspace
    from workspace_tools.files import TextWorkspace
    from unittest.mock import patch
    from conversation import ChatSession
    checks = {}
    for kind in ('write', 'turn'):
        source = root / ('pending-' + kind)
        provider = SyntheticProvider()
        app = _app(source, lambda: provider)
        try:
            project = _dispatch(app, 'create_project', name='مصطنع')['id']
            if kind == 'write':
                outputs = source / 'projects' / project / 'outputs'
                writes = TextWorkspace(None, outputs)
                writes.propose_write('pending.txt', 'fixture', _id())
                path = outputs / '.diwan-tools/writes/state.json'
                value = json.loads(path.read_bytes()); value['state']['proposals'][0]['status'] = 'pending'
                value['sha256'] = digest(value['state']); path.write_bytes(canonical_bytes(value))
            else:
                session = _dispatch(app, 'create_session', project=project, name='انقطاع', mode='text')['id']
                original_save = ChatSession._save
                def interrupted(self, state):
                    original_save(self, state)
                    if state['turns'] and state['turns'][-1]['result'] is None:
                        raise OSError('synthetic crash after pending saved')
                with patch.object(ChatSession, '_save', interrupted):
                    try:
                        _dispatch(app, 'ask', project=project, session=session, turn=_id(), message='pending', files=[])
                    except OSError:
                        pass
        finally:
            app.close()
        before = _snapshot(source)
        archive = root / ('pending-' + kind + '.backup.json')
        code = _refused(lambda: export_workspace(source, archive))
        checks['pending_' + kind + '_refused_without_source_changes'] = (
            code == 'backup_pending' and not archive.exists() and _snapshot(source) == before and provider.calls == 0)
    return checks


def publication_checks(root):
    """Only disposable synthetic acquisitions/corpus. Never real publish/ or rights."""
    from core.acquisitions import Acquisition, SourceRegister
    from core.corpus import CorpusCatalog, CorpusFile
    from core.knowledge import KnowledgeItem
    from tools.publish_projection import build, plan
    from core.ledger import LedgerCorrupt
    root.mkdir(mode=0o700)
    register = SourceRegister(root / 'sources/acquisitions.jsonl')
    register.acquire(Acquisition('open-fixture', 'مصطنع مأذون', 'fixture:open', '2026-01-01', True, True, 'synthetic distribution permission'))
    register.acquire(Acquisition('internal-fixture', 'مصطنع داخلي', 'fixture:private', '2026-01-01', True, False, 'synthetic internal permission only'))
    for store in ('maritime', 'lexicons', 'lexicons-local'):
        folder = root / 'corpus' / store; folder.mkdir(parents=True)
        CorpusCatalog(folder / '_catalog.jsonl').anchor()
    folder = root / 'corpus/maritime'
    def item(text, source, allowed):
        return KnowledgeItem(text=text, lang='ar', domain='maritime', use_internal=True,
            use_distribution=allowed, source_id=source, locus='fixture page', originality='original', part='fixture')
    public = 'PUBLIC_SYNTHETIC_ONLY_approved_page'
    hidden = ['INTERNAL_SYNTHETIC_SECRET', 'FORGED_DISTRIBUTION_SECRET', 'UNKNOWN_SOURCE_SECRET']
    normal = CorpusFile(folder / 'normal.jsonl')
    head = normal.ingest('normal', [item(public, 'open-fixture', True), item(hidden[0], 'internal-fixture', False)], register)
    normal.anchor(); catalog = CorpusCatalog(folder / '_catalog.jsonl')
    catalog.record('normal', 'corpus/maritime/normal.jsonl', 2, head); catalog.anchor()
    forged = CorpusFile(folder / 'forged.jsonl')
    head = forged._locked_ingest('forged', [item(hidden[1], 'internal-fixture', True), item(hidden[2], 'missing-fixture', True)])
    forged.anchor(); catalog.record('forged', 'corpus/maritime/forged.jsonl', 2, head); catalog.anchor()
    private = root / 'var'; private.mkdir(mode=0o700)
    (private / 'workspace-canary.txt').write_text('PRIVATE_WORKSPACE_NEVER_PUBLISHED')
    def published_texts():
        return [json.loads(line)['item']['text'] for path in (root / 'publish').glob('*.jsonl')
                if not path.name.startswith('_') for line in path.read_text().splitlines()]
    result = build(root)
    texts = published_texts()
    checks = {
        'only_current_rights_authorized_fixture_is_published': result['published'] == 1 and texts == [public],
        'internal_and_unknown_or_forged_rights_never_publish': result['skipped'] == 1 and result['rights_refused'] == 2 and
            all(text not in '\n'.join(texts) for text in [*hidden, 'PRIVATE_WORKSPACE_NEVER_PUBLISHED']),
    }
    before_plan = _snapshot(root)
    preview = plan(root)
    checks['publication_plan_is_read_only'] = preview['report']['published'] == 1 and _snapshot(root) == before_plan
    late_catalog = root / 'corpus/lexicons/_catalog.jsonl'
    valid_catalog = late_catalog.read_bytes()
    late_catalog.write_bytes(b'corrupt synthetic catalog\n')
    before_failure = _snapshot(root / 'publish')
    try:
        build(root)
    except (LedgerCorrupt, ValueError):
        checks['late_validation_failure_preserves_previous_publication'] = _snapshot(root / 'publish') == before_failure
    else:
        checks['late_validation_failure_preserves_previous_publication'] = False
    late_catalog.write_bytes(valid_catalog)
    register.acquire(Acquisition('open-fixture', 'مصطنع مسحوب التوزيع', 'fixture:open', '2026-01-02', True, False, 'synthetic distribution revoked'))
    revoked = build(root)
    checks['current_source_revocation_removes_previously_published_text'] = revoked['published'] == 0 and published_texts() == [] and revoked['rights_refused'] == 3
    return checks


def run_synthetic(root):
    root.mkdir(mode=0o700, parents=True)
    checks, initial = restore_checks(root)
    checks.update(pending_checks(root))
    checks.update(publication_checks(root / 'synthetic-knowledge'))
    return {'schema_version': 1, 'scope': 'synthetic_local_workspace_restore_and_publication_restrictions',
        'checks': checks, 'passed': sum(v is True for v in checks.values()), 'total': len(checks),
        'synthetic_provider_calls_during_fixture_creation': initial, 'live_model_calls': 0,
        'human_restore_trial': 'not_performed', 'real_publication': 'not_performed',
        'rights_changes': 'synthetic_fixtures_only', 'quality_pending': True,
        'human_review': 'not_required_q49', 'automated_review': 'pending',
        'independent_bank': 'not_provided', 'm8b_complete': False, 'release_ready': False}


def main():
    from workspace_tools.backup import BackupError
    try:
        with tempfile.TemporaryDirectory(prefix='diwan-m13-') as temp:
            report = run_synthetic(Path(temp).resolve() / 'case')
    except Exception as exc:
        print(json.dumps({'error_code': getattr(exc, 'code', type(exc).__name__), 'release_ready': False}))
        return 1
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(report['checks'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
