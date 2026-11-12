import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: /api is proxied to FastAPI so the session cookie is same-origin (matches the Vercel rewrite in prod).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY || 'http://127.0.0.1:8000',
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
      // Demo/E2E only (backend/e2e_server.py): photo files from the local moto S3, so phones on the LAN can load them.
      // Object paths only (/users/...), so bucket listings aren't reachable from the network.
      '/fitmirror-e2e/users/': 'http://127.0.0.1:5055',
    },
  },
})
