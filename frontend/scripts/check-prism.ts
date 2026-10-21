import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer as createNetServer } from 'node:net';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { createServer as createViteServer } from 'vite';
import { createApiClient } from '../src/api/client.ts';
import { ApiError } from '../src/api/errors.ts';

// Изолированный smoke: реальный Prism + реальный Vite-прокси, без БД и других сервисов.
const reservation = createNetServer();
reservation.listen(0, '127.0.0.1');
await once(reservation, 'listening');
const address = reservation.address();
assert.ok(address && typeof address !== 'string');
const port = address.port;
await new Promise<void>((done, reject) => reservation.close((error) => error ? reject(error) : done()));

const prism = spawn(process.execPath, [
  resolve('node_modules/@stoplight/prism-cli/dist/index.js'),
  'mock', '../docs/openapi.yaml', '--host', '127.0.0.1', '--port', String(port),
], { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
let output = '';
prism.stdout.on('data', (chunk: Buffer) => { output = (output + chunk.toString()).slice(-6000); });
prism.stderr.on('data', (chunk: Buffer) => { output = (output + chunk.toString()).slice(-6000); });
let spawnError: Error | undefined;
prism.on('error', (error) => { spawnError = error; });
const oldTarget = process.env.API_PROXY_TARGET;
let vite: Awaited<ReturnType<typeof createViteServer>> | undefined;

try {
  const deadline = Date.now() + 30_000;
  let ready = false;
  while (Date.now() < deadline) {
    if (spawnError) throw spawnError;
    if (prism.exitCode !== null) throw new Error(`Prism завершился: ${output}`);
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/auth/csrf`, { signal: AbortSignal.timeout(1000) });
      if (response.ok) { ready = true; break; }
    } catch { /* Prism ещё запускается. */ }
    await delay(100);
  }
  assert.ok(ready, `Prism не запустился за 30 секунд: ${output}`);

  // Единственная переменная, которую меняет разработчик при переключении сервиса.
  process.env.API_PROXY_TARGET = `http://127.0.0.1:${port}`;
  vite = await createViteServer({
    configFile: resolve('vite.config.ts'), logLevel: 'error',
    server: { host: '127.0.0.1', port: 0, strictPort: false, open: false },
  });
  await vite.listen();
  const proxyAddress = vite.httpServer?.address();
  assert.ok(proxyAddress && typeof proxyAddress !== 'string');
  const api = createApiClient({
    baseUrl: `http://127.0.0.1:${proxyAddress.port}`,
    fetch: (request) => {
      // Node fetch не имеет браузерного cookie-jar. Prism проверяет только наличие
      // cookie, а не настоящую сессию. Эти значения синтетические и нигде не сохраняются.
      const headers = new Headers(request.headers);
      headers.set('Cookie', 'access_token=synthetic-session; csrf_context=synthetic-context');
      return fetch(new Request(request, { headers, signal: AbortSignal.timeout(5000) }));
    },
  });

  const products = await api.http.GET('/api/products');
  assert.ok(products.data?.items.length);
  const lookup = await api.http.GET('/api/companies/lookup', { params: { query: { inn: '0086906651' } } });
  assert.ok(lookup.data?.company?.inn);
  assert.equal((await api.auth.login({ login: 'demo-approve', password: 'demo-2026' })).role, 'client');
  assert.equal((await api.auth.me()).role, 'client');
  const draft = await api.http.POST('/api/applications', { body: {} });
  assert.ok(draft.data?.id);
  const card = await api.http.GET('/api/applications/{application_id}', {
    params: { path: { application_id: draft.data.id } },
  });
  assert.ok(card.data?.status);
  assert.ok(Array.isArray(card.data.allowed_actions));
  await assert.rejects(api.http.GET('/api/products', { headers: { Prefer: 'code=501' } }),
    (error: unknown) => error instanceof ApiError && error.status === 501);
  await api.auth.logout();
  console.log('Prism → Vite /api → typed client: каталог, ИНН, login/me/logout, черновик, карточка и 501 — OK');
} finally {
  if (oldTarget === undefined) delete process.env.API_PROXY_TARGET;
  else process.env.API_PROXY_TARGET = oldTarget;
  await vite?.close();
  if (prism.exitCode === null && !spawnError) {
    const exited = once(prism, 'exit');
    prism.kill();
    await Promise.race([exited, delay(3000)]);
  }
}
