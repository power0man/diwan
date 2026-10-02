/* Real Chromium, synthetic local server only; no production provider or data. */
"use strict";
const assert = require("node:assert/strict");
const {chromium} = require("playwright");
const origin = process.argv[2], output = process.argv[3];
if (!origin || !output) throw new Error("origin_and_output_required");
(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM || undefined});
  try {
    const context = await browser.newContext({locale:"ar", viewport:{width:1366,height:900}});
    let blockedExternal=0;
    await context.route("**/*", route => {
      if(new URL(route.request().url()).origin===new URL(origin).origin) return route.continue();
      blockedExternal+=1;return route.abort("blockedbyclient");
    });
    const page=await context.newPage(), calls=[], errors=[];
    page.on("pageerror",error=>errors.push(String(error)));
    page.on("request",request=>{if(request.url().endsWith('/api')) calls.push(JSON.parse(request.postData()));});
    await page.goto(origin,{waitUntil:'domcontentloaded'});
    await page.waitForFunction(()=>!document.querySelector('#send').disabled && document.querySelector('#session-title').textContent==='محادثة عامة');
    assert.ok(await page.locator('#messages').innerText().then(text=>text.includes('جواب السلف المصطنع')));
    await page.locator('#message').fill('طلب بعد تغيير النموذج');
    await page.locator('#message').press('Enter');
    await page.waitForFunction(()=>document.querySelector('#technical-errors').textContent.includes('model_changed_new_session'));
    await page.locator('#details-panel > summary').click();
    await page.locator('#continue-unified').click();
    await page.getByRole('button',{name:'إلغاء',exact:true}).click();
    assert.equal(calls.filter(r=>r.action==='continue_unified').length,0);
    assert.equal(await page.locator('#message').inputValue(),'طلب بعد تغيير النموذج');
    await page.locator('#continue-unified').click();
    await page.getByRole('button',{name:'ابدأ المتابعة الموحدة',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#dialog').open && !document.querySelector('#send').disabled);
    assert.equal(calls.filter(r=>r.action==='continue_unified').length,1);
    assert.equal(await page.locator('#message').inputValue(),'');
    assert.equal(await page.locator('#messages').innerText().then(text=>text.includes('جواب السلف المصطنع')),false);
    const before=calls.filter(r=>['agent_ask','agent_resume'].includes(r.action)).length;
    await page.locator('#message').fill('ما رمز المشروع الآخر؟');
    await page.locator('#message').press('Enter');
    await page.waitForFunction(()=>document.querySelector('#messages').textContent.includes('ذاكرة المشروع الآخر وصلت'));
    assert.equal(calls.filter(r=>['agent_ask','agent_resume'].includes(r.action)).length,before+1);
    const continuation=calls.find(r=>r.action==='continue_unified');
    await page.locator(`#sessions button[data-session="${continuation.session}"]`).click();
    await page.waitForFunction(()=>document.querySelector('#messages').textContent.includes('جواب السلف المصطنع'));
    await page.locator('#continue-unified').click();
    await page.getByRole('button',{name:'ابدأ المتابعة الموحدة',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#messages').textContent.includes('ذاكرة المشروع الآخر وصلت'));
    assert.equal(await page.locator('#sessions button').count(),2);
    assert.equal(calls.filter(r=>['agent_ask','agent_resume'].includes(r.action)).length,before+1);
    await page.screenshot({path:output,fullPage:true});
    assert.equal(blockedExternal,0);assert.deepEqual(errors,[]);
    process.stdout.write(JSON.stringify({passed:true,checks:['model-change-refused','explicit-cancel','fresh-unified-memory','old-history','repeat-single-successor','no-action-replay'],blockedExternal,consoleErrors:errors,screenshot:output})+'\n');
  } finally {await browser.close();}
})().catch(error=>{process.stderr.write(String(error.stack||error)+'\n');process.exitCode=1;});
