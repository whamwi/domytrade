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
  session_vpoc:   number | null
  session_vah:    number | null
  session_val:    number | null
  overnight_vpoc: number | null
  mcvpoc_3day:    number | null
  daily_pivot:    number | null
  weekly_pivot:   number | null
  weekly_open:    number | null
  ath_intraday:   number | null
  swing_high:     number | null
  swing_low:      number | null
  prev_high:      number | null
  prev_low:       number | null
  prev_close:     number | null
  vwap:           number | null
}

interface LevelsResponse {
  symbol:      string
  tick:        number
  computed_at: string
  gap:         number | null
  ib_high:     number | null
  ib_low:      number | null
  ib_complete: boolean
  levels:      Levels
}

interface FuturesPanelProps {
  info:    FuturesPanelInfo
  onClose: () => void
}

const LEVEL_LABELS: Record<string, string> = {
  ath_intraday:   'All-Time High',
  swing_high:     'Swing High',
  prev_high:      'Prior High',
  session_vah:    'Value Area High',
  session_vpoc:   'Point of Control',
  session_val:    'Value Area Low',
  overnight_vpoc: 'Night Point of Control',
  mcvpoc_3day:    '3-Day Composite POC',
  vwap:           'VWAP  —  Fair Value',
  ib_high:        'Initial Balance High',
  ib_low:         'Initial Balance Low',
  daily_pivot:    'Daily Pivot',
  weekly_pivot:   'Weekly Pivot',
  weekly_open:    'Weekly Open',
  prev_close:     'Prior Close',
  prev_low:       'Prior Low',
  swing_low:      'Swing Low',
}

const LEVEL_HELP: Record<string, string> = {
  session_vpoc:   'The price where the most volume traded during the session. Price is naturally drawn back to this level — acts as a magnet.',
  session_vah:    'Value Area High — the upper boundary of the price range containing 70% of the prior session\'s volume. Price above VAH means the market is seeking acceptance at higher prices. If it fails to hold above, expect rejection back inside the Value Area.',
  session_val:    'Value Area Low — the lower boundary of the 70% volume zone. Price below VAL means sellers are in control. If buyers step in and push price back above VAL, the gap fill trade toward POC becomes likely.',
  overnight_vpoc: 'The highest-volume price from the overnight (pre-market) session. Shows where institutions were most active before the regular open.',
  mcvpoc_3day:    'The single price with the most volume across the last 3 sessions combined. A powerful multi-day magnet — harder to break than a single-session POC.',
  daily_pivot:    'Calculated as (Yesterday\'s High + Low + Close) ÷ 3. A neutral reference: above it = bullish bias for the day, below = bearish.',
  weekly_pivot:   'Same calculation using last week\'s range. Defines the broader weekly structure and is watched by institutional traders.',
  weekly_open:    'The price where the current week\'s session started. Closing above it confirms a bullish week; below confirms bearish.',
  ath_intraday:   'The highest intraday price ever recorded for this contract. No overhead resistance above it — price is in open air.',
  swing_high:     'The highest price reached over the last 10 sessions. Acts as resistance; a close above it signals a breakout.',
  swing_low:      'The lowest price reached over the last 10 sessions. Acts as support; a close below it signals a breakdown.',
  prev_high:      'Yesterday\'s session high. Breaking above it with volume confirms bullish momentum continuation.',
  prev_low:       'Yesterday\'s session low. Breaking below it with volume confirms bearish momentum continuation.',
  prev_close:     'Where yesterday\'s session ended. The starting reference for today\'s price action and performance.',
  vwap:           'Volume Weighted Average Price — the average price paid weighted by volume. During the session this updates live. Outside market hours it shows the prior session\'s closing VWAP. Price above VWAP = institutions were net buyers. Price below = net sellers. The market constantly gravitates back to this level — it is the true fair value.',
  ib_high:        'Initial Balance High — the highest price of the first 60 minutes of RTH (9:30–10:30 ET). A break above IB High after 10:30 signals a trend day extending upward. Price staying inside IB suggests a rotational/balanced day.',
  ib_low:         'Initial Balance Low — the lowest price of the first 60 minutes of RTH. A break below IB Low after 10:30 signals a trend day extending downward. Narrow IB range = coiled market, explosive move likely.',
}

const LEVEL_COLOR: Record<string, string> = {
  ath_intraday:   '#f87171',   // red — ceiling
  swing_high:     '#fb923c',
  prev_high:      '#fbbf24',
  session_vah:    '#818cf8',   // indigo — value area boundary (upper)
  session_vpoc:   '#a78bfa',   // purple — volume nodes
  session_val:    '#818cf8',   // indigo — value area boundary (lower)
  overnight_vpoc: '#a78bfa',
  mcvpoc_3day:    '#c084fc',
  ib_high:        '#34d399',   // emerald — initial balance
  ib_low:         '#34d399',
  daily_pivot:    '#60a5fa',   // blue — pivots
  weekly_pivot:   '#60a5fa',
  weekly_open:    '#38bdf8',
  prev_close:     '#94a3b8',   // gray
  prev_low:       '#4ade80',   // green — floors
  swing_low:      '#4ade80',
  vwap:           '#f59e0b',   // amber — fair value
}

const NVPOC_COLOR = '#22d3ee'  // cyan — naked/unfilled volume nodes

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

export default function FuturesPanel({ info, onClose }: FuturesPanelProps) {
  const { symbol, last, change, changePct } = info
  const panelRef = useRef<HTMLDivElement>(null)
  const [data, setData]             = useState<LevelsResponse | null>(null)
  const [loading, setLoading]       = useState(true)
  const [nakedVpocs, setNakedVpocs] = useState<NakedVpoc[]>([])

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
  const levelRows: { price: number; key: string; label: string; dist: number }[] = []
  if (data?.levels) {
    for (const [key, price] of Object.entries(data.levels)) {
      if (price == null || !(key in LEVEL_LABELS)) continue
      levelRows.push({
        price,
        key,
        label: LEVEL_LABELS[key],
        dist:  last > 0 ? price - last : 0,
      })
    }
  }

  // Merge IB levels from top-level response (not inside data.levels)
  if (data?.ib_high != null) {
    const ibLabel = data.ib_complete
      ? LEVEL_LABELS['ib_high']
      : `${LEVEL_LABELS['ib_high']}  (developing)`
    levelRows.push({ price: data.ib_high, key: 'ib_high', label: ibLabel, dist: last > 0 ? data.ib_high - last : 0 })
  }
  if (data?.ib_low != null) {
    const ibLabel = data.ib_complete
      ? LEVEL_LABELS['ib_low']
      : `${LEVEL_LABELS['ib_low']}  (developing)`
    levelRows.push({ price: data.ib_low, key: 'ib_low', label: ibLabel, dist: last > 0 ? data.ib_low - last : 0 })
  }

  // Merge naked VPOCs — skip any whose price is already shown as session_vpoc or mcvpoc_3day
  const existingPrices = new Set(levelRows.map(r => r.price))
  for (const nv of nakedVpocs) {
    if (existingPrices.has(nv.vpoc)) continue   // avoid duplicate
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
          width: '480px', maxHeight: '85vh',
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
              Key Levels
            </span>
            {data?.computed_at && (
              <span className="text-xs" style={{ color: 'var(--text-dim)', opacity: 0.6 }}>
                {data.computed_at}
              </span>
            )}
          </div>
        </div>

        {/* Levels ladder */}
        <div style={{ overflowY: 'auto', padding: '0 20px 20px', flex: 1 }}>
          {loading && (
            <div className="py-8 text-center text-xs" style={{ color: 'var(--text-dim)' }}>
              Loading levels…
            </div>
          )}

          {!loading && levelRows.length === 0 && (
            <div className="py-8 text-center text-xs" style={{ color: 'var(--text-dim)' }}>
              No level data available
            </div>
          )}

          {!loading && levelRows.length > 0 && (
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

                    {/* Level row */}
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
