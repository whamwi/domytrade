'use client'

import { useEffect, useState } from 'react'
import { EconAlert } from '../hooks/useEconomicAlerts'

const IMPACT_COLOR: Record<string, string> = {
  High:   '#f87171',
  Medium: '#fbbf24',
  Low:    '#94a3b8',
}

interface Props {
  alert: EconAlert
  onDismiss: () => void
}

export default function AlertToast({ alert, onDismiss }: Props) {
  const [visible, setVisible] = useState(false)

  // Animate in
  useEffect(() => {
    const t1 = setTimeout(() => setVisible(true), 10)
    // Auto-dismiss after 8s
    const t2 = setTimeout(() => {
      setVisible(false)
      setTimeout(onDismiss, 300)
    }, 8_000)
    return () => { clearTimeout(t1); clearTimeout(t2) }
  }, [onDismiss])

  const color = IMPACT_COLOR[alert.impact] ?? '#94a3b8'

  return (
    <div
      style={{
        position  : 'fixed',
        bottom    : 24,
        right     : 24,
        zIndex    : 100,
        width     : 340,
        background: 'var(--bg-panel)',
        border    : `1px solid ${color}`,
        borderLeft: `4px solid ${color}`,
        borderRadius: 10,
        boxShadow : `0 8px 32px rgba(0,0,0,0.5), 0 0 20px ${color}22`,
        padding   : '14px 16px',
        transition: 'all 0.3s ease',
        opacity   : visible ? 1 : 0,
        transform : visible ? 'translateY(0)' : 'translateY(16px)',
      }}
    >
      {/* Top row */}
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span style={{
            width: 8, height: 8, borderRadius: '50%',
            background: color,
            boxShadow: `0 0 8px ${color}`,
            display: 'inline-block', flexShrink: 0,
          }} />
          <span className="text-xs font-bold uppercase tracking-widest" style={{ color }}>
            ⚡ In 15 min — USD Release
          </span>
        </div>
        <button
          onClick={() => { setVisible(false); setTimeout(onDismiss, 300) }}
          style={{ color: 'var(--text-muted)', background: 'rgba(255,255,255,0.06)', border: 'none', cursor: 'pointer', padding: '4px 7px', borderRadius: 6, lineHeight: 1 }}
          title="Dismiss"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>

      {/* Event title */}
      <div className="text-sm font-semibold mt-1" style={{ color: 'var(--text-primary)' }}>
        {alert.title}
      </div>

      {/* Forecast / Previous */}
      {(alert.forecast || alert.previous) && (
        <div className="flex gap-4 mt-1.5 text-xs" style={{ color: 'var(--text-muted)' }}>
          {alert.forecast && (
            <span>Forecast <span style={{ color: 'var(--text-dim)', fontWeight: 600 }}>{alert.forecast}</span></span>
          )}
          {alert.previous && (
            <span>Previous <span style={{ color: 'var(--text-dim)', fontWeight: 600 }}>{alert.previous}</span></span>
          )}
        </div>
      )}
    </div>
  )
}
