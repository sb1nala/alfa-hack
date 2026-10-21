import assert from 'node:assert/strict';
import { test } from 'node:test';
import config from '../../vite.config.ts';

test('единственная переменная переключает Prism и backend, сохраняя Origin браузера', async () => {
  const previous = process.env.API_PROXY_TARGET;
  try {
    assert.equal(typeof config, 'function');
    if (typeof config !== 'function') throw new Error('Ожидается фабрика Vite config');
    for (const target of ['http://127.0.0.1:4010', 'http://127.0.0.1:8000']) {
      process.env.API_PROXY_TARGET = target;
      const result = await config({ mode: 'development', command: 'serve' });
      const proxy = result.server?.proxy?.['/api'];
      assert.ok(proxy && typeof proxy !== 'string');
      assert.equal(proxy.target, target);
      assert.equal(proxy.changeOrigin, false);
    }
  } finally {
    if (previous === undefined) delete process.env.API_PROXY_TARGET;
    else process.env.API_PROXY_TARGET = previous;
  }
});

test('прокси не принимает адрес с учётными данными или ошибочно добавленным /api', async () => {
  const previous = process.env.API_PROXY_TARGET;
  try {
    if (typeof config !== 'function') throw new Error('Ожидается фабрика Vite config');
    for (const target of ['http://127.0.0.1:8000/api', 'http://user:password@127.0.0.1:8000', 'file:///api']) {
      process.env.API_PROXY_TARGET = target;
      await assert.rejects(async () => {
        if (typeof config === 'function') await config({ mode: 'development', command: 'serve' });
      });
    }
  } finally {
    if (previous === undefined) delete process.env.API_PROXY_TARGET;
    else process.env.API_PROXY_TARGET = previous;
  }
});
