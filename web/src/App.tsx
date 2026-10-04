import { useCallback, useEffect, useRef, useState } from 'react'
import {
  BatteryCharging, BatteryFull, BatteryLow, BatteryMedium, Camera, Eye, House, LogOut, Maximize,
  Mic, Minimize, Moon, MoonStar, PlugZap, Route, ShieldCheck, SlidersHorizontal, Sun,
  TriangleAlert, Unplug, Volume2, VolumeX, X,
} from 'lucide-react'
import { action, setConnection, type Action } from './api'
import { Joystick } from './Joystick'
import { useDrive } from './useDrive'
import { useStatus } from './useStatus'
import { useStream, type StreamPref, type StreamState } from './useStream'
import { useTalk } from './useTalk'

function useStored<T extends string | number>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key)
      if (raw !== null) return (typeof initial === 'number' ? Number(raw) : raw) as T
    } catch { /* storage unavailable */ }
    return initial
  })
  const set = useCallback((v: T) => {
    setValue(v)
    try { localStorage.setItem(key, String(v)) } catch { /* storage unavailable */ }
  }, [key])
  return [value, set] as const
}

type Robot = 'unreachable' | 'unconfigured' | 'connecting' | 'released' | 'asleep' | 'live'

const ROBOT_LABEL: Record<Robot, string> = {
  unreachable: 'Bridge offline',
  unconfigured: 'Not set up',
  connecting: 'Connecting',
  released: 'Released',
  asleep: 'Asleep',
  live: 'Live',
}

const STREAM_LABEL: Record<StreamState, string> = {
  idle: '', connecting: 'Starting video', webrtc: 'WebRTC', hls: 'HLS', error: 'Video error',
}

const FEATURES = [
  { key: 'eyes', label: 'Eye lights', icon: Eye },
  { key: 'night', label: 'Night vision', icon: MoonStar },
  { key: 'avoid', label: 'Anti-collision', icon: ShieldCheck },
  { key: 'fall', label: 'Fall protection', icon: TriangleAlert },
  { key: 'patrol', label: 'Patrol', icon: Route },
] as const

function Battery({ level, charging }: { level: number; charging: boolean }) {
  const Icon = charging ? BatteryCharging : level >= 60 ? BatteryFull : level >= 25 ? BatteryMedium : BatteryLow
  return (
    <span className={`chip${level >= 0 && level < 15 && !charging ? ' warn' : ''}`}>
      <Icon size={16} aria-hidden />
      {level >= 0 ? `${level}%` : '—'}
    </span>
  )
}

export default function App() {
  const { info, reachable } = useStatus()
  const videoRef = useRef<HTMLVideoElement>(null)
  const appRef = useRef<HTMLDivElement>(null)
  const [pref, setPref] = useStored<StreamPref>('ebo.stream', 'auto')
  const [speed, setSpeed] = useStored<number>('ebo.speed', 0.7)
  const [sheet, setSheet] = useState(false)
  const [muted, setMuted] = useState(true)
  const [fullscreen, setFullscreen] = useState(false)
  const [features, setFeatures] = useState<Record<string, boolean>>({})
  const [toast, setToast] = useState<string | null>(null)
  const [flash, setFlash] = useState(false)
  const [busy, setBusy] = useState(false)

  const robot: Robot = !reachable || !info ? 'unreachable'
    : !info.configured ? 'unconfigured'
    : info.paused ? 'released'
    : !info.connected ? 'connecting'
    : info.awake ? 'live' : 'asleep'
  const live = robot === 'live'
  const stream = useStream(videoRef, live, pref)
  const talk = useTalk()
  const setDrive = useDrive(live, speed)

  const say = useCallback((msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast((t) => (t === msg ? null : t)), 2600)
  }, [])

  useEffect(() => { if (talk.state.error) say(talk.state.error) }, [talk.state, say])
  const { phase: talkPhase } = talk.state
  const stopTalk = talk.stop
  useEffect(() => { if (!live && talkPhase !== 'idle') stopTalk() }, [live, talkPhase, stopTalk])

  // Keep the screen on while watching.
  useEffect(() => {
    if (!live || !('wakeLock' in navigator)) return
    let lock: WakeLockSentinel | null = null
    const acquire = () => {
      if (document.visibilityState === 'visible') {
        navigator.wakeLock.request('screen').then((l) => { lock = l }, () => {})
      }
    }
    acquire()
    document.addEventListener('visibilitychange', acquire)
    return () => {
      document.removeEventListener('visibilitychange', acquire)
      void lock?.release()
    }
  }, [live])

  useEffect(() => {
    const onChange = () => setFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  const run = async (name: Action, done?: string) => {
    try {
      await action(name)
      if (done) say(done)
    } catch {
      say('Robot did not accept that')
    }
  }

  const toggleFeature = (key: string) => {
    const on = !features[key]
    setFeatures((f) => ({ ...f, [key]: on }))
    void run(`${key}_${on ? 'on' : 'off'}` as Action)
  }

  const toggleConnection = async () => {
    setBusy(true)
    try {
      await setConnection(info?.paused ? 'start' : 'stop')
      say(info?.paused ? 'Reconnecting to the robot' : 'Robot released for the ROLA app')
    } catch {
      say('Bridge did not respond')
    } finally {
      setBusy(false)
    }
  }

  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else {
        await appRef.current!.requestFullscreen({ navigationUI: 'hide' })
        await (screen.orientation as ScreenOrientation & { lock?: (o: string) => Promise<void> })
          .lock?.('landscape').catch(() => {})
      }
    } catch {
      say('Fullscreen not available')
    }
  }

  const snapshot = async () => {
    const v = videoRef.current
    if (!v || !v.videoWidth) return say('No video yet')
    const c = document.createElement('canvas')
    c.width = v.videoWidth
    c.height = v.videoHeight
    c.getContext('2d')!.drawImage(v, 0, 0)
    setFlash(true)
    window.setTimeout(() => setFlash(false), 350)
    const blob = await new Promise<Blob | null>((r) => c.toBlob(r, 'image/jpeg', 0.92))
    if (!blob) return say('Snapshot failed')
    const name = `ebo-${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}.jpg`
    const file = new File([blob], name, { type: 'image/jpeg' })
    if (navigator.canShare?.({ files: [file] })) {
      try { await navigator.share({ files: [file] }); return } catch { /* cancelled: fall through */ }
    }
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = name
    a.click()
    window.setTimeout(() => URL.revokeObjectURL(a.href), 1000)
  }

  const toggleMute = () => {
    const v = videoRef.current
    if (!v) return
    v.muted = !v.muted
    setMuted(v.muted)
    if (!v.muted) v.play().catch(() => {})
    if (!v.muted && info && !info.audio_enabled) say('Robot audio is off on the bridge (EBO_AUDIO=1)')
  }

  const startTalk = (e: React.PointerEvent<HTMLButtonElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    void talk.start()
  }

  return (
    <div ref={appRef} className={`app robot-${robot}${fullscreen ? ' is-fullscreen' : ''}`}>
      <section className="stage">
        <video ref={videoRef} autoPlay muted playsInline disablePictureInPicture />
        {flash && <div className="flash" />}

        {robot !== 'live' && (
          <div className="veil">
            <img src="/robot.svg" alt="" className={`veil-robot ${robot}`} />
            <p className="veil-title">{ROBOT_LABEL[robot]}</p>
            {robot === 'asleep' && (
              <button className="pill-btn" onClick={() => run('wake', 'Waking up…')}>
                <Sun size={18} /> Wake up
              </button>
            )}
            {robot === 'released' && (
              <button className="pill-btn" disabled={busy} onClick={toggleConnection}>
                <PlugZap size={18} /> Reconnect
              </button>
            )}
            {robot === 'connecting' && <p className="veil-sub">Reaching the robot over the LAN…</p>}
            {robot === 'unconfigured' && <p className="veil-sub">The bridge has no robot credentials yet (see docs/SETUP.md).</p>}
            {robot === 'unreachable' && <p className="veil-sub">Can’t reach the bridge. Is Tailscale on?</p>}
          </div>
        )}

        <header className="hud">
          <div className="hud-left">
            <span className={`chip status ${robot}`}><i className="dot" />{ROBOT_LABEL[robot]}</span>
            {info && <Battery level={info.battery} charging={info.charge > 0} />}
            {live && stream !== 'idle' && <span className="chip subtle">{STREAM_LABEL[stream]}</span>}
          </div>
          <div className="hud-right">
            <button className="icon-btn" onClick={toggleMute} aria-label={muted ? 'Listen' : 'Mute'} disabled={!live}>
              {muted ? <VolumeX size={20} /> : <Volume2 size={20} />}
            </button>
            <button className="icon-btn" onClick={snapshot} aria-label="Snapshot" disabled={!live}>
              <Camera size={20} />
            </button>
            {document.fullscreenEnabled && (
              <button className="icon-btn" onClick={toggleFullscreen} aria-label="Fullscreen">
                {fullscreen ? <Minimize size={20} /> : <Maximize size={20} />}
              </button>
            )}
            <button className="icon-btn" onClick={() => setSheet(true)} aria-label="Controls">
              <SlidersHorizontal size={20} />
            </button>
          </div>
        </header>
      </section>

      <section className="controls">
        <Joystick speed={speed} disabled={!live} onChange={setDrive} />
        <div className="quick">
          <button className="round-btn" onClick={() => run('dock', 'Heading home')} disabled={!info?.connected} aria-label="Dock">
            <House size={22} />
          </button>
          <button className="round-btn" onClick={() => run(live ? 'sleep' : 'wake')} disabled={!info?.connected} aria-label={live ? 'Sleep' : 'Wake'}>
            {live ? <Moon size={22} /> : <Sun size={22} />}
          </button>
        </div>
        <button
          className={`talk ${talk.state.phase}`}
          disabled={!live}
          onPointerDown={startTalk}
          onPointerUp={talk.stop}
          onPointerCancel={talk.stop}
          onLostPointerCapture={talk.stop}
          onContextMenu={(e) => e.preventDefault()}
          aria-label="Hold to talk"
        >
          <Mic size={34} />
          <span>{talk.state.phase === 'live' ? 'Talking' : talk.state.phase === 'connecting' ? 'Connecting' : 'Hold to talk'}</span>
        </button>
      </section>

      {sheet && (
        <div className="sheet-backdrop" onClick={() => setSheet(false)}>
          <div className="sheet" role="dialog" aria-label="Robot controls" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-head">
              <h2>EBO</h2>
              <button className="icon-btn" onClick={() => setSheet(false)} aria-label="Close"><X size={20} /></button>
            </div>

            <h3>Robot</h3>
            <div className="grid">
              <button className="tile" onClick={() => run('wake', 'Waking up…')} disabled={!info?.connected}><Sun />Wake</button>
              <button className="tile" onClick={() => run('sleep', 'Going to sleep')} disabled={!info?.connected}><Moon />Sleep</button>
              <button className="tile" onClick={() => run('dock', 'Heading home')} disabled={!info?.connected}><House />Dock</button>
              <button className="tile" onClick={() => run('undock', 'Leaving the dock')} disabled={!info?.connected}><LogOut />Leave dock</button>
            </div>

            <h3>Features</h3>
            <div className="grid">
              {FEATURES.map(({ key, label, icon: Icon }) => (
                <button key={key} className={`tile toggle${features[key] ? ' on' : ''}`} onClick={() => toggleFeature(key)} disabled={!info?.connected}>
                  <Icon />{label}
                </button>
              ))}
            </div>

            <h3>Driving</h3>
            <label className="row">
              <span>Max speed</span>
              <input type="range" min={0.2} max={1} step={0.05} value={speed} onChange={(e) => setSpeed(Number(e.target.value))} />
              <b>{Math.round(speed * 100)}%</b>
            </label>

            <h3>Video</h3>
            <div className="segmented" role="radiogroup">
              {(['auto', 'webrtc', 'hls'] as const).map((p) => (
                <button key={p} role="radio" aria-checked={pref === p} className={pref === p ? 'on' : ''} onClick={() => setPref(p)}>
                  {p === 'auto' ? 'Auto' : p === 'webrtc' ? 'WebRTC' : 'HLS'}
                </button>
              ))}
            </div>

            <h3>Connection</h3>
            <button className="wide" onClick={toggleConnection} disabled={busy || !reachable}>
              {info?.paused ? <><PlugZap size={18} /> Reconnect to the robot</> : <><Unplug size={18} /> Release robot for the ROLA app</>}
            </button>

            <p className="diag">
              {info ? `${info.frames_received} frames · ${info.codec ?? '—'} · ${info.video === 'h264' ? 'H.264 transcode' : 'passthrough'}` : 'no status'}
              {' · '}<a href="/classic">classic panel</a>
            </p>
          </div>
        </div>
      )}

      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  )
}
