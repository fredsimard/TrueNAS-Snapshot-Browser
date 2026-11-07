import { ChevronRightIcon, HardDriveIcon } from 'lucide-react'

import { AppHeader } from '@/components/app-header'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Item, ItemActions, ItemContent, ItemGroup, ItemMedia, ItemSeparator, ItemTitle } from '@/components/ui/item'
import type { Info } from '@/lib/api'
import { routeHref, useLinkHandler } from '@/lib/router'

export function SharePicker({ info }: { info: Info }) {
  const onLink = useLinkHandler()
  return (
    <div className="flex h-dvh flex-col bg-subtle">
      <AppHeader serverLabel={info.server_label} user={info.user} />
      <main className="flex grow justify-center px-4 pt-[min(160px,15vh)]">
        <div className="flex w-full max-w-100 flex-col gap-4">
          <div className="flex flex-col gap-1">
            <h1 className="text-xl font-semibold tracking-tight">Choose a share</h1>
            <p className="text-muted-foreground">The same shares you see in the Finder sidebar.</p>
          </div>
          {info.roots.length === 0 ? (
            <Empty className="border bg-background">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <HardDriveIcon />
                </EmptyMedia>
                <EmptyTitle>No shares here</EmptyTitle>
                <EmptyDescription>There are no shares to browse on this server. Ask IT.</EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <nav aria-label="Shares" className="overflow-hidden rounded-[10px] border bg-background">
              <ItemGroup>
                {info.roots.map((r, i) => (
                  <div key={r.id}>
                    {i > 0 && <ItemSeparator />}
                    <Item asChild size="sm" className="h-12 rounded-none py-0">
                      <a href={routeHref(r.id, [])} onClick={onLink}>
                        <ItemMedia className="text-muted-foreground">
                          <HardDriveIcon className="size-4.5" strokeWidth={1.75} aria-hidden />
                        </ItemMedia>
                        <ItemContent>
                          <ItemTitle className="text-sm">{r.label}</ItemTitle>
                        </ItemContent>
                        <ItemActions>
                          <ChevronRightIcon className="size-4 text-muted-foreground/60" aria-hidden />
                        </ItemActions>
                      </a>
                    </Item>
                  </div>
                ))}
              </ItemGroup>
            </nav>
          )}
        </div>
      </main>
    </div>
  )
}
