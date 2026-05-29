'use client'

import { useEffect, useState } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export interface StockPanelInfo {
  symbol:    string
  last:      number
  change:    number
  changePct: number
}

interface StockProfile {
  ticker:         string
  company_name:   string | null
  sector:         string | null
  industry:       string | null
  market_cap:     number | null
  pe_trailing:    number | null
  pe_forward:     number | null
  eps_trailing:   number | null
  week_52_high:   number | null
  week_52_low:    number | null
  analyst_rating: string | null
  analyst_count:  number | null
  target_price:   number | null
  upside_pct:     number | null
  beta:           number | null
  dividend_yield: number | null
  description:    string | null
  refreshed_at:   string | null
}

interface SignalData {
  side:    'LONG' | 'SHORT'
  model:   string
  entry:   number
  stop:    number
  target:  number
  l1:      number
  l2:      number
  l3:      number
  l4:      number
  last:    number
  swing_pct:       number
  current_range:   number
  typical_range:   number
  signal_state?: string | null
}

interface StockProfileResponse {
  ticker:  string
  profile: StockProfile | null
  signal:  SignalData | null
  last:    number | null
}

interface StockInfoPanelProps {
  info:    StockPanelInfo
  onClose: () => void
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtMarketCap(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`
  if (n >= 1e9)  return `$${(n / 1e9).toFixed(2)}B`
  if (n >= 1e6)  return `$${(n / 1e6).toFixed(2)}M`
  return `$${n.toLocaleString()}`
}

function fmtNum(n: number | null | undefined, decimals = 2): string {
  if (n == null) return '—'
  return n.toFixed(decimals)
}

function fmtPct(n: number | null | undefined): string {
  if (n == null) return '—'
  return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%`
}

const RATING_STYLE: Record<string, { color: string; bg: string }> = {
  'Strong Buy':  { color: '#4ade80', bg: 'rgba(74,222,128,0.15)'  },
  'Buy':         { color: '#86efac', bg: 'rgba(134,239,172,0.12)' },
  'Hold':        { color: '#fbbf24', bg: 'rgba(251,191,36,0.12)'  },
  'Sell':        { color: '#f87171', bg: 'rgba(248,113,113,0.12)' },
  'Strong Sell': { color: '#ef4444', bg: 'rgba(239,68,68,0.15)'   },
}

// Tiny row inside a data table
function Row({ label, value, valueColor }: { label: string; value: React.ReactNode; valueColor?: string }) {
  return (
    <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
      <td style={{ padding: '6px 0', color: '#64748b', fontSize: '12px', width: '48%', whiteSpace: 'nowrap' }}>
        {label}
      </td>
      <td style={{ padding: '6px 0 6px 8px', color: valueColor || '#e2e8f0', fontSize: '12px', fontWeight: 500, textAlign: 'right' }}>
        {value}
      </td>
    </tr>
  )
}

// ── 52-week range bar ─────────────────────────────────────────────────────────
function WeekRangeBar({ low, high, last }: { low: number; high: number; last: number }) {
  const range = high - low
  const pct   = range > 0 ? Math.min(100, Math.max(0, ((last - low) / range) * 100)) : 50

  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', color: '#64748b', marginBottom: 3 }}>
        <span>${low.toFixed(2)}</span>
        <span style={{ color: '#94a3b8', fontSize: '10px' }}>52-Week Range</span>
        <span>${high.toFixed(2)}</span>
      </div>
      <div style={{ position: 'relative', height: 4, background: 'rgba(255,255,255,0.08)', borderRadius: 2 }}>
        <div
          style={{
            position: 'absolute', left: 0, top: 0, height: '100%',
            width: `${pct}%`, borderRadius: 2,
            background: 'linear-gradient(to right, #f87171, #fbbf24, #4ade80)',
          }}
        />
        <div
          style={{
            position: 'absolute', top: -3, width: 10, height: 10,
            borderRadius: '50%', background: '#e2e8f0', border: '2px solid #1e1b2e',
            transform: 'translateX(-50%)',
            left: `${pct}%`,
          }}
        />
      </div>
    </div>
  )
}

// ── Technicals level chart ────────────────────────────────────────────────────
function TechnicalsChart({ sig, last }: { sig: SignalData; last: number }) {
  const levels = [
    { label: 'L4',     price: sig.l4,    color: '#7c3aed', side: 'upper' },
    { label: 'L3',     price: sig.l3,    color: '#a855f7', side: 'upper' },
    { label: 'Target', price: sig.target, color: '#4ade80', side: null    },
    { label: 'Entry',  price: sig.entry,  color: '#fbbf24', side: null    },
    { label: 'L2',     price: sig.l2,     color: '#2563eb', side: 'lower' },
    { label: 'L1',     price: sig.l1,     color: '#60a5fa', side: 'lower' },
    { label: 'Stop',   price: sig.stop,   color: '#f87171', side: null    },
  ].filter(l => l.price > 0)

  const prices = [...levels.map(l => l.price), last].filter(Boolean)
  const minP = Math.min(...prices) * 0.998
  const maxP = Math.max(...prices) * 1.002
  const range = maxP - minP || 1

  const pct = (p: number) => `${((maxP - p) / range * 100).toFixed(1)}%`

  return (
    <div style={{ position: 'relative', height: 240, margin: '12px 0' }}>
      {/* Price axis line */}
      <div style={{
        position: 'absolute', left: 56, top: 0, bottom: 0, width: 1,
        background: 'rgba(255,255,255,0.08)',
      }} />

      {/* Level labels */}
      {levels.map(lvl => (
        <div
          key={lvl.label}
          style={{
            position: 'absolute', top: pct(lvl.price), left: 0, right: 0,
            transform: 'translateY(-50%)',
            display: 'flex', alignItems: 'center', gap: 6,
          }}
        >
          <span style={{ width: 48, textAlign: 'right', fontSize: 10, color: lvl.color, fontWeight: 600 }}>
            {lvl.label}
          </span>
          <div style={{ flex: 1, height: 1, background: lvl.color, opacity: 0.45 }} />
          <span style={{ fontSize: 10, color: lvl.color, minWidth: 56, textAlign: 'right' }}>
            {lvl.price >= 100 ? lvl.price.toFixed(2) : lvl.price.toFixed(3)}
          </span>
        </div>
      ))}

      {/* Live price marker */}
      <div
        style={{
          position: 'absolute', top: pct(last), left: 56, right: 0,
          transform: 'translateY(-50%)',
          display: 'flex', alignItems: 'center', gap: 4,
        }}
      >
        <div style={{ width: 7, height: 7, borderRadius: '50%', background: '#e2e8f0', flexShrink: 0 }} />
        <div style={{ flex: 1, height: 1, background: 'rgba(255,255,255,0.3)', borderStyle: 'dashed' }} />
        <span style={{ fontSize: 10, color: '#e2e8f0', fontWeight: 700, minWidth: 56, textAlign: 'right' }}>
          {last >= 100 ? last.toFixed(2) : last.toFixed(3)}
        </span>
      </div>
    </div>
  )
}


// ── Main panel ────────────────────────────────────────────────────────────────
export default function StockInfoPanel({ info, onClose }: StockInfoPanelProps) {
  const [tab,  setTab]  = useState<'fundamentals' | 'technicals'>('fundamentals')
  const [data, setData] = useState<StockProfileResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    fetch(`${API_URL}/api/stock-profile/${info.symbol}`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false) })
      .catch(() => { setError('Failed to load profile'); setLoading(false) })
  }, [info.symbol])

  const p    = data?.profile
  const sig  = data?.signal
  const last = data?.last ?? info.last

  const isUp     = info.change >= 0
  const chgColor = isUp ? '#4ade80' : '#f87171'
  const rating   = p?.analyst_rating
  const rStyle   = rating ? RATING_STYLE[rating] : null

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-[1080]"
        style={{ background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(2px)' }}
        onClick={onClose}
      />

      {/* Panel */}
      <div
        className="fixed z-[1090] rounded-xl shadow-2xl overflow-hidden flex flex-col"
        style={{
          top: '50%', left: '50%',
          transform: 'translate(-50%, -50%)',
          width: 'min(560px, 96vw)',
          maxHeight: '88vh',
          background: '#0f0d1a',
          border: '1px solid rgba(255,255,255,0.10)',
          boxShadow: '0 0 0 1px rgba(0,0,0,0.8), 0 24px 64px rgba(0,0,0,0.9)',
        }}
      >
        {/* ── Header ─────────────────────────────────────────────── */}
        <div style={{ padding: '16px 20px 12px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', letterSpacing: '0.04em' }}>
                  {info.symbol}
                </span>
                {last > 0 && (
                  <span style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>
                    ${last.toFixed(2)}
                  </span>
                )}
                <span style={{ fontSize: 12, color: chgColor, fontWeight: 600 }}>
                  {isUp ? '+' : ''}{info.change.toFixed(2)} ({isUp ? '+' : ''}{info.changePct.toFixed(2)}%)
                </span>
              </div>
              {p?.company_name && (
                <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                  {p.company_name}
                  {p.sector && <span style={{ marginLeft: 6, color: '#475569' }}>· {p.sector}</span>}
                </div>
              )}
            </div>
            <button
              onClick={onClose}
              style={{ color: '#64748b', fontSize: 18, background: 'none', border: 'none', cursor: 'pointer', flexShrink: 0, lineHeight: 1, padding: 2 }}
            >
              ✕
            </button>
          </div>

          {/* Tab bar */}
          <div className="flex gap-1 mt-3">
            {(['fundamentals', 'technicals'] as const).map(t => (
              <button
                key={t}
                onClick={() => setTab(t)}
                style={{
                  padding: '5px 14px',
                  borderRadius: 6,
                  fontSize: 12,
                  fontWeight: 600,
                  textTransform: 'capitalize',
                  border: 'none',
                  cursor: 'pointer',
                  transition: 'all 0.15s',
                  background: tab === t ? 'rgba(168,85,247,0.2)'  : 'transparent',
                  color:      tab === t ? '#a855f7'               : '#64748b',
                  outline:    tab === t ? '1px solid rgba(168,85,247,0.35)' : 'none',
                }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* ── Body ───────────────────────────────────────────────── */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px 20px' }}>

          {loading && (
            <div className="flex items-center justify-center py-16">
              <div
                className="w-7 h-7 rounded-full border-2 border-t-transparent animate-spin"
                style={{ borderColor: '#a855f7', borderTopColor: 'transparent' }}
              />
            </div>
          )}

          {error && !loading && (
            <div style={{ color: '#f87171', fontSize: 13, textAlign: 'center', padding: '24px 0' }}>
              {error}
            </div>
          )}

          {!loading && !error && tab === 'fundamentals' && (
            <div>
              {/* Analyst rating hero */}
              {rating && rStyle && (
                <div
                  className="rounded-lg flex items-center justify-between mb-4"
                  style={{ padding: '10px 14px', background: rStyle.bg, border: `1px solid ${rStyle.color}33` }}
                >
                  <div>
                    <div style={{ fontSize: 11, color: '#64748b', marginBottom: 2 }}>Analyst Consensus</div>
                    <div style={{ fontSize: 16, fontWeight: 700, color: rStyle.color }}>{rating}</div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    {p?.analyst_count && (
                      <div style={{ fontSize: 11, color: '#64748b' }}>{p.analyst_count} analysts</div>
                    )}
                    {p?.target_price && (
                      <div style={{ fontSize: 13, fontWeight: 600, color: '#e2e8f0' }}>
                        Target ${p.target_price.toFixed(2)}
                        {p.upside_pct != null && (
                          <span style={{ marginLeft: 6, color: p.upside_pct >= 0 ? '#4ade80' : '#f87171', fontSize: 11 }}>
                            ({p.upside_pct >= 0 ? '+' : ''}{p.upside_pct.toFixed(1)}%)
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* 52-week range */}
              {p?.week_52_high && p?.week_52_low && last > 0 && (
                <WeekRangeBar low={p.week_52_low} high={p.week_52_high} last={last} />
              )}

              {/* Data table */}
              <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 14 }}>
                <tbody>
                  {p?.market_cap != null && (
                    <Row label="Market Cap" value={fmtMarketCap(p.market_cap)} />
                  )}
                  {p?.pe_trailing != null && (
                    <Row label="P/E (Trailing)" value={fmtNum(p.pe_trailing, 1)} />
                  )}
                  {p?.pe_forward != null && (
                    <Row label="P/E (Forward)" value={fmtNum(p.pe_forward, 1)} />
                  )}
                  {p?.eps_trailing != null && (
                    <Row label="EPS (TTM)" value={`$${fmtNum(p.eps_trailing)}`} />
                  )}
                  {p?.beta != null && (
                    <Row label="Beta" value={fmtNum(p.beta, 2)} />
                  )}
                  {p?.dividend_yield != null && p.dividend_yield > 0 && (
                    <Row label="Dividend Yield" value={fmtPct(p.dividend_yield * 100)} />
                  )}
                  {p?.industry && (
                    <Row label="Industry" value={p.industry} />
                  )}
                </tbody>
              </table>

              {/* Description */}
              {p?.description && (
                <div style={{ marginTop: 14, padding: '10px 12px', background: 'rgba(255,255,255,0.03)', borderRadius: 8, borderLeft: '3px solid rgba(168,85,247,0.3)' }}>
                  <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>About</div>
                  <p style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6, margin: 0 }}>
                    {p.description}
                  </p>
                </div>
              )}

              {!p && (
                <div style={{ color: '#64748b', fontSize: 12, textAlign: 'center', padding: '24px 0' }}>
                  Fundamentals loading — refreshes daily at 6:00 AM ET
                </div>
              )}
            </div>
          )}

          {!loading && !error && tab === 'technicals' && (
            <div>
              {sig ? (
                <>
                  {/* Signal header */}
                  <div className="flex items-center gap-2 mb-3 flex-wrap">
                    <span
                      className="rounded px-2 py-0.5 text-xs font-bold"
                      style={
                        sig.model === 'AGG'  ? { background: 'rgba(251,191,36,0.15)',  color: '#fbbf24' } :
                        sig.model === 'CON'  ? { background: 'rgba(165,180,252,0.12)', color: '#a5b4fc' } :
                                               { background: 'rgba(45,212,191,0.12)',  color: '#2dd4bf' }
                      }
                    >
                      {sig.model}
                    </span>
                    <span
                      className="rounded px-2 py-0.5 text-xs font-bold"
                      style={sig.side === 'LONG'
                        ? { background: 'rgba(74,222,128,0.12)', color: '#4ade80' }
                        : { background: 'rgba(248,113,113,0.12)', color: '#f87171' }
                      }
                    >
                      {sig.side}
                    </span>
                    {sig.signal_state && sig.signal_state !== 'NEUTRAL' && (
                      <span
                        className="rounded px-2 py-0.5 text-xs font-bold"
                        style={
                          sig.signal_state === 'ENTRY'
                            ? { background: sig.side === 'LONG' ? 'rgba(74,222,128,0.18)' : 'rgba(248,113,113,0.18)', color: sig.side === 'LONG' ? '#4ade80' : '#f87171' }
                            : { background: 'rgba(251,191,36,0.15)', color: '#fbbf24' }
                        }
                      >
                        {sig.signal_state}
                      </span>
                    )}
                  </div>

                  {/* VBH level chart */}
                  <TechnicalsChart sig={sig} last={last} />

                  {/* Key levels table */}
                  <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8 }}>
                    <tbody>
                      <Row label="Entry"  value={`$${sig.entry >= 100 ? sig.entry.toFixed(2) : sig.entry.toFixed(3)}`}  valueColor="#fbbf24" />
                      <Row label="Stop"   value={`$${sig.stop >= 100 ? sig.stop.toFixed(2) : sig.stop.toFixed(3)}`}     valueColor="#f87171" />
                      <Row label="Target" value={`$${sig.target >= 100 ? sig.target.toFixed(2) : sig.target.toFixed(3)}`} valueColor="#4ade80" />
                      <Row label="Risk/Reward" value={
                        sig.entry && sig.stop && sig.target
                          ? `1 : ${Math.abs((sig.target - sig.entry) / (sig.entry - sig.stop)).toFixed(1)}`
                          : '—'
                      } />
                      <Row label="L1 (Lower σ)" value={sig.l1.toFixed(2)} valueColor="#60a5fa" />
                      <Row label="L2 (Upper σ)" value={sig.l2.toFixed(2)} valueColor="#2563eb" />
                      <Row label="Daily Swing"
                        value={`${sig.swing_pct >= 0 ? '+' : ''}${sig.swing_pct.toFixed(1)}% of ${sig.typical_range.toFixed(2)}`}
                      />
                    </tbody>
                  </table>
                </>
              ) : (
                <div style={{ color: '#64748b', fontSize: 12, textAlign: 'center', padding: '32px 0' }}>
                  No active VBH signal for {info.symbol}
                  <div style={{ fontSize: 11, color: '#475569', marginTop: 6 }}>
                    Levels appear when price reaches the signal zone
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Footer ─────────────────────────────────────────────── */}
        {p?.refreshed_at && (
          <div style={{ padding: '8px 20px', borderTop: '1px solid rgba(255,255,255,0.06)', fontSize: 10, color: '#334155', textAlign: 'right' }}>
            Fundamentals as of {new Date(p.refreshed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
          </div>
        )}
      </div>
    </>
  )
}
