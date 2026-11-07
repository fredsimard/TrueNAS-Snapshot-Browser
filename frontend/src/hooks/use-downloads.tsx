import { useCallback, useRef, useState } from 'react'
import { Loader2Icon } from 'lucide-react'
import { toast } from 'sonner'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Progress } from '@/components/ui/progress'
import { api, ApiError, type DownloadPlan } from '@/lib/api'
import { formatSize, itemCount } from '@/lib/format'
import { cn } from '@/lib/utils'

/** Items in one folder, as of one point in time. */
export interface DownloadRequest {
  rootId: string
  path: string
  at: string
  names: string[]
}

type Ready = Extract<DownloadPlan, { too_large: false }>

type Dialog =
  // plan is null while the server measures it; slow once that has taken a while.
  | { kind: 'confirm'; seq: number; names: string[]; plan: Ready | null; slow: boolean }
  | { kind: 'too_large'; names: string[]; limit: number }
  | null

const POLL_MS = 1000
const SLOW_PLAN_MS = 1000
const SHOW_PROGRESS_AFTER_MS = 1200
// The server marks a download running as soon as the browser asks for it, so one still waiting this
// long was never requested.
const START_TIMEOUT_MS = 30_000

/**
 * Downloads: a file starts at once, a folder or several items after a confirm dialog stating the
 * size, and anything over the server's limit is refused before it starts. The browser fetches the
 * download itself, in a hidden frame, so a large zip never sits in this page's memory; the page
 * follows its progress from the server and can cancel it.
 */
export function useDownloads() {
  const [dialog, setDialog] = useState<Dialog>(null)
  // Each request gets a number; closing the dialog or starting another makes older answers stale.
  const seqRef = useRef(0)

  /** file: the caller knows it's one file, which starts without a dialog. */
  const request = useCallback(async (req: DownloadRequest, { file = false }: { file?: boolean } = {}) => {
    const seq = ++seqRef.current
    const mine = (d: Dialog) => d?.kind === 'confirm' && d.seq === seq
    if (!file) {
      // Open at once: measuring a large folder takes a while, and the click needs an answer.
      setDialog({ kind: 'confirm', seq, names: req.names, plan: null, slow: false })
      window.setTimeout(
        () => setDialog((d) => (d?.kind === 'confirm' && d.seq === seq && !d.plan ? { ...d, slow: true } : d)),
        SLOW_PLAN_MS,
      )
    }
    let plan: DownloadPlan
    try {
      plan = await api.prepareDownload(req.rootId, req.path, req.at, req.names)
    } catch (e) {
      if (seq !== seqRef.current) return
      setDialog((d) => (mine(d) ? null : d))
      showError(e)
      return
    }
    if (seq !== seqRef.current) return
    if (plan.too_large) setDialog({ kind: 'too_large', names: req.names, limit: plan.limit })
    else if (plan.kind === 'file') {
      setDialog((d) => (mine(d) ? null : d))
      start(plan)
    } else setDialog({ kind: 'confirm', seq, names: req.names, plan, slow: false })
  }, [])

  const close = (open: boolean) => {
    if (open) return
    seqRef.current++
    setDialog(null)
  }
  const what = (names: string[]) => (names.length === 1 ? `“${names[0]}”` : `${names.length.toLocaleString('en-US')} items`)

  const dialogs = (
    <>
      <AlertDialog open={dialog?.kind === 'confirm'} onOpenChange={close}>
        {dialog?.kind === 'confirm' && (
          <AlertDialogContent className="sm:max-w-md">
            <AlertDialogHeader>
              <AlertDialogTitle className="text-base [overflow-wrap:anywhere]">
                Download {what(dialog.names)} as a zip?
              </AlertDialogTitle>
              <AlertDialogDescription className="min-h-10">
                {dialog.plan ? (
                  `${contents(dialog.plan)} Preparing it can take several minutes.`
                ) : dialog.slow ? (
                  <span className="flex items-center gap-2">
                    <Loader2Icon className="size-4 animate-spin" aria-hidden /> Calculating the size…
                  </span>
                ) : null}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <p className={cn('font-mono text-xs break-all text-muted-foreground', !dialog.plan && 'invisible')}>
              Saves as {dialog.plan?.name ?? '…'}
            </p>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction disabled={!dialog.plan} onClick={() => dialog.plan && start(dialog.plan)}>
                {dialog.plan?.bytes ? `Download ${formatSize(dialog.plan.bytes)}` : 'Download'}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        )}
      </AlertDialog>

      <AlertDialog open={dialog?.kind === 'too_large'} onOpenChange={close}>
        {dialog?.kind === 'too_large' && (
          <AlertDialogContent className="sm:max-w-md">
            <AlertDialogHeader>
              <AlertDialogTitle className="text-base [overflow-wrap:anywhere]">
                {dialog.names.length === 1
                  ? `${what(dialog.names)} is too large to download at once`
                  : `These ${what(dialog.names)} are too large to download at once`}
              </AlertDialogTitle>
              <AlertDialogDescription>
                {dialog.names.length === 1 ? 'It’s' : 'Together they’re'} more than {formatSize(dialog.limit)}, the most
                that can be downloaded at once.{' '}
                {dialog.names.length === 1 ? 'Open it and download what you need from inside' : 'Select fewer items'},
                or ask IT.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogAction>OK</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        )}
      </AlertDialog>
    </>
  )

  return { request, dialogs }
}

function contents(plan: Ready): string {
  const count = itemCount(plan.files, plan.folders)
  if (!count) return 'It’s empty.'
  if (!plan.files) return `It holds ${count} and no files.`
  return `It holds ${count}, ${formatSize(plan.bytes)} in all.`
}

function showError(e: unknown) {
  const code = e instanceof ApiError ? e.code : ''
  if (code === 'denied') toast('This isn’t available to you', { description: 'Your account can’t open it. If you need it, ask IT.' })
  else if (code === 'not_found') toast('It’s no longer there', { description: 'It was moved or deleted since this list was loaded.' })
  else if (code !== 'not_signed_in') toast('The download couldn’t start', { description: 'Try again in a moment; if it keeps happening, ask IT.' })
}

function start(plan: Ready) {
  // A plain navigation: the browser turns it into a download and the page stays. Safari never
  // starts a download loaded in a hidden frame. If the download can't start, the server answers
  // 204 No Content, which browsers ignore, and the progress poll reports it.
  const link = document.createElement('a')
  link.href = api.downloadUrl(plan.id)
  document.body.appendChild(link)
  link.click()
  link.remove()
  follow(plan)
}

/** Follows a download's progress in a toast until it ends. */
function follow(plan: Ready) {
  const id = `download-${plan.id}`
  const started = performance.now()
  let first: { t: number; sent: number } | null = null
  let stoppedHere = false
  let shown = false

  const cancel = () => {
    stoppedHere = true
    api.cancelDownload(plan.id).catch(() => {})
  }

  const tick = async () => {
    let p
    try {
      p = await api.downloadProgress(plan.id)
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 401)) {
        if (shown) toast.dismiss(id)
        return
      }
      window.setTimeout(tick, POLL_MS)
      return
    }
    if (p.state === 'done') {
      toast('Downloaded', { id, description: `Saved to your Mac as ${plan.name}`, duration: 6000, action: undefined })
    } else if (p.state === 'failed') {
      toast('The download didn’t finish', {
        id,
        description: 'Try again; if it keeps happening, ask IT.',
        duration: 10_000,
        action: undefined,
      })
    } else if (p.state === 'cancelled') {
      if (stoppedHere) toast.dismiss(id)
      else toast('Download stopped', { id, description: plan.name, duration: 4000, action: undefined })
    } else if (p.state === 'ready' && performance.now() - started >= START_TIMEOUT_MS) {
      // The browser never asked for it, and every poll keeps the session from expiring: stop here,
      // and release the download on the server.
      api.cancelDownload(plan.id).catch(() => {})
      toast('The download didn’t start', {
        id,
        description: 'Try again; if it keeps happening, ask IT.',
        duration: 10_000,
        action: undefined,
      })
    } else {
      const now = performance.now()
      if (p.sent > 0 && !first) first = { t: now, sent: p.sent }
      if (now - started >= SHOW_PROGRESS_AFTER_MS) {
        shown = true
        toast(`Downloading ${plan.name}`, {
          id,
          duration: Infinity,
          description: <ProgressLine sent={p.sent} total={Math.max(p.total, 1)} left={timeLeft(first, now, p)} />,
          action: { label: 'Cancel', onClick: cancel },
        })
      }
      window.setTimeout(tick, POLL_MS)
    }
  }
  window.setTimeout(tick, 400)
}

function ProgressLine({ sent, total, left }: { sent: number; total: number; left: string }) {
  const shown = Math.min(sent, total)
  return (
    <div className="flex flex-col gap-1.5 pt-1.5">
      <Progress value={(shown / total) * 100} className="h-1.5" />
      <span className="tabular-nums">
        {formatSize(shown)} of {formatSize(total)}
        {left && ` · ${left}`}
      </span>
    </div>
  )
}

function timeLeft(first: { t: number; sent: number } | null, now: number, p: { sent: number; total: number }): string {
  if (!first || now - first.t < 3000 || p.sent <= first.sent) return ''
  const rate = (p.sent - first.sent) / ((now - first.t) / 1000)
  const seconds = Math.max(p.total - p.sent, 0) / rate
  if (seconds < 60) return 'less than a minute left'
  if (seconds < 5 * 60) return 'a few minutes left'
  if (seconds < 90 * 60) return `about ${Math.round(seconds / 60)} minutes left`
  return `about ${Math.round(seconds / 3600)} hours left`
}
