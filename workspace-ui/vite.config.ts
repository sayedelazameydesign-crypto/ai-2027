import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
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
    },
  },
});
