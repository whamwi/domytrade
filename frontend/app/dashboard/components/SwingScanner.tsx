'use client'

import { useEffect, useState, useCallback } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

// ── Types ─────────────────────────────────────────────────────────────────────

interface SwingRow {
  ticker: string
  price: number
  direction: 'LONG' | 'SHORT'
  score: number
  long_score: number
  short_score: number
  // Daily squeeze
  d_sq_state: string | null
  d_sq_color: string | null
  d_mo_state: string | null
  d_mo_color: string | null
  d_momo: number | null
  d_bars_in_sq: number
  d_bars_fired: number | null
  // Weekly / Monthly squeeze
  w_sq_state: string | null
  w_confirms: boolean
  w_bars_in_sq: number
  m_sq_state: string | null
  m_confirms: boolean
  // Indicator values
  sma50: number
  ema8: number
  ema21: number
  moxie_w: number
  laguerre: number
  // VAW / VAM
  vaw_m: number
  vam_m: number
  va_badge: string
}

interface ScanResponse {
  rows: SwingRow[]
  count: number
  scanned_at: string | null
}

// ── Universe classification ───────────────────────────────────────────────────

const SECTOR_TICKERS = new Set([
  'XLK','XLV','XLF','XLC','XLY','XLI','XLP','XLE','XLB','XLU','XLRE',
  'SMH','HACK','SKYY','TAN','JETS','OIH','IYT','EEM','SOCL','KCE','XLG','XRT',
  'QQQ','SPY','GLD','SLV','USO',
])

type UniverseFilter = 'ALL' | 'EQUITIES' | 'SECTORS'

// ── Style maps ────────────────────────────────────────────────────────────────

// Squeeze dot colors — match TOS SqueezePRO palette
// PRE (slow) = orange  ORIG (normal) = red  EXTRA (fast) = near-black  FIRED = green
const SQ_COLOR: Record<string, string> = {
  EXTRA_IN: '#ffffff', EXTRA_OUT: '#ffffff',
  ORIG_IN:  '#dc2626', ORIG_OUT:  '#dc2626',
  PRE_IN:   '#f97316', PRE_OUT:   '#f97316',
  FIRED:    '#16a34a',
}

// Momentum arrow color
const MO_COLOR: Record<string, string> = {
  POS_UP: '#22d3ee', POS_DN: '#3b82f6',
  NEG_DN: '#dc2626', NEG_UP: '#fbbf24',
}

const VA_STYLE: Record<string, { bg: string; color: string }> = {
  ACCUM:    { bg: 'rgba(74,222,128,0.15)',  color: '#4ade80' },
  DIST:     { bg: 'rgba(248,113,113,0.15)', color: '#f87171' },
  'CHURN↑': { bg: 'rgba(251,191,36,0.15)',  color: '#fbbf24' },
  'CHURN↓': { bg: 'rgba(249,115,22,0.15)',  color: '#f97316' },
  NEUTRAL:  { bg: 'rgba(148,163,184,0.08)', color: '#94a3b8' },
}

// ── Legend ────────────────────────────────────────────────────────────────────

function Legend() {
  const items: { color: string; label: string }[] = [
    { color: '#f97316', label: 'Slow squeeze' },
    { color: '#dc2626', label: 'Normal' },
    { color: '#ffffff', label: 'Fast' },
    { color: '#16a34a', label: 'Fired' },
  ]
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap',
      padding: '6px 16px', background: 'var(--bg-panel)',
      borderBottom: '1px solid var(--border)', flexShrink: 0,
      fontSize: 10, color: 'var(--text-dim)',
    }}>
      <span style={{ fontWeight: 700, letterSpacing: '0.06em', color: 'var(--text-dim)', marginRight: 4 }}>LEGEND</span>
      {items.map(({ color, label }) => (
        <span key={label} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{
            width: 10, height: 10, borderRadius: '50%', background: color, flexShrink: 0,
            border: color === '#ffffff' ? '1px solid #555' : 'none',
          }} />
          {label}
        </span>
      ))}
      <span style={{ color: 'var(--text-dim)', opacity: 0.6 }}>·</span>
      <span><span style={{ fontWeight: 700, color: 'var(--text-muted)' }}>12</span> = bars in squeeze</span>
      <span style={{ color: 'var(--text-dim)', opacity: 0.6 }}>·</span>
      <span>
        <span style={{ color: '#22d3ee', fontWeight: 700 }}>▲</span>
        <span style={{ color: '#fbbf24', fontWeight: 700 }}> ▼</span>
        {' '}= momentum direction
      </span>
      <span style={{ color: 'var(--text-dim)', opacity: 0.6 }}>·</span>
      <span>D / W / M = Daily · Weekly · Monthly</span>
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────────────────

function ScoreDots({ score, direction }: { score: number; direction: 'LONG' | 'SHORT' }) {
  const fill = direction === 'LONG' ? '#4ade80' : '#f87171'
  return (
    <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
      {[1, 2, 3, 4, 5].map(i => (
        <span key={i} style={{
          width: 7, height: 7, borderRadius: '50%', display: 'inline-block',
          background: i <= score ? fill : 'rgba(255,255,255,0.12)',
        }} />
      ))}
    </div>
  )
}

function Check({ ok }: { ok: boolean }) {
  return (
    <span style={{ color: ok ? '#4ade80' : '#ef4444', fontWeight: 700, fontSize: 12 }}>
      {ok ? '✓' : '✗'}
    </span>
  )
}

// Squeeze cell: colored dot + momentum arrow stacked, bar count below
function SqCell({ state, moState, bars, fired }: {
  state: string | null
  moState?: string | null
  bars?: number
  fired?: number | null
}) {
  if (!state) return <span style={{ color: 'var(--text-dim)', fontSize: 10 }}>—</span>

  const dotColor = SQ_COLOR[state] ?? '#64748b'
  const moColor  = moState ? (MO_COLOR[moState] ?? '#64748b') : null
  const isUp     = moState === 'POS_UP' || moState === 'NEG_UP'
  const isFired  = state === 'FIRED'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5 }}>
      {/* Dot + arrow row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
        <span style={{
          width: 11, height: 11, borderRadius: '50%', display: 'inline-block', flexShrink: 0,
          background: dotColor,
          border: dotColor === '#ffffff' ? '1px solid #555' : 'none',
          boxShadow: isFired ? `0 0 5px ${dotColor}88` : 'none',
        }} />
        {moColor && (
          <span style={{ color: moColor, fontSize: 9, fontWeight: 800, lineHeight: 1 }}>
            {isUp ? '▲' : '▼'}
          </span>
        )}
      </div>
      {/* Bar count */}
      <span style={{ fontSize: 9, color: 'var(--text-dim)', fontWeight: 600, lineHeight: 1 }}>
        {isFired && fired != null ? `+${fired}` : bars ? bars : ''}
      </span>
    </div>
  )
}

// ── Segmented button ──────────────────────────────────────────────────────────

type DirFilter   = 'ALL' | 'LONG' | 'SHORT'
type ScoreFilter = 0 | 3 | 4 | 5

function Seg<T extends string | number>({
  value, options, labels, onChange,
}: {
  value: T
  options: T[]
  labels?: Record<string, string>
  onChange: (v: T) => void
}) {
  return (
    <div style={{ display: 'flex', gap: 3 }}>
      {options.map(opt => {
        const active = opt === value
        return (
          <button
            key={String(opt)}
            onClick={() => onChange(opt)}
            style={{
              fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
              padding: '3px 9px', borderRadius: 5, cursor: 'pointer',
              background: active ? 'var(--accent-blue)' : 'rgba(255,255,255,0.04)',
              border:     active ? '1px solid var(--accent-blue)' : '1px solid var(--border)',
              color:      active ? '#fff' : 'var(--text-dim)',
              transition: 'all 0.12s',
            }}
          >
            {labels ? labels[String(opt)] : String(opt)}
          </button>
        )
      })}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

const TH: React.CSSProperties = {
  padding: '6px 10px', textAlign: 'left', fontSize: 10, fontWeight: 700,
  letterSpacing: '0.07em', color: 'var(--text-dim)',
  borderBottom: '1px solid var(--border)',
  position: 'sticky', top: 0, background: 'var(--bg-panel)', zIndex: 1,
  whiteSpace: 'nowrap',
}
const TD: React.CSSProperties = {
  padding: '5px 10px', fontSize: 11,
  borderBottom: '1px solid rgba(255,255,255,0.03)',
  whiteSpace: 'nowrap',
}

export default function SwingScanner() {
  const [data, setData]           = useState<ScanResponse | null>(null)
  const [loading, setLoading]     = useState(false)
  const [dirFilter, setDir]       = useState<DirFilter>('ALL')
  const [minScore, setScore]      = useState<ScoreFilter>(0)
  const [sqOnly, setSqOnly]       = useState(false)
  const [universe, setUniverse]   = useState<UniverseFilter>('ALL')
  const [error, setError]         = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`${API_URL}/api/swing-scan`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json() as ScanResponse
      setData(d)
    } catch (e) {
      setError('Failed to load scan results')
    } finally {
      setLoading(false)
    }
  }, [])

  const triggerRescan = useCallback(async () => {
    setError(null)
    try {
      await fetch(`${API_URL}/api/swing-scan/refresh`, { method: 'POST' })
      // Poll until scanned_at changes (rescan takes ~60s)
      const prevAt = data?.scanned_at
      let attempts = 0
      const poll = setInterval(async () => {
        attempts++
        try {
          const r = await fetch(`${API_URL}/api/swing-scan`)
          const d = await r.json() as ScanResponse
          if (d.scanned_at !== prevAt || attempts > 18) {
            clearInterval(poll)
            setData(d)
          }
        } catch { clearInterval(poll) }
      }, 5000)
    } catch (e) {
      setError('Failed to trigger rescan')
    }
  }, [data])

  useEffect(() => { load() }, [load])

  const rows = (data?.rows ?? []).filter(r => {
    if (universe === 'EQUITIES' &&  SECTOR_TICKERS.has(r.ticker)) return false
    if (universe === 'SECTORS'  && !SECTOR_TICKERS.has(r.ticker)) return false
    if (dirFilter !== 'ALL' && r.direction !== dirFilter) return false
    if (r.score < minScore) return false
    if (sqOnly && r.d_sq_state === 'FIRED' && !r.d_bars_fired) return false
    return true
  })

  const longCount  = rows.filter(r => r.direction === 'LONG').length
  const shortCount = rows.filter(r => r.direction === 'SHORT').length

  return (
    <div style={{
      height: '100vh', display: 'flex', flexDirection: 'column',
      background: 'var(--bg-base)', color: 'var(--text-primary)',
    }}>

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
        borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)',
        flexShrink: 0, flexWrap: 'wrap',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginRight: 4 }}>
          <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: '0.05em', color: 'var(--text-primary)' }}>
            SWING SCANNER
          </span>
          {data && (
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>
              {data.count} symbols
              {data.scanned_at && (
                <span> · scanned {new Date(data.scanned_at).toLocaleString('en-US', {
                  month: 'short', day: 'numeric',
                  hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
                })}</span>
              )}
            </span>
          )}
        </div>

        {/* Direction summary */}
        {data && (
          <div style={{ display: 'flex', gap: 8, fontSize: 10, fontWeight: 700 }}>
            <span style={{ color: '#4ade80' }}>▲ {longCount} LONG</span>
            <span style={{ color: '#f87171' }}>▼ {shortCount} SHORT</span>
          </div>
        )}

        <div style={{ flex: 1 }} />

        {/* Filters */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <Seg<UniverseFilter>
            value={universe}
            options={['ALL', 'EQUITIES', 'SECTORS']}
            labels={{ ALL: 'All', EQUITIES: 'Equities', SECTORS: 'Sectors' }}
            onChange={setUniverse}
          />
          <div style={{ width: 1, height: 16, background: 'var(--border)' }} />
          <Seg<DirFilter>
            value={dirFilter}
            options={['ALL', 'LONG', 'SHORT']}
            onChange={setDir}
          />
          <Seg<ScoreFilter>
            value={minScore}
            options={[0, 3, 4, 5]}
            labels={{ '0': 'All', '3': '3+', '4': '4+', '5': '5★' }}
            onChange={setScore}
          />
          <button
            onClick={() => setSqOnly(v => !v)}
            style={{
              fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
              padding: '3px 9px', borderRadius: 5, cursor: 'pointer',
              background: sqOnly ? 'rgba(192,38,211,0.25)' : 'rgba(255,255,255,0.04)',
              border: sqOnly ? '1px solid #c026d3' : '1px solid var(--border)',
              color: sqOnly ? '#e879f9' : 'var(--text-dim)',
              transition: 'all 0.12s',
            }}
          >
            IN SQZ
          </button>
          <button
            onClick={triggerRescan}
            disabled={loading}
            title="Trigger a full rescan (~60s) — runs automatically at 5:15 PM ET"
            style={{
              fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
              padding: '3px 9px', borderRadius: 5, cursor: loading ? 'wait' : 'pointer',
              background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)',
              color: loading ? 'var(--text-dim)' : 'var(--text-primary)',
              transition: 'all 0.12s',
            }}
          >
            ↺ RESCAN
          </button>
        </div>
      </div>

      {/* ── Error ──────────────────────────────────────────────────────────── */}
      {error && (
        <div style={{ padding: 16, color: '#f87171', fontSize: 12 }}>{error}</div>
      )}

      {/* ── Loading state ──────────────────────────────────────────────────── */}
      {loading && !data && (
        <div style={{
          flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexDirection: 'column', gap: 12,
        }}>
          <div style={{
            width: 32, height: 32, border: '2px solid var(--border)',
            borderTopColor: 'var(--accent-blue)', borderRadius: '50%',
            animation: 'spin 0.8s linear infinite',
          }} />
          <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>
            Scanning 161 symbols…
          </span>
        </div>
      )}

      {/* ── Legend ─────────────────────────────────────────────────────────── */}
      {data && <Legend />}

      {/* ── Table ──────────────────────────────────────────────────────────── */}
      {data && (
        <div style={{ flex: 1, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              {/* ── Group row ── */}
              <tr>
                <th style={{ ...TH, borderBottom: 'none' }} rowSpan={2}>SYMBOL</th>
                <th style={{ ...TH, borderBottom: 'none' }} rowSpan={2}>DIR</th>
                <th style={{ ...TH, borderBottom: 'none' }} rowSpan={2}>SCORE</th>
                {/* Squeeze Matrix group */}
                <th colSpan={3} style={{
                  ...TH, textAlign: 'center', borderBottom: '1px solid var(--border)',
                  letterSpacing: '0.1em', color: 'var(--text-muted)',
                  borderLeft: '1px solid var(--border)', borderRight: '1px solid var(--border)',
                }}>
                  SQUEEZE MATRIX
                </th>
                <th style={{ ...TH, borderBottom: 'none' }} rowSpan={2}>SMA50</th>
                <th style={{ ...TH, borderBottom: 'none' }} rowSpan={2}>EMA</th>
                <th style={{ ...TH, borderBottom: 'none' }} rowSpan={2}>MOXIE W</th>
                <th style={{ ...TH, borderBottom: 'none' }} rowSpan={2}>LAGR</th>
                <th style={{ ...TH, borderBottom: 'none' }} rowSpan={2}>VA</th>
                <th style={{ ...TH, textAlign: 'right', borderBottom: 'none' }} rowSpan={2}>VAW</th>
                <th style={{ ...TH, textAlign: 'right', borderBottom: 'none' }} rowSpan={2}>VAM</th>
              </tr>
              {/* ── Sub-row for squeeze TFs ── */}
              <tr>
                <th style={{
                  ...TH, textAlign: 'center', fontSize: 9,
                  borderLeft: '1px solid var(--border)',
                }}>D</th>
                <th style={{ ...TH, textAlign: 'center', fontSize: 9 }}>W</th>
                <th style={{
                  ...TH, textAlign: 'center', fontSize: 9,
                  borderRight: '1px solid var(--border)',
                }}>M</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const isLong = r.direction === 'LONG'
                const dirColor = isLong ? '#4ade80' : '#f87171'
                const rowBg = r.score >= 4
                  ? (isLong ? 'rgba(74,222,128,0.04)' : 'rgba(248,113,113,0.04)')
                  : 'transparent'

                return (
                  <tr
                    key={r.ticker}
                    style={{ background: rowBg }}
                    onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-row)')}
                    onMouseLeave={e => (e.currentTarget.style.background = rowBg)}
                  >
                    {/* Ticker + price */}
                    <td style={{ ...TD, paddingLeft: 16, paddingTop: 8, paddingBottom: 8 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <img
                          src={`https://img.logo.dev/ticker/${r.ticker}?token=pk_fZOnZkh3QrCkdBG6NS8ckQ&size=128&format=png&retina=true`}
                          alt=""
                          style={{ height: 32, width: 32, objectFit: 'contain', borderRadius: 6, flexShrink: 0 }}
                          onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                        />
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                          <span style={{ fontSize: 13, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '0.04em', fontFamily: 'monospace' }}>
                            {r.ticker}
                          </span>
                          <span style={{ fontSize: 10, color: 'var(--text-dim)', fontFamily: 'monospace' }}>
                            ${r.price.toFixed(2)}
                          </span>
                        </div>
                      </div>
                    </td>

                    {/* Direction */}
                    <td style={{ ...TD }}>
                      <span style={{
                        fontSize: 9, fontWeight: 800, letterSpacing: '0.08em',
                        color: dirColor, padding: '2px 6px', borderRadius: 3,
                        background: `${dirColor}18`,
                      }}>
                        {r.direction}
                      </span>
                    </td>

                    {/* Score */}
                    <td style={TD}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <ScoreDots score={r.score} direction={r.direction} />
                        <span style={{
                          fontSize: 10, fontWeight: 700, color: dirColor,
                          fontFamily: 'monospace',
                        }}>
                          {r.score}/5
                        </span>
                      </div>
                    </td>

                    {/* Daily squeeze */}
                    <td style={{ ...TD, textAlign: 'center', borderLeft: '1px solid var(--border)' }}>
                      <SqCell
                        state={r.d_sq_state}
                        moState={r.d_mo_state}
                        bars={r.d_bars_in_sq}
                        fired={r.d_bars_fired}
                      />
                    </td>

                    {/* Weekly squeeze */}
                    <td style={{ ...TD, textAlign: 'center' }}>
                      <SqCell state={r.w_sq_state} bars={r.w_bars_in_sq} />
                    </td>

                    {/* Monthly squeeze */}
                    <td style={{ ...TD, textAlign: 'center', borderRight: '1px solid var(--border)' }}>
                      <SqCell state={r.m_sq_state} />
                    </td>

                    {/* SMA50 */}
                    <td style={{ ...TD, textAlign: 'center' }}>
                      <Check ok={isLong ? r.price > r.sma50 : r.price < r.sma50} />
                    </td>

                    {/* EMA stack */}
                    <td style={{ ...TD, textAlign: 'center' }}>
                      <Check ok={isLong ? r.ema8 > r.ema21 : r.ema8 < r.ema21} />
                    </td>

                    {/* Moxie W */}
                    <td style={TD}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <Check ok={isLong ? r.moxie_w > 0 : r.moxie_w < 0} />
                        <span style={{
                          fontSize: 10, color: r.moxie_w >= 0 ? '#22d3ee' : '#f87171',
                          fontFamily: 'monospace',
                        }}>
                          {r.moxie_w >= 0 ? '+' : ''}{r.moxie_w.toFixed(2)}
                        </span>
                      </div>
                    </td>

                    {/* Laguerre RSI */}
                    <td style={TD}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <Check ok={isLong ? r.laguerre > 0.5 : r.laguerre < 0.5} />
                        <span style={{
                          fontSize: 10, fontFamily: 'monospace',
                          color: r.laguerre < 0.2 ? '#f87171'
                               : r.laguerre > 0.8 ? '#fbbf24'
                               : 'var(--text-muted)',
                        }}>
                          {r.laguerre.toFixed(3)}
                        </span>
                      </div>
                    </td>

                    {/* VA Badge */}
                    <td style={TD}>
                      {(() => {
                        const s = VA_STYLE[r.va_badge] ?? VA_STYLE.NEUTRAL
                        return (
                          <span style={{
                            fontSize: 9, fontWeight: 700, letterSpacing: '0.06em',
                            padding: '2px 7px', borderRadius: 4,
                            background: s.bg, color: s.color,
                          }}>
                            {r.va_badge}
                          </span>
                        )
                      })()}
                    </td>

                    {/* VAW */}
                    <td style={{ ...TD, textAlign: 'right', fontFamily: 'monospace' }}>
                      <span style={{ color: r.vaw_m >= 0 ? '#4ade80' : '#f87171', fontSize: 10 }}>
                        {r.vaw_m >= 0 ? '+' : ''}{r.vaw_m.toFixed(1)}M
                      </span>
                    </td>

                    {/* VAM */}
                    <td style={{ ...TD, textAlign: 'right', fontFamily: 'monospace' }}>
                      <span style={{ color: r.vam_m >= 0 ? '#4ade80' : '#f87171', fontSize: 10 }}>
                        {r.vam_m >= 0 ? '+' : ''}{r.vam_m.toFixed(1)}M
                      </span>
                    </td>
                  </tr>
                )
              })}

              {rows.length === 0 && !loading && (
                <tr>
                  <td colSpan={14} style={{ ...TD, textAlign: 'center', color: 'var(--text-dim)', padding: 32 }}>
                    No symbols match the current filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  )
}
