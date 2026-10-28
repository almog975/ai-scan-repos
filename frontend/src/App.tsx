import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type Me } from './api/client'
import { useI18n, type Lang } from './i18n'
import { Link, navigate, parse, usePath, type Route } from './router'
import { ErrorBox } from './ui'
import Admin from './views/Admin'
import Home from './views/Home'
import Login from './views/Login'
import ProgressView from './views/Progress'
import ReportView from './views/Report'

const TITLE: Record<Route['name'], string> = {
  login: 'login_title', home: 'home_title', progress: 'progress_title', report: 'report_title', admin: 'admin_title', notfound: 'not_found',
}

export default function App() {
  const { t, lang, setLang } = useI18n()
  const path = usePath()
  const route = parse(path)
  const [me, setMe] = useState<Me | null | undefined>(undefined)
  const [err, setErr] = useState<unknown>(null)
  const main = useRef<HTMLElement>(null)
  const first = useRef(true)

  useEffect(() => {
    api.me().then(setMe).catch(e => {
      if (e instanceof ApiError && e.status === 401) setMe(null)
      else { setErr(e); setMe(null) }
    })
  }, [])

  useEffect(() => { if (me === null && route.name !== 'login') navigate('/login') }, [me, route.name])

  // Document title + focus the main heading on every view change (not on first load).
  useEffect(() => {
    document.title = `${t(TITLE[route.name])} · ${t('app')}`
    if (first.current) { first.current = false; return }
    const h = main.current?.querySelector<HTMLElement>('h1')
    if (h) { h.tabIndex = -1; h.focus() }
  }, [path, route.name, t])

  const logout = async () => {
    try { await api.logout() } catch { /* session may already be gone */ }
    setMe(null)
    navigate('/login')
  }

  const view = (() => {
    if (route.name === 'login') return <Login />
    if (me === undefined) return <p aria-busy="true">{t('loading')}</p>
    if (me === null) return null
    switch (route.name) {
      case 'home': return <Home />
      case 'progress': return <ProgressView id={route.id} />
      case 'report': return <ReportView id={route.id} />
      case 'admin': return <Admin me={me} />
      default: return <h1>{t('not_found')}</h1>
    }
  })()

  const nav = (to: string, label: string, active: boolean) =>
    <li><Link to={to} aria-current={active ? 'page' : undefined} className="navitem">{label}</Link></li>

  return (
    <>
      <a href="#main" className="skip">{t('skip')}</a>
      <div className={me ? 'shell' : 'shell bare'}>
        {me && (
          <aside className="sidebar">
            <div className="brand">{t('app')}</div>
            <nav aria-label={t('nav_main')}>
              <ul>
                {nav('/', t('nav_new'), ['home', 'progress', 'report'].includes(route.name))}
                {me.role === 'admin' && nav('/admin/ai-providers', t('nav_providers'), route.name === 'admin')}
              </ul>
            </nav>
            <div className="userchip">
              <span>@<bdi dir="ltr">{me.github_login}</bdi></span>
              <label className="lang">{t('lang_switch')}
                <select value={lang} onChange={e => setLang(e.target.value as Lang)}>
                  <option value="he" lang="he">עברית</option>
                  <option value="en" lang="en">English</option>
                </select>
              </label>
              <button type="button" className="btn ghost small" onClick={logout}>{t('logout')}</button>
            </div>
          </aside>
        )}
        <main id="main" ref={main} tabIndex={-1}>
          {!me && route.name === 'login' && (
            <div className="row end">
              <label className="lang">{t('lang_switch')}
                <select value={lang} onChange={e => setLang(e.target.value as Lang)}>
                  <option value="he" lang="he">עברית</option>
                  <option value="en" lang="en">English</option>
                </select>
              </label>
            </div>
          )}
          <ErrorBox error={err} onDismiss={() => setErr(null)} />
          {view}
        </main>
      </div>
    </>
  )
}
