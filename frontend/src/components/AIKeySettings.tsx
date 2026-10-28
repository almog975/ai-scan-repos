import { useEffect, useState } from 'react'
import { api, ProviderPublic } from '../api'

// AI providers are configured by an admin (POST /api/admin/ai-providers); users only see the public list.
export default function AIKeySettings() {
  const [providers, setProviders] = useState<ProviderPublic[]>([])
  const [msg, setMsg] = useState('')
  useEffect(() => { api.providers().then(setProviders).catch(e => setMsg(e.message)) }, [])
  return (
    <div className="card" aria-label="ai providers">
      <h2>AI providers</h2>
      <p className="hint">Providers and their keys are managed by an admin. Keys are encrypted at rest and never shown.</p>
      <ul>{providers.map(p => <li key={p.id}>{p.name} ({p.kind === 'cloud' ? 'cloud' : 'on-prem'})</li>)}</ul>
      {!providers.length && !msg && <p className="hint">No AI providers configured: scanners only.</p>}
      {msg && <p className="hint">{msg}</p>}
    </div>
  )
}
