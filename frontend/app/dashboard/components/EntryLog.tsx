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
    <span style={{
      color: isLong ? '#4ade80' : '#f87171',
      fontWeight: 700, fontSize: 12,
    }}>
      {isLong ? '▲ LONG' : '▼ SHORT'}
    </span>
  )
}

function fmt(n: number | null | undefined, dec = 2) {
  if (n == null) return '—'
  return n.toFixed(dec)
}

function fmtTime(iso: string) {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hour12: false, timeZone: 'America/New_York',
    })
  } catch { return iso }
}

function fmtDate(iso: string) {
  try {
    const d = new Date(iso)
    return d.toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', timeZone: 'America/New_York',
    })
  } catch { return '' }
}

interface Props {
  visible: boolean
}

export default function EntryLog({ visible }: Props) {
  const [entries, setEntries] = useState<EntryRow[]>([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState<'ALL' | 'AGG' | 'CON' | 'WIDE' | 'CR'>('ALL')
  const [lastCount, setLastCount] = useState(0)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/entry-log?limit=200`)
      const data = await res.json()
      const rows: EntryRow[] = data.entries ?? []
      setEntries(rows)
      // Flash indicator if new entries arrived
      if (rows.length > lastCount && lastCount > 0) {
        setLastCount(rows.length)
      } else {
        setLastCount(rows.length)
      }
    } catch { /* silent */ }
  }, [lastCount])

  useEffect(() => {
    if (!visible) return
    setLoading(true)
    load().finally(() => setLoading(false))
    const t = setInterval(load, 30_000)
    return () => clearInterval(t)
  }, [visible, load])

  if (!visible) return null

  const filtered = filter === 'ALL' ? entries : entries.filter(e => e.model === filter)

  // Group by date for visual separation
  const grouped: { date: string; rows: EntryRow[] }[] = []
  for (const row of filtered) {
    const d = fmtDate(row.fired_at)
    const last = grouped[grouped.length - 1]
    if (last && last.date === d) last.rows.push(row)
    else grouped.push({ date: d, rows: [row] })
  }

  const models: Array<'ALL' | 'AGG' | 'CON' | 'WIDE' | 'CR'> = ['ALL', 'AGG', 'CON', 'WIDE', 'CR']

  return (
    <div style={{
      background: '#111318', border: '1px solid #2a2d36',
      borderRadius: 10, padding: '16px 20px', marginTop: 16,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 14 }}>
        <span style={{ color: '#e2e8f0', fontWeight: 700, fontSize: 14 }}>
          📋 Entry Log
        </span>
        <span style={{ color: '#64748b', fontSize: 12 }}>
          {entries.length} entries total
        </span>
        {loading && <span style={{ color: '#64748b', fontSize: 11 }}>loading…</span>}

        {/* Model filter pills */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          {models.map(m => {
            const s = m === 'ALL' ? null : MODEL_STYLE[m]
            const active = filter === m
            return (
              <button
                key={m}
                onClick={() => setFilter(m)}
                style={{
                  background: active ? (s ? s.bg : 'rgba(255,255,255,0.08)') : 'transparent',
                  color: active ? (s ? s.color : '#e2e8f0') : '#64748b',
                  border: `1px solid ${active ? (s ? s.color + '60' : '#555') : '#2a2d36'}`,
                  borderRadius: 5, padding: '2px 10px', fontSize: 11,
                  fontWeight: 600, cursor: 'pointer',
                }}
              >
                {m}
              </button>
            )
          })}
        </div>
      </div>

      {/* Table */}
      {filtered.length === 0 ? (
        <div style={{ color: '#475569', fontSize: 13, textAlign: 'center', padding: '24px 0' }}>
          No entries yet — waiting for first ENTRY signal
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ color: '#64748b', borderBottom: '1px solid #1e2130' }}>
                <th style={th}>Time (ET)</th>
                <th style={th}>Symbol</th>
                <th style={th}>Model</th>
                <th style={th}>Side</th>
                <th style={{ ...th, textAlign: 'right' }}>Entry</th>
                <th style={{ ...th, textAlign: 'right' }}>Stop</th>
                <th style={{ ...th, textAlign: 'right' }}>T1</th>
                <th style={{ ...th, textAlign: 'right' }}>Target</th>
                <th style={{ ...th, textAlign: 'right' }}>Last</th>
              </tr>
            </thead>
            <tbody>
              {grouped.map(({ date, rows }) => (
                <>
                  <tr key={date + '_hdr'}>
                    <td colSpan={9} style={{
                      color: '#475569', fontSize: 11, fontWeight: 600,
                      padding: '8px 8px 4px', letterSpacing: '0.05em',
                      borderBottom: '1px solid #1e2130',
                    }}>
                      {date}
                    </td>
                  </tr>
                  {rows.map(e => (
                    <tr
                      key={e.id}
                      style={{ borderBottom: '1px solid #1a1d27' }}
                      onMouseEnter={ev => (ev.currentTarget.style.background = '#1a1d27')}
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
                      <td style={{ ...td, textAlign: 'right', color: '#94a3b8' }}>{fmt(e.last_price)}</td>
                    </tr>
                  ))}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

const th: React.CSSProperties = {
  textAlign: 'left', padding: '6px 8px',
  fontWeight: 600, fontSize: 11, whiteSpace: 'nowrap',
}

const td: React.CSSProperties = {
  padding: '5px 8px', color: '#94a3b8', whiteSpace: 'nowrap',
}
