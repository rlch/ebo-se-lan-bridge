import { useCallback, useEffect, useRef } from 'react'
import { drive, stop } from './api'

export type Vec = { ly: number; rx: number }

const HZ = 15
const KEYS: Record<string, keyof Vec | `-${keyof Vec}`> = {
  ArrowUp: 'ly', KeyW: 'ly',
  ArrowDown: '-ly', KeyS: '-ly',
  ArrowLeft: '-rx', KeyA: '-rx',
  ArrowRight: 'rx', KeyD: 'rx',
}

/**
 * Streams the drive vector to the bridge at 15 Hz while it is non-zero, over /ws/drive (HTTP
 * fallback). Zero means stop. The bridge's watchdog halts the robot if updates stop arriving.
 */
export function useDrive(enabled: boolean, speed: number) {
  const vec = useRef<Vec>({ ly: 0, rx: 0 })
  const ws = useRef<WebSocket | null>(null)
  const opened = useRef(0)
  const timer = useRef(0)
  const keys = useRef(new Set<string>())

  const socket = useCallback(() => {
    const cur = ws.current
    if (cur && (cur.readyState <= WebSocket.OPEN || Date.now() - opened.current < 2000)) return cur
    opened.current = Date.now()
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const next = new WebSocket(`${proto}//${location.host}/ws/drive`)
    ws.current = next
    return next
  }, [])

  const send = useCallback(() => {
    const { ly, rx } = vec.current
    const s = socket()
    if (s.readyState === WebSocket.OPEN) s.send(JSON.stringify({ ly, rx }))
    else void drive(ly, rx).catch(() => {})
  }, [socket])

  const set = useCallback((v: Vec) => {
    const moving = v.ly !== 0 || v.rx !== 0
    const wasMoving = vec.current.ly !== 0 || vec.current.rx !== 0
    vec.current = v
    if (moving && !timer.current) {
      send()
      timer.current = window.setInterval(send, 1000 / HZ)
    } else if (!moving && wasMoving) {
      window.clearInterval(timer.current)
      timer.current = 0
      send()
      void stop().catch(() => {})
    }
  }, [send])

  // Keyboard: arrows / WASD, combinable; Space stops.
  useEffect(() => {
    if (!enabled) return
    const apply = () => {
      const v = { ly: 0, rx: 0 }
      for (const code of keys.current) {
        const k = KEYS[code]
        if (!k) continue
        const neg = k.startsWith('-')
        const axis = (neg ? k.slice(1) : k) as keyof Vec
        v[axis] += neg ? -speed : speed
      }
      set(v)
    }
    const down = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return
      if (e.code === 'Space') {
        e.preventDefault()
        keys.current.clear()
        return set({ ly: 0, rx: 0 })
      }
      if (!KEYS[e.code] || e.repeat) return
      e.preventDefault()
      keys.current.add(e.code)
      apply()
    }
    const up = (e: KeyboardEvent) => {
      if (keys.current.delete(e.code)) apply()
    }
    const blur = () => {
      keys.current.clear()
      set({ ly: 0, rx: 0 })
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.removeEventListener('blur', blur)
      blur()
    }
  }, [enabled, speed, set])

  // Open the socket early so the first touch doesn't pay the handshake; close on unmount.
  useEffect(() => {
    if (!enabled) return
    socket()
    return () => {
      window.clearInterval(timer.current)
      timer.current = 0
      ws.current?.close()
      ws.current = null
    }
  }, [enabled, socket])

  return set
}
