import concurrent.futures
import io
import threading
import wave
from pathlib import Path

import pytest

from core.contracts import Response, Usage
from providers.base import ProviderError
from services.media_assistant import MediaAssistant, MAX_SAVED_INPUT_BYTES
from multimodal.codec import pack_media, encode_request


class SyntheticProvider:
    is_local = True
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail
    def estimate_micros(self, request):
        return 0
    def complete(self, request):
        self.calls += 1
        if self.fail:
            raise ProviderError('synthetic_error','test only',retryable=False)
        return Response('saved', Usage(1,1), 'complete', 0)


def race_two_calls(assistant, media):
    session = assistant.session
    original_history, original_turn = session.history, session.turn
    first_read = threading.Event()
    both_read = threading.Barrier(2)
    first_done = threading.Event()
    def snapshot_then_wait():
        history = original_history()
        first_read.set()
        both_read.wait(5)
        return history
    def ordered_turn(turn_id, text, provider, **limits):
        if turn_id == 'late':
            assert first_done.wait(10)
        try:
            return original_turn(turn_id,text,provider,**limits)
        finally:
            if turn_id == 'early':
                first_done.set()
    session.history, session.turn = snapshot_then_wait, ordered_turn
    try:
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            early = pool.submit(assistant.ask,'early','new request',media=media)
            assert first_read.wait(5)
            late = pool.submit(assistant.ask,'late','new request',media=media)
            outcomes=[]
            for future in (early,late):
                try:
                    outcomes.append(future.result(15))
                except Exception as error:
                    outcomes.append(error)
    finally:
        session.history, session.turn = original_history, original_turn
    return outcomes, original_history()


def wav_document(frames):
    output=io.BytesIO()
    with wave.open(output,'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\0\0'*frames)
    return pack_media(output.getvalue(),'fixture.wav')


def test_concurrent_admission_cannot_exceed_32_turns(tmp_path):
    provider=SyntheticProvider()
    assistant=MediaAssistant.open(tmp_path.resolve()/'sessions','fixture',model='synthetic',model_version='a'*64,provider=provider)
    for i in range(31):
        assistant.ask('seed_'+str(i),'request')
    outcomes,history=race_two_calls(assistant,())
    assert [getattr(o,'code',None) for o in outcomes if not isinstance(o,dict)]==['media_session_limit']
    assert len(history)==32 and provider.calls==32 and sum(isinstance(o,dict) for o in outcomes)==1, {'turn_count':len(history),'provider_calls':provider.calls,
                             'outcomes':[getattr(o,'code',o.get('status') if isinstance(o,dict) else type(o).__name__) for o in outcomes]}


def test_concurrent_admission_cannot_exceed_4mib_saved_inputs(tmp_path):
    provider=SyntheticProvider(fail=True)
    assistant=MediaAssistant.open(tmp_path.resolve()/'sessions','fixture',model='synthetic',model_version='a'*64,provider=provider)
    large=wav_document(128000)
    for i in range(12):
        assistant.ask('seed_'+str(i),'request',media=(large,))
    saved=sum(len(t['text'].encode()) for t in assistant.session.history())
    incoming=(wav_document(24000),)
    cost=len(encode_request('new request',incoming).encode())
    assert saved+cost<=MAX_SAVED_INPUT_BYTES<saved+2*cost
    outcomes,history=race_two_calls(assistant,incoming)
    assert [getattr(o,'code',None) for o in outcomes if not isinstance(o,dict)]==['media_session_limit']
    final=sum(len(t['text'].encode()) for t in history)
    assert final==saved+cost and final<=MAX_SAVED_INPUT_BYTES and provider.calls==13, {'input_bytes':final,'limit':MAX_SAVED_INPUT_BYTES,
                                       'provider_calls':provider.calls,'turns':len(history)}
