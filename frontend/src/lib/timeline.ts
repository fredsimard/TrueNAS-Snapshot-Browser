import type { Point } from '@/lib/api'
import { NOW } from '@/lib/api'
import { clock, dayName, daysAgo, longDay, sameDay, shortDate, startOfDay } from '@/lib/format'

export interface TimePoint {
  id: string
  time: Date
  /** The day the point belongs to: a midnight point closes the previous day. */
  day: Date
  midnight: boolean
  label: string
  long: string
  isNow: boolean
}

const H = 3_600_000

/** Snapshots oldest first, then Now. */
export function buildTimePoints(points: Point[], now: Date): TimePoint[] {
  const out: TimePoint[] = points.map((p) => {
    const time = new Date(p.time)
    const midnight = time.getHours() === 0 && time.getMinutes() === 0
    const day = midnight ? new Date(time.getTime() - 1) : time
    const label = midnight
      ? `${dayName(day, now)}, end of day`
      : `${dayName(time, now)}, ${clock(time)}`
    return { id: p.id, time, day, midnight, label, long: longDay(day), isNow: false }
  })
  out.push({
    id: NOW,
    time: now,
    day: now,
    midnight: false,
    label: 'Now',
    long: '',
    isNow: true,
  })
  return out
}

/** Square-root scale: recent points get more room (DESIGN-HANDOFF.md, Timeline). */
export function makeScale(points: TimePoint[], now: Date, left: number, right: number) {
  const maxAge = Math.max((now.getTime() - points[0].time.getTime()) / H, 1e-9)
  return (t: Date) => {
    const age = Math.min(Math.max((now.getTime() - t.getTime()) / H, 0), maxAge)
    return left + (right - left) * (1 - Math.sqrt(age / maxAge))
  }
}

export interface DayMark {
  x: number
  text: string
}

/** Today, Yesterday, then Sundays going back, skipping any that would crowd a mark already placed. */
export function dayMarks(
  points: TimePoint[],
  now: Date,
  x: (t: Date) => number,
  left: number,
  right: number,
  minGap = 56,
): DayMark[] {
  if (points.length < 2) return []
  const oldest = points[0].time
  const today = startOfDay(now)
  const candidates: Date[] = [today, new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1)]
  const sunday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - today.getDay())
  for (let d = sunday; d >= oldest; d = new Date(d.getFullYear(), d.getMonth(), d.getDate() - 7)) {
    if (!candidates.some((c) => sameDay(c, d))) candidates.push(d)
  }
  const marks: DayMark[] = []
  for (const d of candidates) {
    if (d < oldest) continue
    const px = x(d)
    if (px < left || px > right - 40) continue
    if (marks.some((m) => Math.abs(m.x - px) < minGap)) continue
    const a = daysAgo(d, now)
    marks.push({ x: px, text: a === 0 ? 'Today' : a === 1 ? 'Yesterday' : shortDate(d) })
  }
  return marks
}

/**
 * Date picker snapping: the last point whose day is `day`, else the point nearest the end of that day.
 */
export function snapToDay(points: TimePoint[], day: Date): { index: number; exact: boolean } {
  let index = -1
  points.forEach((p, i) => {
    if (sameDay(p.day, day)) index = i
  })
  if (index >= 0) return { index, exact: true }
  const end = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1).getTime()
  let best = 0
  let bestDist = Infinity
  points.forEach((p, i) => {
    const dist = Math.abs(p.time.getTime() - end)
    if (dist < bestDist) {
      bestDist = dist
      best = i
    }
  })
  return { index: best, exact: false }
}
