import { useEffect, useId, useRef, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import { useI18n } from '../i18n'

/** LTR island for codes, URLs, paths, logins, timestamps inside possibly-RTL text. */
export const Ltr = ({ children }: { children: ReactNode }) => <bdi dir="ltr">{children}</bdi>

export function fmtTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return isNaN(d.getTime()) ? iso : d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC'
}

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
