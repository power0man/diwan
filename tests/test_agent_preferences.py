"""Explicit project preferences enter new agent turns, never historical replay."""
import json
from pathlib import Path
import uuid

import pytest

from core.canonical import canonical_bytes
from services.agent_workspace import decode_input, encode_input, INPUT_PREFIX, ATTACHMENT_POLICY
from tests.test_agent_webui import AgentRunning, response
from workspace_tools.files import WorkspaceError
from workspace_tools.preferences import Preferences


@pytest.fixture
def daily(tmp_path):
    app = AgentRunning(tmp_path.resolve() / 'ui')
    try:
        yield app
    finally:
        app.close()


def ask(app, ctx, turn, message='أجب بحسب تفضيلاتي'):
    return app.api('agent_ask', **ctx, turn=turn, message=message, files=[])


def test_preferences_are_frozen_per_turn_and_replay_needs_no_current_store(daily):
    ctx = daily.agent()
    original = daily.api('preferences', project=ctx['project'])
    saved = daily.api('set_preference', project=ctx['project'], key='response_language',
                      value='en', revision=original['revision'])
    first_snapshot = daily.api('set_preference', project=ctx['project'], key='verbosity',
                               value='concise', revision=saved['revision'])
    observed = []
    def inspect(request):
        observed.append(decode_input(request.messages[-1].content)['preferences'])
        return response('Fixture answer')
    daily.provider.responses = [inspect, inspect]
    first_turn = uuid.uuid4().hex
    first = ask(daily, ctx, first_turn)
    assert observed == [first_snapshot]
    assert first['inputs']['preferences'] == first_snapshot
    changed = daily.api('set_preference', project=ctx['project'], key='response_language',
                        value='ar', revision=first_snapshot['revision'])
    second_snapshot = daily.api('delete_preference', project=ctx['project'], key='verbosity',
                                revision=changed['revision'])
    second = ask(daily, ctx, uuid.uuid4().hex)
    assert observed == [first_snapshot, second_snapshot]
    assert second['inputs']['preferences'] == second_snapshot
    prefs = daily.app.project(ctx['project']) / 'preferences'
    prefs.rename(prefs.with_name('preferences-unavailable'))
    daily.app.agent_provider_factory = lambda: pytest.fail('historical replay cannot construct provider')
    replay = ask(daily, ctx, first_turn)
    assert replay['replayed'] is True and replay['inputs']['preferences'] == first_snapshot
    history = daily.api('history', **ctx, before=None)
    assert [turn['inputs']['preferences'] for turn in history['turns']] == observed
    assert len(daily.provider.requests) == 2


def test_agent_does_not_infer_or_create_preferences_from_user_text(daily):
    ctx = daily.agent()
    seen = []
    def inspect(request):
        seen.append(decode_input(request.messages[-1].content)['preferences'])
        return response('قرأت الطلب')
    daily.provider.responses = [inspect]
    ask(daily, ctx, uuid.uuid4().hex, 'اسمي اسم تجريبي، لخص العبارة فقط.')
    assert seen == [None]
    assert not (daily.app.project(ctx['project']) / 'preferences').exists()


def test_preferences_never_cross_projects(daily):
    one, two = daily.agent(), daily.agent()
    snapshot = daily.api('preferences', project=one['project'])
    daily.api('set_preference', project=one['project'], key='address_name', value='مالك المشروع الأول',
              revision=snapshot['revision'])
    def inspect(request):
        assert decode_input(request.messages[-1].content)['preferences'] is None
        return response('مشروع مستقل')
    daily.provider.responses = [inspect]
    result = ask(daily, two, uuid.uuid4().hex)
    assert result['inputs']['preferences'] is None
    assert daily.api('history', **one, before=None)['turns'] == []


def test_old_input_envelope_remains_readable_and_new_snapshot_is_verified(tmp_path):
    old = {'user_request': 'نص قديم', 'attachments': [], 'attachment_policy': ATTACHMENT_POLICY}
    assert decode_input(INPUT_PREFIX + canonical_bytes(old).decode()) == old
    snapshot = Preferences(tmp_path.resolve() / 'prefs').snapshot()
    encoded = encode_input('نص جديد', [], snapshot)
    assert decode_input(encoded)['preferences'] == snapshot
    invalid = dict(snapshot, sha256='0' * 64)
    with pytest.raises(ValueError):
        encode_input('نص', [], invalid)
    with pytest.raises(WorkspaceError, match='agent_input_invalid'):
        decode_input(encoded.replace(snapshot['sha256'], '0' * 64))
