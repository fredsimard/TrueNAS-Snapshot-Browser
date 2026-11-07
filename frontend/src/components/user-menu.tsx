import { ChevronDownIcon, LogOutIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { api, SIGNED_OUT_EVENT } from '@/lib/api'

export function UserMenu({ user }: { user: string }) {
  const name = user.slice(user.lastIndexOf('\\') + 1)

  const signOut = async () => {
    try {
      await api.logout()
    } finally {
      window.dispatchEvent(new Event(SIGNED_OUT_EVENT))
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" aria-label="Account menu" className="h-[30px] gap-2 pr-2 pl-1 text-[13px]">
          <span className="flex size-6 items-center justify-center rounded-full bg-muted text-[11px] font-semibold">
            {name.slice(0, 2).toUpperCase()}
          </span>
          {name}
          <ChevronDownIcon className="size-3.5 text-muted-foreground" aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={signOut}>
          <LogOutIcon /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
