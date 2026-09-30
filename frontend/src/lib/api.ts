// Client for the snapback API. Content is addressed by root id + relative path + opaque point id.

export const NOW = 'now'

export interface Info {
  server_label: string
  roots: { id: string; label: string }[]
}

export interface Point {
  id: string
  time: string // ISO 8601 with offset
}

export interface Values {
  dir: boolean
  size: number | null
  mtime: number // ms since epoch
}

export type Status = 'unchanged' | 'changed' | 'deleted'

export interface Entry extends Values {
  name: string
  status: Status | null
  now: Values | null
  last_seen: string | null
}

export interface Folder {
  state: 'ok' | 'missing' | 'denied'
  entries: Entry[]
  nearest: { at: string; direction: 'later' | 'earlier' } | null
}

export interface Version extends Values {
  current: boolean
  points: string[]
}

export interface History {
  now: Values | null
  versions: Version[] // newest first
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string) {
    super(code)
    this.status = status
    this.code = code
  }
}

async function get<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal, headers: { Accept: 'application/json' } })
  if (!res.ok) {
    let code = 'http_' + res.status
    try {
      code = (await res.json()).error ?? code
    } catch {
      // not JSON
    }
    throw new ApiError(res.status, code)
  }
  return res.json() as Promise<T>
}

const root = (id: string) => `/api/roots/${encodeURIComponent(id)}`

export const api = {
  info: (signal?: AbortSignal) => get<Info>('/api/info', signal),
  points: (rootId: string, signal?: AbortSignal) =>
    get<{ points: Point[] }>(`${root(rootId)}/points`, signal).then((r) => r.points),
  folder: (rootId: string, path: string, at: string, signal?: AbortSignal) =>
    get<Folder>(`${root(rootId)}/folder?${new URLSearchParams({ path, at })}`, signal),
  history: (rootId: string, path: string, signal?: AbortSignal) =>
    get<History>(`${root(rootId)}/history?${new URLSearchParams({ path })}`, signal),
}
