"use strict";
// سائقُ المتصفّح لفحص الواجهة (tools/ui_browser_audit.py): Chromium بلا رأس عبر Playwright لـNode.
// يقرأ إعدادَه من ملف JSON (المسارُ أولُ وسيط)، ويكتب ناتجَه الخام إلى `raw_path` فيه. لا يُشغَّل وحده ولا في CI.
// الخروج: 0 كُتب الناتج؛ 3 تعذّر المتصفّح (`playwright_missing` أو `chromium_missing`)؛ 1 عطبٌ في السائق نفسِه.
const fs = require("fs");
const path = require("path");

const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const VIEWPORTS = {desktop: {width: 1366, height: 768}, mobile: {width: 390, height: 844}};
const MAX_TAB_STOPS = 15;
const WAIT_MS = 20000;
const QUESTION = config.texts.question;

function write(raw) {fs.writeFileSync(config.raw_path, JSON.stringify(raw), "utf8");}

let playwright;
try {playwright = require("playwright");}
catch {write({code: "playwright_missing"}); process.exit(3);}

// نصٌّ قصير من الصفحة للدليل: سطرٌ واحد، بلا أصل الخادم، ولا يتجاوز الحدّ
function short(text, limit = 160) {
  let out = String(text ?? "").replace(/\s+/g, " ").trim();
  // أصلُ الخادم كلمةً بلا «<>»: فـ«ORIGIN/api» مسارٌ نسبيّ عند حارس الدليل، و«>/api» مسارٌ مطلق
  for (const origin of Object.values(config.urls)) out = out.split(origin).join("ORIGIN");
  return out.length > limit ? out.slice(0, limit - 1) + "…" : out;
}
const LATIN_TOKEN = /[A-Za-z_][A-Za-z0-9_]*(?:[._:-][A-Za-z0-9_]+)*/g;
const HEX64 = /\b[0-9a-f]{64}\b/g;

class Journey {
  constructor(name) {this.name = name; this.steps = []; this.checks = {}; this.timings = {};}
  async step(id, viewport, fn) {
    const started = Date.now();
    try {
      const note = await fn();
      this.steps.push({id, viewport, ok: true, ms: Date.now() - started, ...(note ? {note: short(note)} : {})});
      return true;
    } catch (error) {
      this.steps.push({id, viewport, ok: false, ms: Date.now() - started,
        error: short(String(error && error.message || error).split("\n")[0], 200)});
      return false;
    }
  }
  toJSON() {return {steps: this.steps, checks: this.checks, timings_ms: this.timings};}
}

// ما تطلبه الصفحةُ من الخادم (أسماءُ الأفعال وحدها) وما يكتبه سجلُّ الوحدة
function watch(page) {
  const seen = {actions: [], statuses: [], console: [], network_logs: [], page_errors: []};
  page.on("request", request => {
    if (request.method() !== "POST" || !request.url().endsWith("/api")) return;
    try {seen.actions.push(JSON.parse(request.postData() || "{}").action || "?");} catch {seen.actions.push("?");}
  });
  page.on("response", response => {
    if (response.url().endsWith("/api")) seen.statuses.push(response.status());
  });
  page.on("console", message => {
    if (!["error", "warning"].includes(message.type())) return;
    const text = short(message.text());
    // «Failed to load resource … 409» سطرُ الشبكة لردّ رفضٍ مقصود في مسار الخطأ، لا عطبُ شيفرة
    (/^Failed to load resource/.test(text) ? seen.network_logs : seen.console).push({type: message.type(), text});
  });
  page.on("pageerror", error => seen.page_errors.push(short(error.message)));
  return seen;
}

async function open(browser, name, viewportName) {
  const viewport = VIEWPORTS[viewportName];
  const context = await browser.newContext({viewport, deviceScaleFactor: 1, locale: "ar",
    isMobile: viewportName === "mobile", hasTouch: viewportName === "mobile"});
  const page = await context.newPage();
  const seen = watch(page);
  const cdp = await context.newCDPSession(page);
  await cdp.send("Accessibility.enable");
  await cdp.send("DOM.enable");
  return {context, page, seen, cdp, url: config.urls[name]};
}

async function shot(page, file, shots, step) {
  await page.screenshot({path: path.join(config.shots_dir, file), type: "png"});
  shots.push({file, step, viewport: page.viewportSize().width + "x" + page.viewportSize().height});
}

// — الوصول: الأسماءُ من شجرة Chromium نفسِها (CDP)، لا من تقديرٍ في الصفحة —

const CONTROL_ROLES = new Set(["textbox", "combobox", "button", "checkbox", "link", "listbox", "searchbox",
  "spinbutton", "slider", "radio", "menuitem", "switch", "tab", "TextField", "PopUpButton"]);

async function describe(cdp, backendNodeId) {
  try {
    const {node} = await cdp.send("DOM.describeNode", {backendNodeId});
    const attrs = {};
    for (let i = 0; i + 1 < (node.attributes || []).length; i += 2) attrs[node.attributes[i]] = node.attributes[i + 1];
    return {tag: node.localName, id: attrs.id || null, type: attrs.type || null};
  } catch {return {tag: "?", id: null, type: null};}
}

async function unnamedControls(cdp, state) {
  const {nodes} = await cdp.send("Accessibility.getFullAXTree");
  const out = [];
  for (const node of nodes) {
    if (node.ignored) continue;
    const role = node.role && node.role.value;
    if (!CONTROL_ROLES.has(role)) continue;
    const hidden = (node.properties || []).some(p => p.name === "hidden" && p.value && p.value.value);
    if (hidden) continue;
    if ((node.name && String(node.name.value || "").trim())) continue;
    out.push({state, role, ...(await describe(cdp, node.backendDOMNodeId))});
  }
  return out;
}

async function tabOrder(page, cdp) {
  await page.evaluate(() => {document.activeElement && document.activeElement.blur(); window.scrollTo(0, 0);});
  const order = [];
  let first = null;
  for (let i = 0; i < MAX_TAB_STOPS; i++) {
    await page.keyboard.press("Tab");
    const {result} = await cdp.send("Runtime.evaluate", {expression: "document.activeElement"});
    if (!result.objectId) break;
    const {node} = await cdp.send("DOM.describeNode", {objectId: result.objectId});
    if (["body", "html"].includes(node.localName)) break;
    if (first === node.backendNodeId) break;
    first = first ?? node.backendNodeId;
    const {nodes} = await cdp.send("Accessibility.getPartialAXTree", {backendNodeId: node.backendNodeId, fetchRelatives: false});
    const ax = nodes.find(n => n.backendDOMNodeId === node.backendNodeId) || nodes[0] || {};
    const look = await page.evaluate(() => {
      const el = document.activeElement, style = getComputedStyle(el), box = el.getBoundingClientRect();
      return {focus_visible: el.matches(":focus-visible") && style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0,
              in_viewport: box.bottom > 0 && box.top < innerHeight};
    });
    const info = await describe(cdp, node.backendNodeId);
    order.push({index: i + 1, role: ax.role ? ax.role.value : null, name: short(ax.name ? ax.name.value : "", 80),
                tag: info.tag, id: info.id, ...look});
  }
  return order;
}

// axe طُلب فلم يعمل: الحالةُ `failed` برمزٍ مسمًّى، فلا يبدو الفحصُ نظيفًا وهو لم يجرِ (ويرفض الجانبُ البايثونيّ كتابتَه)
function axeFailed(axe, code, error) {
  axe.status = "failed";
  axe.code = axe.code || code;
  axe.errors.push(short(error && error.message || error));
}

async function runAxe(page, state, axe, journeyName = "current") {
  if (!config.axe_path) return;
  let source;
  try {source = fs.readFileSync(config.axe_path, "utf8");}
  catch (error) {axeFailed(axe, "axe_unavailable", error); return;}
  try {
    await page.evaluate(source);
    const result = await page.evaluate(async () => {
      const run = await window.axe.run(document, {runOnly: {type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]}});
      // المخالفاتُ وما يحتاج مراجعةً يدوية (incomplete) بتفاصيلها: القاعدة، والأثر، والرابط، والعناصر، والأسباب
      const record = v => ({id: v.id, impact: v.impact, help_url: v.helpUrl, nodes: v.nodes.length,
        targets: v.nodes.slice(0, 3).map(n => String(n.target.join(" "))),
        reasons: [...new Set(v.nodes.flatMap(n => [...n.any, ...n.all, ...n.none].map(c => String(c.message))))].slice(0, 3)});
      return {version: window.axe.version, violations: run.violations.map(record), incomplete: run.incomplete.map(record),
              passes: run.passes.length};
    });
    axe.version = result.version;
    axe.states.push({journey: journeyName, state, violations: result.violations.length, passes: result.passes,
                     incomplete: result.incomplete.length});
    for (const [kind, items] of [["violations", result.violations], ["incomplete", result.incomplete]]) {
      for (const v of items) {
        const item = {...v, help_url: short(v.help_url, 160), targets: v.targets.map(t => short(t, 80)),
                      reasons: v.reasons.map(r => short(r, 160))};
        const known = axe[kind].find(x => x.id === item.id);
        if (known) {known.nodes = Math.max(known.nodes, item.nodes); if (!known.states.includes(state)) known.states.push(state);}
        else axe[kind].push({...item, states: [state]});
      }
    }
  } catch (error) {axeFailed(axe, "axe_failed", error);}
}

// — ما يُقرأ من الصفحة —

async function pageFacts(page) {
  return page.evaluate(() => {
    const root = document.documentElement, body = getComputedStyle(document.body);
    const live = [...document.querySelectorAll("[aria-live],[role=status],[role=alert],[role=log]")].map(el => ({
      id: el.id || null, role: el.getAttribute("role"), aria_live: el.getAttribute("aria-live"),
      contains_messages: !!el.querySelector("#messages") || el.id === "messages"}));
    return {lang: root.lang, dir: root.dir, body_direction: body.direction, title: document.title,
            horizontal_overflow_px: Math.max(0, root.scrollWidth - root.clientWidth), live_regions: live};
  });
}

async function latinIn(page, selector) {
  return page.evaluate(({selector, source}) => {
    const out = new Set(), hashes = [], json = [];
    const pattern = new RegExp(source, "g");
    for (const box of document.querySelectorAll(selector)) {
      const clone = box.cloneNode(true);
      for (const user of clone.querySelectorAll(".message.user")) user.remove();
      for (const pre of clone.querySelectorAll("pre")) if (/^\s*[\[{]/.test(pre.textContent)) json.push(pre.textContent.length);
      const text = clone.textContent;
      for (const hex of text.match(/[0-9a-f]{64}/g) || []) hashes.push(hex.length);
      for (const word of text.replace(/[0-9a-f]{64}/g, " ").match(pattern) || []) out.add(word);
    }
    return {tokens: [...out].slice(0, 40), hashes: hashes.length, json_blocks: json.length};
  }, {selector, source: LATIN_TOKEN.source});
}

// البصماتُ المعروضة: هل في عنصر `.hash` (اتجاهُه LTR) عنوانٌ عربيّ؟ وهل بقيت البصمةُ متّصلةً على الشاشة؟
// في فقرةٍ LTR يتحوّل الرقمُ الأوروبيّ التالي لحرفٍ عربيّ رقمًا عربيًّا (W2 في خوارزمية الاتجاه)، فإن بدأت البصمةُ برقمٍ
// انفصل رقمُها الأول وانتقل إلى طرف العنوان. الحكمُ على العنصر حتميّ؛ والانفصالُ المرئيّ يُسجَّل كما وقع في هذا التشغيل.
async function hashRendering(page, state, scope) {
  return page.evaluate(({state, scope}) => {
    const out = [];
    for (const el of document.querySelectorAll(`${scope} .hash`)) {
      if (!el.getClientRects().length) continue;
      const text = el.textContent, match = text.match(/[0-9a-f]{64}/);
      if (!match) continue;
      const node = [...el.childNodes].find(n => n.nodeType === 3 && n.textContent.includes(match[0]));
      let contiguous = null;
      if (node) {
        const at = node.textContent.indexOf(match[0]);
        const rect = i => {const r = document.createRange(); r.setStart(node, at + i); r.setEnd(node, at + i + 1); return r.getBoundingClientRect();};
        // كلُّ حرفين متتاليين على السطر نفسِه: الثاني يلي الأولَ مباشرةً إلى اليمين (البصمةُ LTR)
        contiguous = true;
        for (let i = 0; i < 63 && contiguous; i++) {
          const a = rect(i), b = rect(i + 1);
          if (Math.abs(a.top - b.top) < 2) contiguous = Math.abs(a.right - b.left) < a.width * 0.6;
        }
      }
      out.push({state, direction: getComputedStyle(el).direction, arabic_label: /[\u0600-\u06FF]/.test(text),
                starts_with_digit: /^[0-9]/.test(match[0]), contiguous});
    }
    return out;
  }, {state, scope});
}

// هل التركيزُ داخل النافذة المفتوحة؟ (عند فتحها أو استبدال محتواها)
async function focusInDialog(page) {
  return page.evaluate(() => !!(document.activeElement && document.activeElement.closest("dialog[open]")));
}

async function waitText(page, text, timeout = WAIT_MS) {
  await page.waitForFunction(t => document.body.innerText.includes(t), text, {timeout});
}

async function clickButton(page, name) {
  await page.getByRole("button", {name, exact: true}).last().click();
}

// — الرحلةُ القائمة: عرضُ الحاسوب —

async function currentDesktop(browser, journey, shots, axe) {
  const {context, page, seen, cdp, url} = await open(browser, "current-desktop", "desktop");
  const vp = "desktop", checks = journey.checks, t = journey.timings;
  try {
    await journey.step("open", vp, async () => {
      const started = Date.now();
      await page.goto(url, {waitUntil: "networkidle"});
      t.open_to_idle = Date.now() - started;
      const nav = await page.evaluate(() => {
        const n = performance.getEntriesByType("navigation")[0];
        const api = performance.getEntriesByType("resource").filter(r => r.name.endsWith("/api"));
        return {dcl: Math.round(n.domContentLoadedEventEnd), first_api: api.length ? Math.round(api[0].responseEnd) : null};
      });
      t.dom_content_loaded = nav.dcl; t.first_api_response = nav.first_api;
      Object.assign(checks, await pageFacts(page));
      checks.horizontal_scroll_1366 = checks.horizontal_overflow_px > 0;
      delete checks.horizontal_overflow_px;
      return `lang=${checks.lang} dir=${checks.dir}`;
    });
    await journey.step("empty_state", vp, async () => {
      checks.empty_state_heading = short(await page.locator("#session-title").innerText());
      checks.empty_state_notice = short(await page.locator("#notice").innerText());
      const modes = await page.$$eval("#session-mode option", options => options.map(o => ({value: o.value,
        label: o.textContent.trim(), disabled: o.disabled, title: o.title || "",
        described: !!(o.getAttribute("aria-description") || o.getAttribute("aria-describedby"))})));
      const formText = await page.evaluate(() => {
        const form = document.getElementById("new-session").cloneNode(true);
        for (const o of form.querySelectorAll("option")) o.remove();
        return form.textContent;
      });
      const disabled = modes.filter(m => m.disabled);
      checks.session_modes = modes.map(m => ({value: m.value, label: short(m.label, 60), disabled: m.disabled}));
      checks.disabled_session_modes = disabled.map(m => m.value);
      checks.default_session_mode = await page.locator("#session-mode").inputValue();
      checks.disabled_modes_explained = disabled.length === 0 || disabled.every(m => m.title || m.described)
        || /غير مهيأ|غير مهيّأ|معطّل|معطل|لتفعيل|يتطلب|يلزم/.test(formText);
      checks.unnamed_controls = await unnamedControls(cdp, "empty");
      await shot(page, "01-empty-desktop.png", shots, "empty_state");
      await runAxe(page, "empty-desktop", axe);
      return `${disabled.length} disabled of ${modes.length}`;
    });
    await journey.step("tab_order", vp, async () => {
      checks.tab_order = await tabOrder(page, cdp);
      return `${checks.tab_order.length} stops`;
    });
    await journey.step("send_before_session", vp, async () => {
      await page.locator("#message").fill(QUESTION);
      const before = seen.actions.length;
      await page.locator("#send").click();
      await page.waitForSelector("#notice.error", {timeout: 5000});
      checks.send_before_session_notice = short(await page.locator("#notice").innerText());
      checks.send_before_session_refused = !seen.actions.slice(before).some(a => /ask/.test(a));
      await page.locator("#message").fill("");
      return checks.send_before_session_notice;
    });
    await journey.step("create_project", vp, async () => {
      const started = Date.now();
      await page.locator("#project-name").fill("مشروعي الأول");
      await page.locator("#new-project button").click();
      await page.waitForFunction(() => document.getElementById("projects").value
        && document.getElementById("session-title").textContent.includes("اختر محادثة"), null, {timeout: WAIT_MS});
      t.create_project = Date.now() - started;
    });
    await journey.step("create_text_session", vp, async () => {
      const started = Date.now();
      await page.locator("#session-mode").selectOption("text");
      await page.locator("#session-name").fill("محادثتي الأولى");
      await page.locator("#new-session button").click();
      await page.waitForFunction(() => document.getElementById("session-title").textContent === "محادثتي الأولى"
        && /جولة محفوظة/.test(document.getElementById("notice").textContent), null, {timeout: WAIT_MS});
      t.create_session = Date.now() - started;
    });
    let sentByKeyboard = false;
    await journey.step("keyboard_send", vp, async () => {
      const box = page.locator("#message");
      await box.click();
      await box.fill(QUESTION);
      let before = seen.actions.filter(a => /ask/.test(a)).length;
      await page.keyboard.press("Control+Enter");
      await page.waitForTimeout(1200);
      checks.ctrl_enter_sends = seen.actions.filter(a => /ask/.test(a)).length > before;
      if (!checks.ctrl_enter_sends) {
        before = seen.actions.filter(a => /ask/.test(a)).length;
        await box.press("End");
        await page.keyboard.press("Enter");
        await page.waitForTimeout(1200);
        checks.enter_sends = seen.actions.filter(a => /ask/.test(a)).length > before;
        checks.enter_inserts_newline = (await box.inputValue()).includes("\n");
      } else checks.enter_sends = null;
      sentByKeyboard = checks.ctrl_enter_sends || checks.enter_sends === true;
      if (!sentByKeyboard) await box.fill(QUESTION);
      return `ctrl+enter=${checks.ctrl_enter_sends} enter=${checks.enter_sends}`;
    });
    await journey.step("ask_text", vp, async () => {
      const started = Date.now();
      if (!sentByKeyboard) await page.locator("#send").click();
      await page.waitForFunction(m => [...document.querySelectorAll(".message:not(.user) .content")]
        .some(el => el.textContent.includes(m)), config.texts.answer_marker, {timeout: WAIT_MS});
      t.ask_to_answer = Date.now() - started;
      const answer = await page.evaluate(() => {
        const card = [...document.querySelectorAll(".message:not(.user)")].pop();
        const content = card.querySelector(".content"), style = getComputedStyle(content);
        const messages = document.getElementById("messages");
        return {text: content.textContent, direction: style.direction, bidi: style.unicodeBidi,
                label: card.querySelector("strong").textContent, meta: card.querySelector(".meta").textContent,
                messages_live: !!(messages.getAttribute("aria-live") || ["log", "status"].includes(messages.getAttribute("role"))),
                notice: document.getElementById("notice").textContent};
      });
      checks.answer_direction = answer.direction;
      checks.answer_unicode_bidi = answer.bidi;
      checks.answer_label = short(answer.label, 60);
      checks.answer_meta = short(answer.meta, 80);
      checks.answer_markdown_literal = /(^|\n)#{1,6} |\*\*/.test(answer.text);
      checks.messages_live = answer.messages_live;
      checks.notice_when_answer_rendered = short(answer.notice);
      // الإعلانُ بعد أن تهدأ الصفحة (يُعاد التحقّق من الجولة المعلّقة ثم يُكتب الإشعار)
      await page.waitForFunction(() => !/يكتب/.test(document.getElementById("notice").textContent), null, {timeout: 5000}).catch(() => {});
      const settled = await page.locator("#notice").innerText();
      checks.notice_after_answer = short(settled);
      checks.answer_announced = answer.messages_live || [answer.notice, settled].some(n => n.includes(config.texts.answer_marker));
      const layout = await page.evaluate(() => ({composer: document.getElementById("composer").getBoundingClientRect().height,
                                                 position: getComputedStyle(document.getElementById("composer")).position}));
      checks.composer_height_px = Math.round(layout.composer);
      checks.composer_position = layout.position;
      checks.composer_viewport_share = Math.round(100 * layout.composer / VIEWPORTS.desktop.height) / 100;
      const card = await latinIn(page, "#messages");
      checks.text_answer_latin_outside_answer = card.tokens.filter(w => !answer.text.includes(w));
      await shot(page, "02-text-answer-desktop.png", shots, "ask_text");
      checks.unnamed_controls.push(...await unnamedControls(cdp, "text-answer"));
      await runAxe(page, "text-answer-desktop", axe);
      return `answer in ${t.ask_to_answer} ms`;
    });
    await journey.step("draft_review_apply", vp, async () => {
      await clickButton(page, "مراجعة وحفظ مسودة");
      await page.waitForSelector("#dialog[open] #output-name");
      checks.dialog_focus = {propose: await focusInDialog(page)};
      await clickButton(page, "عرض المسودة");
      await waitText(page, "مراجعة المسودة قبل إنشاء الملف");
      const facts = await page.evaluate(() => {
        const hash = document.querySelector("#dialog .hash");
        return {hash_direction: hash ? getComputedStyle(hash).direction : null,
                hash_is_sha256: hash ? /^[0-9a-f]{64}$/.test(hash.textContent) : false};
      });
      checks.hashes = await hashRendering(page, "draft-review", "#dialog");
      checks.draft_hash_direction = facts.hash_direction;
      checks.draft_hash_shown = facts.hash_is_sha256;
      // النافذةُ مفتوحةٌ فيُستبدل محتواها: الزرُّ الذي ضغطه المستخدمُ يُحذف، فأين يذهب التركيز؟
      checks.dialog_focus.review = await focusInDialog(page);
      checks.unnamed_controls.push(...await unnamedControls(cdp, "draft-review-dialog"));
      await clickButton(page, "راجعت المسودة — إنشاء الملف");
      await waitText(page, "تم إنشاء الملف:");
      await shot(page, "03-draft-review-desktop.png", shots, "draft_review_apply");
      await page.locator("#close-dialog").click();
      checks.focus_after_dialog_close = await page.evaluate(() => {
        const el = document.activeElement; return el && el !== document.body ? el.textContent.trim().slice(0, 40) || el.id : "body";
      });
    });
    await journey.step("memory_dialog", vp, async () => {
      await clickButton(page, "تذكّر هذا");
      await waitText(page, "تذكّر هذا في ذاكرة المشروع");
      checks.dialog_focus.remember = await focusInDialog(page);
      checks.unnamed_controls.push(...await unnamedControls(cdp, "remember-dialog"));
      await runAxe(page, "remember-dialog-desktop", axe);
      const field = page.locator("#dialog textarea");
      await field.fill("");
      await clickButton(page, "احفظ في الذاكرة");
      await page.waitForSelector("#dialog-feedback", {timeout: 5000});
      checks.memory_empty_error = short(await page.locator("#dialog-feedback").innerText());
      checks.memory_empty_error_role = await page.locator("#dialog-feedback").getAttribute("role");
      await field.fill(config.texts.memory_text);
      await clickButton(page, "احفظ في الذاكرة");
      await page.waitForFunction(() => !document.getElementById("dialog").open, null, {timeout: WAIT_MS});
      checks.memory_saved_notice = short(await page.locator("#notice").innerText());
      await page.locator("#memory").click();
      await waitText(page, config.texts.memory_text);
      checks.dialog_focus.memory_panel = await focusInDialog(page);
      const listed = await latinIn(page, "#dialog");
      checks.memory_panel_latin = listed.tokens;
      checks.memory_panel_iso_timestamp = await page.evaluate(() =>
        /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(document.getElementById("dialog").textContent));
      checks.unnamed_controls.push(...await unnamedControls(cdp, "memory-panel"));
      // النسيان: يُمحى العنصر ويُكتب إيصالٌ ببصمته (في جذرٍ مؤقّت)
      await clickButton(page, "انسَ");
      await waitText(page, "نُسي.");
      checks.dialog_focus.forget = await focusInDialog(page);
      checks.hashes.push(...await hashRendering(page, "forget-receipt", "#dialog"));
      await shot(page, "09-memory-desktop.png", shots, "memory_dialog");
      await page.locator("#close-dialog").click();
    });
    await journey.step("create_agent_session", vp, async () => {
      const started = Date.now();
      await page.locator("#session-mode").selectOption("agent");
      await page.locator("#session-name").fill("مهمة بالأدوات");
      await page.locator("#new-session button").click();
      await page.waitForFunction(() => document.getElementById("session-title").textContent === "مهمة بالأدوات"
        && !document.getElementById("agent-controls").hidden
        && document.getElementById("agent-capabilities").textContent.length > 0, null, {timeout: WAIT_MS});
      t.create_agent_session = Date.now() - started;
      checks.agent_capabilities_text = short(await page.locator("#agent-capabilities").innerText());
      checks.thinking_checkbox_width_px = Math.round((await page.locator("#agent-thinking").boundingBox()).width);
    });
    await journey.step("agent_write", vp, async () => {
      const started = Date.now();
      await page.locator("#message").fill(config.texts.write_request);
      await page.locator("#send").click();
      await waitText(page, config.texts.agent_done);
      t.agent_write_to_answer = Date.now() - started;
      await page.evaluate(() => {for (const d of document.querySelectorAll("#messages details")) d.open = true;});
      const facts = await latinIn(page, "#messages");
      checks.agent_stream_latin = facts.tokens;
      checks.agent_stream_hashes = facts.hashes;
      checks.agent_stream_json_blocks = facts.json_blocks;
      checks.agent_turn_buttons = await page.evaluate(() => [...[...document.querySelectorAll(".message:not(.user)")].pop()
        .querySelectorAll(".tools button, details button")].map(b => b.textContent.trim().slice(0, 40)));
      checks.hashes.push(...await hashRendering(page, "agent-steps", "#messages"));
      await page.locator("#messages details").last().scrollIntoViewIfNeeded();
      await shot(page, "04-agent-steps-desktop.png", shots, "agent_write");
      checks.unnamed_controls.push(...await unnamedControls(cdp, "agent-steps"));
      return `answer in ${t.agent_write_to_answer} ms`;
    });
    await journey.step("agent_approval", vp, async () => {
      const started = Date.now();
      await page.locator("#message").fill(config.texts.memory_request);
      await page.locator("#send").click();
      const review = page.getByRole("button", {name: /^مراجعة فعل/});
      await review.last().waitFor({timeout: WAIT_MS});
      t.agent_request_to_approval = Date.now() - started;
      checks.approval_button_label = short(await review.last().innerText(), 60);
      await review.last().click();
      await waitText(page, "قرار لفعل محدد");
      checks.dialog_focus.approval = await focusInDialog(page);
      const facts = await latinIn(page, "#dialog");
      checks.approval_dialog_latin = facts.tokens;
      checks.approval_dialog_json_blocks = facts.json_blocks;
      checks.approval_dialog_hashes = facts.hashes;
      checks.hashes.push(...await hashRendering(page, "approval-dialog", "#dialog"));
      checks.unnamed_controls.push(...await unnamedControls(cdp, "approval-dialog"));
      await shot(page, "05-approval-desktop.png", shots, "agent_approval");
      await runAxe(page, "approval-dialog-desktop", axe);
      const decided = Date.now();
      await clickButton(page, "أوافق وأتابع");
      await waitText(page, config.texts.memory_done);
      t.approval_to_answer = Date.now() - decided;
    });
    await journey.step("error_path", vp, async () => {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.locator("#project-name").fill("   ");
      await page.locator("#new-project button").click();
      await page.waitForSelector("#notice.error", {timeout: 5000});
      const text = await page.locator("#notice").innerText();
      checks.error_notice = short(text);
      checks.raw_error_codes_visible = [...text.matchAll(/\(([a-z][a-z0-9_]+)\)/g)].map(m => m[1]);
      await shot(page, "06-raw-error-desktop.png", shots, "error_path");
      return checks.raw_error_codes_visible.join(",") || "no raw code";
    });
  } finally {
    checks.api_actions = [...new Set(seen.actions)];
    checks.console_errors = [...seen.console, ...seen.page_errors.map(text => ({type: "pageerror", text}))];
    checks.network_error_logs = seen.network_logs.length;
    await context.close();
  }
}

// — الرحلةُ القائمة: عرضُ الجوّال —

async function currentMobile(browser, journey, shots, axe) {
  const {context, page, seen, cdp, url} = await open(browser, "current-mobile", "mobile");
  const vp = "mobile", checks = journey.checks, t = journey.timings;
  try {
    await journey.step("mobile_open", vp, async () => {
      const started = Date.now();
      await page.goto(url, {waitUntil: "networkidle"});
      t.mobile_open_to_idle = Date.now() - started;
    });
    await journey.step("mobile_empty", vp, async () => {
      const facts = await pageFacts(page);
      checks.horizontal_overflow_390_px = facts.horizontal_overflow_px;
      checks.horizontal_scroll_390 = facts.horizontal_overflow_px > 0;
      const layout = await page.evaluate(() => {
        const box = el => {const r = document.getElementById(el).getBoundingClientRect(); return {top: Math.round(r.top + scrollY), bottom: Math.round(r.bottom + scrollY)};};
        return {composer: box("message"), send: box("send"), aside_bottom: Math.round(document.querySelector("aside").getBoundingClientRect().bottom + scrollY),
                page_height: document.documentElement.scrollHeight};
      });
      checks.mobile_composer_top_px = layout.composer.top;
      checks.mobile_sidebar_bottom_px = layout.aside_bottom;
      checks.mobile_page_height_px = layout.page_height;
      checks.composer_in_first_screen_mobile = layout.send.bottom <= VIEWPORTS.mobile.height;
      checks.mobile_unnamed_controls = await unnamedControls(cdp, "mobile-empty");
      await shot(page, "07-mobile-empty.png", shots, "mobile_empty");
      await runAxe(page, "empty-mobile", axe);
      return `composer at ${layout.composer.top}px of ${VIEWPORTS.mobile.height}`;
    });
    await journey.step("mobile_ask_text", vp, async () => {
      const started = Date.now();
      await page.locator("#project-name").fill("مشروعي");
      await page.locator("#new-project button").click();
      await page.waitForFunction(() => document.getElementById("projects").value, null, {timeout: WAIT_MS});
      await page.locator("#session-mode").selectOption("text");
      await page.locator("#session-name").fill("محادثة");
      await page.locator("#new-session button").click();
      await page.waitForFunction(() => document.getElementById("session-title").textContent === "محادثة", null, {timeout: WAIT_MS});
      t.mobile_setup = Date.now() - started;
      const asked = Date.now();
      await page.locator("#message").fill(QUESTION);
      await page.locator("#send").click();
      await page.waitForFunction(m => document.body.innerText.includes(m), config.texts.answer_marker, {timeout: WAIT_MS});
      t.mobile_ask_to_answer = Date.now() - asked;
      await page.locator(".message:not(.user)").last().scrollIntoViewIfNeeded();
      checks.horizontal_overflow_390_after_answer_px = (await pageFacts(page)).horizontal_overflow_px;
      await shot(page, "08-mobile-answer.png", shots, "mobile_ask_text");
    });
  } finally {
    checks.mobile_console_errors = [...seen.console, ...seen.page_errors.map(text => ({type: "pageerror", text}))];
    await context.close();
  }
}

// — رحلةُ الصفحة الموحّدة (قبول #166): افتح ← اكتب في حقل السؤال ← Enter ← جواب، بلا إنشاء شيء —

async function singlePage(browser, journey, viewportName, axe) {
  const {context, page, seen, url} = await open(browser, `single-page-${viewportName}`, viewportName);
  const vp = viewportName, checks = journey.checks[viewportName] = {}, t = journey.timings;
  try {
    await journey.step("sp_open", vp, async () => {
      const started = Date.now();
      await page.goto(url, {waitUntil: "networkidle"});
      t[`sp_open_${vp}`] = Date.now() - started;
    });
    let field = null;
    await journey.step("sp_question_field", vp, async () => {
      // أولُ حقلٍ ظاهر لكتابة نصٍّ حرّ: textarea، أو محرِّرٌ قابلٌ للتحرير، أو حقلُ نصٍّ اسمُه يدلّ على السؤال
      const candidates = page.locator("textarea:visible, [contenteditable=true]:visible");
      await candidates.first().waitFor({timeout: 5000});
      field = candidates.first();
      const box = await field.boundingBox();
      checks.question_field_in_first_screen = !!box && box.y + box.height <= VIEWPORTS[vp].height;
      checks.question_field_name = short(await field.evaluate(el => el.labels?.[0]?.textContent || el.getAttribute("aria-label") || el.placeholder || ""), 60);
    });
    // axe على الصفحة الموحّدة نفسِها: الحالةُ الأولى، ثم بعد الجواب إن ظهر
    await runAxe(page, `sp-empty-${vp}`, axe, "single-page");
    await journey.step("sp_enter_answers", vp, async () => {
      if (!field) throw new Error("no_question_field");
      const started = Date.now();
      await field.click();
      await field.fill(QUESTION);
      await page.keyboard.press("Enter");
      await page.waitForFunction(m => document.body.innerText.includes(m), config.texts.answer_marker, {timeout: 8000});
      t[`sp_enter_to_answer_${vp}`] = Date.now() - started;
      await runAxe(page, `sp-answer-${vp}`, axe, "single-page");
    });
    checks.notice_after_enter = short(await page.locator("[role=status],[aria-live]").first().innerText().catch(() => ""));
  } finally {
    checks.api_actions = [...new Set(seen.actions)];
    checks.console_errors = [...seen.console, ...seen.page_errors.map(text => ({type: "pageerror", text}))];
    await context.close();
  }
}

(async () => {
  let browser;
  try {browser = await playwright.chromium.launch({headless: true, args: ["--no-proxy-server"]});}
  catch (error) {write({code: "chromium_missing", detail: short(error.message)}); process.exit(3);}
  const raw = {browser: {name: "chromium", headless: true, version: browser.version(),
                         playwright: require("playwright/package.json").version},
               viewports: Object.entries(VIEWPORTS).map(([name, size]) => ({name, ...size, device_scale_factor: 1})),
               journeys: {}, screenshots: [],
               axe: config.axe_path ? {status: "run", version: null, states: [], violations: [], incomplete: [], errors: []}
                                    : {status: "not_run"}};
  try {
    if (config.journeys.includes("current")) {
      const journey = new Journey("current");
      await currentDesktop(browser, journey, raw.screenshots, raw.axe);
      await currentMobile(browser, journey, raw.screenshots, raw.axe);
      raw.journeys.current = journey.toJSON();
    }
    if (config.journeys.includes("single-page")) {
      const journey = new Journey("single-page");
      await singlePage(browser, journey, "desktop", raw.axe);
      await singlePage(browser, journey, "mobile", raw.axe);
      raw.journeys["single-page"] = journey.toJSON();
    }
  } finally {
    await browser.close();
    write(raw);
  }
})().catch(error => {process.stderr.write(String(error && error.stack || error)); process.exit(1);});
