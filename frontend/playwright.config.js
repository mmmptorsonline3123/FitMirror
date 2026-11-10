import { defineConfig } from '@playwright/test'

const py = process.platform === 'win32' ? '.venv\\Scripts\\python.exe' : '.venv/bin/python'

export default defineConfig({
  testDir: 'e2e',
  timeout: 60_000,
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  webServer: [
    { command: `${py} e2e_server.py`, cwd: '../backend', url: 'http://127.0.0.1:8000/health', reuseExistingServer: false, timeout: 60_000 },
    { command: 'npm run dev -- --host 127.0.0.1 --port 5173 --strictPort', url: 'http://127.0.0.1:5173', reuseExistingServer: false },
  ],
})
