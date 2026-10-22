import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'API_');
  const target = process.env.API_PROXY_TARGET ?? env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000';
  const url = new URL(target);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash) {
    throw new Error('API_PROXY_TARGET должен быть HTTP(S) origin без пути и учётных данных');
  }

  return {
    plugins: [react()],
    server: {
      port: 5173,
      strictPort: true,
      // Тексты (docs/product) и лимиты продуктов (seed/) лежат вне frontend/.
      fs: { allow: ['.', '../docs', '../seed'] },
      proxy: {
        '/api': {
          target: url.origin,
          changeOrigin: false,
        },
      },
    },
  };
});
