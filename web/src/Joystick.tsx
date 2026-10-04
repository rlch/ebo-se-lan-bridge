import { useRef, useState } from 'react'
import type { Vec } from './useDrive'

const DEADZONE = 0.08

/** Analog stick: direction = heading, distance from centre = speed (scaled by `speed`). */
export function Joystick({ speed, disabled, onChange }: {
  speed: number
  disabled?: boolean
  onChange: (v: Vec) => void
}) {
  const base = useRef<HTMLDivElement>(null)
  const [knob, setKnob] = useState({ x: 0, y: 0, active: false })

  const update = (e: React.PointerEvent) => {
    const r = base.current!.getBoundingClientRect()
    const radius = r.width / 2 - r.width * 0.19
    let dx = e.clientX - (r.left + r.width / 2)
    let dy = e.clientY - (r.top + r.height / 2)
    const d = Math.hypot(dx, dy)
    if (d > radius) {
      dx = (dx / d) * radius
      dy = (dy / d) * radius
    }
    setKnob({ x: dx, y: dy, active: true })
    const nx = dx / radius
    const ny = -dy / radius
    const mag = Math.hypot(nx, ny)
    onChange(mag < DEADZONE
      ? { ly: 0, rx: 0 }
      : { ly: +(ny * speed).toFixed(3), rx: +(nx * speed).toFixed(3) })
  }

  const end = () => {
    setKnob({ x: 0, y: 0, active: false })
    onChange({ ly: 0, rx: 0 })
  }

  return (
    <div
      ref={base}
      className={`joystick${knob.active ? ' active' : ''}${disabled ? ' disabled' : ''}`}
      aria-label="Drive joystick"
      role="application"
      onPointerDown={(e) => {
        if (disabled) return
        e.currentTarget.setPointerCapture(e.pointerId)
        update(e)
      }}
      onPointerMove={(e) => knob.active && update(e)}
      onPointerUp={end}
      onPointerCancel={end}
      onLostPointerCapture={() => knob.active && end()}
    >
      <div className="joystick-ring" />
      <div className="joystick-knob" style={{ transform: `translate(calc(-50% + ${knob.x}px), calc(-50% + ${knob.y}px))` }} />
    </div>
  )
}
