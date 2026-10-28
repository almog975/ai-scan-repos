import { useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, ApiError, repoName, type Finding, type Report, type Severity } from '../api/client'
import { useI18n } from '../i18n'
import { Link, navigate } from '../router'
import { ErrorBox, fmtTime, Ltr, SevBadge } from '../ui'

const SEVS: Severity[] = ['critical', 'high', 'medium', 'low', 'info']
const TOOLS = ['semgrep', 'gitleaks', 'trivy', 'ai'] as const
type Facet = 'sev' | 'tool' | 'file'
type View = 'findings' | 'json' | 'md'

function readUrl() {
  const q = new URLSearchParams(location.search)
  const list = (k: string) => (q.get(k) ?? '').split(',').filter(Boolean)
  return { sev: list('sev'), tool: list('tool'), file: list('file'), view: (q.get('view') as View) || 'findings', f: q.get('f') ?? '' }
}

const key = (f: Finding, k: Facet) => (k === 'sev' ? f.severity : k === 'tool' ? f.tool : f.file)

export default function ReportView({ id }: { id: string }) {
  const { t } = useI18n()
  const [rep, setRep] = useState<Report | null>(null)
  const [md, setMd] = useState<string | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const init = readUrl()
  const [sel, setSel] = useState<Record<Facet, string[]>>({ sev: init.sev, tool: init.tool, file: init.file })
  const [view, setView] = useState<View>(init.view)
  const [open, setOpen] = useState(init.f)

  useEffect(() => { api.reportJson(id).then(setRep).catch(setErr) }, [id])
  useEffect(() => { if (view === 'md' && md === null && rep) api.reportMd(id).then(setMd).catch(setErr) }, [view, md, rep, id])

  useEffect(() => {  // URL state (replaceState): reload/share restores the filters
    const q = new URLSearchParams()
    for (const k of ['sev', 'tool', 'file'] as Facet[]) if (sel[k].length) q.set(k, sel[k].join(','))
    if (view !== 'findings') q.set('view', view)
    if (open) q.set('f', open)
    const s = q.toString()
    history.replaceState(null, '', location.pathname + (s ? `?${s}` : ''))
  }, [sel, view, open])

  const findings = rep?.findings ?? []
  const match = (f: Finding, skip?: Facet) =>
    (['sev', 'tool', 'file'] as Facet[]).every(k => k === skip || !sel[k].length || sel[k].includes(key(f, k)))
  const shown = useMemo(() => findings.filter(f => match(f))
    .sort((a, b) => SEVS.indexOf(a.severity) - SEVS.indexOf(b.severity)), [findings, sel]) // eslint-disable-line
  const count = (k: Facet, v: string) => findings.filter(f => match(f, k) && key(f, k) === v).length
  const nActive = sel.sev.length + sel.tool.length + sel.file.length
  const toggle = (k: Facet, v: string) =>
    setSel(s => ({ ...s, [k]: s[k].includes(v) ? s[k].filter(x => x !== v) : [...s[k], v] }))

  if (err) {
    const e = err as ApiError
    return (
      <>
        <h1>{t('report_title')}</h1>
        {e.status === 410 ? (
          <div className="warn" role="alert" data-testid="expired"><h2>{t('expired_t')}</h2><p>{t('expired_b')}</p>
            <p className="row"><Link to={`/reviews/${id}`} className="btn">{t('back_progress')}</Link>
              <Link to="/" className="btn primary">{t('new_review')}</Link></p></div>
        ) : e.status === 409 ? (
          <div className="card" role="status"><p>{t('not_ready')}</p><Link to={`/reviews/${id}`}>{t('back_progress')}</Link></div>
        ) : <ErrorBox error={err} />}
      </>
    )
  }
  if (!rep) return <><h1>{t('report_title')}</h1><p aria-busy="true">{t('loading')}</p></>

  const files = [...new Set(findings.map(f => f.file).filter(Boolean))].sort()
  const chip = (k: Facet, v: string, label: React.ReactNode) => {
    const on = sel[k].includes(v), n = count(k, v)
    return (
      <button key={v} type="button" className={`chip${on ? ' on' : ''}`} aria-pressed={on} disabled={!on && n === 0}
        onClick={() => toggle(k, v)}>{label} <span className="muted">({n})</span></button>
    )
  }

  return (
    <div className="report">
      <p className="eyebrow"><button type="button" className="btn ghost small back"
        onClick={() => (history.length > 1 ? history.back() : navigate(`/reviews/${id}`))}><span className="arrow">←</span> {t('back')}</button></p>
      <h1>{t('report_title')}{rep.repo?.url && <>: <Ltr>{repoName(rep.repo.url)}@{rep.repo.ref ?? '—'}{rep.repo.commit_sha ? ` ${rep.repo.commit_sha.slice(0, 7)}` : ''}</Ltr></>}</h1>
      {rep.partial && <div className="warn" role="status" data-testid="partial">{t('partial_banner')}</div>}
      {rep.provider?.kind === 'cloud' && <div className="warn cloud" role="note">{t('cloud_banner', { name: rep.provider.name })}</div>}
      <div className="card meta">
        <p className="muted small">
          {rep.depth ? `${t(`depth_${rep.depth}`)} · ` : ''}{rep.provider ? <bdi dir="auto">{rep.provider.name}{rep.provider.model ? ` · ${rep.provider.model}` : ''}</bdi> : t('ai_none')}
          {' · '}{t('files_scanned', { n: rep.summary?.files_scanned ?? 0 })}
          {' · '}<Ltr>{fmtTime(rep.started_at)} – {fmtTime(rep.finished_at)}</Ltr>
        </p>
        <ul className="stats" aria-label={t('severity_counts')}>
          {SEVS.map(s => (
            <li key={s}><button type="button" className={`stat sev-${s}${sel.sev.includes(s) ? ' on' : ''}`} aria-pressed={sel.sev.includes(s)}
              onClick={() => toggle('sev', s)}>{t(`sev.${s}`).toUpperCase()}: <span className="n">{rep.summary?.[s] ?? 0}</span></button></li>
          ))}
        </ul>
        {rep.executive_summary && <div className="ai"><h2>✦ {t('ai_analysis')}</h2><div dir="auto"><ReactMarkdown remarkPlugins={[remarkGfm]}>{rep.executive_summary}</ReactMarkdown></div></div>}
        <div className="row">
          <a className="btn" href={`/api/reviews/${id}/report.json`} download={`report-${id}.json`}>{t('download_json')}</a>
          <a className="btn" href={`/api/reviews/${id}/report.md`} download={`report-${id}.md`}>{t('download_md')}</a>
        </div>
      </div>

      <div role="radiogroup" aria-label={t('view')} className="segmented">
        {(['findings', 'json', 'md'] as View[]).map(v => (
          <button key={v} type="button" role="radio" aria-checked={view === v} className={view === v ? 'on' : ''}
            onClick={() => setView(v)}>{t(`view_${v}`)}</button>
        ))}
      </div>

      {view === 'json' && <pre dir="ltr" className="code" tabIndex={0} aria-label={t('view_json')}>{JSON.stringify(rep, null, 2)}</pre>}
      {view === 'md' && (md === null ? <p aria-busy="true">{t('loading')}</p> :
        <pre dir="auto" className="code md" tabIndex={0} aria-label={t('view_md')} data-testid="md">{md}</pre>)}

      {view === 'findings' && (
        <section className="card" aria-labelledby="f-h">
          <h2 id="f-h">{t('view_findings')}</h2>
          {findings.length === 0 ? <p>{t('no_findings', { depth: t(`depth_${rep.depth}`) })}</p> : <>
            <div className="filters" role="group" aria-label={t('filters')}>
              <div role="group" aria-label={t('f_severity')}><span className="fl">{t('f_severity')}</span>{SEVS.map(s => chip('sev', s, t(`sev.${s}`)))}</div>
              <div role="group" aria-label={t('f_tool')}><span className="fl">{t('f_tool')}</span>{TOOLS.map(x => chip('tool', x, <Ltr>{x}</Ltr>))}</div>
              <div role="group" aria-label={t('f_file')}><span className="fl">{t('f_file')}</span>{files.map(x => chip('file', x, <Ltr>{x}</Ltr>))}</div>
            </div>
            <p aria-live="polite" className="row">
              {t('showing', { x: shown.length, y: findings.length })}
              {nActive > 0 && <button type="button" className="btn ghost small" onClick={() => setSel({ sev: [], tool: [], file: [] })}>{t('clear_all', { n: nActive })}</button>}
            </p>
            {shown.length === 0 ? <p>{t('no_match')}</p> : (
              <table>
                <thead><tr><th scope="col">{t('col_sev')}</th><th scope="col">{t('col_finding')}</th><th scope="col">{t('col_tool')}</th></tr></thead>
                <tbody>{shown.map(f => (
                  <FindingRow key={f.id} f={f} open={open === f.id} onToggle={() => setOpen(open === f.id ? '' : f.id)} />
                ))}</tbody>
              </table>
            )}
          </>}
        </section>
      )}
    </div>
  )
}

function FindingRow({ f, open, onToggle }: { f: Finding; open: boolean; onToggle: () => void }) {
  const { t } = useI18n()
  const loc = f.line ? `${f.file}:${f.line}${f.line_end && f.line_end !== f.line ? `–${f.line_end}` : ''}` : f.file
  return (
    <>
      <tr>
        <td><SevBadge sev={f.severity} /></td>
        <th scope="row">
          <button type="button" className="linklike" aria-expanded={open} aria-controls={`fd-${f.id}`}
            aria-label={t('show_detail', { title: f.title })} onClick={onToggle}><bdi dir="auto">{f.title}</bdi></button>
          <div className="mono small"><Ltr>{loc}</Ltr> · <Ltr>{f.rule}</Ltr></div>
        </th>
        <td><span className="badge"><Ltr>{f.tool}</Ltr></span></td>
      </tr>
      {open && (
        <tr id={`fd-${f.id}`}><td colSpan={3} className="detail">
          {f.description && <p dir="auto">{f.description}</p>}
          <p className="row">
            {f.category && <span className="badge"><Ltr>{f.category}</Ltr></span>}
            {(f.cwe ?? []).map(c => <span key={c} className="badge"><Ltr>{c}</Ltr></span>)}
            {f.cve && <span className="badge"><Ltr>{f.cve}</Ltr></span>}
            {f.secret_redacted && <span className="badge">{t('secret_redacted')}</span>}
            {f.ai_severity && <span className="badge ai">✦ {f.ai_severity === f.severity ? t('ai_agrees') : t('ai_sev', { sev: t(`sev.${f.ai_severity}`) })}</span>}
          </p>
          {f.package && <p><Ltr>{f.package}</Ltr> · {f.fixed_version ? t('fixed_in', { v: f.fixed_version }) : t('no_fix')}</p>}
          {f.snippet ? <pre dir="ltr" className="code">{f.snippet.split('\n').slice(0, 10).join('\n')}</pre> : <p className="muted">{t('no_snippet')}</p>}
          {(f.ai_explanation || f.ai_fix) && (
            <div className="ai"><h3>✦ {t('ai_analysis')}{f.confidence != null ? ` · ${Math.round(f.confidence * 100)}%` : ''}</h3>
              {f.ai_explanation && <p dir="auto">{f.ai_explanation}</p>}
              {f.ai_fix && <div dir="auto"><ReactMarkdown remarkPlugins={[remarkGfm]}>{f.ai_fix}</ReactMarkdown></div>}
            </div>
          )}
        </td></tr>
      )}
    </>
  )
}
