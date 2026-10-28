import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, Me, Review } from './api'
import AIKeySettings from './components/AIKeySettings'
import NewReviewForm from './components/NewReviewForm'
import ReportView from './components/ReportView'
import ReviewList from './components/ReviewList'

type View = { name: 'reviews' } | { name: 'settings' } | { name: 'report'; id: string }

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined)
  const [reviews, setReviews] = useState<Review[]>([])
  const [view, setView] = useState<View>({ name: 'reviews' })

  useEffect(() => {
    api.me().then(setMe).catch(e => setMe(e instanceof ApiError && e.status === 401 ? null : null))
  }, [])
  const refresh = useCallback(() => { api.listReviews().then(setReviews).catch(() => undefined) }, [])
  useEffect(() => { if (me) refresh() }, [me, refresh])
  const upsert = (r: Review) => setReviews(rs => [r, ...rs.filter(x => x.id !== r.id)])

  if (me === undefined) return <p>Loading…</p>
  if (me === null) return (
    <main className="login card">
      <h1>AI Scan Repos</h1>
      <p>Security review for GitHub repositories: Semgrep, Gitleaks, Trivy + AI.</p>
      <a className="button" href="/auth/login">Log in with GitHub</a>
    </main>)

  return (
    <div className="app">
      <header>
        <h1>AI Scan Repos</h1>
        <nav>
          <button onClick={() => setView({ name: 'reviews' })}>Reviews</button>
          <button onClick={() => setView({ name: 'settings' })}>AI keys</button>
        </nav>
        <span>@{me.login} <button onClick={() => api.logout().then(() => setMe(null))}>Log out</button></span>
      </header>
      <main>
        {view.name === 'reviews' && <>
          <NewReviewForm onCreated={upsert} />
          <ReviewList reviews={reviews} onChange={upsert} refresh={refresh}
            onOpen={r => setView({ name: 'report', id: r.id })} />
        </>}
        {view.name === 'settings' && <AIKeySettings />}
        {view.name === 'report' && <ReportView id={view.id} onBack={() => setView({ name: 'reviews' })} />}
      </main>
    </div>)
}
