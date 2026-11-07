import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const base = 'h-[17px] shrink-0 rounded px-1.5 py-0 text-[11px] font-medium'

/** Listing column width: every row's pill is this wide, whatever it says. */
export const STATUS_PILL_WIDTH = 'w-[60px]'

export function ChangedBadge({ className }: { className?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn(base, 'border-status-changed/40 bg-status-changed-bg text-status-changed', className)}
    >
      Changed
    </Badge>
  )
}

export function DeletedBadge({ className }: { className?: string }) {
  return <Badge className={cn(base, 'border-transparent bg-status-deleted text-white', className)}>Deleted</Badge>
}

/** Holds the place of a badge on unchanged rows, so the column reads as one. */
export function EmptyBadge({ className }: { className?: string }) {
  return <span className={cn(base, 'inline-block rounded border border-border', className)} aria-hidden />
}

export function ChangeDot({ deleted = false }: { deleted?: boolean }) {
  return (
    <span
      className={cn('size-1.5 shrink-0 rounded-full', deleted ? 'bg-status-deleted' : 'bg-status-changed')}
      aria-hidden
    />
  )
}
