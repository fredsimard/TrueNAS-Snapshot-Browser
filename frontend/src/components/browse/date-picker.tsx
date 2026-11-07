import { useMemo, useState } from 'react'
import { CalendarIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Calendar } from '@/components/ui/calendar'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { startOfDay } from '@/lib/format'
import { snapToDay, type TimePoint } from '@/lib/timeline'

export function DatePicker({
  points,
  selected,
  onPick,
}: {
  points: TimePoint[]
  selected: number
  onPick: (index: number, exact: boolean, day: Date) => void
}) {
  const [open, setOpen] = useState(false)
  const first = startOfDay(points[0].day)
  const last = startOfDay(points[points.length - 1].day)
  const current = points[selected].day

  // Versions per day (a midnight version closes the previous day; Now counts for today). Days
  // without one can't be picked; days with several are bold.
  const perDay = useMemo(() => {
    const m = new Map<number, number>()
    for (const p of points) {
      const k = startOfDay(p.day).getTime()
      m.set(k, (m.get(k) ?? 0) + 1)
    }
    return m
  }, [points])
  const versions = (d: Date) => perDay.get(startOfDay(d).getTime()) ?? 0

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 gap-1.5 px-2.5 text-[12.5px]">
          <CalendarIcon className="size-[15px]" aria-hidden />
          Pick a date
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-auto p-0">
        <Calendar
          mode="single"
          selected={current}
          defaultMonth={current}
          startMonth={first}
          endMonth={last}
          disabled={[{ before: first }, { after: last }, (d: Date) => versions(d) === 0]}
          modifiers={{ several: (d: Date) => versions(d) > 1 }}
          modifiersClassNames={{ several: '[&_button]:font-semibold' }}
          onSelect={(day) => {
            if (!day) return
            const snap = snapToDay(points, day)
            setOpen(false)
            onPick(snap.index, snap.exact, day)
          }}
        />
        <p className="px-3 pb-3 text-xs text-muted-foreground">You’ll see the last version of the day you pick.</p>
      </PopoverContent>
    </Popover>
  )
}
