import { useEffect } from 'react'

import { BrowsePage } from '@/components/browse/browse-page'
import { SharePicker } from '@/components/share-picker'
import { Button } from '@/components/ui/button'
import { useRequest } from '@/hooks/use-request'
import { api } from '@/lib/api'
import { navigate, routeHref, useRoute } from '@/lib/router'

export default function App() {
  const route = useRoute()
  const info = useRequest('info', (s) => api.info(s))
  const roots = info.data?.roots
  const known = roots?.some((r) => r.id === route.rootId) ?? false

  // One share: skip the picker. Unknown share: back to the picker.
  useEffect(() => {
    if (!roots) return
    if (route.rootId === null && roots.length === 1) navigate(routeHref(roots[0].id, []), { replace: true })
    else if (route.rootId !== null && !known) navigate('/', { replace: true })
  }, [roots, route.rootId, known])

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
  if (route.rootId !== null && known) {
    return <BrowsePage key={route.rootId} info={info.data} rootId={route.rootId} parts={route.parts} at={route.at} />
  }
  if (route.rootId === null && info.data.roots.length !== 1) return <SharePicker info={info.data} />
  return null
}
