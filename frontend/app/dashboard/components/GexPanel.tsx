'use client'

import { useEffect, useState, useCallback, useRef } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

// DB-tracked index symbols — these come from Supabase
const INDEX_SYMBOLS = ['SPX', 'NDX', 'RUT'] as const
type IndexSymbol = typeof INDEX_SYMBOLS[number]

// Expiry layer tabs
type Layer = 'all' | 'ex_next' | 'monthly'
const LAYERS: { key: Layer; label: string; sub: string }[] = [
  { key: 'all',      label: 'All Exp',   sub: 'Full picture including 0DTE' },
  { key: 'ex_next',  label: 'Ex-Next',   sub: 'Excluding nearest expiry' },
  { key: 'monthly',  label: 'Monthly',   sub: '3rd-Friday structural only' },
]

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
  vix_ref: number | null
  iv_environment: string
  nearest_expiry: string
  nearest_dte: number | null
  expiries?: string[]        // present on live compute; absent when served from DB
  expiry_type?: '0DTE' | 'QUARTERLY' | 'MONTHLY' | 'WEEKLY' | 'DAILY'
  is_post_expiry_monday?: boolean
  source: 'baseline' | 'intraday' | 'live' | 'transient_cache'
  captured_at?: string

  // All-exp layer
  net_gex_mm: number
  gamma_regime: 'POSITIVE' | 'NEGATIVE'
  call_wall: number
  put_wall: number
  zero_gamma: number | null
  expected_move_pct: number | null
  expected_move_pts: number | null

  // Ex-next layer
  net_gex_ex_next_mm: number
  call_wall_ex_next: number
  put_wall_ex_next: number
  zero_gamma_ex_next: number | null

  // Monthly layer
  net_gex_monthly_mm: number
  call_wall_monthly: number
  put_wall_monthly: number
  zero_gamma_monthly: number | null

  strikes: StrikeRow[]           // all-expiry layer
  strikes_ex_next?: StrikeRow[]  // excluding nearest expiry
  strikes_monthly?: StrikeRow[]  // monthly only
  strike_count: number
  pc_ratio?: number | null
  underlying_vwap?: number | null
  underlying_open?: number | null
  underlying_prev_close?: number | null
  delta_distribution?: {
    calls:         Record<string, number>
    puts:          Record<string, number>
    call_oi:       number
    put_oi:        number
    pc_ratio_oi?:  number | null
    pc_ratio_vol?: number | null
  } | null
}

// ── Helpers ────────────────────────────────────────────────────────────────
function fmt(n: number | null | undefined, d = 2) {
  if (n == null) return '—'
  return n.toFixed(d)
}
function fmtMM(n: number | null | undefined) {
  if (n == null) return '—'
  const s = n >= 0 ? '+' : ''
  const a = Math.abs(n)
  if (a >= 1000) return `${s}${(n / 1000).toFixed(1)}B`
  return `${s}${n.toFixed(1)}M`
}
function ivColor(env: string) {
  if (env === 'LOW')     return '#4ade80'
  if (env === 'NORMAL')  return '#94a3b8'
  if (env === 'HIGH')    return '#fbbf24'
  if (env === 'EXTREME') return '#f87171'
  return '#94a3b8'
}
function layerValues(data: GexData, layer: Layer) {
  if (layer === 'ex_next') return {
    net: data.net_gex_ex_next_mm,
    call_wall: data.call_wall_ex_next,
    put_wall: data.put_wall_ex_next,
    zero_gamma: data.zero_gamma_ex_next,
    regime: data.net_gex_ex_next_mm >= 0 ? 'POSITIVE' : 'NEGATIVE',
  }
  if (layer === 'monthly') return {
    net: data.net_gex_monthly_mm,
    call_wall: data.call_wall_monthly,
    put_wall: data.put_wall_monthly,
    zero_gamma: data.zero_gamma_monthly,
    regime: data.net_gex_monthly_mm >= 0 ? 'POSITIVE' : 'NEGATIVE',
  }
  return {
    net: data.net_gex_mm,
    call_wall: data.call_wall,
    put_wall: data.put_wall,
    zero_gamma: data.zero_gamma,
    regime: data.gamma_regime,
  }
}

// ── Component ──────────────────────────────────────────────────────────────
export default function GexPanel() {
  const [activeSymbol, setActiveSymbol] = useState<string>('SPX')
  const [customInput,  setCustomInput]  = useState('')
  const [layer, setLayer]               = useState<Layer>('all')
  const [data,  setData]                = useState<GexData | null>(null)
  const [loading, setLoading]           = useState(false)
  const [error,   setError]             = useState<string | null>(null)
  const [lastFetch, setLastFetch]       = useState<Date | null>(null)
  const [showAll,          setShowAll]          = useState(false)
  const [showDeltaHelp,    setShowDeltaHelp]    = useState(false)
  const [elapsed,          setElapsed]          = useState(0)      // ms while loading
  const [fetchMs,   setFetchMs]         = useState<number | null>(null)  // final duration
  const refreshRef  = useRef<ReturnType<typeof setInterval> | null>(null)
  const timerRef    = useRef<ReturnType<typeof setInterval> | null>(null)
  const fetchStart  = useRef<number>(0)

  const fetchGex = useCallback(async (sym: string) => {
    // Start timer
    fetchStart.current = performance.now()
    setElapsed(0)
    setFetchMs(null)
    setLoading(true)
    setError(null)
    if (timerRef.current) clearInterval(timerRef.current)
    timerRef.current = setInterval(() => {
      setElapsed(Math.round(performance.now() - fetchStart.current))
    }, 100)

    try {
      const res  = await fetch(`${API_URL}/api/gex/${encodeURIComponent(sym)}?strike_count=100`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      if (json.error) throw new Error(json.error)
      setData(json as GexData)
      setLastFetch(new Date())
      setFetchMs(Math.round(performance.now() - fetchStart.current))
    } catch (e: any) {
      setError(e.message ?? 'Failed to load GEX')
    } finally {
      setLoading(false)
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    }
  }, [])

  useEffect(() => {
    fetchGex(activeSymbol)
    if (refreshRef.current) clearInterval(refreshRef.current)
    // Index symbols auto-refresh every 5 min (DB is updated every 15 min by backend)
    // Transient symbols refresh every 5 min too
    refreshRef.current = setInterval(() => fetchGex(activeSymbol), 5 * 60 * 1000)
    return () => { if (refreshRef.current) clearInterval(refreshRef.current) }
  }, [activeSymbol, fetchGex])

  function selectSymbol(sym: string) {
    const s = sym.toUpperCase().trim()
    if (s) { setData(null); setActiveSymbol(s); setCustomInput('') }
  }

  function handleCustomSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (customInput.trim()) selectSymbol(customInput)
  }

  // ── Active layer values ──────────────────────────────────────────────────
  const lv = data ? layerValues(data, layer) : null

  // ── Strike rows for bar chart — switch by active layer ──────────────────
  const strikes = (() => {
    if (!data) return []
    if (layer === 'ex_next')  return data.strikes_ex_next ?? data.strikes ?? []
    if (layer === 'monthly')  return data.strikes_monthly ?? data.strikes ?? []
    return data.strikes ?? []
  })()
  const maxAbs  = strikes.reduce(
    (m, r) => Math.max(m, r.call_gex_mm, r.put_gex_mm), 1
  )
  const atmIdx = strikes.findIndex(r => r.is_atm)

  // Detect weekend / pre-open OI blackout: OCC publishes OI after each session close;
  // Schwab zeroes it out on weekends. If every strike shows zero GEX, data is unavailable.
  const allZeroOI = data != null
    && data.strikes.length > 0
    && data.strikes.every(r => r.call_gex_mm === 0 && r.put_gex_mm === 0)

  // Detect when a wall is outside the fetched strike range
  const strikeSet = new Set(strikes.map(r => r.strike))
  const callWallInRange = lv?.call_wall != null && strikeSet.has(lv.call_wall)
  const putWallInRange  = lv?.put_wall  != null && strikeSet.has(lv.put_wall)

  const visibleRows = showAll
    ? [...strikes].reverse()
    : [...strikes].reverse().filter((_, i, arr) => {
        const atmRevIdx = arr.length - 1 - atmIdx
        return Math.abs(i - atmRevIdx) <= 22
      })

  // ── Source badge ─────────────────────────────────────────────────────────
  const sourceLabel = (() => {
    if (!data) return ''
    if (data.source === 'baseline')      return '📊 Official baseline'
    if (data.source === 'intraday')      return '⚡ Intraday estimate'
    if (data.source === 'live')          return '🔴 Live (transient)'
    if (data.source === 'transient_cache') return '⏱ Cached (transient)'
    return ''
  })()

  const capturedTime = (() => {
    if (!data?.captured_at) return lastFetch?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) ?? ''
    return new Date(data.captured_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  })()

  return (
    <div className="flex flex-col h-full min-h-0" style={{ background: 'var(--bg-main)', color: 'var(--text-primary)' }}>

      {/* ── Top bar ──────────────────────────────────────────────────────── */}
      <div
        className="flex items-center gap-2 px-5 py-2.5 shrink-0 flex-wrap"
        style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)' }}
      >
        <span className="text-xs font-bold tracking-widest" style={{ color: 'var(--text-muted)', letterSpacing: '0.18em' }}>GEX</span>
        <div style={{ width: 1, height: 14, background: 'var(--border)' }} />

        {/* Index symbol tabs */}
        {INDEX_SYMBOLS.map(sym => (
          <button
            key={sym}
            onClick={() => selectSymbol(sym)}
            className="text-xs px-3 py-1 rounded font-mono font-semibold transition-colors"
            style={{
              background: activeSymbol === sym ? 'var(--accent-blue-dim)' : 'var(--bg-row)',
              color:      activeSymbol === sym ? 'var(--accent-blue)' : 'var(--text-muted)',
              border:     `1px solid ${activeSymbol === sym ? 'var(--accent-blue)' : 'var(--border)'}`,
            }}
          >
            {sym}
            {sym === 'SPX' && <span className="ml-1 text-xs opacity-60">0DTE</span>}
          </button>
        ))}

        <div style={{ width: 1, height: 14, background: 'var(--border)' }} />

        {/* Custom / transient symbol input */}
        <form onSubmit={handleCustomSubmit} className="flex items-center gap-1">
          <input
            value={customInput}
            onChange={e => setCustomInput(e.target.value.toUpperCase())}
            placeholder="AMZN…"
            maxLength={10}
            className="text-xs px-2 py-1 rounded font-mono w-20 outline-none"
            style={{ background: 'var(--bg-row)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
          />
          <button
            type="submit"
            className="text-xs px-2 py-1 rounded"
            style={{ background: 'var(--bg-row)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
          >
            ↗
          </button>
        </form>

        {/* Transient symbol indicator */}
        {!INDEX_SYMBOLS.includes(activeSymbol as IndexSymbol) && (
          <span className="text-xs px-2 py-0.5 rounded" style={{ background: 'rgba(251,191,36,0.1)', color: '#fbbf24', border: '1px solid rgba(251,191,36,0.2)' }}>
            {activeSymbol} · transient
          </span>
        )}

        <div style={{ flex: 1 }} />

        {/* Source + time */}
        {data && (
          <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
            {sourceLabel} · {capturedTime}
          </span>
        )}

        {/* Fetch duration */}
        {fetchMs != null && !loading && (
          <span className="text-xs tabular-nums" style={{ color: 'var(--text-dim)' }}>
            {fetchMs < 1000 ? `${fetchMs}ms` : `${(fetchMs / 1000).toFixed(1)}s`}
          </span>
        )}

        {/* Refresh */}
        <button
          onClick={() => fetchGex(activeSymbol)}
          disabled={loading}
          className="text-xs px-2 py-1 rounded"
          style={{ background: 'var(--bg-row)', border: '1px solid var(--border)', color: 'var(--text-muted)' }}
        >
          {loading ? '…' : '↻'}
        </button>
      </div>

      {/* ── Error ────────────────────────────────────────────────────────── */}
      {error && (
        <div className="px-5 py-2 text-xs" style={{ color: '#f87171', background: 'rgba(248,113,113,0.06)', borderBottom: '1px solid var(--border)' }}>
          ⚠ {error}
        </div>
      )}

      {/* ── VIX + IV context bar ─────────────────────────────────────────── */}
      {data && (
        <div
          className="flex items-center gap-4 px-5 py-2 shrink-0"
          style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)' }}
        >
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold" style={{ color: 'var(--text-dim)', letterSpacing: '0.1em' }}>FEAR INDEX</span>
            <span className="text-sm font-bold tabular-nums" style={{ color: ivColor(data.iv_environment) }}>
              {data.vix_ref != null ? data.vix_ref.toFixed(2) : '—'}
            </span>
            <span
              className="text-xs px-1.5 py-0.5 rounded font-semibold"
              style={{ background: `${ivColor(data.iv_environment)}18`, color: ivColor(data.iv_environment), fontSize: 10 }}
            >
              {data.iv_environment === 'UNKNOWN' ? 'VIX' : `${data.iv_environment} IV`}
            </span>
            <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
              {data.iv_environment === 'LOW'     && '— Walls very sticky'}
              {data.iv_environment === 'NORMAL'  && '— Walls reliable'}
              {data.iv_environment === 'HIGH'    && '— Walls weakening'}
              {data.iv_environment === 'EXTREME' && '— Walls unreliable'}
            </span>
          </div>
          <div style={{ width: 1, height: 14, background: 'var(--border)' }} />
          <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
            Nearest expiry: <span style={{ color: 'var(--text-muted)' }}>{data.nearest_expiry}</span>
            {data.nearest_dte != null && (
              <span style={{ color: data.nearest_dte === 0 ? '#f87171' : 'var(--text-dim)' }}>
                {' '}({data.nearest_dte === 0 ? '0DTE' : `${data.nearest_dte}d`})
              </span>
            )}
          </span>

          {/* Expiry type badge */}
          {data.expiry_type && data.expiry_type !== 'DAILY' && data.expiry_type !== 'WEEKLY' && (
            <ExpiryBadge type={data.expiry_type} />
          )}

          {/* Post-expiry Monday warning */}
          {data.is_post_expiry_monday && (
            <span
              className="text-xs px-2 py-0.5 rounded font-semibold"
              style={{ background: 'rgba(251,191,36,0.12)', color: '#fbbf24', border: '1px solid rgba(251,191,36,0.25)', fontSize: 10 }}
              title="Post-expiry Monday — dealers re-hedge from scratch. Flows can be erratic until ~10:30 AM ET."
            >
              ⚠ POST-EXPIRY MON
            </span>
          )}

          <div style={{ flex: 1 }} />
          {data.expiries && data.expiries.length > 0 && (
            <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
              {data.expiries.slice(0, 4).join(' · ')}
            </span>
          )}
        </div>
      )}

      {/* ── Expiry layer tabs ─────────────────────────────────────────────── */}
      {data && (
        <div
          className="flex items-center gap-1 px-5 py-2 shrink-0"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          {LAYERS.map(l => (
            <button
              key={l.key}
              onClick={() => setLayer(l.key)}
              title={l.sub}
              className="text-xs px-3 py-1 rounded transition-colors"
              style={{
                background: layer === l.key ? 'var(--accent-blue-dim)' : 'var(--bg-row)',
                color:      layer === l.key ? 'var(--accent-blue)' : 'var(--text-muted)',
                border:     `1px solid ${layer === l.key ? 'var(--accent-blue)' : 'var(--border)'}`,
                fontWeight: layer === l.key ? 600 : 400,
              }}
            >
              {l.label}
            </button>
          ))}
          <span className="text-xs ml-2" style={{ color: 'var(--text-dim)' }}>
            {LAYERS.find(l => l.key === layer)?.sub}
          </span>
        </div>
      )}

      {/* ── Summary cards ────────────────────────────────────────────────── */}
      {data && lv && (
        <div
          className="flex gap-2 px-5 py-2.5 shrink-0 flex-wrap"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          <Card label="UNDERLYING" value={`$${fmt(data.underlying)}`} />
          <Card
            label="NET GEX"
            value={fmtMM(lv.net)}
            color={lv.net >= 0 ? '#4ade80' : '#f87171'}
          />
          <Card
            label="REGIME"
            value={lv.regime}
            sub={lv.regime === 'POSITIVE' ? '▲ Damping · tight range' : '▼ Amplifying · trend'}
            color={lv.regime === 'POSITIVE' ? '#4ade80' : '#f87171'}
          />
          <Card
            label="CALL WALL"
            value={lv.call_wall != null ? `$${fmt(lv.call_wall, lv.call_wall >= 1000 ? 0 : 1)}` : '—'}
            sub={!callWallInRange && lv.call_wall != null ? '↑ above chart range' : 'resistance'}
            color="#f87171"
          />
          <Card
            label="PUT WALL"
            value={lv.put_wall != null ? `$${fmt(lv.put_wall, lv.put_wall >= 1000 ? 0 : 1)}` : '—'}
            sub={!putWallInRange && lv.put_wall != null ? '↓ below chart range' : 'support'}
            color="#4ade80"
          />
          <Card
            label="ZERO GAMMA"
            value={lv.zero_gamma != null ? `$${fmt(lv.zero_gamma, lv.zero_gamma >= 1000 ? 0 : 1)}` : '—'}
            sub={
              lv.zero_gamma != null && data.underlying > lv.zero_gamma
                ? '↑ price above flip'
                : lv.zero_gamma != null
                ? '↓ price below flip'
                : undefined
            }
            color={
              lv.zero_gamma != null
                ? data.underlying > lv.zero_gamma ? '#4ade80' : '#f87171'
                : 'var(--text-muted)'
            }
          />
          {data.expected_move_pct != null && layer === 'all' && (
            <Card
              label="EXP MOVE (1D)"
              value={`±${fmt(data.expected_move_pct)}%`}
              sub={data.expected_move_pts != null ? `±${fmt(data.expected_move_pts)} pts` : undefined}
              color="var(--accent-blue)"
            />
          )}
        </div>
      )}

      {/* ── Options flow stats ───────────────────────────────────────────── */}
      {data && (data.pc_ratio != null || data.underlying_vwap != null || data.delta_distribution) && (
        <div
          className="px-5 py-2 shrink-0"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          {/* Top row: P/C (OI), OI totals, price context */}
          <div className="flex items-center gap-4 mb-2 flex-wrap">
            <span className="text-xs font-semibold" style={{ color: 'var(--text-dim)', letterSpacing: '0.1em' }}>OPTIONS FLOW</span>
            <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
              <span style={{ color: '#4ade80', fontWeight: 700 }}>C</span>
              <span style={{ color: 'var(--text-dim)' }}> / </span>
              <span style={{ color: '#f87171', fontWeight: 700 }}>P</span>
              <span style={{ color: 'var(--text-dim)' }}> delta distribution</span>
            </span>
            {data.delta_distribution && (
              <>
                {/* OI-based P/C — accumulated positioning (last close snapshot) */}
                {data.delta_distribution.pc_ratio_oi != null && (
                  <span className="text-xs" title="P/C by Open Interest — accumulated positioning from the last close. Call-heavy = longs built up during the run-up.">
                    <span style={{ color: 'var(--text-dim)' }}>P/C OI </span>
                    <span className="font-bold tabular-nums" style={{
                      color: data.delta_distribution.pc_ratio_oi > 1.2 ? '#f87171'
                           : data.delta_distribution.pc_ratio_oi < 0.8 ? '#4ade80'
                           : 'var(--text-muted)'
                    }}>
                      {data.delta_distribution.pc_ratio_oi.toFixed(2)}
                    </span>
                  </span>
                )}
                {/* Vol-based P/C — today's activity */}
                {data.delta_distribution.pc_ratio_vol != null && (
                  <span className="text-xs" title="P/C by today's volume — what traders actually bought/sold today. Put-heavy on crash days.">
                    <span style={{ color: 'var(--text-dim)' }}>Vol </span>
                    <span className="font-bold tabular-nums" style={{
                      color: data.delta_distribution.pc_ratio_vol > 1.2 ? '#f87171'
                           : data.delta_distribution.pc_ratio_vol < 0.8 ? '#4ade80'
                           : 'var(--text-muted)'
                    }}>
                      {data.delta_distribution.pc_ratio_vol.toFixed(2)}
                    </span>
                  </span>
                )}
                <span style={{ width: 1, height: 12, background: 'var(--border)', flexShrink: 0 }} />
                <span className="tabular-nums" style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                  <span style={{ color: '#4ade80', fontWeight: 600 }}>▲ </span>
                  <span style={{ color: '#4ade80', fontWeight: 700 }}>{(data.delta_distribution.call_oi / 1000).toFixed(0)}K</span>
                  <span style={{ color: 'var(--text-dim)' }}> OI</span>
                </span>
                <span className="tabular-nums" style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                  <span style={{ color: '#f87171', fontWeight: 600 }}>▼ </span>
                  <span style={{ color: '#f87171', fontWeight: 700 }}>{(data.delta_distribution.put_oi / 1000).toFixed(0)}K</span>
                  <span style={{ color: 'var(--text-dim)' }}> OI</span>
                </span>
              </>
            )}
            {data.underlying_prev_close != null && (
              <span className="text-xs tabular-nums" style={{ color: 'var(--text-dim)' }}>
                Prev Close <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>${fmt(data.underlying_prev_close)}</span>
              </span>
            )}
            {data.underlying_open != null && (
              <span className="text-xs tabular-nums" style={{ color: 'var(--text-dim)' }}>
                Open <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>${fmt(data.underlying_open)}</span>
              </span>
            )}
            {data.underlying_vwap != null && (
              <span className="text-xs tabular-nums" style={{ color: 'var(--text-dim)' }} title="VWAP computed from 1-minute bars for the current session">
                VWAP <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>${fmt(data.underlying_vwap)}</span>
                {data.underlying > data.underlying_vwap
                  ? <span style={{ color: '#4ade80' }}> ↑ above</span>
                  : <span style={{ color: '#f87171' }}> ↓ below</span>
                }
              </span>
            )}
            <div style={{ flex: 1 }} />
            <button
              onClick={() => setShowDeltaHelp(v => !v)}
              className="text-xs px-2 py-0.5 rounded transition-colors"
              style={{
                background: showDeltaHelp ? 'rgba(251,191,36,0.12)' : 'transparent',
                color:      '#fbbf24',
                border:     `1px solid ${showDeltaHelp ? 'rgba(251,191,36,0.6)' : 'rgba(251,191,36,0.35)'}`,
                fontWeight: 600,
                whiteSpace: 'nowrap',
              }}
            >
              Δ Explained
            </button>
          </div>

          {/* Delta distribution — 5 bucket columns */}
          {data.delta_distribution && (() => {
            const BUCKETS = ['0_20', '21_40', '41_60', '61_80', '81_100'] as const
            const LABELS  = ['0–20Δ', '21–40Δ', '41–60Δ', '61–80Δ', '81–100Δ']
            const allPcts = BUCKETS.flatMap(b => [
              data.delta_distribution!.calls[b] ?? 0,
              data.delta_distribution!.puts[b]  ?? 0,
            ])
            const maxPct = Math.max(...allPcts, 1)
            return (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '0 10px', marginTop: 6 }}>
                {BUCKETS.map((b, i) => {
                  const cPct = data.delta_distribution!.calls[b] ?? 0
                  const pPct = data.delta_distribution!.puts[b]  ?? 0
                  return (
                    <div key={b} className="flex flex-col gap-1.5">
                      {/* Bucket label */}
                      <div style={{ fontSize: 9, color: '#94a3b8', letterSpacing: '0.05em', textAlign: 'center' }}>
                        {LABELS[i]}
                      </div>
                      {/* Call bar + % */}
                      <div className="flex flex-col gap-0.5">
                        <div style={{ width: '100%', height: 5, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
                          <div style={{ width: `${(cPct / maxPct) * 100}%`, height: '100%', background: '#4ade80', opacity: 0.85 }} />
                        </div>
                        <div style={{ fontSize: 13, color: '#4ade80', textAlign: 'center', fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
                          {cPct.toFixed(0)}%
                        </div>
                      </div>
                      {/* Put bar + % */}
                      <div className="flex flex-col gap-0.5">
                        <div style={{ width: '100%', height: 5, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
                          <div style={{ width: `${(pPct / maxPct) * 100}%`, height: '100%', background: '#f87171', opacity: 0.85 }} />
                        </div>
                        <div style={{ fontSize: 13, color: '#f87171', textAlign: 'center', fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
                          {pPct.toFixed(0)}%
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )
          })()}

          {/* ── Delta explanation panel ─────────────────────────────────── */}
          {showDeltaHelp && (
            <div
              className="mt-3 rounded"
              style={{ background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.18)', padding: '10px 12px' }}
            >
              <div className="text-xs font-semibold mb-2" style={{ color: '#818cf8', letterSpacing: '0.08em' }}>
                DELTA RANGES — WHAT THEY MEAN
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '0 10px' }}>
                {[
                  {
                    range: '0–20Δ',
                    name: 'Far OTM',
                    call: 'Cheap speculative bets. Buyers expect a large move up. Low probability, high reward if it moves.',
                    put:  'Cheap insurance / tail-risk hedges. Far below market — protect against a crash.',
                  },
                  {
                    range: '21–40Δ',
                    name: 'OTM',
                    call: 'Directional plays with moderate premium. Retail buying for leveraged upside exposure.',
                    put:  'Active hedging zone. Funds and traders buying protection against a moderate pullback.',
                  },
                  {
                    range: '41–60Δ',
                    name: 'ATM zone',
                    call: 'Highest gamma — most sensitive to price movement. Near 50Δ = reacts 1:1 with stock.',
                    put:  'ATM puts decay fast but are most reactive. Often used for short-term directional plays.',
                  },
                  {
                    range: '61–80Δ',
                    name: 'ITM',
                    call: 'Behaves like leveraged stock. Less time value, more intrinsic. Institutional / professional.',
                    put:  'Deep hedges or synthetic short positions. High delta = strong downside protection.',
                  },
                  {
                    range: '81–100Δ',
                    name: 'Deep ITM',
                    call: 'Stock replacement strategy. Very low time value. Used in covered calls or LEAPS.',
                    put:  'Near-certain payout. Essentially a short stock with limited downside risk.',
                  },
                ].map(({ range, name, call, put }) => (
                  <div key={range} className="flex flex-col gap-1">
                    <div style={{ fontSize: 9, color: '#94a3b8', letterSpacing: '0.05em', textAlign: 'center' }}>
                      {range}
                    </div>
                    <div className="text-center text-xs font-semibold" style={{ color: '#818cf8', fontSize: 9 }}>{name}</div>
                    <div style={{ fontSize: 9, color: '#4ade80', lineHeight: 1.45, opacity: 0.9 }}>{call}</div>
                    <div style={{ fontSize: 9, color: '#f87171', lineHeight: 1.45, opacity: 0.9, marginTop: 2 }}>{put}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── OI unavailable banner (weekends / pre-open blackout) ─────────── */}
      {allZeroOI && (
        <div
          className="flex items-start gap-3 px-5 py-3 shrink-0"
          style={{
            borderBottom: '1px solid var(--border)',
            background: 'rgba(251,191,36,0.05)',
            borderLeft: '3px solid rgba(251,191,36,0.4)',
          }}
        >
          <span style={{ fontSize: 18 }}>🌙</span>
          <div>
            <div className="text-xs font-semibold" style={{ color: '#fbbf24', marginBottom: 2 }}>
              Open Interest unavailable
            </div>
            <div className="text-xs" style={{ color: 'var(--text-dim)', lineHeight: 1.5 }}>
              OCC (Options Clearing Corporation) publishes OI figures after each session close.
              Schwab resets all OI to zero over the weekend — this is expected, not a bug.
              GEX data will populate after <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>Monday's pre-market open</span>.
            </div>
          </div>
        </div>
      )}

      {/* ── Legend ───────────────────────────────────────────────────────── */}
      {data && !allZeroOI && (
        <div className="flex items-center gap-4 px-5 py-1.5 shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <LegendDot color="#4ade80" label="Put GEX → support" />
          <LegendDot color="#f87171" label="Call GEX → resistance" />
          <LegendLine color="var(--accent-blue)" label="ATM price" />
          <LegendLine color="#fbbf24" dashed label="Zero gamma flip" />
          <div style={{ flex: 1 }} />
          <button
            onClick={() => setShowAll(v => !v)}
            className="text-xs px-2 py-0.5 rounded"
            style={{ background: 'var(--bg-row)', border: '1px solid var(--border)', color: 'var(--text-dim)' }}
          >
            {showAll ? '± compact' : 'all strikes'}
          </button>
        </div>
      )}

      {/* ── Loading spinner ───────────────────────────────────────────────── */}
      {loading && !data && (
        <div className="flex-1 flex flex-col items-center justify-center gap-3">
          <div style={{
            width: 32, height: 32, borderRadius: '50%',
            border: '3px solid var(--border)',
            borderTopColor: 'var(--accent-blue)',
            animation: 'gex-spin 0.8s linear infinite',
          }} />
          <div className="flex flex-col items-center gap-1">
            <span className="text-sm" style={{ color: 'var(--text-muted)' }}>
              Fetching {activeSymbol} option chain…
            </span>
            <span className="text-xs tabular-nums font-mono" style={{ color: 'var(--accent-blue)' }}>
              {elapsed < 1000 ? `${elapsed}ms` : `${(elapsed / 1000).toFixed(1)}s`}
            </span>
          </div>
          <style>{`@keyframes gex-spin { to { transform: rotate(360deg); } }`}</style>
        </div>
      )}

      {/* ── No-data placeholder (OI blackout) ────────────────────────────── */}
      {data && allZeroOI && (
        <div className="flex-1 flex flex-col items-center justify-center gap-3 px-8 py-12">
          <div style={{ fontSize: 11, color: 'var(--text-dim)', textAlign: 'center', maxWidth: 340, lineHeight: 1.7 }}>
            Gamma bars require Open Interest data — come back after Monday's open.<br />
            The greeks (gamma, delta) are still present in the chain; only OI is zeroed by the exchange.
          </div>
          {data.nearest_expiry && (
            <div
              className="text-xs px-3 py-2 rounded"
              style={{ background: 'var(--bg-row)', border: '1px solid var(--border)', color: 'var(--text-dim)' }}
            >
              Nearest expiry on record: <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>{data.nearest_expiry}</span>
              {data.nearest_dte != null && ` (${data.nearest_dte}d)`}
              {' · '}{data.strike_count} strikes fetched
            </div>
          )}
        </div>
      )}

      {/* ── Bar chart ────────────────────────────────────────────────────── */}
      {data && !allZeroOI && (
        <div className="flex-1 overflow-y-auto min-h-0 px-5 py-2">
          {/* Column headers */}
          <div
            className="flex items-center gap-2 mb-1 pb-1 text-xs sticky top-0"
            style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-main)', color: 'var(--text-dim)' }}
          >
            <div style={{ width: 150 }}>STRIKE</div>
            <div style={{ flex: 1, textAlign: 'center' }}>
              ◄ PUT GEX (support) ·················· CALL GEX (resistance) ►
            </div>
            <div style={{ width: 50, textAlign: 'right' }}>PUT $M</div>
            <div style={{ width: 55, textAlign: 'right' }}>CALL $M</div>
            <div style={{ width: 55, textAlign: 'right' }}>NET $M</div>
          </div>

          {visibleRows.map((row) => {
            const callPct = Math.min((row.call_gex_mm / maxAbs) * 100, 100)
            const putPct  = Math.min((row.put_gex_mm  / maxAbs) * 100, 100)
            const isWall  = row.is_call_wall || row.is_put_wall

            return (
              <div key={row.strike}>
                {/* Zero gamma flip line */}
                {row.is_zero_gamma && (
                  <div className="flex items-center gap-2 my-1" style={{ borderTop: '1px dashed #fbbf24', opacity: 0.75 }}>
                    <span
                      className="text-xs px-1"
                      style={{ color: '#fbbf24', background: 'var(--bg-main)', fontSize: 9, letterSpacing: '0.08em' }}
                    >
                      ── ZERO GAMMA {lv?.zero_gamma != null ? `$${fmt(lv.zero_gamma, lv.zero_gamma >= 1000 ? 0 : 1)}` : ''} ──
                    </span>
                  </div>
                )}

                {/* ATM line */}
                {row.is_atm && (
                  <div className="my-0.5" style={{ borderTop: '1px solid var(--accent-blue)', opacity: 0.4 }} />
                )}

                <div
                  className="flex items-center gap-2 py-0.5 rounded"
                  style={{
                    minHeight: 20,
                    background: row.is_atm
                      ? 'rgba(59,130,246,0.05)'
                      : row.is_call_wall
                      ? 'rgba(248,113,113,0.04)'
                      : row.is_put_wall
                      ? 'rgba(74,222,128,0.04)'
                      : 'transparent',
                  }}
                >
                  {/* Strike + badges */}
                  <div className="flex items-center gap-1 shrink-0" style={{ width: 150 }}>
                    <span
                      className="text-xs tabular-nums font-mono"
                      style={{
                        color: row.is_atm
                          ? 'var(--accent-blue)'
                          : row.is_call_wall ? '#f87171'
                          : row.is_put_wall  ? '#4ade80'
                          : 'var(--text-muted)',
                        fontWeight: isWall || row.is_atm ? 700 : 400,
                      }}
                    >
                      ${row.strike >= 1000 ? row.strike.toFixed(0) : row.strike.toFixed(1)}
                    </span>
                    {row.is_atm      && <Badge color="var(--accent-blue)">ATM</Badge>}
                    {row.is_call_wall && <Badge color="#f87171">CALL WALL</Badge>}
                    {row.is_put_wall  && <Badge color="#4ade80">PUT WALL</Badge>}
                  </div>

                  {/* Bar chart: put (green, left) | center | call (red, right) */}
                  <div className="flex items-center flex-1" style={{ height: 12 }}>
                    <div className="flex justify-end items-center" style={{ flex: 1 }}>
                      <div style={{
                        width: `${putPct}%`, height: 8,
                        background: '#4ade80',
                        borderRadius: '3px 0 0 3px',
                        opacity: row.is_put_wall ? 1 : 0.55,
                      }} />
                    </div>
                    <div style={{ width: 1, height: 14, background: 'var(--border)', flexShrink: 0 }} />
                    <div className="flex justify-start items-center" style={{ flex: 1 }}>
                      <div style={{
                        width: `${callPct}%`, height: 8,
                        background: '#f87171',
                        borderRadius: '0 3px 3px 0',
                        opacity: row.is_call_wall ? 1 : 0.55,
                      }} />
                    </div>
                  </div>

                  {/* Values */}
                  <div className="flex gap-0 shrink-0 font-mono text-xs tabular-nums" style={{ width: 165 }}>
                    <span style={{ width: 55, textAlign: 'right', color: '#4ade80' }}>+{fmt(row.put_gex_mm, 1)}</span>
                    <span style={{ width: 55, textAlign: 'right', color: '#f87171' }}>+{fmt(row.call_gex_mm, 1)}</span>
                    <span style={{ width: 55, textAlign: 'right', color: row.net_gex_mm >= 0 ? '#4ade80' : '#f87171' }}>
                      {fmtMM(row.net_gex_mm)}
                    </span>
                  </div>
                </div>

                {row.is_atm && (
                  <div className="my-0.5" style={{ borderTop: '1px solid var(--accent-blue)', opacity: 0.4 }} />
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────
function Card({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div
      className="flex flex-col px-3 py-2 rounded shrink-0"
      style={{ background: 'var(--bg-row)', border: '1px solid var(--border)', minWidth: 88 }}
    >
      <span style={{ color: 'var(--text-dim)', fontSize: 9, fontWeight: 600, letterSpacing: '0.12em' }}>{label}</span>
      <span className="font-bold tabular-nums" style={{ color: color ?? 'var(--text-primary)', fontSize: 14, lineHeight: 1.3 }}>{value}</span>
      {sub && <span style={{ color: 'var(--text-dim)', fontSize: 10 }}>{sub}</span>}
    </div>
  )
}

function Badge({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span
      className="text-xs px-1 rounded"
      style={{ background: `${color}18`, color, fontSize: 9, fontWeight: 700, whiteSpace: 'nowrap' }}
    >
      {children}
    </span>
  )
}

function ExpiryBadge({ type }: { type: string }) {
  const cfg = {
    QUARTERLY: {
      label: '⚡ TRIPLE WITCHING',
      color: '#a855f7',
      title: 'Quarterly expiry (Triple Witching) — stock options + index options + index futures expire simultaneously. Highest volume day of the quarter. Expect elevated volatility Friday and the following Monday.',
    },
    MONTHLY: {
      label: '📅 MONTHLY EXPIRY',
      color: '#f97316',
      title: 'Standard monthly expiry (3rd Friday). Options positioning unwinds at close. Monday repositioning flows can be volatile.',
    },
    '0DTE': {
      label: '🔴 0DTE',
      color: '#f87171',
      title: 'Options expire today — gamma is extreme and decays rapidly. Large intraday moves possible as 0DTE hedging flows dominate.',
    },
  }[type]
  if (!cfg) return null
  return (
    <span
      className="text-xs px-2 py-0.5 rounded font-semibold"
      style={{ background: `${cfg.color}15`, color: cfg.color, border: `1px solid ${cfg.color}30`, fontSize: 10, whiteSpace: 'nowrap' }}
      title={cfg.title}
    >
      {cfg.label}
    </span>
  )
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <div style={{ width: 10, height: 10, background: color, borderRadius: 2 }} />
      <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{label}</span>
    </div>
  )
}

function LegendLine({ color, label, dashed }: { color: string; label: string; dashed?: boolean }) {
  return (
    <div className="flex items-center gap-1.5">
      <div style={{
        width: 14, height: 2,
        background: dashed ? 'transparent' : color,
        borderTop: dashed ? `2px dashed ${color}` : 'none',
      }} />
      <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{label}</span>
    </div>
  )
}
