import { FormEvent, useEffect, useState } from 'react'
import { api, PROVIDERS } from '../api'

export default function AIKeySettings() {
  const [keys, setKeys] = useState<string[]>([])
  const [provider, setProvider] = useState('openai')
  const [key, setKey] = useState('')
  const [msg, setMsg] = useState('')
  const load = () => api.listKeys().then(k => setKeys(k.map(x => x.provider))).catch(e => setMsg(e.message))
  useEffect(() => { load() }, [])
  async function save(e: FormEvent) {
    e.preventDefault()
    try { await api.putKey(provider, key); setKey(''); setMsg(`Saved key for ${provider}`); load() }
    catch (err) { setMsg((err as Error).message) }
  }
  return (
    <form className="card" onSubmit={save} aria-label="ai keys">
      <h2>AI provider keys</h2>
      <p className="hint">Keys are encrypted at rest and only injected into the scan Job as a short-lived Kubernetes Secret. They are never shown again.</p>
      <ul>{PROVIDERS.filter(p => p.id !== 'none').map(p =>
        <li key={p.id}>{p.label}: {keys.includes(p.id) ? '✅ configured' : '— not set'}</li>)}</ul>
      <label>Provider
        <select value={provider} onChange={e => setProvider(e.target.value)}>
          {PROVIDERS.filter(p => p.id !== 'none').map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
      </label>
      <label>API key<input type="password" autoComplete="off" value={key} onChange={e => setKey(e.target.value)} /></label>
      <button type="submit" disabled={!key}>Save key</button>
      {msg && <p className="hint">{msg}</p>}
    </form>
  )
}
