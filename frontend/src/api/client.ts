import createClient from 'openapi-fetch';
import type { components, paths } from './schema.d.ts';
import { ApiError, ApiNetworkError, ApiProtocolError } from './errors.ts';

type ClientOptions = {
  /** Origin фронтенда; /api уже включён в пути OpenAPI. */
  baseUrl: string;
  fetch?: (request: Request) => Promise<Response>;
};

export function createApiClient(options: ClientOptions) {
  const origin = new URL(options.baseUrl).origin;
  let csrfToken: string | undefined;
  let csrfRequest: Promise<string> | undefined;
  let generation = 0;

  const http = createClient<paths>({
    baseUrl: origin,
    credentials: 'same-origin',
    cache: 'no-store',
    fetch: options.fetch ?? globalThis.fetch,
  });

  function resetCsrfToken() {
    csrfToken = undefined;
    csrfRequest = undefined;
    generation += 1;
  }

  /** Параллельные мутации делят один запрос bootstrap. Токен живёт только в памяти. */
  async function getCsrfToken(): Promise<string> {
    if (csrfToken) return csrfToken;
    if (csrfRequest) return csrfRequest;
    const version = generation;
    const pending = (async () => {
      const { data } = await http.GET('/api/auth/csrf');
      if (!data || typeof data.csrf_token !== 'string' || !data.csrf_token) {
        throw new ApiProtocolError();
      }
      // Ответ контекста, инвалидированного за время запроса, использовать нельзя.
      if (version !== generation) return getCsrfToken();
      csrfToken = data.csrf_token;
      return csrfToken;
    })();
    csrfRequest = pending;
    try {
      return await pending;
    } finally {
      if (csrfRequest === pending) csrfRequest = undefined;
    }
  }

  http.use({
    async onRequest({ request }) {
      // Не отправлять cookie или CSRF при случайном переопределении baseUrl на чужой origin.
      if (new URL(request.url).origin !== origin) {
        throw new Error('API-запрос должен использовать origin фронтенда');
      }
      request.signal.throwIfAborted();
      if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method)) {
        request.headers.set('X-CSRF-Token', await getCsrfToken());
      }
      request.signal.throwIfAborted();
      return new Request(request, { credentials: 'same-origin', cache: 'no-store' });
    },
    async onResponse({ response, schemaPath, request }) {
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) resetCsrfToken();
        const payload: unknown = await response.clone().json().catch(() => null);
        // Не повторяем мутации автоматически: submit/sign могут уже иметь побочный эффект.
        throw new ApiError(response.status, payload, response.headers.get('X-Request-ID'));
      }
      if (request.method === 'POST' &&
          (schemaPath === '/api/auth/login' || schemaPath === '/api/auth/logout')) {
        resetCsrfToken();
        await getCsrfToken();
      }
    },
    onError({ error, request }) {
      if (request.signal.aborted || (error instanceof Error && error.name === 'AbortError')) {
        return error instanceof Error ? error : new DOMException('Запрос отменён', 'AbortError');
      }
      return new ApiNetworkError();
    },
  });

  const auth = {
    async login(body: components['schemas']['LoginRequest']) {
      const { data } = await http.POST('/api/auth/login', { body });
      if (!data) throw new ApiProtocolError();
      return data;
    },
    async logout() {
      await http.POST('/api/auth/logout');
    },
    async me(signal?: AbortSignal) {
      const { data } = await http.GET('/api/auth/me', { signal });
      if (!data) throw new ApiProtocolError();
      return data;
    },
  };

  return { http, auth, getCsrfToken, resetCsrfToken };
}

export type ApiClient = ReturnType<typeof createApiClient>;
