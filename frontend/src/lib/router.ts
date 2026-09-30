import { useCallback, useMemo, useSyncExternalStore } from 'react'

import { NOW } from '@/lib/api'

// URLs: /  (share list)   /r/<root>/<folder>/<folder>?at=<point id>

export interface Route {
  rootId: string | null
  parts: string[]
  at: string
}

export function parseRoute(pathname: string, search: string): Route {
  const at = new URLSearchParams(search).get('at') || NOW
  const segs = pathname.split('/').filter(Boolean)
  if (segs[0] !== 'r' || segs.length < 2) return { rootId: null, parts: [], at }
  const decode = (s: string) => {
    try {
      return decodeURIComponent(s)
    } catch {
      return s
    }
  }
  return { rootId: decode(segs[1]), parts: segs.slice(2).map(decode), at }
}

export function routeHref(rootId: string, parts: string[], at: string = NOW): string {
  const path = ['r', rootId, ...parts].map(encodeURIComponent).join('/')
  return `/${path}${parts.length ? '' : '/'}${at === NOW ? '' : `?at=${encodeURIComponent(at)}`}`
}

const listeners = new Set<() => void>()

function subscribe(fn: () => void) {
  listeners.add(fn)
  window.addEventListener('popstate', fn)
  return () => {
    listeners.delete(fn)
    window.removeEventListener('popstate', fn)
  }
}

const snapshot = () => window.location.pathname + window.location.search

export function navigate(href: string, { replace = false } = {}) {
  if (href === snapshot()) return
  if (replace) window.history.replaceState(null, '', href)
  else window.history.pushState(null, '', href)
  listeners.forEach((fn) => fn())
}

export function useRoute(): Route {
  const loc = useSyncExternalStore(subscribe, snapshot)
  return useMemo(() => {
    const q = loc.indexOf('?')
    return parseRoute(q < 0 ? loc : loc.slice(0, q), q < 0 ? '' : loc.slice(q))
  }, [loc])
}

/** onClick for <a href> that navigates in-app on a plain left click. */
export function useLinkHandler() {
  return useCallback((e: React.MouseEvent<HTMLAnchorElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    navigate(e.currentTarget.getAttribute('href')!)
  }, [])
}
