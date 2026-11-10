// Tiny history router: path patterns -> views. navigate() pushes state; links are real <a href>.
import { useEffect, useState, type MouseEvent, type ReactNode } from 'react'

export type Route =
  | { name: 'login' } | { name: 'home' } | { name: 'progress'; id: string }
  | { name: 'report'; id: string } | { name: 'admin' } | { name: 'notfound' }

export function parse(path: string): Route {
  let m
  if (path === '/' || path === '') return { name: 'home' }
  if (path === '/login') return { name: 'login' }
  if (path === '/admin/ai-providers') return { name: 'admin' }
  if ((m = path.match(/^\/reviews\/([0-9a-f-]{36})\/report$/))) return { name: 'report', id: m[1] }
  if ((m = path.match(/^\/reviews\/([0-9a-f-]{36})$/))) return { name: 'progress', id: m[1] }
  return { name: 'notfound' }
}

export function navigate(to: string) {
  if (to === location.pathname + location.search) return
  history.pushState(null, '', to)
  dispatchEvent(new PopStateEvent('popstate'))
}

export function usePath(): string {
  const [p, setP] = useState(location.pathname)
  useEffect(() => {
    const on = () => setP(location.pathname)
    addEventListener('popstate', on)
    return () => removeEventListener('popstate', on)
  }, [])
  return p
}

export function Link({ to, children, className, ...rest }: { to: string; children: ReactNode; className?: string } &
  Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, 'href'>) {
  const onClick = (e: MouseEvent<HTMLAnchorElement>) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return
    e.preventDefault()
    navigate(to)
  }
  return <a href={to} onClick={onClick} className={className} {...rest}>{children}</a>
}
