import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, vi } from 'vitest'
import App from '../App'
import { Review } from '../api'
import AIKeySettings from '../components/AIKeySettings'
import NewReviewForm from '../components/NewReviewForm'
import ReportView from '../components/ReportView'
import ReviewList from '../components/ReviewList'

type Handler = (url: string, init: RequestInit) => [number, unknown]
let handler: Handler
const calls: { url: string; init: RequestInit }[] = []

function json(status: number, body: unknown) {
  const isText = typeof body === 'string'
  return new Response(status === 204 ? null : isText ? body : JSON.stringify(body),
    { status, headers: { 'content-type': isText ? 'text/plain' : 'application/json' } })
}

beforeEach(() => {
  calls.length = 0
  document.cookie = 'aisr_csrf=csrf123'
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init }); const [s, b] = handler(url, init); return json(s, b)
  }))
})
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

const review = (o: Partial<Review> = {}): Review => ({
  id: 'r1', repo_url: 'https://github.com/a/b', depth: 'quick', status: 'running', progress: 40, attempts: 1,
  ai_provider: null, message: null, created_at: '2026-01-01', ...o })

test('cloud AI requires explicit confirmation before submit', async () => {
  handler = () => [201, review({ ai_provider: 'openai' })]
  const onCreated = vi.fn()
  render(<NewReviewForm onCreated={onCreated} />)
  const u = userEvent.setup()
  await u.type(screen.getByPlaceholderText(/github.com\/owner/), 'https://github.com/a/b')
  await u.click(screen.getByLabelText('Deep'))
  const button = screen.getByRole('button', { name: /start review/i })
  expect(button).toBeEnabled()
  await u.selectOptions(screen.getByLabelText(/AI provider/), 'openai')
  expect(screen.getByRole('alert')).toHaveTextContent(/sent to a third-party AI/)
  expect(button).toBeDisabled()
  await u.click(screen.getByRole('checkbox'))
  expect(button).toBeEnabled()
  await u.click(button)
  await waitFor(() => expect(onCreated).toHaveBeenCalled())
  const c = calls[0]
  expect(c.url).toBe('/api/reviews')
  expect(JSON.parse(c.init.body as string)).toEqual({ repo_url: 'https://github.com/a/b', depth: 'deep', ai_provider: 'openai' })
  expect((c.init.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf123')
})

test('invalid URL blocks submit; on-prem has no warning; server error shown', async () => {
  handler = () => [422, { detail: 'bad url' }]
  render(<NewReviewForm onCreated={vi.fn()} />)
  const u = userEvent.setup()
  const input = screen.getByPlaceholderText(/github.com\/owner/)
  await u.type(input, 'https://gitlab.com/a/b')
  expect(screen.getByText(/Must be https/)).toBeInTheDocument()
  expect(screen.queryByRole('alert')).toBeNull()
  await u.clear(input); await u.type(input, 'https://github.com/a/b')
  await u.selectOptions(screen.getByLabelText(/AI provider/), 'none')
  await u.click(screen.getByRole('button', { name: /start review/i }))
  expect(await screen.findByText('bad url')).toBeInTheDocument()
  expect(JSON.parse(calls[0].init.body as string).ai_provider).toBeNull()
})

test('review list polls while active and supports pause/resume', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  handler = (url) => url.endsWith('/pause') ? [200, review({ status: 'paused' })] : [200, review({ status: 'running' })]
  const refresh = vi.fn(); const onChange = vi.fn(); const onOpen = vi.fn()
  const { rerender } = render(<ReviewList reviews={[review()]} onChange={onChange} onOpen={onOpen} refresh={refresh} pollMs={1000} />)
  await act(async () => { vi.advanceTimersByTime(3100) })
  expect(refresh).toHaveBeenCalledTimes(3)
  await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole('button', { name: 'Pause' }))
  await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: 'paused' })))
  rerender(<ReviewList reviews={[review({ status: 'paused' }), review({ id: 'r2', status: 'completed', progress: 100 })]}
    onChange={onChange} onOpen={onOpen} refresh={refresh} pollMs={1000} />)
  refresh.mockClear()
  await act(async () => { vi.advanceTimersByTime(3000) })
  expect(refresh).not.toHaveBeenCalled()
  const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
  await u.click(screen.getByRole('button', { name: 'Resume' }))
  expect(calls.at(-1)!.url).toBe('/api/reviews/r1/resume')
  await u.click(screen.getByRole('button', { name: 'Report' }))
  expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'r2' }))
})

test('empty list', () => {
  render(<ReviewList reviews={[]} onChange={vi.fn()} onOpen={vi.fn()} refresh={vi.fn()} />)
  expect(screen.getByText(/No reviews yet/)).toBeInTheDocument()
})

test('report view renders markdown, severity counts and cloud banner, no raw HTML', async () => {
  handler = (url) => url.endsWith('.md')
    ? [200, '# Security review\n\n| A | B |\n|---|---|\n| x | <img src=x onerror=alert(1)> |\n']
    : [200, { severity_counts: { HIGH: 2, LOW: 1 }, ai: { provider: 'openai', code_left_network: true } }]
  const { container } = render(<ReportView id="r1" onBack={vi.fn()} />)
  expect(await screen.findByRole('heading', { name: 'Security review' })).toBeInTheDocument()
  expect(screen.getByLabelText('severity counts')).toHaveTextContent('HIGH: 2LOW: 1')
  expect(screen.getByRole('alert')).toHaveTextContent(/cloud AI/)
  expect(container.querySelector('table')).not.toBeNull()
  expect(container.querySelector('img')).toBeNull()
})

test('report view error', async () => {
  handler = () => [404, { detail: 'report not ready' }]
  render(<ReportView id="r1" onBack={vi.fn()} />)
  expect(await screen.findByText('report not ready')).toBeInTheDocument()
})

test('AI key settings saves via PUT and never displays the key', async () => {
  let saved = false
  handler = (url, init) => {
    if (init.method === 'PUT') { saved = true; return [204, null] }
    return [200, saved ? [{ provider: 'anthropic', configured: true }] : []]
  }
  render(<AIKeySettings />)
  const u = userEvent.setup()
  await u.selectOptions(screen.getByLabelText('Provider'), 'anthropic')
  await u.type(screen.getByLabelText('API key'), 'sk-ant-xyz')
  await u.click(screen.getByRole('button', { name: /save key/i }))
  expect(await screen.findByText(/Saved key for anthropic/)).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText(/Anthropic \(cloud\): ✅ configured/)).toBeInTheDocument())
  expect(document.body.textContent).not.toContain('sk-ant-xyz')
  const put = calls.find(c => c.init.method === 'PUT')!
  expect(JSON.parse(put.init.body as string)).toEqual({ provider: 'anthropic', api_key: 'sk-ant-xyz' })
})

test('app shows login when unauthenticated', async () => {
  handler = () => [401, { detail: 'not logged in' }]
  render(<App />)
  expect(await screen.findByRole('link', { name: /Log in with GitHub/ })).toHaveAttribute('href', '/auth/login')
})

test('app logged in: lists reviews, navigates, logs out', async () => {
  handler = (url) => {
    if (url === '/auth/me') return [200, { id: 'u1', login: 'almog', role: 'user' }]
    if (url === '/api/reviews') return [200, [review({ status: 'completed', progress: 100 })]]
    if (url === '/api/ai-keys') return [200, []]
    if (url === '/auth/logout') return [200, { ok: true }]
    return [200, {}]
  }
  render(<App />)
  expect(await screen.findByText('@almog')).toBeInTheDocument()
  expect(await screen.findByText('a/b')).toBeInTheDocument()
  const u = userEvent.setup()
  await u.click(screen.getByRole('button', { name: 'AI keys' }))
  expect(await screen.findByRole('heading', { name: /AI provider keys/ })).toBeInTheDocument()
  await u.click(screen.getByRole('button', { name: 'Reviews' }))
  await u.click(screen.getByRole('button', { name: 'Log out' }))
  expect(await screen.findByRole('link', { name: /Log in with GitHub/ })).toBeInTheDocument()
})
