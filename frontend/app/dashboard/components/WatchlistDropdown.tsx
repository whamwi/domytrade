'use client'

import { useState, useRef, useEffect } from 'react'
import { Watchlist } from './WatchlistEditor'

interface Props {
  watchlists:        Watchlist[]
  activeWatchlistId: string | null
  onActivate:        (id: string | null) => void
  onNew:             () => void
  onEdit:            (w: Watchlist) => void
}

export default function WatchlistDropdown({ watchlists, activeWatchlistId, onActivate, onNew, onEdit }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  const active = watchlists.find(w => w.id === activeWatchlistId) ?? null

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          padding: '5px 10px', borderRadius: 7, cursor: 'pointer',
          background: active ? 'rgba(251,191,36,0.1)' : 'var(--bg-row)',
          border: `1px solid ${active ? 'rgba(251,191,36,0.35)' : 'var(--border)'}`,
          color: active ? '#fbbf24' : 'var(--text-muted)',
          fontSize: 12, fontWeight: 600, transition: 'all 0.15s',
          whiteSpace: 'nowrap',
        }}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
        </svg>
        {active ? active.name : 'Watchlist'}
        {active && (
          <span style={{ fontSize: 10, opacity: 0.75 }}>
            {active.assets.length}s · {active.models.join('+')}
          </span>
        )}
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', right: 0, zIndex: 60,
          background: 'var(--bg-panel)', border: '1px solid var(--border)',
          borderRadius: 10, minWidth: 220, overflow: 'hidden',
          boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
        }}>
          {/* All Signals option */}
          <button
            onClick={() => { onActivate(null); setOpen(false) }}
            style={{
              width: '100%', textAlign: 'left', padding: '10px 14px',
              background: !active ? 'rgba(59,130,246,0.1)' : 'transparent',
              border: 'none', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
              borderBottom: '1px solid var(--border)',
            }}
            onMouseEnter={e => { if (active) e.currentTarget.style.background = 'var(--bg-row)' }}
            onMouseLeave={e => { if (active) e.currentTarget.style.background = 'transparent' }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={!active ? 'var(--accent-blue)' : 'var(--text-muted)'} strokeWidth="2">
              <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
              <rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
            </svg>
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: !active ? 'var(--accent-blue)' : 'var(--text-primary)' }}>
                All Signals
              </div>
              <div style={{ fontSize: 10, color: 'var(--text-dim)' }}>No filter active</div>
            </div>
            {!active && <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--accent-blue)' }}>✓</span>}
          </button>

          {/* Saved watchlists */}
          {watchlists.length > 0 && (
            <div style={{ maxHeight: 220, overflowY: 'auto' }}>
              {watchlists.map(w => {
                const isActive = w.id === activeWatchlistId
                return (
                  <div
                    key={w.id}
                    style={{
                      display: 'flex', alignItems: 'center',
                      background: isActive ? 'rgba(251,191,36,0.08)' : 'transparent',
                      borderBottom: '1px solid var(--border)',
                    }}
                    onMouseEnter={e => { if (!isActive) (e.currentTarget as HTMLElement).style.background = 'var(--bg-row)' }}
                    onMouseLeave={e => { if (!isActive) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
                  >
                    <button
                      onClick={() => { onActivate(isActive ? null : w.id); setOpen(false) }}
                      style={{ flex: 1, textAlign: 'left', padding: '10px 14px', background: 'none', border: 'none', cursor: 'pointer' }}
                    >
                      <div style={{ fontSize: 12, fontWeight: 600, color: isActive ? '#fbbf24' : 'var(--text-primary)' }}>
                        {isActive && '★ '}{w.name}
                      </div>
                      <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 2 }}>
                        {w.assets.slice(0, 6).join(', ')}{w.assets.length > 6 ? ` +${w.assets.length - 6}` : ''} · {w.models.join('+')}
                      </div>
                    </button>
                    <button
                      onClick={() => { onEdit(w); setOpen(false) }}
                      style={{
                        padding: '8px 10px', background: 'none', border: 'none',
                        cursor: 'pointer', color: 'var(--text-dim)', flexShrink: 0,
                      }}
                      title="Edit"
                      onMouseEnter={e => e.currentTarget.style.color = 'var(--text-primary)'}
                      onMouseLeave={e => e.currentTarget.style.color = 'var(--text-dim)'}
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                      </svg>
                    </button>
                  </div>
                )
              })}
            </div>
          )}

          {/* Create new */}
          <button
            onClick={() => { onNew(); setOpen(false) }}
            style={{
              width: '100%', textAlign: 'left', padding: '10px 14px',
              background: 'transparent', border: 'none', cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 8,
              color: 'var(--accent-blue)',
            }}
            onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-row)'}
            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/>
            </svg>
            <span style={{ fontSize: 12, fontWeight: 600 }}>New Watchlist</span>
          </button>
        </div>
      )}
    </div>
  )
}
