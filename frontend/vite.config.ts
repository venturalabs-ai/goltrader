import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Em dev, /api e /ws são encaminhados ao backend local (porta 8000) — sem URL fixa no código.
// Para apontar outro backend: VITE_API_URL=https://seu-host (build/dev).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: (process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000').replace(/^http/, 'ws'), ws: true },
    },
  },
});
