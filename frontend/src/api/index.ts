import { createApiClient } from './client.ts';

/** В браузере все запросы идут на /api того же origin, адрес сервиса задаёт прокси. */
export const api = createApiClient({ baseUrl: window.location.origin });

export { createApiClient } from './client.ts';
export type { ApiClient } from './client.ts';
export { ApiError, ApiNetworkError, ApiProtocolError } from './errors.ts';
export type { components, paths } from './schema.d.ts';
