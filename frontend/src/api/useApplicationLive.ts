import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from './index.ts';
import { ApiProtocolError, type ApiError } from './errors.ts';
import { keys, type ClientApplication } from './hooks.ts';
import { newestRevision, readApplicationHistory, watchApplication } from './applicationLive.ts';
import { FINAL } from '../lib/timeline.ts';

export function useApplicationLive(id: string, application: ClientApplication | undefined) {
  const cache = useQueryClient();
  const enabled = !!application && application.status !== 'DRAFT' && !FINAL.has(application.status);
  const [state, setState] = useState<{ id: string; lost: boolean; error?: ApiError }>({ id, lost: false });
  useEffect(() => {
    setState({ id, lost: false });
    if (!enabled) return;
    return watchApplication({
      id,
      refresh: async (signal, restoreHistory) => {
        const { data } = await api.http.GET('/api/applications/{application_id}', {
          params: { path: { application_id: id } }, signal,
        });
        signal.throwIfAborted();
        if (!data) throw new ApiProtocolError();
        cache.setQueryData<ClientApplication>(keys.application(id), (previous) => newestRevision(previous, data));
        if (restoreHistory) {
          const items = await readApplicationHistory(signal, async (cursor) => (
            await api.http.GET('/api/applications/{application_id}/history', {
              params: { path: { application_id: id }, query: { limit: 100, cursor } }, signal,
            })
          ).data);
          cache.setQueryData(keys.history(id), items);
        }
      },
      onConnection: (lost) => setState({ id, lost }),
      onFatal: (error) => {
        setState({ id, lost: false, error });
        if (error.status === 401) {
          void cache.invalidateQueries({ queryKey: keys.me });
        }
      },
    });
  }, [id, enabled, cache]);
  return state.id === id ? state : { id, lost: false };
}
