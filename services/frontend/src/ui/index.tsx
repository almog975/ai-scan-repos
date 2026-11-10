import { useEffect, useId, useRef, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import { useI18n } from '../i18n'

/** LTR island for codes, URLs, paths, logins, timestamps inside possibly-RTL text. */
export const Ltr = ({ children }: { children: ReactNode }) => <bdi dir="ltr">{children}</bdi>

/** Short date + time in the viewer's local time zone, formatted for the UI language. Render it inside <Ltr>. */
export function fmtTime(iso?: string | null, lang?: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const l = lang ?? (typeof document !== 'undefined' ? document.documentElement.lang : '') ?? ''
  return new Intl.DateTimeFormat(l || undefined, { dateStyle: 'short', timeStyle: 'short' }).format(d)
}

/** GitHub login as an LTR isolate: @login. */
export const LoginTag = ({ login }: { login?: string | null }) => <bdi dir="ltr">@{login ?? '—'}</bdi>

const svg = { width: 16, height: 16, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2,
  strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true, focusable: false }
export const PencilIcon = () => <svg {...svg} className="icon"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>
export const TrashIcon = () => <svg {...svg} className="icon"><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /><path d="M10 11v6M14 11v6" /></svg>
export const CheckIcon = () => <svg {...svg} className="icon"><path d="M20 6 9 17l-5-5" /></svg>
export const XIcon = () => <svg {...svg} className="icon"><path d="M18 6 6 18M6 6l12 12" /></svg>

export function StateBadge({ state }: { state: string }) {
  const { t } = useI18n()
  return <span className={`badge state-${state}`}><span aria-hidden="true" className="dot" />{t(`state.${state}`)}</span>
}

export function SevBadge({ sev }: { sev: string }) {
  const { t } = useI18n()
  return <span className={`badge sev-${sev}`}>{t(`sev.${sev}`)}</span>
}

export function KindBadge({ kind }: { kind: 'onprem' | 'cloud' }) {
  const { t } = useI18n()
  return <span className={`badge kind-${kind}`}><span aria-hidden="true">{kind === 'cloud' ? '☁ ' : '⌂ '}</span>{t(kind)}</span>
}

/** Every action error on screen: code + message (+ request id), announced via role=alert. */
export function ErrorBox({ error, onDismiss, children }: { error: unknown; onDismiss?: () => void; children?: ReactNode }) {
  const { t } = useI18n()
  if (!error) return null
  const e = error instanceof ApiError ? error : new ApiError(0, String((error as Error)?.message ?? error), 'INTERNAL')
  return (
    <div className="error" role="alert" data-testid="error">
      <strong>{t('error')}{e.status ? <> <Ltr>{e.status}</Ltr></> : null}{e.code ? <> · <code><Ltr>{e.code}</Ltr></code></> : null}</strong>
      <p><bdi dir="auto">{e.message}</bdi></p>
      {children}
      {e.requestId && <p className="muted small">{t('request_id', { id: '' })}<Ltr>{e.requestId}</Ltr></p>}
      {onDismiss && <button type="button" className="btn ghost small" onClick={onDismiss}>{t('dismiss')}</button>}
    </div>
  )
}

/** Disabled button with a visible + programmatic reason (aria-describedby). */
export function ActionButton({ enabled, reason, onClick, children, variant = '', busy = false }:
  { enabled: boolean; reason: string; onClick: () => void; children: ReactNode; variant?: string; busy?: boolean }) {
  const rid = useId()
  return (
    <span className="action">
      <button type="button" className={`btn ${variant}`} disabled={!enabled || busy} aria-disabled={!enabled || busy}
        aria-describedby={!enabled ? rid : undefined} onClick={onClick}>{children}</button>
      {!enabled && <span id={rid} className="reason">{reason}</span>}
    </span>
  )
}

/** Modal confirm dialog: role=alertdialog, labelled/described, initial focus on the safe button, Escape = safe. */
export function Confirm({ title, body, ok, safe, onOk, onSafe, danger = false }:
  { title: string; body: ReactNode; ok: string; safe: string; onOk: () => void; onSafe: () => void; danger?: boolean }) {
  const tid = useId(), bid = useId()
  const safeRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null
    safeRef.current?.focus()
    return () => prev?.focus?.()
  }, [])
  return (
    <div className="backdrop">
      <div role="alertdialog" aria-modal="true" aria-labelledby={tid} aria-describedby={bid} className="dialog"
        onKeyDown={e => { if (e.key === 'Escape') onSafe() }}>
        <h2 id={tid}>{title}</h2>
        <div id={bid}>{body}</div>
        <div className="row end">
          <button ref={safeRef} type="button" className="btn" onClick={onSafe}>{safe}</button>
          <button type="button" className={`btn ${danger ? 'danger' : 'primary'}`} onClick={onOk}>{ok}</button>
        </div>
      </div>
    </div>
  )
}
