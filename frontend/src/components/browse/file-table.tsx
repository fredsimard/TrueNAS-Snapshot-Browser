import { useMemo, useState } from 'react'
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type OnChangeFn,
  type SortingState,
} from '@tanstack/react-table'
import { ChevronDownIcon, ChevronUpIcon, FolderOpenIcon, HistoryIcon } from 'lucide-react'

import { FileIcon } from '@/components/browse/file-icon'
import {
  ChangeDot,
  ChangedBadge,
  DeletedBadge,
  EmptyBadge,
  STATUS_PILL_WIDTH,
} from '@/components/browse/status-badge'
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from '@/components/ui/context-menu'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/components/ui/hover-card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatSize, modifiedLabel } from '@/lib/format'
import type { FileRow } from '@/lib/rows'
import { cn } from '@/lib/utils'

const WIDTHS: Record<string, string> = {
  status: 'w-[84px]',
  name: '',
  mark: 'w-5',
  mtime: 'w-[208px]',
  size: 'w-24',
  kind: 'w-[208px]',
}

export function FileTable({
  rows,
  sorting,
  onSortingChange,
  now,
  lastSeenLabel,
  selected,
  onSelect,
  onOpenFile,
  onEnterFolder,
  skeleton,
  stale,
}: {
  rows: FileRow[]
  sorting: SortingState
  onSortingChange: OnChangeFn<SortingState>
  now: Date
  lastSeenLabel: (pointId: string | null) => string
  selected: string | null
  onSelect: (name: string) => void
  onOpenFile: (name: string) => void
  onEnterFolder: (name: string) => void
  skeleton: boolean
  stale: boolean
}) {
  const [menuRow, setMenuRow] = useState<FileRow | null>(null)

  const columns = useMemo<ColumnDef<FileRow>[]>(
    () => [
      {
        id: 'status',
        header: '',
        enableSorting: false,
        cell: ({ row }) => (
          <div className="flex items-center pr-2 pl-4">
            {row.original.status === 'changed' ? (
              <ChangedBadge className={STATUS_PILL_WIDTH} />
            ) : row.original.status === 'deleted' ? (
              <DeletedBadge className={STATUS_PILL_WIDTH} />
            ) : (
              <EmptyBadge className={STATUS_PILL_WIDTH} />
            )}
          </div>
        ),
      },
      {
        id: 'name',
        header: 'Name',
        sortDescFirst: false,
        cell: ({ row }) => {
          const r = row.original
          const half = row.index > rows.length / 2
          const open = () => (r.dir ? onEnterFolder(r.name) : onOpenFile(r.name))
          const button = (
            <button
              type="button"
              className="flex h-[22px] w-full min-w-0 cursor-default items-center gap-2 px-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
              onClick={() => (r.dir ? onSelect(r.name) : onOpenFile(r.name))}
              onDoubleClick={() => r.dir && onEnterFolder(r.name)}
              onKeyDown={(e) => e.key === 'Enter' && open()}
            >
              <FileIcon kind={r.icon} />
              <span className="min-w-0 truncate">{r.name}</span>
            </button>
          )
          if (r.status !== 'changed' && r.status !== 'deleted') return button
          return (
            <HoverCard openDelay={350} closeDelay={80}>
              <HoverCardTrigger asChild>{button}</HoverCardTrigger>
              <HoverCardContent side={half ? 'top' : 'bottom'} align="start" className="w-80 p-3 text-[12.5px]">
                {r.status === 'changed' && r.now ? (
                  <div className="grid grid-cols-[64px_1fr_1fr] gap-x-3 gap-y-1.5">
                    <span />
                    <span className="font-semibold">Then</span>
                    <span className="font-semibold">Now</span>
                    <span className="text-muted-foreground">Modified</span>
                    <span>{modifiedLabel(r.mtime, now)}</span>
                    <span>{modifiedLabel(r.now.mtime, now)}</span>
                    <span className="text-muted-foreground">Size</span>
                    <span>{formatSize(r.size)}</span>
                    <span>{formatSize(r.now.size)}</span>
                  </div>
                ) : (
                  <div className="flex flex-col gap-1">
                    <div className="font-semibold">Not in this folder now</div>
                    <div className="text-muted-foreground">Last seen as of {lastSeenLabel(r.last_seen)}.</div>
                  </div>
                )}
              </HoverCardContent>
            </HoverCard>
          )
        },
      },
      {
        // The dot marking a date that differs now, or a deleted item; its own column keeps dates aligned.
        id: 'mark',
        header: '',
        enableSorting: false,
        cell: ({ row }) => {
          const r = row.original
          const changed = r.status === 'changed' && r.now !== null && r.now.mtime !== r.mtime
          return (
            <div className="flex justify-end">
              {changed && <ChangeDot />}
              {r.status === 'deleted' && <ChangeDot deleted />}
            </div>
          )
        },
      },
      {
        id: 'mtime',
        header: 'Date Modified',
        sortDescFirst: false,
        cell: ({ row }) => <div className="truncate px-2">{modifiedLabel(row.original.mtime, now)}</div>,
      },
      {
        id: 'size',
        header: 'Size',
        sortDescFirst: false,
        cell: ({ row }) => {
          const r = row.original
          const diff = r.status === 'changed' && !r.dir && r.now !== null && r.now.size !== r.size
          return (
            <div className="flex items-center justify-end gap-1.5 px-2 tabular-nums">
              {diff && <ChangeDot />}
              <span>{formatSize(r.size)}</span>
            </div>
          )
        },
      },
      {
        id: 'kind',
        header: 'Kind',
        sortDescFirst: false,
        cell: ({ row }) => <div className="truncate pr-2 pl-4">{row.original.kind}</div>,
      },
    ],
    [rows.length, now, lastSeenLabel, onSelect, onOpenFile, onEnterFolder],
  )

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange,
    manualSorting: true,
    enableSortingRemoval: false,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <ContextMenu onOpenChange={(open) => !open && setMenuRow(null)}>
      <ContextMenuTrigger
        asChild
        onContextMenu={(e) => {
          const tr = (e.target as HTMLElement).closest<HTMLElement>('tr[data-name]')
          const row = tr ? rows.find((r) => r.name === tr.dataset.name) : undefined
          if (!row) {
            e.preventDefault()
            return
          }
          setMenuRow(row)
          onSelect(row.name)
        }}
      >
        <div className="min-h-0 grow">
          <Table containerClassName="h-full overflow-auto" className="table-fixed text-[13px]">
            <TableHeader className="sticky top-0 z-10 bg-background">
              {table.getHeaderGroups().map((hg) => (
                <TableRow key={hg.id} className="hover:bg-transparent">
                  {hg.headers.map((h) => {
                    const sorted = h.column.getIsSorted()
                    return (
                      <TableHead
                        key={h.id}
                        aria-sort={sorted ? (sorted === 'asc' ? 'ascending' : 'descending') : 'none'}
                        className={cn('h-7 px-2 text-xs', WIDTHS[h.id], h.id === 'kind' && 'pl-4')}
                      >
                        {h.column.columnDef.enableSorting !== false && (
                          <button
                            type="button"
                            onClick={h.column.getToggleSortingHandler()}
                            className={cn(
                              'flex w-full items-center gap-1 font-medium',
                              h.id === 'size' && 'justify-end',
                              sorted ? 'text-foreground' : 'text-muted-foreground',
                            )}
                          >
                            {flexRender(h.column.columnDef.header, h.getContext())}
                            {sorted === 'asc' && <ChevronUpIcon className="size-3" strokeWidth={2.25} />}
                            {sorted === 'desc' && <ChevronDownIcon className="size-3" strokeWidth={2.25} />}
                          </button>
                        )}
                      </TableHead>
                    )
                  })}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody className={cn('transition-opacity', stale && 'opacity-60')} aria-busy={skeleton || stale}>
              {skeleton
                ? SKELETON.map(([w, k], i) => (
                    <TableRow key={i} className="h-[22px] border-0 hover:bg-transparent">
                      <TableCell className="py-0 pl-4">
                        <EmptyBadge className={cn(STATUS_PILL_WIDTH, 'align-middle')} />
                      </TableCell>
                      <TableCell className="py-0">
                        <div className="flex items-center gap-2">
                          <Skeleton className="h-3.5 w-4" />
                          <Skeleton className="h-2.5" style={{ width: w }} />
                        </div>
                      </TableCell>
                      <TableCell className="py-0" />
                      <TableCell className="py-0">
                        <Skeleton className="h-2.5 w-[150px]" />
                      </TableCell>
                      <TableCell className="py-0">
                        <Skeleton className="ml-auto h-2.5 w-12" />
                      </TableCell>
                      <TableCell className="py-0 pl-4">
                        <Skeleton className="h-2.5" style={{ width: k }} />
                      </TableCell>
                    </TableRow>
                  ))
                : table.getRowModel().rows.map((row) => {
                    const r = row.original
                    return (
                      <TableRow
                        key={r.name}
                        data-name={r.name}
                        data-state={selected === r.name ? 'selected' : undefined}
                        className={cn(
                          'h-[22px] border-0',
                          r.status === 'deleted' && 'bg-status-deleted-bg text-status-deleted',
                          r.status === 'changed' && 'text-status-changed',
                          'hover:bg-muted data-[state=selected]:bg-foreground/10',
                        )}
                      >
                        {row.getVisibleCells().map((cell) => (
                          <TableCell key={cell.id} className="h-[22px] p-0">
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </TableCell>
                        ))}
                      </TableRow>
                    )
                  })}
            </TableBody>
          </Table>
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent className="min-w-52">
        {menuRow?.dir ? (
          <ContextMenuItem onSelect={() => onEnterFolder(menuRow.name)}>
            <FolderOpenIcon /> Open
          </ContextMenuItem>
        ) : (
          <ContextMenuItem onSelect={() => menuRow && onOpenFile(menuRow.name)}>
            <HistoryIcon /> Show all versions
          </ContextMenuItem>
        )}
      </ContextMenuContent>
    </ContextMenu>
  )
}

const SKELETON: [number, number][] = [
  [180, 60], [120, 110], [210, 90], [150, 130], [96, 80], [240, 100], [170, 120],
  [130, 70], [200, 110], [110, 90], [160, 60], [190, 130], [140, 100], [220, 80],
]
