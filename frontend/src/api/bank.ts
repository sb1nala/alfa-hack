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

type ApplicationStatus = components['schemas']['ApplicationStatus'];

/** «Все заявки» для аналитика — всё, что клиент отправил; черновики банку не нужны. */
export const SUBMITTED_STATUSES: ApplicationStatus[] = [
  'SUBMITTED', 'CHECKING', 'SCORING', 'NEEDS_INFO', 'MANUAL_REVIEW', 'APPROVED', 'COUNTER_OFFER',
  'REJECTED', 'OFFER_ACCEPTED', 'SIGNED', 'ISSUED', 'CANCELLED',
];

/** Очередь аналитика: по умолчанию MANUAL_REVIEW, «all» — все отправленные заявки. */
export function useUnderwriterQueue(filter: 'MANUAL_REVIEW' | 'all') {
  return useQuery({
    queryKey: bankKeys.queue(filter),
    queryFn: async ({ signal }) => {
      const { data } = await api.http.GET('/api/underwriter/applications', {
        params: { query: { limit: 100, status: filter === 'all' ? SUBMITTED_STATUSES : ['MANUAL_REVIEW'] } },
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
