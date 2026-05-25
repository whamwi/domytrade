'use client'

import { useEffect, useState, useCallback } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

interface CalEvent {
  title: string
  country: string
  date: string     // ISO 8601 with tz offset
  impact: 'High' | 'Medium' | 'Low'
  forecast: string
  previous: string
}

const IMPACT_COLOR = {
  High:   '#f87171',
  Medium: '#fbbf24',
  Low:    '#64748b',
}

const FLAG: Record<string, string> = {
  USD: '🇺🇸', EUR: '🇪🇺', GBP: '🇬🇧', JPY: '🇯🇵',
  CAD: '🇨🇦', AUD: '🇦🇺', NZD: '🇳🇿', CHF: '🇨🇭',
}

function formatDay(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', {
    timeZone: 'America/New_York',
    weekday: 'long',
    month: 'short',
    day: 'numeric',
  })
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('en-US', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }) + ' ET'
}

function dayKey(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleDateString('en-CA', { timeZone: 'America/New_York' }) // YYYY-MM-DD
}

interface Props {
  onClose: () => void
}

export default function BriefingModal({ onClose }: Props) {
  const [events, setEvents] = useState<CalEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [impactFilter, setImpactFilter] = useState<'all' | 'High' | 'Medium'>('all')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/api/briefing`, { cache: 'no-store' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setEvents(json.events ?? [])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  // Filter & group by day
  const filtered = events.filter(ev =>
    impactFilter === 'all' ? true : ev.impact === impactFilter
  )
  const days: Record<string, CalEvent[]> = {}
  for (const ev of filtered) {
    const k = dayKey(ev.date)
    if (!days[k]) days[k] = []
    days[k].push(ev)
  }
  const sortedDays = Object.keys(days).sort()

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(2px)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      {/* Panel */}
      <div
        className="relative flex flex-col rounded-xl overflow-hidden"
        style={{
          width: 'min(720px, 95vw)',
          maxHeight: '85vh',
          background: 'var(--bg-panel)',
          border: '1px solid var(--border)',
          boxShadow: '0 24px 80px rgba(0,0,0,0.5)',
        }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between px-5 py-4 shrink-0"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          <div className="flex items-center gap-3">
            <span className="text-sm font-bold uppercase tracking-widest" style={{ color: 'var(--text-primary)' }}>
              Economic Calendar
            </span>
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
              USD + Major High-Impact Events
            </span>
          </div>

          {/* Impact filter */}
          <div className="flex items-center gap-2">
            <div className="flex items-center rounded-lg p-0.5 gap-0.5" style={{ background: 'var(--bg-base)' }}>
              {(['all', 'High', 'Medium'] as const).map(f => (
                <button
                  key={f}
                  onClick={() => setImpactFilter(f)}
                  className="rounded-md px-3 py-1 text-xs font-semibold uppercase tracking-wider transition-colors"
                  style={impactFilter === f
                    ? { background: 'var(--accent-blue)', color: '#fff' }
                    : { background: 'transparent', color: 'var(--text-muted)' }
                  }
                >
                  {f === 'all' ? 'All' : f}
                </button>
              ))}
            </div>
            <button
              onClick={onClose}
              className="rounded-lg p-1.5 transition-colors ml-1"
              style={{ color: 'var(--text-muted)', background: 'transparent' }}
              onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-row)'; e.currentTarget.style.color = 'var(--text-primary)' }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-muted)' }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4" style={{ gap: 20 }}>
          {loading && (
            <div className="flex items-center justify-center py-16">
              <span className="text-sm" style={{ color: 'var(--text-muted)' }}>Loading calendar…</span>
            </div>
          )}
          {error && (
            <div className="flex items-center justify-center py-16">
              <span className="text-sm" style={{ color: '#f87171' }}>{error}</span>
            </div>
          )}
          {!loading && !error && sortedDays.length === 0 && (
            <div className="flex items-center justify-center py-16">
              <span className="text-sm" style={{ color: 'var(--text-muted)' }}>No events found</span>
            </div>
          )}

          {!loading && sortedDays.map(day => (
            <div key={day} className="mb-6">
              {/* Day header */}
              <div
                className="text-xs font-bold uppercase tracking-widest mb-3 pb-2"
                style={{ color: 'var(--text-dim)', borderBottom: '1px solid var(--border)' }}
              >
                {formatDay(days[day][0].date)}
              </div>

              {/* Events */}
              <div className="flex flex-col gap-2">
                {days[day].map((ev, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-3 rounded-lg px-3 py-2.5"
                    style={{ background: 'var(--bg-row)' }}
                  >
                    {/* Impact dot */}
                    <span
                      className="shrink-0 rounded-full"
                      style={{
                        width: 8, height: 8,
                        background: IMPACT_COLOR[ev.impact] ?? '#64748b',
                        boxShadow: ev.impact === 'High' ? `0 0 6px ${IMPACT_COLOR.High}` : 'none',
                      }}
                    />

                    {/* Time */}
                    <span
                      className="text-xs tabular-nums shrink-0 w-16"
                      style={{ color: 'var(--text-muted)' }}
                    >
                      {formatTime(ev.date)}
                    </span>

                    {/* Flag + Country */}
                    <span className="text-xs shrink-0 w-12" style={{ color: 'var(--text-dim)' }}>
                      {FLAG[ev.country] ?? ''} {ev.country}
                    </span>

                    {/* Title */}
                    <span
                      className="text-xs flex-1 font-medium"
                      style={{ color: ev.impact === 'High' ? 'var(--text-primary)' : 'var(--text-dim)' }}
                    >
                      {ev.title}
                    </span>

                    {/* Forecast / Previous */}
                    {(ev.forecast || ev.previous) && (
                      <span className="text-xs tabular-nums shrink-0 flex gap-3" style={{ color: 'var(--text-muted)' }}>
                        {ev.forecast && (
                          <span>F: <span style={{ color: 'var(--text-dim)' }}>{ev.forecast}</span></span>
                        )}
                        {ev.previous && (
                          <span>P: <span style={{ color: 'var(--text-dim)' }}>{ev.previous}</span></span>
                        )}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div
          className="flex items-center justify-between px-5 py-2.5 shrink-0 text-xs"
          style={{ borderTop: '1px solid var(--border)', color: 'var(--text-muted)' }}
        >
          <span>Source: ForexFactory · {filtered.length} events</span>
          <span>
            <span style={{ color: IMPACT_COLOR.High }}>● High</span>
            <span style={{ margin: '0 8px' }}>·</span>
            <span style={{ color: IMPACT_COLOR.Medium }}>● Medium</span>
            <span style={{ margin: '0 8px' }}>·</span>
            <span style={{ color: IMPACT_COLOR.Low }}>● Low</span>
          </span>
        </div>
      </div>
    </div>
  )
}
