import { useSyncExternalStore } from 'react'

export type Theme = 'system' | 'light' | 'dark'

// Also read by the inline script in index.html, which applies the theme before first paint.
const STORAGE_KEY = 'snapback-theme'

const media = window.matchMedia('(prefers-color-scheme: dark)')
const listeners = new Set<() => void>()

function stored(): Theme {
  try {
    const t = localStorage.getItem(STORAGE_KEY)
    return t === 'light' || t === 'dark' ? t : 'system'
  } catch {
    return 'system'
  }
}

let current = stored()

function apply() {
  const dark = current === 'dark' || (current === 'system' && media.matches)
  document.documentElement.classList.toggle('dark', dark)
}

media.addEventListener('change', apply)

export function setTheme(theme: Theme) {
  current = theme
  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    // Not persisted; still applies until the page reloads.
  }
  apply()
  listeners.forEach((fn) => fn())
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, () => current)
}
