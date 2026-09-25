"""Independent m12 service/CLI audit using synthetic providers and temp stores."""
import base64
import json
import threading

import pytest

from acceptance_m12 import png_fixture,wav_fixture
from conversation import ChatSession,ConversationError
from conversation.session import SYSTEM
from core.canonical import digest
from core.contracts import Response,Usage
from multimodal.codec import MEDIA_SYSTEM,MEDIA_CONTEXT_CHARS,MediaError,pack_media,read_selected,decode_request
from providers.base import ProviderError
from services.media_assistant import MediaAssistant,MediaAssistantError
import services.media_assistant as service
from tools import media as cli
from workspace_tools.preferences import Preferences

MODEL='synthetic-audit-media'
VERSION='a'*64


class Provider:
    is_local=True
    name='synthetic-audit'
    model=MODEL
    def __init__(self,stop='complete'):
        self.calls=[];self.estimates=0;self.stop=stop
    def estimate_micros(self,request):
        self.estimates+=1
        return 0
    def complete(self,request):
        self.calls.append(request)
        return Response('جواب مصطنع\x1b[2J',Usage(3,4),self.stop,0)


class Poison:
    def __getattr__(self,name):
        raise AssertionError('dependency used during replay: '+name)


def opened(tmp_path,provider=None,preferences=None,session='fixture'):
    return MediaAssistant.open(tmp_path.resolve()/'store',session,model=MODEL,
        model_version=VERSION,provider=provider,preferences=preferences)


def document(kind='image'):
    return pack_media(png_fixture() if kind=='image' else wav_fixture(),kind+'.data')


def snapshot(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*')
            if p.is_file() and not p.name.endswith('.lock')}


@pytest.mark.parametrize('kind',['image','audio'])
def test_frozen_media_survives_source_deletion_and_poisoned_dependencies(tmp_path,kind):
    raw=png_fixture() if kind=='image' else wav_fixture()
    path=tmp_path/'selected.data';path.write_bytes(raw)
    provider=Provider();prefs=Preferences(tmp_path.resolve()/'preferences')
    prefs.set('address_name','اسم أول',expected_revision=0)
    assistant=opened(tmp_path,provider,prefs)
    doc=read_selected(path)
    first=assistant.ask('one','اقرأ الوسيط',media=(doc,))
    before=snapshot(assistant.session.root)
    path.unlink();prefs.set('address_name','اسم ثان',expected_revision=1)
    reopened=opened(tmp_path,Poison(),Poison())
    replay=reopened.replay('one');inspection=reopened.inspect('one');history=reopened.history()
    assert replay['replayed'] and replay['content']==first['content']
    assert history==[replay]
    assert inspection['preferences']['values']['address_name']=='اسم أول'
    assert base64.b64decode(inspection['media'][0]['data_base64'])==raw
    assert len(provider.calls)==provider.estimates==1
    assert snapshot(assistant.session.root)==before


def test_same_turn_is_replayed_before_current_preferences_are_read(tmp_path):
    provider=Provider();assistant=opened(tmp_path,provider)
    doc=document();first=assistant.ask('same','طلب',media=(doc,))
    assistant.provider=Poison();assistant.preferences=Poison()
    replay=assistant.ask('same','طلب',media=(doc,))
    assert replay['replayed'] and replay['request_sha256']==first['request_sha256']
    assert len(provider.calls)==1


@pytest.mark.parametrize('change',['text','bytes','filename','remove-media'])
def test_conflicting_duplicate_never_calls_provider_or_changes_saved_bytes(tmp_path,change):
    provider=Provider();assistant=opened(tmp_path,provider);doc=document()
    assistant.ask('same','طلب',media=(doc,));before=snapshot(assistant.session.root)
    message='مختلف' if change=='text' else 'طلب'
    selected=doc
    if change=='bytes': selected=document('audio')
    if change=='filename': selected={**doc,'name':'different.png'}
    media=() if change=='remove-media' else (selected,)
    with pytest.raises(ConversationError,match='turn_conflict'):
        assistant.ask('same',message,media=media)
    assert snapshot(assistant.session.root)==before and len(provider.calls)==1


@pytest.mark.parametrize('kind',['sha','size','base64','metadata','type','count'])
def test_bad_media_never_reaches_provider_estimate_or_complete(tmp_path,kind):
    provider=Provider();assistant=opened(tmp_path,provider);doc=document()
    before=snapshot(assistant.session.root)
    if kind=='sha': doc['sha256']='0'*64
    elif kind=='size': doc['size_bytes']+=1
    elif kind=='base64': doc['data_base64']='broken'
    elif kind=='metadata': doc['metadata']['width']+=1
    elif kind=='type': doc['kind']='video'
    selected=(doc,doc) if kind=='count' else (doc,)
    with pytest.raises((MediaError,MediaAssistantError)):
        assistant.ask('one','طلب',media=selected)
    assert provider.calls==[] and provider.estimates==0
    assert snapshot(assistant.session.root)==before


def test_caller_mutation_cannot_change_frozen_media_or_returned_inspection(tmp_path):
    provider=Provider();assistant=opened(tmp_path,provider);doc=document()
    original=json.loads(json.dumps(doc))
    result=assistant.ask('one','طلب',media=(doc,))
    doc['metadata']['width']=99;doc['data_base64']='bad'
    inspect=assistant.inspect('one');inspect['media'][0]['data_base64']='also bad'
    assert assistant.inspect('one')['media']==[original]
    assert 'data_base64' not in json.dumps(result)
    assert decode_request(provider.calls[0].messages[-1].content)['media']==[original]


def test_custom_system_does_not_change_old_text_profile(tmp_path):
    provider=Provider();root=tmp_path.resolve()/'store'
    legacy=ChatSession(root,'legacy',model=MODEL,model_version=VERSION)
    first=legacy.turn('one','نص عادي',provider)
    saved=snapshot(root)
    assert legacy.config['system_sha256']==digest(SYSTEM)
    assert provider.calls[0].messages[0].content==SYSTEM
    with pytest.raises(ConversationError,match='config_conflict'):
        MediaAssistant.open(root,'legacy',model=MODEL,model_version=VERSION,provider=Poison())
    assert snapshot(root)==saved
    reopened=ChatSession(root,'legacy',model=MODEL,model_version=VERSION)
    assert reopened.turn('one','نص عادي',Poison())['content']==first['content']
    assert len(provider.calls)==1


def test_media_session_cannot_be_reopened_as_text_session(tmp_path):
    provider=Provider();assistant=opened(tmp_path,provider)
    assistant.ask('one','طلب',media=(document(),));before=snapshot(assistant.session.root)
    with pytest.raises(ConversationError,match='config_conflict'):
        ChatSession(assistant.session.root,'fixture',model=MODEL,model_version=VERSION)
    assert snapshot(assistant.session.root)==before


@pytest.mark.parametrize('turn',['missing','other'])
def test_unknown_replay_has_named_closed_error_without_provider(tmp_path,turn):
    assistant=opened(tmp_path,Poison())
    with pytest.raises(MediaAssistantError,match='media_turn_missing'):
        assistant.replay(turn)
    with pytest.raises(MediaAssistantError,match='media_turn_missing'):
        assistant.inspect(turn)
    assert assistant.history()==[]


def test_completed_history_carries_old_media_and_new_preferences(tmp_path):
    provider=Provider();preferences=Preferences(tmp_path.resolve()/'prefs')
    assistant=opened(tmp_path,provider,preferences);doc=document()
    assistant.ask('one','الأول',media=(doc,))
    preferences.set('verbosity','concise',expected_revision=0)
    assistant.ask('two','تابع')
    messages=provider.calls[-1].messages
    assert [m.role for m in messages]==['system','user','assistant','user']
    assert messages[0].content==MEDIA_SYSTEM
    old,new=decode_request(messages[1].content),decode_request(messages[-1].content)
    assert old['media']==[doc] and new['media']==[]
    assert old['preferences']['revision']==0 and new['preferences']['values']=={'verbosity':'concise'}


def test_truncated_prior_media_is_not_silently_reused_as_complete_context(tmp_path):
    provider=Provider('max_output');assistant=opened(tmp_path,provider)
    first=assistant.ask('one','الأول',media=(document(),));assert first['status']=='truncated'
    provider.stop='complete';assistant.ask('two','طلب جديد')
    assert [m.role for m in provider.calls[-1].messages]==['system','user']
    assert assistant.history()[0]['status']=='truncated'


def test_turn_cap_is_enforced_and_old_replay_still_works(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'MAX_TURNS',2)
    provider=Provider();assistant=opened(tmp_path,provider)
    assistant.ask('one','طلب');assistant.ask('two','طلب آخر')
    with pytest.raises(MediaAssistantError,match='media_session_limit'):
        assistant.ask('three','ثالث')
    assert assistant.replay('one')['replayed'] and len(provider.calls)==2


def test_saved_input_byte_cap_is_enforced_before_provider(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'MAX_SAVED_INPUT_BYTES',100)
    provider=Provider();assistant=opened(tmp_path,provider)
    before=snapshot(assistant.session.root)
    with pytest.raises(MediaAssistantError,match='media_session_limit'):
        assistant.ask('one','طلب',media=(document(),))
    assert provider.calls==[] and provider.estimates==0
    assert snapshot(assistant.session.root)==before


def test_cli_replay_history_inspect_never_construct_provider_or_read_file(tmp_path,monkeypatch,capsys):
    provider=Provider();monkeypatch.setattr(cli,'LocalMediaProvider',lambda *args:provider)
    path=tmp_path/'chosen.png';path.write_bytes(png_fixture())
    args=['--root',str(tmp_path.resolve()/'cli'),'--session','fixture','--model',MODEL,'--model-version',VERSION]
    assert cli.main(args+['ask','--turn','one','--message','طلب','--file',str(path)])==0
    raw=capsys.readouterr().out;first=json.loads(raw)
    assert '\x1b' not in raw and '\\u001b' in raw
    path.unlink()
    def forbidden(*args,**kwargs): raise AssertionError('replay dependency')
    monkeypatch.setattr(cli,'LocalMediaProvider',forbidden);monkeypatch.setattr(cli,'read_selected',forbidden)
    for command in (['replay','--turn','one'],['inspect','--turn','one'],['history']):
        assert cli.main(args+command)==0
        result=json.loads(capsys.readouterr().out)
        assert 'error_code' not in result or result['error_code'] is None
    assert len(provider.calls)==provider.estimates==1


def test_cli_invalid_media_reports_error_without_model(tmp_path,monkeypatch,capsys):
    provider=Provider();monkeypatch.setattr(cli,'LocalMediaProvider',lambda *args:provider)
    bad=tmp_path/'bad.png';bad.write_bytes(b'not a png')
    args=['--root',str(tmp_path.resolve()/'cli'),'--session','fixture','--model',MODEL,'--model-version',VERSION]
    assert cli.main(args+['ask','--turn','one','--message','طلب','--file',str(bad)])==2
    result=json.loads(capsys.readouterr().out)
    assert result['error_code']=='media_type_unsupported' and result['release_ready'] is False
    assert provider.calls==[] and provider.estimates==0


def test_regression_concurrent_distinct_turns_cannot_cross_session_limit(tmp_path):
    """Real 32-turn bound, with only permitted preference-snapshot scheduling."""
    provider=Provider();assistant=opened(tmp_path,provider)
    for number in range(service.MAX_TURNS-1):
        assistant.ask('seed'+str(number),'طلب')
    reached={name:threading.Event() for name in ('first','second')}
    resume={name:threading.Event() for name in ('first','second')}
    class OrderedPreferences:
        def snapshot(self):
            name=threading.current_thread().name
            reached[name].set()
            assert resume[name].wait(3)
            return {'revision':0,'values':{},'sha256':digest({'revision':0,'values':{}})}
    assistant.preferences=OrderedPreferences()
    results={}
    def run(name):
        try: results[name]=assistant.ask(name,'متزامن')
        except Exception as error: results[name]=getattr(error,'code',type(error).__name__)
    first=threading.Thread(target=run,args=('first',),name='first')
    second=threading.Thread(target=run,args=('second',),name='second')
    try:
        first.start();assert reached['first'].wait(3)
        second.start();assert reached['second'].wait(3)
        resume['first'].set();first.join(3);assert not first.is_alive()
        resume['second'].set();second.join(3);assert not second.is_alive()
    finally:
        resume['first'].set();resume['second'].set()
        first.join(3);second.join(3)
    saved=assistant.history()
    assert len(saved)==service.MAX_TURNS, {'saved':len(saved),'limit':service.MAX_TURNS,'results':results}
    assert results['first']['status']=='complete'
    assert results['second']=='media_session_limit'
    assert len(provider.calls)==provider.estimates==service.MAX_TURNS
