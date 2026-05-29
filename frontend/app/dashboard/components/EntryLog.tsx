'use client'

import { useEffect, useState, useCallback } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'

interface EntryRow {
  id: number
  fired_at: string
  symbol: string
  model: 'AGG' | 'CON' | 'WIDE' | 'CR'
  side: 'LONG' | 'SHORT'
  entry: number
  stop: number
  t1: number
  target: number
  last_price: number
  daily_bias: string | null
  hour_et: number | null
}

const MODEL_STYLE: Record<string, { bg: string; color: string }> = {
  AGG:  { bg: 'rgba(59,130,246,0.15)',  color: '#60a5fa' },
  CON:  { bg: 'rgba(34,197,94,0.15)',   color: '#4ade80' },
  WIDE: { bg: 'rgba(251,191,36,0.15)',  color: '#fbbf24' },
  CR:   { bg: 'rgba(168,85,247,0.15)',  color: '#c084fc' },
}

function ModelBadge({ model }: { model: string }) {
  const s = MODEL_STYLE[model] ?? { bg: 'rgba(100,100,100,0.2)', color: '#aaa' }
  return (
    <span style={{
      background: s.bg, color: s.color,
      border: `1px solid ${s.color}40`,
      borderRadius: 4, padding: '1px 6px', fontSize: 11, fontWeight: 700,
    }}>
      {model}
    </span>
  )
}

function SideBadge({ side }: { side: string }) {
  const isLong = side === 'LONG'
  return (
    <span style={{ color: isLong ? '#4ade80' : '#f87171', fontWeight: 700, fontSize: 12 }}>
      {isLong ? '▲ LONG' : '▼ SHORT'}
    </span>
  )
}

function fmt(n: number | null | undefined) {
  if (n == null) return '—'
  if (n < 10)   return n.toFixed(3)   // /NG and other sub-10 futures
  return n.toFixed(2)
}

function fmtTime(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false, timeZone: 'America/New_York',
    })
  } catch { return iso }
}

function fmtDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', timeZone: 'America/New_York',
    })
  } catch { return '' }
}

interface Props {
  visible: boolean
  onClose: () => void
}

export default function EntryLog({ visible, onClose }: Props) {
  const [entries, setEntries] = useState<EntryRow[]>([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState<'ALL' | 'AGG' | 'CON' | 'WIDE' | 'CR'>('ALL')

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/entry-log?limit=200`)
      const data = await res.json()
      setEntries(data.entries ?? [])
    } catch { /* silent */ }
  }, [])

  const purge = useCallback(async () => {
    if (!confirm('Clear all entry log entries?')) return
    try {
      await fetch(`${API}/api/entry-log`, { method: 'DELETE' })
      setEntries([])
    } catch { /* silent */ }
  }, [])

  useEffect(() => {
    if (!visible) return
    setLoading(true)
    load().finally(() => setLoading(false))
    const t = setInterval(load, 30_000)
    return () => clearInterval(t)
  }, [visible, load])

  if (!visible) return null

  const filtered = filter === 'ALL' ? entries : entries.filter(e => e.model === filter)

  // Group by date
  const grouped: { date: string; rows: EntryRow[] }[] = []
  for (const row of filtered) {
    const d = fmtDate(row.fired_at)
    const last = grouped[grouped.length - 1]
    if (last && last.date === d) last.rows.push(row)
    else grouped.push({ date: d, rows: [row] })
  }

  const models: Array<'ALL' | 'AGG' | 'CON' | 'WIDE' | 'CR'> = ['ALL', 'AGG', 'CON', 'WIDE', 'CR']

  return (
    /* Fixed overlay panel — right side, full height */
    <div style={{
      position: 'fixed',
      top: 0, right: 0,
      width: 600,
      height: '100vh',
      background: '#0d0f14',
      borderLeft: '1px solid #2a2d36',
      display: 'flex',
      flexDirection: 'column',
      zIndex: 200,
      boxShadow: '-8px 0 32px rgba(0,0,0,0.5)',
    }}>
      {/* Header */}
      <div style={{
        padding: '16px 20px',
        borderBottom: '1px solid #2a2d36',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        flexShrink: 0,
      }}>
        <span style={{ color: '#e2e8f0', fontWeight: 700, fontSize: 15 }}>Entry Log</span>
        <span style={{ color: '#64748b', fontSize: 12 }}>
          {entries.length} entries
        </span>
        {loading && <span style={{ color: '#475569', fontSize: 11 }}>loading…</span>}

        {/* Model filter pills */}
        <div style={{ display: 'flex', gap: 5, marginLeft: 8 }}>
          {models.map(m => {
            const s = m === 'ALL' ? null : MODEL_STYLE[m]
            const active = filter === m
            return (
              <button key={m} onClick={() => setFilter(m)} style={{
                background: active ? (s ? s.bg : 'rgba(255,255,255,0.08)') : 'transparent',
                color: active ? (s ? s.color : '#e2e8f0') : '#64748b',
                border: `1px solid ${active ? (s ? s.color + '50' : '#555') : '#2a2d36'}`,
                borderRadius: 5, padding: '2px 9px', fontSize: 11,
                fontWeight: 600, cursor: 'pointer',
              }}>
                {m}
              </button>
            )
          })}
        </div>

        {/* Clear + Close buttons */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
          {entries.length > 0 && (
            <button onClick={purge} style={{
              background: 'rgba(248,113,113,0.08)',
              border: '1px solid rgba(248,113,113,0.2)',
              color: '#f87171', cursor: 'pointer',
              borderRadius: 5, padding: '2px 9px', fontSize: 11, fontWeight: 600,
            }} title="Purge all entries">
              Clear
            </button>
          )}
          <button onClick={onClose} style={{
            background: 'transparent',
            border: 'none', color: '#64748b', cursor: 'pointer',
            fontSize: 20, lineHeight: 1, padding: '0 4px',
          }} title="Close log">✕</button>
        </div>
      </div>

      {/* Table */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
        {filtered.length === 0 ? (
          <div style={{
            color: '#475569', fontSize: 13, textAlign: 'center',
            padding: '40px 20px',
          }}>
            {entries.length === 0
              ? 'No entries yet — waiting for first ENTRY signal'
              : 'No entries for this model filter'}
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead style={{ position: 'sticky', top: 0, background: '#0d0f14', zIndex: 1 }}>
              <tr style={{ color: '#64748b', borderBottom: '1px solid #1e2130' }}>
                <th style={th}>Time ET</th>
                <th style={th}>Symbol</th>
                <th style={th}>Model</th>
                <th style={th}>Side</th>
                <th style={{ ...th, textAlign: 'right' }}>Entry</th>
                <th style={{ ...th, textAlign: 'right' }}>Stop</th>
                <th style={{ ...th, textAlign: 'right' }}>T1</th>
                <th style={{ ...th, textAlign: 'right' }}>Target</th>
              </tr>
            </thead>
            <tbody>
              {grouped.map(({ date, rows }) => (
                <>
                  <tr key={date + '_hdr'}>
                    <td colSpan={8} style={{
                      color: '#475569', fontSize: 11, fontWeight: 600,
                      padding: '10px 10px 4px', letterSpacing: '0.05em',
                      borderBottom: '1px solid #1e2130',
                    }}>
                      {date}
                    </td>
                  </tr>
                  {rows.map(e => (
                    <tr key={e.id}
                      style={{ borderBottom: '1px solid #141720' }}
                      onMouseEnter={ev => (ev.currentTarget.style.background = '#13151e')}
                      onMouseLeave={ev => (ev.currentTarget.style.background = 'transparent')}
                    >
                      <td style={td}>{fmtTime(e.fired_at)}</td>
                      <td style={{ ...td, fontWeight: 700, color: '#e2e8f0' }}>{e.symbol}</td>
                      <td style={td}><ModelBadge model={e.model} /></td>
                      <td style={td}><SideBadge side={e.side} /></td>
                      <td style={{ ...td, textAlign: 'right', color: '#e2e8f0' }}>{fmt(e.entry)}</td>
                      <td style={{ ...td, textAlign: 'right', color: '#f87171' }}>{fmt(e.stop)}</td>
                      <td style={{ ...td, textAlign: 'right', color: '#94a3b8' }}>{fmt(e.t1)}</td>
                      <td style={{ ...td, textAlign: 'right', color: '#4ade80' }}>{fmt(e.target)}</td>
                    </tr>
                  ))}
                </>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

const th: React.CSSProperties = {
  textAlign: 'left', padding: '8px 10px',
  fontWeight: 600, fontSize: 11, whiteSpace: 'nowrap',
}

const td: React.CSSProperties = {
  padding: '6px 10px', color: '#94a3b8', whiteSpace: 'nowrap',
}
