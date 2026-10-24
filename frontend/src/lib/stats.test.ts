import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatDuration, plural, share } from './stats.ts';

const forms = 'заявка|заявки|заявок';

test('russian plural forms', () => {
  assert.deepEqual([0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 101, 111].map((n) => plural(n, forms)), [
    'заявок', 'заявка', 'заявки', 'заявки', 'заявок', 'заявок', 'заявок', 'заявок',
    'заявка', 'заявки', 'заявок', 'заявка', 'заявок',
  ]);
});

test('durations read naturally from seconds to days', () => {
  assert.equal(formatDuration(2.14), '2,1 с');
  assert.equal(formatDuration(11.6), '12 с');
  assert.equal(formatDuration(840), '14 мин');
  assert.equal(formatDuration(3600), '1 ч');
  assert.equal(formatDuration(3960), '1 ч 6 мин');
  assert.equal(formatDuration(200_000), '2 дн 7 ч');
  assert.equal(formatDuration(null), '—');
});

test('share is a whole percentage, undefined for an empty base', () => {
  assert.equal(share(19, 22), 86);
  assert.equal(share(0, 0), null);
});
