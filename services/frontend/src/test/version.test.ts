import { afterEach, describe, expect, it, vi } from 'vitest'
import { en } from '../i18n/en'
import { he } from '../i18n/he'

afterEach(() => { vi.unstubAllEnvs(); vi.resetModules() })

describe('footer version badge', () => {
  it('uses VITE_APP_VERSION injected at build time', async () => {
    vi.stubEnv('VITE_APP_VERSION', 'abc1234')
    expect((await import('../version')).APP_VERSION).toBe('abc1234')
  })
  it("falls back to 'dev' when not injected", async () => {
    vi.stubEnv('VITE_APP_VERSION', '')
    expect((await import('../version')).APP_VERSION).toBe('dev')
  })
  it('has a label in both languages; brief depth = top 10 findings', () => {
    expect(en.version).toBe('Version'); expect(he.version).toBe('גרסה')
    expect(en.depth_brief_d).toMatch(/top 10 findings/)
  })
})
