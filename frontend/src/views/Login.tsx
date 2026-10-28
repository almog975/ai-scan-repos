import { useState } from 'react'
import { LOGIN_URL } from '../api/client'
import { useI18n } from '../i18n'

const ERRORS: Record<string, string> = { state: 'login_err_state', github: 'login_err_github', denied: 'login_err_denied' }

export default function Login() {
  const { t } = useI18n()
  const [busy, setBusy] = useState(false)
  const code = new URLSearchParams(location.search).get('error') ?? ''
  return (
    <section className="card narrow">
      <h1>{t('login_title')}</h1>
      <p className="muted">{t('login_lead')}</p>
      {ERRORS[code] && <p role="alert" className="error">{t(ERRORS[code])}</p>}
      <a className="btn primary" href={LOGIN_URL} onClick={() => setBusy(true)} aria-busy={busy}>
        {busy ? t('login_redirecting') : t('login_btn')}
      </a>
    </section>
  )
}
