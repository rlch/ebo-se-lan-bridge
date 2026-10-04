import { useEffect, useState, type RefObject } from 'react'

export type StreamPref = 'auto' | 'webrtc' | 'hls'
export type StreamState = 'idle' | 'connecting' | 'webrtc' | 'hls' | 'error'

const ICE_TIMEOUT_MS = 6000
const RETRY_MS = 2500

class CodecUnsupported extends Error {}

/**
 * Plays the robot stream into `video` while `active`. WebRTC (WHEP, lowest latency) first; with
 * pref "auto" it falls back to LL-HLS when the browser can't take the codec or ICE never connects.
 */
export function useStream(video: RefObject<HTMLVideoElement | null>, active: boolean, pref: StreamPref) {
  const [state, setState] = useState<StreamState>('idle')
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const el = video.current
    if (!active || !el) {
      setState('idle')
      return
    }
    let disposed = false
    let pc = null as RTCPeerConnection | null
    let hls: { destroy(): void } | null = null
    let timer = 0
    const retry = () => {
      if (!disposed) timer = window.setTimeout(() => setAttempt((n) => n + 1), RETRY_MS)
    }

    const startHls = async () => {
      setState('connecting')
      const src = '/hls/ebo/index.m3u8'
      if (el.canPlayType('application/vnd.apple.mpegurl')) {
        el.srcObject = null
        el.src = src
        el.addEventListener('error', () => {
          if (disposed) return
          setState('error')
          retry()
        }, { once: true })
      } else {
        const { default: Hls } = await import('hls.js')
        if (disposed) return
        if (!Hls.isSupported()) {
          setState('error')
          return
        }
        const h = new Hls({ lowLatencyMode: true, liveSyncDurationCount: 1, backBufferLength: 10 })
        hls = h
        h.on(Hls.Events.ERROR, (_e, data) => {
          if (!data.fatal) return
          h.destroy()
          hls = null
          setState('error')
          retry()
        })
        h.loadSource(src)
        h.attachMedia(el)
      }
      el.play().catch(() => {})
      el.addEventListener('playing', () => !disposed && setState('hls'), { once: true })
    }

    const startWebRtc = async () => {
      setState('connecting')
      const p = new RTCPeerConnection()
      pc = p
      p.addTransceiver('video', { direction: 'recvonly' })
      p.addTransceiver('audio', { direction: 'recvonly' })
      p.ontrack = (e) => {
        if (el.srcObject !== e.streams[0]) el.srcObject = e.streams[0]
        el.play().catch(() => {})
      }
      await p.setLocalDescription(await p.createOffer())
      await new Promise<void>((resolve) => {
        if (p.iceGatheringState === 'complete') return resolve()
        const t = window.setTimeout(resolve, 1500)
        p.addEventListener('icegatheringstatechange', () => {
          if (p.iceGatheringState === 'complete') {
            window.clearTimeout(t)
            resolve()
          }
        })
      })
      const r = await fetch('/whep', {
        method: 'POST',
        headers: { 'Content-Type': 'application/sdp' },
        body: p.localDescription!.sdp,
      })
      if (!r.ok) {
        const body = await r.text()
        if (r.status === 400 && /codec/i.test(body)) throw new CodecUnsupported(body)
        throw new Error(`whep ${r.status}`)
      }
      await p.setRemoteDescription({ type: 'answer', sdp: await r.text() })
      await new Promise<void>((resolve, reject) => {
        const t = window.setTimeout(() => reject(new Error('ice timeout')), ICE_TIMEOUT_MS)
        p.addEventListener('connectionstatechange', () => {
          if (p.connectionState === 'connected') {
            window.clearTimeout(t)
            resolve()
          } else if (p.connectionState === 'failed') {
            window.clearTimeout(t)
            reject(new Error('ice failed'))
          }
        })
      })
      if (disposed) return
      setState('webrtc')
      p.addEventListener('connectionstatechange', () => {
        if (p.connectionState === 'failed' || p.connectionState === 'disconnected') {
          setState('connecting')
          retry()
        }
      })
    }

    ;(async () => {
      if (pref === 'hls') return startHls()
      try {
        await startWebRtc()
      } catch (e) {
        if (disposed) return
        pc?.close()
        pc = null
        const iceOrCodec = e instanceof CodecUnsupported || /^ice /.test((e as Error).message)
        if (pref === 'auto' && iceOrCodec) return startHls()
        setState('error')
        retry()
      }
    })()

    return () => {
      disposed = true
      window.clearTimeout(timer)
      pc?.close()
      hls?.destroy()
      el.srcObject = null
      el.removeAttribute('src')
      el.load()
    }
  }, [video, active, pref, attempt])

  return state
}
