import { FormEvent, useState } from 'react'
import { api, Depth, GITHUB_URL, isCloud, PROVIDERS, Review } from '../api'

export default function NewReviewForm({ onCreated }: { onCreated: (r: Review) => void }) {
  const [url, setUrl] = useState('')
  const [depth, setDepth] = useState<Depth>('quick')
  const [provider, setProvider] = useState('openai_compatible')
  const [confirmed, setConfirmed] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const cloud = isCloud(provider)
  const validUrl = GITHUB_URL.test(url.trim())
  const canSubmit = validUrl && (!cloud || confirmed) && !busy

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setBusy(true); setError('')
    try {
      onCreated(await api.createReview(url.trim(), depth, provider === 'none' ? null : provider))
      setUrl(''); setConfirmed(false)
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }

  return (
    <form className="card" onSubmit={submit} aria-label="new review">
      <h2>New review</h2>
      <label>GitHub repository URL
        <input value={url} onChange={e => setUrl(e.target.value)} placeholder="https://github.com/owner/repo" />
      </label>
      {url && !validUrl && <p className="hint error">Must be https://github.com/&lt;owner&gt;/&lt;repo&gt;</p>}
      <fieldset><legend>Depth</legend>
        {(['quick', 'brief', 'deep'] as Depth[]).map(d => (
          <label key={d} className="inline">
            <input type="radio" name="depth" value={d} checked={depth === d} onChange={() => setDepth(d)} />
            {d[0].toUpperCase() + d.slice(1)}
          </label>))}
      </fieldset>
      <label>AI provider
        <select value={provider} onChange={e => { setProvider(e.target.value); setConfirmed(false) }}>
          {PROVIDERS.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
      </label>
      {cloud && (
        <div className="warning" role="alert">
          <strong>⚠️ Cloud AI selected.</strong> Source code snippets and scanner findings from this repository
          will be sent to a third-party AI service outside your network. Do not use this for private or
          sensitive code unless you are allowed to.
          <label className="inline">
            <input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />
            I understand and agree to send code to {PROVIDERS.find(p => p.id === provider)?.label}
          </label>
        </div>)}
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={!canSubmit}>{busy ? 'Starting…' : 'Start review'}</button>
    </form>
  )
}
