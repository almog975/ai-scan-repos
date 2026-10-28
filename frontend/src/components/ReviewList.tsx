import { useEffect } from 'react'
import { api, Review } from '../api'

export const ACTIVE = new Set(['queued', 'running'])

export default function ReviewList({ reviews, onChange, onOpen, refresh, pollMs = 3000 }: {
  reviews: Review[]; onChange: (r: Review) => void; onOpen: (r: Review) => void
  refresh: () => void; pollMs?: number
}) {
  const anyActive = reviews.some(r => ACTIVE.has(r.status))
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
            <td>{r.ai_provider ?? '—'}</td>
            <td><span className={`badge ${r.status}`}>{r.status}</span>{r.message && <small> {r.message}</small>}</td>
            <td><progress max={100} value={r.progress} aria-label={`progress ${r.id}`} /> {r.progress}%</td>
            <td>{r.attempts}</td>
            <td>
              {ACTIVE.has(r.status) && <button onClick={async () => onChange(await api.pause(r.id))}>Pause</button>}
              {r.status === 'paused' && <button onClick={async () => onChange(await api.resume(r.id))}>Resume</button>}
              {r.status === 'completed' && <button onClick={() => onOpen(r)}>Report</button>}
            </td>
          </tr>))}
      </tbody>
    </table>
  )
}
