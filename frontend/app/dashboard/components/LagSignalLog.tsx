'use client'

import { useEffect, useState, useCallback } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

interface LogRow {
  id: number
  ticker: string
  signal_date: string
  signal: 'BUY' | 'SELL'
  entry: number
  target: number
  stop_price: number
  outcome: 'OPEN' | 'HIT_TARGET' | 'HIT_STOP'
  outcome_date: string | null
  outcome_price: number | null
  pnl_pct: number | null
  score: number | null
  d_sq_state: string | null
  d_mo_state: string | null
  d_just_fired: boolean | null
  w_sq_state: string | null
  w_mo_state: string | null
}

type OutcomeFilter = 'ALL' | 'OPEN' | 'HIT_TARGET' | 'HIT_STOP'
type SigFilter     = 'ALL' | 'BUY'  | 'SELL'

const TH: React.CSSProperties = {
  padding: '6px 10px', textAlign: 'left', fontSize: 10, fontWeight: 700,
  letterSpacing: '0.07em', color: 'var(--text-dim)',
  borderBottom: '1px solid var(--border)',
  position: 'sticky', top: 0, background: 'var(--bg-panel)', zIndex: 1,
  whiteSpace: 'nowrap',
}
const TD: React.CSSProperties = {
  padding: '6px 10px', fontSize: 11,
  borderBottom: '1px solid rgba(255,255,255,0.03)',
  whiteSpace: 'nowrap',
}

function OutcomeBadge({ outcome, pnl }: { outcome: LogRow['outcome']; pnl: number | null }) {
  const cfg = {
    OPEN:       { label: 'OPEN',    bg: 'rgba(148,163,184,0.1)', color: '#94a3b8', border: 'rgba(148,163,184,0.25)' },
    HIT_TARGET: { label: '✓ TARGET', bg: 'rgba(74,222,128,0.12)', color: '#4ade80', border: 'rgba(74,222,128,0.3)'  },
    HIT_STOP:   { label: '✗ STOP',   bg: 'rgba(248,113,113,0.12)', color: '#f87171', border: 'rgba(248,113,113,0.3)' },
  }[outcome]
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{
        display: 'inline-block', fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
        padding: '2px 7px', borderRadius: 5,
        background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.border}`,
      }}>
        {cfg.label}
      </span>
      {pnl != null && outcome !== 'OPEN' && (
        <span style={{
          fontSize: 10, fontWeight: 700, fontFamily: 'monospace',
          color: pnl >= 0 ? '#4ade80' : '#f87171',
        }}>
          {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}%
        </span>
      )}
    </div>
  )
}

function SegBtn<T extends string>({
  value, options, labels, onChange,
}: {
  value: T; options: T[]; labels?: Record<string, string>; onChange: (v: T) => void
}) {
  return (
    <div style={{ display: 'flex', gap: 3 }}>
      {options.map(opt => (
        <button key={opt} onClick={() => onChange(opt)} style={{
          fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
          padding: '3px 9px', borderRadius: 5, cursor: 'pointer',
          background: opt === value ? 'var(--accent-blue)' : 'rgba(255,255,255,0.04)',
          border:     opt === value ? '1px solid var(--accent-blue)' : '1px solid var(--border)',
          color:      opt === value ? '#fff' : 'var(--text-dim)',
          transition: 'all 0.12s',
        }}>
          {labels ? labels[opt] : opt}
        </button>
      ))}
    </div>
  )
}

const SQ_COLOR: Record<string, string> = {
  FIRED:     '#facc15',
  EXTRA_IN:  '#f97316',
  ORIG_IN:   '#fb923c',
  PRE_IN:    '#fbbf24',
  EXTRA_OUT: '#818cf8',
  ORIG_OUT:  '#a78bfa',
  PRE_OUT:   '#c4b5fd',
}
const MO_COLOR: Record<string, string> = {
  POS_UP: '#4ade80',
  POS_DN: '#86efac',
  NEG_DN: '#f87171',
  NEG_UP: '#fca5a5',
}

function SqBadge({ state, fired }: { state: string | null; fired?: boolean | null }) {
  if (!state) return <span style={{ color: 'var(--text-dim)' }}>—</span>
  const color = SQ_COLOR[state] ?? '#94a3b8'
  const label = fired ? '★ ' + state : state.replace('_', ' ')
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, letterSpacing: '0.04em',
      padding: '2px 6px', borderRadius: 4,
      background: `${color}18`, color, border: `1px solid ${color}44`,
      whiteSpace: 'nowrap',
    }}>{label}</span>
  )
}

function MoBadge({ state }: { state: string | null }) {
  if (!state) return <span style={{ color: 'var(--text-dim)' }}>—</span>
  const color = MO_COLOR[state] ?? '#94a3b8'
  return (
    <span style={{
      fontSize: 9, fontWeight: 700, letterSpacing: '0.04em',
      padding: '2px 6px', borderRadius: 4,
      background: `${color}18`, color, border: `1px solid ${color}44`,
    }}>{state.replace('_', ' ')}</span>
  )
}

function fmt(n: number | null, decimals = 2) {
  return n == null ? '—' : n.toFixed(decimals)
}

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' })
}

export default function LagSignalLog() {
  const [rows, setRows]         = useState<LogRow[]>([])
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState<string | null>(null)
  const [outcome, setOutcome]   = useState<OutcomeFilter>('ALL')
  const [sig, setSig]           = useState<SigFilter>('ALL')
  const [search, setSearch]     = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const r = await fetch(`${API_URL}/api/lag-log`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setRows(d.rows ?? [])
    } catch (e) {
      setError('Failed to load signal log')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const filtered = rows.filter(r => {
    if (outcome !== 'ALL' && r.outcome !== outcome) return false
    if (sig     !== 'ALL' && r.signal  !== sig)     return false
    if (search && !r.ticker.toUpperCase().includes(search.toUpperCase())) return false
    return true
  })

  // Summary stats (closed trades only)
  const closed   = filtered.filter(r => r.outcome !== 'OPEN')
  const wins     = closed.filter(r => r.outcome === 'HIT_TARGET')
  const winRate  = closed.length ? Math.round(wins.length / closed.length * 100) : null
  const avgPnl   = closed.length
    ? closed.reduce((s, r) => s + (r.pnl_pct ?? 0), 0) / closed.length
    : null

  return (
    <div style={{
      height: '100vh', display: 'flex', flexDirection: 'column',
      background: 'var(--bg-base)', color: 'var(--text-primary)',
    }}>

      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
        borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)',
        flexShrink: 0, flexWrap: 'wrap',
      }}>
        <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: '0.05em' }}>
          LAG SIG LOG
        </span>
        <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>
          {rows.length} entries
        </span>

        {/* Stats */}
        {closed.length > 0 && (
          <div style={{ display: 'flex', gap: 16, fontSize: 10, color: 'var(--text-dim)' }}>
            <span>
              Win rate:{' '}
              <span style={{ fontWeight: 700, color: winRate! >= 50 ? '#4ade80' : '#f87171' }}>
                {winRate}%
              </span>
              {' '}({wins.length}/{closed.length})
            </span>
            {avgPnl != null && (
              <span>
                Avg P&amp;L:{' '}
                <span style={{ fontWeight: 700, color: avgPnl >= 0 ? '#4ade80' : '#f87171' }}>
                  {avgPnl >= 0 ? '+' : ''}{avgPnl.toFixed(2)}%
                </span>
              </span>
            )}
          </div>
        )}

        <div style={{ flex: 1 }} />

        {/* Search */}
        <div style={{ position: 'relative' }}>
          <input
            type="text"
            placeholder="Ticker…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              fontSize: 11, fontFamily: 'monospace', fontWeight: 700,
              padding: '4px 24px 4px 8px', borderRadius: 5, width: 100,
              background: search ? 'rgba(59,130,246,0.1)' : 'rgba(255,255,255,0.04)',
              border: search ? '1px solid var(--accent-blue)' : '1px solid var(--border)',
              color: 'var(--text-primary)', outline: 'none',
            }}
          />
          {search && (
            <button onClick={() => setSearch('')} style={{
              position: 'absolute', right: 5, top: '50%', transform: 'translateY(-50%)',
              background: 'none', border: 'none', cursor: 'pointer',
              color: 'var(--text-dim)', fontSize: 11, padding: 0,
            }}>✕</button>
          )}
        </div>

        <SegBtn<SigFilter>
          value={sig}
          options={['ALL', 'BUY', 'SELL']}
          onChange={setSig}
        />
        <SegBtn<OutcomeFilter>
          value={outcome}
          options={['ALL', 'OPEN', 'HIT_TARGET', 'HIT_STOP']}
          labels={{ ALL: 'All', OPEN: 'Open', HIT_TARGET: '✓ Target', HIT_STOP: '✗ Stop' }}
          onChange={setOutcome}
        />

        <button onClick={load} style={{
          fontSize: 10, fontWeight: 700, padding: '3px 9px', borderRadius: 5,
          background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)',
          color: 'var(--text-dim)', cursor: 'pointer',
        }}>↻</button>
      </div>

      {/* Error */}
      {error && <div style={{ padding: 16, color: '#f87171', fontSize: 12 }}>{error}</div>}

      {/* Table */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr>
              <th style={TH}>TICKER</th>
              <th style={TH}>DATE</th>
              <th style={TH}>SIG</th>
              <th style={{ ...TH, textAlign: 'right' }}>SCORE</th>
              <th style={TH}>D SQ</th>
              <th style={TH}>D MO</th>
              <th style={TH}>W SQ</th>
              <th style={TH}>W MO</th>
              <th style={{ ...TH, textAlign: 'right' }}>ENTRY</th>
              <th style={{ ...TH, textAlign: 'right' }}>TARGET</th>
              <th style={{ ...TH, textAlign: 'right' }}>STOP</th>
              <th style={{ ...TH, textAlign: 'right' }}>DIST %</th>
              <th style={TH}>OUTCOME</th>
              <th style={TH}>OUT DATE</th>
              <th style={{ ...TH, textAlign: 'right' }}>EXIT</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(r => {
              const isBuy     = r.signal === 'BUY'
              const sigColor  = isBuy ? '#4ade80' : '#f87171'
              const targetDist = r.entry ? Math.abs((r.target - r.entry) / r.entry * 100) : null
              return (
                <tr key={r.id}
                  style={{ background: 'transparent' }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-row)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  {/* Ticker */}
                  <td style={{ ...TD, paddingLeft: 16 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <img
                        src={`https://img.logo.dev/ticker/${r.ticker}?token=pk_fZOnZkh3QrCkdBG6NS8ckQ&size=128&format=png&retina=true`}
                        alt=""
                        style={{ height: 24, width: 24, objectFit: 'contain', borderRadius: 4, flexShrink: 0 }}
                        onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                      />
                      <span style={{ fontFamily: 'monospace', fontWeight: 800, fontSize: 12 }}>
                        {r.ticker}
                      </span>
                    </div>
                  </td>

                  {/* Signal date */}
                  <td style={{ ...TD, color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                    {fmtDate(r.signal_date)}
                  </td>

                  {/* Signal badge */}
                  <td style={TD}>
                    <span style={{
                      fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
                      padding: '2px 7px', borderRadius: 4,
                      background: `${sigColor}22`, color: sigColor,
                      border: `1px solid ${sigColor}55`,
                    }}>
                      {isBuy ? '▲ ' : '▼ '}{r.signal}
                    </span>
                  </td>

                  {/* Score */}
                  <td style={{ ...TD, textAlign: 'right', fontFamily: 'monospace', fontWeight: 700 }}>
                    {r.score != null ? r.score.toFixed(1) : '—'}
                  </td>

                  {/* D SQ */}
                  <td style={TD}>
                    <SqBadge state={r.d_sq_state} fired={r.d_just_fired} />
                  </td>

                  {/* D MO */}
                  <td style={TD}>
                    <MoBadge state={r.d_mo_state} />
                  </td>

                  {/* W SQ */}
                  <td style={TD}>
                    <SqBadge state={r.w_sq_state} />
                  </td>

                  {/* W MO */}
                  <td style={TD}>
                    <MoBadge state={r.w_mo_state} />
                  </td>

                  {/* Entry */}
                  <td style={{ ...TD, textAlign: 'right', fontFamily: 'monospace' }}>
                    {fmt(r.entry)}
                  </td>

                  {/* Target */}
                  <td style={{ ...TD, textAlign: 'right', fontFamily: 'monospace', color: '#4ade80' }}>
                    {fmt(r.target)}
                  </td>

                  {/* Stop */}
                  <td style={{ ...TD, textAlign: 'right', fontFamily: 'monospace', color: '#f87171' }}>
                    {fmt(r.stop_price)}
                  </td>

                  {/* Target dist % */}
                  <td style={{ ...TD, textAlign: 'right', fontFamily: 'monospace', color: 'var(--text-muted)' }}>
                    {targetDist != null ? `${targetDist.toFixed(1)}%` : '—'}
                  </td>

                  {/* Outcome */}
                  <td style={TD}>
                    <OutcomeBadge outcome={r.outcome} pnl={r.pnl_pct} />
                  </td>

                  {/* Outcome date */}
                  <td style={{ ...TD, color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                    {fmtDate(r.outcome_date)}
                  </td>

                  {/* Exit price */}
                  <td style={{ ...TD, textAlign: 'right', fontFamily: 'monospace', color: 'var(--text-muted)' }}>
                    {fmt(r.outcome_price)}
                  </td>
                </tr>
              )
            })}

            {filtered.length === 0 && !loading && (
              <tr>
                <td colSpan={15} style={{ ...TD, textAlign: 'center', color: 'var(--text-dim)', padding: 40 }}>
                  {rows.length === 0 ? 'No signals logged yet — runs nightly after each scan.' : 'No entries match filters.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
