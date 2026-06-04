'use client'

import { useEffect, useState, useCallback } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

// ── Futures available in the Market Profile page ───────────────────────────────
const SYMBOLS = ['/ES', '/NQ', '/YM', '/RTY', '/GC', '/CL', '/SI', '/NG', '/HG', '/ZB', '/BTC']

// ── RTH TPO letter colours – warm progression through the day ────────────────
const LETTER_COLOR: Record<string, string> = {
  A: '#ffffff', B: '#ffffff',  // WHITE — Initial Balance (distinct key reference)
  C: '#22d3ee', D: '#22d3ee',  // cyan
  E: '#4ade80', F: '#4ade80',  // green
  G: '#fbbf24', H: '#fbbf24',  // amber — midday
  I: '#fb923c', J: '#fb923c',  // orange
  K: '#f87171', L: '#f87171', M: '#f87171',  // red — late session
}
const DEFAULT_LETTER_COLOR = '#94a3b8'

// ── Overnight TPO letter colours – blue (evening) → cyan (pre-market) ────────
const ON_LETTER_COLOR: Record<string, string> = (() => {
  const m: Record<string, string> = {}
  // a–d: 6 PM – 8 PM (deep blue)
  'abcd'.split('').forEach(c => { m[c] = '#3b82f6' })
  // e–h: 8 PM – 10 PM (blue)
  'efgh'.split('').forEach(c => { m[c] = '#6366f1' })
  // i–l: 10 PM – midnight (indigo)
  'ijkl'.split('').forEach(c => { m[c] = '#8b5cf6' })
  // m–p: midnight – 2 AM (purple-blue)
  'mnop'.split('').forEach(c => { m[c] = '#7c3aed' })
  // q–t: 2 AM – 4 AM (transition)
  'qrst'.split('').forEach(c => { m[c] = '#0ea5e9' })
  // u–z: 4 AM – 7 AM (cyan)
  'uvwxyz'.split('').forEach(c => { m[c] = '#22d3ee' })
  // 1–5: 7 AM – 9:30 AM (pre-market, bright cyan)
  '12345'.split('').forEach(c => { m[c] = '#67e8f9' })
  return m
})()

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
interface Overnight {
  high: number | null; low: number | null
  poc: number | null; vah: number | null; val: number | null
  profile:       ProfileRow[]
  single_prints: number[]
  periods:       number
  period_ranges: Record<string, PeriodRange>
  session_high:  number | null
  session_low:   number | null
}
interface Opening   { type: string; label: string; description: string; inside_prior_va: boolean | null; vs_prior_vah?: number; vs_prior_val?: number; vs_prior_poc?: number }
interface DayType   { type: string; label: string; description: string; ib_range?: number; ext_up?: number; ext_down?: number; ib_ratio?: number }
interface Rule80    { triggered: boolean; direction?: string; target?: number; already_hit?: boolean; label?: string; description: string }
interface IBSignalItem { type: string; signal: string; detail: string }
interface IBKeyLevel   { level: number; label: string; role: string; color: string }
interface IBSignals {
  ready:         boolean
  bias?:         string
  bias_label?:   string
  signals?:      IBSignalItem[]
  key_levels?:   IBKeyLevel[]
  day_context?:  string
  trade_plan?:   string
  description?:  string
}
interface LiveWatchLevel { price: number; label: string; significance: string }
interface LiveReadData {
  active:           boolean
  status:           string   // BUILDING | IB_BUILDING | INTACT | WEAKENING | CRITICAL | CONFIRMED | INVALIDATED
  last_period:      string | null
  last_close:       number | null
  current_read:     string
  live_guidance:    string
  watch_level:      LiveWatchLevel | null
  first_warning?:   boolean  // true when status held pending 2nd-period confirmation
  ib_score?:        number
  live_adjustment?: number
  current_score?:   number
  current_bias?:    string
  current_label?:   string
  live_trade_plan?: string
}
interface PreMarketKeyLevel {
  level: number
  label: string
  role:  string  // 'prior_value' | 'pivot' | 'overnight'
}
interface PreMarketReadData {
  active:        boolean
  gap_type?:     string  // 'ABOVE_VALUE' | 'BELOW_VALUE' | 'INSIDE_VALUE'
  gap_bias?:     string  // 'BULLISH' | 'BEARISH' | 'NEUTRAL'
  gap_label?:    string
  gap_pts?:      number
  inv_pos?:      string  // 'UPPER_THIRD' | 'MIDDLE' | 'LOWER_THIRD'
  inv_label?:    string
  inv_bias?:     string
  position_pct?: number
  on_range?:     number
  expected_open?: string
  open_guidance?: string
  mins_to_open?:  number
  key_levels?:    PreMarketKeyLevel[]
  prior_vah?:     number
  prior_val?:     number
  prior_poc?:     number
  on_high?:       number
  on_low?:        number
  on_poc?:        number
}
interface MPData {
  symbol:           string
  tick:             number
  computed_at:      string
  current_price:    number | null
  today:            SessionProfile
  prior_rth:        SessionProfile
  prior_overnight:  Overnight
  overnight:        Overnight
  opening:          Opening
  day_type:         DayType
  rule_80:          Rule80
  ib_signals:       IBSignals
  prior_ib_signals: IBSignals
  premarket_read?:  PreMarketReadData
  live_read:        LiveReadData
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
function TpoChart({ today, prior, priorOvernight, overnight, currentPrice, tick }: {
  today:         SessionProfile
  prior:         SessionProfile
  priorOvernight:Overnight
  overnight:     Overnight
  currentPrice:  number | null
  tick:          number
}) {
  const hasPriorOn = (priorOvernight.profile?.length ?? 0) > 0
  const hasOn      = (overnight.profile?.length ?? 0) > 0

  if (!today.profile.length && !prior.profile.length && !hasOn && !hasPriorOn) {
    return (
      <div style={{ padding: '48px 0', textAlign: 'center', color: 'var(--text-dim)', fontSize: '13px' }}>
        No profile data — market may be closed or pre-market
      </div>
    )
  }

  // Merge price universe from all four profiles
  const allPrices = new Set<number>()
  today.profile.forEach(r => allPrices.add(r.price))
  prior.profile.forEach(r => allPrices.add(r.price))
  overnight.profile?.forEach(r => allPrices.add(r.price))
  priorOvernight.profile?.forEach(r => allPrices.add(r.price))
  const sortedPrices = Array.from(allPrices).sort((a, b) => b - a)

  // Build lookup maps
  const todayMap    = new Map(today.profile.map(r => [r.price, r]))
  const priorMap    = new Map(prior.profile.map(r => [r.price, r]))
  const onMap       = new Map((overnight.profile ?? []).map(r => [r.price, r]))
  const priorOnMap  = new Map((priorOvernight.profile ?? []).map(r => [r.price, r]))

  // Max letter counts — for proportional bar widths
  const maxTodayCount   = Math.max(1, ...today.profile.map(r => r.count))
  const maxPriorCount   = Math.max(1, ...prior.profile.map(r => r.count))
  const maxOnCount      = Math.max(1, ...(overnight.profile?.map(r => r.count) ?? [1]))
  const maxPriorOnCount = Math.max(1, ...(priorOvernight.profile?.map(r => r.count) ?? [1]))

  // Single print sets
  const todaySP   = new Set(today.single_prints)
  const priorSP   = new Set(prior.single_prints)
  const onSP      = new Set(overnight.single_prints ?? [])
  const priorOnSP = new Set(priorOvernight.single_prints ?? [])

  const FONT       = "'SF Mono', ui-monospace, monospace"
  const ROW_H      = 16
  const PRICE_W    = 56
  const SEP        = 7
  const PRIOR_ON_W = hasPriorOn ? 95 : 0
  const PRIOR_W    = 108
  const ON_W       = hasOn ? 130 : 0
  const TODAY_W    = 160
  const TOTAL_W    = PRICE_W + SEP
    + (hasPriorOn ? PRIOR_ON_W + SEP : 0)
    + PRIOR_W + SEP
    + (hasOn ? ON_W + SEP : 0)
    + TODAY_W
  const totalH = sortedPrices.length * ROW_H + 4

  // X offsets for each panel (left-to-right time order)
  const xPriorOn = PRICE_W + SEP
  const xPrior   = xPriorOn + (hasPriorOn ? PRIOR_ON_W + SEP : 0)
  const xOn      = xPrior + PRIOR_W + SEP
  const xToday   = xOn + (hasOn ? ON_W + SEP : 0)

  const near = (p: number, ref: number | null | undefined) =>
    ref != null && Math.abs(p - ref) < tick * 0.6

  return (
    <div style={{ overflowY: 'auto', overflowX: 'auto' }}>
      {/* Column headers */}
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: '6px',
        paddingLeft: `${PRICE_W + SEP}px`, gap: `${SEP}px` }}>
        {hasPriorOn && (
          <div style={{ width: PRIOR_ON_W, textAlign: 'center', fontSize: '9px', fontWeight: 700,
            color: '#6366f1', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
            Prior ON · {priorOvernight.periods}p
          </div>
        )}
        <div style={{ width: PRIOR_W, textAlign: 'center', fontSize: '9px', fontWeight: 700,
          color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
          Prior RTH{prior.date ? ` · ${prior.date}` : ''}
        </div>
        {hasOn && (
          <div style={{ width: ON_W, textAlign: 'center', fontSize: '9px', fontWeight: 700,
            color: '#22d3ee', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
            Overnight · {overnight.periods}p
          </div>
        )}
        <div style={{ width: TODAY_W, textAlign: 'center', fontSize: '9px', fontWeight: 700,
          color: '#60a5fa', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
          Today — {today.periods}p ({today.periods < 13 ? 'developing' : 'complete'})
        </div>
      </div>

      <svg
        width={TOTAL_W}
        height={totalH}
        style={{ display: 'block', fontFamily: FONT, overflow: 'visible' }}
      >
        {/* ── Prior Overnight value area shading (3-zone: purple VAH / indigo POC / blue VAL) ── */}
        {hasPriorOn && priorOvernight.vah != null && priorOvernight.val != null && priorOvernight.poc != null && (() => {
          const idxVAH = sortedPrices.findIndex(p => p <= priorOvernight.vah!)
          const idxPOC = sortedPrices.findIndex(p => p <= priorOvernight.poc!)
          const idxVAL = sortedPrices.findIndex(p => p <= priorOvernight.val!)
          if (idxVAH < 0 || idxPOC < 0 || idxVAL < 0) return null
          const yVAH = idxVAH * ROW_H
          const yPOC = idxPOC * ROW_H
          const yVAL = idxVAL * ROW_H + ROW_H
          return (
            <g>
              {/* VAH → POC: purple (above-POC zone) */}
              <rect x={xPriorOn} y={yVAH} width={PRIOR_ON_W} height={yPOC - yVAH}
                fill="#a78bfa" fillOpacity={0.09} />
              {/* POC row: indigo highlight */}
              <rect x={xPriorOn} y={yPOC} width={PRIOR_ON_W} height={ROW_H}
                fill="#6366f1" fillOpacity={0.22} />
              {/* POC → VAL: blue (below-POC zone) */}
              <rect x={xPriorOn} y={yPOC + ROW_H} width={PRIOR_ON_W} height={yVAL - yPOC - ROW_H}
                fill="#3b82f6" fillOpacity={0.08} />
            </g>
          )
        })()}

        {/* ── Prior RTH value area shading ── */}
        {prior.vah != null && prior.val != null && prior.poc != null && (() => {
          const idxVAH = sortedPrices.findIndex(p => p <= prior.vah!)
          const idxPOC = sortedPrices.findIndex(p => p <= prior.poc!)
          const idxVAL = sortedPrices.findIndex(p => p <= prior.val!)
          if (idxVAH < 0 || idxPOC < 0 || idxVAL < 0) return null
          const yVAH = idxVAH * ROW_H
          const yPOC = idxPOC * ROW_H
          const yVAL = idxVAL * ROW_H + ROW_H
          return (
            <g>
              <rect x={xPrior} y={yVAH} width={PRIOR_W} height={yPOC - yVAH}
                fill="#4ade80" fillOpacity={0.07} />
              <rect x={xPrior} y={yPOC} width={PRIOR_W} height={ROW_H}
                fill="#a78bfa" fillOpacity={0.18} />
              <rect x={xPrior} y={yPOC + ROW_H} width={PRIOR_W} height={yVAL - yPOC - ROW_H}
                fill="#f87171" fillOpacity={0.07} />
            </g>
          )
        })()}

        {/* ── Current Overnight value area shading (3-zone: teal VAH / cyan POC / sky VAL) ── */}
        {hasOn && overnight.vah != null && overnight.val != null && overnight.poc != null && (() => {
          const idxVAH = sortedPrices.findIndex(p => p <= overnight.vah!)
          const idxPOC = sortedPrices.findIndex(p => p <= overnight.poc!)
          const idxVAL = sortedPrices.findIndex(p => p <= overnight.val!)
          if (idxVAH < 0 || idxPOC < 0 || idxVAL < 0) return null
          const yVAH = idxVAH * ROW_H
          const yPOC = idxPOC * ROW_H
          const yVAL = idxVAL * ROW_H + ROW_H
          return (
            <g>
              {/* VAH → POC: teal (above-POC zone) */}
              <rect x={xOn} y={yVAH} width={ON_W} height={yPOC - yVAH}
                fill="#14b8a6" fillOpacity={0.12} />
              {/* POC row: bright cyan highlight */}
              <rect x={xOn} y={yPOC} width={ON_W} height={ROW_H}
                fill="#22d3ee" fillOpacity={0.28} />
              {/* POC → VAL: sky blue (below-POC zone) */}
              <rect x={xOn} y={yPOC + ROW_H} width={ON_W} height={yVAL - yPOC - ROW_H}
                fill="#0ea5e9" fillOpacity={0.10} />
            </g>
          )
        })()}

        {/* ── Today RTH value area shading ── */}
        {today.vah != null && today.val != null && today.poc != null && (() => {
          const idxVAH = sortedPrices.findIndex(p => p <= today.vah!)
          const idxPOC = sortedPrices.findIndex(p => p <= today.poc!)
          const idxVAL = sortedPrices.findIndex(p => p <= today.val!)
          if (idxVAH < 0 || idxPOC < 0 || idxVAL < 0) return null
          const yVAH = idxVAH * ROW_H
          const yPOC = idxPOC * ROW_H
          const yVAL = idxVAL * ROW_H + ROW_H
          return (
            <g>
              <rect x={xToday} y={yVAH} width={TODAY_W} height={yPOC - yVAH}
                fill="#4ade80" fillOpacity={0.09} />
              <rect x={xToday} y={yPOC} width={TODAY_W} height={ROW_H}
                fill="#a78bfa" fillOpacity={0.22} />
              <rect x={xToday} y={yPOC + ROW_H} width={TODAY_W} height={yVAL - yPOC - ROW_H}
                fill="#f87171" fillOpacity={0.09} />
            </g>
          )
        })()}

        {/* ── Price rows ── */}
        {sortedPrices.map((price, i) => {
          const y          = i * ROW_H
          const todayRow   = todayMap.get(price)
          const priorRow   = priorMap.get(price)
          const onRow      = onMap.get(price)
          const priorOnRow = priorOnMap.get(price)

          const isCurrent      = currentPrice != null && Math.abs(price - currentPrice) < tick * 0.6
          const isTodayPOC     = near(price, today.poc)
          const isPriorPOC     = near(price, prior.poc)
          const isOnPOC        = near(price, overnight.poc)
          const isPriorOnPOC   = near(price, priorOvernight.poc)
          const isTodayVAH     = near(price, today.vah)
          const isTodayVAL     = near(price, today.val)
          const isPriorVAH     = near(price, prior.vah)
          const isPriorVAL     = near(price, prior.val)
          const isTodayIBH     = near(price, today.ib_high)
          const isTodayIBL     = near(price, today.ib_low)
          const isSPToday      = todaySP.has(price)
          const isSPPrior      = priorSP.has(price)
          const isSPOn         = onSP.has(price)
          const isSPPriorOn    = priorOnSP.has(price)

          const priceColor = isCurrent   ? '#fbbf24'
                           : isTodayPOC  ? '#a78bfa'
                           : isTodayVAH || isTodayVAL ? '#60a5fa'
                           : isPriorPOC  ? '#818cf8'
                           : 'var(--text-dim)'

          const priorBarW   = priorRow   ? Math.max(4, Math.round((priorRow.count   / maxPriorCount)   * PRIOR_W    * 0.9)) : 0
          const onBarW      = onRow      ? Math.max(4, Math.round((onRow.count      / maxOnCount)      * ON_W       * 0.9)) : 0
          const priorOnBarW = priorOnRow ? Math.max(4, Math.round((priorOnRow.count / maxPriorOnCount) * PRIOR_ON_W * 0.9)) : 0

          return (
            <g key={price}>
              {/* Row highlight for current price */}
              {isCurrent && (
                <rect x={0} y={y} width={TOTAL_W} height={ROW_H}
                  fill="#fbbf24" fillOpacity={0.06} />
              )}

              {/* Price axis */}
              <text x={PRICE_W - 4} y={y + ROW_H - 4}
                fill={priceColor} fontSize={9}
                fontWeight={isCurrent || isTodayPOC ? '700' : '400'}
                textAnchor="end" opacity={0.85}>
                {price.toFixed(2)}
              </text>

              {/* Prior Overnight letters — muted indigo */}
              {hasPriorOn && priorOnRow && (
                <g>
                  <rect x={xPriorOn} y={y + 2} width={priorOnBarW} height={ROW_H - 4}
                    fill="rgba(99,102,241,0.08)" rx={1} />
                  {priorOnRow.letters.split('').map((ltr, li) => (
                    <text key={li}
                      x={xPriorOn + 2 + li * 5.5}
                      y={y + ROW_H - 4}
                      fontSize={7} fontWeight={isPriorOnPOC ? '700' : '400'}
                      fill={ON_LETTER_COLOR[ltr] ?? '#6366f1'}
                      opacity={isSPPriorOn ? 0.35 : 0.55}>
                      {ltr}
                    </text>
                  ))}
                </g>
              )}
              {hasPriorOn && isPriorOnPOC && (
                <text x={xPriorOn + PRIOR_ON_W - 2} y={y + ROW_H - 4}
                  fontSize={7.5} fontWeight="700" textAnchor="end"
                  fill="#6366f1" opacity={0.6}>◆</text>
              )}

              {/* Prior RTH letters — per-letter so A & B render white */}
              {priorRow && (
                <g>
                  {(() => {
                    // IB single print (row is solely A or B) — keep IB styling, skip orange
                    const isIBSP = priorRow.letters === 'A' || priorRow.letters === 'B'
                    return (
                      <>
                        <rect x={xPrior} y={y + 2} width={priorBarW} height={ROW_H - 4}
                          fill={isSPPrior && !isIBSP ? 'rgba(248,113,113,0.15)' : 'rgba(129,140,248,0.12)'}
                          rx={1} />
                        {priorRow.letters.split('').map((ltr, li) => {
                          const isIB = ltr === 'A' || ltr === 'B'
                          const lc   = isIB         ? '#ffffff'
                                     : isPriorPOC   ? '#a78bfa'
                                     : isSPPrior    ? '#f87171'
                                     : '#475569'
                          return (
                            <text key={li}
                              x={xPrior + 3 + li * 6}
                              y={y + ROW_H - 4}
                              fontSize={8.5}
                              fontWeight={isIB || isPriorPOC ? '700' : '400'}
                              fill={lc}>
                              {ltr}
                            </text>
                          )
                        })}
                      </>
                    )
                  })()}
                </g>
              )}
              {(isPriorPOC || isPriorVAH || isPriorVAL) && (
                <text x={xPrior + PRIOR_W - 2} y={y + ROW_H - 4}
                  fontSize={7.5} fontWeight="700" textAnchor="end"
                  fill={isPriorPOC ? '#a78bfa' : '#818cf8'} opacity={0.65}>
                  {isPriorPOC ? '◆' : isPriorVAH ? '▲' : '▼'}
                </text>
              )}

              {/* Current Overnight letters */}
              {hasOn && onRow && (
                <g>
                  <rect x={xOn} y={y + 2} width={onBarW} height={ROW_H - 4}
                    fill="rgba(34,211,238,0.06)" rx={1} />
                  {onRow.letters.split('').map((ltr, li) => (
                    <text key={li}
                      x={xOn + 2 + li * 6}
                      y={y + ROW_H - 4}
                      fontSize={7.5} fontWeight={isOnPOC ? '700' : '400'}
                      fill={ON_LETTER_COLOR[ltr] ?? '#22d3ee'}
                      opacity={isSPOn ? 0.5 : 0.9}>
                      {ltr}
                    </text>
                  ))}
                </g>
              )}
              {hasOn && isOnPOC && (
                <text x={xOn + ON_W - 2} y={y + ROW_H - 4}
                  fontSize={7.5} fontWeight="700" textAnchor="end"
                  fill="#22d3ee" opacity={0.7}>◆</text>
              )}

              {/* Today RTH letters */}
              {todayRow && (
                <g>
                  {todayRow.letters.split('').map((ltr, li) => {
                    const isIBLtr = ltr === 'A' || ltr === 'B'
                    return (
                      <text key={li}
                        x={xToday + li * 7}
                        y={y + ROW_H - 4}
                        fontSize={9} fontWeight={isTodayPOC ? '700' : '500'}
                        fill={LETTER_COLOR[ltr] ?? DEFAULT_LETTER_COLOR}
                        opacity={isSPToday && !isIBLtr ? 0.6 : 1}>
                        {ltr}
                      </text>
                    )
                  })}
                </g>
              )}
              {(isTodayPOC || isTodayVAH || isTodayVAL || isTodayIBH || isTodayIBL || isCurrent) && (
                <text x={xToday + TODAY_W - 2} y={y + ROW_H - 4}
                  fontSize={7.5} fontWeight="700" textAnchor="end"
                  fill={isTodayPOC ? '#a78bfa' : isCurrent ? '#fbbf24'
                      : isTodayIBH || isTodayIBL ? '#fb923c' : '#60a5fa'}
                  opacity={0.8}>
                  {isTodayPOC ? '◆POC' : isCurrent ? '▶' : isTodayVAH ? '▲VAH' : isTodayVAL ? '▼VAL'
                    : isTodayIBH ? 'IBH' : isTodayIBL ? 'IBL' : ''}
                </text>
              )}
            </g>
          )
        })}

        {/* Current price horizontal dashed line */}
        {currentPrice != null && sortedPrices.length > 0 && (() => {
          const idx = sortedPrices.findIndex(p => p <= currentPrice)
          if (idx < 0) return null
          const cy = idx * ROW_H + ROW_H / 2
          return (
            <line x1={PRICE_W} y1={cy} x2={TOTAL_W} y2={cy}
              stroke="#fbbf24" strokeWidth={0.8} strokeDasharray="3,3" strokeOpacity={0.5} />
          )
        })()}
      </svg>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────
// ── TPO letter → time mappings ───────────────────────────────────────────────
const RTH_LETTERS = [
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

const ON_LETTERS_MAP = [
  { letter: 'a', time: '6:00 – 6:30 PM',   note: 'Evening open' },
  { letter: 'b', time: '6:30 – 7:00 PM',   note: '' },
  { letter: 'c', time: '7:00 – 7:30 PM',   note: '' },
  { letter: 'd', time: '7:30 – 8:00 PM',   note: '' },
  { letter: 'e', time: '8:00 – 8:30 PM',   note: '' },
  { letter: 'f', time: '8:30 – 9:00 PM',   note: '' },
  { letter: 'g', time: '9:00 – 9:30 PM',   note: '' },
  { letter: 'h', time: '9:30 – 10:00 PM',  note: '' },
  { letter: 'i', time: '10:00 – 10:30 PM', note: '' },
  { letter: 'j', time: '10:30 – 11:00 PM', note: '' },
  { letter: 'k', time: '11:00 – 11:30 PM', note: '' },
  { letter: 'l', time: '11:30 PM – 12:00', note: '' },
  { letter: 'm', time: '12:00 – 12:30 AM', note: 'Midnight' },
  { letter: 'n', time: '12:30 – 1:00 AM',  note: '' },
  { letter: 'o', time: '1:00 – 1:30 AM',   note: '' },
  { letter: 'p', time: '1:30 – 2:00 AM',   note: '' },
  { letter: 'q', time: '2:00 – 2:30 AM',   note: '' },
  { letter: 'r', time: '2:30 – 3:00 AM',   note: '' },
  { letter: 's', time: '3:00 – 3:30 AM',   note: '' },
  { letter: 't', time: '3:30 – 4:00 AM',   note: '' },
  { letter: 'u', time: '4:00 – 4:30 AM',   note: '' },
  { letter: 'v', time: '4:30 – 5:00 AM',   note: '' },
  { letter: 'w', time: '5:00 – 5:30 AM',   note: '' },
  { letter: 'x', time: '5:30 – 6:00 AM',   note: '' },
  { letter: 'y', time: '6:00 – 6:30 AM',   note: '' },
  { letter: 'z', time: '6:30 – 7:00 AM',   note: '' },
  { letter: '1', time: '7:00 – 7:30 AM',   note: 'Pre-market' },
  { letter: '2', time: '7:30 – 8:00 AM',   note: '' },
  { letter: '3', time: '8:00 – 8:30 AM',   note: '' },
  { letter: '4', time: '8:30 – 9:00 AM',   note: '' },
  { letter: '5', time: '9:00 – 9:30 AM',   note: 'Last pre-market' },
]

function HelpModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<'rth' | 'on'>('rth')
  return (
    <>
      <div onClick={onClose}
        style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 1090 }} />
      <div style={{
        position: 'fixed', top: '50%', left: '50%',
        transform: 'translate(-50%, -50%)',
        width: '400px', maxHeight: '80vh', zIndex: 1100,
        background: '#13111d',
        border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: '14px',
        boxShadow: '0 24px 64px rgba(0,0,0,0.7)',
        display: 'flex', flexDirection: 'column',
        overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{ padding: '16px 20px 12px',
          borderBottom: '1px solid rgba(255,255,255,0.07)',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          flexShrink: 0 }}>
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

        {/* Tabs */}
        <div style={{ display: 'flex', gap: '0', borderBottom: '1px solid rgba(255,255,255,0.07)', flexShrink: 0 }}>
          {([['rth', 'RTH  A – M', '#60a5fa'], ['on', 'Overnight  a–z, 1–5', '#22d3ee']] as const).map(([t, label, color]) => (
            <button key={t} onClick={() => setTab(t)}
              style={{
                flex: 1, padding: '9px 0', fontSize: '11px', fontWeight: 700,
                background: tab === t ? `${color}12` : 'transparent',
                color: tab === t ? color : 'var(--text-dim)',
                border: 'none', borderBottom: tab === t ? `2px solid ${color}` : '2px solid transparent',
                cursor: 'pointer', transition: 'all 0.12s',
              }}>
              {label}
            </button>
          ))}
        </div>

        {/* Letter table — scrollable */}
        <div style={{ padding: '12px 20px 20px', overflowY: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '28px 1fr 1fr', gap: '0',
            marginBottom: '6px' }}>
            {['', 'Time (ET)', 'Note'].map(h => (
              <div key={h} style={{ fontSize: '9px', fontWeight: 700, color: 'var(--text-dim)',
                textTransform: 'uppercase', letterSpacing: '0.07em', paddingBottom: '6px',
                borderBottom: '1px solid rgba(255,255,255,0.07)' }}>{h}</div>
            ))}
          </div>

          {tab === 'rth' && RTH_LETTERS.map(({ letter, time, note }) => {
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
                <div style={{ fontSize: '12px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>{time}</div>
                <div style={{ fontSize: '10px', color: isIB ? '#60a5fa' : 'var(--text-dim)',
                  fontWeight: isIB ? 600 : 400 }}>{note}</div>
              </div>
            )
          })}

          {tab === 'on' && ON_LETTERS_MAP.map(({ letter, time, note }) => {
            const color = ON_LETTER_COLOR[letter] ?? '#22d3ee'
            const isNum = !isNaN(Number(letter))
            const isMid = letter === 'm'
            return (
              <div key={letter} style={{
                display: 'grid', gridTemplateColumns: '28px 1fr 1fr',
                alignItems: 'center', padding: '4px 0',
                borderBottom: '1px solid rgba(255,255,255,0.04)',
                background: isNum ? 'rgba(34,211,238,0.04)' : isMid ? 'rgba(124,58,237,0.04)' : 'transparent',
              }}>
                <div style={{
                  fontFamily: "'SF Mono', monospace", fontSize: '13px', fontWeight: 700,
                  color, width: '22px', height: '22px', borderRadius: '4px',
                  background: `${color}18`, display: 'flex', alignItems: 'center',
                  justifyContent: 'center',
                }}>{letter}</div>
                <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontFamily: 'monospace' }}>{time}</div>
                <div style={{ fontSize: '10px',
                  color: isNum ? '#67e8f9' : isMid ? '#8b5cf6' : 'var(--text-dim)',
                  fontWeight: isNum || isMid ? 600 : 400 }}>{note}</div>
              </div>
            )
          })}

          {/* Footer note */}
          <div style={{ marginTop: '12px', padding: '10px 12px',
            background: tab === 'rth' ? 'rgba(167,139,250,0.08)' : 'rgba(34,211,238,0.07)',
            borderRadius: '8px',
            border: `1px solid ${tab === 'rth' ? 'rgba(167,139,250,0.18)' : 'rgba(34,211,238,0.15)'}` }}>
            <div style={{ fontSize: '10px',
              color: tab === 'rth' ? '#a78bfa' : '#22d3ee',
              fontWeight: 700, marginBottom: '4px' }}>
              {tab === 'rth' ? 'How to read the RTH profile' : 'How to read the overnight profile'}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--text-dim)', lineHeight: 1.5 }}>
              {tab === 'rth'
                ? <>The wider a price row (more letters), the more time was spent there — accepted value.
                  A single-letter row is a <strong style={{ color: '#f87171' }}>single print</strong>: price
                  passed through fast, likely revisited. Widest row = <strong style={{ color: '#a78bfa' }}>POC</strong>.</>
                : <>Lowercase letters mark overnight activity. <strong style={{ color: '#22d3ee' }}>a–l</strong> = evening
                  (6 PM–midnight), <strong style={{ color: '#8b5cf6' }}>m–z</strong> = early morning,
                  <strong style={{ color: '#67e8f9' }}> 1–5</strong> = pre-market (7–9:30 AM).
                  Overnight value area sets tomorrow's context.</>
              }
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

// ── IB Signal bias colours ────────────────────────────────────────────────────
const BIAS_CFG: Record<string, { bg: string; color: string; border: string; icon: string }> = {
  BULLISH:      { bg: 'rgba(74,222,128,0.12)',  color: '#4ade80', border: 'rgba(74,222,128,0.3)',  icon: '▲' },
  BULLISH_LEAN: { bg: 'rgba(74,222,128,0.07)',  color: '#86efac', border: 'rgba(74,222,128,0.2)',  icon: '↑' },
  BEARISH:      { bg: 'rgba(248,113,113,0.12)', color: '#f87171', border: 'rgba(248,113,113,0.3)', icon: '▼' },
  BEARISH_LEAN: { bg: 'rgba(248,113,113,0.07)', color: '#fca5a5', border: 'rgba(248,113,113,0.2)', icon: '↓' },
  NEUTRAL:      { bg: 'rgba(251,191,36,0.10)',  color: '#fbbf24', border: 'rgba(251,191,36,0.25)', icon: '↔' },
  // Bearish IB context suppressed — LONG-only regime active
  'No Trade — LONG regime': { bg: 'rgba(100,116,139,0.10)', color: '#94a3b8', border: 'rgba(100,116,139,0.25)', icon: '—' },
}
const SIGNAL_CFG: Record<string, { color: string; dot: string }> = {
  BULLISH:  { color: '#4ade80', dot: '#4ade80' },
  BEARISH:  { color: '#f87171', dot: '#f87171' },
  CAUTION:  { color: '#fbbf24', dot: '#fbbf24' },
  NEUTRAL:  { color: '#94a3b8', dot: '#94a3b8' },
  INFO:     { color: '#64748b', dot: '#475569' },   // grey — informational, no action
}

// ── Tooltip component ─────────────────────────────────────────────────────────
function Tip({ text, children }: { text: string; children: React.ReactNode }) {
  const [v, setV] = useState(false)
  return (
    <span style={{ position: 'relative', display: 'inline-flex', cursor: 'help' }}
      onMouseEnter={() => setV(true)} onMouseLeave={() => setV(false)}>
      {children}
      {v && (
        <div style={{
          position: 'absolute', bottom: 'calc(100% + 6px)', left: '50%',
          transform: 'translateX(-50%)',
          background: '#1e293b', border: '1px solid rgba(255,255,255,0.12)',
          borderRadius: '8px', padding: '7px 11px',
          fontSize: '11px', lineHeight: 1.55, color: '#cbd5e1',
          width: '220px', zIndex: 1000, pointerEvents: 'none',
          boxShadow: '0 8px 24px rgba(0,0,0,0.55)',
        }}>
          {text}
          <div style={{ position: 'absolute', top: '100%', left: '50%',
            transform: 'translateX(-50%)', width: 0, height: 0,
            borderLeft: '5px solid transparent', borderRight: '5px solid transparent',
            borderTop: '5px solid rgba(255,255,255,0.12)' }} />
        </div>
      )}
    </span>
  )
}

const TIPS: Record<string, string> = {
  // Level abbreviations
  'ONH':      'Overnight High — highest price during the overnight session (6 PM–9:30 AM ET). Acts as resistance on a gap-up open.',
  'ONL':      'Overnight Low — lowest price during the overnight session. Acts as support on a gap-down open.',
  'ON POC':   'Overnight Point of Control — price with the most time/activity overnight. Key pivot: bulls must hold above it, bears must break below it.',
  'ON VAH':   'Overnight Value Area High — upper boundary where 70% of overnight activity occurred.',
  'ON VAL':   'Overnight Value Area Low — lower boundary where 70% of overnight activity occurred.',
  'POC':      'Point of Control — price level with the most TPO touches today. Acts as a magnet and mean-reversion target.',
  'VAH':      'Value Area High — upper boundary of today\'s 70% value area (where price spent most of its time).',
  'VAL':      'Value Area Low — lower boundary of today\'s 70% value area.',
  'IB High':  'Initial Balance High — highest price during the first 60 min of RTH (9:30–10:30 AM). A close above = buyers extending.',
  'IB Low':   'Initial Balance Low — lowest price during the first 60 min of RTH. A close below = sellers extending.',
  'Prior VAH':'Prior session Value Area High — first upside extension target for a bullish day.',
  'Prior VAL':'Prior session Value Area Low — first downside extension target for a bearish day.',
  'Prior RTH POC': 'Prior session Point of Control — key pivot from yesterday. Price often revisits it.',
  // Opening types
  'OA':       'Open Auction — price opened inside prior day\'s value area. Overnight traders still in control. Two-sided auction expected: buy VAL, sell VAH until a break.',
  'OD ↑':     'Open Drive Up — opened above prior VAH and continued higher without reversing. Buyers in full control. Do not fade.',
  'OD ↓':     'Open Drive Down — opened below prior VAL and continued lower. Sellers in full control. Do not fade.',
  'OTD ↑':    'Open Test Drive Up — opened above prior VAH, briefly tested it, then drove higher. Directional bullish move.',
  'OTD ↓':    'Open Test Drive Down — opened below prior VAL, briefly tested it, then drove lower. Directional bearish move.',
  'ORR ↑':    'Open Rejection Reverse Up — opened below prior VAL but A period reversed back inside value. Gap down failed. Buy the reversal back toward VAH.',
  'ORR ↓':    'Open Rejection Reverse Down — opened above prior VAH but A period reversed back below it. Gap up failed. Sell the reversal back toward VAL.',
  // Zone status
  'CONFIRMED':   'Price extended beyond IB High (bullish) or IB Low (bearish). Sellers/buyers accelerating — trend day developing. Ride the move.',
  'INTACT':      'Signal holding. Bullish: closed above ONH. Bearish: closed below ONL. Excess defending as support/resistance.',
  'WEAKENING':   'Signal fading — price returned inside overnight range. Two-sided OA behaviour active. Reduce position 50%, no new entries.',
  'CRITICAL':    'Last defence — price at ON POC. One more close on the wrong side confirms signal failure.',
  'INVALIDATED': 'Signal broken — price closed through ON POC. Original thesis wrong. Reverse plan.',
  // Day types
  'Trend':          'Trend Day — one-timeframe control, price extended 2.5× the IB range. Do not fade, trail stops.',
  'Normal Var ↑':   'Normal Variation Up — buyers won the IB auction, one-sided extension above IB High.',
  'Normal Var ↓':   'Normal Variation Down — sellers won the IB auction, one-sided extension below IB Low.',
  'Normal':         'Normal Day — wide IB, both sides active, balanced range day.',
  'Neutral':        'Neutral Day — both sides tested IB extremes but neither won. No directional edge.',
  'Neutral Extreme':'Neutral Extreme — both sides extended significantly but balanced. High volatility, no trend.',
  // Other
  'IB':             'Initial Balance — the price range of the first 60 minutes (A+B periods, 9:30–10:30 AM). Sets the day\'s directional hypothesis.',
  'TPO':            'Time Price Opportunity — each 30-min period = one letter. TPO count = how many periods touched a price.',
  'Single Prints':  'Prices touched by only one period — fast move, unconfirmed structure. Often revisited as price returns to fill the gap.',
  'Value Area':     '70% of activity. VAH and VAL define where most trading occurred. Price gravitates back to value on balanced days.',
  'Prior RTH':      'Prior Regular Trading Hours session (yesterday 9:30 AM–4:00 PM). Value area and POC from yesterday act as reference levels.',
  'Overnight':      'Overnight session (6:00 PM–9:30 AM ET). Establishes the context for today\'s open and initial inventory positioning.',
  'Initial Balance':'First 60 minutes of RTH (A+B periods, 9:30–10:30 AM). The most important reference range — everything after tests or extends it.',
  'Developing':     'Today\'s developing profile — POC, VAH, and VAL update as the session progresses.',
}

// ── Pre-Market Read ───────────────────────────────────────────────────────────
const GAP_CFG: Record<string, { bg: string; color: string; border: string; icon: string }> = {
  BULLISH: { bg: 'rgba(74,222,128,0.12)',  color: '#4ade80', border: 'rgba(74,222,128,0.3)',  icon: '▲' },
  BEARISH: { bg: 'rgba(248,113,113,0.12)', color: '#f87171', border: 'rgba(248,113,113,0.3)', icon: '▼' },
  NEUTRAL: { bg: 'rgba(251,191,36,0.10)',  color: '#fbbf24', border: 'rgba(251,191,36,0.25)', icon: '↔' },
}
const KL_COLOR: Record<string, string> = {
  prior_value: '#fb923c',
  pivot:       '#a78bfa',
  overnight:   '#60a5fa',
}

function PreMarketRead({ pm }: { pm: PreMarketReadData }) {
  if (!pm.active) return null
  const gcfg = GAP_CFG[pm.gap_bias ?? 'NEUTRAL'] ?? GAP_CFG.NEUTRAL
  const icfg = GAP_CFG[pm.inv_bias ?? 'NEUTRAL'] ?? GAP_CFG.NEUTRAL
  const hrs  = pm.mins_to_open != null ? Math.floor(pm.mins_to_open / 60) : 0
  const mins = pm.mins_to_open != null ? pm.mins_to_open % 60 : 0
  const countdown = pm.mins_to_open != null
    ? (hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`)
    : '—'

  return (
    <div style={{ background: 'var(--bg-panel)', border: `1px solid ${gcfg.border}`,
      borderRadius: '10px', overflow: 'hidden' }}>

      {/* Header */}
      <div style={{ padding: '11px 16px', background: `${gcfg.color}0a`,
        borderBottom: `1px solid ${gcfg.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
        <span style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-dim)',
          textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Pre-Market
          {pm.mins_to_open != null && (
            <span style={{ marginLeft: '8px', color: '#64748b', fontWeight: 400 }}>
              Opens in {countdown}
            </span>
          )}
        </span>
        <span style={{ fontSize: '12px', fontWeight: 700, color: gcfg.color,
          background: gcfg.bg, border: `1px solid ${gcfg.border}`,
          borderRadius: '5px', padding: '2px 10px' }}>
          {gcfg.icon} {pm.gap_label}
        </span>
      </div>

      <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>

        {/* Overnight inventory position */}
        {pm.position_pct != null && pm.on_range != null && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              marginBottom: '5px' }}>
              <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>Overnight inventory</span>
              <span style={{ fontSize: '11px', fontWeight: 600, color: icfg.color }}>
                {pm.inv_label}
              </span>
            </div>
            {/* Progress bar — position within overnight range */}
            <div style={{ height: '5px', borderRadius: '3px', background: 'rgba(255,255,255,0.06)',
              position: 'relative', overflow: 'hidden' }}>
              <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0,
                width: `${pm.position_pct}%`, background: icfg.color,
                borderRadius: '3px', transition: 'width 0.4s ease' }} />
              {/* Zone markers at 33% and 67% */}
              {[33, 67].map(p => (
                <div key={p} style={{ position: 'absolute', top: 0, bottom: 0,
                  left: `${p}%`, width: '1px', background: 'rgba(255,255,255,0.15)' }} />
              ))}
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between',
              fontSize: '10px', color: '#475569', marginTop: '3px' }}>
              <span>{fmt(pm.on_low)}</span>
              <span>ON range {pm.on_range} pts</span>
              <span>{fmt(pm.on_high)}</span>
            </div>
          </div>
        )}

        {/* Expected opening type */}
        {pm.expected_open && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{ fontSize: '11px', color: 'var(--text-dim)', flexShrink: 0 }}>
              Expected open
            </span>
            <Tip text={TIPS[pm.expected_open] ?? pm.expected_open}>
              <span style={{ fontSize: '12px', fontWeight: 700, color: gcfg.color, cursor: 'help' }}>
                {pm.expected_open}
              </span>
            </Tip>
          </div>
        )}

        {/* Opening guidance */}
        {pm.open_guidance && (
          <div style={{ padding: '8px 12px', background: gcfg.bg,
            border: `1px solid ${gcfg.border}`, borderRadius: '7px' }}>
            <div style={{ fontSize: '10px', fontWeight: 700, color: gcfg.color,
              textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '4px' }}>
              Opening Guidance
            </div>
            <div style={{ fontSize: '12px', color: '#94a3b8', lineHeight: 1.55 }}>
              {pm.open_guidance}
            </div>
          </div>
        )}

        {/* Key levels */}
        {pm.key_levels && pm.key_levels.length > 0 && (
          <div>
            <div style={{ fontSize: '10px', fontWeight: 700, color: 'var(--text-dim)',
              textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '6px' }}>
              Key Levels
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              {pm.key_levels.map((kl, i) => {
                const klColor = KL_COLOR[kl.role] ?? '#64748b'
                const isCurrent = pm.on_poc != null && Math.abs(kl.level - (pm.on_poc ?? 0)) < 0.5
                return (
                  <div key={i} style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'center', padding: '3px 0',
                    borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                    <span style={{ fontSize: '12px', color: klColor }}>
                    {TIPS[kl.label] ? <Tip text={TIPS[kl.label]}>{kl.label}</Tip> : kl.label}
                  </span>
                    <span style={{ fontSize: '13px', fontWeight: 700, fontFamily: 'monospace',
                      color: isCurrent ? '#fbbf24' : 'var(--text-primary)' }}>
                      {fmt(kl.level)}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}

      </div>
    </div>
  )
}

function IBAnalysis({ signals, title }: { signals: IBSignals; title: string }) {
  const [open, setOpen] = useState(true)
  if (!signals.ready) {
    return (
      <div style={{ padding: '12px 16px', background: 'var(--bg-panel)',
        border: '1px solid var(--border)', borderRadius: '10px' }}>
        <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-dim)',
          textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>{title}</div>
        <p style={{ fontSize: '13px', color: 'var(--text-dim)' }}>{signals.description}</p>
      </div>
    )
  }

  const bias = signals.bias ?? 'NEUTRAL'
  const bcfg = BIAS_CFG[bias] ?? BIAS_CFG.NEUTRAL

  return (
    <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)',
      borderRadius: '10px', overflow: 'hidden' }}>
      {/* Header — click to collapse */}
      <button onClick={() => setOpen(o => !o)}
        style={{ width: '100%', padding: '12px 16px', background: 'none', border: 'none',
          cursor: 'pointer', display: 'flex', alignItems: 'center',
          justifyContent: 'space-between' }}>
        <span style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-dim)',
          textTransform: 'uppercase', letterSpacing: '0.08em' }}>{title}</span>
        <span style={{ fontSize: '13px', fontWeight: 700, color: bcfg.color,
          background: bcfg.bg, border: `1px solid ${bcfg.border}`,
          borderRadius: '5px', padding: '2px 10px' }}>
          {bcfg.icon} {signals.bias_label}
        </span>
      </button>

      {open && (
        <div style={{ padding: '0 16px 14px', display: 'flex', flexDirection: 'column', gap: '10px' }}>

          {/* Signal items */}
          {signals.signals?.map((s, i) => {
            const sc = SIGNAL_CFG[s.type] ?? SIGNAL_CFG.NEUTRAL
            return (
              <div key={i} style={{ paddingLeft: '10px',
                borderLeft: `2px solid ${sc.dot}` }}>
                <div style={{ fontSize: '13px', fontWeight: 700, color: sc.color,
                  marginBottom: '2px' }}>{s.signal}</div>
                <div style={{ fontSize: '12px', color: '#94a3b8', lineHeight: 1.5 }}>
                  {s.detail}
                </div>
              </div>
            )
          })}

          {/* Trade plan */}
          {signals.trade_plan && (
            <div style={{ padding: '8px 10px', background: `${bcfg.color}0d`,
              border: `1px solid ${bcfg.border}`, borderRadius: '7px' }}>
              <div style={{ fontSize: '10px', fontWeight: 700, color: bcfg.color,
                textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '4px' }}>
                Trade Plan as Per IB Ranges
              </div>
              <div style={{ fontSize: '13px', color: '#94a3b8', lineHeight: 1.5 }}>
                {signals.trade_plan}
              </div>
            </div>
          )}

          {/* Day context */}
          {signals.day_context && (
            <div style={{ fontSize: '12px', color: '#94a3b8', lineHeight: 1.4,
              fontStyle: 'italic' }}>
              {signals.day_context}
            </div>
          )}

          {/* Key levels */}
          {signals.key_levels && signals.key_levels.length > 0 && (
            <div>
              <div style={{ fontSize: '10px', fontWeight: 700, color: 'var(--text-dim)',
                textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '6px' }}>
                Key Levels
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                {signals.key_levels.map((kl, i) => {
                  const lc = kl.color === 'green'  ? '#4ade80'
                           : kl.color === 'red'    ? '#f87171'
                           : kl.color === 'cyan'   ? '#22d3ee'
                           : kl.color === 'purple' ? '#a78bfa'
                           : kl.color === 'amber'  ? '#fbbf24'
                           : '#94a3b8'
                  return (
                    <div key={i} style={{ display: 'flex', justifyContent: 'space-between',
                      alignItems: 'center', padding: '3px 0',
                      borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                      <span style={{ fontSize: '12px', color: lc, fontWeight: 600 }}>
                        {kl.label}
                      </span>
                      <span style={{ fontSize: '13px', fontWeight: 700, color: lc,
                        fontFamily: 'monospace' }}>
                        {kl.level.toFixed(2)}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Live Read status config ───────────────────────────────────────────────────
const LIVE_STATUS_CFG: Record<string, { color: string; bg: string; border: string; label: string }> = {
  BUILDING:    { color: '#94a3b8', bg: 'rgba(148,163,184,0.10)', border: 'rgba(148,163,184,0.25)', label: 'Pre-Market'  },
  IB_BUILDING: { color: '#60a5fa', bg: 'rgba(96,165,250,0.12)',  border: 'rgba(96,165,250,0.30)',  label: 'IB Building' },
  INTACT:      { color: '#4ade80', bg: 'rgba(74,222,128,0.12)',  border: 'rgba(74,222,128,0.30)',  label: 'Intact'      },
  CONFIRMED:   { color: '#34d399', bg: 'rgba(52,211,153,0.14)',  border: 'rgba(52,211,153,0.35)',  label: 'Confirmed'   },
  WEAKENING:   { color: '#fbbf24', bg: 'rgba(251,191,36,0.12)',  border: 'rgba(251,191,36,0.30)',  label: 'Weakening'   },
  CRITICAL:    { color: '#f97316', bg: 'rgba(249,115,22,0.12)',  border: 'rgba(249,115,22,0.30)',  label: 'Critical'    },
  INVALIDATED: { color: '#f87171', bg: 'rgba(248,113,113,0.12)', border: 'rgba(248,113,113,0.30)', label: 'Invalidated' },
}

function LiveRead({ lr }: { lr: LiveReadData }) {
  const cfg  = LIVE_STATUS_CFG[lr.status] ?? LIVE_STATUS_CFG['BUILDING']
  const bcfg = lr.current_bias ? (BIAS_CFG[lr.current_bias] ?? BIAS_CFG.NEUTRAL) : null
  return (
    <div style={{ background: 'var(--bg-panel)', border: `1px solid ${cfg.border}`,
      borderRadius: '10px', overflow: 'hidden' }}>

      {/* Header */}
      <div style={{ padding: '11px 16px', background: `${cfg.color}0a`,
        borderBottom: `1px solid ${cfg.border}`,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
        <span style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-dim)',
          textTransform: 'uppercase', letterSpacing: '0.08em' }}>Live Read</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          {/* Pending-confirmation chip — shown when badge is held pending 2nd period */}
          {lr.first_warning && (
            <span style={{ fontSize: '10px', fontWeight: 600, color: '#fbbf24',
              background: 'rgba(251,191,36,0.12)', border: '1px solid rgba(251,191,36,0.30)',
              borderRadius: '4px', padding: '2px 7px', letterSpacing: '0.04em' }}>
              Watching
            </span>
          )}
          {/* Zone status badge */}
          <Tip text={TIPS[lr.status] ?? cfg.label}>
            <span style={{ fontSize: '12px', fontWeight: 700, color: cfg.color,
              background: cfg.bg, border: `1px solid ${cfg.border}`,
              borderRadius: '5px', padding: '2px 10px' }}>
              {cfg.label}
            </span>
          </Tip>
        </div>
      </div>

      <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>

        {/* Score math row: IB + adj = current  [▼ Current Label] */}
        {lr.current_score != null && lr.ib_score != null && lr.live_adjustment != null && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px',
            fontSize: '11px', color: 'var(--text-dim)', fontFamily: 'monospace',
            flexWrap: 'wrap' }}>
            <span>IB {lr.ib_score > 0 ? '+' : ''}{lr.ib_score}</span>
            <span style={{ color: '#475569' }}>+</span>
            <span style={{ color: lr.live_adjustment >= 0 ? '#4ade80' : '#f87171' }}>
              adj {lr.live_adjustment > 0 ? '+' : ''}{lr.live_adjustment}
            </span>
            <span style={{ color: '#475569' }}>=</span>
            <span style={{ fontWeight: 700, color: bcfg?.color ?? 'var(--text-primary)' }}>
              {lr.current_score > 0 ? '+' : ''}{lr.current_score}
            </span>
            {bcfg && lr.current_label && (
              <span style={{ fontSize: '11px', fontWeight: 700, color: bcfg.color,
                background: bcfg.bg, border: `1px solid ${bcfg.border}`,
                borderRadius: '4px', padding: '1px 8px', fontFamily: 'sans-serif' }}>
                {bcfg.icon} {lr.current_label}
              </span>
            )}
          </div>
        )}

        {/* Last period chip */}
        {lr.last_period && lr.last_close != null && (
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '6px' }}>
            <span style={{
              fontFamily: 'monospace', fontSize: '14px', fontWeight: 700,
              color: LETTER_COLOR[lr.last_period] ?? cfg.color,
              background: `${LETTER_COLOR[lr.last_period] ?? cfg.color}18`,
              borderRadius: '4px', padding: '1px 6px',
            }}>{lr.last_period}</span>
            <span style={{ fontSize: '12px', color: 'var(--text-dim)' }}>closed at</span>
            <span style={{ fontSize: '14px', fontWeight: 700, fontFamily: 'monospace',
              color: 'var(--text-primary)' }}>{lr.last_close.toFixed(2)}</span>
          </div>
        )}

        {/* Narrative */}
        {lr.current_read && (
          <p style={{ fontSize: '12px', color: '#94a3b8', lineHeight: 1.6, margin: 0 }}>
            {lr.current_read}
          </p>
        )}

        {/* Guidance box */}
        {lr.live_guidance && (
          <div style={{ padding: '8px 12px', background: cfg.bg,
            border: `1px solid ${cfg.border}`, borderRadius: '7px' }}>
            <div style={{ fontSize: '10px', fontWeight: 700, color: cfg.color,
              textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '4px' }}>
              Guidance
            </div>
            <div style={{ fontSize: '12px', color: '#94a3b8', lineHeight: 1.55 }}>
              {lr.live_guidance}
            </div>
          </div>
        )}

        {/* Live trade plan — overrides IB plan when signal has evolved */}
        {lr.live_trade_plan && bcfg && (
          <div style={{ padding: '8px 12px', background: bcfg.bg,
            border: `1px solid ${bcfg.border}`, borderRadius: '7px' }}>
            <div style={{ fontSize: '10px', fontWeight: 700, color: bcfg.color,
              textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '4px' }}>
              Live Trade Plan
            </div>
            <div style={{ fontSize: '12px', color: '#94a3b8', lineHeight: 1.55 }}>
              {lr.live_trade_plan}
            </div>
          </div>
        )}

        {/* Watch level */}
        {lr.watch_level && (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            padding: '8px 12px', background: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(255,255,255,0.06)', borderRadius: '7px' }}>
            <div>
              <div style={{ fontSize: '10px', fontWeight: 700, color: '#64748b',
                textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '2px' }}>
                Watch
              </div>
              <div style={{ fontSize: '12px', fontWeight: 700, color: cfg.color }}>
                {lr.watch_level.label}
              </div>
              <div style={{ fontSize: '10px', color: '#64748b', marginTop: '2px', lineHeight: 1.4 }}>
                {lr.watch_level.significance}
              </div>
            </div>
            <div style={{ fontSize: '16px', fontWeight: 700, fontFamily: 'monospace',
              color: cfg.color, marginLeft: '12px' }}>
              {lr.watch_level.price.toFixed(2)}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function MarketProfile() {
  const [symbol,      setSymbol]      = useState('/ES')
  const [data,        setData]        = useState<MPData | null>(null)
  const [loading,     setLoading]     = useState(false)
  const [error,       setError]       = useState<string | null>(null)
  const [tokenBanner, setTokenBanner] = useState<{status:string; message:string} | null>(null)
  const [showHelp,    setShowHelp]    = useState(false)

  // Check Schwab token health on mount — show banner before the app breaks
  useEffect(() => {
    fetch(`${API_URL}/api/token-status`)
      .then(r => r.json())
      .then(d => { if (d.status !== 'ok') setTokenBanner(d) })
      .catch(() => {})
  }, [])

  const load = useCallback(async (sym: string) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(
        `${API_URL}/api/market-profile/${encodeURIComponent(sym)}`,
        { cache: 'no-store' }
      )
      if (!res.ok) {
        // Try to parse a structured error from the backend
        let msg = `HTTP ${res.status}`
        try {
          const body = await res.json()
          if (body.error === 'token_expired') {
            setTokenBanner({ status: 'expired', message: body.message })
            msg = body.message
          } else if (body.message) {
            msg = body.message
          }
        } catch {}
        throw new Error(msg)
      }
      setData(await res.json())
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(symbol) }, [symbol, load])

  // Auto-refresh every 60 s during the active trading window.
  // Dead zone is 4 PM – 6 PM ET (session close → overnight open).
  // All other hours: RTH (9:30 AM–4 PM) + overnight (6 PM–9:30 AM) are live.
  useEffect(() => {
    const id = setInterval(() => {
      const etH = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' })).getHours()
      const inDeadZone = etH >= 16 && etH < 18   // 4 PM – 6 PM ET
      if (!inDeadZone) load(symbol)
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
                {TIPS[label] ? <Tip text={TIPS[label]}>{label}</Tip> : label}
              </div>
              {/* Items in a row */}
              <div style={{ display: 'flex', gap: '14px' }}>
                {items.map(({ key, value, color: c }) => value != null && (
                  <div key={key}>
                    <div style={{ fontSize: '9px', color: 'var(--text-dim)', fontWeight: 600,
                      letterSpacing: '0.05em', marginBottom: '1px' }}>
                      {TIPS[key] ? <Tip text={TIPS[key]}>{key}</Tip> : key}
                    </div>
                    <div style={{ fontSize: '13px', fontWeight: 700, color: c,
                      fontFamily: "'SF Mono', monospace" }}>
                      {fmt(value)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}

          {/* Opening type + Day type badges */}
          <div style={{ padding: '10px 14px', display: 'flex', flexDirection: 'column',
            justifyContent: 'center', gap: '5px', borderRight: '1px solid var(--border)' }}>
            <div style={{ fontSize: '9px', fontWeight: 700, color: 'var(--text-dim)',
              textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '2px' }}>Session</div>
            <Tip text={TIPS[data.opening.label] ?? `${data.opening.label}: ${data.opening.description ?? ''}`}>
              <span style={{ fontSize: '11px', fontWeight: 700,
                color: openCfg.color, background: openCfg.bg,
                border: `1px solid ${openCfg.border}`,
                borderRadius: '4px', padding: '2px 8px', cursor: 'help' }}>
                {data.opening.label}
              </span>
            </Tip>
            <Tip text={TIPS[data.day_type.label] ?? data.day_type.label}>
              <span style={{ fontSize: '11px', fontWeight: 700,
                color: dayCfg.color, background: `${dayCfg.bg}`,
                border: `1px solid ${dayCfg.color}33`,
                borderRadius: '4px', padding: '2px 8px', cursor: 'help' }}>
                {data.day_type.label}
              </span>
            </Tip>
          </div>

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

      {/* Schwab token banner — shown when token is expiring or expired */}
      {tokenBanner && (() => {
        const isExpired  = tokenBanner.status === 'expired'
        const isCritical = tokenBanner.status === 'critical'
        const bg      = isExpired  ? 'rgba(248,113,113,0.12)' : 'rgba(251,191,36,0.10)'
        const border  = isExpired  ? 'rgba(248,113,113,0.35)' : 'rgba(251,191,36,0.30)'
        const color   = isExpired  ? '#f87171'                 : '#fbbf24'
        const icon    = isExpired  ? '🔴' : isCritical ? '🟠' : '🟡'
        return (
          <div style={{ margin: '0 0 12px', padding: '10px 16px',
            background: bg, border: `1px solid ${border}`, borderRadius: '8px',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
            <span style={{ fontSize: '12px', color, lineHeight: 1.5 }}>
              {icon} <strong>Schwab token {isExpired ? 'expired' : 'expiring soon'}:</strong>{' '}
              {tokenBanner.message}
            </span>
            <span style={{ fontSize: '11px', color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
              Run <code style={{ background: 'rgba(255,255,255,0.08)', padding: '1px 5px',
                borderRadius: '3px' }}>renew_schwab_token.py</code>
            </span>
          </div>
        )
      })()}

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
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 260px', gap: '16px', alignItems: 'start' }}>

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
              priorOvernight={data.prior_overnight}
              overnight={data.overnight}
              currentPrice={data.current_price}
              tick={data.tick}
            />
          </div>

          {/* ── Right panel: context cards ── */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>

            {data.premarket_read?.active === true ? (
              /* ── PRE-MARKET VIEW ── */
              <>
                {/* Pre-market gap + inventory + opening scenario */}
                {data.premarket_read?.active && (
                  <PreMarketRead pm={data.premarket_read} />
                )}
                {/* Prior session IB — always useful context before the open */}
                {data.prior_ib_signals?.ready && (
                  <IBAnalysis signals={data.prior_ib_signals} title="IB Analysis — Prior Session" />
                )}
              </>
            ) : (
              /* ── RTH VIEW ── */
              <>
                {/* Live Read — dynamic per-period market read */}
                {data.live_read && <LiveRead lr={data.live_read} />}

                {/* IB Signals — current session (after B period) */}
                {data.ib_signals && (
                  <IBAnalysis signals={data.ib_signals} title="IB Analysis — Today" />
                )}

                {/* IB Signals — prior session: only show before today's IB is ready */}
                {data.prior_ib_signals?.ready && !data.ib_signals?.ready && (
                  <IBAnalysis signals={data.prior_ib_signals} title="IB Analysis — Prior Session" />
                )}
              </>
            )}

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
                    <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>
                      {TIPS[label] ? <Tip text={TIPS[label]}>{label}</Tip> : label}
                    </span>
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
                  ⚠ <Tip text={TIPS['Single Prints']}>Single Prints</Tip> (Poor Structure)
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
                  { color: '#ffffff', label: 'A – B',   note: 'Initial Balance (first hour)' },
                  { color: '#4ade80', label: 'C – F',   note: 'Morning session' },
                  { color: '#fbbf24', label: 'G – H',   note: 'Midday / lunch' },
                  { color: '#fb923c', label: 'I – J',   note: 'Afternoon' },
                  { color: '#f87171', label: 'K – M',   note: 'Late / closing' },
                  { color: '#3b82f6', label: 'a – l',   note: 'Overnight evening (6 PM–12 AM)' },
                  { color: '#8b5cf6', label: 'm – z',   note: 'Overnight night (12 AM–7 AM)' },
                  { color: '#67e8f9', label: '1 – 5',   note: 'Pre-market (7–9:30 AM)' },
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
