'use client'

import { useEffect, useState, useCallback } from 'react'

const PULSE_STYLE = `
@keyframes sqz-first-fire {
  0%   { box-shadow: 0 0 0 0 rgba(74,222,128,0.9), 0 0 6px rgba(74,222,128,0.6); }
  60%  { box-shadow: 0 0 0 7px rgba(74,222,128,0), 0 0 10px rgba(74,222,128,0.3); }
  100% { box-shadow: 0 0 0 0 rgba(74,222,128,0),   0 0 6px rgba(74,222,128,0.6); }
}
.sqz-first-fire { animation: sqz-first-fire 1.4s ease-out infinite; }
`

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

// ── Types ─────────────────────────────────────────────────────────────────────

interface SwingRow {
  ticker: string
  price: number
  scan_price: number   // EOD close from the scan — never overwritten by live refresh
  pct_change?: number
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
  d_just_fired: boolean
  // Weekly / Monthly squeeze
  w_sq_state: string | null
  w_mo_state: string | null
  w_confirms: boolean
  w_bars_in_sq: number
  w_bars_fired: number | null
  w_just_fired: boolean
  m_sq_state: string | null
  m_mo_state: string | null
  m_confirms: boolean
  m_bars_in_sq: number
  m_bars_fired: number | null
  m_just_fired: boolean
  // Indicator values
  sma50: number
  ema8: number
  ema21: number
  moxie_w: number
  laguerre: number
  lag_signal:   'BUY' | 'SELL' | null
  lag_entry:    number | null
  lag_target:   number | null
  lag_bars_ago: number | null
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
// _IN  = tightening (squeeze building)   _OUT = loosening (about to fire)
// PRE_IN=pink  PRE_OUT=yellow  ORIG=red  EXTRA=yellow  FIRED=green
const SQ_COLOR: Record<string, string> = {
  EXTRA_IN:  '#eab308', EXTRA_OUT: '#eab308',
  ORIG_IN:   '#dc2626', ORIG_OUT:  '#dc2626',
  PRE_IN:    '#ec4899', PRE_OUT:   '#eab308',
  FIRED:     '#16a34a',
}

// Momentum arrow color
const MO_COLOR: Record<string, string> = {
  POS_UP: '#22d3ee', POS_DN: '#3b82f6',
  NEG_DN: '#dc2626', NEG_UP: '#fbbf24',
}

const VA_STYLE: Record<string, { bg: string; color: string; label?: string }> = {
  ACCUM:    { bg: 'rgba(74,222,128,0.15)',  color: '#4ade80' },
  DIST:     { bg: 'rgba(248,113,113,0.15)', color: '#f87171' },
  // Legacy keys with ↑↓ arrows from DB — normalize to ▲▼ for display
  'CHURN↑': { bg: 'rgba(251,191,36,0.15)',  color: '#fbbf24', label: 'CHURN▲' },
  'CHURN↓': { bg: 'rgba(249,115,22,0.15)',  color: '#f97316', label: 'CHURN▼' },
  'CHURN▲': { bg: 'rgba(251,191,36,0.15)',  color: '#fbbf24' },
  'CHURN▼': { bg: 'rgba(249,115,22,0.15)',  color: '#f97316' },
  NEUTRAL:  { bg: 'rgba(148,163,184,0.08)', color: '#94a3b8' },
}

// ── Legend ────────────────────────────────────────────────────────────────────

function Legend() {
  const items: { color: string; label: string }[] = [
    { color: '#ec4899', label: 'Slow (building)' },
    { color: '#eab308', label: 'Slow (loosening) / Fast' },
    { color: '#dc2626', label: 'Normal' },
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
            border: 'none',
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

// Squeeze cell — mirrors squeezesetups.com .sqzd-cell layout:
//   [dot  ▲/▼]   ← cell-row
//      25         ← cell-bars mono
function SqCell({ state, moState, bars, fired, justFired, tf }: {
  state: string | null
  moState?: string | null
  bars?: number
  fired?: number | null
  justFired?: boolean
  tf?: 'D' | 'W' | 'M'
}) {
  if (!state) return <span style={{ color: 'var(--text-dim)', fontSize: 10 }}>—</span>

  const dotColor   = SQ_COLOR[state] ?? '#64748b'
  const moColor    = moState ? (MO_COLOR[moState] ?? '#64748b') : null
  const isUp       = moState === 'POS_UP' || moState === 'NEG_UP'
  const isFired    = state === 'FIRED'
  // Flash ⚡ window: daily = just_fired or bars=2 (covers weekend gap); weekly/monthly = just_fired only
  const recentFire = tf === 'D'
    ? (justFired || fired === 2)
    : justFired
  const showFlash  = isFired && moState === 'POS_UP' && recentFire
  const barLabel   = showFlash ? '⚡' : isFired && fired != null ? `+${fired}` : (bars || '')

  return (
    <div style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
      {/* cell-row: dot + arrow */}
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <span
          className={justFired ? 'sqz-first-fire' : undefined}
          style={{
            width: 12, height: 12, borderRadius: '50%', display: 'inline-block', flexShrink: 0,
            background: dotColor,
            border: 'none',
            boxShadow: isFired ? `0 0 ${showFlash ? 8 : 5}px ${dotColor}${showFlash ? 'cc' : '88'}` : 'none',
          }}
        />
        {moColor && (
          <span style={{
            color: moColor, fontSize: 10, fontWeight: 900, lineHeight: 1,
            fontFamily: 'monospace',
          }}>
            {isUp ? '▲' : '▼'}
          </span>
        )}
      </span>
      {/* cell-bars mono */}
      {barLabel !== '' && (
        <span style={{
          fontSize: showFlash ? 11 : 10,
          fontWeight: 700,
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

// Score pill — hot badge like .sqzd-tfs.hot when score=5
function ScorePill({ score, direction }: { score: number; direction: 'LONG' | 'SHORT' }) {
  const isHot  = score >= 5
  const isWarm = score === 4
  const fill   = direction === 'LONG' ? '#4ade80' : '#f87171'
  const bg     = isHot  ? (direction === 'LONG' ? '#14532d' : '#7f1d1d')
               : isWarm ? 'rgba(255,255,255,0.08)'
               : 'rgba(255,255,255,0.04)'
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
  padding: '5px 7px', textAlign: 'left', fontSize: 10, fontWeight: 700,
  letterSpacing: '0.07em', color: 'var(--text-dim)',
  borderBottom: '1px solid var(--border)',
  position: 'sticky', top: 0, background: 'var(--bg-panel)', zIndex: 1,
  whiteSpace: 'nowrap',
}
const TD: React.CSSProperties = {
  padding: '4px 7px', fontSize: 11,
  borderBottom: '1px solid rgba(255,255,255,0.03)',
  whiteSpace: 'nowrap',
}

export default function SwingScanner() {
  const [data, setData]           = useState<ScanResponse | null>(null)
  const [loading, setLoading]     = useState(false)
  const [dirFilter, setDir]       = useState<DirFilter>('ALL')
  const [minScore, setScore]      = useState<ScoreFilter>(0)
  const [sqOnly, setSqOnly]       = useState(false)
  const [fireOnly, setFireOnly]   = useState(false)
  const [universe, setUniverse]   = useState<UniverseFilter>('ALL')
  const [error, setError]         = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`${API_URL}/api/swing-scan`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const d = await r.json() as ScanResponse
      // scan_price comes from backend (EOD close); fall back to price if symbol had no live quote
      d.rows = d.rows.map(row => ({ ...row, scan_price: row.scan_price ?? row.price }))
      setData(d)
    } catch (e) {
      setError('Failed to load scan results')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Refresh price + % change every 30s without re-sorting rows
  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const r = await fetch(`${API_URL}/api/swing-scan`)
        if (!r.ok) return
        const d = await r.json() as ScanResponse
        setData(prev => {
          if (!prev) return d
          const priceMap = new Map(d.rows.map(r => [r.ticker, { price: r.price, pct_change: r.pct_change }]))
          return {
            ...prev,
            rows: prev.rows.map(row => {
              const updated = priceMap.get(row.ticker)
              if (!updated) return row
              return { ...row, price: updated.price, pct_change: updated.pct_change }
              // scan_price is intentionally NOT updated — it stays as EOD close
            }),
          }
        })
      } catch { /* silent — keep stale prices */ }
    }, 30_000)
    return () => clearInterval(id)
  }, [])

  const rows = (data?.rows ?? []).filter(r => {
    if (universe === 'EQUITIES' &&  SECTOR_TICKERS.has(r.ticker)) return false
    if (universe === 'SECTORS'  && !SECTOR_TICKERS.has(r.ticker)) return false
    if (dirFilter !== 'ALL' && r.direction !== dirFilter) return false
    if (r.score < minScore) return false
    if (sqOnly && r.d_sq_state === 'FIRED' && !r.d_bars_fired) return false
    if (fireOnly) {
      // Fresh FIRE signal per timeframe:
      //   Daily  — fired last trading day (just_fired OR bars_fired=2, covers weekend gap)
      //   Weekly — fired this past week (just_fired only)
      //   Monthly — fired this past month (just_fired only)
      // Upper-TF momo check only applied to weekly/monthly fires (daily fires stand alone;
      // the score column already reflects higher-TF alignment).
      const NEG = new Set(['NEG_DN', 'NEG_UP'])
      const mOk = !NEG.has(r.m_mo_state ?? '')
      const dFire = (r.d_just_fired || r.d_bars_fired === 2) && r.d_mo_state === 'POS_UP'
      const wFire = r.w_just_fired && r.w_mo_state === 'POS_UP' && mOk
      const mFire = r.m_just_fired && r.m_mo_state === 'POS_UP'
      if (!(dFire || wFire || mFire)) return false
    }
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
            onClick={() => setFireOnly(v => !v)}
            title="Show only tickers where a squeeze fired for the first time today (any timeframe)"
            style={{
              fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
              padding: '3px 9px', borderRadius: 5, cursor: 'pointer',
              background: fireOnly ? 'rgba(74,222,128,0.2)' : 'rgba(255,255,255,0.04)',
              border: fireOnly ? '1px solid #4ade80' : '1px solid var(--border)',
              color: fireOnly ? '#4ade80' : 'var(--text-dim)',
              transition: 'all 0.12s',
            }}
          >
            ⚡ 1ST FIRE
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
                {/* Stacked MAs group */}
                <th colSpan={3} style={{
                  ...TH, textAlign: 'center', borderBottom: '1px solid var(--border)',
                  letterSpacing: '0.1em', color: 'var(--text-muted)',
                  borderLeft: '1px solid var(--border)', borderRight: '1px solid var(--border)',
                }}>
                  STACKED MAs
                </th>
                <th style={{ ...TH, borderBottom: 'none' }} rowSpan={2}>MOXIE W</th>
                <th style={{ ...TH, borderBottom: 'none' }} rowSpan={2}>LAGR</th>
                <th style={{ ...TH, borderBottom: 'none' }} rowSpan={2}>LAG SIG</th>
                <th style={{ ...TH, borderBottom: 'none' }} rowSpan={2}>VA</th>
                <th style={{ ...TH, textAlign: 'right', borderBottom: 'none' }} rowSpan={2}>VAW</th>
                <th style={{ ...TH, textAlign: 'right', borderBottom: 'none' }} rowSpan={2}>VAM</th>
              </tr>
              {/* ── Sub-row for squeeze TFs + MA levels ── */}
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
                <th style={{
                  ...TH, textAlign: 'center', fontSize: 9, padding: '5px 4px', width: 42,
                  borderLeft: '1px solid var(--border)',
                }}>SMA50</th>
                <th style={{
                  ...TH, textAlign: 'center', fontSize: 9, padding: '5px 4px', width: 42,
                }}>EMA8</th>
                <th style={{
                  ...TH, textAlign: 'center', fontSize: 9, padding: '5px 4px', width: 42,
                  borderRight: '1px solid var(--border)',
                }}>EMA21</th>
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
                          <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontFamily: 'monospace', fontSize: 10 }}>
                            <span style={{ color: 'var(--text-dim)' }}>${r.price.toFixed(2)}</span>
                            {r.pct_change != null && (
                              <span style={{ color: r.pct_change >= 0 ? '#4ade80' : '#f87171', fontWeight: 700 }}>
                                {r.pct_change >= 0 ? '+' : ''}{r.pct_change.toFixed(2)}%
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                    </td>

                    {/* Direction — status-pill style */}
                    <td style={{ ...TD }}>
                      <span style={{
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                        fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
                        fontFamily: 'monospace',
                        color: dirColor,
                        padding: '3px 8px', borderRadius: 20,
                        background: `${dirColor}15`,
                        border: `1px solid ${dirColor}40`,
                      }}>
                        {isLong ? '▲' : '▼'} {r.direction}
                      </span>
                    </td>

                    {/* Score — hot pill like .sqzd-tfs.hot */}
                    <td style={TD}>
                      <ScorePill score={r.score} direction={r.direction} />
                    </td>

                    {/* Daily squeeze */}
                    <td style={{ ...TD, textAlign: 'center', borderLeft: '1px solid var(--border)' }}>
                      <SqCell
                        state={r.d_sq_state}
                        moState={r.d_mo_state}
                        bars={r.d_bars_in_sq}
                        fired={r.d_bars_fired}
                        justFired={r.d_just_fired}
                        tf="D"
                      />
                    </td>

                    {/* Weekly squeeze */}
                    <td style={{ ...TD, textAlign: 'center' }}>
                      <SqCell state={r.w_sq_state} moState={r.w_mo_state} bars={r.w_bars_in_sq} fired={r.w_bars_fired} justFired={r.w_just_fired} tf="W" />
                    </td>

                    {/* Monthly squeeze */}
                    <td style={{ ...TD, textAlign: 'center', borderRight: '1px solid var(--border)' }}>
                      <SqCell state={r.m_sq_state} moState={r.m_mo_state} bars={r.m_bars_in_sq} fired={r.m_bars_fired} justFired={r.m_just_fired} tf="M" />
                    </td>

                    {/* SMA50 — EOD close vs SMA50 % */}
                    {(() => {
                      const pct50 = r.sma50 ? ((r.scan_price - r.sma50) / r.sma50 * 100) : null
                      const above50 = r.scan_price > r.sma50
                      return (
                        <td style={{ ...TD, textAlign: 'center', padding: '4px 4px', width: 42, borderLeft: '1px solid var(--border)' }}>
                          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
                            <Triangle above={above50} />
                            {pct50 != null && (
                              <span style={{ fontSize: 9, fontFamily: 'monospace', color: above50 ? '#4ade80' : '#f87171' }}>
                                {pct50 >= 0 ? '+' : ''}{pct50.toFixed(1)}%
                              </span>
                            )}
                          </div>
                        </td>
                      )
                    })()}

                    {/* EMA8 — EOD close vs EMA8 % */}
                    {(() => {
                      const pct8 = r.ema8 ? ((r.scan_price - r.ema8) / r.ema8 * 100) : null
                      const above8 = r.scan_price > r.ema8
                      return (
                        <td style={{ ...TD, textAlign: 'center', padding: '4px 4px', width: 42 }}>
                          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
                            <Triangle above={above8} />
                            {pct8 != null && (
                              <span style={{ fontSize: 9, fontFamily: 'monospace', color: above8 ? '#4ade80' : '#f87171' }}>
                                {pct8 >= 0 ? '+' : ''}{pct8.toFixed(1)}%
                              </span>
                            )}
                          </div>
                        </td>
                      )
                    })()}

                    {/* EMA21 — EOD close vs EMA21 % */}
                    {(() => {
                      const pct21 = r.ema21 ? ((r.scan_price - r.ema21) / r.ema21 * 100) : null
                      const above21 = r.scan_price > r.ema21
                      return (
                        <td style={{ ...TD, textAlign: 'center', padding: '4px 4px', width: 42, borderRight: '1px solid var(--border)' }}>
                          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1 }}>
                            <Triangle above={above21} />
                            {pct21 != null && (
                              <span style={{ fontSize: 9, fontFamily: 'monospace', color: above21 ? '#4ade80' : '#f87171' }}>
                                {pct21 >= 0 ? '+' : ''}{pct21.toFixed(1)}%
                              </span>
                            )}
                          </div>
                        </td>
                      )
                    })()}

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

                    {/* Laguerre Signal */}
                    <td style={{ ...TD, minWidth: 90 }}>
                      {r.lag_signal ? (() => {
                        const isBuy = r.lag_signal === 'BUY'
                        const col   = isBuy ? '#4ade80' : '#f87171'
                        const dim   = 'var(--text-muted)'
                        return (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                              <span style={{
                                fontSize: 8, fontWeight: 700, letterSpacing: '0.06em',
                                padding: '2px 5px', borderRadius: 4,
                                background: `${col}22`, color: col,
                                border: `1px solid ${col}55`,
                              }}>
                                {r.lag_signal}
                              </span>
                              {r.lag_bars_ago != null && (
                                <span style={{ fontSize: 9, color: dim, fontFamily: 'monospace' }}>
                                  {r.lag_bars_ago === 0 ? 'today' : `${r.lag_bars_ago}d`}
                                </span>
                              )}
                            </div>
                            {r.lag_entry != null && (
                              <span style={{ fontSize: 9, fontFamily: 'monospace', color: dim }}>
                                E {r.lag_entry.toFixed(2)}
                              </span>
                            )}
                            {r.lag_target != null && (
                              <span style={{ fontSize: 9, fontFamily: 'monospace', color: col }}>
                                T {r.lag_target.toFixed(2)}
                              </span>
                            )}
                          </div>
                        )
                      })() : <span style={{ color: 'var(--text-dim)', fontSize: 9 }}>—</span>}
                    </td>

                    {/* VA Badge — mp-chip pill style */}
                    <td style={TD}>
                      {(() => {
                        const s = VA_STYLE[r.va_badge] ?? VA_STYLE.NEUTRAL
                        return (
                          <span style={{
                            fontSize: 9, fontWeight: 700, letterSpacing: '0.05em',
                            fontFamily: 'monospace',
                            padding: '3px 8px', borderRadius: 20,
                            background: s.bg, color: s.color,
                            border: `1px solid ${s.color}40`,
                            display: 'inline-block',
                          }}>
                            {s.label ?? r.va_badge}
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
                  <td colSpan={15} style={{ ...TD, textAlign: 'center', color: 'var(--text-dim)', padding: 32 }}>
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
        ${PULSE_STYLE}
      `}</style>
    </div>
  )
}
