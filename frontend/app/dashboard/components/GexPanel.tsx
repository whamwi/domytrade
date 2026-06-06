'use client'

import { useEffect, useState, useCallback, useRef } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

// ── Default symbols to show as quick-pick chips ────────────────────────────
const QUICK_SYMBOLS = ['SPY', 'QQQ', 'AMZN', 'AAPL', 'NVDA', 'TSLA', 'SPX']

// ── Types ──────────────────────────────────────────────────────────────────
interface StrikeRow {
  strike: number
  call_gex_mm: number
  put_gex_mm: number
  net_gex_mm: number
  is_atm: boolean
  is_call_wall: boolean
  is_put_wall: boolean
  is_zero_gamma: boolean
}

interface GexData {
  symbol: string
  underlying: number
  net_gex_mm: number
  gamma_regime: 'POSITIVE' | 'NEGATIVE'
  call_wall: number
  put_wall: number
  zero_gamma_level: number | null
  expected_move_pct: number | null
  expected_move_pts: number | null
  expiries: string[]
  strikes: StrikeRow[]
  strike_count: number
}

// ── Helpers ────────────────────────────────────────────────────────────────
function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null) return '—'
  return n.toFixed(decimals)
}
function fmtMM(n: number | null | undefined): string {
  if (n == null) return '—'
  const abs = Math.abs(n)
  if (abs >= 1000) return `${(n / 1000).toFixed(1)}B`
  return `${n >= 0 ? '+' : ''}${n.toFixed(1)}M`
}

export default function GexPanel() {
  const [symbol, setSymbol]       = useState('SPY')
  const [inputVal, setInputVal]   = useState('SPY')
  const [data, setData]           = useState<GexData | null>(null)
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState<string | null>(null)
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)
  const [showAll, setShowAll]     = useState(false)    // show all strikes vs ±15 around ATM
  const refreshTimer              = useRef<ReturnType<typeof setTimeout> | null>(null)

  const fetchGex = useCallback(async (sym: string) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_URL}/api/gex/${sym}?strike_count=60`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json: GexData = await res.json()
      if ((json as any).error) throw new Error((json as any).error)
      setData(json)
      setLastUpdate(new Date())
    } catch (e: any) {
      setError(e.message ?? 'Failed to load GEX data')
    } finally {
      setLoading(false)
    }
  }, [])

  // Auto-refresh every 5 minutes
  useEffect(() => {
    fetchGex(symbol)
    refreshTimer.current = setInterval(() => fetchGex(symbol), 5 * 60 * 1000)
    return () => { if (refreshTimer.current) clearInterval(refreshTimer.current) }
  }, [symbol, fetchGex])

  function handleSymbolSelect(sym: string) {
    setSymbol(sym.toUpperCase())
    setInputVal(sym.toUpperCase())
  }

  function handleCustomSubmit(e: React.FormEvent) {
    e.preventDefault()
    const sym = inputVal.trim().toUpperCase()
    if (sym) handleSymbolSelect(sym)
  }

  // ── Compute display rows ─────────────────────────────────────────────────
  const strikes = data?.strikes ?? []
  const maxAbsGex = strikes.reduce((m, r) => Math.max(m, Math.abs(r.call_gex_mm), Math.abs(r.put_gex_mm)), 1)

  // Visible rows: show all or ±20 strikes around ATM
  const atmIdx = strikes.findIndex(r => r.is_atm)
  const visibleRows = showAll
    ? [...strikes].reverse()
    : [...strikes]
        .reverse()
        .filter((_, i, arr) => {
          const atmRevIdx = arr.length - 1 - atmIdx
          return Math.abs(i - atmRevIdx) <= 20
        })

  const isPositive = (data?.gamma_regime === 'POSITIVE')

  return (
    <div
      className="flex flex-col h-full min-h-0"
      style={{ background: 'var(--bg-main)', color: 'var(--text-primary)' }}
    >
      {/* ── Top bar ────────────────────────────────────────────────────── */}
      <div
        className="flex items-center gap-3 px-5 py-3 shrink-0 flex-wrap"
        style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)' }}
      >
        {/* Title */}
        <span className="font-bold text-sm tracking-widest" style={{ color: 'var(--text-muted)', letterSpacing: '0.15em' }}>
          GEX
        </span>
        <span className="text-xs" style={{ color: 'var(--text-dim)' }}>Gamma Exposure</span>

        <div style={{ width: 1, height: 16, background: 'var(--border)' }} />

        {/* Quick pick chips */}
        {QUICK_SYMBOLS.map(sym => (
          <button
            key={sym}
            onClick={() => handleSymbolSelect(sym)}
            className="text-xs px-2.5 py-1 rounded font-mono font-semibold transition-colors"
            style={{
              background: symbol === sym ? 'var(--accent-blue-dim)' : 'var(--bg-row)',
              color: symbol === sym ? 'var(--accent-blue)' : 'var(--text-muted)',
              border: `1px solid ${symbol === sym ? 'var(--accent-blue)' : 'var(--border)'}`,
            }}
          >
            {sym}
          </button>
        ))}

        {/* Custom symbol input */}
        <form onSubmit={handleCustomSubmit} className="flex items-center gap-1">
          <input
            value={inputVal}
            onChange={e => setInputVal(e.target.value.toUpperCase())}
            placeholder="TICKER"
            maxLength={10}
            className="text-xs px-2 py-1 rounded font-mono w-20 outline-none"
            style={{
              background: 'var(--bg-row)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
          />
          <button
            type="submit"
            className="text-xs px-2 py-1 rounded transition-colors"
            style={{ background: 'var(--bg-row)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
          >
            Go
          </button>
        </form>

        <div style={{ flex: 1 }} />

        {/* Show all toggle */}
        <button
          onClick={() => setShowAll(v => !v)}
          className="text-xs px-2.5 py-1 rounded transition-colors"
          style={{
            background: showAll ? 'var(--accent-blue-dim)' : 'var(--bg-row)',
            border: '1px solid var(--border)',
            color: showAll ? 'var(--accent-blue)' : 'var(--text-muted)',
          }}
        >
          {showAll ? 'Compact' : 'All Strikes'}
        </button>

        {/* Refresh */}
        <button
          onClick={() => fetchGex(symbol)}
          disabled={loading}
          className="text-xs px-2.5 py-1 rounded transition-colors"
          style={{ background: 'var(--bg-row)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
        >
          {loading ? '...' : '↻'}
        </button>

        {/* Last update */}
        {lastUpdate && (
          <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
            {lastUpdate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>

      {/* ── Error state ──────────────────────────────────────────────────── */}
      {error && (
        <div className="px-5 py-3 text-sm" style={{ color: '#f87171', background: 'rgba(248,113,113,0.08)', borderBottom: '1px solid var(--border)' }}>
          ⚠ {error}
        </div>
      )}

      {/* ── Summary cards ────────────────────────────────────────────────── */}
      {data && (
        <div
          className="flex gap-3 px-5 py-3 shrink-0 flex-wrap"
          style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)' }}
        >
          {/* Underlying */}
          <SummaryCard
            label="UNDERLYING"
            value={`$${fmt(data.underlying)}`}
            color="var(--text-primary)"
          />

          {/* Net GEX */}
          <SummaryCard
            label="NET GEX"
            value={fmtMM(data.net_gex_mm)}
            color={data.net_gex_mm >= 0 ? '#4ade80' : '#f87171'}
          />

          {/* Regime */}
          <SummaryCard
            label="REGIME"
            value={data.gamma_regime}
            sub={isPositive ? '▲ STABLE · MEAN REVERT' : '▼ VOLATILE · TREND'}
            color={isPositive ? '#4ade80' : '#f87171'}
          />

          {/* Call Wall */}
          <SummaryCard
            label="CALL WALL"
            value={`$${fmt(data.call_wall)}`}
            sub="RESISTANCE"
            color="#f87171"
          />

          {/* Put Wall */}
          <SummaryCard
            label="PUT WALL"
            value={`$${fmt(data.put_wall)}`}
            sub="SUPPORT"
            color="#4ade80"
          />

          {/* Zero Gamma */}
          <SummaryCard
            label="ZERO GAMMA"
            value={data.zero_gamma_level != null ? `$${fmt(data.zero_gamma_level)}` : '—'}
            sub={
              data.zero_gamma_level != null
                ? data.underlying > data.zero_gamma_level
                  ? '↑ ABOVE FLIP'
                  : '↓ BELOW FLIP'
                : undefined
            }
            color={
              data.zero_gamma_level != null
                ? data.underlying > data.zero_gamma_level ? '#4ade80' : '#f87171'
                : 'var(--text-muted)'
            }
          />

          {/* Expected move */}
          {data.expected_move_pct != null && (
            <SummaryCard
              label="EXP. MOVE"
              value={`±${fmt(data.expected_move_pct)}%`}
              sub={data.expected_move_pts != null ? `±${fmt(data.expected_move_pts)} pts` : undefined}
              color="var(--accent-blue)"
            />
          )}
        </div>
      )}

      {/* ── Legend ───────────────────────────────────────────────────────── */}
      {data && (
        <div className="flex items-center gap-4 px-5 py-2 shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex items-center gap-1.5">
            <div style={{ width: 12, height: 12, background: '#f87171', borderRadius: 2 }} />
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>Call GEX (resistance)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div style={{ width: 12, height: 12, background: '#4ade80', borderRadius: 2 }} />
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>Put GEX (support)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div style={{ width: 12, height: 4, background: 'var(--accent-blue)', borderRadius: 2 }} />
            <span className="text-xs" style={{ color: 'var(--text-muted)' }}>ATM price</span>
          </div>
          {data.zero_gamma_level != null && (
            <div className="flex items-center gap-1.5">
              <div style={{ width: 12, height: 2, background: '#fbbf24', borderRadius: 2, borderTop: '2px dashed #fbbf24' }} />
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>Zero gamma flip</span>
            </div>
          )}
          <div style={{ flex: 1 }} />
          <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
            All expiries · {data.expiries.slice(0, 4).join(' · ')}
          </span>
        </div>
      )}

      {/* ── Loading placeholder ───────────────────────────────────────────── */}
      {loading && !data && (
        <div className="flex items-center justify-center flex-1" style={{ color: 'var(--text-dim)' }}>
          <span className="text-sm">Loading option chain…</span>
        </div>
      )}

      {/* ── GEX Bar Chart ────────────────────────────────────────────────── */}
      {data && (
        <div className="flex-1 overflow-y-auto min-h-0 px-5 py-3">
          <div style={{ fontFamily: 'var(--font-mono, monospace)' }}>
            {visibleRows.map((row) => {
              const callBarPct = (row.call_gex_mm / maxAbsGex) * 100
              const putBarPct  = (row.put_gex_mm  / maxAbsGex) * 100
              const isAtm      = row.is_atm
              const isCallWall = row.is_call_wall
              const isPutWall  = row.is_put_wall
              const isFlip     = row.is_zero_gamma

              return (
                <div key={row.strike}>
                  {/* Zero gamma flip indicator */}
                  {isFlip && (
                    <div
                      className="flex items-center gap-2 my-1"
                      style={{ borderTop: '1px dashed #fbbf24', opacity: 0.7 }}
                    >
                      <span className="text-xs px-1" style={{ color: '#fbbf24', background: 'var(--bg-main)' }}>
                        ── ZERO GAMMA FLIP ${data.zero_gamma_level?.toFixed(2)} ──
                      </span>
                    </div>
                  )}

                  {/* ATM marker */}
                  {isAtm && (
                    <div
                      className="my-0.5"
                      style={{ borderTop: '1px solid var(--accent-blue)', opacity: 0.5 }}
                    />
                  )}

                  <div
                    className="flex items-center gap-2 py-0.5 rounded"
                    style={{
                      background: isAtm
                        ? 'rgba(59,130,246,0.06)'
                        : isCallWall
                        ? 'rgba(248,113,113,0.04)'
                        : isPutWall
                        ? 'rgba(74,222,128,0.04)'
                        : 'transparent',
                      minHeight: 22,
                    }}
                  >
                    {/* Strike label + badges */}
                    <div className="flex items-center gap-1 shrink-0" style={{ width: 140 }}>
                      <span
                        className="text-xs tabular-nums"
                        style={{
                          color: isAtm
                            ? 'var(--accent-blue)'
                            : isCallWall
                            ? '#f87171'
                            : isPutWall
                            ? '#4ade80'
                            : 'var(--text-muted)',
                          fontWeight: (isAtm || isCallWall || isPutWall) ? 700 : 400,
                        }}
                      >
                        ${row.strike.toFixed(row.strike >= 1000 ? 0 : 1)}
                      </span>
                      {isAtm && (
                        <span className="text-xs px-1 rounded" style={{ background: 'rgba(59,130,246,0.15)', color: 'var(--accent-blue)', fontSize: 9 }}>
                          ATM
                        </span>
                      )}
                      {isCallWall && (
                        <span className="text-xs px-1 rounded" style={{ background: 'rgba(248,113,113,0.15)', color: '#f87171', fontSize: 9 }}>
                          CALL WALL
                        </span>
                      )}
                      {isPutWall && (
                        <span className="text-xs px-1 rounded" style={{ background: 'rgba(74,222,128,0.15)', color: '#4ade80', fontSize: 9 }}>
                          PUT WALL
                        </span>
                      )}
                    </div>

                    {/* Bar chart: put bar (green, left) + center line + call bar (red, right) */}
                    <div className="flex items-center flex-1" style={{ height: 14 }}>
                      {/* Put bar — goes LEFT from center */}
                      <div className="flex items-center justify-end" style={{ flex: 1 }}>
                        <div
                          style={{
                            width: `${Math.min(putBarPct, 100)}%`,
                            height: 10,
                            background: '#4ade80',
                            borderRadius: '3px 0 0 3px',
                            opacity: isPutWall ? 1 : 0.65,
                            transition: 'width 0.3s ease',
                          }}
                        />
                      </div>
                      {/* Center spine */}
                      <div style={{ width: 1, height: 14, background: 'var(--border)', flexShrink: 0 }} />
                      {/* Call bar — goes RIGHT from center */}
                      <div className="flex items-center justify-start" style={{ flex: 1 }}>
                        <div
                          style={{
                            width: `${Math.min(callBarPct, 100)}%`,
                            height: 10,
                            background: '#f87171',
                            borderRadius: '0 3px 3px 0',
                            opacity: isCallWall ? 1 : 0.65,
                            transition: 'width 0.3s ease',
                          }}
                        />
                      </div>
                    </div>

                    {/* GEX values */}
                    <div
                      className="text-xs tabular-nums shrink-0 flex gap-3"
                      style={{ width: 180, color: 'var(--text-dim)', textAlign: 'right' }}
                    >
                      <span style={{ color: '#4ade80', flex: 1, textAlign: 'right' }}>
                        +{fmt(row.put_gex_mm, 1)}M
                      </span>
                      <span style={{ color: '#f87171', flex: 1, textAlign: 'right' }}>
                        +{fmt(row.call_gex_mm, 1)}M
                      </span>
                      <span
                        style={{
                          color: row.net_gex_mm >= 0 ? '#4ade80' : '#f87171',
                          flex: 1,
                          textAlign: 'right',
                        }}
                      >
                        {fmtMM(row.net_gex_mm)}
                      </span>
                    </div>
                  </div>

                  {isAtm && (
                    <div
                      className="my-0.5"
                      style={{ borderTop: '1px solid var(--accent-blue)', opacity: 0.5 }}
                    />
                  )}
                </div>
              )
            })}
          </div>

          {/* Column header (sticky-like, shown at bottom for reference) */}
          <div
            className="flex items-center gap-2 mt-3 pt-2 text-xs"
            style={{ borderTop: '1px solid var(--border)', color: 'var(--text-dim)' }}
          >
            <div style={{ width: 140 }}>STRIKE</div>
            <div style={{ flex: 1, textAlign: 'center' }}>
              ◄ PUT GEX (support) ·············· CALL GEX (resistance) ►
            </div>
            <div className="flex gap-3" style={{ width: 180, textAlign: 'right' }}>
              <span style={{ flex: 1, textAlign: 'right' }}>PUT $M</span>
              <span style={{ flex: 1, textAlign: 'right' }}>CALL $M</span>
              <span style={{ flex: 1, textAlign: 'right' }}>NET $M</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Summary card sub-component ─────────────────────────────────────────────
function SummaryCard({
  label, value, sub, color,
}: {
  label: string
  value: string
  sub?: string
  color?: string
}) {
  return (
    <div
      className="flex flex-col px-3 py-2 rounded"
      style={{ background: 'var(--bg-row)', border: '1px solid var(--border)', minWidth: 90 }}
    >
      <span className="text-xs font-semibold tracking-wider" style={{ color: 'var(--text-dim)', fontSize: 9, letterSpacing: '0.12em' }}>
        {label}
      </span>
      <span className="font-bold tabular-nums" style={{ color: color ?? 'var(--text-primary)', fontSize: 15 }}>
        {value}
      </span>
      {sub && (
        <span className="text-xs mt-0.5" style={{ color: 'var(--text-dim)', fontSize: 10 }}>
          {sub}
        </span>
      )}
    </div>
  )
}
