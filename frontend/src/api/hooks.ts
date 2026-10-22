import { useQuery } from '@tanstack/react-query';
import { api } from './index.ts';
import { ApiProtocolError } from './errors.ts';
import type { components } from './schema.d.ts';
import { FINAL, PROCESSING } from '../lib/timeline.ts';

export type ClientApplication = components['schemas']['ClientApplication'];
export type Product = components['schemas']['Product'];

function required<T>(data: T | undefined): T {
  if (data === undefined) throw new ApiProtocolError();
  return data;
}

export const keys = {
  me: ['me'] as const,
  products: ['products'] as const,
  applications: ['applications'] as const,
  application: (id: string) => ['applications', id] as const,
  lookup: (inn: string) => ['lookup', inn] as const,
};

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: ({ signal }) => api.auth.me(signal),
    retry: false,
    staleTime: 60_000,
  });
}

export function useProducts() {
  return useQuery({
    queryKey: keys.products,
    queryFn: async ({ signal }) => required((await api.http.GET('/api/products', { signal })).data).items,
    staleTime: 10 * 60_000,
  });
}

export function useApplications() {
  return useQuery({
    queryKey: keys.applications,
    queryFn: async ({ signal }) =>
      required((await api.http.GET('/api/applications', { params: { query: { limit: 100 } }, signal })).data),
  });
}

/**
 * Пока на бэкенде нет SSE (B09), статус обновляется опросом: часто — пока заявку
 * обрабатывает система, реже — пока ждём аналитика или клиента, никогда — в конечных статусах.
 */
export function useApplication(id: string) {
  return useQuery({
    queryKey: keys.application(id),
    queryFn: async ({ signal }) =>
      required((await api.http.GET('/api/applications/{application_id}', {
        params: { path: { application_id: id } },
        signal,
      })).data),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (!status || FINAL.has(status) || status === 'DRAFT') return false;
      return PROCESSING.has(status) ? 2_000 : 5_000;
    },
    refetchIntervalInBackground: false,
  });
}

export function productById(products: Product[] | undefined, id: string | null | undefined) {
  return id ? products?.find((p) => p.id === id) : undefined;
}

/** Idempotency-Key: crypto.randomUUID есть не во всех контекстах (например, http по IP). */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
