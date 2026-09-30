import type { SortingState } from '@tanstack/react-table'

import type { Entry } from '@/lib/api'
import { iconKind, kindOf, type IconKind } from '@/lib/format'

export interface FileRow extends Entry {
  kind: string
  icon: IconKind
}

export function toRows(entries: Entry[]): FileRow[] {
  return entries.map((e) => {
    const kind = kindOf(e.name, e.dir)
    return { ...e, kind, icon: iconKind(kind) }
  })
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** Folders first in either direction, as in Finder; ties by name. */
export function sortRows(rows: FileRow[], sorting: SortingState): FileRow[] {
  const { id = 'name', desc = false } = sorting[0] ?? {}
  const dir = desc ? -1 : 1
  return [...rows].sort((a, b) => {
    if (a.dir !== b.dir) return a.dir ? -1 : 1
    let c = 0
    if (id === 'mtime') c = a.mtime - b.mtime
    else if (id === 'size') c = (a.size ?? 0) - (b.size ?? 0)
    else if (id === 'kind') c = collator.compare(a.kind, b.kind)
    if (!c) c = collator.compare(a.name, b.name)
    return c * dir
  })
}
