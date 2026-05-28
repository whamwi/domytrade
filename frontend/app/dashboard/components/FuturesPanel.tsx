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
  // Dalton TPO — primary display (volume profile hidden, kept in backend for agent)
  prior_rth_tpo_vah:    'Prior RTH  —  VAH',
  prior_rth_tpo_vpoc:   'Prior RTH  —  POC',
  prior_rth_tpo_val:    'Prior RTH  —  VAL',
  overnight_tpo_vah:    'Overnight  —  VAH',
  overnight_tpo_vpoc:   'Overnight  —  POC',
  overnight_tpo_val:    'Overnight  —  VAL',
  developing_tpo_vah:   'Developing  —  VAH',
  developing_tpo_vpoc:  'Developing  —  POC',
  developing_tpo_val:   'Developing  —  VAL',
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
  developing_tpo_vah:  'Developing Value Area High — live upper boundary of today\'s TPO value area. Updates every 30 minutes as new periods complete. Shows where today\'s session is currently accepting the upper edge of value.',
  developing_tpo_vpoc: 'Developing Point of Control — today\'s current TPO POC, the price revisited by the most 30-min periods so far. The session\'s live fulcrum — watch for price to accept or reject it as the day builds.',
  developing_tpo_val:  'Developing Value Area Low — live lower boundary of today\'s TPO value. Holding above = buyers in control of value migration. Breaking below = value shifting lower.',
  // Other
  mcvpoc_3day:         'The single price with the most volume across the last 3 RTH sessions combined. A powerful multi-day magnet — harder to break than a single-session POC.',
  daily_pivot:         '(Yesterday\'s High + Low + Close) ÷ 3. A neutral reference: above it = bullish bias for the day, below = bearish.',
  prev_high:           'Yesterday\'s RTH session high (4:00 PM close included). Breaking above with volume confirms bullish continuation.',
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
  prev_low:             '#4ade80',   // green — floors
  vwap:                 '#f59e0b',   // amber — fair value
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

  // Merge naked VPOCs — skip any whose price is already shown as prior_rth_vpoc or mcvpoc_3day
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
