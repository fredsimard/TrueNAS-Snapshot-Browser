import type { ReactNode } from 'react'
import { HistoryIcon } from 'lucide-react'

import { ThemeToggle } from '@/components/theme-toggle'
import { UserMenu } from '@/components/user-menu'
import { Separator } from '@/components/ui/separator'

export function AppHeader({
  serverLabel,
  user,
  actions,
  children,
}: {
  serverLabel: string
  user: string | null
  actions?: ReactNode
  children?: ReactNode
}) {
  return (
    <header className="flex h-11 shrink-0 items-center gap-3 border-b bg-background px-4">
      <div className="flex shrink-0 items-center gap-2">
        <span className="flex size-6 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <HistoryIcon className="size-3.5" aria-hidden />
        </span>
        <span className="font-semibold whitespace-nowrap">{serverLabel}</span>
      </div>
      {children && (
        <>
          <Separator orientation="vertical" className="data-[orientation=vertical]:h-4" />
          {children}
        </>
      )}
      <div className="grow" />
      {actions}
      <ThemeToggle />
      {user && <UserMenu user={user} />}
    </header>
  )
}
