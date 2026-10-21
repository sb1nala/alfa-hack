import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createApiClient } from './client.ts';
import { ApiError, ApiNetworkError, ApiProtocolError } from './errors.ts';
import { uploadDocument } from './documents.ts';

const baseUrl = 'http://frontend.test';
const user = { id: 'user-1', login: 'demo-approve', display_name: 'Демо', role: 'client' };
const json = (value: unknown, status = 200) => Response.json(value, { status });
const token = (value = 'token-1') => json({ csrf_token: value });
const path = (request: Request) => new URL(request.url).pathname;
const problem = (code: string) => ({ code, message: 'Ошибка запроса', field_errors: [], request_id: 'request-1' });

test('GET сериализует query и всегда использует cookie того же origin без CSRF', async () => {
  const api = createApiClient({ baseUrl, fetch: async (request) => {
    assert.equal(request.method, 'GET');
    assert.equal(request.credentials, 'same-origin');
    assert.equal(request.cache, 'no-store');
    assert.equal(request.headers.has('X-CSRF-Token'), false);
    assert.equal(new URL(request.url).searchParams.get('status'), 'DRAFT');
    assert.equal(new URL(request.url).searchParams.get('offset'), '10');
    return json({ items: [], total: 0, offset: 10, limit: 20 });
  } });
  const { data } = await api.http.GET('/api/applications', {
    params: { query: { status: 'DRAFT', offset: 10 } }, credentials: 'omit',
  });
  assert.equal(data?.total, 0);
});

test('мутации разделяют один bootstrap и получают CSRF; JSON сериализуется', async () => {
  let bootstraps = 0;
  let mutations = 0;
  const api = createApiClient({ baseUrl, fetch: async (request) => {
    if (path(request) === '/api/auth/csrf') {
      bootstraps += 1;
      await new Promise((resolve) => setTimeout(resolve, 10));
      return token();
    }
    mutations += 1;
    assert.equal(request.credentials, 'same-origin');
    assert.equal(request.headers.get('X-CSRF-Token'), 'token-1');
    assert.equal(request.headers.get('Content-Type'), 'application/json');
    assert.deepEqual(await request.json(), { amount: '100000.00' });
    return json({ id: 'draft-1' }, 201);
  } });
  await Promise.all([1, 2, 3].map(() => api.http.POST('/api/applications', { body: { amount: '100000.00' } })));
  assert.equal(bootstraps, 1);
  assert.equal(mutations, 3);
});

test('login и logout ротируют CSRF; me и 204 обрабатываются без JSON тела', async () => {
  let bootstraps = 0;
  const seen: string[] = [];
  const api = createApiClient({ baseUrl, fetch: async (request) => {
    seen.push(`${request.method} ${path(request)}`);
    if (path(request) === '/api/auth/csrf') return token(`token-${++bootstraps}`);
    if (request.method === 'POST') {
      assert.equal(request.headers.get('X-CSRF-Token'), `token-${bootstraps}`);
      return path(request).endsWith('logout') ? new Response(null, { status: 204 }) : json(user);
    }
    return json(user);
  } });
  assert.equal((await api.auth.login({ login: 'demo-approve', password: 'synthetic-password' })).role, 'client');
  assert.equal((await api.auth.me()).login, 'demo-approve');
  await api.auth.logout();
  assert.equal(bootstraps, 3);
  assert.deepEqual(seen, [
    'GET /api/auth/csrf', 'POST /api/auth/login', 'GET /api/auth/csrf',
    'GET /api/auth/me', 'POST /api/auth/logout', 'GET /api/auth/csrf',
  ]);
});

for (const status of [401, 403, 409, 422, 501]) {
  test(`HTTP ${status}: code, field_errors и request_id сохраняются`, async () => {
    const field = { field: 'body.amount', code: 'invalid_amount', message: 'Проверьте сумму' };
    const api = createApiClient({ baseUrl, fetch: async () => json({ ...problem('test_error'), field_errors: [field] }, status) });
    await assert.rejects(api.http.GET('/api/products'), (error: unknown) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, status);
      assert.equal(error.code, 'test_error');
      assert.equal(error.requestId, 'request-1');
      assert.deepEqual(error.fieldErrors, [field]);
      return true;
    });
  });
}

test('HTML/невалидная ошибка не выдаётся пользователю как сообщение', async () => {
  const api = createApiClient({ baseUrl, fetch: async () => new Response('<html>private detail</html>', {
    status: 502, headers: { 'X-Request-ID': 'proxy-1' },
  }) });
  await assert.rejects(api.http.GET('/api/products'), (error: unknown) => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.requestId, 'proxy-1');
    assert.equal(error.code, 'http_error');
    assert.ok(!error.message.includes('private detail'));
    return true;
  });
});

test('сетевая ошибка отделена от HTTP; AbortError остаётся отменой', async () => {
  const offline = createApiClient({ baseUrl, fetch: async () => { throw new TypeError('fetch failed'); } });
  await assert.rejects(offline.http.GET('/api/products'), ApiNetworkError);
  const cancelled = createApiClient({ baseUrl, fetch: async () => { throw new DOMException('cancelled', 'AbortError'); } });
  await assert.rejects(cancelled.http.GET('/api/products'), { name: 'AbortError' });
  const controller = new AbortController();
  controller.abort();
  let called = false;
  const api = createApiClient({ baseUrl, fetch: async () => { called = true; return json({}); } });
  await assert.rejects(api.http.GET('/api/products', { signal: controller.signal }), { name: 'AbortError' });
  assert.equal(called, false);
});

test('ошибка bootstrap не отправляет мутацию и не блокирует следующую попытку', async () => {
  let bootstraps = 0;
  let mutations = 0;
  const api = createApiClient({ baseUrl, fetch: async (request) => {
    if (path(request) === '/api/auth/csrf') return ++bootstraps === 1 ? json({}, 503) : token();
    mutations += 1;
    return json({}, 201);
  } });
  await assert.rejects(api.http.POST('/api/applications', { body: {} }), ApiError);
  assert.equal(mutations, 0);
  await api.http.POST('/api/applications', { body: {} });
  assert.equal(bootstraps, 2);
  assert.equal(mutations, 1);
});

test('пустой CSRF-токен отклоняется до мутации', async () => {
  const api = createApiClient({ baseUrl, fetch: async () => token('') });
  await assert.rejects(api.http.POST('/api/applications', { body: {} }), ApiProtocolError);
});

test('после 401/403 токен очищается; неуспешная мутация не повторяется автоматически', async () => {
  for (const status of [401, 403]) {
    let bootstraps = 0;
    let mutations = 0;
    const api = createApiClient({ baseUrl, fetch: async (request) => {
      if (path(request) === '/api/auth/csrf') return token(`token-${++bootstraps}`);
      mutations += 1;
      assert.equal(request.headers.get('X-CSRF-Token'), `token-${bootstraps}`);
      return mutations === 1 ? json(problem('access_error'), status) : json({}, 201);
    } });
    await assert.rejects(api.http.POST('/api/applications', { body: {} }), ApiError);
    assert.equal(mutations, 1);
    await api.http.POST('/api/applications', { body: {} });
    assert.equal(bootstraps, 2);
    assert.equal(mutations, 2);
  }
});

test('submit сохраняет Idempotency-Key и expected_revision, без повторов на 409', async () => {
  let submits = 0;
  const api = createApiClient({ baseUrl, fetch: async (request) => {
    if (path(request) === '/api/auth/csrf') return token();
    submits += 1;
    assert.equal(request.headers.get('Idempotency-Key'), 'attempt-1');
    assert.deepEqual(await request.json(), { expected_revision: 3, consent_to_checks: true });
    return json(problem('revision_conflict'), 409);
  } });
  await assert.rejects(api.http.POST('/api/applications/{application_id}/submit', {
    params: { path: { application_id: 'draft-1' }, header: { 'Idempotency-Key': 'attempt-1' } },
    body: { expected_revision: 3, consent_to_checks: true },
  }), ApiError);
  assert.equal(submits, 1);
});

test('multipart передаёт File, тип документа, ревизию и автоматически ставит boundary', async () => {
  const api = createApiClient({ baseUrl, fetch: async (request) => {
    if (path(request) === '/api/auth/csrf') return token();
    assert.match(request.headers.get('Content-Type') ?? '', /^multipart\/form-data; boundary=/);
    assert.equal(request.headers.get('X-CSRF-Token'), 'token-1');
    const form = await request.formData();
    assert.equal(form.get('document_type'), 'bank_statement');
    assert.equal(form.get('expected_revision'), '2');
    const file = form.get('file');
    assert.ok(file instanceof File);
    assert.equal(file.name, 'demo.pdf');
    assert.equal(await file.text(), 'synthetic');
    return json({ id: 'document-1' }, 201);
  } });
  await uploadDocument(api, 'draft-1', {
    file: new File(['synthetic'], 'demo.pdf', { type: 'application/pdf' }),
    document_type: 'bank_statement', expected_revision: 2,
  });
});

test('бинарная загрузка не пытается разобрать файл как JSON', async () => {
  const api = createApiClient({ baseUrl, fetch: async () => new Response('synthetic document', {
    headers: { 'Content-Type': 'application/octet-stream' },
  }) });
  const { data } = await api.http.GET('/api/applications/{application_id}/documents/{document_id}/download', {
    params: { path: { application_id: 'draft-1', document_id: 'document-1' } }, parseAs: 'blob',
  });
  assert.equal(await data?.text(), 'synthetic document');
});

test('переопределение запроса на чужой origin отклоняется до bootstrap', async () => {
  let called = false;
  const api = createApiClient({ baseUrl, fetch: async () => { called = true; return token(); } });
  await assert.rejects(api.http.POST('/api/applications', { body: {}, baseUrl: 'http://other.test' }));
  assert.equal(called, false);
});

test('ответ старого bootstrap после сброса не возвращает устаревший токен', async () => {
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let started: (() => void) | undefined;
  const ready = new Promise<void>((resolve) => { started = resolve; });
  let bootstraps = 0;
  const api = createApiClient({ baseUrl, fetch: async () => {
    const version = ++bootstraps;
    if (version === 1) { started?.(); await gate; }
    return token(`token-${version}`);
  } });
  const previous = api.getCsrfToken();
  await ready;
  api.resetCsrfToken();
  assert.equal(await api.getCsrfToken(), 'token-2');
  release?.();
  assert.equal(await previous, 'token-2');
  assert.equal(bootstraps, 2);
});

test('отмена во время общего bootstrap не отправляет отменённую мутацию', async () => {
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let started: (() => void) | undefined;
  const ready = new Promise<void>((resolve) => { started = resolve; });
  let mutations = 0;
  const api = createApiClient({ baseUrl, fetch: async (request) => {
    if (path(request) === '/api/auth/csrf') { started?.(); await gate; return token(); }
    mutations += 1;
    return json({}, 201);
  } });
  const controller = new AbortController();
  const cancelled = api.http.POST('/api/applications', { body: {}, signal: controller.signal });
  await ready;
  controller.abort();
  release?.();
  await assert.rejects(cancelled, { name: 'AbortError' });
  assert.equal(mutations, 0);
  await api.http.POST('/api/applications', { body: {} });
  assert.equal(mutations, 1);
});
