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
    // Auto-dismiss after 30s
    const t2 = setTimeout(() => {
      setVisible(false)
      setTimeout(onDismiss, 300)
    }, 30_000)
    return () => { clearTimeout(t1); clearTimeout(t2) }
  }, [onDismiss])

  const color = IMPACT_COLOR[alert.impact] ?? '#94a3b8'

  return (
    <div
      style={{
        position     : 'fixed',
        top          : 16,
        left         : '50%',
        transform    : visible ? 'translateX(-50%) translateY(0)' : 'translateX(-50%) translateY(-24px)',
        zIndex       : 2000,
        width        : 460,
        background   : '#0f0d18',
        border       : `1px solid ${color}`,
        borderTop    : `4px solid ${color}`,
        borderRadius : 10,
        boxShadow    : `0 0 0 1px rgba(0,0,0,0.9), 0 16px 48px rgba(0,0,0,0.8), 0 0 40px ${color}33`,
        padding      : '16px 20px',
        transition   : 'all 0.3s cubic-bezier(0.34,1.56,0.64,1)',
        opacity      : visible ? 1 : 0,
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
      <div style={{ color: '#e2e8f0', fontSize: '15px', fontWeight: 700, marginTop: '6px', letterSpacing: '-0.01em' }}>
        {alert.title}
      </div>

      {/* Forecast / Previous */}
      {(alert.forecast || alert.previous) && (
        <div className="flex gap-4 mt-1.5 text-xs" style={{ color: '#64748b' }}>
          {alert.forecast && (
            <span>Forecast <span style={{ color: '#cbd5e1', fontWeight: 600 }}>{alert.forecast}</span></span>
          )}
          {alert.previous && (
            <span>Previous <span style={{ color: '#cbd5e1', fontWeight: 600 }}>{alert.previous}</span></span>
          )}
        </div>
      )}
    </div>
  )
}
