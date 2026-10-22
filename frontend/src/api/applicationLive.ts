import { ApiError, ApiProtocolError } from './errors.ts';
import type { components } from './schema.d.ts';

export const APPLICATION_EVENTS = ['status_changed', 'application_updated', 'decision_created', 'document_uploaded'] as const;
type Source = Pick<EventTarget, 'addEventListener' | 'removeEventListener'> & Pick<EventSource, 'close' | 'readyState'>;
const activeStreams = new Set<() => void>();

export function closeApplicationStreams() {
  for (const stop of [...activeStreams]) stop();
}

/** Events invalidate the snapshot; decisions and allowed actions always come from GET. */
export function watchApplication(options: {
  id: string;
  refresh: (signal: AbortSignal, restoreHistory: boolean) => Promise<void>;
  onConnection: (lost: boolean) => void;
  onFatal: (error: ApiError) => void;
  createSource?: (url: string) => Source;
  retryMs?: number;
}) {
  const lifetime = new AbortController();
  let source: Source | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  let running = false;
  let pending = false;
  let pendingHistory = false;
  let lost = false;
  const seen = new Set<string>();
  const retryMs = options.retryMs ?? 3_000;

  function stop() {
    if (stopped) return;
    stopped = true;
    lifetime.abort();
    clearTimeout(timer);
    detach();
    activeStreams.delete(stop);
  }

  function detach() {
    source?.removeEventListener('open', opened);
    source?.removeEventListener('error', disconnected);
    for (const name of APPLICATION_EVENTS) source?.removeEventListener(name, changed);
    source?.close();
    source = undefined;
  }

  function schedule() {
    if (stopped || timer !== undefined) return;
    timer = setTimeout(() => { timer = undefined; void refresh(true); }, retryMs);
  }

  async function refresh(restoreHistory = false) {
    if (stopped) return;
    if (running) { pending = true; pendingHistory ||= restoreHistory; return; }
    running = true;
    try {
      await options.refresh(AbortSignal.any([lifetime.signal, AbortSignal.timeout(10_000)]), restoreHistory);
      if (stopped) return;
      if (source?.readyState === 1) {
        lost = false;
        clearTimeout(timer);
        timer = undefined;
        options.onConnection(false);
      }
      // Native reconnect retains Last-Event-ID. Only replace a permanently CLOSED source.
      if (source?.readyState === 2) connect();
    } catch (error) {
      if (stopped) return;
      if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
        stop();
        options.onFatal(error);
        return;
      }
      lost = true;
      options.onConnection(true);
    } finally {
      running = false;
      if (!stopped) {
        if (pending) {
          const history = pendingHistory;
          pending = false;
          pendingHistory = false;
          void refresh(history);
        }
        else if (lost) schedule();
      }
    }
  }

  function opened() {
    if (stopped) return;
    lost = false;
    clearTimeout(timer);
    timer = undefined;
    options.onConnection(false);
    // Also runs on native reconnect, covering gaps and expired cursors.
    void refresh(true);
  }

  function disconnected() {
    if (stopped) return;
    lost = true;
    options.onConnection(true);
    void refresh(true);
  }

  function changed(event: Event) {
    if (stopped || !(event instanceof MessageEvent)) return;
    try {
      const data: unknown = JSON.parse(String(event.data));
      if (!data || typeof data !== 'object' || !('application_id' in data) ||
        data.application_id !== options.id || !('revision' in data) ||
        !Number.isSafeInteger(data.revision) || Number(data.revision) < 1 ||
        !('event_type' in data) || data.event_type !== event.type) return;
    } catch { return; }
    if (event.lastEventId) {
      if (seen.has(event.lastEventId)) return;
      seen.add(event.lastEventId);
      if (seen.size > 256) seen.delete(seen.values().next().value!);
    }
    void refresh();
  }

  function connect() {
    detach();
    source = (options.createSource ?? ((url) => new EventSource(url)))(
      `/api/applications/${encodeURIComponent(options.id)}/events`,
    );
    source.addEventListener('open', opened);
    source.addEventListener('error', disconnected);
    for (const name of APPLICATION_EVENTS) source.addEventListener(name, changed);
  }

  activeStreams.add(stop);
  connect();
  return stop;
}

export function newestRevision<T extends { id: string; revision: number }>(previous: T | undefined, incoming: T): T {
  return previous?.id === incoming.id && previous.revision > incoming.revision ? previous : incoming;
}

/** Preserve server sequence, including different events with the same revision. */
export async function readApplicationHistory(
  signal: AbortSignal,
  loadPage: (cursor: string | undefined) => Promise<components['schemas']['EventList'] | undefined>,
) {
  const items: components['schemas']['ApplicationEvent'][] = [];
  const cursors = new Set<string>();
  let cursor: string | undefined;
  do {
    signal.throwIfAborted();
    const page = await loadPage(cursor);
    signal.throwIfAborted();
    if (!page) throw new ApiProtocolError();
    items.push(...page.items);
    cursor = page.next_cursor ?? undefined;
    if (cursor && cursors.has(cursor)) throw new ApiProtocolError();
    if (cursor) cursors.add(cursor);
  } while (cursor);
  return items;
}
