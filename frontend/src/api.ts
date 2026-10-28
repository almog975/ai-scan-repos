// Client for the gateway contract (contracts/gateway.openapi.yaml): /auth/github/*, /me, /api/* -> review-api.
export type Depth = 'quick' | 'brief' | 'deep'
export type State = 'queued' | 'running' | 'paused' | 'stuck' | 'retrying' | 'completed' | 'failed' | 'cancelled'
export interface ProviderRef { id: string; kind: 'onprem' | 'cloud'; cloud_warning: boolean }
export interface Review {
  id: string; repo_url: string; ref: string; commit_sha?: string | null; depth: Depth; state: State
  attempt: number; budget_no: number; files_done?: number; files_total?: number | null; findings_count?: number
  failure_reason?: string | null; provider?: ProviderRef | null
  created_at: string; updated_at: string; started_at: string | null; finished_at: string | null
}
export interface ReviewList { items: Review[]; page: number; page_size: number; total: number }
export interface Me { id: string; github_login: string; avatar_url: string | null; role: 'user' | 'admin' }
export interface ProviderPublic { id: string; name: string; kind: 'onprem' | 'cloud'; cloud_warning: boolean }
export interface ApiErrorBody { error: { code: string; message: string; details?: unknown }; request_id: string }

export const LOGIN_URL = '/auth/github/login'
export const CSRF_COOKIE = 'aiscan_csrf'
export const CSRF_HEADER = 'X-CSRF-Token'

export function csrfToken(): string {
  const c = document.cookie.split('; ').find(c => c.startsWith(`${CSRF_COOKIE}=`))
  return c ? decodeURIComponent(c.slice(CSRF_COOKIE.length + 1)) : ''
}

export class ApiError extends Error {
  constructor(public status: number, msg: string, public code = '', public requestId = '') { super(msg) }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method ?? 'GET'
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) }
  if (!['GET', 'HEAD'].includes(method)) headers[CSRF_HEADER] = csrfToken()
  if (init.body) headers['Content-Type'] = 'application/json'
  const r = await fetch(path, { ...init, method, headers, credentials: 'same-origin' })
  if (!r.ok) {
    let msg = r.statusText, code = '', rid = ''
    try {
      const j = (await r.json()) as ApiErrorBody
      msg = j.error?.message ?? msg; code = j.error?.code ?? ''; rid = j.request_id ?? ''
    } catch { /* not json */ }
    throw new ApiError(r.status, msg, code, rid)
  }
  if (r.status === 204) return undefined as T
  return (r.headers.get('content-type') ?? '').includes('json') ? r.json() : (r.text() as Promise<T>)
}

export const api = {
  me: () => req<Me>('/me'),
  logout: () => req<void>('/auth/logout', { method: 'POST' }),
  listReviews: () => req<ReviewList>('/api/reviews').then(l => l.items),
  createReview: (repo_url: string, depth: Depth, provider_id: string | null, ack_cloud = false) =>
    req<Review>('/api/reviews', { method: 'POST', body: JSON.stringify({ repo_url, depth, provider_id, ack_cloud }) }),
  getReview: (id: string) => req<Review>(`/api/reviews/${id}`),
  pause: (id: string) => req<unknown>(`/api/reviews/${id}/pause`, { method: 'POST' }).then(() => api.getReview(id)),
  resume: (id: string) => req<unknown>(`/api/reviews/${id}/resume`, { method: 'POST' }).then(() => api.getReview(id)),
  reportJson: (id: string) => req<Record<string, any>>(`/api/reviews/${id}/report.json`),
  reportMd: (id: string) => req<string>(`/api/reviews/${id}/report.md`),
  providers: () => req<ProviderPublic[]>('/api/ai-providers'),
}

export const progressOf = (r: Review) =>
  r.state === 'completed' ? 100 : r.files_total ? Math.min(100, Math.round(((r.files_done ?? 0) / r.files_total) * 100)) : 0

export const GITHUB_URL = /^https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}?(\.git)?\/?$/
