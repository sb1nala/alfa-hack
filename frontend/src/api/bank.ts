import { useQuery } from '@tanstack/react-query';
import { api } from './index.ts';
import { ApiProtocolError } from './errors.ts';
import type { components } from './schema.d.ts';

export type UnderwriterApplication = components['schemas']['UnderwriterApplication'];
export type BankDecision = components['schemas']['BankDecision'];

export const bankKeys = {
  queue: (status: string) => ['underwriter', 'queue', status] as const,
  card: (id: string) => ['underwriter', 'card', id] as const,
};

/** Очередь аналитика: по умолчанию MANUAL_REVIEW, «all» — все заявки (для демо). */
export function useUnderwriterQueue(filter: 'MANUAL_REVIEW' | 'all') {
  return useQuery({
    queryKey: bankKeys.queue(filter),
    queryFn: async ({ signal }) => {
      const { data } = await api.http.GET('/api/underwriter/applications', {
        params: { query: { limit: 100, ...(filter === 'all' ? {} : { status: filter }) } },
        signal,
      });
      if (!data) throw new ApiProtocolError();
      return data;
    },
    refetchInterval: 10_000,
  });
}

export function useUnderwriterApplication(id: string) {
  return useQuery({
    queryKey: bankKeys.card(id),
    queryFn: async ({ signal }) => {
      const { data } = await api.http.GET('/api/underwriter/applications/{application_id}', {
        params: { path: { application_id: id } },
        signal,
      });
      if (!data) throw new ApiProtocolError();
      return data;
    },
  });
}
