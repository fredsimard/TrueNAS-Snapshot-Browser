import type { ReactNode } from 'react'
import { HistoryIcon } from 'lucide-react'

import { Separator } from '@/components/ui/separator'

export function AppHeader({ serverLabel, children }: { serverLabel: string; children?: ReactNode }) {
  return (
    <header className="flex h-11 shrink-0 items-center gap-3 border-b bg-background px-4">
      <div className="flex items-center gap-2">
        <span className="flex size-6 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <HistoryIcon className="size-3.5" aria-hidden />
        </span>
        <span className="font-semibold">{serverLabel}</span>
      </div>
      {children && (
        <>
          <Separator orientation="vertical" className="data-[orientation=vertical]:h-4" />
          {children}
        </>
      )}
    </header>
  )
}
