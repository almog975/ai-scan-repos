// Minimal i18n: keyed catalogs (en, he), {placeholder} interpolation, <html lang dir> switching, persisted choice.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { en } from './en'
import { he } from './he'

export type Lang = 'en' | 'he'
export type Key = keyof typeof en
const CATALOGS: Record<Lang, Record<Key, string>> = { en, he }
const STORE = 'aiscan_lang'

export function format(lang: Lang, key: Key | string, vars: Record<string, string | number> = {}): string {
  const s = (CATALOGS[lang] as Record<string, string>)[key] ?? (en as Record<string, string>)[key] ?? key
  return s.replace(/\{(\w+)\}/g, (_, k) => (k in vars ? String(vars[k]) : `{${k}}`))
}

interface Ctx { lang: Lang; dir: 'ltr' | 'rtl'; setLang: (l: Lang) => void; t: (k: Key | string, v?: Record<string, string | number>) => string }
const I18n = createContext<Ctx | null>(null)

function initial(): Lang {
  const q = new URLSearchParams(location.search).get('lang')
  if (q === 'he' || q === 'en') return q
  const s = localStorage.getItem(STORE)
  return s === 'he' ? 'he' : 'en'
}

export function I18nProvider({ children, lang: forced }: { children: ReactNode; lang?: Lang }) {
  const [lang, setLangState] = useState<Lang>(forced ?? initial)
  const dir = lang === 'he' ? 'rtl' : 'ltr'
  useEffect(() => {
    document.documentElement.lang = lang
    document.documentElement.dir = dir
  }, [lang, dir])
  const setLang = useCallback((l: Lang) => { localStorage.setItem(STORE, l); setLangState(l) }, [])
  const t = useCallback((k: Key | string, v?: Record<string, string | number>) => format(lang, k, v), [lang])
  const value = useMemo(() => ({ lang, dir, setLang, t }), [lang, dir, setLang, t]) as Ctx
  return <I18n.Provider value={value}>{children}</I18n.Provider>
}

export function useI18n(): Ctx {
  const c = useContext(I18n)
  if (!c) throw new Error('I18nProvider missing')
  return c
}
