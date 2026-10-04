import { useEffect, useState } from 'react'
import { getInfo, type Info } from './api'

export type Status = { info: Info | null; reachable: boolean }

/** Polls the bridge every 1.5 s while the page is visible. */
export function useStatus(): Status {
  const [status, setStatus] = useState<Status>({ info: null, reachable: true })

  useEffect(() => {
    let timer = 0
    let ctrl: AbortController | null = null
    const tick = async () => {
      ctrl?.abort()
      ctrl = new AbortController()
      try {
        const info = await getInfo(ctrl.signal)
        setStatus({ info, reachable: true })
      } catch (e) {
        if ((e as Error).name !== 'AbortError') setStatus((s) => ({ ...s, reachable: false }))
      }
      if (document.visibilityState === 'visible') timer = window.setTimeout(tick, 1500)
    }
    const onVisible = () => {
      window.clearTimeout(timer)
      if (document.visibilityState === 'visible') tick()
    }
    tick()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearTimeout(timer)
      ctrl?.abort()
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [])

  return status
}
