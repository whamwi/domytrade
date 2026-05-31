'use client'

import { useEffect, useRef, useState } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export interface FuturesPanelInfo {
  symbol:    string
  last:      number
  change:    number
  changePct: number
}

interface Levels {
  // Volume profile
  prior_rth_vah:        number | null
  prior_rth_vpoc:       number | null
  prior_rth_val:        number | null
  overnight_vah:        number | null
  overnight_vpoc:       number | null
  overnight_val:        number | null
  developing_vah:       number | null
  developing_vpoc:      number | null
  developing_val:       number | null
  // Dalton TPO (Time Price Opportunity — original Market Profile)
  prior_rth_tpo_vah:    number | null
  prior_rth_tpo_vpoc:   number | null
  prior_rth_tpo_val:    number | null
  overnight_tpo_vah:    number | null
  overnight_tpo_vpoc:   number | null
  overnight_tpo_val:    number | null
  developing_tpo_vah:   number | null
  developing_tpo_vpoc:  number | null
  developing_tpo_val:   number | null
  // Other
  mcvpoc_3day:          number | null
  daily_pivot:          number | null
  prev_high:            number | null
  prev_low:             number | null
  prev_close:           number | null
  vwap:                 number | null
}

interface LevelsResponse {
  symbol:      string
  tick:        number
  computed_at: string
  gap:         number | null
  ib_high:     number | null
  ib_low:      number | null
  ib_complete: boolean
  ib_source:   'today' | 'prior' | null
  levels:      Levels
}

interface FuturesPanelProps {
  info:    FuturesPanelInfo
  onClose: () => void
}

const LEVEL_LABELS: Record<string, string> = {
  prev_high:            'Prior Day High',
  prev_close:           'Prior Day Close',
  // Dalton TPO — primary display (volume profile hidden, kept in backend for agent)
  prior_rth_tpo_vah:    'Prior RTH  —  VAH',
  prior_rth_tpo_vpoc:   'Prior RTH  —  POC',
  prior_rth_tpo_val:    'Prior RTH  —  VAL',
  overnight_tpo_vah:    'Overnight  —  VAH',
  overnight_tpo_vpoc:   'Overnight  —  POC',
  overnight_tpo_val:    'Overnight  —  VAL',
  developing_tpo_vah:   '__SESSION__  —  VAH',
  developing_tpo_vpoc:  '__SESSION__  —  POC',
  developing_tpo_val:   '__SESSION__  —  VAL',
  // Other
  mcvpoc_3day:          '3-Day Composite POC',
  vwap:                 'VWAP  —  Fair Value',
  ib_high:              'Initial Balance High',
  ib_low:               'Initial Balance Low',
  daily_pivot:          'Daily Pivot',
  prev_low:             'Prior Day Low',
}

const LEVEL_HELP: Record<string, string> = {
  // Dalton TPO — time-based, exact from 1-min bars (30-min periods)
  prior_rth_tpo_vah:   'Prior RTH Value Area High — upper boundary of the 70% TPO zone from yesterday\'s cash session (9:30–4:00 ET). Dalton\'s original method: each 30-min period marks every price it touched (1 TPO per tick). Price opening above VAH and accepting = bullish continuation. Failure to hold = rejection back into value.',
  prior_rth_tpo_vpoc:  'Prior RTH Point of Control — price touched by the most 30-min periods in yesterday\'s session. Dalton\'s time-based POC: exact from 1-min bars, no intrabar volume guessing. The strongest single-session magnet.',
  prior_rth_tpo_val:   'Prior RTH Value Area Low — lower boundary of the 70% TPO zone from yesterday\'s session. Price below VAL = market rejecting prior value. Reclaim of VAL triggers the rotation trade back toward POC.',
  overnight_tpo_vah:   'Overnight Value Area High — upper boundary of the 70% TPO zone from the overnight session (6 PM–9:30 AM ET). Shows where overnight participants accepted the upper edge of fair value.',
  overnight_tpo_vpoc:  'Overnight Point of Control — price where the overnight market spent the most 30-min periods. The overnight fulcrum. Gap between this and Prior RTH POC signals directional intent for the open.',
  overnight_tpo_val:   'Overnight Value Area Low — lower boundary of overnight TPO value. Opening inside (VAL–VAH) = rotational day likely. Opening outside = directional move expected.',
  developing_tpo_vah:  'Today\'s Value Area High — live upper boundary of today\'s TPO value area. Updates every 30 minutes as new periods complete. Shows where today\'s session is currently accepting the upper edge of value.',
  developing_tpo_vpoc: 'Today\'s Point of Control — today\'s current TPO POC, the price revisited by the most 30-min periods so far. The session\'s live fulcrum — watch for price to accept or reject it as the day builds.',
  developing_tpo_val:  'Today\'s Value Area Low — live lower boundary of today\'s TPO value. Holding above = buyers in control of value migration. Breaking below = value shifting lower.',
  // Other
  mcvpoc_3day:         'The single price with the most volume across the last 3 RTH sessions combined. A powerful multi-day magnet — harder to break than a single-session POC.',
  daily_pivot:         '(Yesterday\'s High + Low + Close) ÷ 3. A neutral reference: above it = bullish bias for the day, below = bearish.',
  prev_high:           'Yesterday\'s RTH session high (4:00 PM close included). Breaking above with volume confirms bullish continuation.',
  prev_close:          'Yesterday\'s RTH closing price (4:00 PM ET). The baseline for today\'s gap calculation — compare to today\'s RTH open to determine gap up or gap down.',
  prev_low:            'Yesterday\'s RTH session low. Breaking below with volume confirms bearish continuation.',
  vwap:                'Volume Weighted Average Price — average price paid weighted by volume. Price above VWAP = institutions were net buyers. Below = net sellers. The market constantly gravitates back to this level.',
  ib_high:             'Initial Balance High — highest price of the first 60 minutes of RTH (9:30–10:30 ET). A break above IB High after 10:30 signals a trend day extending upward.',
  ib_low:              'Initial Balance Low — lowest price of the first 60 minutes of RTH. A break below after 10:30 signals a trend day extending downward. Narrow IB = coiled market.',
}

const LEVEL_COLOR: Record<string, string> = {
  prev_high:            '#fbbf24',   // amber — prior day reference
  // Dalton TPO — primary display (original session palette)
  prior_rth_tpo_vah:    '#818cf8',   // indigo — prior RTH value area
  prior_rth_tpo_vpoc:   '#a78bfa',   // purple — prior RTH POC
  prior_rth_tpo_val:    '#818cf8',   // indigo
  overnight_tpo_vah:    '#22d3ee',   // cyan — overnight session
  overnight_tpo_vpoc:   '#06b6d4',   // cyan darker
  overnight_tpo_val:    '#22d3ee',   // cyan
  developing_tpo_vah:   '#34d399',   // emerald — live developing session
  developing_tpo_vpoc:  '#10b981',   // emerald darker
  developing_tpo_val:   '#34d399',   // emerald
  // Other
  mcvpoc_3day:          '#c084fc',   // soft purple — composite
  ib_high:              '#f97316',   // orange — initial balance
  ib_low:               '#f97316',   // orange
  daily_pivot:          '#60a5fa',   // blue — pivot
  prev_close:           '#94a3b8',   // slate — prior close
  prev_low:             '#4ade80',   // green — floors
  vwap:                 '#f59e0b',   // amber — fair value
}

const NVPOC_COLOR = '#22d3ee'  // cyan — naked/unfilled volume nodes

// ── Full readable label for chart view ───────────────────────────────────────
// Uses the same label as the list view; collapses multiple spaces for clarity.
function chartLabel(key: string, label: string): string {
  return label.replace(/\s{2,}/g, ' ').trim()
}

// ── SVG price ladder chart ────────────────────────────────────────────────────
function LevelsChart({
  levelRows, last, priceColor,
}: {
  levelRows: { price: number; key: string; label: string; dist: number }[]
  last: number
  priceColor: string
}) {
  if (!levelRows.length) return null

  // Layout: [full label RIGHT-ALIGNED] | gap | [===line===] | gap | [price LEFT-ALIGNED]
  // Longest label ~20 chars × 5px/char (monospace 8.5px) ≈ 100px → give 155px
  const W = 440, H = 460
  const LBL_END = 155   // labels right-align to this X
  const LX1 = 163       // line left edge
  const LX2 = 253       // line right edge (90px wide lines)
  const PRC_X = 261     // prices left-align from this X

  // Collect all prices (include current for scale)
  const allP = levelRows.map(r => r.price)
  if (last > 0) allP.push(last)
  const minP = Math.min(...allP)
  const maxP = Math.max(...allP)

  // ── Non-linear scale: outliers (top & bottom ~12%) get compressed zones ──
  // Core range = p12 → p88 of all prices, mapped to the middle of the chart.
  // Levels outside the core are squeezed into fixed top/bottom bands.
  const sortedP = [...allP].sort((a, b) => a - b)
  const n = sortedP.length
  const p12 = sortedP[Math.max(0, Math.floor(n * 0.12))]
  const p88 = sortedP[Math.min(n - 1, Math.floor(n * 0.88))]
  const corePad  = Math.max((p88 - p12) * 0.08, 3)
  const coreMin  = p12 - corePad
  const coreMax  = p88 + corePad
  const coreRange = coreMax - coreMin || 1

  const OZ = 22                       // px reserved at each edge for outlier zone (small = tight)
  const CORE_TOP = OZ                 // y where core starts (high prices)
  const CORE_BOT = H - OZ            // y where core ends (low prices)
  const CORE_H   = CORE_BOT - CORE_TOP

  const toY = (p: number): number => {
    if (p >= coreMin && p <= coreMax) {
      // Linear inside core
      return CORE_TOP + ((coreMax - p) / coreRange) * CORE_H
    }
    if (p > coreMax) {
      // Above core → compress into top OZ px (high price = small Y)
      const range = maxP - coreMax || 1
      const frac  = (p - coreMax) / range       // 0 at core edge, 1 at max
      return CORE_TOP - frac * (CORE_TOP - 6)   // from CORE_TOP down to y=6
    }
    // Below core → compress into bottom OZ px
    const range = coreMin - minP || 1
    const frac  = (coreMin - p) / range
    return CORE_BOT + frac * (H - 6 - CORE_BOT)
  }

  // Anti-collision: push labels down when stacked within MIN_GAP px
  const MIN_GAP = 13
  const rawY  = levelRows.map(r => toY(r.price))
  const order = levelRows.map((_, i) => i).sort((a, b) => rawY[a] - rawY[b])
  const adjY  = [...rawY]
  let prevY = -Infinity
  for (const i of order) {
    if (adjY[i] < prevY + MIN_GAP) adjY[i] = prevY + MIN_GAP
    prevY = adjY[i]
  }

  // Session value area shading (uses true price Y, not collision-adjusted)
  const getP = (k: string) => levelRows.find(r => r.key === k)?.price
  const sessions = [
    { prefix: 'prior_rth_tpo',  color: '#818cf8' },
    { prefix: 'overnight_tpo',  color: '#22d3ee' },
    { prefix: 'developing_tpo', color: '#34d399' },
  ]
  const isPOC = (k: string) =>
    k.endsWith('_vpoc') || k.endsWith('_poc') || k === 'mcvpoc_3day' || k.startsWith('nvpoc_')

  const mid = (LX1 + LX2) / 2

  return (
    <svg width={W} height={H} style={{ display: 'block', overflow: 'visible' }}>

      {/* Value area shading */}
      {sessions.map(({ prefix, color }) => {
        const vah = getP(`${prefix}_vah`)
        const val = getP(`${prefix}_val`)
        if (!vah || !val) return null
        const y1 = toY(vah), y2 = toY(val)
        return (
          <rect key={prefix}
            x={LX1} y={y1} width={LX2 - LX1} height={y2 - y1}
            fill={color} fillOpacity={0.09}
          />
        )
      })}

      {/* Level lines + labels + prices */}
      {levelRows.map((row, i) => {
        const ly    = toY(row.price)        // true price Y (for the line)
        const lblY  = adjY[i]              // collision-adjusted Y (for text)
        const color = row.key.startsWith('nvpoc_') ? NVPOC_COLOR : (LEVEL_COLOR[row.key] ?? '#666')
        const solid = isPOC(row.key)

        return (
          <g key={row.key}>
            {/* Horizontal level line at true price position */}
            <line
              x1={LX1} y1={ly} x2={LX2} y2={ly}
              stroke={color}
              strokeWidth={solid ? 1.5 : 1}
              strokeDasharray={solid ? undefined : '5,3'}
              strokeOpacity={0.85}
            />
            {/* Thin vertical leader on left edge when label is shifted */}
            {Math.abs(lblY - ly) > 2 && (
              <line
                x1={LX1} y1={ly} x2={LX1} y2={lblY}
                stroke={color} strokeWidth={0.5} strokeOpacity={0.3}
              />
            )}
            {/* Label — right-aligned on LEFT side of line */}
            <text
              x={LBL_END} y={lblY + 4}
              fill={color} fontSize={10.5}
              fontFamily="'SF Mono', ui-monospace, monospace"
              fontWeight={solid ? '600' : '400'}
              textAnchor="end">
              {chartLabel(row.key, row.label)}
            </text>
            {/* Price — left-aligned on RIGHT side of line */}
            <text
              x={PRC_X} y={lblY + 4}
              fill={color} fontSize={10.5}
              fontFamily="'SF Mono', ui-monospace, monospace"
              textAnchor="start">
              {row.price.toFixed(2)}
            </text>
          </g>
        )
      })}

      {/* Current price badge — spans full line width */}
      {last > 0 && (() => {
        const cy = toY(last)
        return (
          <g>
            <line x1={LX1 - 6} y1={cy} x2={LX2 + 6} y2={cy}
              stroke={priceColor} strokeWidth={2} />
            <rect x={mid - 34} y={cy - 10} width={68} height={20}
              fill="#0f172a" stroke={priceColor} strokeWidth={1.5} rx={3} />
            <text x={mid} y={cy + 5}
              fill={priceColor} fontSize={11}
              fontFamily="'SF Mono', ui-monospace, monospace"
              fontWeight="700" textAnchor="middle">
              {last.toFixed(2)}
            </text>
          </g>
        )
      })()}
    </svg>
  )
}

// ── Individual level row with hover tooltip ───────────────────────────────────
function LevelRow({
  row, isClose, aboveLine, helpText, last,
}: {
  row: { key: string; label: string; price: number; dist: number }
  isClose: boolean
  aboveLine: boolean
  helpText?: string
  last: number
}) {
  const [showTip, setShowTip] = useState(false)
  const [tipUp,   setTipUp]   = useState(false)
  const rowRef = useRef<HTMLDivElement>(null)
  const color = row.key.startsWith('nvpoc_') ? NVPOC_COLOR : (LEVEL_COLOR[row.key] ?? 'var(--text-dim)')

  function handleMouseEnter() {
    if (rowRef.current) {
      const rect = rowRef.current.getBoundingClientRect()
      // Walk up the DOM to find the nearest scrollable ancestor (the panel's levels container)
      // and use its bottom edge as the real constraint — not window.innerHeight
      let boundary = window.innerHeight
      let el = rowRef.current.parentElement
      while (el) {
        const style = window.getComputedStyle(el)
        if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
          boundary = el.getBoundingClientRect().bottom
          break
        }
        el = el.parentElement
      }
      // Tooltip can be ~160px tall (multi-line help text + padding + title)
      setTipUp(boundary - rect.bottom < 170)
    }
    setShowTip(true)
  }

  return (
    <div
      ref={rowRef}
      className="flex items-center justify-between rounded-md px-3 py-1.5"
      style={{
        background: isClose ? 'rgba(255,255,255,0.05)' : 'transparent',
        border:     isClose ? '1px solid rgba(255,255,255,0.1)' : '1px solid transparent',
        position: 'relative',
      }}
    >
      {/* Label + help icon */}
      <div className="flex items-center gap-2 min-w-0">
        <div style={{ width: '3px', height: '14px', borderRadius: '2px', flexShrink: 0,
          background: color, opacity: aboveLine ? 0.7 : 1 }} />

        <span className="text-xs font-semibold" style={{ color, opacity: aboveLine ? 0.75 : 1 }}>
          {row.label}
        </span>

        {/* ⓘ help trigger */}
        {helpText && (
          <span
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              width: '14px', height: '14px', borderRadius: '50%', flexShrink: 0,
              fontSize: '9px', fontWeight: 700, lineHeight: 1, cursor: 'default',
              color: showTip ? '#fff' : 'var(--text-dim)',
              background: showTip ? color : 'rgba(255,255,255,0.08)',
              border: `1px solid ${showTip ? color : 'rgba(255,255,255,0.12)'}`,
              transition: 'all 0.15s',
            }}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={() => setShowTip(false)}
          >
            ?
          </span>
        )}

        {/* Tooltip bubble — flips above when near bottom of panel */}
        {showTip && helpText && (
          <div style={{
            position: 'absolute', left: '0',
            ...(tipUp ? { bottom: 'calc(100% + 6px)' } : { top: 'calc(100% + 6px)' }),
            width: '320px', zIndex: 10,
            padding: '10px 12px',
            background: 'var(--bg-panel)',
            border: `1px solid ${color}`,
            borderRadius: '8px',
            boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
            pointerEvents: 'none',
          }}>
            <p className="text-xs font-bold mb-1" style={{ color }}>
              {row.label}
            </p>
            <p className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>
              {helpText}
            </p>
          </div>
        )}
      </div>

      {/* Price + distance */}
      <div className="flex items-center gap-3 flex-shrink-0">
        {last > 0 && (
          <span className="text-xs tabular-nums" style={{ color: '#60a5fa', opacity: 0.7, fontSize: '10px' }}>
            {row.dist >= 0 ? '+' : ''}{row.dist.toFixed(2)}
          </span>
        )}
        <span className="text-xs font-bold tabular-nums"
          style={{ color: '#60a5fa', minWidth: '64px', textAlign: 'right' }}>
          {row.price.toFixed(2)}
        </span>
      </div>
    </div>
  )
}


interface NakedVpoc { date: string; vpoc: number }

// ── Hourly profile types ──────────────────────────────────────────────────────
interface HourScore {
  signal_strength: 'STRONG' | 'MODERATE' | 'WEAK' | 'DEAD'
  direction_bias:  'LONG' | 'SHORT' | 'NEUTRAL' | 'AVOID'
  win_rate:        number
  avg_pnl_usd:     number
  net_pnl_usd:     number
  total_trades:    number
  long_win_rate:   number
  short_win_rate:  number
  long_net_usd:    number
  short_net_usd:   number
  session:         string
}
type ProfileData = Record<string, Record<string, HourScore>>   // hour → model → score

const PROFILE_STRENGTH: Record<string, { label: string; bg: string; color: string }> = {
  STRONG:   { label: 'Hot',     bg: 'rgba(251,146,60,0.22)',  color: '#fb923c' },
  MODERATE: { label: 'Good',    bg: 'rgba(74,222,128,0.18)',  color: '#4ade80' },
  WEAK:     { label: 'Neutral', bg: 'rgba(148,163,184,0.10)', color: '#64748b' },
  DEAD:     { label: 'Avoid',   bg: 'rgba(248,113,113,0.10)', color: '#475569' },
}
const BIAS_ARROW: Record<string, string> = { LONG: '↑', SHORT: '↓', NEUTRAL: '', AVOID: '' }

// 9 AM–4 PM RTH hours
const RTH_HOURS = [9, 10, 11, 12, 13, 14, 15]
const MODELS    = ['AGG', 'CON', 'WIDE']

function HourLabel(h: number): string {
  const suffix = h >= 12 ? 'PM' : 'AM'
  const h12    = h > 12 ? h - 12 : h
  return `${h12}:00 ${suffix}`
}

function ProfileCell({ score }: { score?: HourScore }) {
  if (!score) return <div style={{ textAlign: 'center', color: '#334155', fontSize: '10px' }}>—</div>
  const cfg = PROFILE_STRENGTH[score.signal_strength]
  const arr = BIAS_ARROW[score.direction_bias] ?? ''
  return (
    <div style={{
      borderRadius: '5px',
      padding: '4px 6px',
      background: cfg.bg,
      textAlign: 'center',
    }}>
      <div style={{ fontSize: '10px', fontWeight: 700, color: cfg.color, lineHeight: 1.3 }}>
        {cfg.label}{arr ? ` ${arr}` : ''}
      </div>
      <div style={{ fontSize: '9px', color: cfg.color, opacity: 0.7, lineHeight: 1.2 }}>
        {score.win_rate.toFixed(0)}% WR
      </div>
    </div>
  )
}

function ProfileView({ profileData, currentHour }: { profileData: ProfileData; currentHour: number }) {
  const hasData = Object.keys(profileData).length > 0

  if (!hasData) {
    return (
      <div style={{ padding: '32px 0', textAlign: 'center', color: 'var(--text-dim)', fontSize: '13px' }}>
        No profile data available for this symbol
      </div>
    )
  }

  return (
    <div>
      {/* Legend */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '12px', flexWrap: 'wrap' }}>
        {Object.entries(PROFILE_STRENGTH).map(([, cfg]) => (
          <span key={cfg.label} style={{
            fontSize: '10px', fontWeight: 600, color: cfg.color,
            background: cfg.bg, borderRadius: '4px', padding: '2px 7px',
          }}>
            {cfg.label}
          </span>
        ))}
        <span style={{ fontSize: '10px', color: 'var(--text-dim)', marginLeft: 'auto' }}>
          RTH 9 AM – 4 PM ET
        </span>
      </div>

      {/* Grid: hour rows × model columns */}
      <div style={{ display: 'grid', gridTemplateColumns: '72px repeat(3, 1fr)', gap: '3px' }}>
        {/* Header */}
        <div style={{ fontSize: '9px', color: 'var(--text-dim)', fontWeight: 700,
          textTransform: 'uppercase', letterSpacing: '0.07em', alignSelf: 'center' }}>Hour</div>
        {MODELS.map(m => (
          <div key={m} style={{ fontSize: '9px', color: 'var(--text-dim)', fontWeight: 700,
            textTransform: 'uppercase', letterSpacing: '0.07em', textAlign: 'center' }}>{m}</div>
        ))}

        {/* One row per RTH hour */}
        {RTH_HOURS.map(h => {
          const hourScores = profileData[String(h)]
          const isNow = h === currentHour
          return (
            <div key={h} style={{ display: 'contents' }}>
              {/* Hour label */}
              <div style={{
                fontSize: '11px', fontWeight: isNow ? 700 : 400,
                color: isNow ? '#60a5fa' : 'var(--text-muted)',
                background: isNow ? 'rgba(96,165,250,0.08)' : 'transparent',
                borderRadius: '5px', padding: '6px 4px', alignSelf: 'center',
                borderLeft: isNow ? '2px solid #60a5fa' : '2px solid transparent',
                paddingLeft: isNow ? '6px' : '4px',
              }}>
                {HourLabel(h)}
                {isNow && <span style={{ fontSize: '8px', marginLeft: '4px', color: '#60a5fa' }}>◀</span>}
              </div>
              {/* AGG / CON / WIDE cells */}
              {MODELS.map(m => (
                <div key={m} style={{
                  background: isNow ? 'rgba(96,165,250,0.04)' : 'transparent',
                  borderRadius: '5px', padding: '2px',
                }}>
                  <ProfileCell score={hourScores?.[m]} />
                </div>
              ))}
            </div>
          )
        })}
      </div>

      {/* Net PnL summary row for each model */}
      <div style={{ marginTop: '14px', padding: '10px 12px',
        background: 'rgba(255,255,255,0.03)', borderRadius: '8px',
        border: '1px solid rgba(255,255,255,0.06)' }}>
        <div style={{ fontSize: '9px', color: 'var(--text-dim)', fontWeight: 700,
          textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '8px' }}>
          RTH Net PnL / year (all hours)
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
          {MODELS.map(m => {
            let net = 0
            RTH_HOURS.forEach(h => {
              net += profileData[String(h)]?.[m]?.net_pnl_usd ?? 0
            })
            return (
              <div key={m} style={{ textAlign: 'center' }}>
                <div style={{ fontSize: '9px', color: 'var(--text-dim)', fontWeight: 700,
                  textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: '3px' }}>{m}</div>
                <div style={{ fontSize: '13px', fontWeight: 700,
                  color: net >= 0 ? '#4ade80' : '#f87171' }}>
                  {net >= 0 ? '+' : ''}${Math.round(net).toLocaleString()}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <div style={{ marginTop: '8px', fontSize: '9px', color: 'var(--text-dim)', opacity: 0.5, textAlign: 'right' }}>
        365-day backtest · WR = win rate · ↑↓ = directional bias
      </div>
    </div>
  )
}

export default function FuturesPanel({ info, onClose }: FuturesPanelProps) {
  const { symbol, last, change, changePct } = info
  const panelRef = useRef<HTMLDivElement>(null)
  const [data, setData]             = useState<LevelsResponse | null>(null)
  const [loading, setLoading]       = useState(true)
  const [nakedVpocs, setNakedVpocs] = useState<NakedVpoc[]>([])
  const [view, setView]             = useState<'list' | 'chart' | 'profile'>('chart')
  const [profileData,    setProfileData]    = useState<ProfileData | null>(null)
  const [profileLoading, setProfileLoading] = useState(false)
  const [currentHour,    setCurrentHour]    = useState(0)

  // Fetch levels + naked VPOCs concurrently
  useEffect(() => {
    const encoded = encodeURIComponent(symbol)
    setLoading(true)
    setNakedVpocs([])

    Promise.all([
      fetch(`${API_URL}/api/levels/${encoded}`,        { cache: 'no-store' }).then(r => r.json()),
      fetch(`${API_URL}/api/session-vpocs/${encoded}`, { cache: 'no-store' }).then(r => r.json()),
    ])
      .then(([lvl, sess]) => {
        setData(lvl)
        setNakedVpocs(sess?.naked_vpocs ?? [])
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [symbol])

  // Fetch full hourly profile when Profile tab is first opened
  useEffect(() => {
    if (view !== 'profile' || profileData !== null) return
    const encoded = encodeURIComponent(symbol.split(':')[0])
    setProfileLoading(true)
    fetch(`${API_URL}/api/personality/${encoded}`, { cache: 'no-store' })
      .then(r => r.json())
      .then(res => {
        setProfileData(res.data ?? {})
        setCurrentHour(res.current_hour_et ?? new Date().getHours())
      })
      .catch(() => setProfileData({}))
      .finally(() => setProfileLoading(false))
  }, [view, symbol, profileData])

  // Close on outside click
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [onClose])

  // Close on Escape
  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const isUp        = changePct >= 0
  const priceColor  = isUp ? '#4ade80' : '#f87171'
  const hasPrice    = last > 0

  // Build sorted level list
  const sessionDate = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  const levelRows: { price: number; key: string; label: string; dist: number }[] = []
  if (data?.levels) {
    for (const [key, price] of Object.entries(data.levels)) {
      if (price == null || !(key in LEVEL_LABELS)) continue
      levelRows.push({
        price,
        key,
        label: LEVEL_LABELS[key].replace('__SESSION__', sessionDate),
        dist:  last > 0 ? price - last : 0,
      })
    }
  }

  // Merge IB levels from top-level response (not inside data.levels)
  // ib_source: 'today' = current session, 'prior' = showing yesterday's completed IB
  if (data?.ib_high != null) {
    const ibSuffix = data.ib_complete
      ? ''
      : data.ib_source === 'prior'
        ? '  (prior)'
        : '  (developing)'
    levelRows.push({ price: data.ib_high, key: 'ib_high', label: `${LEVEL_LABELS['ib_high']}${ibSuffix}`, dist: last > 0 ? data.ib_high - last : 0 })
  }
  if (data?.ib_low != null) {
    const ibSuffix = data.ib_complete
      ? ''
      : data.ib_source === 'prior'
        ? '  (prior)'
        : '  (developing)'
    levelRows.push({ price: data.ib_low, key: 'ib_low', label: `${LEVEL_LABELS['ib_low']}${ibSuffix}`, dist: last > 0 ? data.ib_low - last : 0 })
  }

  // Merge naked VPOCs — only closest above current price and closest below
  const existingPrices = new Set(levelRows.map(r => r.price))
  const filteredNv = nakedVpocs.filter(nv => !existingPrices.has(nv.vpoc))
  const nvAbove = last > 0
    ? filteredNv.filter(nv => nv.vpoc > last).sort((a, b) => a.vpoc - b.vpoc)[0]
    : filteredNv[0]
  const nvBelow = last > 0
    ? filteredNv.filter(nv => nv.vpoc < last).sort((a, b) => b.vpoc - a.vpoc)[0]
    : undefined
  for (const nv of [nvAbove, nvBelow].filter((x): x is NakedVpoc => !!x)) {
    const dateLabel = new Date(nv.date + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    levelRows.push({
      price: nv.vpoc,
      key:   `nvpoc_${nv.date}`,
      label: `Naked POC  ${dateLabel}`,
      dist:  last > 0 ? nv.vpoc - last : 0,
    })
  }

  levelRows.sort((a, b) => b.price - a.price)

  // Find insertion index for the current price separator
  const sepIdx = last > 0
    ? levelRows.findIndex(r => r.price <= last)
    : -1

  return (
    <>
      {/* Backdrop */}
      <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 999 }} />

      {/* Panel */}
      <div
        ref={panelRef}
        style={{
          position: 'fixed', top: '50%', left: '50%',
          transform: 'translate(-50%, -50%)',
          width: '480px', maxHeight: '92vh',
          display: 'flex', flexDirection: 'column',
          background: 'var(--bg-panel)',
          border: '1px solid var(--border)',
          borderRadius: '14px', zIndex: 1000,
          boxShadow: '0 24px 64px rgba(0,0,0,0.6)',
        }}
      >
        {/* Header */}
        <div style={{ padding: '20px 20px 12px' }}>
          <div className="flex items-start justify-between mb-1">
            <span className="text-xl font-bold tracking-wider" style={{ color: 'var(--text-primary)' }}>
              {symbol}
            </span>
            <button
              onClick={onClose}
              style={{ color: 'var(--text-dim)', fontSize: '20px', lineHeight: 1, marginTop: '-1px', marginLeft: '12px' }}
              className="transition-opacity hover:opacity-60"
            >×</button>
          </div>

          {/* Price row */}
          {hasPrice ? (
            <div
              className="flex items-center gap-3 rounded-lg px-3 py-2.5 mb-2 flex-wrap"
              style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)' }}
            >
              <span className="text-2xl font-bold tabular-nums" style={{ color: 'var(--text-primary)' }}>
                {last.toFixed(2)}
              </span>
              <span className="text-sm font-semibold tabular-nums" style={{ color: priceColor }}>
                {isUp ? '+' : ''}{change.toFixed(2)}{' '}
                <span style={{ opacity: 0.8 }}>({isUp ? '+' : ''}{changePct.toFixed(2)}%)</span>
              </span>

              {/* Overnight gap */}
              {data?.gap != null && (
                <span
                  className="text-xs font-semibold tabular-nums rounded px-2 py-0.5 ml-auto"
                  style={{
                    color     : data.gap >= 0 ? '#4ade80' : '#f87171',
                    background: data.gap >= 0 ? 'rgba(74,222,128,0.1)' : 'rgba(248,113,113,0.1)',
                    border    : `1px solid ${data.gap >= 0 ? 'rgba(74,222,128,0.3)' : 'rgba(248,113,113,0.3)'}`,
                  }}
                  title="Gap between previous session close and today's first trade"
                >
                  Gap {data.gap >= 0 ? '+' : ''}{data.gap.toFixed(2)}
                </span>
              )}
            </div>
          ) : null}

          {/* Section header */}
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: 'var(--text-dim)' }}>
              {view === 'profile' ? 'Hourly Profile' : 'Key Levels'}
            </span>
            <div className="flex items-center gap-1.5">
              {/* View toggle */}
              {[
                { key: 'chart',   label: '⟋ Chart'   },
                { key: 'list',    label: '≡ List'    },
                { key: 'profile', label: '◑ Profile' },
              ].map(({ key, label }) => (
                <button key={key} onClick={() => setView(key as 'list' | 'chart' | 'profile')}
                  style={{
                    fontSize: '10px', fontWeight: 600, padding: '2px 8px',
                    borderRadius: '4px', cursor: 'pointer',
                    background: view === key ? 'rgba(255,255,255,0.12)' : 'transparent',
                    color: view === key ? 'var(--text-primary)' : 'var(--text-dim)',
                    border: `1px solid ${view === key ? 'rgba(255,255,255,0.2)' : 'transparent'}`,
                    transition: 'all 0.15s',
                  }}>
                  {label}
                </button>
              ))}
              {view !== 'profile' && data?.computed_at && (
                <span className="text-xs" style={{ color: 'var(--text-dim)', opacity: 0.6 }}>
                  {data.computed_at}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Levels / Profile body */}
        <div style={{ overflowY: 'auto', padding: '0 20px 20px', flex: 1 }}>

          {/* ── Profile view ── */}
          {view === 'profile' && (
            profileLoading ? (
              <div className="py-8 text-center text-xs" style={{ color: 'var(--text-dim)' }}>
                Loading profile…
              </div>
            ) : (
              <ProfileView profileData={profileData ?? {}} currentHour={currentHour} />
            )
          )}

          {view !== 'profile' && loading && (
            <div className="py-8 text-center text-xs" style={{ color: 'var(--text-dim)' }}>
              Loading levels…
            </div>
          )}

          {view !== 'profile' && !loading && levelRows.length === 0 && (
            <div className="py-8 text-center text-xs" style={{ color: 'var(--text-dim)' }}>
              No level data available
            </div>
          )}

          {/* ── Chart view ── */}
          {!loading && levelRows.length > 0 && view === 'chart' && (
            <LevelsChart levelRows={levelRows} last={last} priceColor={priceColor} />
          )}

          {/* ── List view ── */}
          {!loading && levelRows.length > 0 && view === 'list' && (
            <div className="flex flex-col" style={{ gap: '2px' }}>
              {levelRows.map((row, i) => {
                const showSep   = sepIdx !== -1 && i === sepIdx
                const isClose   = last > 0 && Math.abs(row.dist) / last < 0.003
                const aboveLine = last > 0 ? row.price > last : false
                const helpText  = row.key.startsWith('nvpoc_')
                  ? 'A prior session\'s Point of Control that price has never returned to. These are the strongest magnets on the chart — price is eventually drawn back to fill them. The older the naked POC, the more powerful the pull.'
                  : LEVEL_HELP[row.key]

                return (
                  <div key={row.key}>
                    {/* Current price separator */}
                    {showSep && (
                      <div className="flex items-center gap-2 my-1.5">
                        <div style={{ flex: 1, height: '1px', background: priceColor, opacity: 0.5 }} />
                        <span
                          className="text-xs font-bold tabular-nums px-2 py-0.5 rounded"
                          style={{ color: priceColor, background: isUp ? 'rgba(74,222,128,0.1)' : 'rgba(248,113,113,0.1)', border: `1px solid ${priceColor}`, opacity: 0.85 }}
                        >
                          {last.toFixed(2)}
                        </span>
                        <div style={{ flex: 1, height: '1px', background: priceColor, opacity: 0.5 }} />
                      </div>
                    )}
                    <LevelRow
                      row={row}
                      isClose={isClose}
                      aboveLine={aboveLine}
                      helpText={helpText}
                      last={last}
                    />
                  </div>
                )
              })}

              {/* Separator at bottom if price is below all levels */}
              {sepIdx === -1 && last > 0 && (
                <div className="flex items-center gap-2 my-1.5">
                  <div style={{ flex: 1, height: '1px', background: priceColor, opacity: 0.5 }} />
                  <span className="text-xs font-bold tabular-nums px-2 py-0.5 rounded"
                    style={{ color: priceColor, background: isUp ? 'rgba(74,222,128,0.1)' : 'rgba(248,113,113,0.1)', border: `1px solid ${priceColor}` }}>
                    {last.toFixed(2)}
                  </span>
                  <div style={{ flex: 1, height: '1px', background: priceColor, opacity: 0.5 }} />
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
