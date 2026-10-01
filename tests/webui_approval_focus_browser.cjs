/* Real-Chromium regression for approval focus. The caller owns a local server
 * with a synthetic provider; this driver blocks every origin except that server. */
"use strict";

const assert = require("node:assert/strict");
let playwright;
try { playwright = require("playwright"); }
catch { process.stderr.write("playwright_missing\n"); process.exit(3); }

const origin = process.argv[2];
if (!origin) throw new Error("origin_required");

(async () => {
  let browser;
  try { browser = await playwright.chromium.launch({headless: true}); }
  catch (error) {
    if (/Executable doesn't exist|browserType\.launch/.test(String(error))) {
      process.stderr.write("chromium_missing\n"); process.exit(3);
    }
    throw error;
  }
  try {
    const context = await browser.newContext({locale: "ar", viewport: {width: 1366, height: 768}});
    let blockedExternal = 0;
    await context.route("**/*", route => {
      if (new URL(route.request().url()).origin === new URL(origin).origin) return route.continue();
      blockedExternal += 1;
      return route.abort("blockedbyclient");
    });
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    await page.goto(origin, {waitUntil: "domcontentloaded"});
    await page.locator("#session-title").filter({hasText: "محادثة عامة"}).waitFor();
    await page.locator("#message").fill("احفظ تفضيلي بعد موافقتي.");
    await page.locator("#message").press("Enter");
    const review = page.getByRole("button", {name: "مراجعة: اقتراح حفظ في الذاكرة", exact: true});
    await review.waitFor();
    await review.click();
    assert.equal(await page.evaluate(() => document.querySelector("#dialog").contains(document.activeElement)), true,
      "approval dialog must receive focus");
    await page.getByRole("button", {name: "أوافق وأتابع", exact: true}).click();
    await page.locator("#messages").filter({hasText: "اكتملت الجولة بعد الموافقة."}).waitFor();
    const focus = await page.evaluate(() => ({id: document.activeElement?.id || null,
      tag: document.activeElement?.tagName?.toLowerCase() || null}));
    assert.deepEqual(focus, {id: "message", tag: "textarea"},
      `focus after the approved round must reach the composer, got ${JSON.stringify(focus)}`);
    assert.equal(blockedExternal, 0, "the real-browser regression attempted an external request");
    process.stdout.write(JSON.stringify({focus, blocked_external: blockedExternal}) + "\n");
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { process.stderr.write(String(error && error.stack || error) + "\n"); process.exit(1); });
