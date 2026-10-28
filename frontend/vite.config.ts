/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const gateway = process.env.GATEWAY_URL ?? 'http://localhost:8001'

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': gateway, '/auth': gateway } },
  test: {
    environment: 'jsdom', globals: true, setupFiles: './src/test/setup.ts',
    coverage: { include: ['src/**/*.{ts,tsx}'], exclude: ['src/main.tsx', 'src/test/**'] },
  },
})
