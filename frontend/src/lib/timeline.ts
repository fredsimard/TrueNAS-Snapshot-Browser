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

/** Gaps longer than this, with no version in them, can collapse into a break... */
const BREAK_AFTER = 48 * H
/** ...if they're also this many times longer than the spacing of the versions around them. */
const BREAK_VS_CADENCE = 4
/** Width of a break, and the least room a gap must take before it's worth collapsing. */
const BREAK_PX = 28

/** A stretch of time with no versions, drawn as a short faded break instead of to scale. */
export interface TimeBreak {
  x0: number
  x1: number
  from: Date
  to: Date
}

export interface Scale {
  x: (t: Date) => number
  breaks: TimeBreak[]
}

/**
 * Square-root scale, so recent points get more room (DESIGN-HANDOFF.md, Timeline), minus the
 * holes: a gap of more than two days that is also much longer than the spacing around it (so not
 * just weekly versions being weekly), and that would take more than two breaks' width on screen,
 * becomes a fixed-width break, and its time is left out of the scale. That frees room for the rest,
 * which can make other holes wide enough to collapse, so it repeats until nothing changes.
 */
export function makeScale(points: TimePoint[], now: Date, left: number, right: number): Scale {
  const times = points.map((p) => p.time.getTime())
  times[times.length - 1] = Math.max(times[times.length - 1], now.getTime())
  const gaps = times.slice(1).map((t, i) => t - times[i])
  const holes = gaps.map((_, i) => isHole(gaps, i))
  const collapsed = new Set<number>()
  let scale = layout(times, collapsed, left, right)
  for (let round = 0; round < 8; round++) {
    let changed = false
    for (let i = 0; i < gaps.length; i++) {
      if (collapsed.has(i) || !holes[i]) continue
      if (scale.x(new Date(times[i + 1])) - scale.x(new Date(times[i])) > 2 * BREAK_PX) {
        collapsed.add(i)
        changed = true
      }
    }
    if (!changed) break
    scale = layout(times, collapsed, left, right)
  }
  return scale
}

/** A gap of more than two days, much longer than the median of the three gaps on each side. */
function isHole(gaps: number[], i: number): boolean {
  if (gaps[i] <= BREAK_AFTER) return false
  const near = [...gaps.slice(Math.max(0, i - 3), i), ...gaps.slice(i + 1, i + 4)].sort((a, b) => a - b)
  if (!near.length) return true
  const mid = near.length >> 1
  const median = near.length % 2 ? near[mid] : (near[mid - 1] + near[mid]) / 2
  return gaps[i] > BREAK_VS_CADENCE * median
}

/** times: oldest first, Now last. collapsed: indexes i of the gaps times[i] → times[i + 1]. */
function layout(times: number[], collapsed: Set<number>, left: number, right: number): Scale {
  const first = times[0]
  const last = times[times.length - 1]
  const gaps = [...collapsed].sort((a, b) => a - b).map((i) => ({ a: times[i], b: times[i + 1] }))
  const removed = gaps.reduce((sum, g) => sum + (g.b - g.a), 0)
  const maxAge = last - first - removed
  const span = right - left
  const empty = maxAge <= 0
  const breakPx = gaps.length ? (empty ? span / gaps.length : Math.min(BREAK_PX, span / 2 / gaps.length)) : 0
  const room = span - gaps.length * breakPx

  const at = (t: number): number => {
    const tc = Math.min(Math.max(t, first), last)
    let before = 0
    let removedAfter = 0
    for (const g of gaps) {
      if (g.b <= tc) before++
      else if (g.a >= tc) removedAfter += g.b - g.a
      else return at(g.a) + (breakPx * (tc - g.a)) / (g.b - g.a) // inside a break
    }
    const age = last - tc - removedAfter
    return left + before * breakPx + room * (empty ? 1 : 1 - Math.sqrt(Math.max(age, 0) / maxAge))
  }

  return {
    x: (t: Date) => at(t.getTime()),
    breaks: gaps.map((g) => ({ x0: at(g.a), x1: at(g.a) + breakPx, from: new Date(g.a), to: new Date(g.b) })),
  }
}

export interface DayMark {
  x: number
  text: string
}

/** Today, Yesterday, then Sundays going back, skipping any inside a break or that would crowd a mark already placed. */
export function dayMarks(
  points: TimePoint[],
  now: Date,
  { x, breaks }: Scale,
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
    if (breaks.some((b) => d > b.from && d < b.to)) continue
    const px = x(d)
    if (breaks.some((b) => b.x0 > px - 2 && b.x0 < px + 44)) continue // its label would run into a break
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
