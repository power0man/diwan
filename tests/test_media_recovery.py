from pathlib import Path
import pytest

from acceptance_m12 import png_fixture
from core.contracts import Response, Usage
from conversation import ChatSession, ConversationError
from multimodal.codec import pack_media
from services.media_assistant import MediaAssistant

class Provider:
    is_local=True
    def __init__(self): self.calls=0
    def estimate_micros(self,request): return 0
    def complete(self,request):
        self.calls+=1
        return Response('saved response',Usage(1,1),'complete',0)

class Never:
    def __getattr__(self,name): raise AssertionError('unexpected replay dependency')

def open_media(root,provider):
    return MediaAssistant.open(root,'fixture',model='synthetic',model_version='a'*64,provider=provider)


def test_pending_without_ledger_never_reexecutes_selected_media(tmp_path):
    root=tmp_path.resolve()/'chat'
    provider=Provider()
    assistant=open_media(root,provider)
    document=pack_media(png_fixture(),'fixture.png')
    save=assistant.session._save
    def interrupted(state):
        save(state)
        if state['turns'] and state['turns'][-1]['result'] is None:
            raise KeyboardInterrupt()
    assistant.session._save=interrupted
    with pytest.raises(KeyboardInterrupt):
        assistant.ask('turn','describe',media=(document,))
    assert provider.calls==0
    restored=open_media(root,Never())
    result=restored.ask('turn','describe',media=(document,))
    assert result['status']=='error' and result['error_code']=='outcome_uncertain' and result['replayed']
    assert restored.inspect('turn')['media']==[document]
    assert len(restored.history())==1


def test_pending_with_complete_ledger_recovers_without_model(tmp_path):
    root=tmp_path.resolve()/'chat'
    provider=Provider()
    assistant=open_media(root,provider)
    document=pack_media(png_fixture(),'fixture.png')
    save=assistant.session._save
    def interrupted(state):
        if state['turns'] and state['turns'][-1]['result'] is not None:
            raise KeyboardInterrupt()
        save(state)
    assistant.session._save=interrupted
    with pytest.raises(KeyboardInterrupt):
        assistant.ask('turn','describe',media=(document,))
    assert provider.calls==1
    restored=open_media(root,Never())
    result=restored.replay('turn')
    assert result['status']=='complete' and result['content']=='saved response' and result['replayed']
    assert restored.inspect('turn')['media']==[document]
    assert len(restored.history())==1


def test_media_session_cannot_reopen_as_text_profile(tmp_path):
    root=tmp_path.resolve()/'chat'
    assistant=open_media(root,Provider())
    assistant.ask('turn','describe',media=(pack_media(png_fixture(),'fixture.png'),))
    before={p.name:p.read_bytes() for p in (root/'fixture').iterdir() if p.is_file()}
    with pytest.raises(ConversationError,match='config_conflict'):
        ChatSession(root,'fixture',model='synthetic',model_version='a'*64)
    assert {p.name:p.read_bytes() for p in (root/'fixture').iterdir() if p.is_file()}==before
