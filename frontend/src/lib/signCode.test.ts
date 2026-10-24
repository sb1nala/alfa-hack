import { test } from 'node:test';
import assert from 'node:assert/strict';
import { checkCode, formatCode, generateCode, MAX_ATTEMPTS, normalizeCode } from './signCode.ts';

test('code always has six digits, including leading zeros', () => {
  assert.equal(generateCode((b) => { b[0] = 42; return b; }), '000042');
  assert.equal(generateCode((b) => { b[0] = 4_294_967_295; return b; }), '967295');
  assert.match(generateCode(), /^\d{6}$/);
});

test('code is shown in two groups and accepted with spaces or dashes', () => {
  assert.equal(formatCode('482913'), '482 913');
  assert.equal(normalizeCode(' 482-913 '), '482913');
  assert.equal(normalizeCode('48291399'), '482913');
});

test('check: incomplete, mismatch, ok, locked after attempts or without a code', () => {
  assert.equal(checkCode('482913', '4829', 0), 'incomplete');
  assert.equal(checkCode('482913', '482 914', 0), 'mismatch');
  assert.equal(checkCode('482913', '482 913', MAX_ATTEMPTS - 1), 'ok');
  assert.equal(checkCode('482913', '482913', MAX_ATTEMPTS), 'locked');
  assert.equal(checkCode(null, '482913', 0), 'locked');
});
