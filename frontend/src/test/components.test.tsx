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
  document.cookie = 'aiscan_csrf=csrf123'
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init }); const [s, b] = handler(url, init); return json(s, b)
  }))
})
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

const review = (o: Partial<Review> = {}): Review => ({
  id: 'r1', repo_url: 'https://github.com/a/b', ref: 'HEAD', depth: 'quick', state: 'running', attempt: 1, budget_no: 1,
  files_done: 2, files_total: 5, provider: null, failure_reason: null, created_at: '2026-01-01', updated_at: '2026-01-01',
  started_at: null, finished_at: null, ...o })
const PROVIDERS = [
  { id: 'p-cloud', name: 'OpenAI', kind: 'cloud', cloud_warning: true },
  { id: 'p-onprem', name: 'Ollama', kind: 'onprem', cloud_warning: false },
]
const err = (code: string, message: string) => ({ error: { code, message }, request_id: 'rq1' })

test('cloud AI requires explicit confirmation before submit', async () => {
  handler = (url) => url === '/api/ai-providers' ? [200, PROVIDERS] : [201, review()]
  const onCreated = vi.fn()
  render(<NewReviewForm onCreated={onCreated} />)
  const u = userEvent.setup()
  await u.type(screen.getByPlaceholderText(/github.com\/owner/), 'https://github.com/a/b')
  await u.click(screen.getByLabelText('Deep'))
  const button = screen.getByRole('button', { name: /start review/i })
  expect(button).toBeEnabled()
  await screen.findByRole('option', { name: 'OpenAI' })
  await u.selectOptions(screen.getByLabelText(/AI provider/), 'p-cloud')
  expect(screen.getByRole('alert')).toHaveTextContent(/sent to a third-party AI/)
  expect(button).toBeDisabled()
  await u.click(screen.getByRole('checkbox'))
  expect(button).toBeEnabled()
  await u.click(button)
  await waitFor(() => expect(onCreated).toHaveBeenCalled())
  const c = calls.find(c => c.url === '/api/reviews')!
  expect(c.url).toBe('/api/reviews')
  expect(JSON.parse(c.init.body as string)).toEqual({ repo_url: 'https://github.com/a/b', depth: 'deep', provider_id: 'p-cloud', ack_cloud: true })
  expect((c.init.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf123')
})

test('invalid URL blocks submit; on-prem has no warning; server error shown', async () => {
  handler = (url) => url === '/api/ai-providers' ? [200, PROVIDERS] : [400, err('VALIDATION_ERROR', 'bad url')]
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
  expect(JSON.parse(calls.find(c => c.url === '/api/reviews')!.init.body as string).provider_id).toBeNull()
})

test('review list polls while active and supports pause/resume', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  handler = (url) => url.endsWith('/pause') ? [202, { state: 'paused' }] : url === '/api/reviews/r1' ? [200, review({ state: 'paused' })] : [202, {}]
  const refresh = vi.fn(); const onChange = vi.fn(); const onOpen = vi.fn()
  const { rerender } = render(<ReviewList reviews={[review()]} onChange={onChange} onOpen={onOpen} refresh={refresh} pollMs={1000} />)
  await act(async () => { vi.advanceTimersByTime(3100) })
  expect(refresh).toHaveBeenCalledTimes(3)
  await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole('button', { name: 'Pause' }))
  await waitFor(() => expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ state: 'paused' })))
  rerender(<ReviewList reviews={[review({ state: 'paused' }), review({ id: 'r2', state: 'completed' })]}
    onChange={onChange} onOpen={onOpen} refresh={refresh} pollMs={1000} />)
  refresh.mockClear()
  await act(async () => { vi.advanceTimersByTime(3000) })
  expect(refresh).not.toHaveBeenCalled()
  const u = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
  await u.click(screen.getByRole('button', { name: 'Resume' }))
  expect(calls.some(c => c.url === '/api/reviews/r1/resume' && c.init.method === 'POST')).toBe(true)
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
    : [200, { summary: { critical: 0, high: 2, medium: 0, low: 1, info: 0 }, provider: { name: 'OpenAI', model: 'gpt', kind: 'cloud' } }]
  const { container } = render(<ReportView id="r1" onBack={vi.fn()} />)
  expect(await screen.findByRole('heading', { name: 'Security review' })).toBeInTheDocument()
  expect(screen.getByLabelText('severity counts')).toHaveTextContent('HIGH: 2LOW: 1')
  expect(screen.getByRole('alert')).toHaveTextContent(/cloud AI/)
  expect(container.querySelector('table')).not.toBeNull()
  expect(container.querySelector('img')).toBeNull()
})

test('report view error', async () => {
  handler = () => [409, err('CONFLICT', 'report not ready')]
  render(<ReportView id="r1" onBack={vi.fn()} />)
  expect(await screen.findByText('report not ready')).toBeInTheDocument()
})

test('AI providers list is read-only and shows no keys', async () => {
  handler = () => [200, PROVIDERS]
  render(<AIKeySettings />)
  expect(await screen.findByText('OpenAI (cloud)')).toBeInTheDocument()
  expect(screen.getByText('Ollama (on-prem)')).toBeInTheDocument()
  expect(calls.map(c => c.url)).toEqual(['/api/ai-providers'])
  expect(screen.queryByLabelText('API key')).toBeNull()
})

test('logout sends CSRF header; error envelope message is surfaced', async () => {
  const { api, ApiError } = await import('../api')
  handler = () => [204, null]
  await api.logout()
  expect((calls[0].init.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf123')
  handler = () => [403, err('FORBIDDEN', 'csrf')]
  await expect(api.logout()).rejects.toMatchObject({ status: 403, code: 'FORBIDDEN', message: 'csrf', requestId: 'rq1' })
  expect(ApiError).toBeDefined()
})

test('app shows login when unauthenticated', async () => {
  handler = () => [401, err('UNAUTHORIZED', 'not logged in')]
  render(<App />)
  expect(await screen.findByRole('link', { name: /Log in with GitHub/ })).toHaveAttribute('href', '/auth/github/login')
})

test('app logged in: lists reviews, navigates, logs out', async () => {
  handler = (url) => {
    if (url === '/me') return [200, { id: 'u1', github_login: 'almog', avatar_url: null, role: 'user' }]
    if (url === '/api/reviews') return [200, { items: [review({ state: 'completed' })], page: 1, page_size: 20, total: 1 }]
    if (url === '/api/ai-providers') return [200, PROVIDERS]
    if (url === '/auth/logout') return [204, null]
    return [200, {}]
  }
  render(<App />)
  expect(await screen.findByText('@almog')).toBeInTheDocument()
  expect(await screen.findByText('a/b')).toBeInTheDocument()
  const u = userEvent.setup()
  await u.click(screen.getByRole('button', { name: 'AI providers' }))
  expect(await screen.findByRole('heading', { name: /AI providers/ })).toBeInTheDocument()
  await u.click(screen.getByRole('button', { name: 'Reviews' }))
  await u.click(screen.getByRole('button', { name: 'Log out' }))
  expect(await screen.findByRole('link', { name: /Log in with GitHub/ })).toBeInTheDocument()
})
