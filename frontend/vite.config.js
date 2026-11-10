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
    },
  },
})
