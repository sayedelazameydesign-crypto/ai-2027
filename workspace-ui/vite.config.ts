import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  // These server-only variables configure reverse proxies. They are deliberately
  // not VITE_-prefixed, so service URLs and credentials never enter browser code.
  const env = loadEnv(mode, '.', '');

  return {
    plugins: [react()],
    server: {
      host: '0.0.0.0',
      port: 4173,
      strictPort: true,
      allowedHosts: ['.e2b.app', 'localhost', '127.0.0.1'],
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8090',
          changeOrigin: true,
        },
        '/ops': {
          target: 'http://127.0.0.1:8091',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/ops/, '') || '/',
        },
        '/studio-api': {
          target: env.AI2027_STUDIO_URL || 'http://127.0.0.1:8080',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/studio-api/, '') || '/',
        },
      },
    },
  };
});
