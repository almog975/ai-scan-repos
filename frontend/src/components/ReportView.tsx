import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'

const ORDER = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO', 'UNKNOWN']

export default function ReportView({ id, onBack }: { id: string; onBack: () => void }) {
  const [json, setJson] = useState<Record<string, any> | null>(null)
  const [md, setMd] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    Promise.all([api.reportJson(id), api.reportMd(id)])
      .then(([j, m]) => { setJson(j); setMd(m) })
      .catch(e => setError(e.message))
  }, [id])
  if (error) return <div className="card"><button onClick={onBack}>← Back</button><p className="error">{error}</p></div>
  if (!json) return <p className="card">Loading report…</p>
  const counts: Record<string, number> = json.severity_counts ?? {}
  return (
    <div className="card report">
      <button onClick={onBack}>← Back</button>
      {json.ai?.code_left_network && <p className="warning" role="alert">⚠️ Code from this repository was sent to cloud AI ({json.ai.provider}).</p>}
      <div className="sev-row" aria-label="severity counts">
        {ORDER.filter(s => counts[s]).map(s => <span key={s} className={`sev ${s.toLowerCase()}`}>{s}: {counts[s]}</span>)}
        {!Object.keys(counts).length && <span className="sev none">No findings</span>}
      </div>
      {/* react-markdown does not render raw HTML: safe against HTML in findings */}
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{md}</ReactMarkdown>
    </div>
  )
}
