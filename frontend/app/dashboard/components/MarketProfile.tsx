'use client'

import { useEffect, useState, useCallback } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

// ── Futures available in the Market Profile page ───────────────────────────────
const SYMBOLS = ['/ES', '/NQ', '/YM', '/RTY', '/GC', '/CL', '/SI', '/NG', '/HG', '/ZB', '/BTC']

// ── TPO letter colours – warm progression through the day ────────────────────
const LETTER_COLOR: Record<string, string> = {
  A: '#60a5fa', B: '#60a5fa',  // blue  — Initial Balance
  C: '#22d3ee', D: '#22d3ee',  // cyan
  E: '#4ade80', F: '#4ade80',  // green
  G: '#fbbf24', H: '#fbbf24',  // amber — midday
  I: '#fb923c', J: '#fb923c',  // orange
  K: '#f87171', L: '#f87171', M: '#f87171',  // red — late session
}
const DEFAULT_LETTER_COLOR = '#94a3b8'

// ── Types ─────────────────────────────────────────────────────────────────────
interface ProfileRow   { price: number; letters: string; count: number }
interface PeriodRange  { high: number; low: number }
interface SessionProfile {
  profile:       ProfileRow[]
  poc:           number | null
  vah:           number | null
  val:           number | null
  single_prints: number[]
  ib_high:       number | null
  ib_low:        number | null
  ib_range:      number | null
  periods:       number
  period_ranges: Record<string, PeriodRange>
  session_high:  number | null
  session_low:   number | null
  date?:         string
  high?:         number
  low?:          number
  close?:        number
}
interface Overnight { high: number | null; low: number | null; poc: number | null; vah: number | null; val: number | null }
interface Opening   { type: string; label: string; description: string; inside_prior_va: boolean | null; vs_prior_vah?: number; vs_prior_val?: number; vs_prior_poc?: number }
interface DayType   { type: string; label: string; description: string; ib_range?: number; ext_up?: number; ext_down?: number; ib_ratio?: number }
interface Rule80    { triggered: boolean; direction?: string; target?: number; already_hit?: boolean; label?: string; description: string }
interface MPData {
  symbol:        string
  tick:          number
  computed_at:   string
  current_price: number | null
  today:         SessionProfile
  prior_rth:     SessionProfile
  overnight:     Overnight
  opening:       Opening
  day_type:      DayType
  rule_80:       Rule80
}

// ── Opening type badge config ─────────────────────────────────────────────────
const OPEN_CFG: Record<string, { bg: string; color: string; border: string }> = {
  OA:      { bg: 'rgba(96,165,250,0.12)',  color: '#60a5fa', border: 'rgba(96,165,250,0.3)'  },
  OD:      { bg: 'rgba(74,222,128,0.12)',  color: '#4ade80', border: 'rgba(74,222,128,0.3)'  },
  OTD:     { bg: 'rgba(251,191,36,0.12)',  color: '#fbbf24', border: 'rgba(251,191,36,0.3)'  },
  ORR:     { bg: 'rgba(248,113,113,0.12)', color: '#f87171', border: 'rgba(248,113,113,0.3)' },
  PENDING: { bg: 'rgba(148,163,184,0.08)', color: '#94a3b8', border: 'rgba(148,163,184,0.2)' },
  UNKNOWN: { bg: 'rgba(148,163,184,0.08)', color: '#94a3b8', border: 'rgba(148,163,184,0.2)' },
  PREMARKET:{ bg: 'rgba(148,163,184,0.08)',color: '#94a3b8', border: 'rgba(148,163,184,0.2)' },
}

const DAY_CFG: Record<string, { bg: string; color: string }> = {
  TREND:          { bg: 'rgba(251,146,60,0.15)',  color: '#fb923c' },
  NORMAL_VAR_UP:  { bg: 'rgba(74,222,128,0.12)',  color: '#4ade80' },
  NORMAL_VAR_DOWN:{ bg: 'rgba(248,113,113,0.12)', color: '#f87171' },
  NEUTRAL:        { bg: 'rgba(251,191,36,0.12)',  color: '#fbbf24' },
  NEUTRAL_EXTREME:{ bg: 'rgba(251,191,36,0.15)',  color: '#fbbf24' },
  NORMAL:         { bg: 'rgba(96,165,250,0.10)',  color: '#60a5fa' },
  DEVELOPING:     { bg: 'rgba(148,163,184,0.08)', color: '#94a3b8' },
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function fmt(p: number | null | undefined, decimals = 2): string {
  if (p == null) return '—'
  return p.toFixed(decimals)
}

function InfoCard({ title, badge, badgeColor, badgeBg, badgeBorder, description, children }: {
  title: string; badge: string; badgeColor: string; badgeBg: string; badgeBorder: string
  description: string; children?: React.ReactNode
}) {
  return (
    <div style={{ padding: '14px 16px', background: 'var(--bg-panel)',
      border: '1px solid var(--border)', borderRadius: '10px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
        <span style={{ fontSize: '10px', fontWeight: 700, color: 'var(--text-dim)',
          textTransform: 'uppercase', letterSpacing: '0.08em' }}>{title}</span>
        <span style={{ fontSize: '11px', fontWeight: 700, color: badgeColor,
          background: badgeBg, border: `1px solid ${badgeBorder}`,
          borderRadius: '5px', padding: '2px 8px' }}>{badge}</span>
      </div>
      <p style={{ fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.55, marginBottom: children ? '10px' : 0 }}>
        {description}
      </p>
      {children}
    </div>
  )
}

// ── TPO Profile visual ────────────────────────────────────────────────────────
function TpoChart({ today, prior, overnight, currentPrice, tick }: {
  today:        SessionProfile
  prior:        SessionProfile
  overnight:    Overnight
  currentPrice: number | null
  tick:         number
}) {
  if (!today.profile.length && !prior.profile.length) {
    return (
      <div style={{ padding: '48px 0', textAlign: 'center', color: 'var(--text-dim)', fontSize: '13px' }}>
        No profile data — market may be closed or pre-market
      </div>
    )
  }

  // Merge price universe from both profiles
  const allPrices = new Set<number>()
  today.profile.forEach(r => allPrices.add(r.price))
  prior.profile.forEach(r => allPrices.add(r.price))
  const sortedPrices = Array.from(allPrices).sort((a, b) => b - a)

  // Build lookup maps
  const todayMap  = new Map(today.profile.map(r => [r.price, r]))
  const priorMap  = new Map(prior.profile.map(r => [r.price, r]))

  // Max letters count — for proportional width
  const maxTodayCount = Math.max(1, ...today.profile.map(r => r.count))
  const maxPriorCount = Math.max(1, ...prior.profile.map(r => r.count))

  // Single print lookup
  const todaySP = new Set(today.single_prints)
  const priorSP = new Set(prior.single_prints)

  const FONT    = "'SF Mono', ui-monospace, monospace"
  const ROW_H   = 16   // px per tick row
  const PRICE_W = 58   // px for price column
  const SEP     = 10   // px gap between prior and today panels
  const PRIOR_W = 130  // px prior panel
  const TODAY_W = 200  // px today panel
  const TOTAL_W = PRICE_W + SEP + PRIOR_W + SEP + TODAY_W
  const totalH  = sortedPrices.length * ROW_H + 4

  // Key price sets for highlight
  const keyPrices = new Set([
    today.poc, today.vah, today.val, today.ib_high, today.ib_low,
    prior.poc, prior.vah, prior.val,
    overnight.vah, overnight.val, overnight.poc,
    currentPrice,
  ].filter(Boolean) as number[])

  // Close enough to be "at" a key level
  const near = (p: number, ref: number | null | undefined) =>
    ref != null && Math.abs(p - ref) < tick * 0.6

  return (
    <div style={{ overflowY: 'auto', overflowX: 'hidden' }}>
      {/* Column headers */}
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: '6px',
        paddingLeft: `${PRICE_W + SEP}px`, gap: `${SEP}px` }}>
        <div style={{ width: PRIOR_W, textAlign: 'center', fontSize: '9px', fontWeight: 700,
          color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
          Prior RTH{prior.date ? ` · ${prior.date}` : ''}
        </div>
        <div style={{ width: TODAY_W, textAlign: 'center', fontSize: '9px', fontWeight: 700,
          color: '#60a5fa', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
          Today — {today.periods} period{today.periods !== 1 ? 's' : ''} ({today.periods < 13 ? 'developing' : 'complete'})
        </div>
      </div>

      <svg
        width={TOTAL_W}
        height={totalH}
        style={{ display: 'block', fontFamily: FONT, overflow: 'visible' }}
      >
        {/* Value area shading — prior (left panel): upper=green, POC=purple, lower=red */}
        {prior.vah != null && prior.val != null && prior.poc != null && (() => {
          const idxVAH = sortedPrices.findIndex(p => p <= prior.vah!)
          const idxPOC = sortedPrices.findIndex(p => p <= prior.poc!)
          const idxVAL = sortedPrices.findIndex(p => p <= prior.val!)
          if (idxVAH < 0 || idxPOC < 0 || idxVAL < 0) return null
          const x = PRICE_W + SEP
          const yVAH = idxVAH * ROW_H
          const yPOC = idxPOC * ROW_H
          const yVAL = idxVAL * ROW_H + ROW_H
          return (
            <g>
              {/* VAH → POC: green (bullish upper zone) */}
              <rect x={x} y={yVAH} width={PRIOR_W} height={yPOC - yVAH}
                fill="#4ade80" fillOpacity={0.07} />
              {/* POC row: purple highlight */}
              <rect x={x} y={yPOC} width={PRIOR_W} height={ROW_H}
                fill="#a78bfa" fillOpacity={0.18} />
              {/* POC → VAL: red (bearish lower zone) */}
              <rect x={x} y={yPOC + ROW_H} width={PRIOR_W} height={yVAL - yPOC - ROW_H}
                fill="#f87171" fillOpacity={0.07} />
            </g>
          )
        })()}

        {/* Value area shading — today (right panel): upper=green, POC=purple, lower=red */}
        {today.vah != null && today.val != null && today.poc != null && (() => {
          const idxVAH = sortedPrices.findIndex(p => p <= today.vah!)
          const idxPOC = sortedPrices.findIndex(p => p <= today.poc!)
          const idxVAL = sortedPrices.findIndex(p => p <= today.val!)
          if (idxVAH < 0 || idxPOC < 0 || idxVAL < 0) return null
          const x = PRICE_W + SEP + PRIOR_W + SEP
          const yVAH = idxVAH * ROW_H
          const yPOC = idxPOC * ROW_H
          const yVAL = idxVAL * ROW_H + ROW_H
          return (
            <g>
              {/* VAH → POC: green */}
              <rect x={x} y={yVAH} width={TODAY_W} height={yPOC - yVAH}
                fill="#4ade80" fillOpacity={0.09} />
              {/* POC row: purple highlight */}
              <rect x={x} y={yPOC} width={TODAY_W} height={ROW_H}
                fill="#a78bfa" fillOpacity={0.22} />
              {/* POC → VAL: red */}
              <rect x={x} y={yPOC + ROW_H} width={TODAY_W} height={yVAL - yPOC - ROW_H}
                fill="#f87171" fillOpacity={0.09} />
            </g>
          )
        })()}

        {/* Overnight range bracket (right side of today panel) */}
        {overnight.high != null && overnight.low != null && (() => {
          const yi = sortedPrices.findIndex(p => p <= overnight.high!)
          const yj = sortedPrices.findIndex(p => p <= overnight.low!)
          if (yi < 0 || yj < 0) return null
          const y1 = yi * ROW_H
          const y2 = yj * ROW_H + ROW_H
          const x  = PRICE_W + SEP + PRIOR_W + SEP + TODAY_W + 4
          return (
            <g>
              <line x1={x} y1={y1} x2={x} y2={y2} stroke="#22d3ee" strokeWidth={2} strokeOpacity={0.4} />
              <line x1={x} y1={y1} x2={x + 4} y2={y1} stroke="#22d3ee" strokeWidth={1.5} strokeOpacity={0.5} />
              <line x1={x} y1={y2} x2={x + 4} y2={y2} stroke="#22d3ee" strokeWidth={1.5} strokeOpacity={0.5} />
              <text x={x + 7} y={y1 + 9} fill="#22d3ee" fontSize={8} fontWeight="600" opacity={0.7}>ONH</text>
              <text x={x + 7} y={y2 - 2} fill="#22d3ee" fontSize={8} fontWeight="600" opacity={0.7}>ONL</text>
            </g>
          )
        })()}

        {/* Price rows */}
        {sortedPrices.map((price, i) => {
          const y        = i * ROW_H
          const todayRow = todayMap.get(price)
          const priorRow = priorMap.get(price)
          const isCurrent  = currentPrice != null && Math.abs(price - currentPrice) < tick * 0.6
          const isTodayPOC = near(price, today.poc)
          const isPriorPOC = near(price, prior.poc)
          const isTodayVAH = near(price, today.vah)
          const isTodayVAL = near(price, today.val)
          const isPriorVAH = near(price, prior.vah)
          const isPriorVAL = near(price, prior.val)
          const isTodayIBH = near(price, today.ib_high)
          const isTodayIBL = near(price, today.ib_low)
          const isSPToday  = todaySP.has(price)
          const isSPPrior  = priorSP.has(price)

          // Price label colour
          const priceColor = isCurrent   ? '#fbbf24'
                           : isTodayPOC  ? '#a78bfa'
                           : isTodayVAH || isTodayVAL ? '#60a5fa'
                           : isPriorPOC  ? '#818cf8'
                           : 'var(--text-dim)'

          // Prior panel — bar width proportional
          const priorBarW = priorRow ? Math.max(4, Math.round((priorRow.count / maxPriorCount) * PRIOR_W)) : 0
          // Today panel
          const todayBarW = todayRow ? Math.max(4, Math.round((todayRow.count / maxTodayCount) * TODAY_W * 0.85)) : 0

          return (
            <g key={price}>
              {/* Row highlight for current price */}
              {isCurrent && (
                <rect x={0} y={y} width={TOTAL_W} height={ROW_H}
                  fill="#fbbf24" fillOpacity={0.06} />
              )}

              {/* Price */}
              <text x={PRICE_W - 4} y={y + ROW_H - 4}
                fill={priceColor} fontSize={9}
                fontWeight={isCurrent || isTodayPOC ? '700' : '400'}
                textAnchor="end" opacity={0.85}>
                {price.toFixed(2)}
              </text>

              {/* Prior RTH letters */}
              {priorRow && (
                <g>
                  <rect x={PRICE_W + SEP} y={y + 2} width={priorBarW} height={ROW_H - 4}
                    fill={isSPPrior ? 'rgba(248,113,113,0.15)' : 'rgba(129,140,248,0.12)'}
                    rx={1} />
                  <text x={PRICE_W + SEP + 3} y={y + ROW_H - 4}
                    fontSize={8.5} fill={isPriorPOC ? '#a78bfa' : isSPPrior ? '#f87171' : '#475569'}
                    fontWeight={isPriorPOC ? '700' : '400'}>
                    {priorRow.letters}
                  </text>
                </g>
              )}

              {/* Today's letters */}
              {todayRow && (
                <g>
                  {/* Letter-by-letter coloring */}
                  {todayRow.letters.split('').map((ltr, li) => (
                    <text key={li}
                      x={PRICE_W + SEP + PRIOR_W + SEP + li * 7}
                      y={y + ROW_H - 4}
                      fontSize={9} fontWeight={isTodayPOC ? '700' : '500'}
                      fill={LETTER_COLOR[ltr] ?? DEFAULT_LETTER_COLOR}
                      opacity={isSPToday ? 0.6 : 1}>
                      {ltr}
                    </text>
                  ))}
                </g>
              )}

              {/* Key level annotations (right margin) */}
              {(isTodayPOC || isTodayVAH || isTodayVAL || isTodayIBH || isTodayIBL || isCurrent) && (
                <text
                  x={PRICE_W + SEP + PRIOR_W + SEP + TODAY_W - 2}
                  y={y + ROW_H - 4}
                  fontSize={7.5} fontWeight="700" textAnchor="end"
                  fill={isTodayPOC ? '#a78bfa' : isCurrent ? '#fbbf24'
                      : isTodayIBH || isTodayIBL ? '#fb923c' : '#60a5fa'}
                  opacity={0.8}>
                  {isTodayPOC ? '◆POC' : isCurrent ? '▶' : isTodayVAH ? '▲VAH' : isTodayVAL ? '▼VAL'
                    : isTodayIBH ? 'IBH' : isTodayIBL ? 'IBL' : ''}
                </text>
              )}

              {/* Prior POC / VAH / VAL annotations */}
              {(isPriorPOC || isPriorVAH || isPriorVAL) && (
                <text
                  x={PRICE_W + SEP + PRIOR_W - 2}
                  y={y + ROW_H - 4}
                  fontSize={7.5} fontWeight="700" textAnchor="end"
                  fill={isPriorPOC ? '#a78bfa' : '#818cf8'}
                  opacity={0.65}>
                  {isPriorPOC ? '◆' : isPriorVAH ? '▲' : '▼'}
                </text>
              )}
            </g>
          )
        })}

        {/* Current price horizontal line */}
        {currentPrice != null && sortedPrices.length > 0 && (() => {
          const idx = sortedPrices.findIndex(p => p <= currentPrice)
          if (idx < 0) return null
          const cy = idx * ROW_H + ROW_H / 2
          return (
            <line x1={PRICE_W} y1={cy} x2={PRICE_W + SEP + PRIOR_W + SEP + TODAY_W}
              y2={cy} stroke="#fbbf24" strokeWidth={0.8} strokeDasharray="3,3" strokeOpacity={0.5} />
          )
        })()}
      </svg>

    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
// ── TPO letter → time mapping ─────────────────────────────────────────────────
const TPO_LETTERS = [
  { letter: 'A', time: '9:30 – 10:00 AM', note: 'Initial Balance' },
  { letter: 'B', time: '10:00 – 10:30 AM', note: 'Initial Balance' },
  { letter: 'C', time: '10:30 – 11:00 AM', note: '' },
  { letter: 'D', time: '11:00 – 11:30 AM', note: '' },
  { letter: 'E', time: '11:30 – 12:00 PM', note: '' },
  { letter: 'F', time: '12:00 – 12:30 PM', note: '' },
  { letter: 'G', time: '12:30 – 1:00 PM',  note: 'Lunch lull' },
  { letter: 'H', time: '1:00 – 1:30 PM',   note: '' },
  { letter: 'I', time: '1:30 – 2:00 PM',   note: '' },
  { letter: 'J', time: '2:00 – 2:30 PM',   note: '' },
  { letter: 'K', time: '2:30 – 3:00 PM',   note: '' },
  { letter: 'L', time: '3:00 – 3:30 PM',   note: 'Late session' },
  { letter: 'M', time: '3:30 – 4:00 PM',   note: 'Closing period' },
]

function HelpModal({ onClose }: { onClose: () => void }) {
  return (
    <>
      <div onClick={onClose}
        style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 1090 }} />
      <div style={{
        position: 'fixed', top: '50%', left: '50%',
        transform: 'translate(-50%, -50%)',
        width: '380px', zIndex: 1100,
        background: '#13111d',
        border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: '14px',
        boxShadow: '0 24px 64px rgba(0,0,0,0.7)',
        overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{ padding: '16px 20px 12px',
          borderBottom: '1px solid rgba(255,255,255,0.07)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '0.03em' }}>
              TPO Letter Reference
            </div>
            <div style={{ fontSize: '11px', color: 'var(--text-dim)', marginTop: '2px' }}>
              Each letter = one 30-min period · All times ET
            </div>
          </div>
          <button onClick={onClose}
            style={{ fontSize: '18px', color: 'var(--text-dim)', background: 'none',
              border: 'none', cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>

        {/* Letter table */}
        <div style={{ padding: '12px 20px 20px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '28px 1fr 1fr', gap: '0',
            marginBottom: '6px' }}>
            {['', 'Time (ET)', 'Note'].map(h => (
              <div key={h} style={{ fontSize: '9px', fontWeight: 700, color: 'var(--text-dim)',
                textTransform: 'uppercase', letterSpacing: '0.07em', paddingBottom: '6px',
                borderBottom: '1px solid rgba(255,255,255,0.07)' }}>{h}</div>
            ))}
          </div>
          {TPO_LETTERS.map(({ letter, time, note }) => {
            const color = LETTER_COLOR[letter] ?? DEFAULT_LETTER_COLOR
            const isIB  = letter === 'A' || letter === 'B'
            return (
              <div key={letter} style={{
                display: 'grid', gridTemplateColumns: '28px 1fr 1fr',
                alignItems: 'center', padding: '5px 0',
                borderBottom: '1px solid rgba(255,255,255,0.04)',
                background: isIB ? 'rgba(96,165,250,0.04)' : 'transparent',
              }}>
                <div style={{
                  fontFamily: "'SF Mono', monospace", fontSize: '13px', fontWeight: 700,
                  color, width: '22px', height: '22px', borderRadius: '4px',
                  background: `${color}18`, display: 'flex', alignItems: 'center',
                  justifyContent: 'center',
                }}>{letter}</div>
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>
                  {time}
                </div>
                <div style={{ fontSize: '10px', color: isIB ? '#60a5fa' : 'var(--text-dim)',
                  fontWeight: isIB ? 600 : 400 }}>
                  {note}
                </div>
              </div>
            )
          })}

          {/* Footer note */}
          <div style={{ marginTop: '12px', padding: '10px 12px',
            background: 'rgba(167,139,250,0.08)', borderRadius: '8px',
            border: '1px solid rgba(167,139,250,0.18)' }}>
            <div style={{ fontSize: '10px', color: '#a78bfa', fontWeight: 700, marginBottom: '4px' }}>
              How to read the profile
            </div>
            <div style={{ fontSize: '11px', color: 'var(--text-dim)', lineHeight: 1.5 }}>
              The wider a price row (more letters), the more time was spent there — that is
              accepted value. A single-letter row is a <strong style={{ color: '#f87171' }}>single
              print</strong>: price passed through fast and is likely to be revisited.
              The widest row is the <strong style={{ color: '#a78bfa' }}>POC</strong>.
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

export default function MarketProfile() {
  const [symbol,   setSymbol]   = useState('/ES')
  const [data,     setData]     = useState<MPData | null>(null)
  const [loading,  setLoading]  = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [showHelp, setShowHelp] = useState(false)

  const load = useCallback(async (sym: string) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(
        `${API_URL}/api/market-profile/${encodeURIComponent(sym)}`,
        { cache: 'no-store' }
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setData(await res.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(symbol) }, [symbol, load])

  // Auto-refresh every 60 s during RTH
  useEffect(() => {
    const id = setInterval(() => {
      const etH = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' })).getHours()
      if (etH >= 9 && etH < 16) load(symbol)
    }, 60_000)
    return () => clearInterval(id)
  }, [symbol, load])

  const openCfg  = data ? (OPEN_CFG[data.opening.type]  ?? OPEN_CFG['UNKNOWN']) : OPEN_CFG['UNKNOWN']
  const dayCfg   = data ? (DAY_CFG[data.day_type.type]  ?? DAY_CFG['DEVELOPING']) : DAY_CFG['DEVELOPING']
  const rule80c  = data?.rule_80.triggered
    ? (data.rule_80.direction === 'LONG'
        ? { bg: 'rgba(74,222,128,0.15)', color: '#4ade80', border: 'rgba(74,222,128,0.3)' }
        : { bg: 'rgba(248,113,113,0.15)', color: '#f87171', border: 'rgba(248,113,113,0.3)' })
    : { bg: 'rgba(148,163,184,0.08)', color: '#94a3b8', border: 'rgba(148,163,184,0.2)' }

  return (
    <div style={{ padding: '24px', minHeight: '100vh', background: 'var(--bg-main)' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '18px', fontWeight: 700, color: 'var(--text-primary)',
            letterSpacing: '0.04em', marginBottom: '2px' }}>
            Market Profile
          </h1>
          <p style={{ fontSize: '11px', color: 'var(--text-dim)' }}>
            J. Dalton TPO · Value Area · Opening Type · Day Classification
            {data && <span style={{ marginLeft: 8, opacity: 0.5 }}>· {data.computed_at}</span>}
          </p>
        </div>

        {/* Symbol selector */}
        <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
          {SYMBOLS.map(sym => (
            <button key={sym}
              onClick={() => setSymbol(sym)}
              style={{
                fontSize: '11px', fontWeight: 600, padding: '4px 10px',
                borderRadius: '6px', cursor: 'pointer',
                background:   symbol === sym ? 'rgba(96,165,250,0.15)' : 'rgba(255,255,255,0.04)',
                color:        symbol === sym ? '#60a5fa' : 'var(--text-dim)',
                border:       `1px solid ${symbol === sym ? 'rgba(96,165,250,0.4)' : 'rgba(255,255,255,0.08)'}`,
                transition:   'all 0.12s',
              }}>
              {sym}
            </button>
          ))}
          <button onClick={() => load(symbol)} title="Refresh"
            style={{ fontSize: '11px', padding: '4px 8px', borderRadius: '6px', cursor: 'pointer',
              background: 'rgba(255,255,255,0.04)', color: 'var(--text-dim)',
              border: '1px solid rgba(255,255,255,0.08)' }}>
            ↺
          </button>
        </div>
      </div>

      {/* ── Key Levels Strip ── */}
      {data && (
        <div style={{
          display: 'flex', alignItems: 'stretch', marginBottom: '20px',
          background: 'var(--bg-panel)', border: '1px solid var(--border)',
          borderRadius: '12px', overflow: 'hidden',
        }}>
          {/* Current Price */}
          <div style={{ padding: '12px 20px', display: 'flex', alignItems: 'center',
            borderRight: '1px solid var(--border)', flexShrink: 0 }}>
            <div>
              <div style={{ fontSize: '9px', fontWeight: 700, color: 'var(--text-dim)',
                textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '3px' }}>
                {data.symbol}
              </div>
              <div style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-primary)',
                fontFamily: "'SF Mono', monospace", lineHeight: 1 }}>
                {fmt(data.current_price)}
              </div>
            </div>
          </div>

          {/* Grouped level sections */}
          {[
            {
              label: 'Prior RTH',
              color: '#a78bfa',
              bg:    'rgba(167,139,250,0.05)',
              items: [
                { key: 'POC', value: data.prior_rth.poc, color: '#a78bfa' },
                { key: 'VAH', value: data.prior_rth.vah, color: '#818cf8' },
                { key: 'VAL', value: data.prior_rth.val, color: '#818cf8' },
              ],
            },
            {
              label: 'Overnight',
              color: '#22d3ee',
              bg:    'rgba(34,211,238,0.04)',
              items: [
                { key: 'ONH', value: data.overnight.high, color: '#22d3ee' },
                { key: 'ONL', value: data.overnight.low,  color: '#22d3ee' },
              ],
            },
            {
              label: 'Initial Balance',
              color: '#fb923c',
              bg:    'rgba(251,146,60,0.05)',
              items: [
                { key: 'IB High', value: data.today.ib_high, color: '#fb923c' },
                { key: 'IB Low',  value: data.today.ib_low,  color: '#fb923c' },
              ],
            },
            {
              label: 'Developing',
              color: '#60a5fa',
              bg:    'rgba(96,165,250,0.05)',
              items: [
                { key: 'POC', value: data.today.poc, color: '#c084fc' },
                { key: 'VAH', value: data.today.vah, color: '#60a5fa' },
                { key: 'VAL', value: data.today.val, color: '#60a5fa' },
              ],
            },
          ].map(({ label, color, bg, items }) => (
            <div key={label} style={{
              padding: '10px 16px', background: bg,
              borderRight: '1px solid var(--border)',
              display: 'flex', flexDirection: 'column', justifyContent: 'center', gap: '6px',
            }}>
              {/* Section label */}
              <div style={{ fontSize: '9px', fontWeight: 700, color,
                textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                {label}
              </div>
              {/* Items in a row */}
              <div style={{ display: 'flex', gap: '14px' }}>
                {items.map(({ key, value, color: c }) => value != null && (
                  <div key={key}>
                    <div style={{ fontSize: '9px', color: 'var(--text-dim)', fontWeight: 600,
                      letterSpacing: '0.05em', marginBottom: '1px' }}>{key}</div>
                    <div style={{ fontSize: '13px', fontWeight: 700, color: c,
                      fontFamily: "'SF Mono', monospace" }}>
                      {fmt(value)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}

          {/* Help button — right edge */}
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', padding: '0 16px' }}>
            <button
              onClick={() => setShowHelp(true)}
              title="TPO letter reference"
              style={{
                fontSize: '11px', fontWeight: 700, padding: '6px 14px',
                borderRadius: '7px', cursor: 'pointer',
                background: 'rgba(167,139,250,0.10)',
                color: '#a78bfa',
                border: '1px solid rgba(167,139,250,0.25)',
                letterSpacing: '0.04em',
                transition: 'all 0.15s',
              }}
              onMouseEnter={e => {
                e.currentTarget.style.background = 'rgba(167,139,250,0.2)'
                e.currentTarget.style.borderColor = 'rgba(167,139,250,0.5)'
              }}
              onMouseLeave={e => {
                e.currentTarget.style.background = 'rgba(167,139,250,0.10)'
                e.currentTarget.style.borderColor = 'rgba(167,139,250,0.25)'
              }}
            >
              ? Help
            </button>
          </div>
        </div>
      )}

      {/* Help modal */}
      {showHelp && <HelpModal onClose={() => setShowHelp(false)} />}

      {/* Loading / Error states */}
      {loading && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '60px 0' }}>
          <div className="w-7 h-7 rounded-full border-2 border-t-transparent animate-spin"
            style={{ borderColor: '#60a5fa', borderTopColor: 'transparent' }} />
        </div>
      )}
      {error && !loading && (
        <div style={{ padding: '24px', color: '#f87171', fontSize: '13px', textAlign: 'center' }}>
          {error}
          <button onClick={() => load(symbol)} style={{ marginLeft: 12, color: '#60a5fa',
            background: 'none', border: 'none', cursor: 'pointer', fontSize: '12px' }}>
            Retry
          </button>
        </div>
      )}

      {/* Main content */}
      {data && !loading && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: '20px', alignItems: 'start' }}>

          {/* ── TPO Chart ── */}
          <div style={{ padding: '16px', background: 'var(--bg-panel)',
            border: '1px solid var(--border)', borderRadius: '12px', overflowX: 'auto' }}>
            <div style={{ marginBottom: '12px', display: 'flex', alignItems: 'center',
              justifyContent: 'space-between' }}>
              <span style={{ fontSize: '10px', fontWeight: 700, color: 'var(--text-dim)',
                textTransform: 'uppercase', letterSpacing: '0.08em' }}>TPO Profile · 30-min Periods</span>
              {data.today.ib_range != null && (
                <span style={{ fontSize: '10px', color: '#fb923c' }}>
                  IB range: <strong>{fmt(data.today.ib_range)}</strong>
                </span>
              )}
            </div>
            <TpoChart
              today={data.today}
              prior={data.prior_rth}
              overnight={data.overnight}
              currentPrice={data.current_price}
              tick={data.tick}
            />
          </div>

          {/* ── Right panel: context cards ── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>

            {/* Opening type */}
            <InfoCard
              title="Opening Type"
              badge={data.opening.label}
              badgeColor={openCfg.color}
              badgeBg={openCfg.bg}
              badgeBorder={openCfg.border}
              description={data.opening.description}
            >
              {data.opening.vs_prior_vah != null && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)',
                  gap: '6px', marginTop: '2px' }}>
                  {[
                    { label: 'vs VAH', val: data.opening.vs_prior_vah },
                    { label: 'vs VAL', val: data.opening.vs_prior_val },
                    { label: 'vs POC', val: data.opening.vs_prior_poc },
                  ].map(({ label, val }) => val != null && (
                    <div key={label} style={{ textAlign: 'center', padding: '6px 4px',
                      background: 'rgba(255,255,255,0.03)', borderRadius: '6px',
                      border: '1px solid rgba(255,255,255,0.05)' }}>
                      <div style={{ fontSize: '8px', color: 'var(--text-dim)',
                        textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
                      <div style={{ fontSize: '12px', fontWeight: 700, fontFamily: 'monospace',
                        color: val >= 0 ? '#4ade80' : '#f87171' }}>
                        {val >= 0 ? '+' : ''}{fmt(val)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </InfoCard>

            {/* Day type */}
            <InfoCard
              title="Day Type"
              badge={data.day_type.label}
              badgeColor={dayCfg.color}
              badgeBg={dayCfg.bg}
              badgeBorder={dayCfg.bg.replace('0.12', '0.35').replace('0.15', '0.35').replace('0.08', '0.2')}
              description={data.day_type.description}
            >
              {data.day_type.ib_range != null && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '6px' }}>
                  {[
                    { label: 'IB Range', val: fmt(data.day_type.ib_range) },
                    { label: 'Ext ↑',   val: `+${fmt(data.day_type.ext_up)}` },
                    { label: 'Ext ↓',   val: `-${fmt(data.day_type.ext_down)}` },
                  ].map(({ label, val }) => (
                    <div key={label} style={{ textAlign: 'center', padding: '6px 4px',
                      background: 'rgba(255,255,255,0.03)', borderRadius: '6px',
                      border: '1px solid rgba(255,255,255,0.05)' }}>
                      <div style={{ fontSize: '8px', color: 'var(--text-dim)',
                        textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
                      <div style={{ fontSize: '12px', fontWeight: 600, fontFamily: 'monospace',
                        color: 'var(--text-primary)' }}>{val}</div>
                    </div>
                  ))}
                </div>
              )}
            </InfoCard>

            {/* 80% rule */}
            <InfoCard
              title="80% Rule"
              badge={data.rule_80.triggered
                ? (data.rule_80.already_hit ? '✓ Target Reached' : `Active ${data.rule_80.direction === 'LONG' ? '↑' : '↓'}`)
                : 'Not Triggered'}
              badgeColor={rule80c.color}
              badgeBg={rule80c.bg}
              badgeBorder={rule80c.border}
              description={data.rule_80.description}
            >
              {data.rule_80.triggered && data.rule_80.target != null && (
                <div style={{ padding: '8px 12px', background: rule80c.bg,
                  border: `1px solid ${rule80c.border}`, borderRadius: '7px', textAlign: 'center' }}>
                  <div style={{ fontSize: '9px', color: rule80c.color, fontWeight: 700,
                    textTransform: 'uppercase', letterSpacing: '0.06em' }}>Target</div>
                  <div style={{ fontSize: '18px', fontWeight: 700, color: rule80c.color,
                    fontFamily: 'monospace' }}>{fmt(data.rule_80.target)}</div>
                </div>
              )}
            </InfoCard>

            {/* IB & session stats */}
            <div style={{ padding: '14px 16px', background: 'var(--bg-panel)',
              border: '1px solid var(--border)', borderRadius: '10px' }}>
              <div style={{ fontSize: '10px', fontWeight: 700, color: 'var(--text-dim)',
                textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '10px' }}>
                Session Stats
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                {[
                  { label: 'IB High',         val: fmt(data.today.ib_high),       color: '#fb923c' },
                  { label: 'IB Low',          val: fmt(data.today.ib_low),        color: '#fb923c' },
                  { label: 'Developing POC',  val: fmt(data.today.poc),           color: '#a78bfa' },
                  { label: 'Developing VAH',  val: fmt(data.today.vah),           color: '#60a5fa' },
                  { label: 'Developing VAL',  val: fmt(data.today.val),           color: '#60a5fa' },
                  { label: 'Prior RTH Close', val: fmt(data.prior_rth.close),     color: '#94a3b8' },
                  { label: 'Prior RTH POC',   val: fmt(data.prior_rth.poc),       color: '#818cf8' },
                  { label: 'Overnight High',  val: fmt(data.overnight.high),      color: '#22d3ee' },
                  { label: 'Overnight Low',   val: fmt(data.overnight.low),       color: '#22d3ee' },
                  { label: 'Single Prints ↑', val: data.today.single_prints.filter(p => data.today.poc != null && p > data.today.poc).length + ' levels', color: '#f87171' },
                  { label: 'Single Prints ↓', val: data.today.single_prints.filter(p => data.today.poc != null && p < data.today.poc).length + ' levels', color: '#f87171' },
                ].map(({ label, val, color }) => (
                  <div key={label} style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'center' }}>
                    <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>{label}</span>
                    <span style={{ fontSize: '11px', fontWeight: 600, color, fontFamily: 'monospace' }}>
                      {val}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Single prints list */}
            {data.today.single_prints.length > 0 && (
              <div style={{ padding: '14px 16px', background: 'var(--bg-panel)',
                border: '1px solid rgba(248,113,113,0.2)', borderRadius: '10px' }}>
                <div style={{ fontSize: '10px', fontWeight: 700, color: '#f87171',
                  textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
                  ⚠ Single Prints (Poor Structure)
                </div>
                <p style={{ fontSize: '11px', color: 'var(--text-dim)', marginBottom: '8px', lineHeight: 1.4 }}>
                  Prices touched by only 1 period — fast move, likely to be revisited.
                </p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                  {data.today.single_prints.slice(0, 12).map(p => (
                    <span key={p} style={{ fontSize: '10px', fontFamily: 'monospace',
                      color: '#f87171', background: 'rgba(248,113,113,0.08)',
                      border: '1px solid rgba(248,113,113,0.2)',
                      borderRadius: '4px', padding: '2px 6px' }}>
                      {fmt(p)}
                    </span>
                  ))}
                  {data.today.single_prints.length > 12 && (
                    <span style={{ fontSize: '10px', color: 'var(--text-dim)' }}>
                      +{data.today.single_prints.length - 12} more
                    </span>
                  )}
                </div>
              </div>
            )}

            {/* Legend */}
            <div style={{ padding: '14px 16px', background: 'var(--bg-panel)',
              border: '1px solid var(--border)', borderRadius: '10px' }}>
              <div style={{ fontSize: '10px', fontWeight: 700, color: 'var(--text-dim)',
                textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '10px' }}>
                Chart Legend
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {[
                  { color: '#60a5fa', label: 'A – B',   note: 'Initial Balance (first hour)' },
                  { color: '#4ade80', label: 'C – F',   note: 'Morning session' },
                  { color: '#fbbf24', label: 'G – H',   note: 'Midday / lunch' },
                  { color: '#fb923c', label: 'I – J',   note: 'Afternoon' },
                  { color: '#f87171', label: 'K – M',   note: 'Late / closing' },
                  { color: '#a78bfa', label: '■ Purple', note: 'POC row' },
                  { color: '#4ade80', label: '■ Green',  note: 'Above POC — VAH zone' },
                  { color: '#f87171', label: '■ Red',    note: 'Below POC — VAL zone' },
                  { color: '#fb923c', label: 'IBH / IBL', note: 'Initial Balance extremes' },
                  { color: '#f87171', label: '1 letter',  note: 'Single print — poor structure' },
                ].map(({ color, label, note }) => (
                  <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ width: 8, height: 8, background: color, borderRadius: 2,
                      display: 'inline-block', flexShrink: 0 }} />
                    <span style={{ fontSize: '11px', fontWeight: 600, color,
                      minWidth: '52px', fontFamily: "'SF Mono', monospace" }}>{label}</span>
                    <span style={{ fontSize: '10px', color: 'var(--text-dim)' }}>{note}</span>
                  </div>
                ))}
              </div>
            </div>

          </div>
        </div>
      )}
    </div>
  )
}
