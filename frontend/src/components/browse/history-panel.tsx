import { XIcon } from 'lucide-react'

import { FileIcon } from '@/components/browse/file-icon'
import { ChangedBadge, DeletedBadge } from '@/components/browse/status-badge'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { History } from '@/lib/api'
import { formatSize, iconKind, kindOf, modifiedLabel } from '@/lib/format'
import type { TimePoint } from '@/lib/timeline'
import { cn } from '@/lib/utils'

/** A file's versions, docked on the right; pushes the listing narrower instead of covering it. */
export function HistoryPanel({
  name,
  history,
  error,
  points,
  selected,
  now,
  onClose,
}: {
  name: string
  history: History | undefined
  error: boolean
  points: TimePoint[]
  selected: number
  now: Date
  onClose: () => void
}) {
  const kind = kindOf(name, false)
  const cur = points[selected]
  const atNow = cur.isNow
  const labelOf = (id: string) => points.find((p) => p.id === id)?.label ?? ''

  const earlier = history?.versions.filter((v) => !v.current) ?? []
  const viewingVersion = history?.versions.find((v) => v.points.includes(cur.id))
  const deleted = history !== undefined && history.now === null
  const lastSeen = deleted && history.versions.length ? labelOf(history.versions[0].points.at(-1)!) : ''
  const changed = !deleted && !atNow && viewingVersion !== undefined && !viewingVersion.current

  return (
    <aside aria-label="File history" className="flex w-96 shrink-0 flex-col border-l bg-subtle">
      <div className="flex items-start gap-3 border-b py-4 pr-3 pl-4">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-foreground/70">
          <FileIcon kind={iconKind(kind)} className="size-[18px] text-inherit" />
        </div>
        <div className="flex min-w-0 grow flex-col gap-0.5 pt-px">
          <div className="text-sm font-semibold [overflow-wrap:anywhere]">{name}</div>
          <div className="text-[12.5px] text-muted-foreground">{kind}</div>
        </div>
        <Button variant="ghost" size="icon" className="size-7 text-muted-foreground" aria-label="Close" onClick={onClose}>
          <XIcon />
        </Button>
      </div>

      {(deleted || changed) && (
        <div className="flex items-center gap-2 px-4 pt-3 text-[12.5px]">
          {deleted ? <DeletedBadge /> : <ChangedBadge />}
          <span className="text-foreground/80">
            {deleted ? `Not in this folder now. Last seen as of ${lastSeen}.` : `Different now than as of ${cur.label}.`}
          </span>
        </div>
      )}

      {error ? (
        <p className="px-4 pt-4 text-muted-foreground">This file’s history couldn’t be loaded.</p>
      ) : !history ? (
        <div className="flex flex-col gap-2 p-4">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-[74px] w-full rounded-lg" />
          <Skeleton className="h-[74px] w-full rounded-lg" />
        </div>
      ) : (
        <>
          <div className="px-4 pt-4 pb-2 text-xs font-medium text-muted-foreground">
            {earlier.length === 1 ? '1 earlier copy' : `${earlier.length} earlier copies`}
          </div>
          <div className="flex min-h-0 grow flex-col gap-2 overflow-auto px-4 pb-4">
            {history.now && !history.versions.some((v) => v.current) && (
              <div className="flex flex-col gap-[3px] rounded-lg border bg-background px-3 py-2.5">
                <span className="font-medium">Now, in your folder</span>
                <div className="text-[12.5px] text-foreground/80">
                  {formatSize(history.now.size)} · Modified {modifiedLabel(history.now.mtime, now)}
                </div>
              </div>
            )}
            {history.versions.map((v) => {
              const viewing = !atNow && v === viewingVersion
              const first = v.points[0]
              const last = v.points[v.points.length - 1]
              const range = v.points.length === 1 ? `As of ${labelOf(first)}` : `As of ${labelOf(first)} → ${labelOf(last)}`
              return (
                <div
                  key={v.points[0]}
                  className={cn(
                    'flex flex-col gap-[3px] rounded-lg border px-3 py-2.5',
                    viewing ? 'border-timeline-selected bg-timeline-selected/5' : 'bg-background',
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{v.current ? 'Now, in your folder' : modifiedLabel(v.mtime, now)}</span>
                    {viewing && (
                      <Badge className="h-[17px] rounded border-transparent bg-timeline-selected/15 px-1.5 py-0 text-[11px] text-timeline-selected">
                        Viewing
                      </Badge>
                    )}
                  </div>
                  <div className="text-[12.5px] text-foreground/80">
                    {v.current ? `${formatSize(v.size)} · Modified ${modifiedLabel(v.mtime, now)}` : formatSize(v.size)}
                  </div>
                  {!v.current && <div className="text-xs text-muted-foreground">{range}</div>}
                </div>
              )
            })}
          </div>
        </>
      )}
    </aside>
  )
}
