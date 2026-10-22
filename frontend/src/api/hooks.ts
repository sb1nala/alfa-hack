import { useQuery } from '@tanstack/react-query';
import { api } from './index.ts';
import { ApiProtocolError } from './errors.ts';
import type { components } from './schema.d.ts';
import { FINAL, PROCESSING } from '../lib/timeline.ts';
import { newestRevision } from './applicationLive.ts';

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
  history: (id: string) => ['application-history', id] as const,
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
 * Polling is retained for action screens; the status page uses SSE instead.
 */
export function useApplication(id: string, live = false) {
  return useQuery({
    queryKey: keys.application(id),
    structuralSharing: (previous, incoming) => newestRevision(previous as ClientApplication | undefined, incoming as ClientApplication),
    queryFn: async ({ signal }) =>
      required((await api.http.GET('/api/applications/{application_id}', {
        params: { path: { application_id: id } },
        signal,
      })).data),
    refetchInterval: (query) => {
      if (live) return false;
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
