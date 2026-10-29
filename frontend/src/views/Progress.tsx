import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, CAN, repoName, TERMINAL, type Progress, type Review, type ReviewAttempt, type ReviewFile } from '../api/client'
import { useI18n } from '../i18n'
import { Link, navigate } from '../router'
import { ActionButton, Confirm, ErrorBox, fmtTime, Ltr, StateBadge } from '../ui'

type ReportAvail = 'none' | 'full' | 'partial' | 'expired' | 'unknown'

/** Unicode first-strong isolate: keeps LTR tokens (logins, times) intact inside Hebrew sentences. */
const iso = (x: string) => `\u2068${x}\u2069`

function mmss(ms: number) {
  const s = Math.max(0, Math.round(ms / 1000))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export default function ProgressView({ id }: { id: string }) {
  const { t } = useI18n()
  const [r, setR] = useState<Review | null>(null)
  const [prog, setProg] = useState<Progress | null>(null)
  const [files, setFiles] = useState<ReviewFile[]>([])
  const [attempts, setAttempts] = useState<ReviewAttempt[]>([])
  const [live, setLive] = useState<'sse' | 'poll' | 'off'>('off')
  const [report, setReport] = useState<ReportAvail>('unknown')
  const [err, setErr] = useState<unknown>(null)
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState<'cancel' | 'resume' | null>(null)
  const [now, setNow] = useState(Date.now())
  const stateRef = useRef<string>('')

  const refresh = useCallback(async () => {
    try {
      const rv = await api.getReview(id)
      setR(rv); stateRef.current = rv.state; setLoadErr(null)
      api.files(id).then(p => setFiles(p.items)).catch(() => { /* per-file list is best-effort */ })
      api.attempts(id, rv.budget_no).then(setAttempts).catch(() => { /* idem */ })
      if (rv.state === 'completed') setReport('full')
      else if (rv.state === 'failed' || rv.state === 'cancelled') {
        api.reportJson(id).then(rep => setReport(rep.partial ? 'partial' : 'full')).catch(e =>
          setReport(e instanceof ApiError && e.status === 410 ? 'expired' : 'none'))
      } else setReport('none')
    } catch (e) { setLoadErr(e) }
  }, [id])

  // SSE first; on error fall back to ?format=json polling every 5 s.
  useEffect(() => {
    let es: EventSource | null = null
    let timer: ReturnType<typeof setInterval> | undefined
    let closed = false
    refresh()
    const poll = () => {
      setLive('poll')
      timer = setInterval(async () => {
        try { setProg(await api.progress(id)) } catch { /* keep polling */ }
        refresh()
        if (TERMINAL.includes(stateRef.current as never)) clearInterval(timer)
      }, 5000)
    }
    if (typeof EventSource === 'undefined') poll()
    else {
      es = new EventSource(`/api/reviews/${id}/progress`, { withCredentials: true })
      es.onopen = () => setLive('sse')
      es.addEventListener('progress', ev => {
        const p = JSON.parse((ev as MessageEvent).data) as Progress
        setProg(p)
        if (p.files) setFiles(p.files.map(f => ({ path: f.path, status: f.status, findings_count: f.findings_count })))
      })
      es.addEventListener('state', () => refresh())
      es.addEventListener('done', () => { closed = true; es?.close(); setLive('off'); refresh() })
      es.onerror = () => {
        if (closed) return
        es?.close(); es = null
        if (!TERMINAL.includes(stateRef.current as never)) poll()
      }
    }
    return () => { closed = true; es?.close(); if (timer) clearInterval(timer) }
  }, [id, refresh])

  useEffect(() => {
    if (r?.state !== 'retrying' && r?.state !== 'stuck') return
    const h = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(h)
  }, [r?.state])

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null); setConfirm(null)
    try { await fn(); await refresh() } catch (e) { setErr(e) } finally { setBusy(false) }
  }

  if (loadErr) return <><h1>{t('progress_title')}</h1><ErrorBox error={loadErr} /></>
  if (!r) return <><h1>{t('progress_title')}</h1><p aria-busy="true">{t('loading')}</p></>

  const s = r.state
  const cloud = r.provider?.kind === 'cloud'
  const total = prog?.totals?.files ?? r.files_total ?? 0
  const done = prog?.totals?.done ?? r.files_done ?? 0
  const fcode = r.failure_code ?? 'INTERNAL'

  return (
    <>
      <p className="eyebrow"><Link to="/" className="back"><span aria-hidden="true" className="arrow">←</span> {t('back')}</Link></p>
      <h1>{t('progress_title')}: <Ltr>{repoName(r.repo_url)}@{r.ref}</Ltr></h1>
      <div className="card">
        <div className="row between">
          <div aria-live="polite" aria-atomic="true" data-testid="state"><StateBadge state={s} /></div>
          <span className="muted small">{live === 'sse' ? t('live') : live === 'poll' ? t('polling') : ''}</span>
        </div>
        <p>{t('attempt', { n: r.attempt })} · {t('budget', { n: r.budget_no })}</p>
        <progress max={Math.max(total, 1)} value={s === 'completed' ? Math.max(total, 1) : done}
          aria-label={t('files_done', { done, total })} />
        <p className="muted">{t('files_done', { done, total })}</p>

        <div aria-live="polite" className="statemsg">
          {s === 'queued' && <p>{r.queue_position ? t('queued_pos', { n: r.queue_position }) : t('queued_wait')}</p>}
          {s === 'running' && prog?.current_file && <p>{t('running_file', { file: '' })}<Ltr>{prog.current_file}</Ltr></p>}
          {s === 'running' && prog?.stage && <p className="muted">{t('stage', { stage: '' })}<Ltr>{prog.stage}</Ltr></p>}
          {s === 'paused' && <p>{t('paused_by', { login: iso('@' + (r.paused_by?.login ?? '—')), at: iso(fmtTime(r.paused_at)) })}
            {' '}<span className="muted">{t('paused_note')}</span></p>}
          {s === 'stuck' && <p>{t('stuck_since', { at: iso(fmtTime(r.stuck_since)),
            min: r.stuck_since ? Math.max(0, Math.round((now - Date.parse(r.stuck_since)) / 60000)) : 0 })}
            {' '}<span className="muted">{t('stuck_note', { n: Math.min(5, r.attempt + 1) })}</span></p>}
          {s === 'retrying' && <p>{t('retrying_in', { n: r.attempt, next: Math.min(5, r.attempt + 1),
            mmss: r.next_retry_at ? mmss(Date.parse(r.next_retry_at) - now) : '—' })}</p>}
          {s === 'cancelled' && <p>{t('cancelled_by', { login: iso('@' + (r.cancelled_by?.login ?? '—')), at: iso(fmtTime(r.cancelled_at)) })}</p>}
          {s === 'completed' && <p>{t('completed', { n: r.findings_count ?? 0 })}</p>}
        </div>

        {s === 'failed' && (
          <div className="failure" data-testid="failure">
            <h2>{t(`F.${fcode}`)} <code><Ltr>{fcode}</Ltr></code></h2>
            {(r.failure_detail ?? r.failure_reason) && <pre dir="ltr">{r.failure_detail ?? r.failure_reason}</pre>}
            <p><strong>{t('next_step')}:</strong> {t(`F.${fcode}.h`)}</p>
          </div>
        )}

        {report === 'full' && <p><Link to={`/reviews/${id}/report`} className="btn primary">{t('open_report')}</Link></p>}
        {report === 'partial' && <p><Link to={`/reviews/${id}/report`} className="btn">{t('open_partial')}</Link></p>}
        {report === 'expired' && (
          <div className="warn" role="status"><h2>{t('expired_t')}</h2><p>{t('expired_b')}</p>
            <Link to="/">{t('new_review')}</Link></div>
        )}

        <div className="row actions" role="group" aria-label={t('col_actions')}>
          <ActionButton enabled={CAN.pause(s)} reason={t('why_pause')} busy={busy} onClick={() => act(() => api.pause(id))}>{t('pause')}</ActionButton>
          <ActionButton enabled={CAN.resume(s)} reason={t('why_resume')} busy={busy}
            onClick={() => (cloud ? setConfirm('resume') : act(() => api.resume(id, false)))}>{t('resume')}</ActionButton>
          <ActionButton enabled={CAN.retry(s)} reason={t('why_retry')} busy={busy} onClick={() => act(() => api.retry(id))}>{t('retry')}</ActionButton>
          <ActionButton enabled={CAN.cancel(s)} reason={t('why_cancel')} busy={busy} variant="danger"
            onClick={() => setConfirm('cancel')}>{t('cancel')}</ActionButton>
        </div>
        <ErrorBox error={err} onDismiss={() => setErr(null)} />
      </div>

      {confirm === 'cancel' && <Confirm title={t('cancel_confirm_t')} body={<p>{t('cancel_confirm_b')}</p>} danger
        ok={t('cancel')} safe={t('keep')} onSafe={() => setConfirm(null)} onOk={() => act(() => api.cancel(id))} />}
      {confirm === 'resume' && <Confirm title={t('resume_confirm_t')} body={<p>{t('resume_confirm_b')}</p>}
        ok={t('resume_confirm_ok')} safe={t('keep')} onSafe={() => setConfirm(null)} onOk={() => act(() => api.resume(id, true))} />}

      <section className="card" aria-labelledby="files-h">
        <h2 id="files-h">{t('files')}</h2>
        {files.length === 0 ? <p className="muted">{t('no_files')}</p> : (
          <table>
            <thead><tr><th scope="col">{t('col_file')}</th><th scope="col">{t('col_status')}</th><th scope="col">{t('col_findings')}</th></tr></thead>
            <tbody>{files.map(f => (
              <tr key={f.path}><th scope="row"><Ltr>{f.path}</Ltr></th><td>{t(`fstatus.${f.status}`)}</td><td>{f.findings_count}</td></tr>
            ))}</tbody>
          </table>
        )}
      </section>
      <section className="card" aria-labelledby="att-h">
        <h2 id="att-h">{t('attempts')}</h2>
        {attempts.length === 0 ? <p className="muted">{t('no_attempts')}</p> : (
          <table>
            <thead><tr><th scope="col">{t('col_n')}</th><th scope="col">{t('col_outcome')}</th><th scope="col">{t('col_started')}</th><th scope="col">{t('col_error')}</th></tr></thead>
            <tbody>{attempts.map(a => (
              <tr key={a.id}><th scope="row">{a.n}</th><td>{a.outcome ? t(`outcome.${a.outcome}`) : '—'}</td>
                <td><Ltr>{fmtTime(a.started_at)}</Ltr></td><td><Ltr>{a.error ?? ''}</Ltr></td></tr>
            ))}</tbody>
          </table>
        )}
      </section>
      {s === 'failed' && <p><button type="button" className="btn ghost" onClick={() => navigate('/')}>{t('new_review')}</button></p>}
    </>
  )
}
