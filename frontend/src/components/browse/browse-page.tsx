import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SortingState } from '@tanstack/react-table'
import { toast } from 'sonner'

import { AppHeader } from '@/components/app-header'
import { FileTable } from '@/components/browse/file-table'
import { FolderState } from '@/components/browse/folder-state'
import { HistoryPanel } from '@/components/browse/history-panel'
import { TimeBar, type Filter } from '@/components/browse/time-bar'
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from '@/components/ui/breadcrumb'
import { useNow } from '@/hooks/use-now'
import { useRequest } from '@/hooks/use-request'
import { api, ApiError, NOW, type Info } from '@/lib/api'
import { shortDay } from '@/lib/format'
import { navigate, routeHref, useLinkHandler } from '@/lib/router'
import { sortRows, toRows } from '@/lib/rows'
import { buildTimePoints } from '@/lib/timeline'

const POINTS_REFRESH_MS = 5 * 60_000

export function BrowsePage({
  info,
  rootId,
  parts,
  at,
}: {
  info: Info
  rootId: string
  parts: string[]
  at: string
}) {
  const root = info.roots.find((r) => r.id === rootId)!
  const now = useNow()
  const onLink = useLinkHandler()
  const rootRef = useRef<HTMLDivElement>(null)
  const path = parts.join('/')

  // Points in time. Refreshed periodically: snapshots are created and expire while people browse.
  const [pointsTick, setPointsTick] = useState(0)
  useEffect(() => {
    const id = window.setInterval(() => setPointsTick((t) => t + 1), POINTS_REFRESH_MS)
    return () => window.clearInterval(id)
  }, [])
  const pointsReq = useRequest(`${rootId}#${pointsTick}`, (s) => api.points(rootId, s))
  const points = useMemo(
    () => (pointsReq.data && pointsReq.dataKey?.startsWith(`${rootId}#`) ? buildTimePoints(pointsReq.data, now) : null),
    [pointsReq.data, pointsReq.dataKey, rootId, now],
  )
  const selected = points ? points.findIndex((p) => p.id === at) : -1
  const cur = points && selected >= 0 ? points[selected] : null
  const labelOf = useCallback(
    (id: string | null) => (id && points?.find((p) => p.id === id)?.label) || '',
    [points],
  )

  // The listing at the selected point.
  const folderKey = `${rootId}\n${path}\n${at}`
  const folderReq = useRequest(folderKey, (s) => api.folder(rootId, path, at, s))
  const samePath = folderReq.dataKey?.startsWith(`${rootId}\n${path}\n`) ?? false
  const folder = samePath ? folderReq.data : undefined
  const fresh = folderReq.dataKey === folderKey
  const unknownPoint = folderReq.error instanceof ApiError && folderReq.error.code === 'unknown_point'

  const goTo = useCallback(
    (id: string) => navigate(routeHref(rootId, parts, id), { replace: true }),
    [rootId, parts],
  )

  // A point that no longer exists (expired, or filtered out): fall back to Now.
  const pointGone = at !== NOW && (unknownPoint || (points !== null && selected < 0))
  const handledGone = useRef<string | null>(null)
  useEffect(() => {
    if (!pointGone || handledGone.current === at) return
    handledGone.current = at
    toast('That version is no longer available', { description: 'Showing the share as it is now.' })
    setPointsTick((t) => t + 1)
    goTo(NOW)
  }, [pointGone, at, goTo])

  const [sorting, setSorting] = useState<SortingState>([{ id: 'name', desc: false }])
  const [filter, setFilter] = useState<Filter>('all')
  const [selectedName, setSelectedName] = useState<{ path: string; name: string } | null>(null)
  const [openFile, setOpenFile] = useState<{ path: string; name: string } | null>(null)
  const selectedRow = selectedName?.path === path ? selectedName.name : null
  const openName = openFile?.path === path ? openFile.name : null

  const historyKey = openName ? `${rootId}\n${path ? `${path}/` : ''}${openName}` : null
  const historyReq = useRequest(historyKey, (s) => api.history(rootId, `${path ? `${path}/` : ''}${openName}`, s))

  const atNow = at === NOW
  const all = useMemo(() => toRows(folder?.state === 'ok' ? folder.entries : []), [folder])
  const counts = useMemo(
    () => ({
      all: all.length,
      changed: all.filter((r) => r.status === 'changed').length,
      deleted: all.filter((r) => r.status === 'deleted').length,
    }),
    [all],
  )
  const effFilter = atNow ? 'all' : filter
  const shown = useMemo(
    () => sortRows(effFilter === 'all' ? all : all.filter((r) => r.status === effFilter), sorting),
    [all, effFilter, sorting],
  )

  const enterFolder = useCallback(
    (name: string) => navigate(routeHref(rootId, [...parts, name], at)),
    [rootId, parts, at],
  )
  const selectRow = useCallback((name: string) => setSelectedName({ path, name }), [path])
  const showHistory = useCallback(
    (name: string) => {
      setSelectedName({ path, name })
      setOpenFile({ path, name })
    },
    [path],
  )

  const step = (delta: number) => {
    if (!points || selected < 0) return
    const i = Math.min(Math.max(selected + delta, 0), points.length - 1)
    if (i !== selected) goTo(points[i].id)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.defaultPrevented || e.altKey || e.metaKey || e.ctrlKey) return
    const t = e.target as HTMLElement
    if (t.closest('input, textarea, [data-slot=popover-content], [data-slot=context-menu-content]')) return
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      step(-1)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault()
      step(1)
    } else if (e.key === 'Escape') {
      setOpenFile(null)
    }
  }

  useEffect(() => {
    rootRef.current?.focus({ preventScroll: true })
  }, [])

  const folderName = parts.length ? parts[parts.length - 1] : root.label
  useEffect(() => {
    document.title = `${folderName} · ${info.server_label}`
  }, [folderName, info.server_label])

  // Main area.
  let body: React.ReactNode
  const label = cur?.label ?? ''
  if (folderReq.error && !unknownPoint && !folderReq.loading) {
    body = (
      <FolderState icon="error" title="This folder couldn’t be loaded" action="Try again" onAction={folderReq.reload}>
        Something went wrong on the server. Try again in a moment; if it keeps happening, ask IT.
      </FolderState>
    )
  } else if (!folder || !points || !cur) {
    body = <FileTable {...tableProps()} rows={[]} skeleton stale={false} />
  } else if (folder.state === 'denied') {
    body = (
      <FolderState icon="denied" title="This folder isn’t available to you">
        Your account can’t open it. If you need something from it, ask IT.
      </FolderState>
    )
  } else if (folder.state === 'missing') {
    const target = folder.nearest ? points.find((p) => p.id === folder.nearest!.at) : undefined
    const go = target ? () => goTo(target.id) : undefined
    if (target && folder.nearest!.direction === 'later') {
      body = (
        <FolderState icon="missing" title="This folder didn’t exist yet" action={`Go to ${target.label}`} onAction={go}>
          As of {label}, there was no {folderName} folder here. The earliest version that has it is {target.label}.
        </FolderState>
      )
    } else if (target) {
      body = (
        <FolderState icon="missing" title={atNow ? 'This folder isn’t here now' : 'This folder was gone by then'} action={`Go to ${target.label}`} onAction={go}>
          {atNow ? 'There’s' : `As of ${label}, there was`} no {folderName} folder here. The last version that has it is {target.label}.
        </FolderState>
      )
    } else {
      body = (
        <FolderState icon="missing" title="This folder isn’t in any version">
          There’s no {folderName} folder here in any version that’s available.
        </FolderState>
      )
    }
  } else if (all.length === 0 && fresh) {
    if (parts.length === 0) {
      body = (
        <FolderState icon="blank" title="Nothing here">
          {atNow ? `${root.label} has no files right now.` : `${root.label} had no files as of ${label}.`}
        </FolderState>
      )
    } else if (atNow) {
      body = (
        <FolderState icon="empty" title="This folder is empty">
          {folderName} has nothing in it right now.
        </FolderState>
      )
    } else {
      body = (
        <FolderState icon="empty" title="This folder was empty then" action="Step forward in time" onAction={() => step(1)}>
          As of {label}, {folderName} existed but had nothing in it. Try a later time.
        </FolderState>
      )
    }
  } else {
    body = (
      <>
        <FileTable {...tableProps()} rows={shown} skeleton={false} stale={!fresh} />
        {shown.length === 0 && (
          <p className="absolute inset-x-0 top-16 text-center text-muted-foreground">
            {effFilter === 'changed' ? 'Nothing here has changed since then.' : 'Nothing here has been deleted since then.'}
          </p>
        )}
      </>
    )
  }

  function tableProps() {
    return {
      sorting,
      onSortingChange: setSorting,
      now,
      lastSeenLabel: labelOf,
      selected: selectedRow ?? openName,
      onSelect: selectRow,
      onOpenFile: showHistory,
      onEnterFolder: enterFolder,
    }
  }

  let footer = ''
  if (folder?.state === 'ok' && fresh && cur && all.length > 0) {
    footer = `${shown.length} ${shown.length === 1 ? 'item' : 'items'}${effFilter !== 'all' ? ` of ${all.length}` : ''}${atNow ? '' : ` · as of ${label}`}`
  } else if (!fresh && cur && !folderReq.error) {
    footer = `Loading this folder as of ${label}…`
  }

  return (
    <div
      ref={rootRef}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      // Safari doesn't focus buttons on click; keep keyboard focus inside Browse so ← → keep working.
      onMouseUp={() => document.activeElement === document.body && rootRef.current?.focus({ preventScroll: true })}
      className="flex h-dvh flex-col outline-none"
    >
      <AppHeader serverLabel={info.server_label}>
        <Breadcrumb className="min-w-0">
          <BreadcrumbList className="flex-nowrap gap-1.5 text-[13px] sm:gap-1.5">
            {[root.label, ...parts].map((text, i, arr) => (
              <Fragment key={i}>
                {i > 0 && <BreadcrumbSeparator />}
                <BreadcrumbItem className="min-w-0">
                  {i === arr.length - 1 ? (
                    <BreadcrumbPage className="truncate font-medium">{text}</BreadcrumbPage>
                  ) : (
                    <BreadcrumbLink asChild>
                      <a className="truncate" href={routeHref(rootId, parts.slice(0, i), at)} onClick={onLink}>
                        {text}
                      </a>
                    </BreadcrumbLink>
                  )}
                </BreadcrumbItem>
              </Fragment>
            ))}
          </BreadcrumbList>
        </Breadcrumb>
        {info.roots.length > 1 && (
          <a href="/" onClick={onLink} className="ml-auto shrink-0 text-muted-foreground hover:text-foreground">
            All shares
          </a>
        )}
      </AppHeader>

      {points && cur ? (
        <TimeBar
          points={points}
          selected={selected}
          now={now}
          onSelect={(i) => goTo(points[i].id)}
          onPickDate={(i, exact, day) => {
            goTo(points[i].id)
            const dayText = shortDay(day)
            if (exact) {
              toast(`Showing ${points[i].label}`, {
                description: points[i].isNow ? 'That is the share as it is right now.' : `The last version from ${dayText}.`,
              })
            } else {
              toast(`No version from ${dayText}`, { description: `Showing ${points[i].label}, the closest one.` })
            }
          }}
          filter={effFilter}
          onFilter={setFilter}
          counts={folder?.state === 'ok' && fresh && all.length > 0 ? counts : null}
        />
      ) : (
        <div className="h-[106px] shrink-0 border-b bg-subtle" />
      )}

      <div className="flex min-h-0 grow">
        <div className="relative flex min-w-0 grow flex-col">
          {body}
          <footer className="flex h-9 shrink-0 items-center gap-3 border-t px-4 text-xs text-muted-foreground">
            <span>{footer}</span>
          </footer>
        </div>
        {openName && points && cur && (
          <HistoryPanel
            name={openName}
            history={historyReq.dataKey === historyKey ? historyReq.data : undefined}
            error={!!historyReq.error}
            points={points}
            selected={selected}
            now={now}
            onClose={() => setOpenFile(null)}
          />
        )}
      </div>
    </div>
  )
}
