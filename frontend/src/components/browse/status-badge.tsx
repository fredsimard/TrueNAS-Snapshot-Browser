import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const base = 'h-[17px] shrink-0 rounded px-1.5 py-0 text-[11px] font-medium'

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

export function ChangeDot() {
  return <span className="size-1.5 shrink-0 rounded-full bg-status-changed" aria-hidden />
}
