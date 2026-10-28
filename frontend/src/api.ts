export type Depth = 'quick' | 'brief' | 'deep'
export type Status = 'queued' | 'running' | 'paused' | 'completed' | 'failed'
export interface Review {
  id: string; repo_url: string; depth: Depth; status: Status; progress: number; attempts: number
  ai_provider: string | null; message: string | null; created_at: string
}
export interface Me { id: string; login: string; role: string }

export const PROVIDERS: { id: string; label: string; cloud: boolean }[] = [
  { id: 'openai_compatible', label: 'On-prem (LM Studio / Ollama / vLLM via Tailscale)', cloud: false },
  { id: 'openai', label: 'OpenAI (cloud)', cloud: true },
  { id: 'anthropic', label: 'Anthropic (cloud)', cloud: true },
  { id: 'gemini', label: 'Google Gemini (cloud)', cloud: true },
  { id: 'grok', label: 'xAI Grok (cloud)', cloud: true },
  { id: 'none', label: 'No AI (scanners only)', cloud: false },
]
export const isCloud = (p: string) => PROVIDERS.find(x => x.id === p)?.cloud ?? false

export function csrfToken(): string {
  return document.cookie.split('; ').find(c => c.startsWith('aisr_csrf='))?.split('=')[1] ?? ''
}

export class ApiError extends Error { constructor(public status: number, msg: string) { super(msg) } }

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method ?? 'GET'
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) }
  if (method !== 'GET') headers['X-CSRF-Token'] = csrfToken()
  if (init.body) headers['Content-Type'] = 'application/json'
  const r = await fetch(path, { ...init, method, headers, credentials: 'same-origin' })
  if (!r.ok) {
    let msg = r.statusText
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail) } catch { /* not json */ }
    throw new ApiError(r.status, msg)
  }
  if (r.status === 204) return undefined as T
  return (r.headers.get('content-type') ?? '').includes('json') ? r.json() : (r.text() as Promise<T>)
}

export const api = {
  me: () => req<Me>('/auth/me'),
  logout: () => req('/auth/logout', { method: 'POST' }),
  listReviews: () => req<Review[]>('/api/reviews'),
  createReview: (repo_url: string, depth: Depth, ai_provider: string | null) =>
    req<Review>('/api/reviews', { method: 'POST', body: JSON.stringify({ repo_url, depth, ai_provider }) }),
  pause: (id: string) => req<Review>(`/api/reviews/${id}/pause`, { method: 'POST' }),
  resume: (id: string) => req<Review>(`/api/reviews/${id}/resume`, { method: 'POST' }),
  reportJson: (id: string) => req<Record<string, any>>(`/api/reviews/${id}/report`),
  reportMd: (id: string) => req<string>(`/api/reviews/${id}/report.md`),
  listKeys: () => req<{ provider: string; configured: boolean }[]>('/api/ai-keys'),
  putKey: (provider: string, api_key: string) =>
    req<void>('/api/ai-keys', { method: 'PUT', body: JSON.stringify({ provider, api_key }) }),
}

export const GITHUB_URL = /^https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}?(\.git)?\/?$/
