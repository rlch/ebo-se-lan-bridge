import { useCallback, useEffect, useRef, useState } from 'react'

export type TalkState = { phase: 'idle' | 'connecting' | 'live'; error?: string }

const OUT_RATE = 8000
const CHUNK = 320 // samples per WebSocket message (40 ms, 640 bytes; server max is 1280)

const TAP_WORKLET = `
class Tap extends AudioWorkletProcessor {
  process(inputs) { const c = inputs[0] && inputs[0][0]; if (c) this.port.postMessage(c.slice(0)); return true }
}
registerProcessor('ebo-tap', Tap)`

/** Box-filter decimator to 8 kHz that keeps its fractional position across calls. */
function makeResampler(inRate: number) {
  const ratio = inRate / OUT_RATE
  let carry = new Float32Array(0)
  let pos = 0
  return (input: Float32Array): Int16Array => {
    const buf = new Float32Array(carry.length + input.length)
    buf.set(carry)
    buf.set(input, carry.length)
    const out: number[] = []
    while (pos + ratio <= buf.length) {
      const a = Math.floor(pos)
      const b = Math.max(a + 1, Math.floor(pos + ratio))
      let sum = 0
      for (let j = a; j < b; j++) sum += buf[j]
      const s = Math.max(-1, Math.min(1, sum / (b - a)))
      out.push(s < 0 ? s * 32768 : s * 32767)
      pos += ratio
    }
    const keep = Math.floor(pos)
    carry = buf.slice(keep)
    pos -= keep
    return Int16Array.from(out)
  }
}

/** Push-to-talk: microphone -> 8 kHz mono PCM16 -> /ws/talk (the bridge drives the robot speaker). */
export function useTalk() {
  const [state, setState] = useState<TalkState>({ phase: 'idle' })
  const session = useRef<{ wanted: boolean; release: () => void } | null>(null)

  const stop = useCallback(() => {
    const s = session.current
    session.current = null
    if (s) {
      s.wanted = false
      s.release()
    }
    setState((prev) => (prev.phase === 'idle' ? prev : { phase: 'idle' }))
  }, [])

  const start = useCallback(async () => {
    if (session.current) return
    const cleanups: (() => void)[] = []
    const s = { wanted: true, release: () => cleanups.splice(0).reverse().forEach((f) => f()) }
    session.current = s
    setState({ phase: 'connecting' })
    const fail = (error: string) => {
      if (session.current === s) session.current = null
      s.release()
      setState({ phase: 'idle', error })
    }
    try {
      const ctx = new AudioContext()
      cleanups.push(() => void ctx.close())
      await ctx.resume()
      const mic = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      })
      cleanups.push(() => mic.getTracks().forEach((t) => t.stop()))
      if (!s.wanted) return s.release()

      const url = URL.createObjectURL(new Blob([TAP_WORKLET], { type: 'text/javascript' }))
      await ctx.audioWorklet.addModule(url)
      URL.revokeObjectURL(url)
      if (!s.wanted) return s.release()

      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${proto}//${location.host}/ws/talk`)
      ws.binaryType = 'arraybuffer'
      cleanups.push(() => {
        if (ws.readyState < WebSocket.CLOSING) ws.close(1000, 'released')
      })
      ws.onclose = () => {
        if (session.current === s) fail('Speaker disconnected')
      }
      ws.onerror = () => {
        if (session.current === s) fail('Talk connection failed')
      }
      ws.onmessage = (ev) => {
        let msg: { type?: string }
        try {
          msg = JSON.parse(ev.data)
        } catch {
          return
        }
        if (msg.type === 'error') return fail('Speaker unavailable')
        if (msg.type !== 'ready') return
        if (!s.wanted) return s.release()

        const source = ctx.createMediaStreamSource(mic)
        const tap = new AudioWorkletNode(ctx, 'ebo-tap')
        const mute = ctx.createGain()
        mute.gain.value = 0
        source.connect(tap).connect(mute).connect(ctx.destination)
        cleanups.push(() => {
          tap.port.onmessage = null
          source.disconnect()
          tap.disconnect()
        })
        const resample = makeResampler(ctx.sampleRate)
        let pending = new Int16Array(0)
        tap.port.onmessage = (e: MessageEvent<Float32Array>) => {
          if (!s.wanted || ws.readyState !== WebSocket.OPEN) return
          const pcm = resample(e.data)
          const merged = new Int16Array(pending.length + pcm.length)
          merged.set(pending)
          merged.set(pcm, pending.length)
          let off = 0
          // Drop audio rather than queue it if the link is slow: talkback must stay real-time.
          for (; off + CHUNK <= merged.length; off += CHUNK) {
            if (ws.bufferedAmount < 4096) ws.send(merged.slice(off, off + CHUNK).buffer)
          }
          pending = merged.slice(off)
        }
        navigator.vibrate?.(15)
        setState({ phase: 'live' })
      }
    } catch (e) {
      fail((e as Error).name === 'NotAllowedError' ? 'Microphone permission denied' : 'Talk unavailable')
    }
  }, [])

  useEffect(() => {
    window.addEventListener('pagehide', stop)
    return () => {
      window.removeEventListener('pagehide', stop)
      stop()
    }
  }, [stop])

  return { state, start, stop }
}
