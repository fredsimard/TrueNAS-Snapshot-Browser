import { useLayoutEffect, useMemo, useRef, useState } from 'react'

import { cn } from '@/lib/utils'
import { dayMarks, makeScale, type TimePoint } from '@/lib/timeline'

const LEFT = 12
const RIGHT_INSET = 28 // Now sits this far from the right edge

/**
 * Horizontal timeline of available points (DESIGN-HANDOFF.md, Timeline). Irregular spacing is the
 * point: recent history is finer grained, so it gets more room.
 */
export function Timeline({
  points,
  selected,
  now,
  onSelect,
}: {
  points: TimePoint[]
  selected: number
  now: Date
  onSelect: (index: number) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width))
    ro.observe(el)
    setWidth(el.getBoundingClientRect().width)
    return () => ro.disconnect()
  }, [])

  const right = Math.max(width - RIGHT_INSET, LEFT + 1)
  const nowIndex = points.length - 1
  const atNow = selected === nowIndex

  const { xs, marks } = useMemo(() => {
    const x = makeScale(points, now, LEFT, right)
    return { xs: points.map((p) => x(p.time)), marks: dayMarks(points, now, x, LEFT, right) }
  }, [points, now, right])

  const selX = atNow ? right : xs[selected]

  return (
    <div ref={ref} className="relative h-[50px] select-none">
      {width > 0 && (
        <>
          {marks.map((m) => (
            <div key={m.text}>
              <span className="absolute top-0.5 h-7 w-px bg-border" style={{ left: m.x }} />
              <span
                className="absolute top-[34px] text-[11.5px] whitespace-nowrap text-muted-foreground"
                style={{ left: m.x + 4 }}
              >
                {m.text}
              </span>
            </div>
          ))}
          <span className="absolute inset-x-0 top-[29px] h-px bg-foreground/15" />
          {!atNow && (
            <span
              className="absolute top-1.5 h-6 rounded-[3px] bg-timeline-selected/7 dark:bg-timeline-selected/14"
              style={{ left: selX, width: Math.max(0, right - selX) }}
            />
          )}
          {points.slice(0, nowIndex).map((p, i) => (
            <button
              key={p.id}
              type="button"
              tabIndex={-1}
              aria-label={p.label}
              title={p.label}
              onClick={() => onSelect(i)}
              className="group absolute top-1 flex h-[26px] w-[7px] cursor-pointer items-end justify-center p-0"
              style={{ left: xs[i] - 3.5 }}
            >
              <span
                className={cn(
                  'block w-px bg-muted-foreground/70 group-hover:bg-foreground',
                  p.midnight ? 'h-3' : 'h-2',
                )}
              />
            </button>
          ))}
          {!atNow && (
            <>
              <span
                className="pointer-events-none absolute top-2 h-[22px] w-0.5 rounded-[1px] bg-timeline-selected"
                style={{ left: selX - 1 }}
              />
              <span
                className="pointer-events-none absolute top-px size-2.5 rounded-full bg-timeline-selected ring-2 ring-subtle"
                style={{ left: selX - 5 }}
              />
            </>
          )}
          <button
            type="button"
            tabIndex={-1}
            aria-label="Now"
            onClick={() => onSelect(nowIndex)}
            className={cn(
              'absolute top-[23px] size-3 cursor-pointer rounded-full border-2 border-subtle p-0',
              atNow ? 'bg-timeline-selected ring-1 ring-timeline-selected' : 'bg-foreground ring-1 ring-foreground',
            )}
            style={{ left: right - 6 }}
          />
          <span className="absolute top-[34px] text-[11.5px] font-semibold" style={{ left: right - 12 }}>
            Now
          </span>
        </>
      )}
    </div>
  )
}
