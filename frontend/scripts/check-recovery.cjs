// Production React + native Chromium EventSource against a controlled HTTP fixture.
// This checks browser recovery, not Docker/backend/DB resilience acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fixture = require('./recovery-fixtures.json');
const texts = require('../../docs/product/ui-texts.ru.json');
const dist = path.resolve(__dirname, '../dist');
const output = path.resolve(process.env.QA_OUTPUT || 'coverage/recovery');
const copy = (value) => structuredClone(value);

function createStand() {
  let app = copy(fixture.bank.application);
  let outage = false;
  let eventsUnavailable = false;
  let role = 'client';
  let authenticated = true;
  const streams = new Set();
  const requests = [];
  const closeStreams = () => { for (const res of streams) res.end(); };
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const route = url.pathname;
    const json = (body, status = 200) => {
      res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify(body));
    };
    if (route.startsWith('/api/')) {
      const request = { path: route, method: req.method, lastId: req.headers['last-event-id'], revision: app.revision, cursor: url.searchParams.get('cursor') };
      requests.push(request);
      if (outage || (eventsUnavailable && route.endsWith('/events'))) return json({ code: 'UNAVAILABLE' }, 502);
      if (req.method === 'POST' && !route.endsWith('/documents')) {
        const chunks = [];
        for await (const chunk of req) chunks.push(chunk);
        request.body = JSON.parse(Buffer.concat(chunks).toString() || '{}');
      }
      if (route === '/api/auth/me') return json(authenticated ? { ...fixture.user, role } : { code: 'UNAUTHORIZED' }, authenticated ? 200 : 401);
      if (route === '/api/auth/csrf') return json({ csrf_token: 'synthetic-qa-token' });
      if (route === '/api/auth/logout') { authenticated = false; res.writeHead(204); return res.end(); }
      if (route === '/api/products') return json(fixture.products);
      if (route === '/api/underwriter/stats') return json(fixture.stats);
      if (route === '/api/underwriter/applications') return json({ items: [fixture.bank.application], total: 1 });
      if (route === `/api/underwriter/applications/${app.id}`) return json(fixture.bank);
      if (route.endsWith('/documents')) {
        if (req.method === 'GET') return json({ items: [] });
        const chunks = [];
        for await (const chunk of req) chunks.push(chunk);
        request.multipart = Buffer.concat(chunks).toString();
        app.revision++;
        return json({ id: '60000000-0000-4000-8000-000000000001', application_id: app.id,
          filename: 'statement.pdf', document_type: 'bank_statement_12m', media_type: 'application/pdf',
          size_bytes: Buffer.byteLength('%PDF-1.4\nsynthetic'), created_at: app.updated_at, uploaded_by: fixture.user.id });
      }
      if (route.endsWith('/history')) {
        const second = !!url.searchParams.get('cursor');
        return json({ items: [{ id: `70000000-0000-4000-8000-00000000000${second ? 2 : 1}`,
          application_id: app.id, event_type: 'status_changed', revision: app.revision,
          status: app.status, previous_status: 'CHECKING', message: 'Синтетическое событие QA', occurred_at: app.updated_at }],
          next_cursor: second ? null : '70000000-0000-4000-8000-000000000001' });
      }
      if (route === '/api/applications') return json({ items: [app], total: 1 });
      if (route.endsWith('/events')) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store' });
        streams.add(res);
        res.on('close', () => streams.delete(res));
        res.write(`retry: 300\nid: ${app.revision}\nevent: status_changed\ndata: ${JSON.stringify({
          application_id: app.id, revision: app.revision, event_type: 'status_changed',
        })}\n\n`);
        return;
      }
      if (route === `/api/applications/${app.id}`) return json(app);
      if (route.endsWith('/accept-offer')) {
        app = { ...app, status: 'OFFER_ACCEPTED', allowed_actions: ['sign'], revision: app.revision + 1 };
        return json(app);
      }
      if (route.endsWith('/additional-info')) {
        app = { ...copy(fixture.approved), revision: app.revision + 1 };
        return json(app);
      }
      if (route.endsWith('/decision')) {
        app = { ...copy(fixture.approved), revision: app.revision + 1 };
        return json({ application: app, decisions: [], allowed_actions: [] });
      }
      if (route.endsWith('/sign')) {
        app = { ...app, status: 'ISSUED', allowed_actions: [], revision: app.revision + 1 };
        return json(app);
      }
      return json({ code: 'NOT_FOUND' }, 404);
    }
    const candidate = path.resolve(dist, `.${route}`);
    const file = candidate.startsWith(dist + path.sep) && fs.existsSync(candidate) && fs.statSync(candidate).isFile()
      ? candidate : path.join(dist, 'index.html');
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    fs.createReadStream(file).pipe(res);
  });
  return {
    app: () => app,
    setApp: (value) => { app = copy(value); },
    approve: () => { app = { ...copy(fixture.approved), revision: app.revision + 1 }; },
    setRole: (value) => { role = value; },
    outage: (value) => { outage = value; if (value) closeStreams(); },
    eventsUnavailable: (value) => { eventsUnavailable = value; if (value) closeStreams(); },
    closeStreams, streams, requests, server,
  };
}

async function until(predicate, label) {
  const deadline = Date.now() + 15_000;
  while (!predicate()) {
    if (Date.now() > deadline) throw Error(`Timeout: ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

async function main() {
  if (!fs.existsSync(path.join(dist, 'index.html'))) throw Error('Run pnpm build first');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true,
    ...(process.env.CHROMIUM_EXECUTABLE ? { executablePath: process.env.CHROMIUM_EXECUTABLE } : {}),
  });
  const results = [];
  async function scenario(name, run, viewport = { width: 1280, height: 900 }) {
    const stand = createStand();
    await new Promise((resolve) => stand.server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${stand.server.address().port}`;
    const context = await browser.newContext({ viewport });
    await context.addInitScript(() => {
      window.qaSources = [];
      const NativeSource = window.EventSource;
      window.EventSource = class extends NativeSource {
        constructor(...args) {
          super(...args);
          window.qaSources.push(this);
          this.addEventListener('error', () => { this.qaErrorState = this.readyState; });
        }
      };
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    let documents = 0;
    page.on('request', (request) => { if (request.resourceType() === 'document') documents++; });
    try {
      await run({ page, context, stand, origin, documents: () => documents });
      assert.deepEqual(errors, [], 'No uncaught React/browser exceptions');
      await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true });
      results.push({ name, result: 'PASS', documents,
        sources: await page.evaluate(() => window.qaSources.map((source) => ({ state: source.readyState, errorState: source.qaErrorState }))),
        historyPages: stand.requests.filter((request) => request.path.endsWith('/history')).length,
      });
      console.log(`PASS ${name}`);
    } catch (error) {
      results.push({ name, result: 'FAIL', error: error.message, documents });
      await page.screenshot({ path: path.join(output, `${name}-FAILED.png`), fullPage: true }).catch(() => {});
      throw error;
    } finally {
      await context.close();
      stand.closeStreams();
      stand.server.closeAllConnections();
      await new Promise((resolve) => stand.server.close(resolve));
    }
  }

  async function open({ page, stand, origin }) {
    await page.goto(`${origin}/client/applications/${stand.app().id}`);
    await page.getByRole('heading', { name: texts.statuses.MANUAL_REVIEW.title, exact: true }).waitFor();
    await page.waitForFunction(() => window.qaSources.some((source) => source.readyState === 1));
  }
  async function restored({ page, stand, documents }) {
    await page.getByRole('button', { name: texts.decision.approved.accept, exact: true }).waitFor({ timeout: 20_000 });
    await page.getByText(texts.errors.liveUpdatesLost, { exact: true }).waitFor({ state: 'hidden' });
    await page.waitForFunction(() => window.qaSources.filter((source) => source.readyState !== 2).length === 1);
    assert.equal(documents(), 1, 'No document reload during recovery');
    await until(() => stand.streams.size === 1, 'One active server SSE stream');
    await until(() => stand.requests.some((request) => request.path.endsWith('/history') &&
      request.revision === stand.app().revision && request.cursor), 'All history pages reread after recovery');
  }

  try {
    await scenario('network-offline', async (cfg) => {
      await open(cfg);
      await cfg.context.setOffline(true);
      cfg.stand.closeStreams();
      await cfg.page.getByText(texts.errors.liveUpdatesLost, { exact: true }).waitFor();
      await cfg.page.screenshot({ path: path.join(output, 'network-offline-disconnected.png'), fullPage: true });
      await cfg.page.getByRole('heading', { name: texts.statuses.MANUAL_REVIEW.title, exact: true }).waitFor();
      cfg.stand.approve();
      await cfg.context.setOffline(false);
      await restored(cfg);
    });
    await scenario('native-connecting', async (cfg) => {
      await open(cfg);
      cfg.stand.approve();
      cfg.stand.closeStreams();
      await restored(cfg);
      assert.equal(await cfg.page.evaluate(() => window.qaSources.length), 1, 'Native reconnect retains the source');
      assert(cfg.stand.requests.some((request) => request.lastId), 'Browser sends Last-Event-ID');
    });
    await scenario('closed-502', async (cfg) => {
      await open(cfg);
      cfg.stand.outage(true);
      await cfg.page.getByText(texts.errors.liveUpdatesLost, { exact: true }).waitFor();
      await cfg.page.waitForFunction(() => window.qaSources.some((source) => source.qaErrorState === 2));
      await cfg.page.screenshot({ path: path.join(output, 'closed-502-disconnected.png'), fullPage: true });
      // The previous snapshot remains usable while HTTP reads fail.
      await cfg.page.getByRole('heading', { name: texts.statuses.MANUAL_REVIEW.title, exact: true }).waitFor();
      await cfg.page.getByRole('button', { name: texts.applications.cancel, exact: true }).click();
      const dialog = cfg.page.getByRole('dialog');
      await dialog.getByRole('button', { name: texts.applications.cancelConfirm, exact: true }).click();
      await dialog.getByText(texts.errors.generic, { exact: true }).waitFor();
      assert(!cfg.stand.requests.some((r) => r.path.endsWith('/cancel')), 'Failed CSRF read prevents mutation');
      await dialog.getByRole('button', { name: texts.common.cancel, exact: true }).click();
      cfg.stand.approve();
      cfg.stand.outage(false);
      await restored(cfg);
      assert(await cfg.page.evaluate(() => window.qaSources.length > 1), 'CLOSED source replaced');
      await cfg.page.getByRole('button', { name: texts.common.logout, exact: true }).click();
      await cfg.page.getByRole('button', { name: texts.login.submit, exact: true }).waitFor();
      await until(() => cfg.stand.streams.size === 0, 'Logout closes the stream');
    });
    await scenario('closed-stream-only', async (cfg) => {
      await open(cfg);
      cfg.stand.eventsUnavailable(true);
      await cfg.page.getByText(texts.errors.liveUpdatesLost, { exact: true }).waitFor();
      await cfg.page.waitForFunction(() => window.qaSources.some((source) => source.qaErrorState === 2));
      cfg.stand.approve();
      cfg.stand.eventsUnavailable(false);
      await restored(cfg);
    });

    for (const width of [375, 1280]) {
      await scenario(`bank-${width}`, async ({ page, stand, origin }) => {
        stand.setRole('underwriter');
        await page.goto(`${origin}/bank`);
        await page.getByRole('heading', { name: texts.bank.stats.title, exact: true }).waitFor();
        await assertFits(page);
        await page.screenshot({ path: path.join(output, `bank-queue-${width}.png`), fullPage: true });
        await page.getByRole('row').filter({ hasText: fixture.bank.application.company_name }).click();
        await page.getByText(texts.manualReviewReasons.gray_zone.title, { exact: true }).waitFor();
        await page.getByText(texts.manualReviewReasons.gray_zone.explanation, { exact: true }).waitFor();
        await assertFits(page);
        await page.screenshot({ path: path.join(output, `bank-card-${width}.png`), fullPage: true });
        await page.getByLabel(texts.bank.decision.auditCommentLabel).fill('Синтетическая проверка решения аналитика');
        await page.getByRole('button', { name: texts.bank.decision.submit, exact: true }).click();
        await until(() => stand.requests.some((r) => r.path.endsWith('/decision')), 'Manual decision sent');
        const decision = stand.requests.find((r) => r.path.endsWith('/decision')).body;
        assert.equal(decision.expected_revision, fixture.bank.application.revision);
        assert.equal(decision.outcome, 'APPROVED');
      }, { width, height: 900 });
      await scenario(`sign-${width}`, async ({ page, stand, origin }) => {
        stand.setApp({ ...copy(fixture.approved), status: 'OFFER_ACCEPTED', allowed_actions: ['sign'] });
        await page.goto(`${origin}/client/applications/${stand.app().id}/sign`);
        await page.getByRole('checkbox', { name: texts.signing.confirmCheckbox, exact: true }).check();
        await page.getByRole('button', { name: texts.signing.getCode, exact: true }).click();
        await assertFits(page);
        await page.screenshot({ path: path.join(output, `sign-code-${width}.png`), fullPage: true });
        const code = (await page.getByText(/^\d{3} \d{3}$/).textContent()).replace(/\D/g, '');
        await page.getByLabel(texts.signing.codeLabel, { exact: true }).fill(code === '000000' ? '111111' : '000000');
        await page.getByRole('button', { name: texts.signing.sign, exact: true }).click();
        await page.getByText(texts.signing.codeMismatch, { exact: true }).waitFor();
        assert(!stand.requests.some((r) => r.path.endsWith('/sign')), 'Wrong code does not submit');
        await page.getByLabel(texts.signing.codeLabel, { exact: true }).fill(code);
        await page.getByRole('button', { name: texts.signing.sign, exact: true }).click();
        await page.getByRole('heading', { name: texts.statuses.ISSUED.title, exact: true }).waitFor();
        await assertFits(page);
        const sign = stand.requests.find((r) => r.path.endsWith('/sign')).body;
        assert.equal(sign.consent, true);
        assert.equal(sign.offer_id, fixture.approved.decision.offer.id);
      }, { width, height: 900 });
      await scenario(`additional-info-${width}`, async ({ page, stand, origin }) => {
        const app = copy(fixture.needsInfo);
        app.decision.required_documents = ['annual_revenue', 'bank_statement_12m'].map((code) => ({
          code, title: texts.requiredDocuments[code].title, explanation: texts.requiredDocuments[code].explanation,
        }));
        stand.setApp(app);
        await page.goto(`${origin}/client/applications/${app.id}`);
        await page.getByLabel(texts.form.annualRevenue.label, { exact: true }).fill('1200000');
        await page.locator('input[type=file]').setInputFiles({ name: 'statement.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\nsynthetic') });
        await page.getByText(new RegExp(`${texts.decision.needsInfo.uploaded}: statement.pdf`)).waitFor();
        await page.getByLabel(texts.decision.needsInfo.commentLabel, { exact: true }).fill('Синтетические данные для QA');
        await assertFits(page);
        await page.screenshot({ path: path.join(output, `additional-info-form-${width}.png`), fullPage: true });
        await page.getByRole('button', { name: texts.decision.needsInfo.submit, exact: true }).click();
        await page.getByRole('button', { name: texts.decision.approved.accept, exact: true }).waitFor();
        const request = stand.requests.find((r) => r.path.endsWith('/additional-info'));
        assert.equal(request.body.expected_revision, fixture.needsInfo.revision + 1);
        assert.equal(request.body.annual_revenue, '1200000.00');
        assert.deepEqual(request.body.document_ids, ['60000000-0000-4000-8000-000000000001']);
        const upload = stand.requests.find((r) => r.method === 'POST' && r.path.endsWith('/documents'));
        assert(upload.multipart.includes('statement.pdf') && upload.multipart.includes('bank_statement_12m'), 'Multipart document and type');
      }, { width, height: 900 });
    }
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({
      scope: 'Production React + native Chromium; synthetic HTTP/SSE fixture, not backend/Docker acceptance',
      browser: browser.version(), checkedAt: new Date().toISOString(), results,
    }, null, 2));
  }
}

async function assertFits(page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  assert.equal(overflow, false, 'No horizontal page overflow');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
