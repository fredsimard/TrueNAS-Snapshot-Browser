import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

export interface RequestState<T> {
  /** Last successful result, kept while the next key loads. */
  data: T | undefined
  /** Key that `data` belongs to. */
  dataKey: string | null
  error: unknown
  loading: boolean
  reload: () => void
}

/** Fetches whenever `key` changes; aborts the previous request. `key` null: idle. */
export function useRequest<T>(key: string | null, fetcher: (signal: AbortSignal) => Promise<T>): RequestState<T> {
  const [state, setState] = useState<{ data?: T; dataKey: string | null; error: unknown; loading: boolean }>({
    dataKey: null,
    error: null,
    loading: key !== null,
  })
  const [nonce, setNonce] = useState(0)
  const fetcherRef = useRef(fetcher)
  useLayoutEffect(() => {
    fetcherRef.current = fetcher
  })

  useEffect(() => {
    if (key === null) return
    const ctrl = new AbortController()
    setState((s) => ({ ...s, error: null, loading: true }))
    fetcherRef.current(ctrl.signal).then(
      (data) => setState({ data, dataKey: key, error: null, loading: false }),
      (error) => {
        if (!ctrl.signal.aborted) setState((s) => ({ ...s, error, loading: false }))
      },
    )
    return () => ctrl.abort()
  }, [key, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data: state.data, dataKey: state.dataKey, error: state.error, loading: state.loading, reload }
}
