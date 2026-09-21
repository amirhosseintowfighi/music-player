import { fileURLToPath, URL } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    // Dev proxy so the Mini App can talk to the API without CORS.
    proxy: { '/v1': { target: process.env.VITE_API_PROXY ?? 'http://localhost:8000', changeOrigin: true } },
  },
  build: {
    target: 'es2022',
    sourcemap: true,
    rollupOptions: {
      output: {
        // Keep the first paint small: the player and route screens load on demand.
        manualChunks(id) {
          if (id.includes('node_modules')) {
            if (id.includes('framer-motion')) return 'motion';
            if (id.includes('react-router') || id.includes('@tanstack')) return 'router';
            return 'vendor';
          }
          return undefined;
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/api/schema.d.ts', 'src/main.tsx', 'src/**/*.d.ts'],
    },
  },
});
