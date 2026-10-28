// Live review updates. SSE (/api/reviews/{id}/progress) is the main path; every SSE event triggers a
// GET /api/reviews/{id} so the row/page always shows the contract Review shape. Polling is only a fallback.
import { useEffect, useRef } from 'react'
import { api, TERMINAL, type Review } from './api/client'

export const MAX_STREAMS = 3  // browsers allow ~6 HTTP/1.1 connections per origin; keep room for API calls

export function subscribe(id: string, onReview: (r: Review) => void, onFail: () => void, onEvent?: (type: string, data: unknown) => void) {
  if (typeof EventSource === 'undefined') { onFail(); return () => {} }
  let closed = false
  let inflight = false, again = false
  const pull = async () => {
    if (inflight) { again = true; return }
    inflight = true
    try { onReview(await api.getReview(id)) } catch { /* next event retries */ } finally {
      inflight = false
      if (again) { again = false; pull() }
    }
  }
  const es = new EventSource(`/api/reviews/${id}/progress`, { withCredentials: true })
  const on = (type: string) => (ev: Event) => {
    let data: unknown = null
    try { data = JSON.parse((ev as MessageEvent).data) } catch { /* keep null */ }
    onEvent?.(type, data)
    pull()
    if (type === 'done') { closed = true; es.close() }
  }
  for (const ty of ['progress', 'state', 'done']) es.addEventListener(ty, on(ty))
  es.onerror = () => {
    if (closed) return
    if (es.readyState === EventSource.CLOSED) { closed = true; onFail() }
  }
  return () => { closed = true; es.close() }
}

/** Keep `reviews` live: one SSE stream per non-terminal review (up to MAX_STREAMS); poll the list every 3 s
 *  only for reviews without a stream (fallback) that are still moving (paused reviews don't poll). */
export function useLiveReviews(reviews: Review[] | null, update: (r: Review) => void, reload: () => void) {
  const failed = useRef(new Set<string>())
  const active = (reviews ?? []).filter(r => !TERMINAL.includes(r.state))
  const streamed = active.filter(r => !failed.current.has(r.id)).slice(0, MAX_STREAMS)
  const key = streamed.map(r => r.id).join(',')
  const upd = useRef(update); upd.current = update

  useEffect(() => {
    const offs = streamed.map(r => subscribe(r.id, rv => upd.current(rv), () => { failed.current.add(r.id); reload() }))
    return () => offs.forEach(o => o())
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps

  const needPoll = active.some(r => r.state !== 'paused' && !streamed.some(s => s.id === r.id))
  useEffect(() => {
    if (!needPoll) return
    const h = setInterval(reload, 3000)
    return () => clearInterval(h)
  }, [needPoll, reload])
}
