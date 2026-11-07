import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'

import { DatePicker } from '@/components/browse/date-picker'
import { Timeline } from '@/components/browse/timeline'
import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { TimePoint } from '@/lib/timeline'

export type Filter = 'all' | 'changed' | 'deleted'

export function TimeBar({
  points,
  selected,
  now,
  onSelect,
  onPickDate,
  filter,
  onFilter,
  counts,
}: {
  points: TimePoint[]
  selected: number
  now: Date
  onSelect: (index: number) => void
  onPickDate: (index: number, exact: boolean, day: Date) => void
  filter: Filter
  onFilter: (f: Filter) => void
  counts: { all: number; changed: number; deleted: number } | null
}) {
  const cur = points[selected]
  const nowIndex = points.length - 1
  const atNow = selected === nowIndex

  return (
    <section aria-label="Point in time" className="flex shrink-0 flex-col gap-1.5 border-b bg-subtle px-6 pt-2.5 pb-2.5">
      <div className="flex h-[34px] items-center gap-5">
        <div className="flex min-w-0 items-baseline gap-2 whitespace-nowrap">
          <span className="text-muted-foreground">As of</span>
          <span className="text-[17px] font-semibold tracking-[-0.01em]">{cur.label}</span>
          <span className="truncate text-muted-foreground">{cur.long}</span>
        </div>
        {!atNow && counts && (
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            value={filter}
            onValueChange={(v) => v && onFilter(v as Filter)}
            aria-label="Show"
            className="bg-background"
          >
            <ToggleGroupItem value="all" className="h-[26px] gap-1.5 px-2.5 text-[12.5px]">
              All <Count n={counts.all} />
            </ToggleGroupItem>
            <ToggleGroupItem value="changed" className="h-[26px] gap-1.5 px-2.5 text-[12.5px]">
              <span className="size-[7px] rounded-full bg-status-changed" aria-hidden />
              Changed <Count n={counts.changed} />
            </ToggleGroupItem>
            <ToggleGroupItem value="deleted" className="h-[26px] gap-1.5 px-2.5 text-[12.5px]">
              <span className="size-[7px] rounded-full bg-status-deleted" aria-hidden />
              Deleted <Count n={counts.deleted} />
            </ToggleGroupItem>
          </ToggleGroup>
        )}
        <div className="grow" />
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex gap-1">
              <Button
                variant="outline"
                size="icon"
                className="size-7"
                aria-label="Earlier version"
                disabled={selected === 0}
                onClick={() => onSelect(selected - 1)}
              >
                <ChevronLeftIcon />
              </Button>
              <Button
                variant="outline"
                size="icon"
                className="size-7"
                aria-label="Later version"
                disabled={atNow}
                onClick={() => onSelect(selected + 1)}
              >
                <ChevronRightIcon />
              </Button>
            </div>
          </TooltipTrigger>
          <TooltipContent className="flex items-center gap-1.5">
            Step through time <Kbd>←</Kbd>
            <Kbd>→</Kbd>
          </TooltipContent>
        </Tooltip>
        {points.length > 1 && <DatePicker points={points} selected={selected} onPick={onPickDate} />}
        {!atNow && (
          <Button size="sm" className="h-7 px-3 text-[12.5px]" onClick={() => onSelect(nowIndex)}>
            Back to Now
          </Button>
        )}
      </div>
      <Timeline points={points} selected={selected} now={now} onSelect={onSelect} />
    </section>
  )
}

function Count({ n }: { n: number }) {
  return <span className="text-muted-foreground tabular-nums">{n}</span>
}
