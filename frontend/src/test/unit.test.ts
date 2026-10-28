import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, CAN, csrfToken, GITHUB_URL, repoName, TERMINAL } from '../api/client'
import { en } from '../i18n/en'
import { he } from '../i18n/he'
import { format } from '../i18n'

afterEach(() => { vi.restoreAllMocks(); document.cookie = 'aiscan_csrf=; max-age=0' })

describe('state matrix (Planner decisions)', () => {
  it('pause only for running', () => {
    expect(['queued', 'running', 'paused', 'stuck', 'retrying'].filter(s => CAN.pause(s as never))).toEqual(['running'])
  })
  it('cancel / resume / retry', () => {
    expect(['queued', 'running', 'paused', 'stuck', 'retrying', 'completed', 'failed', 'cancelled'].filter(s => CAN.cancel(s as never)))
      .toEqual(['queued', 'running', 'paused', 'stuck', 'retrying'])
    expect(CAN.resume('paused')).toBe(true); expect(CAN.resume('stuck')).toBe(true); expect(CAN.resume('running')).toBe(false)
    expect(CAN.retry('failed')).toBe(true); expect(CAN.retry('cancelled')).toBe(false)
    expect(TERMINAL).toEqual(['completed', 'failed', 'cancelled'])
  })
})

describe('github url', () => {
  it.each(['https://github.com/stamparm/DSVW', 'https://github.com/a-b/c.d.git', 'https://github.com/o/r/'])('accepts %s', u =>
    expect(GITHUB_URL.test(u)).toBe(true))
  it.each(['http://github.com/o/r', 'https://gitlab.com/o/r', 'https://github.com/o', 'https://github.com/o/r/tree/x', 'javascript:alert(1)'])(
    'rejects %s', u => expect(GITHUB_URL.test(u)).toBe(false))
  it('repoName', () => expect(repoName('https://github.com/stamparm/DSVW.git')).toBe('stamparm/DSVW'))
})

describe('i18n', () => {
  it('he has every en key and the same placeholders', () => {
    for (const k of Object.keys(en) as (keyof typeof en)[]) {
      expect(he, k).toHaveProperty(k)
      const ph = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort()
      expect(ph((he as Record<string, string>)[k]), k).toEqual(ph(en[k]))
    }
  })
  it('formats vars', () => expect(format('en', 'started', { repo: 'o/r' })).toBe('Review of o/r started.'))
})

describe('api client', () => {
  it('sends X-CSRF-Token on POST and maps contract errors', async () => {
    document.cookie = 'aiscan_csrf=tok123'
    expect(csrfToken()).toBe('tok123')
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify({ error: { code: 'CONFLICT', message: 'cannot pause from completed' }, request_id: 'req-1' }),
      { status: 409, headers: { 'Content-Type': 'application/json' } }))
    const err = await api.pause('11111111-1111-1111-1111-111111111111').catch(e => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ status: 409, code: 'CONFLICT', requestId: 'req-1' })
    const req = f.mock.calls[0][0] as Request
    expect(req.method).toBe('POST')
    expect(req.headers.get('X-CSRF-Token')).toBe('tok123')
    expect(new URL(req.url).pathname).toBe('/api/reviews/11111111-1111-1111-1111-111111111111/pause')
  })
  it('GET carries no CSRF header', async () => {
    document.cookie = 'aiscan_csrf=tok123'
    const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{"items":[],"page":1,"page_size":20,"total":0}',
      { status: 200, headers: { 'Content-Type': 'application/json' } }))
    await api.listReviews()
    expect((f.mock.calls[0][0] as Request).headers.get('X-CSRF-Token')).toBeNull()
  })
})
