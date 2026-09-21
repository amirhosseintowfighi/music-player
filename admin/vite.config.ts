import { fileURLToPath, URL } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    port: 5174,
    proxy: {
      '/admin': { target: process.env.VITE_API_PROXY ?? 'http://localhost:8000', changeOrigin: true },
    },
  },
  build: { target: 'es2022', sourcemap: true },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    css: false,
  },
});
