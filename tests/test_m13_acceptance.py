"""Synthetic-only acceptance; no existing workspace, rights or publication modified."""
from acceptance_m13 import publication_checks, prepare_workspace, _snapshot


def test_publication_fixture_checks_current_rights_not_labels(tmp_path):
    checks = publication_checks(tmp_path.resolve() / 'knowledge')
    assert all(checks.values()), checks


def test_source_fixture_covers_text_png_wav_receipts_and_proposal(tmp_path):
    root = tmp_path.resolve() / 'source'
    result = prepare_workspace(root)
    assert result['synthetic_calls'] == 3
    assert len(result['expected']['text_history']['turns']) == 1
    assert len(result['expected']['media_history']['turns']) == 2
    assert [r['media'][0]['kind'] for r in result['expected']['media_inputs']] == ['image', 'audio']
    assert result['expected']['files']['files']
    outputs = root / 'projects' / result['project'] / 'outputs'
    assert (outputs / 'approved.txt').is_file() and not (outputs / 'not-approved.txt').exists()


def test_full_synthetic_acceptance_remains_honest(tmp_path):
    from acceptance_m13 import run_synthetic
    report = run_synthetic(tmp_path.resolve() / 'case')
    assert all(report['checks'].values()), report
    assert report['live_model_calls'] == 0 and report['synthetic_provider_calls_during_fixture_creation'] == 3
    assert report['release_ready'] is False and report['quality_pending'] is True
    assert report['human_review'] == 'not_required_q49'
    assert report['automated_review'] == 'pending'
    assert report['real_publication'] == 'not_performed'
    assert report['human_restore_trial'] == 'not_performed'
