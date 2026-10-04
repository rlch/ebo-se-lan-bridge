export type Info = {
  configured: boolean
  connected: boolean
  paused: boolean
  awake: boolean
  codec: string | null
  video: 'copy' | 'h264'
  audio_enabled: boolean
  frames_received: number
  battery: number
  charge: number
}

const json = { 'Content-Type': 'application/json' }

export async function getInfo(signal?: AbortSignal): Promise<Info> {
  const r = await fetch('/info', { cache: 'no-store', signal })
  if (!r.ok) throw new Error(`info ${r.status}`)
  return r.json()
}

export function drive(ly: number, rx: number) {
  return fetch('/drive', { method: 'POST', headers: json, body: JSON.stringify({ ly, rx }), keepalive: true })
}

export function stop() {
  return fetch('/stop', { method: 'POST', keepalive: true })
}

export type Action =
  | 'wake' | 'sleep' | 'dock' | 'undock'
  | `${'eyes' | 'night' | 'avoid' | 'fall' | 'patrol'}_${'on' | 'off'}`

export async function action(name: Action) {
  const r = await fetch(`/action/${name}`, { method: 'POST' })
  const body = await r.json().catch(() => ({ ok: false }))
  if (!r.ok || !body.ok) throw new Error(`action ${name} failed`)
}

export async function setConnection(state: 'start' | 'stop') {
  const r = await fetch(`/connection/${state}`, { method: 'POST' })
  if (!r.ok) throw new Error(`connection ${state} failed`)
}
