import { useEffect } from 'react'
import { api, progressOf, Review } from '../api'

export const ACTIVE = new Set(['queued', 'running', 'retrying', 'stuck'])

export default function ReviewList({ reviews, onChange, onOpen, refresh, pollMs = 3000 }: {
  reviews: Review[]; onChange: (r: Review) => void; onOpen: (r: Review) => void
  refresh: () => void; pollMs?: number
}) {
  const anyActive = reviews.some(r => ACTIVE.has(r.state))
  useEffect(() => {
    if (!anyActive) return
    const t = setInterval(refresh, pollMs)
    return () => clearInterval(t)
  }, [anyActive, pollMs, refresh])

  if (!reviews.length) return <p className="card">No reviews yet.</p>
  return (
    <table className="card reviews">
      <thead><tr><th>Repository</th><th>Depth</th><th>AI</th><th>Status</th><th>Progress</th><th>Attempts</th><th /></tr></thead>
      <tbody>
        {reviews.map(r => (
          <tr key={r.id}>
            <td>{r.repo_url.replace('https://github.com/', '')}</td>
            <td>{r.depth}</td>
            <td>{r.provider ? r.provider.kind : '—'}</td>
            <td><span className={`badge ${r.state}`}>{r.state}</span>{r.failure_reason && <small> {r.failure_reason}</small>}</td>
            <td><progress max={100} value={progressOf(r)} aria-label={`progress ${r.id}`} /> {progressOf(r)}%</td>
            <td>{r.attempt}</td>
            <td>
              {ACTIVE.has(r.state) && <button onClick={async () => onChange(await api.pause(r.id))}>Pause</button>}
              {(r.state === 'paused' || r.state === 'stuck') && <button onClick={async () => onChange(await api.resume(r.id))}>Resume</button>}
              {r.state === 'completed' && <button onClick={() => onOpen(r)}>Report</button>}
            </td>
          </tr>))}
      </tbody>
    </table>
  )
}
