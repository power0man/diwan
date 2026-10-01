/* Actual Chromium geometry, not the fake-DOM frontend harness. Run via
 * tools/ui_responsive_controls_audit.py; each viewport gets a fresh fake server. */
'use strict';
const {chromium} = require('playwright');
const fs = require('fs');

async function measure(page) {
  return page.evaluate(() => {
    const answer = [...document.querySelectorAll('.message:not(.user) .content')].at(-1);
    const walker = document.createTreeWalker(answer, NodeFilter.SHOW_TEXT);
    let node, last;
    while ((node = walker.nextNode())) if (node.textContent.trim()) last = node;
    const range = document.createRange();
    range.setStart(last, Math.max(0, last.textContent.length - 12));
    range.setEnd(last, last.textContent.length);
    let line = range.getBoundingClientRect();
    window.scrollBy(0, line.bottom - (innerHeight - 24));
    line = range.getBoundingClientRect();
    const composer = document.querySelector('#composer').getBoundingClientRect();
    const hit = document.elementFromPoint((line.left + line.right) / 2, (line.top + line.bottom) / 2);
    const checkbox = document.querySelector('#agent-thinking');
    const box = checkbox.getBoundingClientRect();
    const labelWalker = document.createTreeWalker(checkbox.closest('label'), NodeFilter.SHOW_TEXT);
    let labelText;
    while ((labelText = labelWalker.nextNode())) if (labelText.textContent.trim()) break;
    const textRange = document.createRange();
    textRange.selectNodeContents(labelText);
    const text = textRange.getClientRects()[0];
    return {
      composer_height_px: composer.height, composer_viewport_share: composer.height / innerHeight,
      position: getComputedStyle(document.querySelector('#composer')).position,
      last_answer_line: {top: line.top, bottom: line.bottom, left: line.left, right: line.right},
      last_answer_visible: !!hit && (hit === answer || answer.contains(hit)),
      answer_composer_overlap_px: Math.max(0, Math.min(line.bottom, composer.bottom) - Math.max(line.top, composer.top)),
      checkbox_width_px: box.width, checkbox_text_gap_px: Math.max(0, box.left - text.right, text.left - box.right),
      horizontal_overflow_px: document.documentElement.scrollWidth - innerWidth,
      textarea_height_px: document.querySelector('#message').getBoundingClientRect().height,
    };
  });
}

(async () => {
  const origin = process.argv[2], output = process.argv[3];
  const viewport = process.argv[4] === '390' ? {width: 390, height: 844} : {width: 1366, height: 768};
  const browser = await chromium.launch({headless: true, args: ['--no-proxy-server']});
  try {
    const context = await browser.newContext({viewport, locale: 'ar'});
    const page = await context.newPage(), errors = [], external = [];
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => errors.push(String(error)));
    page.on('console', message => {if (message.type() === 'error') errors.push(message.text());});
    await context.route('**/*', route => {
      if (new URL(route.request().url()).origin === origin) return route.continue();
      external.push('blocked_external_request');
      return route.abort('blockedbyclient');
    });
    await page.goto(origin);
    await page.waitForFunction(() => document.querySelector('#projects').value
      && document.querySelector('#session-title').textContent === 'محادثة عامة');
    const initial = await page.evaluate(() => {
      const composer = document.querySelector('#composer').getBoundingClientRect();
      return {composer_height_px: composer.height, composer_viewport_share: composer.height / innerHeight,
        send_bottom_px: document.querySelector('#send').getBoundingClientRect().bottom};
    });
    const checkbox = page.getByRole('checkbox', {name: /اطلب تفكير النموذج/});
    await checkbox.focus();
    await page.keyboard.press('Space');
    const toggledByKeyboard = await checkbox.isChecked();
    await checkbox.locator('..').click();
    const toggledByLabel = !(await checkbox.isChecked());
    await page.waitForFunction(() => !document.querySelector('#send').disabled);
    await page.locator('#message').fill('سؤال مصطنع');
    await page.locator('#message').press('Enter');
    await page.waitForFunction(() => document.querySelector('.message:not(.user) .content')?.textContent.includes('نهاية الجواب'));
    await page.waitForFunction(() => !document.querySelector('#send').disabled);
    const normal = await measure(page);
    await page.locator('#message').evaluate(field => {
      field.style.height = '600px';
      field.value = Array(30).fill('سطر مصطنع').join('\n');
    });
    const grown = await measure(page);
    const checks = {
      initial_send_visible: initial.send_bottom_px <= viewport.height,
      initial_composer_under_30_percent: initial.composer_viewport_share < 0.30,
      normal_answer_visible: normal.last_answer_visible && normal.answer_composer_overlap_px === 0,
      grown_answer_visible: grown.last_answer_visible && grown.answer_composer_overlap_px === 0,
      draft_height_capped: grown.textarea_height_px <= viewport.height * 0.30 + 1,
      checkbox_native_width: normal.checkbox_width_px >= 10 && normal.checkbox_width_px <= 24,
      checkbox_beside_text: normal.checkbox_text_gap_px <= 12,
      checkbox_keyboard_and_label: toggledByKeyboard && toggledByLabel,
      no_horizontal_overflow: normal.horizontal_overflow_px === 0 && grown.horizontal_overflow_px === 0,
      no_console_errors: errors.length === 0, no_external_requests: external.length === 0,
    };
    const report = {viewport, browser: browser.version(), initial, normal, grown, checks, errors, external,
      passed: Object.values(checks).every(Boolean)};
    fs.writeFileSync(output, JSON.stringify(report, null, 2));
    await context.close();
    if (!report.passed) process.exitCode = 1;
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
