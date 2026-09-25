/* Behavioral contract checks for the actual app.js, without a browser or network. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(process.argv[2], 'utf8');
const staticIDs = [...fs.readFileSync(path.join(path.dirname(process.argv[2]),'index.html'),'utf8').matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.style = {}; this.dataset = {};
    this.attributes = {}; this.textContent = ''; this.value = ''; this.disabled = false;
    this.hidden = false; this.open = false; this.files = []; this.parent = null;
    this.listeners = {};
  }
  append(...nodes) {for(const node of nodes) {node.parent = this; this.children.push(node);}}
  replaceChildren(...nodes) {this.children = []; this.append(...nodes);}
  add(node) {this.append(node);}
  setAttribute(key, value) {this.attributes[key] = value;}
  remove() {if(this.parent) this.parent.children = this.parent.children.filter(x => x !== this);}
  showModal() {this.open = true;}
  close() {this.open = false;}
  addEventListener(type, handler) {(this.listeners[type] ||= []).push(handler);}
  set value(value) {this._value=value;if(value===''&&['media-file','upload'].includes(this.id)) this.files=[];}
  get value() {return this._value;}
  get options() {return this.children;}
  get selectedOptions() {return this.children.filter(x => x.value === this.value);}
  querySelectorAll(tag) {return descendants(this).filter(x => x.tagName === tag.toUpperCase());}
  set innerHTML(value) {throw new Error('Untrusted HTML rendering forbidden in this contract');}
}
function descendants(el) {return el.children.flatMap(x => [x, ...descendants(x)]);}
function textOf(el) {return el.textContent + el.children.map(textOf).join('\n');}
function deferred() {let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve};}
const tick = async () => {for(let i=0;i<12;i++) await Promise.resolve();};
const event = {preventDefault(){}};
const A='a'.repeat(32), B='b'.repeat(32), SA='c'.repeat(32), SB='d'.repeat(32), T='e'.repeat(32);
const turn = {turn_id:T,user_request:'old request',content:'old answer',status:'complete',usage:{input_tokens:1,output_tokens:1}};

async function harness(routes = {}, options = {}) {
  const nodes = new Map(), calls = [], storage = new Map(Object.entries(options.storage||{})), timers=[], createdURLs=[], revokedURLs=[];
  for(const id of staticIDs) {const el=new Element(id==='projects'?'select':'div');el.id=id;nodes.set(id,el);}
  const get = id => nodes.get(id) || [...nodes.values()].flatMap(descendants).find(el=>el.id===id) || null;
  let uuid = 0;
  const document = {getElementById:get,querySelector:()=>({content:'fixture-csrf'}),
    createElement:tag=>new Element(tag),createTextNode:text=>{const el=new Element('#text');el.textContent=text;return el;}};
  const sandbox = {document, window:{addEventListener(){}},
    Option:function(text,value) {const el=new Element('option');el.textContent=text;el.value=value;return el;},
    crypto:{randomUUID:()=>String(++uuid).padStart(32,'0')},
    sessionStorage:{getItem:key=>storage.has(key)?storage.get(key):null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)},
    setTimeout:fn=>{timers.push(fn);return timers.length;},TextDecoder,AbortController,Blob,
    btoa:text=>Buffer.from(text,'binary').toString('base64'),atob:text=>Buffer.from(text,'base64').toString('binary'),
    URL:{createObjectURL:blob=>{const url='blob:fixture-'+createdURLs.length;createdURLs.push({url,blob});return url;},revokeObjectURL:url=>revokedURLs.push(url)},
    fetch:async (url, options)=>{
      const request=JSON.parse(options.body);calls.push(request);
      let data;
      if(routes[request.action]) data=await routes[request.action](request);
      else if(request.action==='projects') data={projects:[{id:A,name:'A'},{id:B,name:'B'}]};
      else if(request.action==='sessions') data={sessions:[]};
      else if(request.action==='files') data={files:[]};
      else if(request.action==='history') data={status:'idle',turns:[],before:0,total:0};
      else if(request.action==='replay') data={...turn,replayed:true};
      else throw Error('unexpected action '+request.action);
      if(data && data.__httpStatus) return {ok:data.__httpStatus<400,json:async()=>data.body};
      return {ok:true,json:async()=>data};
    },
  };
  const context=vm.createContext(sandbox);vm.runInContext(source,context);await tick();
  const run=code=>vm.runInContext(code,context);
  if(options.preset!==false) {run(`state.project='${A}';state.session='${SA}';state.epoch=1;`);get('projects').value=A;}
  return {get,calls,storage,timers,run,createdURLs,revokedURLs};
}

const cases = {
  async new_general_session_defaults_to_agent_and_sends_on_agent_route() {
    const h=await harness({projects:()=>({projects:[{id:A,name:'A'}],agent_enabled:true,default_session_mode:'agent'}),
      create_session:request=>({id:SB,name:request.name,mode:request.mode}),agent_capabilities:()=>({execution_enabled:false}),agent_ask:()=>turn});
    assert.equal(h.get('session-mode').value,'agent');assert.equal(h.get('agent-option').disabled,false);
    h.get('session-name').value='محادثة عامة';await h.get('new-session').onsubmit(event);
    assert.equal(h.calls.find(x=>x.action==='create_session').mode,'agent');assert.equal(h.run('state.mode'),'agent');
    h.get('message').value='اكتب';await h.get('composer').onsubmit(event);
    assert.equal(h.calls.filter(x=>x.action==='agent_ask').length,1);
    assert.equal(h.calls.some(x=>['ask','ask_media','agent_decide','agent_resume'].includes(x.action)),false);
  },
  async unavailable_agent_cannot_be_selected_by_the_default_advertisement() {
    for(const agent_enabled of [false,undefined,'true']) {
      const h=await harness({projects:()=>({projects:[{id:A,name:'A'}],agent_enabled,default_session_mode:'agent'}),
        create_session:request=>({id:SB,name:request.name,mode:request.mode}),ask:()=>turn});
      assert.equal(h.get('session-mode').value,'text');assert.equal(h.get('agent-option').disabled,true);
      h.get('session-name').value='نص';await h.get('new-session').onsubmit(event);
      h.get('message').value='اكتب';await h.get('composer').onsubmit(event);
      assert.equal(h.calls.find(x=>x.action==='create_session').mode,'text');
      assert.equal(h.calls.filter(x=>x.action==='ask').length,1);assert.equal(h.calls.some(x=>x.action==='agent_ask'),false);
    }
  },
  async bootstrap_keeps_text_when_the_server_does_not_default_to_agent() {
    for(const default_session_mode of ['text',undefined,'media']) {
      const h=await harness({projects:()=>({projects:[{id:A,name:'A'}],agent_enabled:true,media_enabled:true,default_session_mode})});
      assert.equal(h.get('session-mode').value,'text');assert.equal(h.get('agent-option').disabled,false);
    }
  },
  async explicit_text_and_media_choices_survive_project_list_refresh() {
    for(const mode of ['text','media']) {
      const h=await harness({projects:()=>({projects:[{id:A,name:'A'}],agent_enabled:true,media_enabled:true,default_session_mode:'agent'}),
        create_session:request=>({id:SB,name:request.name,mode:request.mode})});
      h.get('session-mode').value=mode;h.get('session-mode').onchange();await h.run('projects()');
      assert.equal(h.get('session-mode').value,mode);
      h.get('session-name').value='اختيار صريح';await h.get('new-session').onsubmit(event);
      assert.equal(h.calls.find(x=>x.action==='create_session').mode,mode);assert.equal(h.run('state.mode'),mode);
    }
  },
  async a_delayed_project_refresh_does_not_replace_a_new_explicit_choice() {
    let pending;const h=await harness({projects:()=>pending ? pending.promise : {projects:[{id:A,name:'A'}],agent_enabled:true,default_session_mode:'agent'}});
    pending=deferred();const refresh=h.run('projects()');await tick();
    h.get('session-mode').value='text';h.get('session-mode').onchange();
    pending.resolve({projects:[{id:A,name:'A'}],agent_enabled:true,default_session_mode:'agent'});await refresh;
    assert.equal(h.get('session-mode').value,'text');
  },
  async unavailable_explicit_mode_returns_to_an_available_default() {
    for(const mode of ['agent','media']) {
      let enabled=true;const h=await harness({projects:()=>({projects:[{id:A,name:'A'}],agent_enabled:enabled,media_enabled:enabled,default_session_mode:'agent'})});
      h.get('session-mode').value=mode;h.get('session-mode').onchange();enabled=false;await h.run('projects()');
      assert.equal(h.get('session-mode').value,'text');assert.equal(h.get('agent-option').disabled,true);assert.equal(h.get('media-option').disabled,true);
    }
  },
  async restoring_legacy_text_sessions_keeps_the_text_route_with_agent_default() {
    for(const mode of [undefined,'text']) {
      const h=await harness({projects:()=>({projects:[{id:A,name:'A'}],agent_enabled:true,default_session_mode:'agent'}),
        sessions:()=>({sessions:[{id:SA,name:'نص محفوظ',mode}]}),ask:()=>turn},
        {preset:false,storage:{'diwan.last':JSON.stringify({project:A,session:SA,mode:'agent'})}});
      await tick();assert.equal(h.get('session-mode').value,'agent');assert.equal(h.run('state.mode'),'text');
      h.get('message').value='تابع';await h.get('composer').onsubmit(event);
      assert.equal(h.calls.filter(x=>x.action==='ask').length,1);assert.equal(h.calls.some(x=>x.action==='agent_ask'),false);
    }
  },
  async agent_stop_is_available_while_busy_and_does_not_claim_rollback() {
    const h=await harness({agent_stop:()=>({status:'stop_requested'})});
    h.run("setMode('agent');state.busy=true");h.storage.set(`diwan.pending.${A}.${SA}`,T);h.run('syncPending()');
    assert.equal(h.get('send').disabled,true);assert.equal(h.get('agent-stop').disabled,false);assert.equal(h.get('agent-stop').hidden,false);
    await h.get('agent-stop').onclick();
    assert.deepEqual(h.calls.filter(x=>x.action==='agent_stop'),[{action:'agent_stop',project:A,session:SA,turn:T}]);
    assert.ok(h.get('notice').textContent.includes('قد تكتمل الخطوة الجارية'));
    assert.equal(h.run('state.busy'),true);
    assert.equal(h.calls.some(x=>['agent_ask','agent_resume','agent_revert'].includes(x.action)),false);
  },
  async agent_stop_failure_allows_explicit_retry_without_false_success() {
    const h=await harness({agent_stop:()=>({__httpStatus:409,body:{error_code:'stop_not_ready'}})});
    h.run(`setMode('agent');state.runningTurn='${T}';syncPending()`);
    await h.get('agent-stop').onclick();
    assert.equal(h.get('notice').className,'error');assert.ok(h.get('notice').textContent.includes('لم يُسجّل طلب الإيقاف'));
    assert.equal(h.get('agent-stop').disabled,false);assert.equal(h.calls.filter(x=>x.action==='agent_stop').length,1);
  },
  async stale_stop_response_does_not_refresh_another_project() {
    const pending=deferred(),h=await harness({agent_stop:()=>pending.promise});
    h.run(`setMode('agent');state.runningTurn='${T}';syncPending()`);
    const stopping=h.get('agent-stop').onclick();await tick();await h.run(`chooseProject('${B}')`);
    pending.resolve({status:'cancelled'});await stopping;
    assert.equal(h.calls.some(x=>x.action==='history'),false);assert.equal(h.get('agent-stop').hidden,true);
    assert.equal(h.run('state.runningTurn'),null);
  },
  async recovered_running_turn_can_be_stopped_without_local_pending_storage() {
    const h=await harness({history:()=>({status:'running',turn:T}),agent_stop:()=>({status:'stop_requested'})});
    h.run("setMode('agent')");await h.run('refresh()');
    assert.equal(h.get('send').disabled,true);await h.get('composer').onsubmit(event);
    assert.equal(h.get('agent-stop').hidden,false);await h.get('agent-stop').onclick();
    assert.equal(h.calls.find(x=>x.action==='agent_stop').turn,T);
    assert.equal(h.calls.some(x=>['agent_ask','agent_resume'].includes(x.action)),false);
  },
  async cancelled_turn_exposes_saved_inputs_and_revert_but_not_old_approval() {
    const h=await harness();const cancelled={...turn,status:'cancelled',pending:[{state:'prepared',name:'run_command'}],
      inputs:{preferences:{revision:3,values:{address_name:'<img onerror=attack()>'},sha256:'f'.repeat(64)},attachments:[]},
      steps:[{index:0,tool_results:[{status:'ok',name:'write_file',content:'saved',action_id:'a',journal_action_id:'act-a'}]}]};
    h.run(`setMode('agent');state.turns=[${JSON.stringify(cancelled)}];render();syncPending()`);
    const text=textOf(h.get('messages'));
    assert.ok(text.includes('سياق الطلب المحفوظ'));assert.ok(text.includes('نسخة التفضيلات: 3'));
    assert.ok(text.includes('الرجوع عن هذا التعديل'));assert.equal(text.includes('مراجعة فعل'),false);
    assert.equal(h.get('send').disabled,false);assert.equal(h.get('agent-stop').hidden,true);
    assert.equal(descendants(h.get('messages')).some(x=>x.tagName==='IMG'),false);
    assert.equal(h.calls.some(x=>x.action==='preferences'),false);
  },
  async agent_approval_is_explicit_bound_and_then_resumes_once() {
    const h=await harness({agent_decide:()=>({state:'approved'}),agent_resume:()=>({status:'complete'})});
    h.run("setMode('agent')");
    const action={action_id:'action-1',call_digest:'f'.repeat(64),revision:3,name:'run_command',arguments:{argv:['python','main.py']},input_files:[{path:'main.py',sha256:'e'.repeat(64),size_bytes:12}]};
    h.run(`reviewAgentAction(context(),'${T}',${JSON.stringify(action)})`);
    assert.equal(h.calls.some(x=>x.action==='agent_decide'||x.action==='agent_resume'),false);
    assert.ok(textOf(h.get('dialog-body')).includes('main.py'));
    const yes=descendants(h.get('dialog-body')).find(x=>x.textContent==='أوافق وأتابع');
    await yes.onclick();await tick();
    const decisions=h.calls.filter(x=>x.action==='agent_decide');
    assert.deepEqual(decisions,[{action:'agent_decide',project:A,session:SA,action_id:'action-1',call_digest:action.call_digest,expected_revision:3,approve:true}]);
    assert.equal(h.calls.filter(x=>x.action==='agent_resume').length,1);
    await yes.onclick();assert.equal(h.calls.filter(x=>x.action==='agent_resume').length,1);
  },
  async stale_agent_approval_cannot_apply_to_another_project() {
    const h=await harness();h.run("setMode('agent')");
    h.run(`reviewAgentAction(context(),'${T}',{action_id:'a',call_digest:'f',revision:1,name:'write_file',arguments:{}})`);
    const yes=descendants(h.get('dialog-body')).find(x=>x.textContent==='أوافق وأتابع');
    await h.run(`chooseProject('${B}')`);await yes.onclick();
    assert.equal(h.calls.some(x=>x.action==='agent_decide'),false);
  },
  async rejected_binding_never_resumes_agent() {
    const h=await harness({agent_decide:()=>({__httpStatus:409,body:{error_code:'action_binding_conflict'}})});
    h.run(`reviewAgentAction(context(),'${T}',{action_id:'a',call_digest:'f',revision:1,name:'run_command',arguments:{}})`);
    await descendants(h.get('dialog-body')).find(x=>x.textContent==='أوافق وأتابع').onclick();
    assert.equal(h.calls.some(x=>x.action==='agent_resume'),false);
    assert.equal(h.get('dialog').open,true);assert.equal(h.get('notice').className,'error');
  },
  async agent_recovery_reads_history_without_resubmitting_or_resuming() {
    const pending={...turn,status:'awaiting_owner',pending:[{state:'prepared',name:'run_command'}],steps:[]};
    const h=await harness({history:()=>({status:'idle',turns:[pending],before:0,total:1})},{storage:{[`diwan.pending.${A}.${SA}`]:T}});
    h.run("setMode('agent');syncPending()");await h.run('refresh()');
    assert.equal(h.storage.has(`diwan.pending.${A}.${SA}`),false);
    assert.equal(h.get('send').disabled,true);
    await h.get('composer').onsubmit(event);
    assert.equal(h.calls.some(x=>['agent_resume','agent_ask','ask','replay'].includes(x.action)),false);
    assert.ok(textOf(h.get('messages')).includes('مراجعة فعل run_command'));
  },
  async agent_send_uses_selected_files_and_agent_route() {
    const h=await harness({agent_ask:()=>turn});h.run("setMode('agent');state.selected.add('chosen.txt')");
    h.get('message').value='اكتب';await h.get('composer').onsubmit(event);
    const sent=h.calls.filter(x=>x.action==='agent_ask');assert.equal(sent.length,1);
    assert.deepEqual(sent[0].files,['chosen.txt']);assert.equal(h.calls.some(x=>x.action==='ask'),false);
  },
  async refused_agent_revert_preserves_error_and_reuses_request_identity() {
    const h=await harness({agent_revert:()=>({status:'refused',code:'changed_since_action'})});
    const open=`reviewAgentRevert(context(),{action_id:'action-1',name:'write_file',content:'saved'})`;
    h.run(open);await descendants(h.get('dialog-body')).find(x=>x.tagName==='BUTTON').onclick();
    assert.equal(textOf(h.get('dialog-body')).includes('إيصال الرجوع محفوظ'),false);
    assert.ok(textOf(h.get('dialog-body')).includes('حُفظ التعديل الأحدث'));
    h.get('close-dialog').onclick();h.run(open);await descendants(h.get('dialog-body')).find(x=>x.tagName==='BUTTON').onclick();
    const calls=h.calls.filter(x=>x.action==='agent_revert');assert.equal(calls.length,2);
    assert.equal(calls[0].request,calls[1].request,'An explicit retry must retain the saved revert identity');
  },
  async already_reverted_receipt_is_not_a_new_write_claim() {
    const h=await harness({agent_revert:()=>({status:'already_reverted'})});
    h.run(`reviewAgentRevert(context(),{action_id:'action-1',name:'write_file',content:'saved'})`);
    await descendants(h.get('dialog-body')).find(x=>x.tagName==='BUTTON').onclick();
    assert.ok(textOf(h.get('dialog-body')).includes('إيصال الرجوع محفوظ'));
    assert.ok(textOf(h.get('dialog-body')).includes('قد يحمل الملف تعديلات لاحقة'));
  },
  async agent_file_markup_is_plain_text_and_blob_is_revoked() {
    const content='<script>steal()</script>',h=await harness({agent_read:()=>({path:'out.txt',content})});
    await h.run(`showAgentFile('${A}','out.txt')`);
    assert.ok(textOf(h.get('dialog-body')).includes(content));assert.equal(h.createdURLs.length,1);
    h.get('close-dialog').onclick();assert.deepEqual(h.revokedURLs,['blob:fixture-0']);
  },
  async stale_agent_file_never_opens_or_creates_blob() {
    const pending=deferred(),h=await harness({agent_read:()=>pending.promise});
    const reading=h.run(`showAgentFile('${A}','out.txt')`);await tick();await h.run(`chooseProject('${B}')`);
    pending.resolve({path:'out.txt',content:'private A'});await reading;
    assert.equal(h.get('dialog').open,false);assert.equal(h.createdURLs.length,0);
  },
  async agent_file_list_error_is_visible() {
    const h=await harness({agent_files:()=>({__httpStatus:409,body:{error_code:'unsafe_path'}})});
    h.run("setMode('agent')");await h.get('agent-files').onclick();assert.equal(h.get('notice').className,'error');
  },
  async media_runtime_mismatch_has_specific_arabic_explanation() {
    const h=await harness();
    h.run("showError({code:'local_media_runtime_unsupported'})");
    assert.ok(h.get('notice').textContent.includes('نسخة التشغيل المحلي تغيرت'));
    assert.ok(h.get('notice').textContent.includes('فحص توافقها'));
  },
  async apply_error_never_announces_creation() {
    const h=await harness({review:()=>({path:'exists.txt',sha256:'f'.repeat(64),content:'draft',status:'proposed'}),
      apply:()=>({status:'error',error_code:'target_exists',path:'exists.txt'})});
    await h.run(`review('${A}','proposal')`);
    const button=descendants(h.get('dialog-body')).find(x=>x.tagName==='BUTTON');
    await button.onclick();await tick();
    assert.equal(textOf(h.get('dialog-body')).includes('تم إنشاء الملف:'),false,'UI falsely announced creation after status:error');
    assert.equal(h.get('notice').className,'error','UI should expose target_exists as an error');
  },
  async http_apply_error_remains_visible() {
    const h=await harness({review:()=>({path:'exists.txt',sha256:'f'.repeat(64),content:'draft',status:'proposed'}),
      apply:()=>({__httpStatus:409,body:{error_code:'target_exists'}})});
    await h.run(`review('${A}','proposal')`);
    await descendants(h.get('dialog-body')).find(x=>x.tagName==='BUTTON').onclick();await tick();
    assert.equal(h.get('dialog').open,true,'HTTP409 must keep the review dialog available');
    assert.equal(textOf(h.get('dialog-body')).includes('تم إنشاء الملف:'),false);
    assert.equal(h.get('notice').className,'error');
    assert.ok(textOf(h.get('dialog-body')).includes('يوجد ملف بهذا الاسم'));
  },
  async newer_draft_survives_earlier_answer() {
    const asked=deferred();
    const h=await harness({ask:()=>asked.promise,history:()=>({status:'idle',turns:[turn],before:0,total:1})});
    h.get('message').value='sent request';const submit=h.get('composer').onsubmit(event);await tick();
    h.get('message').value='new unsent draft';asked.resolve({...turn});await submit;
    assert.equal(h.get('message').value,'new unsent draft','Completed earlier request erased the newer draft');
  },
  async stale_preferences_do_not_open_in_new_project() {
    const pending=deferred(),h=await harness({preferences:()=>pending.promise});
    const loading=h.get('preferences').onclick();await tick();await h.run(`chooseProject('${B}')`);
    pending.resolve({revision:1,sha256:'f'.repeat(64),values:{response_language:'ar'}});await loading;
    assert.equal(h.get('dialog').open,false,'Preferences from project A opened over project B');
  },
  async stale_inspection_does_not_open_in_new_project() {
    const pending=deferred(),h=await harness({inspect:()=>pending.promise});
    const loading=h.run(`inspect({project:'${A}',session:'${SA}'},'${T}')`);await tick();await h.run(`chooseProject('${B}')`);
    pending.resolve({attachments:[{relative_path:'a.txt',size_bytes:1,sha256:'f'.repeat(64),content:'A secret'}],preferences:null});await loading;
    assert.equal(h.get('dialog').open,false,'Frozen inputs from A opened over project B');
  },
  async stale_review_does_not_open_in_new_project() {
    const pending=deferred(),h=await harness({review:()=>pending.promise});
    const loading=h.run(`review('${A}','proposal')`);await tick();await h.run(`chooseProject('${B}')`);
    pending.resolve({path:'a.txt',sha256:'f'.repeat(64),content:'A draft',status:'proposed'});await loading;
    assert.equal(h.get('dialog').open,false,'A write approval dialog appeared over project B');
  },
  async late_session_creation_does_not_switch_wrong_project() {
    const pending=deferred(),h=await harness({create_session:()=>pending.promise});
    h.get('session-name').value='new A session';const loading=h.get('new-session').onsubmit(event);await tick();
    await h.run(`chooseProject('${B}')`);pending.resolve({id:SA,name:'new A session'});await loading;
    assert.equal(h.run('state.project'),B);
    assert.equal(h.run('state.session'),'','Session from project A was selected under project B');
    assert.equal(h.calls.some(r=>r.action==='history'&&r.project===B&&r.session===SA),false);
  },
  async stale_preference_save_does_not_close_new_dialog() {
    const pending=deferred(),h=await harness({preferences:req=>({revision:1,sha256:'f'.repeat(64),values:{response_language:req.project===A?'ar':'en'}}),set_preference:()=>pending.promise});
    await h.get('preferences').onclick();const form=descendants(h.get('dialog-body')).find(x=>x.tagName==='FORM');
    const saving=form.onsubmit(event);await tick();h.get('close-dialog').onclick();await h.run(`chooseProject('${B}')`);await h.get('preferences').onclick();
    assert.equal(h.get('dialog').open,true);pending.resolve({revision:2,values:{response_language:'ar'}});await saving;
    assert.equal(h.get('dialog').open,true,'Completed A preference save closed the new B dialog');
  },
  async stale_preference_delete_does_not_close_new_dialog() {
    const pending=deferred(),h=await harness({preferences:req=>({revision:1,sha256:'f'.repeat(64),values:{response_language:req.project===A?'ar':'en'}}),delete_preference:()=>pending.promise});
    await h.get('preferences').onclick();const remove=descendants(h.get('dialog-body')).find(x=>x.tagName==='BUTTON'&&x.textContent==='حذف التفضيل');
    const deleting=remove.onclick();await tick();h.get('close-dialog').onclick();await h.run(`chooseProject('${B}')`);await h.get('preferences').onclick();
    pending.resolve({revision:2,values:{}});await deleting;await tick();
    assert.equal(h.get('dialog').open,true,'Completed A preference delete closed the new B dialog');
  },
  async late_history_is_ignored_after_project_switch() {
    const pending=deferred(),h=await harness({history:()=>pending.promise});
    const loading=h.run('refresh()');await tick();await h.run(`chooseProject('${B}')`);
    pending.resolve({status:'idle',turns:[turn],before:0,total:1});await loading;
    assert.equal(h.run('state.turns.length'),0);
  },
  async double_submit_calls_provider_route_once() {
    const pending=deferred(),h=await harness({ask:()=>pending.promise});
    h.get('message').value='request';const one=h.get('composer').onsubmit(event);await tick();
    await h.get('composer').onsubmit(event);
    assert.equal(h.calls.filter(x=>x.action==='ask').length,1);
    assert.equal(h.storage.has(`diwan.pending.${A}.${SA}`),true);
    pending.resolve(turn);await one;
  },
  async interrupted_request_recovers_without_resubmission() {
    let submitted;
    const h=await harness({ask:request=>{submitted=request;throw new Error('simulated lost response');},
      history:()=>({status:'idle',turns:[{...turn,turn_id:submitted.turn}],before:0,total:1}),
      replay:request=>({...turn,turn_id:request.turn,replayed:true})});
    h.get('message').value='request with lost response';await h.get('composer').onsubmit(event);
    assert.equal(h.storage.get(`diwan.pending.${A}.${SA}`),submitted.turn);
    assert.equal(h.get('send').disabled,true,'Uncertain request must prevent accidental resubmission');
    assert.equal(h.get('notice').className,'error');
    await h.get('composer').onsubmit(event);
    assert.equal(h.calls.filter(x=>x.action==='ask').length,1);
    await h.run('refresh()');
    assert.equal(h.calls.filter(x=>x.action==='ask').length,1,'Recovery sent another generation request');
    assert.equal(h.calls.filter(x=>x.action==='replay').length,1);
    assert.equal(h.storage.has(`diwan.pending.${A}.${SA}`),false);
    assert.equal(h.get('send').disabled,false);
    assert.equal(h.run('state.turns[0].turn_id'),submitted.turn);
  },
  async media_submission_keeps_exact_selected_bytes_and_mode() {
    const h=await harness({ask_media:()=>turn});h.run(`setMode('media')`);
    h.get('media-file').files=[{name:'fixture.png',size:3,arrayBuffer:async()=>new Uint8Array([0,128,255]).buffer}];
    h.get('message').value='describe';await h.get('composer').onsubmit(event);
    const sent=h.calls.filter(x=>x.action==='ask_media');
    assert.equal(sent.length,1);assert.equal(h.calls.some(x=>x.action==='ask'),false);
    assert.deepEqual(sent[0].media,[{name:'fixture.png',data_base64:'AID/'}]);
    assert.equal(h.get('media-file').files.length,0);
  },
  async media_read_cannot_send_after_project_switch() {
    const pending=deferred(),h=await harness();h.run(`setMode('media')`);
    h.get('media-file').files=[{name:'fixture.png',size:1,arrayBuffer:()=>pending.promise}];
    h.get('message').value='old project request';const sending=h.get('composer').onsubmit(event);await tick();
    await h.run(`chooseProject('${B}')`);pending.resolve(new Uint8Array([1]).buffer);await sending;
    assert.equal(h.calls.some(x=>x.action==='ask_media'||x.action==='ask'),false);
    assert.equal(h.storage.has(`diwan.pending.${A}.${SA}`),false);
    assert.equal(h.run('state.busy'),false);
  },
  async media_replacement_survives_earlier_answer() {
    const pending=deferred(),h=await harness({ask_media:()=>pending.promise});h.run(`setMode('media')`);
    const first={name:'first.png',size:1,arrayBuffer:async()=>new Uint8Array([1]).buffer};
    const second={name:'second.png',size:1,arrayBuffer:async()=>new Uint8Array([2]).buffer};
    h.get('media-file').files=[first];h.get('message').value='first';
    const sending=h.get('composer').onsubmit(event);await tick();h.get('media-file').files=[second];
    h.get('message').value='new draft';pending.resolve(turn);await sending;
    assert.equal(h.get('media-file').files[0],second);
    assert.equal(h.get('message').value,'new draft');
  },
  async media_oversize_does_not_read_or_record_pending() {
    const h=await harness();h.run(`setMode('media')`);let reads=0;
    h.get('media-file').files=[{name:'large.wav',size:262145,arrayBuffer:async()=>{reads++;return new ArrayBuffer(0);}}];
    h.get('message').value='oversize';await h.get('composer').onsubmit(event);
    assert.equal(reads,0);assert.equal(h.calls.some(x=>x.action==='ask_media'),false);
    assert.equal(h.storage.has(`diwan.pending.${A}.${SA}`),false);
    assert.equal(h.get('send').disabled,false);assert.equal(h.get('notice').className,'error');
  },
  async missing_media_turn_requires_explicit_pending_release() {
    const h=await harness({replay:()=>({__httpStatus:409,body:{error_code:'media_turn_missing'}})});
    h.run(`setMode('media')`);h.storage.set(`diwan.pending.${A}.${SA}`,T);h.run('syncPending()');
    await h.run('refresh()');
    assert.equal(h.get('release-pending').hidden,false);assert.equal(h.get('send').disabled,true);
    assert.equal(h.storage.get(`diwan.pending.${A}.${SA}`),T);
    h.get('release-pending').onclick();
    assert.equal(h.storage.has(`diwan.pending.${A}.${SA}`),false);assert.equal(h.get('send').disabled,false);
    assert.equal(h.calls.some(x=>x.action==='ask_media'||x.action==='ask'),false);
  },
  async boot_uses_persisted_session_mode_from_server() {
    const h=await harness({projects:()=>({projects:[{id:A,name:'A'}],media_enabled:true,agent_enabled:true,default_session_mode:'agent'}),
      sessions:()=>({sessions:[{id:SA,name:'media session',mode:'media'}]})},
      {preset:false,storage:{'diwan.last':JSON.stringify({project:A,session:SA,name:'stale title',mode:'text'})}});
    await tick();assert.equal(h.run('state.mode'),'media');assert.equal(h.run('state.session'),SA);assert.equal(h.get('session-mode').value,'agent');
    assert.equal(h.get('message').maxLength,4000);
    assert.equal(h.get('text-inputs').hidden,true);assert.equal(h.get('media-inputs').hidden,false);
    assert.equal(h.get('media-option').disabled,false);
  },
  async frozen_media_preview_is_bounded_to_blobs_and_revoked_on_close() {
    const media=[{name:'image.png',kind:'image',mime:'image/png',size_bytes:3,sha256:'f'.repeat(64),data_base64:'AID/'},
      {name:'audio.wav',kind:'audio',mime:'audio/wav',size_bytes:1,sha256:'e'.repeat(64),data_base64:'AQ=='}];
    const h=await harness({inspect:()=>({media,preferences:null})});
    await h.run(`inspect({project:'${A}',session:'${SA}'},'${T}')`);
    assert.equal(h.createdURLs.length,2);
    const all=descendants(h.get('dialog-body')),img=all.find(x=>x.tagName==='IMG'),audio=all.find(x=>x.tagName==='AUDIO');
    assert.equal(img.src,'blob:fixture-0');assert.equal(img.alt,'image.png');
    assert.equal(audio.src,'blob:fixture-1');assert.equal(audio.controls,true);assert.equal(audio.preload,'none');
    assert.equal(audio.autoplay,undefined);
    assert.equal(h.createdURLs[0].blob.type,'image/png');assert.equal(h.createdURLs[0].blob.size,3);
    h.get('close-dialog').onclick();
    assert.deepEqual(h.revokedURLs,['blob:fixture-0','blob:fixture-1']);assert.equal(h.run('state.urls.length'),0);
  },
  async stale_media_inspect_creates_no_object_url() {
    const pending=deferred(),h=await harness({inspect:()=>pending.promise});
    const loading=h.run(`inspect({project:'${A}',session:'${SA}'},'${T}')`);await tick();
    await h.run(`chooseProject('${B}')`);
    pending.resolve({media:[{name:'a.png',kind:'image',mime:'image/png',size_bytes:1,sha256:'f'.repeat(64),data_base64:'AQ=='}],preferences:null});await loading;
    assert.equal(h.createdURLs.length,0);assert.equal(h.get('dialog').open,false);
  },
  async media_preview_url_is_revoked_on_escape_and_navigation() {
    const h=await harness({inspect:()=>({media:[{name:'a.png',kind:'image',mime:'image/png',size_bytes:1,sha256:'f'.repeat(64),data_base64:'AQ=='}],preferences:null})});
    await h.run(`inspect({project:'${A}',session:'${SA}'},'${T}')`);
    for(const handler of h.get('dialog').listeners.cancel) handler({});
    assert.deepEqual(h.revokedURLs,['blob:fixture-0']);
    await h.run(`inspect({project:'${A}',session:'${SA}'},'${T}')`);
    await h.run(`chooseProject('${B}')`);
    assert.deepEqual(h.revokedURLs,['blob:fixture-0','blob:fixture-1']);
  },
  async answer_markup_is_plain_text() {
    const h=await harness();
    h.run(`state.turns=[{turn_id:'${T}',user_request:'<script>attack()</script>',content:'<img onerror=attack()>',status:'complete',usage:{input_tokens:1,output_tokens:1}}];render()`);
    assert.equal(descendants(h.get('messages')).some(x=>x.tagName==='IMG'||x.tagName==='SCRIPT'),false);
    assert.ok(textOf(h.get('messages')).includes('<img onerror=attack()>'));
  },
};

(async()=>{
  const output={scope:'node_fake_dom_behavior_only',browser_rendering:'not_tested',checks:[]};
  for(const [name,test] of Object.entries(cases)) {
    try {await test();output.checks.push({name,passed:true});}
    catch(error) {output.checks.push({name,passed:false,reason:error.message});}
  }
  output.passed=output.checks.filter(x=>x.passed).length;output.total=output.checks.length;
  process.stdout.write(JSON.stringify(output,null,2)+'\n');process.exitCode=output.passed===output.total?0:1;
})();
