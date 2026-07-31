import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
  server: {
    port: 5174,
    open: false,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('/node_modules/three/examples/')) {
            return 'three-examples-vendor';
          }
          if (id.includes('/node_modules/three/')) {
            return 'three-core-vendor';
          }
          if (id.includes('/node_modules/@jscad/modeling/')) {
            return 'jscad-vendor';
          }
          if (id.includes('/node_modules/react-router') || id.includes('/node_modules/@remix-run/router/')) {
            return 'router-vendor';
          }
          if (id.includes('/node_modules/lucide-react/')) {
            return 'icons-vendor';
          }
          if (id.includes('/node_modules/react/') || id.includes('/node_modules/react-dom/') || id.includes('/node_modules/scheduler/')) {
            return 'react-vendor';
          }
          return undefined;
        },
      },
    },
  },
});
