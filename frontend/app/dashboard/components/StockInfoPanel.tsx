'use client'

import { useEffect, useState } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export interface StockPanelInfo {
  symbol:    string
  last:      number
  change:    number
  changePct: number
}

// ── API types ─────────────────────────────────────────────────────────────────
interface EarningsRow {
  date:         string        // 'Apr 2026'
  eps_estimate: number | null
  eps_actual:   number | null
  surprise_pct: number | null
  result:       'BEAT' | 'MISS' | null
  move_pct:     number | null // overnight: prior close → next open
}

interface StockProfile {
  ticker:               string
  company_name:         string | null
  sector:               string | null
  industry:             string | null
  exchange:             string | null
  logo_url:             string | null
  market_cap:           number | null
  short_float:          number | null
  beta:                 number | null
  revenue_ttm:          number | null
  week_52_high:         number | null
  week_52_low:          number | null
  analyst_rating:       string | null
  analyst_count:        number | null
  target_price:         number | null
  upside_pct:           number | null
  last_eps_actual:      number | null
  last_eps_estimate:    number | null
  last_eps_surprise_pct: number | null
  last_eps_result:      'BEAT' | 'MISS' | null
  next_earnings_date:   string | null
  days_to_earnings:     number | null
  earnings_history:     EarningsRow[]
  beat_count:           number | null
  beat_streak:          number | null
  avg_surprise_pct:     number | null
  avg_move_pct:         number | null
  news: { title: string; publisher: string; published_at: number }[]
  refreshed_at:         string | null
}

interface SignalData {
  side:           'LONG' | 'SHORT'
  model:          string
  entry:          number
  stop:           number
  t1:             number   // 1:1 R/R target (T1)
  target:         number   // T2 statistical target
  l1:             number   // raw distance in pts — NOT an absolute price
  l2:             number
  l3:             number
  l4:             number
  last:           number
  swing_pct:      number
  current_range:  number
  typical_range:  number
  signal_state?:  string | null
}

interface SRLevel  { price: number; touches: number; strength: number; zone_type: string; dist_pct: number }
interface SRLevels { support: SRLevel[]; resistance: SRLevel[] }

interface PriceBar { date: string; close: number }

interface ProfileResponse {
  ticker:        string
  profile:       StockProfile | null
  signal:        SignalData | null
  signals:       SignalData[]
  last:          number | null
  price_history: PriceBar[]
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmtCap(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`
  if (n >= 1e9)  return `$${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6)  return `$${(n / 1e6).toFixed(2)}M`
  return `$${n.toLocaleString()}`
}

function fmtP(n: number | null | undefined, dec = 2): string {
  return n == null ? '—' : n.toFixed(dec)
}

function fmtPct(n: number | null | undefined, showPlus = true): string {
  if (n == null) return '—'
  return `${showPlus && n >= 0 ? '+' : ''}${n.toFixed(1)}%`
}

function timeAgo(ts: number): string {
  const diff = Date.now() / 1000 - ts
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

const RATING_STYLE: Record<string, { color: string; bg: string }> = {
  'Strong Buy':  { color: '#4ade80', bg: 'rgba(74,222,128,0.14)'  },
  'Buy':         { color: '#86efac', bg: 'rgba(134,239,172,0.11)' },
  'Hold':        { color: '#fbbf24', bg: 'rgba(251,191,36,0.11)'  },
  'Sell':        { color: '#f87171', bg: 'rgba(248,113,113,0.11)' },
  'Strong Sell': { color: '#ef4444', bg: 'rgba(239,68,68,0.14)'   },
}

// ── 90-day sparkline ──────────────────────────────────────────────────────────
function Sparkline({ bars }: { bars: PriceBar[] }) {
  if (bars.length < 2) return <div style={{ height: 60, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#334155', fontSize: 11 }}>No chart data</div>

  const closes = bars.map(b => b.close)
  const lo = Math.min(...closes)
  const hi = Math.max(...closes)
  const rng = hi - lo || 1
  const W = 300
  const H = 60

  const pts = closes.map((c, i) => {
    const x = (i / (closes.length - 1)) * W
    const y = H - ((c - lo) / rng) * (H - 4) - 2
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })

  const isUp    = closes[closes.length - 1] >= closes[0]
  const stroke  = isUp ? '#4ade80' : '#f87171'
  const fillId  = `spark-fill-${isUp ? 'up' : 'dn'}`
  const polyPts = `0,${H} ${pts.join(' ')} ${W},${H}`

  return (
    <div style={{ position: 'relative', height: H + 16 }}>
      {/* Price labels */}
      <div style={{ position: 'absolute', right: 0, top: 0, fontSize: 9, color: '#475569' }}>${hi.toFixed(2)}</div>
      <div style={{ position: 'absolute', right: 0, bottom: 0, fontSize: 9, color: '#475569' }}>${lo.toFixed(2)}</div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', height: H, display: 'block' }}
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.18" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0.01" />
          </linearGradient>
        </defs>
        <polygon points={polyPts} fill={`url(#${fillId})`} />
        <polyline
          points={pts.join(' ')}
          fill="none"
          stroke={stroke}
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
        {/* Last-price dot */}
        <circle
          cx={W}
          cy={closes.length > 1 ? parseFloat(pts[pts.length - 1].split(',')[1]) : H / 2}
          r="2.5"
          fill={stroke}
        />
      </svg>
    </div>
  )
}

// ── Stat chip ─────────────────────────────────────────────────────────────────
function Chip({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div style={{ background: 'rgba(255,255,255,0.04)', borderRadius: 8, padding: '8px 12px', flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 10, color: '#475569', marginBottom: 3, whiteSpace: 'nowrap' }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 700, color: valueColor || '#e2e8f0' }}>{value}</div>
    </div>
  )
}

// ── Model colour map ──────────────────────────────────────────────────────────
const MODEL_COLOR: Record<string, string> = {
  AGG:  '#fbbf24',   // amber
  CON:  '#a5b4fc',   // lavender
  WIDE: '#2dd4bf',   // teal
}
const modelColor = (m: string) => MODEL_COLOR[m] ?? '#94a3b8'

// ── VBH level chart — SVG, all models + S/R ──────────────────────────────────
function TechnicalsChart({
  signals, srLevels, last, activeModel,
}: {
  signals:     SignalData[]
  srLevels:    SRLevels | null
  last:        number
  activeModel: string | null
}) {
  if (!signals.length) return null

  // Only draw the selected model (or first if none chosen)
  const displayModel = activeModel ?? signals[0]?.model
  const activeSigs   = signals.filter(s => s.model === displayModel)

  // ── Build level list (entry / stop / T1 / T2 — all absolute prices) ──────
  type Lvl = {
    price: number; label: string; color: string
    dash: string; sw: number; model: string; role: string
  }
  const lvls: Lvl[] = []

  for (const sig of activeSigs) {
    const mc = modelColor(sig.model)
    // entry, stop, t1, target are real absolute price levels from the backend
    if (sig.entry  > 0) lvls.push({ price: sig.entry,  label: 'Entry', color: mc,        dash: '',    sw: 2.0, model: sig.model, role: 'entry'  })
    if (sig.stop   > 0) lvls.push({ price: sig.stop,   label: 'Stop',  color: '#f87171', dash: '5 3', sw: 1.4, model: sig.model, role: 'stop'   })
    if (sig.t1     > 0) lvls.push({ price: sig.t1,     label: 'T1',    color: '#86efac', dash: '4 3', sw: 1.0, model: sig.model, role: 't1'     })
    if (sig.target > 0) lvls.push({ price: sig.target, label: 'T2',    color: '#4ade80', dash: '5 3', sw: 1.4, model: sig.model, role: 'target' })
    // Note: l1/l2/l3/l4 are raw statistical distances (pts), NOT absolute prices — do not plot
  }

  // S/R from backend — top 3 each side
  const srRes = srLevels?.resistance?.slice(0, 3) ?? []
  const srSup = srLevels?.support   ?.slice(0, 3) ?? []
  for (const r of srRes) lvls.push({ price: r.price, label: r.zone_type === 'supply' ? 'Supply' : 'Res',    color: '#f87171AA', dash: '3 4', sw: 0.9, model: '', role: 'sr' })
  for (const s of srSup) lvls.push({ price: s.price, label: s.zone_type === 'demand' ? 'Demand' : 'Sup',   color: '#4ade80AA', dash: '3 4', sw: 0.9, model: '', role: 'sr' })

  // ── Price range ───────────────────────────────────────────────────────────
  const allPx = [...lvls.map(l => l.price), last].filter(p => p > 0)
  if (!allPx.length) return null
  const minP = Math.min(...allPx)
  const maxP = Math.max(...allPx)
  const pad  = Math.max((maxP - minP) * 0.12, maxP * 0.003)
  const minPP = minP - pad
  const maxPP = maxP + pad
  const rng   = maxPP - minPP || 1

  // ── SVG layout ────────────────────────────────────────────────────────────
  const VW = 480; const VH = 300
  const PL = 80; const PR = 70          // left/right margins for labels

  const yOf  = (p: number) => VH - ((p - minPP) / rng) * VH
  const fmt  = (p: number) => `$${p >= 100 ? p.toFixed(2) : p.toFixed(3)}`

  // Sort by Y descending (top = high price)
  const sorted = [...lvls].sort((a, b) => b.price - a.price)

  // Label collision avoidance — anchor bottom label, spread upward
  const MIN_GAP = 14
  const rawY  = sorted.map(l => yOf(l.price))
  let labelY  = rawY.map(y => Math.max(8, Math.min(VH - 4, y)))

  // Pass 1 — bottom to top: when label above is too close to the one below, push it up
  for (let i = labelY.length - 2; i >= 0; i--) {
    if (labelY[i + 1] - labelY[i] < MIN_GAP) {
      labelY[i] = Math.max(8, labelY[i + 1] - MIN_GAP)
    }
  }
  // Pass 2 — top to bottom: if top-clamping caused overlap, push down to recover
  for (let i = 1; i < labelY.length; i++) {
    if (labelY[i] - labelY[i - 1] < MIN_GAP) {
      labelY[i] = Math.min(VH - 4, labelY[i - 1] + MIN_GAP)
    }
  }

  const lastY = yOf(last)

  return (
    <svg
      viewBox={`0 0 ${VW} ${VH}`}
      style={{ width: '100%', height: 300, display: 'block', overflow: 'visible' }}
      preserveAspectRatio="xMidYMid meet"
    >
      {/* S/R bands — subtle background */}
      {srRes.map((r, i) => (
        <rect key={`sr-r-${i}`} x={PL} y={yOf(r.price) - 3} width={VW - PL - PR} height={6}
          fill="rgba(248,113,113,0.10)" />
      ))}
      {srSup.map((s, i) => (
        <rect key={`sr-s-${i}`} x={PL} y={yOf(s.price) - 3} width={VW - PL - PR} height={6}
          fill="rgba(74,222,128,0.10)" />
      ))}

      {/* Price level lines + labels */}
      {sorted.map((lvl, i) => {
        const lineY  = yOf(lvl.price)
        const lbY    = labelY[i]
        const needTick = Math.abs(lineY - lbY) > 2
        return (
          <g key={i}>
            {/* horizontal price line */}
            <line x1={PL} y1={lineY} x2={VW - PR} y2={lineY}
              stroke={lvl.color} strokeWidth={lvl.sw}
              strokeDasharray={lvl.dash} />
            {/* connector tick if label shifted */}
            {needTick && (
              <line x1={PL - 6} y1={lineY} x2={PL - 6} y2={lbY}
                stroke={lvl.color} strokeWidth={0.6} opacity={0.5} />
            )}
            {/* left label */}
            <text x={PL - 8} y={lbY + 4}
              textAnchor="end" fontSize={10.5} fontWeight={lvl.role === 'entry' ? 700 : 400}
              fill={lvl.color}>
              {lvl.label}
            </text>
            {/* right price */}
            <text x={VW - PR + 6} y={lbY + 4}
              fontSize={10.5} fontWeight={lvl.role === 'entry' ? 700 : 400}
              fill={lvl.color}>
              {fmt(lvl.price)}
            </text>
          </g>
        )
      })}

      {/* Current price — bold white dashed */}
      <line x1={PL} y1={lastY} x2={VW - PR} y2={lastY}
        stroke="rgba(255,255,255,0.7)" strokeWidth={1.8} strokeDasharray="8 4" />
      <circle cx={PL} cy={lastY} r={4} fill="#e2e8f0" />
      <text x={VW - PR + 6} y={lastY + 4}
        fontSize={11} fontWeight={700} fill="#e2e8f0">
        {fmt(last)}
      </text>
    </svg>
  )
}

// ── Sector → ETF mapping ──────────────────────────────────────────────────────
const SECTOR_ETF: Record<string, string> = {
  'Technology':             'XLK',
  'Healthcare':             'XLV',
  'Financial Services':     'XLF',
  'Communication Services': 'XLC',
  'Consumer Cyclical':      'XLY',
  'Industrials':            'XLI',
  'Consumer Defensive':     'XLP',
  'Energy':                 'XLE',
  'Basic Materials':        'XLB',
  'Utilities':              'XLU',
  'Real Estate':            'XLRE',
}

// ── Main panel ────────────────────────────────────────────────────────────────
export default function StockInfoPanel({
  info,
  onClose,
  onSectorClick,
}: {
  info:           StockPanelInfo
  onClose:        () => void
  onSectorClick?: (etf: string) => void
}) {
  const [tab,          setTab]         = useState<'overview' | 'earnings' | 'technicals'>('overview')
  const [data,         setData]         = useState<ProfileResponse | null>(null)
  const [loading,      setLoading]      = useState(true)
  const [error,        setError]        = useState<string | null>(null)
  const [srLevels,     setSrLevels]     = useState<SRLevels | null>(null)
  const [activeModel,  setActiveModel]  = useState<string | null>(null)

  useEffect(() => {
    setLoading(true); setError(null)
    Promise.all([
      fetch(`${API_URL}/api/stock-profile/${info.symbol}`).then(r => r.json()),
      fetch(`${API_URL}/api/sr-levels/${info.symbol}?days=5`).then(r => r.json()).catch(() => null),
    ]).then(([profile, sr]) => {
      setData(profile)
      setSrLevels(sr && !sr.error ? sr : null)
      // default active model to the first signal's model
      if (profile?.signals?.length) setActiveModel(profile.signals[0].model)
      setLoading(false)
    }).catch(() => { setError('Failed to load profile'); setLoading(false) })
  }, [info.symbol])

  const p    = data?.profile
  const sig  = data?.signal
  const last = data?.last ?? info.last
  const bars = data?.price_history ?? []

  const isUp     = info.change >= 0
  const chgColor = isUp ? '#4ade80' : '#f87171'
  const rStyle   = p?.analyst_rating ? RATING_STYLE[p.analyst_rating] : null

  // Earnings countdown label
  const dte = p?.days_to_earnings
  const dteLabel = dte == null ? null
    : dte === 0  ? 'Today'
    : dte > 0    ? `in ${dte} days`
    : `${Math.abs(dte)} days ago`

  // Urgency colour for earnings countdown
  const dteColor = dte == null ? '#64748b'
    : dte <= 7   ? '#f87171'   // within 1 week — red
    : dte <= 30  ? '#fbbf24'   // within 1 month — amber
    :              '#4ade80'   // far away — green

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-[1080]" style={{ background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(2px)' }} onClick={onClose} />

      {/* Panel */}
      <div
        className="fixed z-[1090] rounded-xl shadow-2xl flex flex-col"
        style={{
          top: '50%', left: '50%', transform: 'translate(-50%, -50%)',
          width: 'min(580px, 96vw)', maxHeight: '90vh',
          background: '#0f0d1a',
          border: '1px solid rgba(255,255,255,0.10)',
          boxShadow: '0 0 0 1px rgba(0,0,0,0.8), 0 24px 64px rgba(0,0,0,0.9)',
          overflow: 'hidden',
        }}
      >
        {/* ── Header ─────────────────────────────────────────────────────── */}
        <div style={{ padding: '16px 20px 0', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
          <div className="flex items-start justify-between gap-3">
            <div className="flex items-start gap-3 flex-1 min-w-0">
              {/* Company logo via logo.dev — no frame, natural rendering */}
              <img
                src={`https://img.logo.dev/ticker/${info.symbol}?token=pk_fZOnZkh3QrCkdBG6NS8ckQ&size=128&format=png&retina=true`}
                alt={info.symbol}
                style={{ height: 48, width: 'auto', flexShrink: 0 }}
                onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
              />
              <div className="flex-1 min-w-0">
                {/* Symbol + price + change */}
                <div className="flex items-center flex-wrap gap-2">
                  <span style={{ fontSize: 20, fontWeight: 800, color: '#e2e8f0', letterSpacing: '0.05em' }}>{info.symbol}</span>
                  {last > 0 && <span style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0' }}>${last.toFixed(2)}</span>}
                  <span style={{ fontSize: 12, color: chgColor, fontWeight: 600 }}>
                    {isUp ? '+' : ''}{info.change.toFixed(2)}&nbsp;({isUp ? '+' : ''}{info.changePct.toFixed(2)}%)
                  </span>
                </div>

                {/* Company name — white */}
                {p?.company_name && (
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0', marginTop: 2 }}>
                    {p.company_name}
                  </div>
                )}

                {/* Exchange · Sector · [ETF chip] */}
                <div className="flex items-center flex-wrap gap-1.5 mt-1">
                  {p?.exchange && (
                    <span style={{ fontSize: 10, color: '#64748b', fontWeight: 500 }}>
                      {p.exchange}
                    </span>
                  )}
                  {p?.sector && (
                    <>
                      {p.exchange && <span style={{ fontSize: 10, color: '#334155' }}>·</span>}
                      <span style={{ fontSize: 10, color: '#64748b' }}>{p.sector}</span>
                      {SECTOR_ETF[p.sector as string] && (
                        <button
                          onClick={() => onSectorClick?.(SECTOR_ETF[p.sector as string]!)}
                          style={{
                            fontSize:      10,
                            fontWeight:    700,
                            color:         '#a855f7',
                            background:    'rgba(168,85,247,0.12)',
                            border:        '1px solid rgba(168,85,247,0.25)',
                            borderRadius:  4,
                            padding:       '1px 6px',
                            cursor:        onSectorClick ? 'pointer' : 'default',
                            lineHeight:    '1.6',
                          }}
                          title={`View ${SECTOR_ETF[p.sector as string]} sector ETF`}
                        >
                          {SECTOR_ETF[p.sector]}
                        </button>
                      )}
                    </>
                  )}
                </div>
              </div>
            </div>
            <button onClick={onClose} style={{ color: '#475569', fontSize: 18, background: 'none', border: 'none', cursor: 'pointer', lineHeight: 1, padding: 2, flexShrink: 0 }}>✕</button>
          </div>

          {/* Tab bar */}
          <div className="flex gap-1 mt-3">
            {(['overview', 'earnings', 'technicals'] as const).map(t => (
              <button
                key={t}
                onClick={() => setTab(t)}
                style={{
                  padding: '6px 14px', borderRadius: '6px 6px 0 0',
                  fontSize: 12, fontWeight: 600, textTransform: 'capitalize',
                  border: 'none', cursor: 'pointer', transition: 'all 0.15s',
                  background: tab === t ? 'rgba(168,85,247,0.18)' : 'transparent',
                  color:      tab === t ? '#a855f7' : '#475569',
                  borderBottom: tab === t ? '2px solid #a855f7' : '2px solid transparent',
                  marginBottom: -1,
                }}
              >{t}</button>
            ))}
          </div>
        </div>

        {/* ── Body ───────────────────────────────────────────────────────── */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px 20px' }}>

          {/* Loading */}
          {loading && (
            <div className="flex items-center justify-center py-16">
              <div className="w-7 h-7 rounded-full border-2 border-t-transparent animate-spin" style={{ borderColor: '#a855f7', borderTopColor: 'transparent' }} />
            </div>
          )}

          {error && !loading && (
            <div style={{ color: '#f87171', fontSize: 13, textAlign: 'center', padding: '24px 0' }}>{error}</div>
          )}

          {/* ── OVERVIEW ─────────────────────────────────────────────────── */}
          {!loading && !error && tab === 'overview' && (
            <div>
              {/* Metric chips */}
              <div className="flex gap-2 mb-4">
                <Chip label="Market Cap"   value={fmtCap(p?.market_cap ?? null)} />
                <Chip label="Short Float"  value={p?.short_float != null ? `${(p.short_float * 100).toFixed(1)}%` : '—'} valueColor={p?.short_float != null && p.short_float > 0.15 ? '#f87171' : undefined} />
                <Chip label="Beta"         value={fmtP(p?.beta, 2)} />
              </div>

              {/* EPS row */}
              {p?.last_eps_actual != null && (
                <div
                  className="rounded-lg flex items-center justify-between mb-3"
                  style={{ padding: '9px 13px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)' }}
                >
                  <div>
                    <div style={{ fontSize: 10, color: '#475569', marginBottom: 2 }}>EPS — Most Recent Quarter</div>
                    <div className="flex items-baseline gap-2">
                      <span style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0' }}>${fmtP(p.last_eps_actual)}</span>
                      {p.last_eps_estimate != null && (
                        <span style={{ fontSize: 11, color: '#64748b' }}>est. ${fmtP(p.last_eps_estimate)}</span>
                      )}
                    </div>
                  </div>
                  <div className="text-right">
                    {p.last_eps_surprise_pct != null && (
                      <div style={{ fontSize: 13, fontWeight: 700, color: p.last_eps_surprise_pct >= 0 ? '#4ade80' : '#f87171' }}>
                        {fmtPct(p.last_eps_surprise_pct)}
                      </div>
                    )}
                    {p.last_eps_result && (
                      <span
                        className="text-xs font-bold rounded px-1.5 py-0.5"
                        style={p.last_eps_result === 'BEAT'
                          ? { background: 'rgba(74,222,128,0.15)', color: '#4ade80' }
                          : { background: 'rgba(248,113,113,0.15)', color: '#f87171' }}
                      >{p.last_eps_result}</span>
                    )}
                  </div>
                </div>
              )}

              {/* Revenue row */}
              {p?.revenue_ttm != null && (
                <div
                  className="rounded-lg flex items-center justify-between mb-4"
                  style={{ padding: '9px 13px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)' }}
                >
                  <div style={{ fontSize: 10, color: '#475569' }}>Revenue (TTM)</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0' }}>{fmtCap(p.revenue_ttm)}</div>
                </div>
              )}

              {/* 52-week range */}
              {p?.week_52_high != null && p?.week_52_low != null && last > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#475569', marginBottom: 4 }}>
                    <span>${p.week_52_low.toFixed(2)}</span>
                    <span>52-Week Range</span>
                    <span>${p.week_52_high.toFixed(2)}</span>
                  </div>
                  {(() => {
                    const rng = p.week_52_high - p.week_52_low || 1
                    const pct = Math.min(100, Math.max(0, (last - p.week_52_low) / rng * 100))
                    return (
                      <div style={{ position: 'relative', height: 4, background: 'rgba(255,255,255,0.07)', borderRadius: 2 }}>
                        <div style={{ position: 'absolute', left: 0, top: 0, height: '100%', width: `${pct}%`, borderRadius: 2, background: 'linear-gradient(to right, #f87171, #fbbf24, #4ade80)' }} />
                        <div style={{ position: 'absolute', top: -3, width: 10, height: 10, borderRadius: '50%', background: '#e2e8f0', border: '2px solid #1e1b2e', transform: 'translateX(-50%)', left: `${pct}%` }} />
                      </div>
                    )
                  })()}
                </div>
              )}

              {/* Analyst target */}
              {rStyle && p?.analyst_rating && (
                <div className="flex items-center justify-between rounded-lg mb-4"
                  style={{ padding: '9px 13px', background: rStyle.bg, border: `1px solid ${rStyle.color}28` }}>
                  <div>
                    <div style={{ fontSize: 10, color: '#475569', marginBottom: 2 }}>Analyst Consensus</div>
                    <div style={{ fontSize: 14, fontWeight: 700, color: rStyle.color }}>{p.analyst_rating}</div>
                    {p.analyst_count != null && <div style={{ fontSize: 10, color: '#64748b' }}>{p.analyst_count} analysts</div>}
                  </div>
                  {p.target_price != null && (
                    <div className="text-right">
                      <div style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0' }}>Target ${p.target_price.toFixed(2)}</div>
                      {p.upside_pct != null && (
                        <div style={{ fontSize: 11, color: p.upside_pct >= 0 ? '#4ade80' : '#f87171', fontWeight: 600 }}>
                          {p.upside_pct >= 0 ? '+' : ''}{p.upside_pct.toFixed(1)}% upside
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* 90-day sparkline */}
              {bars.length > 1 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 10, color: '#475569', marginBottom: 6 }}>90-Day Price</div>
                  <Sparkline bars={bars} />
                </div>
              )}

              {/* News */}
              {p?.news && p.news.length > 0 && (
                <div>
                  <div style={{ fontSize: 10, color: '#475569', marginBottom: 8, textTransform: 'uppercase', letterSpacing: '0.07em' }}>Recent News</div>
                  {p.news.map((n, i) => (
                    <div key={i} style={{ padding: '8px 0', borderBottom: i < p.news.length - 1 ? '1px solid rgba(255,255,255,0.05)' : 'none' }}>
                      <div style={{ fontSize: 12, color: '#cbd5e1', lineHeight: 1.4, marginBottom: 3 }}>{n.title}</div>
                      <div style={{ fontSize: 10, color: '#475569' }}>
                        {n.publisher} · {n.published_at ? timeAgo(n.published_at) : ''}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {!p && (
                <div style={{ color: '#475569', fontSize: 12, textAlign: 'center', padding: '32px 0' }}>
                  Fundamentals loading — refreshes daily at 6:00 AM ET
                </div>
              )}
            </div>
          )}

          {/* ── EARNINGS ─────────────────────────────────────────────────── */}
          {!loading && !error && tab === 'earnings' && (
            <div>
              {/* Next earnings hero */}
              {p?.next_earnings_date && (
                <div
                  className="rounded-lg flex items-center justify-between mb-4"
                  style={{ padding: '12px 16px', background: 'rgba(255,255,255,0.03)', border: `1px solid ${dteColor}28` }}
                >
                  <div>
                    <div style={{ fontSize: 10, color: '#475569', marginBottom: 3 }}>Next Earnings</div>
                    <div style={{ fontSize: 16, fontWeight: 800, color: '#e2e8f0' }}>
                      {new Date(p.next_earnings_date + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                    </div>
                  </div>
                  {dteLabel && (
                    <div style={{ fontSize: 13, fontWeight: 700, color: dteColor, background: `${dteColor}18`, padding: '4px 10px', borderRadius: 6 }}>
                      {dteLabel}
                    </div>
                  )}
                </div>
              )}

              {/* Beat stats */}
              {p?.beat_count != null && (p.earnings_history?.length ?? 0) > 0 && (
                <>
                  <div className="flex gap-2 mb-3">
                    <Chip label="Beat Record"   value={`${p.beat_count} / ${p.earnings_history.length}`} valueColor="#e2e8f0" />
                    <Chip label="Beat Streak"   value={p.beat_streak ? `${p.beat_streak} ●` : '0'} valueColor={p.beat_streak ? '#4ade80' : '#64748b'} />
                    <Chip label="Avg Surprise"  value={fmtPct(p.avg_surprise_pct)} valueColor={(p.avg_surprise_pct ?? 0) >= 0 ? '#4ade80' : '#f87171'} />
                    <Chip label="Avg |Move|"    value={p.avg_move_pct != null ? `±${p.avg_move_pct.toFixed(1)}%` : '—'} valueColor="#fbbf24" />
                  </div>

                  {/* Beat/miss dot strip */}
                  <div className="flex gap-1.5 mb-4" style={{ padding: '6px 0' }}>
                    {p.earnings_history.map((row, i) => (
                      <div
                        key={i}
                        title={`${row.date}: ${row.result ?? '—'}`}
                        style={{
                          width: 10, height: 10, borderRadius: '50%',
                          background: row.result === 'BEAT' ? '#4ade80'
                                    : row.result === 'MISS' ? '#f87171'
                                    : '#334155',
                          opacity: i === 0 ? 1 : 0.6 + (p.earnings_history.length - i) * 0.04,
                        }}
                      />
                    ))}
                    <span style={{ marginLeft: 6, fontSize: 10, color: '#334155' }}>← most recent</span>
                  </div>
                </>
              )}

              {/* History table */}
              {(p?.earnings_history?.length ?? 0) > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                        {['Quarter', 'Est', 'Actual', 'Surprise', 'Result', 'Overnight'].map(h => (
                          <th key={h} style={{ padding: '5px 8px', color: '#475569', fontSize: 10, fontWeight: 600, textAlign: h === 'Quarter' ? 'left' : 'right', textTransform: 'uppercase', letterSpacing: '0.06em', whiteSpace: 'nowrap' }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {p!.earnings_history.map((row, i) => {
                        const movColor = row.move_pct == null ? '#475569' : row.move_pct >= 0 ? '#4ade80' : '#f87171'
                        return (
                          <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)', opacity: i === 0 ? 1 : 0.75 }}>
                            <td style={{ padding: '7px 8px', color: '#94a3b8', whiteSpace: 'nowrap' }}>{row.date}</td>
                            <td style={{ padding: '7px 8px', color: '#64748b', textAlign: 'right' }}>{row.eps_estimate != null ? `$${fmtP(row.eps_estimate)}` : '—'}</td>
                            <td style={{ padding: '7px 8px', color: '#e2e8f0', fontWeight: 600, textAlign: 'right' }}>{row.eps_actual != null ? `$${fmtP(row.eps_actual)}` : '—'}</td>
                            <td style={{ padding: '7px 8px', textAlign: 'right', color: (row.surprise_pct ?? 0) >= 0 ? '#4ade80' : '#f87171', fontWeight: 600 }}>
                              {row.surprise_pct != null ? fmtPct(row.surprise_pct) : '—'}
                            </td>
                            <td style={{ padding: '7px 8px', textAlign: 'right' }}>
                              {row.result ? (
                                <span className="rounded px-1.5 py-0.5 text-xs font-bold"
                                  style={row.result === 'BEAT'
                                    ? { background: 'rgba(74,222,128,0.15)',  color: '#4ade80' }
                                    : { background: 'rgba(248,113,113,0.15)', color: '#f87171' }}>
                                  {row.result}
                                </span>
                              ) : '—'}
                            </td>
                            <td style={{ padding: '7px 8px', textAlign: 'right', color: movColor, fontWeight: 600 }}>
                              {row.move_pct != null ? fmtPct(row.move_pct) : '—'}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                  <div style={{ fontSize: 10, color: '#334155', marginTop: 8, textAlign: 'right' }}>
                    Overnight = prior close → next open (gap reaction)
                  </div>
                </div>
              ) : (
                <div style={{ color: '#475569', fontSize: 12, textAlign: 'center', padding: '24px 0' }}>
                  No earnings history available
                </div>
              )}
            </div>
          )}

          {/* ── TECHNICALS ───────────────────────────────────────────────── */}
          {!loading && !error && tab === 'technicals' && (() => {
            const allSigs = data?.signals ?? (sig ? [sig] : [])
            const activeSig = allSigs.find(s => s.model === activeModel) ?? allSigs[0] ?? null

            return (
              <div>
                {allSigs.length > 0 ? (
                  <>
                    {/* ── Model selector tabs + direction badge ── */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
                      {allSigs.map(s => {
                        const mc = modelColor(s.model)
                        const isActive = s.model === activeModel
                        return (
                          <button key={s.model}
                            onClick={() => setActiveModel(s.model)}
                            style={{
                              padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 700,
                              cursor: 'pointer', border: `1px solid ${mc}${isActive ? '' : '55'}`,
                              background: isActive ? `${mc}22` : 'transparent',
                              color: isActive ? mc : `${mc}99`,
                              transition: 'all 0.15s',
                            }}>
                            {s.model}
                          </button>
                        )
                      })}
                      {activeSig && (
                        <span style={{
                          marginLeft: 4, padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 700,
                          background: activeSig.side === 'LONG' ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)',
                          color: activeSig.side === 'LONG' ? '#4ade80' : '#f87171',
                          border: `1px solid ${activeSig.side === 'LONG' ? 'rgba(74,222,128,0.3)' : 'rgba(248,113,113,0.3)'}`,
                        }}>
                          {activeSig.side}
                        </span>
                      )}
                      {activeSig?.signal_state && activeSig.signal_state !== 'NEUTRAL' && (
                        <span style={{
                          padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 700,
                          background: activeSig.signal_state === 'ENTRY'
                            ? (activeSig.side === 'LONG' ? 'rgba(74,222,128,0.18)' : 'rgba(248,113,113,0.18)')
                            : 'rgba(251,191,36,0.15)',
                          color: activeSig.signal_state === 'ENTRY'
                            ? (activeSig.side === 'LONG' ? '#4ade80' : '#f87171')
                            : '#fbbf24',
                        }}>
                          {activeSig.signal_state}
                        </span>
                      )}
                      {srLevels && (
                        <span style={{ marginLeft: 'auto', fontSize: 10, color: '#475569' }}>
                          S/R: {(srLevels.support?.length ?? 0) + (srLevels.resistance?.length ?? 0)} zones
                        </span>
                      )}
                    </div>

                    {/* ── Chart — all models + S/R ── */}
                    <TechnicalsChart
                      signals={allSigs}
                      srLevels={srLevels}
                      last={last}
                      activeModel={activeModel}
                    />

                    {/* ── Stats table for selected model ── */}
                    {activeSig && (() => {
                      const fmtPx = (p: number) => `$${p >= 100 ? p.toFixed(2) : p.toFixed(3)}`
                      const risk  = Math.abs(activeSig.entry - activeSig.stop)
                      const rwT1  = activeSig.t1 > 0 && risk > 0 ? Math.abs(activeSig.t1  - activeSig.entry) / risk : null
                      const rwT2  = activeSig.target > 0 && risk > 0 ? Math.abs(activeSig.target - activeSig.entry) / risk : null
                      const rows = [
                        { label: 'Entry',      val: fmtPx(activeSig.entry),  color: modelColor(activeSig.model) },
                        { label: 'Stop',       val: fmtPx(activeSig.stop),   color: '#f87171' },
                        { label: 'T1 (1:1)',   val: activeSig.t1 > 0 ? fmtPx(activeSig.t1) : '—', color: '#86efac' },
                        { label: 'T2 target',  val: activeSig.target > 0 ? fmtPx(activeSig.target) : '—', color: '#4ade80' },
                        { label: 'R/R to T1',  val: rwT1 != null ? `1 : ${rwT1.toFixed(1)}` : '—', color: undefined },
                        { label: 'R/R to T2',  val: rwT2 != null ? `1 : ${rwT2.toFixed(1)}` : '—', color: undefined },
                        { label: 'Daily Swing', val: `${activeSig.swing_pct >= 0 ? '+' : ''}${activeSig.swing_pct.toFixed(1)}% of ${activeSig.typical_range.toFixed(2)} pts`, color: undefined },
                      ]
                      return (
                        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 4, fontSize: 12 }}>
                          <tbody>
                            {rows.map(r => (
                              <tr key={r.label} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                                <td style={{ padding: '6px 0', color: '#64748b', fontSize: 11 }}>{r.label}</td>
                                <td style={{ padding: '6px 0', textAlign: 'right', fontWeight: 600, fontSize: 12, color: r.color || '#e2e8f0' }}>{r.val}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )
                    })()}
                  </>
                ) : (
                  <div style={{ color: '#475569', fontSize: 12, textAlign: 'center', padding: '32px 0' }}>
                    No active VBH signal for {info.symbol}
                    <div style={{ fontSize: 11, color: '#334155', marginTop: 6 }}>Levels appear when price reaches the signal zone</div>
                  </div>
                )}
              </div>
            )
          })()}
        </div>

        {/* ── Footer ─────────────────────────────────────────────────────── */}
        {p?.refreshed_at && (
          <div style={{ padding: '7px 20px', borderTop: '1px solid rgba(255,255,255,0.05)', fontSize: 10, color: '#1e293b', textAlign: 'right' }}>
            Fundamentals as of {new Date(p.refreshed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
          </div>
        )}
      </div>
    </>
  )
}
