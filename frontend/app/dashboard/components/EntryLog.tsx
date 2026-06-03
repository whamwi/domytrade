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
  // Outcome tracking
  outcome: 'OPEN' | 'HIT_TARGET' | 'HIT_STOP' | 'EXPIRED'
  outcome_at: string | null
  pnl_pts: number | null
  price_1h: number | null
  price_4h: number | null
  price_eod: number | null
}

const MODEL_STYLE: Record<string, { bg: string; color: string }> = {
  AGG:  { bg: 'rgba(59,130,246,0.15)',  color: '#60a5fa' },
  CON:  { bg: 'rgba(34,197,94,0.15)',   color: '#4ade80' },
  WIDE: { bg: 'rgba(251,191,36,0.15)',  color: '#fbbf24' },
  CR:   { bg: 'rgba(168,85,247,0.15)',  color: '#c084fc' },
}

const OUTCOME_STYLE: Record<string, { bg: string; color: string; label: string }> = {
  OPEN:       { bg: 'rgba(100,116,139,0.15)', color: '#94a3b8', label: 'OPEN'   },
  HIT_TARGET: { bg: 'rgba(34,197,94,0.15)',   color: '#4ade80', label: '✓ TARGET' },
  HIT_STOP:   { bg: 'rgba(248,113,113,0.15)', color: '#f87171', label: '✕ STOP'  },
  EXPIRED:    { bg: 'rgba(251,191,36,0.12)',  color: '#fbbf24', label: 'EXPIRED' },
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
      {isLong ? '▲' : '▼'} {side}
    </span>
  )
}

function OutcomeBadge({ outcome }: { outcome: string }) {
  const s = OUTCOME_STYLE[outcome] ?? OUTCOME_STYLE.OPEN
  return (
    <span style={{
      background: s.bg, color: s.color,
      border: `1px solid ${s.color}30`,
      borderRadius: 4, padding: '1px 5px', fontSize: 10, fontWeight: 700,
      whiteSpace: 'nowrap',
    }}>
      {s.label}
    </span>
  )
}

function fmt(n: number | null | undefined) {
  if (n == null) return '—'
  if (Math.abs(n) < 10) return n.toFixed(3)
  return n.toFixed(2)
}

function fmtPnl(n: number | null | undefined) {
  if (n == null) return '—'
  const sign = n >= 0 ? '+' : ''
  const val  = Math.abs(n) < 10 ? Math.abs(n).toFixed(3) : Math.abs(n).toFixed(2)
  return `${sign}${n >= 0 ? '' : '-'}${val}`
}

function fmtTime(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit',
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

// ── Summary stats ─────────────────────────────────────────────────────────────
function computeStats(rows: EntryRow[]) {
  const graded = rows.filter(r => r.outcome === 'HIT_TARGET' || r.outcome === 'HIT_STOP')
  const wins   = graded.filter(r => r.outcome === 'HIT_TARGET')
  const losses = graded.filter(r => r.outcome === 'HIT_STOP')
  const open   = rows.filter(r => r.outcome === 'OPEN').length

  const winRate  = graded.length > 0 ? (wins.length / graded.length) * 100 : null
  const avgWin   = wins.length   > 0 ? wins.reduce((s, r)   => s + (r.pnl_pts ?? 0), 0) / wins.length   : null
  const avgLoss  = losses.length > 0 ? losses.reduce((s, r) => s + (r.pnl_pts ?? 0), 0) / losses.length : null
  const profFact = avgLoss && avgLoss < 0 && avgWin != null
    ? Math.abs(avgWin / avgLoss)
    : null

  return { graded: graded.length, wins: wins.length, losses: losses.length, open, winRate, avgWin, avgLoss, profFact }
}

interface Props {
  visible: boolean
  onClose: () => void
}

type ModelFilter   = 'ALL' | 'AGG' | 'CON' | 'WIDE' | 'CR'
type OutcomeFilter = 'ALL' | 'OPEN' | 'HIT_TARGET' | 'HIT_STOP' | 'EXPIRED'

export default function EntryLog({ visible, onClose }: Props) {
  const [entries,    setEntries]    = useState<EntryRow[]>([])
  const [loading,    setLoading]    = useState(false)
  const [modelF,     setModelF]     = useState<ModelFilter>('ALL')
  const [outcomeF,   setOutcomeF]   = useState<OutcomeFilter>('ALL')
  const [symSearch,  setSymSearch]  = useState('')

  const load = useCallback(async () => {
    try {
      const modelParam = modelF === 'ALL' ? '' : `&model=${modelF}`
      const res  = await fetch(`${API}/api/entry-log?limit=500${modelParam}`)
      const data = await res.json()
      setEntries(data.entries ?? [])
    } catch { /* silent */ }
  }, [modelF])

  const purge = useCallback(async () => {
    if (!confirm('Archive and clear all entry log entries?')) return
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

  // Client-side filters
  const sym      = symSearch.trim().toUpperCase()
  const filtered = entries
    .filter(e => !sym || e.symbol.toUpperCase().includes(sym))
    .filter(e => outcomeF === 'ALL' || e.outcome === outcomeF)

  // Summary stats over the full (unfiltered-by-outcome) symbol/model set
  const statsBase = entries.filter(e => !sym || e.symbol.toUpperCase().includes(sym))
  const stats     = computeStats(statsBase)

  // Group by date
  const grouped: { date: string; rows: EntryRow[] }[] = []
  for (const row of filtered) {
    const d    = fmtDate(row.fired_at)
    const last = grouped[grouped.length - 1]
    if (last && last.date === d) last.rows.push(row)
    else grouped.push({ date: d, rows: [row] })
  }

  const models:   ModelFilter[]   = ['ALL', 'AGG', 'CON', 'WIDE', 'CR']
  const outcomes: OutcomeFilter[] = ['ALL', 'OPEN', 'HIT_TARGET', 'HIT_STOP', 'EXPIRED']

  return (
    <div style={{
      position: 'fixed', top: 0, right: 0,
      width: 640, height: '100vh',
      background: '#0d0f14',
      borderLeft: '1px solid #2a2d36',
      display: 'flex', flexDirection: 'column',
      zIndex: 200, boxShadow: '-8px 0 32px rgba(0,0,0,0.5)',
    }}>

      {/* ── Header ── */}
      <div style={{
        padding: '12px 16px', borderBottom: '1px solid #2a2d36',
        display: 'flex', flexDirection: 'column', gap: 8, flexShrink: 0,
      }}>
        {/* Row 1: title + count + close */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: '#e2e8f0', fontWeight: 700, fontSize: 15 }}>Entry Log</span>
          <span style={{ color: '#64748b', fontSize: 12 }}>
            {filtered.length}{filtered.length !== entries.length ? `/${entries.length}` : ''} entries
          </span>
          {loading && <span style={{ color: '#475569', fontSize: 11 }}>loading…</span>}
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
            {entries.length > 0 && (
              <button onClick={purge} style={{
                background: 'rgba(248,113,113,0.08)',
                border: '1px solid rgba(248,113,113,0.2)',
                color: '#f87171', cursor: 'pointer',
                borderRadius: 5, padding: '2px 9px', fontSize: 11, fontWeight: 600,
              }}>Archive & Clear</button>
            )}
            <button onClick={onClose} style={{
              background: 'transparent', border: 'none',
              color: '#64748b', cursor: 'pointer', fontSize: 20, lineHeight: 1, padding: '0 4px',
            }}>✕</button>
          </div>
        </div>

        {/* Row 2: model pills */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>
          {models.map(m => {
            const s      = m === 'ALL' ? null : MODEL_STYLE[m]
            const active = modelF === m
            return (
              <button key={m} onClick={() => setModelF(m)} style={{
                background: active ? (s ? s.bg : 'rgba(255,255,255,0.08)') : 'transparent',
                color:      active ? (s ? s.color : '#e2e8f0') : '#64748b',
                border:     `1px solid ${active ? (s ? s.color + '50' : '#555') : '#2a2d36'}`,
                borderRadius: 5, padding: '2px 8px', fontSize: 11, fontWeight: 600, cursor: 'pointer',
              }}>{m}</button>
            )
          })}

          {/* Symbol search */}
          <div style={{ position: 'relative', marginLeft: 2 }}>
            <input
              type="text" placeholder="Symbol…" value={symSearch}
              onChange={e => setSymSearch(e.target.value)}
              style={{
                background: 'rgba(255,255,255,0.05)',
                border: `1px solid ${symSearch ? '#60a5fa50' : '#2a2d36'}`,
                borderRadius: 5, padding: '2px 24px 2px 8px',
                color: '#e2e8f0', fontSize: 11, width: 85, outline: 'none',
              }}
            />
            {symSearch && (
              <button onClick={() => setSymSearch('')} style={{
                position: 'absolute', right: 4, top: '50%', transform: 'translateY(-50%)',
                background: 'none', border: 'none', color: '#64748b',
                cursor: 'pointer', fontSize: 12, lineHeight: 1, padding: 0,
              }}>✕</button>
            )}
          </div>
        </div>

        {/* Row 3: outcome filter pills */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexWrap: 'wrap' }}>
          {outcomes.map(o => {
            const s      = o === 'ALL' ? null : OUTCOME_STYLE[o]
            const active = outcomeF === o
            return (
              <button key={o} onClick={() => setOutcomeF(o)} style={{
                background: active ? (s ? s.bg : 'rgba(255,255,255,0.06)') : 'transparent',
                color:      active ? (s ? s.color : '#e2e8f0') : '#475569',
                border:     `1px solid ${active ? (s ? s.color + '40' : '#555') : '#1e2130'}`,
                borderRadius: 5, padding: '1px 7px', fontSize: 10, fontWeight: 600, cursor: 'pointer',
              }}>
                {o === 'ALL' ? 'All Outcomes' : o === 'HIT_TARGET' ? '✓ TARGET' : o === 'HIT_STOP' ? '✕ STOP' : o}
              </button>
            )
          })}
        </div>

        {/* Row 4: stats summary — only show when there are graded trades */}
        {stats.graded > 0 && (
          <div style={{
            display: 'flex', gap: 14, alignItems: 'center',
            padding: '6px 10px', borderRadius: 6,
            background: 'rgba(255,255,255,0.03)', border: '1px solid #1e2130',
            flexWrap: 'wrap',
          }}>
            <StatChip label="Trades"  value={`${stats.graded}`}            color="#94a3b8" />
            <StatChip label="Open"    value={`${stats.open}`}              color="#64748b" />
            <StatChip
              label="Win Rate"
              value={stats.winRate != null ? `${stats.winRate.toFixed(0)}%` : '—'}
              color={stats.winRate != null ? (stats.winRate >= 50 ? '#4ade80' : '#f87171') : '#64748b'}
            />
            <StatChip
              label="Avg Win"
              value={stats.avgWin != null ? `+${stats.avgWin.toFixed(1)} pts` : '—'}
              color="#4ade80"
            />
            <StatChip
              label="Avg Loss"
              value={stats.avgLoss != null ? `${stats.avgLoss.toFixed(1)} pts` : '—'}
              color="#f87171"
            />
            {stats.profFact != null && (
              <StatChip
                label="Prof. Factor"
                value={stats.profFact.toFixed(2)}
                color={stats.profFact >= 1 ? '#4ade80' : '#f87171'}
              />
            )}
          </div>
        )}
      </div>

      {/* ── Table ── */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
        {filtered.length === 0 ? (
          <div style={{ color: '#475569', fontSize: 13, textAlign: 'center', padding: '40px 20px' }}>
            {entries.length === 0
              ? 'No entries yet — waiting for first ENTRY signal'
              : 'No entries match current filters'}
          </div>
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead style={{ position: 'sticky', top: 0, background: '#0d0f14', zIndex: 1 }}>
              <tr style={{ color: '#64748b', borderBottom: '1px solid #1e2130' }}>
                <th style={th}>Time</th>
                <th style={th}>Symbol</th>
                <th style={th}>Mdl</th>
                <th style={th}>Side</th>
                <th style={{ ...th, textAlign: 'right' }}>Entry</th>
                <th style={{ ...th, textAlign: 'right' }}>Stop</th>
                <th style={{ ...th, textAlign: 'right' }}>Target</th>
                <th style={th}>Outcome</th>
                <th style={{ ...th, textAlign: 'right' }}>P&L pts</th>
              </tr>
            </thead>
            <tbody>
              {grouped.map(({ date, rows }) => (
                <>
                  <tr key={date + '_hdr'}>
                    <td colSpan={9} style={{
                      color: '#475569', fontSize: 11, fontWeight: 600,
                      padding: '10px 10px 4px', letterSpacing: '0.05em',
                      borderBottom: '1px solid #1e2130',
                    }}>
                      {date}
                    </td>
                  </tr>
                  {rows.map(e => {
                    const pnlColor = e.pnl_pts == null ? '#64748b'
                      : e.pnl_pts > 0 ? '#4ade80' : '#f87171'
                    return (
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
                        <td style={{ ...td, textAlign: 'right', color: '#4ade80' }}>{fmt(e.target)}</td>
                        <td style={td}><OutcomeBadge outcome={e.outcome ?? 'OPEN'} /></td>
                        <td style={{ ...td, textAlign: 'right', color: pnlColor, fontWeight: e.pnl_pts != null ? 700 : 400 }}>
                          {fmtPnl(e.pnl_pts)}
                        </td>
                      </tr>
                    )
                  })}
                </>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function StatChip({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
      <span style={{ color: '#475569', fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</span>
      <span style={{ color, fontSize: 13, fontWeight: 700 }}>{value}</span>
    </span>
  )
}

const th: React.CSSProperties = {
  textAlign: 'left', padding: '7px 8px',
  fontWeight: 600, fontSize: 11, whiteSpace: 'nowrap',
}

const td: React.CSSProperties = {
  padding: '5px 8px', color: '#94a3b8', whiteSpace: 'nowrap',
}
