import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import Home from '../views/Home'
import { I18nProvider } from '../i18n'
import { en } from '../i18n/en'

afterEach(() => vi.restoreAllMocks())

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

describe('default provider is cloud but the UI did not know (CLOUD_ACK_REQUIRED)', () => {
  it('shows the cloud warning first, then resends with ack_cloud=true', async () => {
    const posts: Array<Record<string, unknown>> = []
    vi.stubGlobal('fetch', vi.fn(async (req: Request) => {
      const u = new URL(req.url)
      if (u.pathname === '/api/providers') return json(200, [])  // default provider not visible to the UI
      if (u.pathname === '/api/reviews' && req.method === 'GET') return json(200, { items: [] })
      if (u.pathname === '/api/reviews' && req.method === 'POST') {
        const b = await req.json(); posts.push(b)
        if (!b.ack_cloud) return json(422, { error: { code: 'CLOUD_ACK_REQUIRED', message: 'ack' }, request_id: 'r1' })
        return json(201, { id: 'rv1', repo_url: b.repo_url, ref: 'HEAD', depth: 'quick', state: 'queued', created_at: '2026-01-01T00:00:00Z' })
      }
      return json(404, {})
    }))
    const user = userEvent.setup()
    render(<I18nProvider lang="en"><Home /></I18nProvider>)
    await user.type(screen.getByLabelText(en.repo_url), 'https://github.com/o/r')
    await user.click(screen.getByRole('button', { name: en.start }))

    // warning first: one rejected POST, no silent resend, no raw 422 error
    expect(await screen.findByText(en.cloud_warn)).toBeInTheDocument()
    expect(posts).toHaveLength(1)
    expect(posts[0].ack_cloud).toBe(false)
    expect(screen.queryByText('ack')).toBeNull()

    await user.click(screen.getByRole('button', { name: en.cloud_retry_ok }))
    await waitFor(() => expect(posts).toHaveLength(2))
    expect(posts[1].ack_cloud).toBe(true)
    expect(posts[1].repo_url).toBe('https://github.com/o/r')
  })

  it('cancelling the warning sends nothing more', async () => {
    let n = 0
    vi.stubGlobal('fetch', vi.fn(async (req: Request) => {
      const u = new URL(req.url)
      if (u.pathname === '/api/reviews' && req.method === 'POST') { n++; return json(422, { error: { code: 'CLOUD_ACK_REQUIRED', message: 'ack' } }) }
      return json(200, u.pathname === '/api/reviews' ? { items: [] } : [])
    }))
    const user = userEvent.setup()
    render(<I18nProvider lang="en"><Home /></I18nProvider>)
    await user.type(screen.getByLabelText(en.repo_url), 'https://github.com/o/r')
    await user.click(screen.getByRole('button', { name: en.start }))
    await user.click(await screen.findByRole('button', { name: en.cloud_retry_cancel }))
    expect(n).toBe(1)
  })
})
