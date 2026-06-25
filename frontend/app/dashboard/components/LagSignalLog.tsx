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
  d_bars_in_sq: number | null
  d_bars_fired: number | null
  sma50: number | null
  ema8: number | null
  ema21: number | null
  moxie_w: number | null
}

type OutcomeFilter = 'ALL' | 'OPEN' | 'HIT_TARGET' | 'HIT_STOP'
type SigFilter     = 'ALL' | 'BUY'  | 'SELL'
type ScoreFilter   = 'ALL' | '5' | '4+' | '3+'

// ── Shared scanner colours ─────────────────────────────────────────────────────
const SQ_COLOR: Record<string, string> = {
  EXTRA_IN:  '#eab308', EXTRA_OUT: '#eab308',
  ORIG_IN:   '#dc2626', ORIG_OUT:  '#dc2626',
  PRE_IN:    '#ec4899', PRE_OUT:   '#eab308',
  FIRED:     '#16a34a',
}
const MO_COLOR: Record<string, string> = {
  POS_UP: '#22d3ee', POS_DN: '#3b82f6',
  NEG_DN: '#dc2626', NEG_UP: '#fbbf24',
}

// ── Reused scanner sub-components ─────────────────────────────────────────────
function Check({ ok }: { ok: boolean }) {
  return (
    <span style={{ color: ok ? '#4ade80' : '#ef4444', fontWeight: 700, fontSize: 12 }}>
      {ok ? '✓' : '✗'}
    </span>
  )
}

function Triangle({ above }: { above: boolean }) {
  return (
    <span style={{ color: above ? '#4ade80' : '#ef4444', fontSize: 13, lineHeight: 1 }}>
      {above ? '▲' : '▼'}
    </span>
  )
}

function SqCell({ state, moState, bars, fired, justFired }: {
  state: string | null
  moState?: string | null
  bars?: number | null
  fired?: number | null
  justFired?: boolean | null
}) {
  if (!state) return <span style={{ color: 'var(--text-dim)', fontSize: 10 }}>—</span>
  const dotColor  = SQ_COLOR[state] ?? '#64748b'
  const moColor   = moState ? (MO_COLOR[moState] ?? '#64748b') : null
  const isUp      = moState === 'POS_UP' || moState === 'NEG_UP'
  const isFired   = state === 'FIRED'
  const recentFire = justFired || fired === 2
  const showFlash  = isFired && moState === 'POS_UP' && recentFire
  const barLabel   = showFlash ? '⚡' : isFired && fired != null ? `+${fired}` : (bars ?? '')
  return (
    <div style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <span
          className={justFired ? 'sqz-first-fire' : undefined}
          style={{
            width: 12, height: 12, borderRadius: '50%', display: 'inline-block',
            background: dotColor,
            boxShadow: isFired ? `0 0 ${showFlash ? 8 : 5}px ${dotColor}${showFlash ? 'cc' : '88'}` : 'none',
          }}
        />
        {moColor && (
          <span style={{ color: moColor, fontSize: 10, fontWeight: 900, lineHeight: 1, fontFamily: 'monospace' }}>
            {isUp ? '▲' : '▼'}
          </span>
        )}
      </span>
      {barLabel !== '' && (
        <span style={{
          fontSize: showFlash ? 11 : 10, fontWeight: 700,
          fontFamily: showFlash ? 'inherit' : 'monospace',
          color: showFlash ? '#4ade80' : isFired ? '#86efac' : 'var(--text-muted)',
          lineHeight: 1,
        }}>
          {barLabel}
        </span>
      )}
    </div>
  )
}

function ScorePill({ score, direction }: { score: number; direction: 'LONG' | 'SHORT' }) {
  const isHot  = score >= 5
  const isWarm = score === 4
  const fill   = direction === 'LONG' ? '#4ade80' : '#f87171'
  const bg     = isHot  ? (direction === 'LONG' ? '#14532d' : '#7f1d1d')
               : isWarm ? 'rgba(255,255,255,0.08)'
               :           'rgba(255,255,255,0.04)'
  const color  = isHot ? fill : isWarm ? fill : 'var(--text-dim)'
  const border = isHot ? `1px solid ${fill}55` : '1px solid var(--border)'
  return (
    <span style={{
      display: 'inline-block', fontFamily: 'monospace', fontWeight: 800,
      fontSize: 11, padding: '3px 9px', borderRadius: 6,
      background: bg, color, border, letterSpacing: '0.04em',
    }}>
      {score}/5
    </span>
  )
}

// ── Outcome badge ──────────────────────────────────────────────────────────────
function OutcomeBadge({ outcome, pnl }: { outcome: LogRow['outcome']; pnl: number | null }) {
  const cfg = {
    OPEN:       { label: 'OPEN',     bg: 'rgba(148,163,184,0.1)',  color: '#94a3b8', border: 'rgba(148,163,184,0.25)' },
    HIT_TARGET: { label: '✓ TARGET', bg: 'rgba(74,222,128,0.12)',  color: '#4ade80', border: 'rgba(74,222,128,0.3)'  },
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
        <span style={{ fontSize: 10, fontWeight: 700, fontFamily: 'monospace', color: pnl >= 0 ? '#4ade80' : '#f87171' }}>
          {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}%
        </span>
      )}
    </div>
  )
}

// ── Filter button ──────────────────────────────────────────────────────────────
function SegBtn<T extends string>({
  value, options, labels, onChange,
}: { value: T; options: T[]; labels?: Record<string, string>; onChange: (v: T) => void }) {
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

function fmt(n: number | null, decimals = 2) {
  return n == null ? '—' : n.toFixed(decimals)
}
function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' })
}

// ── Styles ─────────────────────────────────────────────────────────────────────
const TH: React.CSSProperties = {
  padding: '6px 8px', textAlign: 'left', fontSize: 10, fontWeight: 700,
  letterSpacing: '0.07em', color: 'var(--text-dim)',
  borderBottom: '1px solid var(--border)',
  position: 'sticky', top: 0, background: 'var(--bg-panel)', zIndex: 1,
  whiteSpace: 'nowrap',
}
const TD: React.CSSProperties = {
  padding: '6px 8px', fontSize: 11,
  borderBottom: '1px solid rgba(255,255,255,0.03)',
  whiteSpace: 'nowrap',
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function LagSignalLog() {
  const [rows, setRows]       = useState<LogRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState<string | null>(null)
  const [outcome, setOutcome]   = useState<OutcomeFilter>('ALL')
  const [sig, setSig]           = useState<SigFilter>('ALL')
  const [scoreF, setScoreF]     = useState<ScoreFilter>('ALL')
  const [search, setSearch]     = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const r = await fetch(`${API_URL}/api/lag-log`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json()
      setRows(d.rows ?? [])
    } catch {
      setError('Failed to load signal log')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const filtered = rows.filter(r => {
    if (outcome !== 'ALL' && r.outcome !== outcome) return false
    if (sig     !== 'ALL' && r.signal  !== sig)     return false
    if (scoreF === '5'  && (r.score ?? 0) < 5)      return false
    if (scoreF === '4+' && (r.score ?? 0) < 4)      return false
    if (scoreF === '3+' && (r.score ?? 0) < 3)      return false
    if (search && !r.ticker.toUpperCase().includes(search.toUpperCase())) return false
    return true
  })

  const closed  = filtered.filter(r => r.outcome !== 'OPEN')
  const wins    = closed.filter(r => r.outcome === 'HIT_TARGET')
  const winRate = closed.length ? Math.round(wins.length / closed.length * 100) : null
  const avgPnl  = closed.length
    ? closed.reduce((s, r) => s + (r.pnl_pct ?? 0), 0) / closed.length
    : null

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg-base)', color: 'var(--text-primary)' }}>

      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
        borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)',
        flexShrink: 0, flexWrap: 'wrap',
      }}>
        <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: '0.05em' }}>LAG SIG LOG</span>
        <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{rows.length} entries</span>

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
            type="text" placeholder="Ticker…" value={search}
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

        <SegBtn<ScoreFilter>
          value={scoreF} options={['ALL', '5', '4+', '3+']}
          labels={{ ALL: 'All', '5': '★ 5', '4+': '≥4', '3+': '≥3' }}
          onChange={setScoreF}
        />
        <SegBtn<SigFilter>
          value={sig} options={['ALL', 'BUY', 'SELL']} onChange={setSig}
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

      {error && <div style={{ padding: 16, color: '#f87171', fontSize: 12 }}>{error}</div>}

      {/* Table */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr>
              <th style={TH}>TICKER</th>
              <th style={TH}>DATE</th>
              <th style={TH}>SIG</th>
              <th style={{ ...TH, textAlign: 'center' }}>SCORE</th>
              {/* Daily SQZ */}
              <th style={{ ...TH, textAlign: 'center', borderLeft: '1px solid var(--border)' }}>SQZ D</th>
              {/* Stacked MA */}
              <th style={{ ...TH, textAlign: 'center', borderLeft: '1px solid var(--border)' }}>SMA50</th>
              <th style={{ ...TH, textAlign: 'center' }}>EMA8</th>
              <th style={{ ...TH, textAlign: 'center', borderRight: '1px solid var(--border)' }}>EMA21</th>
              {/* Moxie */}
              <th style={{ ...TH }}>MOXIE</th>
              {/* Trade levels */}
              <th style={{ ...TH, textAlign: 'right', borderLeft: '1px solid var(--border)' }}>ENTRY</th>
              <th style={{ ...TH, textAlign: 'right' }}>TARGET</th>
              <th style={{ ...TH, textAlign: 'right' }}>STOP</th>
              <th style={{ ...TH, textAlign: 'right' }}>DIST %</th>
              {/* Outcome */}
              <th style={{ ...TH, borderLeft: '1px solid var(--border)' }}>OUTCOME</th>
              <th style={TH}>OUT DATE</th>
              <th style={{ ...TH, textAlign: 'right' }}>EXIT</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(r => {
              const isBuy       = r.signal === 'BUY'
              const sigColor    = isBuy ? '#4ade80' : '#f87171'
              const direction   = isBuy ? 'LONG' : 'SHORT'
              const targetDist  = r.entry ? Math.abs((r.target - r.entry) / r.entry * 100) : null
              const p50 = r.sma50 && r.entry ? (r.entry - r.sma50) / r.sma50 * 100 : null
              const p8  = r.ema8  && r.entry ? (r.entry - r.ema8)  / r.ema8  * 100 : null
              const p21 = r.ema21 && r.entry ? (r.entry - r.ema21) / r.ema21 * 100 : null

              return (
                <tr key={r.id}
                  style={{ background: 'transparent' }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-row)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  {/* Ticker */}
                  <td style={{ ...TD, paddingLeft: 12 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                      <img
                        src={`https://img.logo.dev/ticker/${r.ticker}?token=pk_fZOnZkh3QrCkdBG6NS8ckQ&size=128&format=png&retina=true`}
                        alt=""
                        style={{ height: 22, width: 22, objectFit: 'contain', borderRadius: 4, flexShrink: 0 }}
                        onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                      />
                      <span style={{ fontFamily: 'monospace', fontWeight: 800, fontSize: 12 }}>
                        {r.ticker}
                      </span>
                    </div>
                  </td>

                  {/* Date */}
                  <td style={{ ...TD, color: 'var(--text-muted)', fontFamily: 'monospace', fontSize: 10 }}>
                    {fmtDate(r.signal_date)}
                  </td>

                  {/* Signal */}
                  <td style={TD}>
                    <span style={{
                      fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
                      padding: '2px 7px', borderRadius: 4,
                      background: `${sigColor}22`, color: sigColor,
                      border: `1px solid ${sigColor}55`,
                    }}>
                      {isBuy ? '▲ BUY' : '▼ SELL'}
                    </span>
                  </td>

                  {/* Score */}
                  <td style={{ ...TD, textAlign: 'center' }}>
                    {r.score != null
                      ? <ScorePill score={r.score} direction={direction} />
                      : <span style={{ color: 'var(--text-dim)' }}>—</span>}
                  </td>

                  {/* Daily SQZ */}
                  <td style={{ ...TD, textAlign: 'center', borderLeft: '1px solid var(--border)' }}>
                    <SqCell
                      state={r.d_sq_state}
                      moState={r.d_mo_state}
                      bars={r.d_bars_in_sq}
                      fired={r.d_bars_fired}
                      justFired={r.d_just_fired}
                    />
                  </td>

                  {/* SMA50 */}
                  <td style={{ ...TD, textAlign: 'center', borderLeft: '1px solid var(--border)' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
                      <Triangle above={r.entry != null && r.sma50 != null && r.entry > r.sma50} />
                      {p50 != null && (
                        <span style={{ fontSize: 9, fontFamily: 'monospace', color: p50 >= 0 ? '#4ade80' : '#f87171' }}>
                          {p50 >= 0 ? '+' : ''}{p50.toFixed(1)}%
                        </span>
                      )}
                    </div>
                  </td>

                  {/* EMA8 */}
                  <td style={{ ...TD, textAlign: 'center' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
                      <Triangle above={r.entry != null && r.ema8 != null && r.entry > r.ema8} />
                      {p8 != null && (
                        <span style={{ fontSize: 9, fontFamily: 'monospace', color: p8 >= 0 ? '#4ade80' : '#f87171' }}>
                          {p8 >= 0 ? '+' : ''}{p8.toFixed(1)}%
                        </span>
                      )}
                    </div>
                  </td>

                  {/* EMA21 */}
                  <td style={{ ...TD, textAlign: 'center', borderRight: '1px solid var(--border)' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
                      <Triangle above={r.entry != null && r.ema21 != null && r.entry > r.ema21} />
                      {p21 != null && (
                        <span style={{ fontSize: 9, fontFamily: 'monospace', color: p21 >= 0 ? '#4ade80' : '#f87171' }}>
                          {p21 >= 0 ? '+' : ''}{p21.toFixed(1)}%
                        </span>
                      )}
                    </div>
                  </td>

                  {/* Moxie */}
                  <td style={TD}>
                    {r.moxie_w != null
                      ? <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <Check ok={isBuy ? r.moxie_w > 0 : r.moxie_w < 0} />
                          <span style={{
                            fontSize: 10, fontFamily: 'monospace',
                            color: r.moxie_w >= 0 ? '#22d3ee' : '#f87171',
                          }}>
                            {r.moxie_w >= 0 ? '+' : ''}{r.moxie_w.toFixed(2)}
                          </span>
                        </div>
                      : <span style={{ color: 'var(--text-dim)' }}>—</span>}
                  </td>

                  {/* Entry */}
                  <td style={{ ...TD, textAlign: 'right', fontFamily: 'monospace', borderLeft: '1px solid var(--border)' }}>
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

                  {/* Dist % */}
                  <td style={{ ...TD, textAlign: 'right', fontFamily: 'monospace', color: 'var(--text-muted)' }}>
                    {targetDist != null ? `${targetDist.toFixed(1)}%` : '—'}
                  </td>

                  {/* Outcome */}
                  <td style={{ ...TD, borderLeft: '1px solid var(--border)' }}>
                    <OutcomeBadge outcome={r.outcome} pnl={r.pnl_pct} />
                  </td>

                  {/* Outcome date */}
                  <td style={{ ...TD, color: 'var(--text-muted)', fontFamily: 'monospace', fontSize: 10 }}>
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
                <td colSpan={16} style={{ ...TD, textAlign: 'center', color: 'var(--text-dim)', padding: 40 }}>
                  {rows.length === 0
                    ? 'No signals logged yet — runs nightly after each scan.'
                    : 'No entries match filters.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
