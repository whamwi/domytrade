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
  // Synthetic (dealer-only) GEX — present when OCC MM% data is available
  dealer_call_gex_mm?: number
  dealer_put_gex_mm?: number
  dealer_net_gex_mm?: number
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
  // Synthetic GEX — dealer-only estimate from OCC MM% data
  mm_pct_calls?: number | null
  mm_pct_puts?: number | null
  mm_pct_date?: string | null
  synthetic_net_gex_mm?: number | null
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
  const [showAll,       setShowAll]       = useState(false)
  const [showGammaHelp, setShowGammaHelp] = useState(false)
  const [syntheticMode, setSyntheticMode] = useState(false)
  // const [showDeltaHelp, setShowDeltaHelp] = useState(false)  // delta buckets removed — kept for future use
  const [elapsed,  setElapsed] = useState(0)      // ms while loading
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
  // True when OCC MM% data is available and at least one strike has dealer fields
  const hasSynthetic = !!(
    data?.mm_pct_calls != null &&
    strikes.some(r => r.dealer_call_gex_mm != null)
  )

  const maxAbs = strikes.reduce((m, r) => {
    const c = syntheticMode && hasSynthetic ? (r.dealer_call_gex_mm ?? r.call_gex_mm) : r.call_gex_mm
    const p = syntheticMode && hasSynthetic ? (r.dealer_put_gex_mm  ?? r.put_gex_mm)  : r.put_gex_mm
    return Math.max(m, c, p)
  }, 1)
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

  // LVP / HVP — switch to dealer_net_gex_mm in synthetic mode
  const _getNet = (r: StrikeRow) =>
    syntheticMode && hasSynthetic ? (r.dealer_net_gex_mm ?? r.net_gex_mm) : r.net_gex_mm

  const lvpRow = !allZeroOI && strikes.length > 0
    ? (() => {
        const best = strikes.reduce((b, r) => _getNet(r) > _getNet(b) ? r : b)
        return _getNet(best) > 0 ? best : null
      })()
    : null
  const hvpRow = !allZeroOI && strikes.length > 0
    ? (() => {
        const best = strikes.reduce((b, r) => _getNet(r) < _getNet(b) ? r : b)
        return _getNet(best) < 0 ? best : null
      })()
    : null

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
            label={syntheticMode && hasSynthetic ? 'DEALER GEX' : 'NET GEX'}
            value={syntheticMode && hasSynthetic && data.synthetic_net_gex_mm != null
              ? fmtMM(data.synthetic_net_gex_mm)
              : fmtMM(lv.net)}
            sub={syntheticMode && hasSynthetic ? 'dealer estimate' : 'per 1% price move'}
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
            color="#4ade80"
          />
          <Card
            label="PUT WALL"
            value={lv.put_wall != null ? `$${fmt(lv.put_wall, lv.put_wall >= 1000 ? 0 : 1)}` : '—'}
            sub={!putWallInRange && lv.put_wall != null ? '↓ below chart range' : 'support'}
            color="#f87171"
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
          {lvpRow && (
            <Card
              label="Low Vol. Point"
              value={`$${fmt(lvpRow.strike, lvpRow.strike >= 1000 ? 0 : 1)}`}
              sub="dealers dampen"
              color="#a855f7"
              title={`Low Volatility Point — strike with the highest net GEX.\n\nAs price rises toward this level, dealers sell stock to delta-hedge (damping the rally). As price falls from it, they buy (cushioning the drop).\n\nTwo outcomes when price tests this level:\n1. Dealer selling volume exceeds buyer demand → price pins or reverses (wall holds).\n2. Buy flow overwhelms dealer hedging → price breaks through.\n\nKey: dealers only trade the Δ-change × OI — a fraction of total volume. Strong enough buying pressure can always push past it.`}
            />
          )}
          {hvpRow && (
            <Card
              label="High Vol. Point"
              value={`$${fmt(hvpRow.strike, hvpRow.strike >= 1000 ? 0 : 1)}`}
              sub="dealers fuel moves"
              color="#f97316"
              title={`High Volatility Point — strike with the most negative net GEX.\n\nDealers are short gamma here: as price falls through this level they sell more stock to re-hedge, amplifying the move. As price rises through it they buy, amplifying the rally.\n\nUnlike the LVP which resists moves, the HVP accelerates them — once price breaks this level, dealer flow adds fuel rather than friction.`}
            />
          )}
          {hasSynthetic && data.mm_pct_calls != null && (
            <Card
              label="DEALER MM%"
              value={`C ${(data.mm_pct_calls * 100).toFixed(0)}%  P ${((data.mm_pct_puts ?? 0) * 100).toFixed(0)}%`}
              sub={data.mm_pct_date ? `OCC · ${data.mm_pct_date}` : 'OCC volume'}
              color="#a855f7"
              title={`Market-maker share of options volume from OCC.\n\nC = % of call volume traded by market makers.\nP = % of put volume traded by market makers.\n\nDealer GEX = Total GEX × MM% — isolates the portion of gamma exposure that belongs to dealers who must delta-hedge.\n\nSource: OCC volume-query API (prior session).`}
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
            {data.delta_distribution && (
              <>
                {/* OI-based P/C — accumulated positioning (last close snapshot) */}
                {data.delta_distribution.pc_ratio_oi != null && (
                  <span className="text-xs" title="P/C by Open Interest — accumulated positioning from the last close. Call-heavy = longs built up during the run-up.">
                    <span style={{ color: 'var(--text-dim)' }}>P/C OI </span>
                    <span className="font-bold tabular-nums" style={{
                      color: data.delta_distribution.pc_ratio_oi > 1 ? '#f87171' : '#4ade80'
                    }}>
                      {data.delta_distribution.pc_ratio_oi.toFixed(2)}
                    </span>
                  </span>
                )}
                {data.delta_distribution.pc_ratio_vol != null && (
                  <span className="text-xs" title="P/C by today's volume — what traders actually bought/sold today. Put-heavy on crash days.">
                    <span style={{ color: 'var(--text-dim)' }}>Vol </span>
                    <span className="font-bold tabular-nums" style={{
                      color: data.delta_distribution.pc_ratio_vol > 1 ? '#f87171' : '#4ade80'
                    }}>
                      {data.delta_distribution.pc_ratio_vol.toFixed(2)}
                    </span>
                  </span>
                )}
                <span style={{ width: 1, height: 12, background: 'var(--border)', flexShrink: 0 }} />
                <span className="tabular-nums" style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                  <span style={{ color: '#4ade80', fontWeight: 600 }}>Calls </span>
                  <span style={{ color: '#4ade80', fontWeight: 700 }}>{(data.delta_distribution.call_oi / 1000).toFixed(0)}K</span>
                  <span style={{ color: 'var(--text-dim)' }}> OI</span>
                </span>
                <span className="tabular-nums" style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                  <span style={{ color: '#f87171', fontWeight: 600 }}>Puts </span>
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
              onClick={() => setShowGammaHelp(v => !v)}
              className="text-xs px-2 py-0.5 rounded transition-colors"
              style={{
                background: showGammaHelp ? 'rgba(251,191,36,0.12)' : 'transparent',
                color:      '#fbbf24',
                border:     `1px solid ${showGammaHelp ? 'rgba(251,191,36,0.6)' : 'rgba(251,191,36,0.35)'}`,
                fontWeight: 600, whiteSpace: 'nowrap', fontSize: 11,
              }}
            >
              Explained
            </button>
          </div>

          {/* ── COMMENTED OUT: delta bucket distribution (OI-based ±Δ buckets) ──────
          {data.delta_distribution && (() => { ... 5-column bucket grid ... })()}
          {showDeltaHelp && ( ... delta ranges explanation panel ... )}
          ── Replaced by Γ Concentration below, which directly measures where
             gamma exposure lives relative to spot — more actionable for GEX. ── */}

          {/* ── Γ Concentration — cumulative gamma within concentric ATM bands ── */}
          {data.strikes.length > 0 && !allZeroOI && (() => {
            const spot = data.underlying
            const BANDS = [
              { pct: 0.005, label: '±0.5%' },
              { pct: 0.010, label: '±1%'   },
              { pct: 0.020, label: '±2%'   },
              { pct: 0.050, label: '±5%'   },
              { pct: 0.100, label: '±10%'  },
            ]
            const src    = data.strikes
            const _cVal  = (r: StrikeRow) => syntheticMode && hasSynthetic ? (r.dealer_call_gex_mm ?? r.call_gex_mm) : r.call_gex_mm
            const _pVal  = (r: StrikeRow) => syntheticMode && hasSynthetic ? (r.dealer_put_gex_mm  ?? r.put_gex_mm)  : r.put_gex_mm
            const totalC = src.reduce((s, r) => s + _cVal(r), 0)
            const totalP = src.reduce((s, r) => s + _pVal(r), 0)
            if (totalC <= 0 && totalP <= 0) return null

            const bands = BANDS.map(({ pct, label }) => {
              const lo  = spot * (1 - pct)
              const hi  = spot * (1 + pct)
              const sub = src.filter(r => r.strike >= lo && r.strike <= hi)
              const c   = sub.reduce((s, r) => s + _cVal(r), 0)
              const p   = sub.reduce((s, r) => s + _pVal(r), 0)
              const pts = spot * pct   // dollar move = spot × pct
              return {
                label,
                pts,
                cPct: totalC > 0 ? (c / totalC) * 100 : 0,
                pPct: totalP > 0 ? (p / totalP) * 100 : 0,
              }
            })

            // ±1% band is the key pin level
            const pin      = bands[1]
            const pinMin   = Math.min(pin.cPct, pin.pPct)
            const pinColor = pinMin >= 25 ? '#4ade80' : pinMin >= 10 ? '#fbbf24' : '#f87171'
            const pinLabel = pinMin >= 25 ? 'Strong pin' : pinMin >= 10 ? 'Moderate' : 'Spread'

            return (
              <div style={{ marginTop: 8 }}>
                {/* Sub-header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <span style={{ fontSize: 10, color: 'var(--text-dim)', fontWeight: 600, letterSpacing: '0.10em' }}>
                    Γ CONCENTRATION
                  </span>
                  <span
                    style={{
                      fontSize: 9, fontWeight: 700, padding: '1px 6px', borderRadius: 3,
                      color: pinColor, background: `${pinColor}15`, border: `1px solid ${pinColor}30`,
                    }}
                    title={`${pin.cPct.toFixed(0)}% of call gamma and ${pin.pPct.toFixed(0)}% of put gamma lives within ±1% of ATM. High % = gamma is pinned near spot → dealers must hedge actively here.`}
                  >
                    {pinLabel} at ±1%
                  </span>
                  <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>
                    cumulative gamma within each band of{' '}
                    <span style={{ color: 'var(--text-muted)' }}>
                      ${spot >= 1000 ? spot.toFixed(0) : spot.toFixed(2)}
                    </span>
                  </span>
                </div>

                {/* Column headers — widths mirror the GEX bar chart (strike=150, bars=flex:1, values=165) */}
                <div className="flex items-center gap-2" style={{ marginBottom: 3 }}>
                  <div style={{ width: 140 }} />
                  <div className="flex items-center" style={{ flex: 1 }}>
                    <div style={{ flex: 1, textAlign: 'right', fontSize: 9, color: '#4ade80', fontWeight: 600, letterSpacing: '0.06em', paddingRight: 10 }}>CALLS ◄</div>
                    <div style={{ width: 16, flexShrink: 0 }} />
                    <div style={{ flex: 1, textAlign: 'left', fontSize: 9, color: '#f87171', fontWeight: 600, letterSpacing: '0.06em', paddingLeft: 10 }}>► PUTS</div>
                  </div>
                  <div style={{ width: 150, display: 'flex', alignItems: 'center', justifyContent: 'flex-start', gap: 4, paddingLeft: 8 }}>
                    <span style={{ fontSize: 9, color: '#4ade80', fontWeight: 600, letterSpacing: '0.06em', width: 30, textAlign: 'right' }}>C%</span>
                    <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>·</span>
                    <span style={{ fontSize: 9, color: '#f87171', fontWeight: 600, letterSpacing: '0.06em', width: 30, textAlign: 'left' }}>P%</span>
                  </div>
                </div>

                {/* Band rows — gap-2 + column widths match the GEX bar chart exactly */}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                  {bands.map(({ label, pts, cPct, pPct }, i) => {
                    const isKey  = i === 1
                    const ptsStr = `±$${pts >= 100 ? pts.toFixed(0) : pts >= 10 ? pts.toFixed(1) : pts.toFixed(2)}`
                    return (
                    <div key={label} className="flex items-center gap-2" style={{ height: 20 }}>
                      {/* Band label — 140px */}
                      <div style={{ width: 140, textAlign: 'right', display: 'flex', alignItems: 'baseline', justifyContent: 'flex-end', gap: 4 }}>
                        <span style={{
                          fontSize: 12, fontVariantNumeric: 'tabular-nums',
                          color: isKey ? 'var(--text-primary)' : 'var(--text-muted)',
                          fontWeight: isKey ? 700 : 500,
                        }}>
                          {label}
                        </span>
                        <span style={{
                          fontSize: 10, fontVariantNumeric: 'tabular-nums',
                          color: isKey ? 'var(--text-muted)' : 'var(--text-dim)',
                        }}>
                          ({ptsStr})
                        </span>
                      </div>

                      {/* Bar container — green left track | dashed centre | red right track */}
                      <div className="flex items-center" style={{ flex: 1, height: 8 }}>
                        <div className="flex justify-end items-center" style={{ flex: 1, height: 8, background: 'rgba(74,222,128,0.08)', borderRadius: '3px 0 0 3px' }}>
                          <div style={{
                            height: 8, width: `${cPct}%`,
                            background: '#4ade80', borderRadius: '3px 0 0 3px',
                            opacity: isKey ? 0.9 : 0.5,
                          }} />
                        </div>
                        {/* Centre gap — dashed line aligned with GEX chart spine below */}
                        <div style={{ width: 16, flexShrink: 0, display: 'flex', justifyContent: 'center', alignItems: 'center', height: 18 }}>
                          <div style={{
                            width: 1,
                            height: 18,
                            background: 'repeating-linear-gradient(to bottom, rgba(148,163,184,0.45) 0px, rgba(148,163,184,0.45) 3px, transparent 3px, transparent 6px)',
                          }} />
                        </div>
                        <div className="flex justify-start items-center" style={{ flex: 1, height: 8, background: 'rgba(248,113,113,0.08)', borderRadius: '0 3px 3px 0' }}>
                          <div style={{
                            height: 8, width: `${pPct}%`,
                            background: '#f87171', borderRadius: '0 3px 3px 0',
                            opacity: isKey ? 0.9 : 0.5,
                          }} />
                        </div>
                      </div>

                      {/* Values — 150px mirrors the label column, numbers left-aligned next to bars */}
                      <div style={{ width: 150, display: 'flex', alignItems: 'center', justifyContent: 'flex-start', gap: 4, paddingLeft: 8 }}>
                        <span style={{
                          fontSize: 12, color: '#4ade80', fontVariantNumeric: 'tabular-nums',
                          fontWeight: isKey ? 700 : 500, width: 30, textAlign: 'right',
                        }}>
                          {cPct.toFixed(0)}%
                        </span>
                        <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>·</span>
                        <span style={{
                          fontSize: 12, color: '#f87171', fontVariantNumeric: 'tabular-nums',
                          fontWeight: isKey ? 700 : 500, width: 30, textAlign: 'left',
                        }}>
                          {pPct.toFixed(0)}%
                        </span>
                      </div>
                    </div>
                    )
                  })}
                </div>

                {/* ── Explanation panel ── */}
                {showGammaHelp && (
                  <div
                    style={{
                      marginTop: 10, borderRadius: 4, padding: '10px 12px',
                      background: 'rgba(251,191,36,0.05)', border: '1px solid rgba(251,191,36,0.2)',
                    }}
                  >
                    <div style={{ fontSize: 10, color: '#fbbf24', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 8 }}>
                      HOW TO READ Γ CONCENTRATION
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '0 12px' }}>
                      {[
                        {
                          band: '±0.5%',
                          title: 'Immediate pin',
                          body: 'The tightest squeeze zone. If >40% of gamma lives here, dealers are forced to hedge on every tick — expect very tight, choppy price action around ATM.',
                        },
                        {
                          band: '±1%',
                          title: 'Intraday range',
                          body: 'The key pin level. A typical pinned day stays within this band. High % here = magnet effect pulling price back to ATM. Watch for reversals at the edges.',
                        },
                        {
                          band: '±2%',
                          title: 'Session range',
                          body: 'Normal intraday drift. If most gamma is contained here, the day is likely range-bound with no sustained trend. Breakout above = gamma regime weakening.',
                        },
                        {
                          band: '±5%',
                          title: 'Weekly move',
                          body: 'Covers most earnings-day and macro-event moves. High cumulative % here means gamma is spread across a wide range — less pinning, more directional potential.',
                        },
                        {
                          band: '±10%',
                          title: 'Tail / event',
                          body: 'Far-wing positioning. Typically 90 %+ by this band. The remaining percentage beyond ±10% is deep OTM insurance with negligible gamma — dealers barely hedge it.',
                        },
                      ].map(({ band, title, body }) => (
                        <div key={band} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                          <div style={{ fontSize: 11, color: '#fbbf24', fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{band}</div>
                          <div style={{ fontSize: 11, color: 'var(--text-muted)', fontWeight: 600 }}>{title}</div>
                          <div style={{ fontSize: 11, color: 'var(--text-dim)', lineHeight: 1.55 }}>{body}</div>
                        </div>
                      ))}
                    </div>
                    <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid rgba(251,191,36,0.12)', fontSize: 11, color: 'var(--text-dim)', lineHeight: 1.6 }}>
                      <span style={{ color: '#4ade80', fontWeight: 600 }}>Strong pin</span> = ±1% holds ≥25% of gamma on both sides — expect tight mean-reversion, stock magnetised to ATM.{'  '}
                      <span style={{ color: '#fbbf24', fontWeight: 600 }}>Moderate</span> = 10–25% — some anchoring near spot, range-bound but breakout possible.{'  '}
                      <span style={{ color: '#f87171', fontWeight: 600 }}>Spread</span> = &lt;10% — gamma dispersed, dealers hedge lightly near spot, trending/amplified moves more likely.
                    </div>
                  </div>
                )}
              </div>
            )
          })()}
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
          <LegendDot color="#4ade80" label="Call GEX → resistance" />
          <LegendDot color="#f87171" label="Put GEX → support" />
          <LegendLine color="var(--accent-blue)" label="ATM price" />
          <LegendLine color="#fbbf24" dashed label="Zero gamma flip" />
          {lvpRow && <LegendLine color="#a855f7" label="LVP · pin" />}
          {hvpRow && <LegendLine color="#f97316" dashed label="HVP · accel" />}
          <div style={{ flex: 1 }} />
          {hasSynthetic && (
            <button
              onClick={() => setSyntheticMode(v => !v)}
              className="text-xs px-2 py-0.5 rounded"
              style={{
                background: syntheticMode ? 'rgba(168,85,247,0.15)' : 'var(--bg-row)',
                border: `1px solid ${syntheticMode ? '#a855f7' : 'var(--border)'}`,
                color: syntheticMode ? '#a855f7' : 'var(--text-dim)',
                fontWeight: syntheticMode ? 600 : 400,
              }}
              title="Toggle between Total GEX (all market participants) and Dealer GEX (market-maker only, from OCC volume data)"
            >
              🔬 {syntheticMode ? 'dealer view' : 'synthetic'}
            </button>
          )}
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
            const callVal = syntheticMode && hasSynthetic ? (row.dealer_call_gex_mm ?? row.call_gex_mm) : row.call_gex_mm
            const putVal  = syntheticMode && hasSynthetic ? (row.dealer_put_gex_mm  ?? row.put_gex_mm)  : row.put_gex_mm
            const callPct = Math.min((callVal / maxAbs) * 100, 100)
            const putPct  = Math.min((putVal  / maxAbs) * 100, 100)
            const isWall  = row.is_call_wall || row.is_put_wall

            const isLvp = lvpRow != null && row.strike === lvpRow.strike
            const isHvp = hvpRow != null && row.strike === hvpRow.strike

            return (
              <div key={row.strike}>
                {/* LVP marker — max net GEX → pin zone */}
                {isLvp && (
                  <div className="flex items-center gap-2 my-1" style={{ borderTop: '1px solid #a855f7', opacity: 0.8 }}>
                    <span
                      className="text-xs px-1"
                      style={{ color: '#a855f7', background: 'var(--bg-main)', fontSize: 9, letterSpacing: '0.08em' }}
                    >
                      ── LVP ${fmt(lvpRow.strike, lvpRow.strike >= 1000 ? 0 : 1)} ──
                    </span>
                  </div>
                )}

                {/* HVP marker — most negative net GEX → acceleration zone */}
                {isHvp && (
                  <div className="flex items-center gap-2 my-1" style={{ borderTop: '1px dashed #f97316', opacity: 0.8 }}>
                    <span
                      className="text-xs px-1"
                      style={{ color: '#f97316', background: 'var(--bg-main)', fontSize: 9, letterSpacing: '0.08em' }}
                    >
                      ── HVP ${fmt(hvpRow.strike, hvpRow.strike >= 1000 ? 0 : 1)} ──
                    </span>
                  </div>
                )}

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
                      : isLvp
                      ? 'rgba(168,85,247,0.05)'
                      : isHvp
                      ? 'rgba(249,115,22,0.05)'
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
                          : isLvp    ? '#a855f7'
                          : isHvp    ? '#f97316'
                          : row.is_call_wall ? '#4ade80'
                          : row.is_put_wall  ? '#f87171'
                          : 'var(--text-muted)',
                        fontWeight: isWall || row.is_atm || isLvp || isHvp ? 700 : 400,
                      }}
                    >
                      ${row.strike >= 1000 ? row.strike.toFixed(0) : row.strike.toFixed(1)}
                    </span>
                    {row.is_atm       && <Badge color="var(--accent-blue)">ATM</Badge>}
                    {row.is_call_wall  && <Badge color="#4ade80">CALL WALL</Badge>}
                    {row.is_put_wall   && <Badge color="#f87171">PUT WALL</Badge>}
                    {isLvp             && <Badge color="#a855f7">LVP</Badge>}
                    {isHvp             && <Badge color="#f97316">HVP</Badge>}
                  </div>

                  {/* Bar chart: put (red, left) | center | call (green, right) */}
                  <div className="flex items-center flex-1" style={{ height: 12 }}>
                    <div className="flex justify-end items-center" style={{ flex: 1 }}>
                      <div style={{
                        width: `${putPct}%`, height: 8,
                        background: '#f87171',
                        borderRadius: '3px 0 0 3px',
                        opacity: row.is_put_wall ? 1 : 0.55,
                      }} />
                    </div>
                    <div style={{ width: 1, height: 14, background: 'var(--border)', flexShrink: 0 }} />
                    <div className="flex justify-start items-center" style={{ flex: 1 }}>
                      <div style={{
                        width: `${callPct}%`, height: 8,
                        background: '#4ade80',
                        borderRadius: '0 3px 3px 0',
                        opacity: row.is_call_wall ? 1 : 0.55,
                      }} />
                    </div>
                  </div>

                  {/* Values */}
                  <div className="flex gap-0 shrink-0 font-mono text-xs tabular-nums" style={{ width: 165 }}>
                    <span style={{ width: 55, textAlign: 'right', color: '#f87171' }}>+{fmt(putVal, 1)}</span>
                    <span style={{ width: 55, textAlign: 'right', color: '#4ade80' }}>+{fmt(callVal, 1)}</span>
                    <span style={{ width: 55, textAlign: 'right', color: _getNet(row) >= 0 ? '#4ade80' : '#f87171' }}>
                      {fmtMM(_getNet(row))}
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
function Card({ label, value, sub, color, title }: { label: string; value: string; sub?: string; color?: string; title?: string }) {
  return (
    <div
      className="flex flex-col px-3 py-2 rounded shrink-0"
      style={{ background: 'var(--bg-row)', border: '1px solid var(--border)', minWidth: 88, cursor: title ? 'help' : 'default' }}
      title={title}
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
