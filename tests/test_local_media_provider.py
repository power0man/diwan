"""وسائط مصطنعة ونقل HTTP مصطنع؛ لا يُشغَّل نموذج أو تُفتح شبكة."""
from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import socket
import struct
import sys
import threading
import time
import wave
import zlib

import pytest

from core.budget import Budget
from core.canonical import canonical_bytes, digest
from core.contracts import Message, Request, ToolSpec
from core.ledger import Ledger
from core.run import execute
from multimodal.codec import MEDIA_PREFIX, MEDIA_SYSTEM, POLICY
from providers.base import ProviderError
from providers.local_media import LocalMediaProvider
import providers.local_chat as local

MODEL = 'synthetic-media-fixture:1'
VERSION = 'a' * 64


def png(rgb=(255,0,0)):
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(b'\x00'+bytes(rgb)))+chunk(b'IEND',b''))


def wav(frames=160, sample=0):
    out=io.BytesIO()
    with wave.open(out,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000)
        stream.writeframes(struct.pack('<h',sample)*frames)
    return out.getvalue()


def media(kind='image', *, frames=160, sample=0):
    raw=png() if kind=='image' else wav(frames,sample)
    return {'name':'fixture.png' if kind=='image' else 'fixture.wav','kind':kind,
            'mime':'image/png' if kind=='image' else 'audio/wav',
            'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw),
            'metadata':({'width':1,'height':1} if kind=='image' else
                        {'sample_rate':16000,'channels':1,'sample_width':2,'frames':frames}),
            'data_base64':base64.b64encode(raw).decode('ascii')}


def envelope(doc=None, *, text='ما المحتوى؟', preferences=None):
    value={'schema_version':1,'kind':'multimodal_request','policy':dict(POLICY),
           'user_request':text,'preferences':preferences,'media':[] if doc is None else [doc]}
    return MEDIA_PREFIX+canonical_bytes(value).decode('utf-8')


def request(doc=None, **changes):
    messages=(Message('system',MEDIA_SYSTEM), Message('user',envelope(media() if doc is None else doc)))
    return replace(Request(messages,MODEL,VERSION,800,30,'local_only','media-turn'),**changes)


def history(docs):
    messages=[Message('system',MEDIA_SYSTEM)]
    for index, doc in enumerate(docs):
        if index: messages.append(Message('assistant','جواب سابق'))
        messages.append(Message('user',envelope(doc)))
    return request(messages=tuple(messages))


def tags():
    return {'models':[{'name':MODEL,'model':MODEL,'digest':VERSION,'size':1000,'details':{'format':'gguf'}}]}


def shown(caps=('completion','vision','audio','thinking'), context=8192):
    return {'details':{'format':'gguf'},'model_info':{'general.architecture':'fixture','fixture.context_length':context},'capabilities':list(caps)}


def answer():
    return {'model':MODEL,'message':{'role':'assistant','content':'جواب مصطنع'},
            'done':True,'done_reason':'stop','prompt_eval_count':32,'eval_count':4}


class Socket:
    def __init__(self): self.shut=threading.Event()
    def shutdown(self,direction):
        assert direction==socket.SHUT_RDWR
        self.shut.set()


class Reply:
    def __init__(self,payload=None,*,status=200,raw=None,headers=None,hang=False):
        self.raw=raw if raw is not None else json.dumps(payload).encode()
        self.status=status;self.headers={'Content-Type':'application/json',**(headers or {})}
        self.hang=hang;self.sock=None;self.closed=False
    def getheader(self,key,default=None): return self.headers.get(key,default)
    def read(self,size):
        if self.hang:
            assert self.sock.shut.wait(2),'transport deadline failed'
            raise OSError('fixture socket shutdown')
        return self.raw[:size]
    def close(self): self.closed=True


class Transport:
    def __init__(self,monkeypatch,replies=None):
        self.replies=list(replies if replies is not None else
                          [Reply({'version':'0.34.2'}),Reply(tags()),Reply(shown()),Reply(answer())])
        self.calls=[];self.connections=[]
        outer=self
        class Connection:
            def __init__(self,host,port,*,timeout):
                assert (host,port)==('127.0.0.1',11434)
                assert 0<timeout<=300
                self.sock=Socket();self.closed=False;outer.connections.append(self)
            def connect(self): pass
            def request(self,method,path,*,body,headers): outer.calls.append((method,path,body,headers))
            def getresponse(self):
                result=outer.replies.pop(0)
                if isinstance(result,BaseException): raise result
                result.sock=self.sock;self.sock=None
                return result
            def close(self): self.closed=True
        monkeypatch.setattr(local.http.client,'HTTPConnection',Connection)


def refused(provider,req,code=None):
    with pytest.raises(ProviderError) as error: provider.complete(req)
    if code: assert error.value.code==code
    assert error.value.retryable is False
    return error.value.code


def test_governed_image_call_and_replay_have_exact_http_counts(monkeypatch,tmp_path):
    transport=Transport(monkeypatch);provider=LocalMediaProvider(MODEL,VERSION)
    ledger=Ledger(tmp_path/'calls.jsonl');req=request()
    result=execute(req,provider,Budget(0,0),ledger)
    assert result.response.provider=='ollama-local-media' and result.response.content=='جواب مصطنع'
    assert result.response.usage.input_tokens==32 and result.response.usage.output_tokens==4
    assert (provider.metadata_calls,provider.chat_calls)==(3,1)
    assert [call[1] for call in transport.calls]==['/api/version','/api/tags','/api/show','/api/chat']
    assert transport.calls[0][2] is None and transport.calls[1][2] is None
    assert json.loads(transport.calls[2][2])=={'model':MODEL}
    payload=json.loads(transport.calls[3][2]);msg=payload['messages'][-1]
    doc=media();assert msg['images']==[doc['data_base64']]
    projected=json.loads(msg['content'])
    assert projected['media']==[{key:value for key,value in doc.items() if key!='data_base64'}]
    assert projected['policy']==dict(POLICY)
    assert payload['stream'] is False and payload['truncate'] is False and payload['shift'] is False
    assert payload['think'] is False
    assert payload['options']=={'num_ctx':8192,'num_predict':800,'temperature':0}
    again=execute(req,provider,Budget(0,0),ledger)
    assert again.replayed and again.response==result.response
    assert (provider.metadata_calls,provider.chat_calls)==(3,1)
    assert all(connection.closed for connection in transport.connections)


def test_image_and_audio_history_preserves_media_order_and_assistant_text(monkeypatch):
    transport=Transport(monkeypatch);provider=LocalMediaProvider(MODEL,VERSION)
    image,audio=media(),media('audio');req=history([image,audio])
    provider.complete(req)
    messages=json.loads(transport.calls[-1][2])['messages']
    assert messages[1]['images']==[image['data_base64']]
    assert messages[2]=={'role':'assistant','content':'جواب سابق'}
    assert messages[3]['images']==[audio['data_base64']]
    assert json.loads(messages[3]['content'])['media'][0]['mime']=='audio/wav'


@pytest.mark.parametrize('version',[None,'','0.34.1','0.34.2-rc1','0.34.3',34])
def test_runtime_mismatch_stops_before_model_metadata_and_media(monkeypatch,version):
    transport=Transport(monkeypatch,[Reply({'version':version})])
    provider=LocalMediaProvider(MODEL,VERSION)
    refused(provider,request(),'local_media_runtime_unsupported')
    assert [call[1] for call in transport.calls]==['/api/version']
    assert provider.chat_calls==0


@pytest.mark.parametrize('kind,cap',[('image','vision'),('audio','audio')])
def test_required_modality_absence_blocks_media(monkeypatch,kind,cap):
    caps=['completion','vision','audio'];caps.remove(cap)
    transport=Transport(monkeypatch,[Reply({'version':'0.34.2'}),Reply(tags()),Reply(shown(caps))])
    provider=LocalMediaProvider(MODEL,VERSION)
    refused(provider,request(media(kind)),'local_media_capability_missing')
    assert provider.chat_calls==0 and len(transport.calls)==3
    assert all(media(kind)['data_base64'].encode() not in (call[2] or b'') for call in transport.calls)


def test_text_followup_still_requires_previous_image_capability(monkeypatch):
    transport=Transport(monkeypatch,[Reply({'version':'0.34.2'}),Reply(tags()),Reply(shown(['completion','audio']))])
    provider=LocalMediaProvider(MODEL,VERSION)
    refused(provider,history([media(),None]),'local_media_capability_missing')
    assert provider.chat_calls==0


def test_8192_context_is_sufficient_and_smaller_is_refused(monkeypatch):
    transport=Transport(monkeypatch,[Reply({'version':'0.34.2'}),Reply(tags()),Reply(shown(context=8191))])
    refused(LocalMediaProvider(MODEL,VERSION),request(),'local_chat_context_unsupported')
    assert len(transport.calls)==3


@pytest.mark.parametrize('position',['version','tags','show','answer'])
def test_remote_metadata_and_response_never_accepted(monkeypatch,position):
    values=[{'version':'0.34.2'},tags(),shown(),answer()]
    target=['version','tags','show','answer'].index(position)
    item=values[target]['models'][0] if position=='tags' else values[target]
    item['remote_host']='https://example.invalid'
    transport=Transport(monkeypatch,[Reply(value) for value in values])
    refused(LocalMediaProvider(MODEL,VERSION),request(),'local_chat_remote_model')
    assert len(transport.calls)==target+1


def test_artifact_change_is_refused_before_media(monkeypatch):
    changed=tags();changed['models'][0]['digest']='b'*64
    transport=Transport(monkeypatch,[Reply({'version':'0.34.2'}),Reply(changed)])
    refused(LocalMediaProvider(MODEL,VERSION),request(),'local_chat_artifact_mismatch')
    assert len(transport.calls)==2


@pytest.mark.parametrize('change,code',[
    ({'model':'different-fixture'},'local_chat_request_identity'),
    ({'model_version':'b'*64},'local_chat_request_identity'),
    ({'tools':(ToolSpec('probe','أداةٌ صوريّة داخل اختبار',{'type':'object'},
                        consent='auto'),)},'local_media_tools_unsupported'),
    ({'max_output':4097},'local_chat_output_limit'),
    ({'deadline_s':301},'local_media_deadline_limit'),
])
def test_request_limits_fail_before_any_http(monkeypatch,change,code):
    transport=Transport(monkeypatch,[])
    refused(LocalMediaProvider(MODEL,VERSION),request(**change),code)
    assert transport.calls==[]


@pytest.mark.parametrize('messages',[
    (Message('user','plain'),),
    (Message('system','different'),Message('user','plain')),
    (Message('system',MEDIA_SYSTEM),Message('assistant','answer')),
    (Message('system',MEDIA_SYSTEM),Message('user','plain'),Message('assistant','answer')),
    (Message('system',MEDIA_SYSTEM),Message('user','plain'),Message('system',MEDIA_SYSTEM),Message('user','plain')),
])
def test_message_system_and_order_are_closed(monkeypatch,messages):
    transport=Transport(monkeypatch,[])
    refused(LocalMediaProvider(MODEL,VERSION),request(messages=messages))
    assert transport.calls==[]


def test_plain_user_text_is_never_sent_from_media_profile(monkeypatch):
    transport=Transport(monkeypatch,[])
    refused(LocalMediaProvider(MODEL,VERSION),request(messages=(Message('system',MEDIA_SYSTEM),Message('user','نص دون غلاف'))))
    assert transport.calls==[]


@pytest.mark.parametrize('field,value',[('sha256','0'*64),('size_bytes',1),('data_base64','not-base64'),('mime','image/jpeg')])
def test_invalid_media_is_closed_before_http(monkeypatch,field,value):
    transport=Transport(monkeypatch,[]);doc=media();doc[field]=value
    refused(LocalMediaProvider(MODEL,VERSION),request(doc))
    assert transport.calls==[]


def test_policy_change_is_rejected_before_http(monkeypatch):
    transport=Transport(monkeypatch,[])
    text=envelope(media());value=json.loads(text[len(MEDIA_PREFIX):]);value['policy']['num_ctx']=16384
    altered=MEDIA_PREFIX+canonical_bytes(value).decode()
    refused(LocalMediaProvider(MODEL,VERSION),request(messages=(Message('system',MEDIA_SYSTEM),Message('user',altered))))
    assert transport.calls==[]


def test_media_and_policy_are_already_bound_by_request_fingerprint():
    first=request(media('audio',sample=1));second=request(media('audio',sample=2))
    assert digest(first.fingerprint_payload())!=digest(second.fingerprint_payload())
    assert 'data_base64' in first.messages[-1].content
    assert 'provider_version' in first.messages[-1].content


def test_five_media_in_history_are_rejected_without_http(monkeypatch):
    transport=Transport(monkeypatch,[])
    refused(LocalMediaProvider(MODEL,VERSION),history([media() for _ in range(5)]),'local_media_context_limit')
    assert transport.calls==[]


def test_cumulative_media_bytes_are_bounded_without_http(monkeypatch):
    transport=Transport(monkeypatch,[])
    refused(LocalMediaProvider(MODEL,VERSION),history([media('audio',frames=68000) for _ in range(4)]),'local_media_context_limit')
    assert transport.calls==[]


def test_transport_accepts_valid_media_history_above_old_512k_cap(monkeypatch):
    transport=Transport(monkeypatch);provider=LocalMediaProvider(MODEL,VERSION)
    provider.complete(history([media('audio',frames=60000) for _ in range(4)]))
    assert 512*1024<len(transport.calls[-1][2])<=2*1024*1024
    assert provider.chat_calls==1


def test_plain_text_and_assistant_budget_is_independent_from_base64(monkeypatch):
    transport=Transport(monkeypatch,[])
    source=history([media(),None]);messages=list(source.messages)
    messages[2]=Message('assistant','س'*24000)
    refused(LocalMediaProvider(MODEL,VERSION),replace(source,messages=tuple(messages)),'local_media_text_limit')
    assert transport.calls==[]


@pytest.mark.parametrize('part,key,value',[
    ('message','images',['unexpected']),('message','audio',{'data':'unexpected'}),
    ('message','tool_calls',[{'name':'unexpected'}]),('message','thinking','hidden reasoning'),
    ('message','role','user'),('message','content',None),
    ('root','audio','unexpected'),('root','done',False),('root','done_reason','other'),
])
def test_output_must_be_completed_plain_assistant_text(monkeypatch,part,key,value):
    out=answer();(out['message'] if part=='message' else out)[key]=value
    transport=Transport(monkeypatch,[Reply({'version':'0.34.2'}),Reply(tags()),Reply(shown()),Reply(out)])
    provider=LocalMediaProvider(MODEL,VERSION)
    refused(provider,request(),'local_media_malformed')
    assert provider.chat_calls==1 and len(transport.calls)==4


@pytest.mark.parametrize('key,value',[('prompt_eval_count',None),('eval_count',True),('eval_count',-1),('eval_count',2**53)])
def test_usage_is_required_integer_and_never_estimated(monkeypatch,key,value):
    out=answer();out[key]=value
    transport=Transport(monkeypatch,[Reply({'version':'0.34.2'}),Reply(tags()),Reply(shown()),Reply(out)])
    refused(LocalMediaProvider(MODEL,VERSION),request(),'local_chat_malformed')
    assert len(transport.calls)==4


def test_truncated_output_is_not_reported_complete(monkeypatch):
    out=answer();out['done_reason']='length'
    Transport(monkeypatch,[Reply({'version':'0.34.2'}),Reply(tags()),Reply(shown()),Reply(out)])
    assert LocalMediaProvider(MODEL,VERSION).complete(request()).stop_reason=='max_output'


@pytest.mark.parametrize('position',range(4))
def test_redirects_never_follow_or_repeat(monkeypatch,position):
    replies=[Reply({'version':'0.34.2'}),Reply(tags()),Reply(shown()),Reply(answer())]
    replies[position]=Reply({},status=307,headers={'Location':'https://example.invalid'})
    transport=Transport(monkeypatch,replies)
    refused(LocalMediaProvider(MODEL,VERSION),request(),'local_chat_redirect')
    assert len(transport.calls)==position+1


def test_response_timeout_has_one_chat_attempt_and_saved_error_replays(monkeypatch,tmp_path):
    transport=Transport(monkeypatch,[Reply({'version':'0.34.2'}),Reply(tags()),Reply(shown()),Reply(answer(),hang=True)])
    provider=LocalMediaProvider(MODEL,VERSION);req=request(deadline_s=.1)
    ledger=Ledger(tmp_path/'calls.jsonl');started=time.monotonic()
    result=execute(req,provider,Budget(0,0),ledger)
    assert result.error_code=='local_chat_timeout'
    replay=execute(req,provider,Budget(0,0),ledger)
    assert replay.replayed and replay.error_code==result.error_code
    assert time.monotonic()-started<2 and provider.chat_calls==1 and len(transport.calls)==4


def test_proxy_and_endpoint_environment_cannot_redirect_media(monkeypatch):
    for name in ('HTTP_PROXY','ALL_PROXY','OLLAMA_HOST'):
        monkeypatch.setenv(name,'http://example.invalid:9999')
    transport=Transport(monkeypatch)
    LocalMediaProvider(MODEL,VERSION).complete(request())
    assert len(transport.calls)==4
    with pytest.raises(TypeError): LocalMediaProvider(MODEL,VERSION,base_url='http://example.invalid')
