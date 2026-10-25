// Actual React controls and original Docker services. No API/SSE response mocks.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { execFileSync } = require('node:child_process');
const texts = require('../../docs/product/ui-texts.ru.json');
const records = require('../../seed/companies.json').companies;
const accounts = require('../../seed/accounts.json');

module.exports = async function checkDockerRecovery(browser, cfg) {
  // The existing product runner supplies a private config for its ephemeral stand.
  // Never discover, stop or recreate services in a developer's default project.
  assert.match(cfg.env.COMPOSE_PROJECT_NAME, /^alfa-b10-[a-f0-9]{10}$/);
  assert.equal(new URL(cfg.origin).hostname, '127.0.0.1');
  assert.equal(new URL(cfg.origin).port, String(cfg.env.FRONTEND_PORT));
  const compose = (...args) => execFileSync('docker', [...cfg.compose, ...args], {
    env: { ...process.env, ...cfg.env }, stdio: 'pipe', timeout: 30_000,
  }).toString().trim();
  const record = records.find((r) => r.key === 'manual');
  const clientAccount = accounts.clients.find((a) => a.login === record.client_login);
  const analystAccount = accounts.underwriters[0];
  const results = [];

  async function login(page, account) {
    await page.goto(cfg.origin, { waitUntil: 'networkidle' });
    await page.getByLabel(texts.login.loginLabel).fill(account.login);
    await page.getByLabel(texts.login.passwordLabel).fill(account.password);
    await page.getByRole('button', { name: texts.login.submit, exact: true }).click();
    await page.waitForURL((url) => url.pathname === (account === analystAccount ? '/bank' : '/client'));
  }
  async function ready(context) {
    const end = Date.now() + 25_000;
    while (Date.now() < end) {
      try {
        const response = await context.request.get(`${cfg.origin}/api/auth/me`, { timeout: 2000 });
        if (response.ok()) return;
      } catch { /* backend/Docker DNS can still be recovering */ }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    throw Error('Backend did not recover through nginx');
  }
  async function get(context, route) {
    const response = await context.request.get(`${cfg.origin}/api/${route}`, { timeout: 5000 });
    assert(response.ok(), `Read ${route}: ${response.status()}`);
    return response.json();
  }

  for (const fault of ['offline', 'offline-closed', 'backend-502', 'backend-recreate']) {
    const context = await browser.newContext();
    const analystContext = await browser.newContext();
    let backendDown = false;
    try {
      await context.addInitScript(() => {
        window.qaRecovery = { sources: [], errors: [], frames: [], maximum: 0 };
        const NativeSource = window.EventSource;
        window.EventSource = class extends NativeSource {
          constructor(...args) {
            super(...args);
            const qa = window.qaRecovery;
            qa.sources.push(this);
            qa.maximum = Math.max(qa.maximum, qa.sources.filter((s) => s.readyState !== 2).length);
            this.addEventListener('error', () => qa.errors.push(this.readyState));
            for (const name of ['status_changed', 'application_updated', 'decision_created', 'document_uploaded']) {
              this.addEventListener(name, (event) => qa.frames.push(event.lastEventId));
            }
          }
        };
      });
      const page = await context.newPage();
      const analyst = await analystContext.newPage();
      page.setDefaultTimeout(20_000);
      analyst.setDefaultTimeout(20_000);
      const pageErrors = [];
      page.on('pageerror', (error) => pageErrors.push(error.message));
      analyst.on('pageerror', (error) => pageErrors.push(error.message));
      let documents = 0;
      let historyReads = 0;
      let failed502 = 0;
      let cancelPosts = 0;
      page.on('request', (request) => {
        if (request.resourceType() === 'document') documents++;
        if (request.url().endsWith('/cancel') && request.method() === 'POST') cancelPosts++;
      });
      page.on('response', (response) => {
        const route = new URL(response.url()).pathname;
        if (route.endsWith('/history') && response.ok()) historyReads++;
        if (route.startsWith('/api/') && response.status() === 502) failed502++;
      });

      await login(page, clientAccount);
      await page.getByRole('link', { name: texts.applications.newApplication, exact: true }).click();
      await page.getByRole('radio', { name: texts.purposes[record.application.purpose].label, exact: true }).click();
      await page.getByRole('button', {
        name: `${texts.productPicker.choose}: ${texts.products[record.application.product_code].name}`, exact: true,
      }).click();
      await page.getByLabel(texts.form.inn.label, { exact: true }).fill(record.company.inn);
      await page.getByText(texts.form.autofillNote, { exact: true }).waitFor();
      await page.getByLabel(texts.form.amount.label, { exact: true }).fill(record.application.amount.split('.')[0]);
      await page.getByLabel(texts.form.term.label, { exact: true }).fill(String(record.application.term_months));
      await page.getByLabel(texts.form.annualRevenue.label, { exact: true }).fill(record.application.annual_revenue.split('.')[0]);
      await page.getByRole('button', { name: texts.form.submit, exact: true }).click();
      await page.getByRole('heading', { name: texts.statuses.MANUAL_REVIEW.title, exact: true }).waitFor();
      const id = new URL(page.url()).pathname.split('/').at(-1);
      assert.match(id, /^[0-9a-f-]{36}$/);
      await page.waitForFunction(() => window.qaRecovery.sources.some((s) => s.readyState === 1));
      await page.waitForResponse((r) => new URL(r.url()).pathname.endsWith('/history') && r.ok(), { timeout: 5000 })
        .catch(() => assert(historyReads > 0, 'Initial history was read'));

      await login(analyst, analystAccount);
      await analyst.goto(`${cfg.origin}/bank/applications/${id}`);
      await analyst.getByText(texts.manualReviewReasons.gray_zone.title, { exact: true }).waitFor();
      await analyst.getByText(texts.manualReviewReasons.gray_zone.explanation, { exact: true }).waitFor();
      const historyBefore = historyReads;
      const initialCard = await get(context, `applications/${id}`);
      assert(!('review_reason' in initialCard.decision), 'Bank-only reason stays out of the client projection');
      const beforeContainer = compose('ps', '-q', 'backend');
      assert(beforeContainer, 'Backend container exists in the exact test project');
      let approved;

      async function approve() {
        await analyst.getByLabel(texts.bank.decision.auditCommentLabel).fill(`Проверка восстановления UI: ${fault}`);
        const saved = analyst.waitForResponse((r) => r.url().endsWith(`/applications/${id}/decision`) && r.request().method() === 'POST');
        await analyst.getByRole('button', { name: texts.bank.decision.submit, exact: true }).click();
        const response = await saved;
        assert(response.ok(), `Analyst decision: ${response.status()}`);
        approved = (await response.json()).application;
        assert.equal(approved.status, 'APPROVED');
      }

      if (fault === 'offline' || fault === 'offline-closed') {
        await context.setOffline(true);
        // Offline is applied by Chromium, not by replacing fetch or EventSource.
        await page.getByText(texts.errors.liveUpdatesLost, { exact: true }).waitFor();
        await page.getByRole('heading', { name: texts.statuses.MANUAL_REVIEW.title, exact: true }).waitFor();
        if (fault === 'offline-closed') {
          // Deterministic terminal transport fault: use the real browser close() API.
          // HTTP 502 alone may leave Chromium in CONNECTING instead of CLOSED.
          await page.evaluate(() => window.qaRecovery.sources.forEach((source) => source.close()));
          await page.waitForFunction(() => window.qaRecovery.sources.every((source) => source.readyState === 2));
        }
        await approve();
        await context.setOffline(false);
      } else {
        compose('stop', '-t', '1', 'backend');
        backendDown = true;
        await page.getByText(texts.errors.liveUpdatesLost, { exact: true }).waitFor();
        await page.waitForFunction(() => window.qaRecovery.errors.length > 0);
        if (!failed502) await page.waitForResponse((r) => new URL(r.url()).pathname.startsWith('/api/') && r.status() === 502);
        assert(failed502 > 0, 'Real nginx returned 502 during backend outage');
        await page.getByRole('heading', { name: texts.statuses.MANUAL_REVIEW.title, exact: true }).waitFor();
        await page.getByRole('button', { name: texts.applications.cancel, exact: true }).click();
        const dialog = page.getByRole('dialog');
        await dialog.getByRole('button', { name: texts.applications.cancelConfirm, exact: true }).click();
        await dialog.getByText(texts.errors.generic, { exact: true }).waitFor();
        const failedActionCount = cancelPosts;
        await dialog.getByRole('button', { name: texts.common.cancel, exact: true }).click();
        if (fault === 'backend-recreate') {
          compose('up', '-d', '--no-deps', '--force-recreate', 'backend');
          assert.notEqual(compose('ps', '-q', 'backend'), beforeContainer, 'Backend container really changed');
        } else {
          compose('start', 'backend');
        }
        backendDown = false;
        await ready(analystContext);
        await page.getByText(texts.errors.liveUpdatesLost, { exact: true }).waitFor({ state: 'hidden' });
        await approve();
        assert.equal(cancelPosts, failedActionCount, 'Failed mutation was not retried automatically');
      }

      await page.getByRole('button', { name: texts.decision.approved.accept, exact: true }).waitFor();
      await page.getByText(texts.errors.liveUpdatesLost, { exact: true }).waitFor({ state: 'hidden' });
      await page.waitForFunction(() => window.qaRecovery.sources.filter((s) => s.readyState !== 2).length === 1 &&
        window.qaRecovery.sources.some((s) => s.readyState === 1));
      assert(historyReads > historyBefore, 'React reread history after reconnect');
      assert.equal(documents, 1, 'React recovered without a document reload');
      const card = await get(context, `applications/${id}`);
      assert.equal(card.status, 'APPROVED');
      assert.equal(card.revision, approved.revision);
      assert.equal(card.decision.id, approved.decision.id, 'Client sees the saved analyst decision');
      const history = await get(context, `applications/${id}/history?limit=100`);
      assert(history.items.some((event) => event.status === 'APPROVED' && event.revision === card.revision), 'Real saved approval event exists');
      const transport = await page.evaluate(() => ({
        sources: window.qaRecovery.sources.length, errors: window.qaRecovery.errors,
        maximum: window.qaRecovery.maximum, frames: window.qaRecovery.frames.length,
      }));
      assert.equal(transport.maximum, 1, 'React never creates parallel EventSource objects');
      if (fault === 'offline-closed') assert(transport.sources > 1, 'Real CLOSED source was replaced after Online');
      assert.deepEqual(pageErrors, [], 'No uncaught React errors');
      await page.getByRole('button', { name: texts.common.logout, exact: true }).click();
      await page.getByRole('button', { name: texts.login.submit, exact: true }).waitFor();
      await page.waitForFunction(() => window.qaRecovery.sources.every((s) => s.readyState === 2));
      const sourceCount = await page.evaluate(() => window.qaRecovery.sources.length);
      // One full retry interval: logout must not leave a delayed reconnect alive.
      await page.waitForTimeout(3500);
      assert.equal(await page.evaluate(() => window.qaRecovery.sources.length), sourceCount);
      results.push({ fault, documents, revision: card.revision, historyReads, failed502, transport });
      console.log(`PASS React Docker recovery ${JSON.stringify(results.at(-1))}`);
    } finally {
      if (backendDown) compose('up', '-d', '--no-deps', 'backend');
      await context.close();
      await analystContext.close();
    }
  }
  if (process.env.GITHUB_STEP_SUMMARY) {
    fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY,
      '\n## React UI recovery on the original Docker stand\n\n' +
      `Commit: ${process.env.GITHUB_SHA || 'local'}\n\n` +
      '| Fault | Result | Document loads | History reads | Observed 502 |\n|---|---|---:|---:|---:|\n' +
      results.map((r) => `| ${r.fault} | PASS | ${r.documents} | ${r.historyReads} | ${r.failed502} |`).join('\n') + '\n');
  }
  console.log('PASS actual React Docker recovery 4/4: persisted analyst decisions, native transport, no reload, logout cleanup');
};
