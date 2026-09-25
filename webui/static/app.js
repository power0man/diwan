"use strict";
const $ = id => document.getElementById(id);
const state = {project: "", session: "", mode: "text", defaultSessionMode: "text", sessionModeChosen: false, mediaEnabled: false, urls: [], busy: false, stopBusy: false, runningTurn: null, epoch: 0, turns: [], selected: new Set(), before: 0, pending: null, poll: 0, dialogEpoch: 0};
const token = document.querySelector('meta[name="diwan-token"]').content;
const uuid = () => crypto.randomUUID().replaceAll("-", "");
const errors = {
  agent_unavailable: "مسار الأدوات غير مهيأ في هذا التشغيل.",
  action_binding_conflict: "تغير الفعل أو مدخلاته منذ عرضه؛ لم تُنقل الموافقة إلى نسخة مختلفة.",
  action_revision_conflict: "سبق اتخاذ قرار لهذا الفعل. استرجع الحالة الحالية.",
  outcome_unknown: "وقع انقطاع ولا يوجد دليل كافٍ على نتيجة الأثر. لن يُعاد تلقائيًا.",
  action_store_required: "مخزن إيصالات الأفعال غير مهيأ؛ لم تُنفذ الأداة.",
  changed_since_action: "تغير الملف بعد فعل ديوان. حُفظ التعديل الأحدث ورُفض الرجوع.",
  turn_unresolved: "توجد جولة معلقة أو مجهولة النتيجة. راجع قرارها؛ إذا بقي الأثر مجهولًا فابدأ محادثة جديدة.",
  agent_capabilities_changed_new_session: "تغيرت الأدوات المتاحة. تستطيع قراءة هذه المحادثة؛ ابدأ محادثة جديدة للمتابعة.",
  agent_turn_pending: "أكمل قرار الجولة المعلقة قبل بدء طلب جديد.",
  stop_not_ready: "لم تُحفظ الجولة بعد. انتظر قليلًا ثم اطلب الإيقاف مجددًا؛ لم يُسجّل طلب الإيقاف.",
  stop_requested: "طُلب إيقاف المتابعة. قد تكتمل الخطوة الجارية؛ لا يُلغى أثرها تلقائيًا.",
  turn_cancelled: "أُوقفت متابعة هذه الجولة. تبقى آثار الخطوات المكتملة محفوظة.",
  execution_backend_unavailable: "تنفيذ الأوامر غير مهيأ. أدوات الملفات المتاحة تبقى ضمن المشروع.",
  media_unavailable: "مورد الصور والصوت غير مهيأ في هذا التشغيل.",
  media_too_large: "اختر ملفًا لا يتجاوز 256 كيلوبايت.",
  png_invalid: "الصورة خارج الصيغة المدعومة. استخدم PNG ملونًا غير متداخل، حتى مليون بكسل وضلع 1024.",
  wav_invalid: "استخدم WAV أحادي PCM، 16 بت و16 كيلوهرتز، حتى 8 ثوان.",
  media_type_unsupported: "الصيغ المتاحة الآن PNG وWAV فقط.",
  media_session_limit: "بلغ حفظ الوسائط حد الجلسة. ابدأ محادثة جديدة.",
  local_media_context_limit: "بلغت الوسائط حد السياق. ابدأ محادثة جديدة.",
  local_media_text_limit: "بلغ النص حد سياق الوسائط. ابدأ محادثة جديدة.",
  local_media_runtime_unsupported: "نسخة التشغيل المحلي تغيرت؛ يلزم فحص توافقها قبل إرسال وسائط جديدة.",
  session_busy: "الجلسة قيد التنفيذ. استرجع حالتها بعد قليل.",
  generation_busy: "هناك جواب قيد التنفيذ. انتظر اكتماله ثم أرسل طلبك.",
  context_limit: "بلغت المحادثة حد السياق. ابدأ محادثة جديدة؛ يبقى تاريخك محفوظًا.",
  outcome_uncertain: "انقطع تنفيذ الجولة ونتيجتها غير مؤكدة. لم تُعَد تلقائيًا.",
  turn_conflict: "معرف الجولة مرتبط بطلب آخر. استرجع الحالة قبل إنشاء جولة جديدة.",
  http_refused: "انتهت جلسة اتصال الواجهة أو رُفض مصدر الطلب. أعد تحميل الصفحة.",
  network_error: "انقطع الاتصال. استرجع الحالة قبل إرسال طلب آخر؛ قد يكون الجواب محفوظًا.",
  model_changed_new_session: "تغير إعداد المزود. تستطيع قراءة هذه المحادثة؛ ابدأ محادثة جديدة للمتابعة.",
  file_too_large: "حجم الملف أكبر من 64 كيلوبايت. اختر مقتطفًا أصغر.",
  text_invalid: "الملف ليس نص UTF-8 صالحًا أو يحتوي بيانات ثنائية.",
  preference_revision_conflict: "تغيرت التفضيلات من نافذة أخرى. افتحها مجددًا وراجع النسخة الحالية.",
  attachment_unavailable: "أحد المرفقات غير مكتمل أو تغير بعد رفعه. ارفع نسخة جديدة واخترها.",
  target_exists: "يوجد ملف بهذا الاسم. اختر اسمًا جديدًا؛ لن يُستبدل الملف الحالي.",
  approval_mismatch: "البصمة لا تطابق المسودة المعروضة. افتح المسودة وراجعها مجددًا.",
  applied_output_changed: "تغير الملف بعد إنشائه. لن يُعاد استبداله.",
};
function notice(text, error = false) {$("notice").textContent = text; $("notice").className = error ? "error" : "";}
function showError(error) {
  const text = errors[error.code] || `تعذر إكمال العملية (${error.code || "request_failed"}). استرجع الحالة وراجع المدخلات.`;
  notice(text, true);
  if($("dialog").open) {let feedback = $("dialog-feedback"); if(!feedback) {feedback = element("p"); feedback.id = "dialog-feedback"; feedback.setAttribute("role", "alert"); $("dialog-body").append(feedback);} feedback.textContent = text;}
}
async function api(action, values = {}) {
  let response;
  try {response = await fetch("/api", {method: "POST", headers: {"Content-Type": "application/json", "X-Diwan-CSRF": token}, body: JSON.stringify({action, ...values})});}
  catch {throw {code: "network_error"};}
  let data; try {data = await response.json();} catch {throw {code: "network_error"};}
  if (!response.ok || (["apply", "upload"].includes(action) && data.status === "error")) throw {code: data.error_code};
  return data;
}
function context() {return {project: state.project, session: state.session};}
function pendingKey(ctx = context()) {return `diwan.pending.${ctx.project}.${ctx.session}`;}
function stoppableTurn() {
  return state.mode === "agent" ? state.runningTurn || state.pending || state.turns.find(t => t.status === "awaiting_owner")?.turn_id : null;
}
function syncPending() {
  state.pending = sessionStorage.getItem(pendingKey());
  $("recovery").hidden = !state.pending; $("send").disabled = state.busy || !!state.pending || !!state.runningTurn || (state.mode === "agent" && state.turns.some(t => ["awaiting_owner","outcome_unknown"].includes(t.status)));
  $("release-pending").hidden = true;
  $("agent-stop").hidden = !stoppableTurn(); $("agent-stop").disabled = state.stopBusy;
}
function element(tag, text, className) {const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if(className) el.className = className; return el;}
function button(text, action) {const el = element("button", text); el.type = "button"; el.onclick = () => Promise.resolve().then(action).catch(showError); return el;}
function render() {
  const box = $("messages"); box.replaceChildren();
  if (!state.turns.length) {box.append(element("p", "اكتب رسالتك لبدء المحادثة.", "empty")); return;}
  for (const turn of state.turns) {
    const user = element("article", undefined, "message user"); user.append(element("strong", "أنت"), element("div", turn.user_request, "content"));
    const answer = element("article", undefined, "message"); answer.append(element("strong", "ديوان · جواب غير متحقق"), element("div", turn.content || (turn.status === "awaiting_owner" ? "طلب ديوان تنفيذ الفعل المبين أدناه." : errors[turn.error_code] || `تعذر توليد الجواب (${turn.error_code || turn.status})`), "content"));
    const statusLabel = {complete:"مكتمل",truncated:"جواب مبتور — لم يكتمل",awaiting_owner:"ينتظر قرارك في فعل محدد",outcome_unknown:"نتيجة الأثر غير مؤكدة",step_limit:"بلغ حد الخطوات",timed_out:"انتهت المهلة",cancelled:"توقفت المتابعة — تبقى آثار الخطوات المكتملة"}[turn.status] || "تعذر التنفيذ";
    answer.append(element("div", `${statusLabel}${turn.usage ? ` · ${turn.usage.input_tokens + turn.usage.output_tokens} وحدة نصية` : ""}`, "meta"));
    const ctx = context(), actions = element("div", undefined, "tools");
    if(state.mode === "agent") renderAgentActions(answer, actions, ctx, turn);
    else {
      actions.append(button("المدخلات المستخدمة", () => inspect(ctx, turn.turn_id)));
      if (turn.status === "complete" && turn.content.trim()) actions.append(button("مراجعة وحفظ مسودة", () => propose(ctx, turn.turn_id)));
    }
    answer.append(actions);
    box.append(user, answer);
  }
}
function renderAgentActions(answer, actions, ctx, turn) {
  if(turn.inputs) {
    const inputs = element("details"); inputs.append(element("summary", "سياق الطلب المحفوظ"));
    inputs.append(element("p", "هذه نسخة المدخلات وقت إرسال الطلب؛ تعديل التفضيلات لاحقًا لا يغيرها."));
    const snapshot = turn.inputs.preferences;
    inputs.append(element("p", snapshot ? `نسخة التفضيلات: ${snapshot.revision}` : "لم تُرفق تفضيلات بهذا الطلب."));
    if(snapshot) {inputs.append(element("pre", JSON.stringify(snapshot.values, null, 2)), element("p", snapshot.sha256, "hash"));}
    if(turn.inputs.attachments?.length) inputs.append(element("pre", JSON.stringify(turn.inputs.attachments, null, 2)));
    answer.append(inputs);
  }
  for(const step of turn.steps || []) {
    const details = element("details"); details.append(element("summary", `الخطوة ${step.index + 1}`));
    for(const result of step.tool_results || []) {
      details.append(element("p", `${result.name} · ${result.status}${result.code ? ` · ${result.code}` : ""}`));
      if(result.content) details.append(element("pre", result.content));
      if(result.status === "ok" && result.action_id && result.journal_action_id) {
        details.append(button("الرجوع عن هذا التعديل", () => reviewAgentRevert(ctx, result)));
      }
    }
    answer.append(details);
  }
  const pending = turn.pending || [];
  for(const action of turn.status === "awaiting_owner" ? pending : []) {
    if(action.state === "prepared" || action.status === "awaiting_owner") actions.append(button(`مراجعة فعل ${action.name}`, () => reviewAgentAction(ctx, turn.turn_id, action)));
  }
  if(turn.status === "awaiting_owner" && !pending.some(a => a.state === "prepared" || a.status === "awaiting_owner")) {
    actions.append(button("متابعة الجولة بالقرار المحفوظ", () => resumeAgent(ctx, turn.turn_id)));
  }
  if(turn.status === "outcome_unknown") answer.append(element("p", errors.outcome_unknown, "pending"));
}
$("agent-stop").onclick = async () => {
  const turn = stoppableTurn(), ctx = context(), epoch = state.epoch;
  if(!turn || state.stopBusy) return;
  state.stopBusy = true; syncPending();
  try {
    const stopped = await api("agent_stop", {...ctx, turn});
    if(epoch !== state.epoch) return;
    if(stopped.status === "stop_requested") notice(errors.stop_requested);
    else if(stopped.status === "cancelled") {dismissDialog(); await refresh(); if(epoch === state.epoch) notice(errors.turn_cancelled);}
    else if(stopped.status === "not_running") {await refresh(); if(epoch === state.epoch) notice("الجولة منتهية بالفعل؛ لم يتغير أثرها.");}
    else if(stopped.status === "outcome_unknown") {await refresh(); if(epoch === state.epoch) showError({code:"outcome_unknown"});}
    else throw {code:stopped.error_code || "request_failed"};
  } catch(error) {if(epoch === state.epoch) showError(error);}
  finally {state.stopBusy = false; syncPending();}
};
async function resumeAgent(ctx, turn) {
  if(state.busy || ctx.project !== state.project || ctx.session !== state.session) return;
  const epoch = state.epoch; state.busy = true;
  try {
    sessionStorage.setItem(pendingKey(ctx), turn); syncPending(); notice("تُستأنف الجولة من الإيصالات المحفوظة.");
    await api("agent_resume", {...ctx, turn});
    if(epoch === state.epoch) await refresh();
  } finally {state.busy = false; syncPending();}
}
function reviewAgentAction(ctx, turn, action) {
  if(ctx.project !== state.project || ctx.session !== state.session) return;
  const epoch = state.epoch, ticket = ++state.dialogEpoch, body = dialog("قرار لفعل محدد");
  body.append(element("p", action.name), element("pre", JSON.stringify(action.arguments || {}, null, 2)));
  body.append(element("p", "الموافقة تخص هذا الفعل ومدخلاته المثبتة وحدها. لا تمنح إذنًا لأفعال لاحقة."));
  if(action.input_snapshot_sha256) body.append(element("p", `بصمة نسخة المدخلات: ${action.input_snapshot_sha256}`, "hash"));
  if(action.input_files?.length) body.append(element("pre", JSON.stringify(action.input_files, null, 2)));
  if(action.input_files_truncated) body.append(element("p", `تضم النسخة ${action.input_files_count} ملفًا؛ المعروض أول 64 ملفًا فقط.`));
  const buttons = [];
  for(const [approve, title] of [[true,"أوافق وأتابع"],[false,"أرفض وأتابع"]]) {
    const choice = button(title, async () => {
      if(!currentDialog(epoch,ticket) || state.busy) return;
      state.busy = true; buttons.forEach(b => b.disabled = true); syncPending();
      try {
        await api("agent_decide", {...ctx, action_id:action.action_id, call_digest:action.call_digest,
          expected_revision:action.revision, approve});
        if(!currentDialog(epoch,ticket)) return;
        dismissDialog(); state.busy = false; await resumeAgent(ctx, turn);
      } catch(error) {if(epoch === state.epoch) throw error;}
      finally {state.busy = false; syncPending(); if(currentDialog(epoch,ticket)) buttons.forEach(b => b.disabled = false);}
    }); buttons.push(choice); body.append(choice);
  }
}
function reviewAgentRevert(ctx, result) {
  if(ctx.project !== state.project || ctx.session !== state.session) return;
  const epoch = state.epoch, ticket = ++state.dialogEpoch, body = dialog("الرجوع عن تعديل ديوان");
  body.append(element("p", "تُستعاد النسخة السابقة فقط إن لم يتغير الملف بعد هذا الفعل. يبقى تعديلك الأحدث محفوظًا."));
  body.append(element("p", result.name), element("pre", result.content || ""));
  const restore = button("استعادة النسخة السابقة", async () => {
    if(!currentDialog(epoch,ticket) || state.busy) return;
    state.busy = true; restore.disabled = true; syncPending();
    try {
      const key = `diwan.revert.${ctx.project}.${ctx.session}.${result.action_id}`;
      let request = sessionStorage.getItem(key);
      if(!request) {request = uuid(); sessionStorage.setItem(key,request);}
      const reverted = await api("agent_revert", {...ctx, action_id:result.action_id, request});
      if(!["ok","reverted","already_reverted"].includes(reverted.status)) throw {code:reverted.error_code || reverted.code || "outcome_unknown"};
      if(currentDialog(epoch,ticket)) {body.append(element("p", "إيصال الرجوع محفوظ. يعرض سجل الطلب نتيجته؛ قد يحمل الملف تعديلات لاحقة.")); restore.remove(); await refresh();}
    } catch(error) {if(currentDialog(epoch,ticket)) throw error;}
    finally {state.busy = false; syncPending();}
  }); body.append(restore);
}
$("agent-files").onclick = async () => {
  const project = state.project, epoch = state.epoch, ticket = ++state.dialogEpoch;
  if(!project || state.mode !== "agent") return;
  try {
    const data = await api("agent_files", {project});
    if(!currentDialog(epoch,ticket)) return;
    const body = dialog("ملفات عمل المشروع");
    if(!data.files.length) body.append(element("p", "لا توجد ملفات عمل حاليًا."));
    for(const file of data.files) body.append(button(file.path, () => showAgentFile(project,file.path)));
  } catch(error) {if(currentDialog(epoch,ticket)) showError(error);}
};
async function showAgentFile(project, path) {
  const epoch = state.epoch, ticket = ++state.dialogEpoch;
  const data = await api("agent_read", {project,path});
  if(!currentDialog(epoch,ticket)) return;
  if(typeof data.content !== "string") throw {code:"file_not_text"};
  const body = dialog(data.path || path); body.append(element("pre",data.content));
  const url = URL.createObjectURL(new Blob([data.content],{type:"text/plain;charset=utf-8"})); state.urls.push(url);
  const download = element("a","تنزيل هذه النسخة"); download.href = url; download.download = path.split("/").pop(); body.append(download);
}
async function projects() {
  const data = await api("projects"); const select = $("projects"); select.replaceChildren(new Option("اختر مشروعًا", ""));
  for (const project of data.projects) select.add(new Option(project.name, project.id));
  select.value = state.project;
  state.mediaEnabled = data.media_enabled === true; $("media-option").disabled = !state.mediaEnabled;
  const agentEnabled = data.agent_enabled === true; $("agent-option").disabled = !agentEnabled;
  state.defaultSessionMode = agentEnabled && data.default_session_mode === "agent" ? "agent" : "text";
  const mode = $("session-mode"), available = mode.value === "text" || (mode.value === "agent" && agentEnabled) || (mode.value === "media" && state.mediaEnabled);
  if(!state.sessionModeChosen || !available) {mode.value = state.defaultSessionMode; state.sessionModeChosen = false;}
}
async function chooseProject(id) {
  dismissDialog();
  const epoch = ++state.epoch; state.project = id; state.session = ""; state.runningTurn = null; setMode("text"); state.turns = []; render(); $("sessions").replaceChildren();
  state.selected.clear(); $("files").replaceChildren(); $("file-count").textContent = "(0)"; $("message").value = ""; syncPending(); $("older").hidden = true;
  $("project-title").textContent = $("projects").selectedOptions[0]?.textContent || "مساحتك الخاصة";
  $("session-title").textContent = "اختر محادثة أو أنشئ واحدة";
  if (!id) return;
  const data = await api("sessions", {project: id}); if (epoch !== state.epoch) return;
  for (const session of data.sessions) {
    const el = button(session.name, () => chooseSession(session.id, session.name, session.mode || "text")); el.dataset.session = session.id; el.dataset.mode = session.mode || "text"; $("sessions").append(el);
  }
  await loadFiles(id, epoch);
}
function setMode(mode) {
  state.mode = mode; $("text-inputs").hidden = mode === "media"; $("media-inputs").hidden = mode !== "media";
  $("media-file").value = ""; $("message").maxLength = mode === "media" ? 4000 : 24000;
  $("agent-controls").hidden = mode !== "agent";
}
$("clear-media").onclick = () => {$("media-file").value = "";};
async function chooseSession(id, name, mode = "text") {
  dismissDialog();
  ++state.epoch; state.session = id; state.runningTurn = null; setMode(mode); state.turns = []; $("session-title").textContent = name;
  state.selected.clear(); $("message").value = ""; syncPending();
  for (const el of $("files").querySelectorAll("input")) el.checked = false;
  $("file-count").textContent = "(0)";
  sessionStorage.setItem("diwan.last", JSON.stringify({...context(), name}));
  for (const el of $("sessions").children) el.setAttribute("aria-current", String(el.dataset.session === id));
  render(); state.poll = 0; await refresh();
  if(mode === "agent") {const epoch = state.epoch; const caps = await api("agent_capabilities", {project:state.project}); if(epoch === state.epoch) $("agent-capabilities").textContent = caps.execution_enabled ? "أدوات الملفات وتنفيذ الأوامر المضبوطة متاحة؛ يُعرض الإذن المطلوب لكل فعل." : "أدوات ملفات المشروع متاحة. تنفيذ الأوامر غير مهيأ.";}
}
async function refresh() {
  if (!state.session) {notice("اختر محادثة أولًا."); return;}
  const epoch = state.epoch, ctx = context(), data = await api("history", {...ctx, before: null}); if (epoch !== state.epoch) return;
  if (data.status === "running") {
    state.runningTurn = data.turn || null; syncPending();
    notice("ديوان يكتب الآن. تُسترجع الحالة دون إعادة إرسال الطلب.");
    if (++state.poll < 90) setTimeout(() => {if(epoch === state.epoch) refresh().catch(showError);}, 2000);
    return;
  }
  state.runningTurn = null; state.poll = 0; state.turns = data.turns; state.before = data.before; $("older").hidden = !data.before; render();
  syncPending();
  if (state.pending) {
    try {
      if(state.mode === "agent") {if(!data.turns.some(t => t.turn_id === state.pending)) throw {code:"workspace_turn_missing"};}
      else await api("replay", {...ctx, turn: state.pending});
      if(epoch !== state.epoch) return; sessionStorage.removeItem(pendingKey(ctx)); syncPending();}
    catch(e) {if(epoch !== state.epoch) return; if(["workspace_turn_missing", "media_turn_missing"].includes(e.code)) {$("release-pending").hidden = false; notice("لم يسجل الخادم هذه الجولة. يمكنك بدء طلب جديد صراحة.", true); return;} throw e;}
  }
  notice(`تم استرجاع المحادثة من جهازك. ${data.total} جولة محفوظة.`);
}
$("projects").onchange = () => chooseProject($("projects").value).catch(showError);
$("session-mode").onchange = () => {state.sessionModeChosen = true;};
$("refresh").onclick = () => refresh().catch(showError);
$("new-project").onsubmit = async event => {
  event.preventDefault(); const epoch = state.epoch, name = $("project-name").value;
  try {const p = await api("create_project", {name}); if(epoch !== state.epoch) return; state.project = p.id; await projects(); if(epoch !== state.epoch) return; await chooseProject(p.id); if($("project-name").value === name) $("project-name").value = "";} catch(e) {if(epoch === state.epoch) showError(e);}
};
$("new-session").onsubmit = async event => {
  event.preventDefault(); if (!state.project) {notice("أنشئ مشروعًا أولًا.", true); return;}
  const project = state.project, epoch = state.epoch, name = $("session-name").value;
  try {const s = await api("create_session", {project, name, mode:$("session-mode").value || state.defaultSessionMode}); if(epoch !== state.epoch) return; await chooseProject(project); if(state.epoch !== epoch + 1) return; await chooseSession(s.id, s.name, s.mode || "text"); if($("session-name").value === name) $("session-name").value = "";} catch(e) {if(state.project === project) showError(e);}
};
$("composer").onsubmit = async event => {
  event.preventDefault(); if (state.busy || state.pending || state.runningTurn || !state.session) {notice("اختر محادثة واسترجع حالة أي إرسال سابق أولًا.", true); return;}
  if(state.mode === "agent" && state.turns.some(t => ["awaiting_owner","outcome_unknown"].includes(t.status))) {showError({code:"turn_unresolved"}); return;}
  const epoch = state.epoch, ctx = context(), message = $("message").value, turn = uuid();
  const files = [...state.selected], mode = state.mode, selectedFile = $("media-file").files[0];
  state.busy = true; $("send").disabled = true;
  try {
    const media = [];
    if(mode === "media" && selectedFile) {
      if(selectedFile.size > 262144) throw {code:"media_too_large"};
      const bytes = new Uint8Array(await selectedFile.arrayBuffer());
      if(epoch !== state.epoch) return;
      if(bytes.length > 262144) throw {code:"media_too_large"};
      let binary = ""; for(const byte of bytes) binary += String.fromCharCode(byte);
      media.push({name:selectedFile.name, data_base64:btoa(binary)});
    }
    try {sessionStorage.setItem(pendingKey(ctx), turn);} catch {notice("تعذر حفظ معرف الجولة في المتصفح. فعّل تخزين الجلسة قبل الإرسال.", true); return;}
    syncPending(); notice("ديوان يكتب… يمكنك استرجاع الحالة إذا انقطع الاتصال.");
    await api(mode === "media" ? "ask_media" : mode === "agent" ? "agent_ask" : "ask", {...ctx, turn, message, ...(mode === "media" ? {media} : {files})});
    if(epoch === state.epoch) {
      if($("message").value === message) $("message").value = "";
      if($("media-file").files[0] === selectedFile) $("media-file").value = "";
      await refresh();
    }
  } catch(e) {if(epoch === state.epoch) showError(e);}
  finally {state.busy = false; syncPending();}
};
$("release-pending").onclick = () => {sessionStorage.removeItem(pendingKey()); syncPending(); notice("يمكنك مراجعة الرسالة وإرسال طلب جديد.");};
$("older").onclick = async () => {try {const epoch = state.epoch, data = await api("history", {...context(), before: state.before}); if(epoch !== state.epoch || data.status === "running") return; state.turns = [...data.turns, ...state.turns]; state.before = data.before; $("older").hidden = !data.before; render();} catch(e) {showError(e);}};
function clearPreviewURLs() {for(const url of state.urls) URL.revokeObjectURL(url); state.urls = [];}
function dialog(title) {clearPreviewURLs(); const body = $("dialog-body"); body.replaceChildren(element("h2", title)); if(!$("dialog").open) $("dialog").showModal(); return body;}
function dismissDialog() {clearPreviewURLs(); ++state.dialogEpoch; $("dialog").close();}
function currentDialog(epoch, ticket) {return state.epoch === epoch && state.dialogEpoch === ticket;}
$("close-dialog").onclick = dismissDialog;
$("dialog").addEventListener("cancel", () => {clearPreviewURLs(); ++state.dialogEpoch;});
async function loadFiles(project = state.project, epoch = state.epoch) {
  const data = await api("files", {project}); if(epoch !== state.epoch) return;
  $("files").replaceChildren();
  if(data.unavailable?.length) $("files").append(element("p", `${data.unavailable.length} ملف غير مكتمل أو تغير بعد الرفع. ارفع نسخة جديدة لاستخدامه.`, "pending"));
  for(const file of data.files) {
    const row = element("label"), input = element("input"); input.type = "checkbox"; input.checked = state.selected.has(file.path);
    input.onchange = () => {if(input.checked && state.selected.size >= 4) {input.checked = false; notice("أربعة ملفات كحد أقصى لكل طلب.", true); return;} if(input.checked) state.selected.add(file.path); else state.selected.delete(file.path); $("file-count").textContent = `(${state.selected.size})`;};
    row.append(input, document.createTextNode(` ${file.name} · ${file.size_bytes} بايت`)); $("files").append(row);
  }
}
$("upload").onchange = async () => {
  const file = $("upload").files[0], project = state.project, epoch = state.epoch; if(!file) return;
  try {
    if(!project) {notice("اختر مشروعًا قبل رفع ملف.", true); return;}
    if(file.size > 65536) throw {code:"file_too_large"};
    let content; try {content = new TextDecoder("utf-8", {fatal:true, ignoreBOM:true}).decode(await file.arrayBuffer());} catch {throw {code:"text_invalid"};}
    await api("upload", {project, upload:uuid(), name:file.name, content});
    if(epoch === state.epoch) {await loadFiles(project, epoch); notice("رُفع الملف. حدد المربع بجانبه لإرفاقه في الطلب.");}
  } catch(e) {if(epoch === state.epoch) showError(e);} finally {$("upload").value = "";}
};
async function inspect(ctx, turn) {
  const epoch = state.epoch, ticket = ++state.dialogEpoch, data = await api("inspect", {...ctx, turn});
  if(!currentDialog(epoch, ticket)) return;
  const body = dialog("المدخلات المستخدمة في هذا الجواب");
  body.append(element("p", "هذه النسخ كما وصلت إلى الجولة، وليست شهادة على صحة الجواب."));
  for(const file of data.attachments || []) {
    const details = element("details"); details.append(element("summary", file.relative_path), element("p", `${file.size_bytes} بايت`), element("p", file.sha256, "hash"), element("pre", file.content)); body.append(details);
  }
  for(const file of data.media || []) {
    const details = element("details"); details.append(element("summary", file.name), element("p", `${file.size_bytes} بايت`), element("p", file.sha256, "hash"));
    const bytes = Uint8Array.from(atob(file.data_base64), c => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], {type:file.mime})); state.urls.push(url);
    const preview = element(file.kind === "image" ? "img" : "audio"); preview.src = url;
    if(file.kind === "image") {preview.alt = file.name; preview.style.maxWidth = "100%";} else {preview.controls = true; preview.preload = "none";}
    details.append(preview); body.append(details);
  }
  if(!(data.attachments?.length || data.media?.length)) body.append(element("p", "لم يُرفق ملف بهذه الجولة."));
  body.append(element("h3", "التفضيلات وقت الطلب"), element("pre", JSON.stringify(data.preferences?.values || {}, null, 2)));
}
async function propose(ctx, turn) {
  const epoch = state.epoch, ticket = ++state.dialogEpoch;
  const body = dialog("حفظ جواب في ملف جديد"), form = element("form"), input = element("input"), labelEl = element("label", "اسم الملف داخل مخرجات المشروع");
  input.required = true; input.value = "مسودة.txt"; input.id = "output-name"; labelEl.htmlFor = input.id;
  const submit = element("button", "عرض المسودة"); form.append(labelEl, input, submit); body.append(form);
  form.onsubmit = async e => {e.preventDefault(); submit.disabled = true; try {const proposal = await api("propose", {...ctx, turn, name:input.value, request:uuid()}); if(currentDialog(epoch,ticket)) await review(ctx.project, proposal.proposal_id);} catch(error) {if(currentDialog(epoch,ticket)) showError(error);} finally {submit.disabled = false;}};
}
async function review(project, proposal) {
  const epoch = state.epoch, ticket = ++state.dialogEpoch, data = await api("review", {project, proposal});
  if(!currentDialog(epoch,ticket)) return;
  const body = dialog("مراجعة المسودة قبل إنشاء الملف");
  body.append(element("p", data.path), element("p", data.sha256, "hash"), element("pre", data.content));
  const apply = button("راجعت المسودة — إنشاء الملف", async () => {apply.disabled = true; try {const result = await api("apply", {project, proposal, sha256:data.sha256}); if(currentDialog(epoch,ticket)) {body.append(element("p", `تم إنشاء الملف: ${result.path}`)); apply.remove();}} catch(error) {apply.disabled = false; if(currentDialog(epoch,ticket)) throw error;}}); body.append(apply);
}
$("preferences").onclick = async () => {
  if(!state.project) {notice("اختر مشروعًا أولًا.", true); return;}
  const project = state.project, epoch = state.epoch, ticket = ++state.dialogEpoch;
  try {
    const data = await api("preferences", {project}); if(!currentDialog(epoch,ticket)) return;
    const body = dialog("تفضيلات هذا المشروع");
    body.append(element("p", "تُحفظ باختيار صريح، وتطبق على الطلبات الجديدة داخل هذا المشروع فقط."));
    for(const [key, title, options] of [["response_language", "لغة الجواب", [["ar","العربية"],["en","الإنجليزية"]]], ["verbosity","طول الجواب",[["concise","موجز"],["balanced","متوازن"],["detailed","مفصل"]]], ["address_name","الاسم الذي تفضل مخاطبتك به",null]]) {
      const form = element("form"), field = element(options ? "select" : "input"), lab = element("label", title); field.id = `pref-${key}`; lab.htmlFor = field.id;
      if(options) {field.add(new Option("غير محدد", "")); for(const [value,text] of options) field.add(new Option(text,value));} else field.maxLength = 80;
      field.value = data.values[key] || ""; const save = element("button", "حفظ هذا التفضيل"); form.append(lab,field,save); body.append(form);
      if(key in data.values) form.append(button("حذف التفضيل", async () => {try {await api("delete_preference", {project,key,revision:data.revision}); if(currentDialog(epoch,ticket)) {dismissDialog(); notice("حُذف التفضيل الصريح.");}} catch(error) {if(currentDialog(epoch,ticket)) throw error;}}));
      form.onsubmit = async e => {e.preventDefault(); save.disabled = true; try {await api("set_preference", {project,key,value:field.value,revision:data.revision}); if(currentDialog(epoch,ticket)) {dismissDialog(); notice("حُفظ التفضيل. يؤثر في الجولات الجديدة.");}} catch(error) {if(currentDialog(epoch,ticket)) showError(error);} finally {save.disabled = false;}};
    }
  } catch(e) {if(currentDialog(epoch,ticket)) showError(e);}
};
async function boot() {
  await projects();
  let last; try {last = JSON.parse(sessionStorage.getItem("diwan.last"));} catch {return;}
  if(last && [...$("projects").options].some(o => o.value === last.project)) {
    $("projects").value = last.project; await chooseProject(last.project);
    const saved = [...$("sessions").children].find(el => el.dataset.session === last.session);
    if(saved) await chooseSession(last.session, saved.textContent, saved.dataset.mode);
  }
}
boot().catch(showError);
// سطح قراءة فقط للوكيل؛ لا نمنحه إرسال طلب أو تطبيق ملف من نص النموذج.
if(document.modelContext?.registerTool) {
  const lifecycle = new AbortController();
  window.addEventListener("pagehide", () => lifecycle.abort(), {once:true});
  try {Promise.resolve(document.modelContext.registerTool({
    name:"read_current_diwan_conversation", title:"قراءة المحادثة المعروضة",
    description:"Read the currently displayed Diwan conversation and execution state. Returns unverified user and assistant text; does not send a message or apply a file.",
    inputSchema:{type:"object",properties:{},additionalProperties:false},
    annotations:{readOnlyHint:true,untrustedContentHint:true},
    execute(input) {if(!input || typeof input !== "object" || Array.isArray(input) || Object.keys(input).length) throw new Error("invalid_input"); return {project:state.project,session:state.session,pending:state.pending,turns:state.turns.map(t=>({turn:t.turn_id,request:t.user_request,answer:t.content,status:t.status,verification:"unverified"}))};},
  }, {signal:lifecycle.signal})).catch(() => {});} catch {}
}
