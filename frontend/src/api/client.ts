// Typed client generated from contracts/gateway.openapi.yaml (npm run gen:api -> src/api/schema.d.ts).
// Every call is a relative path (/api, /auth, /me) so it works behind the ingress, nginx and the vite proxy.
import createClient, { type Middleware } from 'openapi-fetch'
import type { components, paths } from './schema'

export type S = components['schemas']
export type Review = S['Review']
export type State = S['state']
export type Depth = S['depth']
export type Me = S['Me']
export type ProviderPublic = S['ProviderPublic']
export type AIProvider = S['AIProvider']
export type AIProviderWrite = S['AIProviderWrite']
export type AIProviderPatch = S['AIProviderPatch']
export type ProviderTestResult = S['ProviderTestResult']
export type ReviewFile = S['ReviewFile']
export type ReviewAttempt = S['ReviewAttempt']
export type Progress = S['Progress']
export type ErrorBody = S['Error']

export const LOGIN_URL = '/auth/github/login'
export const CSRF_COOKIE = 'aiscan_csrf'
export const CSRF_HEADER = 'X-CSRF-Token'

export function csrfToken(): string {
  const c = document.cookie.split('; ').find(x => x.startsWith(`${CSRF_COOKIE}=`))
  return c ? decodeURIComponent(c.slice(CSRF_COOKIE.length + 1)) : ''
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public code = '', public requestId = '') { super(message) }
}

const csrf: Middleware = {
  onRequest({ request }) {
    if (!['GET', 'HEAD'].includes(request.method)) request.headers.set(CSRF_HEADER, csrfToken())
    return request
  },
}

// fetch is looked up per call so tests can stub globalThis.fetch.
export const client = createClient<paths>({ baseUrl: location.origin /* same origin: /api, /me, /auth */, credentials: 'same-origin', fetch: (r: Request) => globalThis.fetch(r) })
client.use(csrf)

async function toError(res: Response, body: unknown): Promise<ApiError> {
  const e = body as Partial<ErrorBody> | undefined
  return new ApiError(res.status, e?.error?.message ?? res.statusText ?? 'request failed', e?.error?.code ?? '', e?.request_id ?? '')
}

/** Unwrap an openapi-fetch result: return data, or throw ApiError with the contract envelope (code, message, request_id). */
export async function ok<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  let r
  try { r = await p } catch (e) { throw new ApiError(0, e instanceof Error ? e.message : 'network error', 'NETWORK') }
  if (!r.response.ok) throw await toError(r.response, r.error)
  return r.data as T
}

const id = (rid: string) => ({ params: { path: { id: rid } } })

const progressRaw = (rid: string) => ok(client.GET('/api/reviews/{id}/progress', { params: { path: { id: rid }, query: { format: 'json' } } }))
export const api = {
  me: () => ok(client.GET('/me')),
  logout: () => ok(client.POST('/auth/logout')),
  listReviews: () => ok(client.GET('/api/reviews', { params: { query: {} } })).then(l => l.items),
  checkRepo: (url: string, ref?: string) => ok(client.GET('/api/repos/check', { params: { query: { url, ...(ref ? { ref } : {}) } } })),
  createReview: (body: S['ReviewCreate']) => ok(client.POST('/api/reviews', { body })),
  getReview: (rid: string) => ok(client.GET('/api/reviews/{id}', id(rid))),
  progress: (rid: string) => ok(client.GET('/api/reviews/{id}/progress', { params: { path: { id: rid }, query: { format: 'json' } } })) as Promise<Exclude<Awaited<ReturnType<typeof progressRaw>>, string>>,
  files: (rid: string) => ok(client.GET('/api/reviews/{id}/files', { params: { path: { id: rid }, query: { page_size: 100 } } })),
  attempts: (rid: string, budget_no?: number) =>
    ok(client.GET('/api/reviews/{id}/attempts', { params: { path: { id: rid }, query: budget_no ? { budget_no } : {} } })),
  pause: (rid: string) => ok(client.POST('/api/reviews/{id}/pause', id(rid))),
  resume: (rid: string, ack_cloud: boolean) => ok(client.POST('/api/reviews/{id}/resume', { ...id(rid), body: { ack_cloud } })),
  retry: (rid: string) => ok(client.POST('/api/reviews/{id}/retry', id(rid))),
  cancel: (rid: string) => ok(client.POST('/api/reviews/{id}/cancel', id(rid))),
  reportJson: (rid: string) => ok(client.GET('/api/reviews/{id}/report.json', id(rid))) as Promise<Report>,
  reportMd: (rid: string) => ok(client.GET('/api/reviews/{id}/report.md', { ...id(rid), parseAs: 'text' })) as Promise<string>,
  providers: () => ok(client.GET('/api/ai-providers')),
  adminProviders: () => ok(client.GET('/api/admin/ai-providers')),
  createProvider: (body: AIProviderWrite) => ok(client.POST('/api/admin/ai-providers', { body })),
  updateProvider: (pid: string, body: AIProviderPatch) => ok(client.PATCH('/api/admin/ai-providers/{id}', { ...id(pid), body })),
  deleteProvider: (pid: string) => ok(client.DELETE('/api/admin/ai-providers/{id}', id(pid))),
  testProvider: (pid: string) => ok(client.POST('/api/admin/ai-providers/{id}/test', id(pid))) as Promise<ProviderTestResult>,
  testUnsaved: (body: AIProviderWrite) => ok(client.POST('/api/admin/ai-providers/test', { body })) as Promise<ProviderTestResult>,
}

/** report.schema.json (report/v1); JSON Schema files aren't part of the OpenAPI codegen, so typed by hand. */
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'
export interface Finding {
  id: string; tool: 'semgrep' | 'gitleaks' | 'trivy' | 'ai'; rule: string; severity: Severity; file: string
  line?: number | null; line_end?: number | null; title: string; description?: string | null; category?: string | null
  cwe?: string[]; cve?: string | null; package?: string | null; fixed_version?: string | null; secret_redacted?: boolean
  snippet?: string | null; ai_severity?: Severity | null; ai_explanation?: string | null; ai_fix?: string | null; confidence?: number | null
}
export interface Report {
  schema: string; review_id: string; partial: boolean
  repo: { url: string; ref?: string | null; commit_sha?: string | null; private?: boolean }
  depth: Depth; provider: { name: string; model?: string | null; kind: 'onprem' | 'cloud' } | null
  started_at?: string | null; finished_at?: string | null; attempt?: number; budget_no?: number
  summary: Record<Severity, number> & { files_scanned?: number }
  executive_summary?: string | null; tool_versions?: Record<string, string>; findings: Finding[]
}

// ---- state matrix: what the API allows (review-api state machine) ----
export const CAN = {
  pause: (s: State) => s === 'running',
  resume: (s: State) => s === 'paused' || s === 'stuck',
  retry: (s: State) => s === 'failed',
  cancel: (s: State) => ['queued', 'running', 'paused', 'stuck', 'retrying'].includes(s),
}
export const TERMINAL: State[] = ['completed', 'failed', 'cancelled']
export const GITHUB_URL = /^https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}?(\.git)?\/?$/
export const repoName = (url: string) => url.replace(/^https:\/\/github\.com\//, '').replace(/(\.git)?\/?$/, '')

/** report.json uses {name:'none', kind:'onprem', model:'none'} when no AI ran (contracts/report.schema.json). */
export const hasAI = (p?: { name: string; model?: string | null } | null): boolean =>
  !!p && !(p.name === 'none' && (p.model ?? 'none') === 'none')
