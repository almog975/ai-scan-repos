import { useCallback, useEffect, useId, useState, type FormEvent } from 'react'
import { api, ApiError, CAN, GITHUB_URL, repoName, type Depth, type ProviderPublic, type Review } from '../api/client'
import { useI18n } from '../i18n'
import { useLiveReviews } from '../live'
import { Link, navigate } from '../router'
import { Confirm, ErrorBox, fmtTime, KindBadge, Ltr, StateBadge } from '../ui'

const DEPTHS: Depth[] = ['quick', 'brief', 'deep']

export default function Home() {
  const { t } = useI18n()
  const [url, setUrl] = useState('')
  const [touched, setTouched] = useState(false)
  const [ref, setRef] = useState('')
  const [depth, setDepth] = useState<Depth>('quick')
  const [providers, setProviders] = useState<ProviderPublic[]>([])
  const [pid, setPid] = useState('')
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState<unknown>(null)
  const [reviews, setReviews] = useState<Review[] | null>(null)
  const urlErr = useId(), urlHint = useId(), refHint = useId(), cloudId = useId(), reasonId = useId()

  const [rowErr, setRowErr] = useState<unknown>(null)
  const [partial, setPartial] = useState<Record<string, boolean>>({})
  const [resumeCloud, setResumeCloud] = useState<Review | null>(null)
  // server said CLOUD_ACK_REQUIRED (e.g. the default provider is cloud but the list did not say so): warn, then resend
  const [cloudRetry, setCloudRetry] = useState<Parameters<typeof api.createReview>[0] | null>(null)
  const reload = useCallback(() => { api.listReviews().then(setReviews).catch(setErr) }, [])
  const update = useCallback((rv: Review) => setReviews(l => (l ?? []).map(x => (x.id === rv.id ? rv : x))), [])

  useEffect(() => {
    api.providers().then(setProviders).catch(setErr)
    reload()
  }, [reload])
  useLiveReviews(reviews, update, reload)

  // failed/cancelled: a Report button only when a partial report exists (report.json 200 with partial=true)
  const ended = (reviews ?? []).filter(r => (r.state === 'failed' || r.state === 'cancelled') && !(r.id in partial)).map(r => r.id).join(',')
  useEffect(() => {
    for (const rid of ended.split(',').filter(Boolean)) {
      api.reportJson(rid).then(rep => setPartial(p => ({ ...p, [rid]: !!rep })))
        .catch(() => setPartial(p => ({ ...p, [rid]: false })))
    }
  }, [ended])

  const rowAct = async (r: Review, fn: () => Promise<unknown>) => {
    setRowErr(null)
    try { await fn() } catch (e) { setRowErr(e) }
    try { update(await api.getReview(r.id)) } catch { /* keep row */ }
    setPartial(p => { const n = { ...p }; delete n[r.id]; return n })
  }

  const urlOk = GITHUB_URL.test(url.trim())
  const showUrlErr = (touched || url.length > 0) && !urlOk
  // provider_id null = the default provider (server settings); its kind decides the cloud warning too.
  const def = providers.find(p => p.is_default)
  const prov = pid ? providers.find(p => p.id === pid) : def
  const cloud = prov?.kind === 'cloud'
  const reason = !urlOk ? t('start_disabled_url') : cloud && !ack ? t('cloud_ack_required') : ''

  const isCloudAck = (x: unknown) => x instanceof ApiError && x.status === 422 && x.code === 'CLOUD_ACK_REQUIRED'

  const send = async (body: Parameters<typeof api.createReview>[0]) => {
    setBusy(true); setErr(null)
    try {
      const r = await api.createReview(body)
      setReviews(l => [r, ...(l ?? []).filter(x => x.id !== r.id)])
      setUrl(''); setRef(''); setTouched(false); setAck(false)
      setMsg(t('started', { repo: repoName(r.repo_url) }))
    } catch (x) {
      if (isCloudAck(x) && !body.ack_cloud) setCloudRetry(body)  // never resend without showing the warning
      else setErr(x)
    } finally { setBusy(false) }
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (reason) return
    await send({ repo_url: url.trim(), depth, provider_id: pid || null, ack_cloud: cloud ? ack : false, ref: ref.trim() || 'HEAD' })
  }

  // row resume: provider kind may be unknown on the row -> the server's CLOUD_ACK_REQUIRED opens the same warning
  const resumeRow = (r: Review) => rowAct(r, async () => {
    try { await api.resume(r.id, false) } catch (x) { if (isCloudAck(x)) { setResumeCloud(r); return } throw x }
  })

  const activeId = err instanceof ApiError && err.status === 409 ? (reviews?.find(r => !['completed', 'failed', 'cancelled'].includes(r.state))?.id) : undefined

  return (
    <>
      <h1>{t('home_title')}</h1>
      <form className="card" onSubmit={submit} noValidate aria-busy={busy}>
        <div className="field">
          <label htmlFor="repo_url">{t('repo_url')}</label>
          <input id="repo_url" name="repo_url" type="url" dir="ltr" required autoComplete="url" inputMode="url"
            placeholder={t('repo_url_hint')} value={url} onChange={e => setUrl(e.target.value)} onBlur={() => setTouched(true)}
            aria-invalid={showUrlErr} aria-describedby={showUrlErr ? `${urlHint} ${urlErr}` : urlHint} />
          <span id={urlHint} className="hint"><Ltr>{t('repo_url_hint')}</Ltr></span>
          {showUrlErr && <span id={urlErr} className="field-error" role="alert">{t('repo_url_invalid')}</span>}
        </div>
        <div className="field">
          <label htmlFor="ref">{t('ref')}</label>
          <input id="ref" name="ref" dir="ltr" value={ref} onChange={e => setRef(e.target.value)} aria-describedby={refHint} />
          <span id={refHint} className="hint">{t('ref_hint')}</span>
        </div>
        <fieldset className="depths">
          <legend>{t('depth')}</legend>
          {DEPTHS.map(d => (
            <label key={d} className={`depthcard${depth === d ? ' on' : ''}`}>
              <input type="radio" name="depth" value={d} checked={depth === d} onChange={() => setDepth(d)} />
              <span className="dt">{t(`depth_${d}`)}</span>
              <span className="muted small">{t(`depth_${d}_d`)}</span>
            </label>
          ))}
        </fieldset>
        <div className="field">
          <label htmlFor="provider">{t('provider')}</label>
          <select id="provider" value={pid} onChange={e => { setPid(e.target.value); setAck(false) }}
            aria-describedby={cloud ? cloudId : undefined}>
            <option value="">{def ? t('provider_default_is', { name: def.name, kind: t(def.kind) }) : t('provider_none')}</option>
            {providers.map(p => <option key={p.id} value={p.id}>{p.name} ({t(p.kind)})</option>)}
          </select>
          {prov && <span><KindBadge kind={prov.kind} /></span>}
        </div>
        {cloud && (
          <div className="warn" id={cloudId} role="note">
            <p>{t('cloud_warn')}</p>
            <label className="check"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} /> {t('cloud_ack')}</label>
          </div>
        )}
        <div className="row">
          <button type="submit" className="btn primary" disabled={!!reason || busy} aria-describedby={reason ? reasonId : undefined}>
            {busy ? t('starting') : t('start')}
          </button>
          {reason && <span id={reasonId} className="reason">{reason}</span>}
        </div>
        <p aria-live="polite" className="ok">{msg}</p>
        <ErrorBox error={err} onDismiss={() => setErr(null)}>
          {activeId && <Link to={`/reviews/${activeId}`}>{t('active_link')}</Link>}
        </ErrorBox>
      </form>

      {reviews && reviews.length > 0 && (
        <section aria-labelledby="recent-h" className="card">
          <h2 id="recent-h">{t('recent')}</h2>
          <ErrorBox error={rowErr} onDismiss={() => setRowErr(null)} />
          <table>
            <thead><tr>
              <th scope="col">{t('col_repo')}</th><th scope="col">{t('col_state')}</th><th scope="col">{t('col_progress')}</th>
              <th scope="col">{t('col_depth')}</th><th scope="col">{t('col_created')}</th><th scope="col">{t('col_actions')}</th>
            </tr></thead>
            <tbody>
              {reviews.map(r => <ReviewRow key={r.id} r={r} partial={partial[r.id]} onAction={(fn) => rowAct(r, fn)}
                onResumeCloud={() => setResumeCloud(r)} onResume={() => resumeRow(r)} />)}
            </tbody>
          </table>
        </section>
      )}
      {cloudRetry && <Confirm title={t('cloud_retry_t')} body={<p>{t('cloud_warn')}</p>} ok={t('cloud_retry_ok')}
        safe={t('cloud_retry_cancel')} onSafe={() => setCloudRetry(null)}
        onOk={() => { const b = cloudRetry; setCloudRetry(null); setAck(true); send({ ...b, ack_cloud: true }) }} />}
      {resumeCloud && <Confirm title={t('resume_confirm_t')} body={<p>{t('resume_confirm_b')}</p>} ok={t('resume_confirm_ok')}
        safe={t('keep')} onSafe={() => setResumeCloud(null)}
        onOk={() => { const r = resumeCloud; setResumeCloud(null); rowAct(r, () => api.resume(r.id, true)) }} />}
      {reviews && reviews.length === 0 && <p className="muted">{t('no_recent')}</p>}
    </>
  )
}

function pct(r: Review) {
  if (r.state === 'completed') return 100
  return r.files_total ? Math.min(100, Math.round(((r.files_done ?? 0) / r.files_total) * 100)) : 0
}

function ReviewRow({ r, partial, onAction, onResumeCloud, onResume }:
  { r: Review; partial?: boolean; onAction: (fn: () => Promise<unknown>) => void; onResumeCloud: () => void; onResume: () => void }) {
  const { t } = useI18n()
  const name = repoName(r.repo_url)
  const s = r.state
  const p = pct(r)
  const repoId = `repo-${r.id}`
  const showReport = s === 'completed' || ((s === 'failed' || s === 'cancelled') && partial)
  const b = (label: string, fn: () => void, cls = '') =>
    <button type="button" className={`btn small ${cls}`} aria-describedby={repoId} onClick={fn}>{label}</button>
  return (
    <tr>
      <td id={repoId}><Link to={`/reviews/${r.id}`}><Ltr>{name}</Ltr></Link>
        <div className="mono small muted"><Ltr>@{r.ref}</Ltr></div></td>
      <td><StateBadge state={s} /></td>
      <td><progress max={100} value={p} aria-label={t('col_progress')} aria-describedby={repoId} /> <Ltr>{p}%</Ltr></td>
      <td>{t(`depth_${r.depth}`)}</td>
      <td><Ltr>{fmtTime(r.created_at)}</Ltr></td>
      <td><div className="row">
        {CAN.pause(s) && b(t('pause'), () => onAction(() => api.pause(r.id)))}
        {CAN.resume(s) && b(t('resume'), () => (r.provider?.kind === 'cloud' ? onResumeCloud() : onResume()))}
        {CAN.retry(s) && b(t('retry'), () => onAction(() => api.retry(r.id)))}
        {CAN.cancel(s) && b(t('cancel_short'), () => onAction(() => api.cancel(r.id)), 'danger')}
        {showReport && b(t('report_btn'), () => navigate(`/reviews/${r.id}/report`), partial && s !== 'completed' ? '' : 'primary')}
      </div></td>
    </tr>
  )
}
