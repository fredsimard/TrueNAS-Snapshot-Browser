import type { ReactNode } from 'react'
import { FolderOpenIcon, HistoryIcon, InboxIcon, LockIcon, TriangleAlertIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'

/** One Empty per folder state (DESIGN-HANDOFF.md, Folder state copy). */
export function FolderState({
  icon,
  title,
  children,
  action,
  onAction,
}: {
  icon: 'missing' | 'empty' | 'denied' | 'blank' | 'error'
  title: string
  children: ReactNode
  action?: string
  onAction?: () => void
}) {
  const Icon = { missing: HistoryIcon, empty: FolderOpenIcon, denied: LockIcon, blank: InboxIcon, error: TriangleAlertIcon }[icon]
  return (
    <div className="flex grow items-center justify-center px-6 pt-6 pb-20">
      <Empty className="max-w-100 gap-2.5 p-0">
        <EmptyHeader className="gap-2.5">
          <EmptyMedia variant="icon" className="mb-1 size-11 rounded-[10px]">
            <Icon className="size-5" strokeWidth={1.75} />
          </EmptyMedia>
          <EmptyTitle className="text-[15px] font-semibold">{title}</EmptyTitle>
          <EmptyDescription className="text-[13px] text-pretty">{children}</EmptyDescription>
        </EmptyHeader>
        {action && onAction && (
          <EmptyContent>
            <Button variant="outline" size="sm" className="mt-1.5" onClick={onAction}>
              {action}
            </Button>
          </EmptyContent>
        )}
      </Empty>
    </div>
  )
}
