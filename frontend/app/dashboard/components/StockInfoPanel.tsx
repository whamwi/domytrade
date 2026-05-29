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
  target:         number
  l1:             number
  l2:             number
  l3:             number
  l4:             number
  last:           number
  swing_pct:      number
  current_range:  number
  typical_range:  number
  signal_state?:  string | null
}

interface PriceBar { date: string; close: number }

interface ProfileResponse {
  ticker:        string
  profile:       StockProfile | null
  signal:        SignalData | null
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

// ── VBH level chart (Technicals tab) ─────────────────────────────────────────
function TechnicalsChart({ sig, last }: { sig: SignalData; last: number }) {
  const levels = [
    { label: 'L4',     price: sig.l4,     color: '#7c3aed' },
    { label: 'L3',     price: sig.l3,     color: '#a855f7' },
    { label: 'Target', price: sig.target,  color: '#4ade80' },
    { label: 'Entry',  price: sig.entry,   color: '#fbbf24' },
    { label: 'L2',     price: sig.l2,      color: '#2563eb' },
    { label: 'L1',     price: sig.l1,      color: '#60a5fa' },
    { label: 'Stop',   price: sig.stop,    color: '#f87171' },
  ].filter(l => l.price > 0)

  const prices = [...levels.map(l => l.price), last].filter(Boolean)
  const minP = Math.min(...prices) * 0.999
  const maxP = Math.max(...prices) * 1.001
  const rng  = maxP - minP || 1

  const pctTop = (p: number) => `${((maxP - p) / rng * 100).toFixed(1)}%`
  const fmt    = (p: number) => p >= 100 ? p.toFixed(2) : p.toFixed(3)

  return (
    <div style={{ position: 'relative', height: 220, margin: '12px 0 8px' }}>
      <div style={{ position: 'absolute', left: 52, top: 0, bottom: 0, width: 1, background: 'rgba(255,255,255,0.07)' }} />
      {levels.map(lvl => (
        <div key={lvl.label} style={{ position: 'absolute', top: pctTop(lvl.price), left: 0, right: 0, transform: 'translateY(-50%)', display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 44, textAlign: 'right', fontSize: 10, color: lvl.color, fontWeight: 700 }}>{lvl.label}</span>
          <div style={{ flex: 1, height: 1, background: lvl.color, opacity: 0.4 }} />
          <span style={{ fontSize: 10, color: lvl.color, minWidth: 52, textAlign: 'right' }}>${fmt(lvl.price)}</span>
        </div>
      ))}
      <div style={{ position: 'absolute', top: pctTop(last), left: 52, right: 0, transform: 'translateY(-50%)', display: 'flex', alignItems: 'center', gap: 4 }}>
        <div style={{ width: 7, height: 7, borderRadius: '50%', background: '#e2e8f0', flexShrink: 0 }} />
        <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.25)', borderStyle: 'dashed' }} />
        <span style={{ fontSize: 10, color: '#e2e8f0', fontWeight: 700, minWidth: 52, textAlign: 'right' }}>${fmt(last)}</span>
      </div>
    </div>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────
export default function StockInfoPanel({ info, onClose }: { info: StockPanelInfo; onClose: () => void }) {
  const [tab,     setTab]     = useState<'overview' | 'earnings' | 'technicals'>('overview')
  const [data,    setData]    = useState<ProfileResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  useEffect(() => {
    setLoading(true); setError(null)
    fetch(`${API_URL}/api/stock-profile/${info.symbol}`)
      .then(r => r.json())
      .then(d  => { setData(d); setLoading(false) })
      .catch(() => { setError('Failed to load profile'); setLoading(false) })
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
              {/* Company logo */}
              {p?.logo_url && (
                <img
                  src={p.logo_url}
                  alt={info.symbol}
                  style={{
                    width: 40, height: 40, borderRadius: 8, flexShrink: 0,
                    objectFit: 'contain', background: 'rgba(255,255,255,0.06)',
                    padding: 5, border: '1px solid rgba(255,255,255,0.08)',
                  }}
                  onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
                />
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center flex-wrap gap-2">
                  <span style={{ fontSize: 20, fontWeight: 800, color: '#e2e8f0', letterSpacing: '0.05em' }}>{info.symbol}</span>
                  {last > 0 && <span style={{ fontSize: 15, fontWeight: 700, color: '#e2e8f0' }}>${last.toFixed(2)}</span>}
                  <span style={{ fontSize: 12, color: chgColor, fontWeight: 600 }}>
                    {isUp ? '+' : ''}{info.change.toFixed(2)}&nbsp;({isUp ? '+' : ''}{info.changePct.toFixed(2)}%)
                  </span>
                </div>
                {p?.company_name && (
                  <div style={{ fontSize: 11, color: '#64748b', marginTop: 1 }}>
                    {p.company_name}
                    {p.exchange && <span style={{ marginLeft: 6, color: '#334155' }}>· {p.exchange}</span>}
                    {p.sector   && <span style={{ marginLeft: 6, color: '#334155' }}>· {p.sector}</span>}
                  </div>
                )}
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
          {!loading && !error && tab === 'technicals' && (
            <div>
              {sig ? (
                <>
                  <div className="flex items-center gap-2 mb-4 flex-wrap">
                    <span className="rounded px-2 py-0.5 text-xs font-bold"
                      style={sig.model === 'AGG' ? { background: 'rgba(251,191,36,0.15)',  color: '#fbbf24' }
                           : sig.model === 'CON' ? { background: 'rgba(165,180,252,0.12)', color: '#a5b4fc' }
                           :                       { background: 'rgba(45,212,191,0.12)',  color: '#2dd4bf' }}>
                      {sig.model}
                    </span>
                    <span className="rounded px-2 py-0.5 text-xs font-bold"
                      style={sig.side === 'LONG'
                        ? { background: 'rgba(74,222,128,0.12)', color: '#4ade80' }
                        : { background: 'rgba(248,113,113,0.12)', color: '#f87171' }}>
                      {sig.side}
                    </span>
                    {sig.signal_state && sig.signal_state !== 'NEUTRAL' && (
                      <span className="rounded px-2 py-0.5 text-xs font-bold"
                        style={sig.signal_state === 'ENTRY'
                          ? { background: sig.side === 'LONG' ? 'rgba(74,222,128,0.18)' : 'rgba(248,113,113,0.18)', color: sig.side === 'LONG' ? '#4ade80' : '#f87171' }
                          : { background: 'rgba(251,191,36,0.15)', color: '#fbbf24' }}>
                        {sig.signal_state}
                      </span>
                    )}
                  </div>

                  <TechnicalsChart sig={sig} last={last} />

                  <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8, fontSize: 12 }}>
                    <tbody>
                      {[
                        { label: 'Entry',        val: `$${sig.entry >= 100 ? sig.entry.toFixed(2) : sig.entry.toFixed(3)}`,   color: '#fbbf24' },
                        { label: 'Stop',         val: `$${sig.stop  >= 100 ? sig.stop.toFixed(2)  : sig.stop.toFixed(3)}`,    color: '#f87171' },
                        { label: 'Target',       val: `$${sig.target >= 100 ? sig.target.toFixed(2) : sig.target.toFixed(3)}`, color: '#4ade80' },
                        { label: 'Risk / Reward', val: sig.entry && sig.stop && sig.target ? `1 : ${Math.abs((sig.target - sig.entry) / (sig.entry - sig.stop)).toFixed(1)}` : '—', color: undefined },
                        { label: 'Daily Swing',   val: `${sig.swing_pct >= 0 ? '+' : ''}${sig.swing_pct.toFixed(1)}% of ${sig.typical_range.toFixed(2)}`, color: undefined },
                      ].map(r => (
                        <tr key={r.label} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                          <td style={{ padding: '6px 0', color: '#475569' }}>{r.label}</td>
                          <td style={{ padding: '6px 0', textAlign: 'right', fontWeight: 600, color: r.color || '#e2e8f0' }}>{r.val}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              ) : (
                <div style={{ color: '#475569', fontSize: 12, textAlign: 'center', padding: '32px 0' }}>
                  No active VBH signal for {info.symbol}
                  <div style={{ fontSize: 11, color: '#334155', marginTop: 6 }}>Levels appear when price reaches the signal zone</div>
                </div>
              )}
            </div>
          )}
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
