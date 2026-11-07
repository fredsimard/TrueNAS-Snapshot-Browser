import { useEffect } from 'react'

import { BrowsePage } from '@/components/browse/browse-page'
import { SharePicker } from '@/components/share-picker'
import { SignIn } from '@/components/sign-in'
import { Button } from '@/components/ui/button'
import { useRequest } from '@/hooks/use-request'
import { api, SIGNED_OUT_EVENT } from '@/lib/api'
import { navigate, routeHref, useRoute } from '@/lib/router'

export default function App() {
  const route = useRoute()
  const info = useRequest('info', (s) => api.info(s))
  const signedIn = !!info.data?.user
  const roots = info.data?.roots
  const known = roots?.some((r) => r.id === route.rootId) ?? false

  // Back to the sign-in screen when the session ends. The URL stays, so sign-in returns to it.
  const { reload } = info
  useEffect(() => {
    window.addEventListener(SIGNED_OUT_EVENT, reload)
    return () => window.removeEventListener(SIGNED_OUT_EVENT, reload)
  }, [reload])

  // One share: skip the picker. Unknown share: back to the picker.
  useEffect(() => {
    if (!roots || !signedIn) return
    if (route.rootId === null && roots.length === 1) navigate(routeHref(roots[0].id, []), { replace: true })
    else if (route.rootId !== null && !known) navigate('/', { replace: true })
  }, [roots, signedIn, route.rootId, known])

  if (info.error) {
    return (
      <div className="flex h-dvh flex-col items-center justify-center gap-3 text-center">
        <p className="text-muted-foreground">The server didn’t answer. Try again in a moment.</p>
        <Button variant="outline" size="sm" onClick={info.reload}>
          Try again
        </Button>
      </div>
    )
  }
  if (!info.data) return null
  if (!signedIn) return <SignIn serverLabel={info.data.server_label} onSignedIn={reload} />
  if (route.rootId !== null && known) {
    return <BrowsePage key={route.rootId} info={info.data} rootId={route.rootId} parts={route.parts} at={route.at} />
  }
  if (route.rootId === null && info.data.roots.length !== 1) return <SharePicker info={info.data} />
  return null
}
