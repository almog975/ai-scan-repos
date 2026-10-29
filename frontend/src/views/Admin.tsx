import { useEffect, useId, useState, type FormEvent } from 'react'
import { api, ApiError, type AIProvider, type AIProviderPatch, type AIProviderWrite, type Me, type ProviderTestResult } from '../api/client'
import { useI18n } from '../i18n'
import { CheckIcon, Confirm, ErrorBox, fmtTime, KindBadge, Ltr, PencilIcon, TrashIcon, XIcon } from '../ui'

type Kind = 'onprem' | 'cloud'
interface Form { name: string; kind: Kind; base_url: string; model: string; api_key: string; enabled: boolean; ack: boolean }
const EMPTY: Form = { name: '', kind: 'onprem', base_url: '', model: '', api_key: '', enabled: true, ack: false }

function TestResult({ res, err }: { res: ProviderTestResult | null; err: unknown }) {
  const { t } = useI18n()
  if (res) return <p className="ok" role="status">{t('test_ok', { model: res.model, ms: res.latency_ms })}</p>
  if (!err) return null
  const code = err instanceof ApiError ? err.code : ''
  const known = code.startsWith('AI_PROVIDER_')
  return (
    <ErrorBox error={err}>
      {known && <><p><strong>{t(`E.${code}`)}</strong></p><p>{t(`E.${code}.h`)}</p></>}
    </ErrorBox>
  )
}

export default function Admin({ me }: { me: Me }) {
  const { t } = useI18n()
  const [list, setList] = useState<AIProvider[] | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [msg, setMsg] = useState('')
  const [editing, setEditing] = useState<AIProvider | 'new' | null>(null)
  const [del, setDel] = useState<AIProvider | null>(null)
  const [inUse, setInUse] = useState<{ p: AIProvider; e: unknown } | null>(null)
  const [rowTest, setRowTest] = useState<Record<string, { busy?: boolean; res?: ProviderTestResult; err?: unknown }>>({})

  const load = () => api.adminProviders().then(setList).catch(setErr)
  useEffect(() => { if (me.role === 'admin') load() }, [me.role])

  if (me.role !== 'admin') return <><h1>{t('admin_title')}</h1><p role="alert" className="error">{t('admin_forbidden')}</p></>

  const testRow = async (p: AIProvider) => {
    setRowTest(s => ({ ...s, [p.id]: { busy: true } }))
    try { const res = await api.testProvider(p.id); setRowTest(s => ({ ...s, [p.id]: { res } })) }
    catch (e) { setRowTest(s => ({ ...s, [p.id]: { err: e } })) }
  }
  const doDelete = async (p: AIProvider) => {
    setDel(null)
    try { await api.deleteProvider(p.id); setMsg(t('deleted')); load() }
    catch (e) { if (e instanceof ApiError && e.code === 'PROVIDER_IN_USE') setInUse({ p, e }); else setErr(e) }
  }
  const disable = async (p: AIProvider) => {
    setInUse(null)
    try { await api.updateProvider(p.id, { enabled: false }); setMsg(t('saved')); load() } catch (e) { setErr(e) }
  }

  return (
    <>
      <h1>{t('admin_title')}</h1>
      <div className="warn" role="note">{t('admin_warn')}</div>
      <p aria-live="polite" className="ok">{msg}</p>
      <ErrorBox error={err} onDismiss={() => setErr(null)} />
      {inUse && (
        <ErrorBox error={inUse.e} onDismiss={() => setInUse(null)}>
          <p>{t('in_use')}</p>
          <button type="button" className="btn" onClick={() => disable(inUse.p)}>{t('disable_instead')}</button>
        </ErrorBox>
      )}
      <p><button type="button" className="btn primary" onClick={() => setEditing('new')}>{t('add_provider')}</button></p>
      {editing && <ProviderForm key={editing === 'new' ? 'new' : editing.id} p={editing === 'new' ? null : editing}
        onClose={() => setEditing(null)} onSaved={() => { setEditing(null); setMsg(t('saved')); load() }} />}
      {list === null ? <p aria-busy="true">{t('loading')}</p> : list.length === 0 ? <p>{t('add_onprem_cta')}</p> : (
        <table className="card">
          <thead><tr>
            <th scope="col">{t('col_name')}</th><th scope="col">{t('col_kind')}</th><th scope="col">{t('col_model')}</th>
            <th scope="col">{t('col_key')}</th><th scope="col">{t('col_enabled')}</th><th scope="col">{t('col_actions')}</th>
          </tr></thead>
          <tbody>{list.map(p => {
            const rt = rowTest[p.id] ?? {}
            return (
              <tr key={p.id}>
                <th scope="row"><bdi dir="auto">{p.name}</bdi><div className="mono small"><Ltr>{p.base_url ?? ''}</Ltr></div></th>
                <td><KindBadge kind={p.kind} />
                  {p.kind === 'cloud' && p.cloud_ack.acknowledged && <div className="muted small">
                    {t('acked_by', { login: `\u2068@${p.cloud_ack.by?.login ?? '—'}\u2069`, at: `\u2068${fmtTime(p.cloud_ack.at)}\u2069` })}</div>}
                </td>
                <td><Ltr>{p.model}</Ltr></td>
                <td><Ltr>{p.key_masked ?? '—'}</Ltr></td>
                <td><span className={`badge ${p.enabled ? 'state-completed' : 'state-cancelled'}`}>{p.enabled ? <CheckIcon /> : <XIcon />}{p.enabled ? t('enabled') : t('disabled')}</span></td>
                <td>
                  <div className="row">
                    <button type="button" className="btn small" aria-label={t('test_p', { name: p.name })} disabled={rt.busy}
                      onClick={() => testRow(p)}>{rt.busy ? t('testing') : t('test')}</button>
                    <button type="button" className="btn small" aria-label={t('edit_p', { name: p.name })} onClick={() => setEditing(p)}><PencilIcon /></button>
                    <button type="button" className="btn small danger" aria-label={t('delete_p', { name: p.name })} onClick={() => setDel(p)}><TrashIcon /></button>
                  </div>
                  <div aria-live="polite"><TestResult res={rt.res ?? null} err={rt.err} /></div>
                </td>
              </tr>
            )
          })}</tbody>
        </table>
      )}
      {del && <Confirm title={t('delete_confirm_t', { name: del.name })} body={<p>{t('delete_confirm_b')}</p>} danger
        ok={t('delete')} safe={t('keep_provider')} onSafe={() => setDel(null)} onOk={() => doDelete(del)} />}
    </>
  )
}

function ProviderForm({ p, onClose, onSaved }: { p: AIProvider | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n()
  const [f, setF] = useState<Form>(p ? { name: p.name, kind: p.kind, base_url: p.base_url ?? '', model: p.model, api_key: '',
    enabled: p.enabled, ack: p.kind === 'cloud' && p.cloud_ack.acknowledged } : EMPTY)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<unknown>(null)
  const [test, setTest] = useState<{ busy?: boolean; res?: ProviderTestResult; err?: unknown }>({})
  const hid = useId(), reasonId = useId(), ackId = useId(), keyHint = useId()
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF(s => ({ ...s, [k]: v }))
  const dirty = !!p && (f.name !== p.name || f.kind !== p.kind || f.base_url !== (p.base_url ?? '') || f.model !== p.model
    || f.api_key !== '' || f.enabled !== p.enabled || (f.kind === 'cloud' && f.ack !== (p.kind === 'cloud' && p.cloud_ack.acknowledged)))
  const switchedToCloud = !!p && p.kind !== 'cloud' && f.kind === 'cloud'
  const reason = !f.name.trim() || !f.model.trim() ? t('save_disabled_fields') : f.kind === 'cloud' && !f.ack ? t('save_disabled_ack') : ''

  const writeBody = (): AIProviderWrite => ({ name: f.name.trim(), kind: f.kind, model: f.model.trim(), base_url: f.base_url.trim() || null,
    api_key: f.api_key || null, enabled: f.enabled, ...(f.kind === 'cloud' ? { cloud_ack: { acknowledged: f.ack } } : {}) })

  const runTest = async () => {
    setTest({ busy: true })
    // Unsaved changes (or a new provider) → test the form body; otherwise the stored provider (incl. its stored key).
    // Contract gap: AIProviderWrite has no provider id, so an unsaved edit without a re-typed key is tested without
    // the stored key (docs/contract-gaps.md#test-unsaved-stored-key).
    try { setTest({ res: !p || dirty ? await api.testUnsaved(writeBody()) : await api.testProvider(p.id) }) }
    catch (e) { setTest({ err: e }) }
  }

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (reason) return
    setBusy(true); setErr(null)
    try {
      if (!p) await api.createProvider(writeBody())
      else {
        const body: AIProviderPatch = { name: f.name.trim(), model: f.model.trim(), base_url: f.base_url.trim() || null, enabled: f.enabled }
        if (f.api_key) body.api_key = f.api_key
        if (f.kind !== p.kind) body.kind = f.kind
        if (f.kind === 'cloud' && (f.kind !== p.kind || !p.cloud_ack.acknowledged)) body.cloud_ack = { acknowledged: f.ack }
        await api.updateProvider(p.id, body)
      }
      onSaved()
    } catch (x) { setErr(x) } finally { setBusy(false) }
  }

  return (
    <form className="card" aria-labelledby={hid} onSubmit={save} noValidate>
      <h2 id={hid}>{p ? t('edit_provider') : t('add_provider')}</h2>
      <div className="field"><label htmlFor="pv-name">{t('name')}</label>
        <input id="pv-name" dir="auto" required value={f.name} onChange={e => set('name', e.target.value)} aria-invalid={!f.name.trim() || undefined} /></div>
      <fieldset className="kind"><legend>{t('kind')}</legend>
        {(['onprem', 'cloud'] as Kind[]).map(k => (
          <label key={k} className="check"><input type="radio" name="kind" value={k} checked={f.kind === k}
            onChange={() => setF(s => ({ ...s, kind: k, ack: k === 'cloud' && p?.kind === 'cloud' ? p.cloud_ack.acknowledged : false }))} /> {t(k)}</label>
        ))}
      </fieldset>
      {f.kind === 'cloud' && (
        <div className="warn strong" role={switchedToCloud ? 'alert' : 'note'} id={ackId}>
          <p>{t('cloud_warn')}</p>
          {switchedToCloud && <p>{t('ack_again')}</p>}
          {p?.kind === 'cloud' && p.cloud_ack.acknowledged && <p className="small">
            {t('acked_by', { login: `\u2068@${p.cloud_ack.by?.login ?? '—'}\u2069`, at: `\u2068${fmtTime(p.cloud_ack.at)}\u2069` })}</p>}
          <label className="check"><input type="checkbox" required checked={f.ack} onChange={e => set('ack', e.target.checked)} /> {t('admin_cloud_ack')}</label>
        </div>
      )}
      <div className="field"><label htmlFor="pv-url">{t('base_url')}</label>
        <input id="pv-url" dir="ltr" type="url" value={f.base_url} onChange={e => set('base_url', e.target.value)} /></div>
      <div className="field"><label htmlFor="pv-model">{t('model')}</label>
        <input id="pv-model" dir="ltr" required value={f.model} onChange={e => set('model', e.target.value)} aria-invalid={!f.model.trim() || undefined} /></div>
      <div className="field"><label htmlFor="pv-key">{t('api_key')}</label>
        <input id="pv-key" dir="ltr" type="password" autoComplete="off" value={f.api_key} onChange={e => set('api_key', e.target.value)}
          aria-describedby={p ? keyHint : undefined} />
        {p && <span id={keyHint} className="hint">{t('api_key_keep')}</span>}</div>
      <label className="check"><input type="checkbox" checked={f.enabled} onChange={e => set('enabled', e.target.checked)} /> {t('enabled')}</label>
      <div className="row">
        <button type="button" className="btn" onClick={runTest} disabled={test.busy || !f.model.trim()}>{test.busy ? t('testing') : t('test')}</button>
        <button type="submit" className="btn primary" disabled={!!reason || busy} aria-describedby={reason ? reasonId : undefined}>{t('save')}</button>
        <button type="button" className="btn ghost" onClick={onClose}>{t('close')}</button>
        {reason && <span id={reasonId} className="reason">{reason}</span>}
      </div>
      <div aria-live="polite"><TestResult res={test.res ?? null} err={test.err} /></div>
      <ErrorBox error={err} onDismiss={() => setErr(null)} />
    </form>
  )
}
