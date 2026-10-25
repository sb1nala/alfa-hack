import assert from 'node:assert/strict';
import { test } from 'node:test';
import { APPLICATION_EVENTS, closeApplicationStreams, newestRevision, readApplicationHistory, watchApplication } from './applicationLive.ts';
import { ApiError, ApiProtocolError } from './errors.ts';
import type { components } from './schema.d.ts';

class FakeSource extends EventTarget {
  readyState = 0;
  closed = false;
  close() { this.closed = true; this.readyState = 2; }
  open() { this.readyState = 1; this.dispatchEvent(new Event('open')); }
  fail(state = 0) { this.readyState = state; this.dispatchEvent(new Event('error')); }
  change(type: string, id: string, application = 'app', revision = 2) {
    this.dispatchEvent(new MessageEvent(type, {
      data: JSON.stringify({ application_id: application, revision, event_type: type }), lastEventId: id,
    }));
  }
}
const tick = () => new Promise<void>((resolve) => setImmediate(resolve));

test('browser offline is visible even if native SSE remains OPEN; online restores snapshot and history', async () => {
  const network = new EventTarget();
  const source = new FakeSource();
  const states: boolean[] = [];
  const reads: boolean[] = [];
  let sources = 0;
  const stop = watchApplication({ id: 'app', network,
    createSource: () => { sources++; return source; },
    refresh: async (_signal, history) => { reads.push(history); },
    onConnection: (lost) => states.push(lost), onFatal: () => {},
  });
  try {
    source.open(); await tick();
    network.dispatchEvent(new Event('offline'));
    assert.equal(source.readyState, 1);
    assert.equal(states.at(-1), true);
    source.change('status_changed', 'missed'); source.open(); await tick();
    assert.equal(reads.length, 1, 'No refresh or false recovery while browser is offline');
    assert.equal(states.at(-1), true);
    network.dispatchEvent(new Event('online')); await tick();
    assert.deepEqual(reads, [true, true]);
    assert.equal(states.at(-1), false);
    assert.equal(sources, 1, 'Reuse the native source without parallel connections');
  } finally { stop(); }
  network.dispatchEvent(new Event('offline')); network.dispatchEvent(new Event('online')); await tick();
  assert.equal(reads.length, 2, 'Stop removes network listeners');
});

test('open and all named events reread the snapshot; duplicate, foreign and invalid events are ignored', async () => {
  const source = new FakeSource();
  let reads = 0;
  const stop = watchApplication({ id: 'app', refresh: async () => { reads++; },
    onConnection: () => {}, onFatal: () => {}, createSource: (url) => {
      assert.equal(url, '/api/applications/app/events'); return source;
    } });
  try {
    source.open(); await tick();
    for (const type of APPLICATION_EVENTS) { source.change(type, type); await tick(); }
    assert.equal(reads, 5);
    source.change('status_changed', 'status_changed');
    source.change('status_changed', 'foreign', 'other');
    source.change('status_changed', 'invalid', 'app', 0);
    source.dispatchEvent(new MessageEvent('status_changed', { data: '{' }));
    await tick(); assert.equal(reads, 5);
  } finally { stop(); }
  source.change('status_changed', 'after-stop'); await tick();
  assert.equal(reads, 5); assert.equal(source.closed, true);
});

test('events during a request cause one trailing refresh so the last revision is not missed', async () => {
  const source = new FakeSource();
  let finish: (() => void) | undefined;
  let reads = 0;
  const stop = watchApplication({ id: 'app', createSource: () => source,
    onConnection: () => {}, onFatal: () => {}, refresh: async () => {
      reads++;
      if (reads === 1) await new Promise<void>((resolve) => { finish = resolve; });
    } });
  try {
    source.open(); source.change('status_changed', '1'); source.change('decision_created', '2');
    assert.equal(reads, 1); finish!(); await tick(); assert.equal(reads, 2);
  } finally { stop(); }
});

test('native CONNECTING is retained; reconnect open rereads; CLOSED gets a new source', async () => {
  const sources: FakeSource[] = [];
  const states: boolean[] = [];
  let reads = 0;
  const stop = watchApplication({ id: 'app', refresh: async () => { reads++; },
    onConnection: (lost) => states.push(lost), onFatal: () => {},
    createSource: () => { const source = new FakeSource(); sources.push(source); return source; } });
  try {
    sources[0]!.open(); await tick(); sources[0]!.fail(); await tick();
    assert.equal(sources.length, 1); assert.equal(states.at(-1), true);
    sources[0]!.open(); await tick(); assert.equal(reads, 3); assert.equal(states.at(-1), false);
    sources[0]!.fail(2); await tick(); assert.equal(sources.length, 2);
    sources[1]!.open(); await tick(); assert.equal(reads, 5);
  } finally { stop(); }
});

for (const status of [401, 403, 404]) {
  test(`HTTP ${status} closes the stream and stops retries`, async () => {
    const source = new FakeSource();
    let fatal: ApiError | undefined;
    const stop = watchApplication({ id: 'app', createSource: () => source,
      refresh: async () => { throw new ApiError(status, null); },
      onConnection: () => {}, onFatal: (error) => { fatal = error; } });
    source.open(); await tick();
    assert.equal(fatal?.status, status); assert.equal(source.closed, true); stop();
  });
}

test('temporary snapshot failure retries even while transport stays open', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const source = new FakeSource();
  let reads = 0;
  let lost = false;
  const stop = watchApplication({ id: 'app', createSource: () => source,
    refresh: async () => { if (++reads === 1) throw new Error('offline'); },
    onConnection: (value) => { lost = value; }, onFatal: () => {} });
  try {
    source.open(); await tick(); assert.equal(lost, true);
    context.mock.timers.tick(3_000); await tick();
    assert.equal(reads, 2); assert.equal(lost, false);
    context.mock.timers.tick(30_000); await tick(); assert.equal(reads, 2);
  } finally { stop(); }
});

test('logout aborts pending work and ignores late completion', async () => {
  const source = new FakeSource();
  let signal: AbortSignal | undefined;
  let finish: (() => void) | undefined;
  let fatal = false;
  watchApplication({ id: 'app', createSource: () => source,
    refresh: async (value) => { signal = value; await new Promise<void>((resolve) => { finish = resolve; }); },
    onConnection: () => {}, onFatal: () => { fatal = true; } });
  source.open(); closeApplicationStreams();
  assert.equal(signal?.aborted, true); assert.equal(source.closed, true);
  finish!(); await tick(); assert.equal(fatal, false);
});

test('older snapshot cannot overwrite a newer mutation result', () => {
  const current = { id: 'app', revision: 8 };
  assert.equal(newestRevision(current, { id: 'app', revision: 7 }), current);
  assert.equal(newestRevision(current, { id: 'app', revision: 9 }).revision, 9);
  assert.equal(newestRevision(current, { id: 'other', revision: 1 }).id, 'other');
});

test('history follows opaque cursors and preserves events sharing one revision', async () => {
  const event = (id: string): components['schemas']['ApplicationEvent'] => ({
    id, application_id: 'app', event_type: 'status_changed', revision: 3,
    status: 'SCORING', occurred_at: '2026-10-22T10:00:00Z',
    previous_status: 'CHECKING', message: 'Оценка заявки',
  });
  const cursors: (string | undefined)[] = [];
  const result = await readApplicationHistory(new AbortController().signal, async (cursor) => {
    cursors.push(cursor);
    return cursor === undefined ? { items: [event('z')], next_cursor: 'z' }
      : cursor === 'z' ? { items: [event('a')], next_cursor: 'a' }
        : { items: [event('b')], next_cursor: null };
  });
  assert.deepEqual(cursors, [undefined, 'z', 'a']);
  assert.deepEqual(result.map((item) => item.id), ['z', 'a', 'b']);
});

test('history rejects non-advancing cursors and stops after cancellation', async () => {
  await assert.rejects(readApplicationHistory(new AbortController().signal,
    async () => ({ items: [], next_cursor: 'same' })), ApiProtocolError);
  const abort = new AbortController();
  let calls = 0;
  await assert.rejects(readApplicationHistory(abort.signal, async () => {
    calls++; abort.abort(); return { items: [], next_cursor: 'next' };
  }), { name: 'AbortError' });
  assert.equal(calls, 1);
});
