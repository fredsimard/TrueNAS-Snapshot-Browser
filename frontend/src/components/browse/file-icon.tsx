import { FileTextIcon, FilmIcon, FolderIcon, ImageIcon } from 'lucide-react'

import { cn } from '@/lib/utils'
import type { IconKind } from '@/lib/format'

export function FileIcon({ kind, className }: { kind: IconKind; className?: string }) {
  const cls = cn('size-4 shrink-0', className)
  switch (kind) {
    case 'folder':
      return <FolderIcon className={cn(cls, 'fill-border text-foreground/70')} strokeWidth={1.75} aria-hidden />
    case 'image':
      return <ImageIcon className={cn(cls, 'text-muted-foreground')} strokeWidth={1.75} aria-hidden />
    case 'video':
      return <FilmIcon className={cn(cls, 'text-muted-foreground')} strokeWidth={1.75} aria-hidden />
    default:
      return <FileTextIcon className={cn(cls, 'text-muted-foreground')} strokeWidth={1.75} aria-hidden />
  }
}
