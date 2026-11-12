/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const gateway = process.env.GATEWAY_URL ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': gateway, '/auth': gateway, '/me': gateway, '/webhooks': gateway } },
  test: {
    environment: 'jsdom', include: ['src/**/*.test.{ts,tsx}'], globals: true, setupFiles: './src/test/setup.ts',
    coverage: { include: ['src/**/*.{ts,tsx}'], exclude: ['src/main.tsx', 'src/test/**'] },
  },
})
