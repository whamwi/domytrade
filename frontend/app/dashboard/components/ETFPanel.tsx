'use client'

import { useEffect, useRef, useState } from 'react'
import { ETF_META, Holding } from './etfMeta'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export interface EtfPanelInfo {
  symbol:    string
  last:      number
  change:    number
  changePct: number
}

interface HoldingPrice {
  last:      number | null
  change:    number | null
  changePct: number | null
}

interface SRLevel {
  price:     number
  touches:   number
  zone_type: string
  dist_pct?: number
}

interface HoldingSR {
  resistance:   SRLevel[]
  support:      SRLevel[]
  candle_price: number
  bars:         number
}


interface ETFPanelProps {
  info:    EtfPanelInfo
  onClose: () => void
}

export default function ETFPanel({ info, onClose }: ETFPanelProps) {
  const { symbol, last, change, changePct } = info
  const meta     = ETF_META[symbol]
  const panelRef = useRef<HTMLDivElement>(null)

  // Live holdings from DB (Yahoo Finance, refreshed daily)
  // Falls back to etfMeta.ts if cache not yet populated
  const [dbHoldings,    setDbHoldings]    = useState<Holding[]>([])
  const [holdingPrices, setHoldingPrices] = useState<Record<string, HoldingPrice>>({})
  const [holdingSR,     setHoldingSR]     = useState<Record<string, HoldingSR>>({})
  const [loadingPrices, setLoadingPrices] = useState(false)

  // YTD performance: ETF vs SPY
  const [ytd, setYtd] = useState<{ etf: number | null; spy: number | null }>({ etf: null, spy: null })


  // 1. Fetch holdings from DB
  useEffect(() => {
    fetch(`${API_URL}/api/etf-holdings/${symbol}`, { cache: 'no-store' })
      .then(r => r.json())
      .then(data => {
        if (data.holdings?.length > 0) setDbHoldings(data.holdings)
      })
      .catch(() => {})
  }, [symbol])

  // 1b. Fetch YTD % for this ETF and SPY
  useEffect(() => {
    const syms = symbol === 'SPY' ? symbol : `${symbol},SPY`
    fetch(`${API_URL}/api/ytd?symbols=${encodeURIComponent(syms)}`, { cache: 'no-store' })
      .then(r => r.json())
      .then(data => setYtd({ etf: data[symbol] ?? null, spy: data['SPY'] ?? null }))
      .catch(() => {})
  }, [symbol])

  // Normalize Yahoo Finance ticker format to Schwab format:
  //   BRK-B → BRK/B  (class shares)
  //   ENLT.TA → ENLT  (strip exchange suffix)
  const normTicker = (t: string) =>
    t.replace(/-([A-Z])$/, '/$1').replace(/\.[A-Z]{1,4}$/, '')

  // Build a lookup of meta holdings by ticker for name/weight fallback
  const metaByTicker = Object.fromEntries(
    (meta?.holdings ?? []).map(h => [h.ticker, h])
  )

  // Active holdings: DB gives us current ranking + live tickers;
  // meta fills in name and weight when yfinance returns empty/zero for those fields.
  // Schwab quote names (holdingPrices[t].name) fill any remaining gaps.
  // Sorted by weight descending so heaviest holdings are always at the top.
  const rawHoldings = dbHoldings.length > 0 ? dbHoldings : (meta?.holdings ?? [])
  const holdings = rawHoldings
    .map(h => {
      const norm = normTicker(h.ticker)
      // Look up meta by normalized ticker (handles BRK-B → BRK/B mismatch)
      const m = metaByTicker[norm] ?? metaByTicker[h.ticker]
      const p = holdingPrices[norm] ?? holdingPrices[h.ticker]
      return {
        ...h,
        ticker: norm,   // always use normalized ticker for price lookups + display
        name  : h.name || m?.name || (p as any)?.name || '',
        weight: h.weight > 0 ? h.weight : (m?.weight ?? 0),
      }
    })
    .sort((a, b) => b.weight - a.weight)

  // Stable tickers string — only changes when the actual set of tickers changes
  const tickersKey = holdings.map(h => h.ticker).join(',')

  // 2. Fetch live prices + S/R for all holdings
  useEffect(() => {
    if (!tickersKey) return
    setLoadingPrices(true)
    // Live quotes
    fetch(`${API_URL}/api/quotes?symbols=${encodeURIComponent(tickersKey)}`, { cache: 'no-store' })
      .then(r => r.json())
      .then(data => setHoldingPrices(data))
      .catch(() => {})
      .finally(() => setLoadingPrices(false))

    // S/R levels — fetch once, then retry any missing tickers after 4 s
    const fetchSR = (tickers: string) =>
      fetch(`${API_URL}/api/sr?tickers=${encodeURIComponent(tickers)}`, { cache: 'no-store' })
        .then(r => r.json())
        .catch(() => ({} as Record<string, HoldingSR>))

    let retryTimer: ReturnType<typeof setTimeout>
    fetchSR(tickersKey).then(data => {
      setHoldingSR(data)
      // Retry tickers that came back empty (backend still warming cache)
      const missing = tickersKey.split(',').filter(t => !data[t])
      if (missing.length > 0) {
        retryTimer = setTimeout(() => {
          fetchSR(missing.join(',')).then(retry => {
            if (Object.keys(retry).length > 0) {
              setHoldingSR(prev => ({ ...prev, ...retry }))
            }
          })
        }, 4000)
      }
    })
    return () => clearTimeout(retryTimer)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tickersKey])

  // Close on outside click
  useEffect(() => {
    function handleDown(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleDown)
    return () => document.removeEventListener('mousedown', handleDown)
  }, [onClose])

  // Close on Escape
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [onClose])

  if (!meta) return null

  const isUp       = changePct >= 0
  const priceColor = isUp ? '#4ade80' : '#f87171'
  const hasPrice   = last > 0
  const maxWeight  = holdings.length ? Math.max(...holdings.map(h => h.weight)) : 1

  return (
    <>
      {/* Backdrop */}
      <div
        style={{
          position: 'fixed', inset: 0,
          background: 'rgba(0,0,0,0.45)',
          zIndex: 999,
        }}
      />

      {/* Panel */}
      <div
        ref={panelRef}
        style={{
          position:     'fixed',
          top:          '50%',
          left:         '50%',
          transform:    'translate(-50%, -50%)',
          width:        '560px',
          maxHeight:    '85vh',
          display:      'flex',
          flexDirection:'column',
          background:   'var(--bg-panel)',
          border:       '1px solid var(--border)',
          borderRadius: '14px',
          zIndex:       1000,
          boxShadow:    '0 24px 64px rgba(0,0,0,0.6)',
        }}
      >
        {/* ── Fixed header ───────────────────────────────── */}
        <div style={{ padding: '20px 20px 0' }}>
          <div className="flex items-start justify-between mb-1">
            <span
              className="text-xl font-bold tracking-wider"
              style={{ color: 'var(--text-primary)' }}
            >
              {symbol}
            </span>
            <button
              onClick={onClose}
              style={{
                color:      'var(--text-dim)',
                fontSize:   '20px',
                lineHeight: 1,
                marginTop:  '-1px',
                marginLeft: '12px',
              }}
              className="transition-opacity hover:opacity-60"
            >
              ×
            </button>
          </div>

          <p className="text-xs mb-3" style={{ color: 'var(--text-muted)', lineHeight: 1.5 }}>
            {meta.description}
          </p>

          {/* YTD comparison row — above price, border colour = vs-SPY performance */}
          {(() => {
            const delta       = ytd.etf !== null && ytd.spy !== null ? ytd.etf - ytd.spy : null
            const beating     = delta !== null ? delta >= 0 : null
            const borderColor = beating === true  ? 'rgba(74,222,128,0.55)'
                              : beating === false ? 'rgba(248,113,113,0.55)'
                              : 'var(--border)'
            const bgColor     = beating === true  ? 'rgba(74,222,128,0.06)'
                              : beating === false ? 'rgba(248,113,113,0.06)'
                              : 'rgba(255,255,255,0.03)'
            return (
              <div
                className="flex items-center gap-2 rounded-lg px-3 py-2 mb-3"
                style={{ background: bgColor, border: `1.5px solid ${borderColor}` }}
              >
                {/* Label */}
                <span className="text-xs font-semibold uppercase tracking-widest"
                  style={{ color: 'var(--text-dim)', flexShrink: 0 }}>
                  YTD
                </span>

                {/* ETF */}
                <div className="flex items-center gap-1.5 flex-1">
                  <span className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>{symbol}</span>
                  {ytd.etf !== null
                    ? <span className="text-xs font-semibold tabular-nums"
                        style={{ color: ytd.etf >= 0 ? '#4ade80' : '#f87171' }}>
                        {ytd.etf >= 0 ? '+' : ''}{ytd.etf.toFixed(1)}%
                      </span>
                    : <span className="text-xs animate-pulse" style={{ color: 'var(--text-dim)' }}>…</span>
                  }
                </div>

                <div style={{ width: '1px', height: '14px', background: 'var(--border)' }} />

                {/* SPY */}
                <div className="flex items-center gap-1.5 flex-1">
                  <span className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>SPY</span>
                  {ytd.spy !== null
                    ? <span className="text-xs font-semibold tabular-nums"
                        style={{ color: ytd.spy >= 0 ? '#4ade80' : '#f87171' }}>
                        {ytd.spy >= 0 ? '+' : ''}{ytd.spy.toFixed(1)}%
                      </span>
                    : <span className="text-xs animate-pulse" style={{ color: 'var(--text-dim)' }}>…</span>
                  }
                </div>

                {/* vs SPY delta */}
                {delta !== null && (
                  <>
                    <div style={{ width: '1px', height: '14px', background: 'var(--border)' }} />
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <span className="text-xs" style={{ color: 'var(--text-dim)' }}>vs SPY</span>
                      <span className="text-xs font-bold tabular-nums"
                        style={{ color: delta >= 0 ? '#4ade80' : '#f87171' }}>
                        {delta >= 0 ? '+' : ''}{delta.toFixed(1)}%
                      </span>
                    </div>
                  </>
                )}
              </div>
            )
          })()}

          {/* ETF price */}
          {hasPrice ? (
            <div
              className="flex items-baseline gap-3 rounded-lg px-3 py-2.5 mb-4"
              style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)' }}
            >
              <span
                className="text-2xl font-bold tabular-nums"
                style={{ color: 'var(--text-primary)' }}
              >
                {last >= 100 ? last.toFixed(2) : last.toFixed(3)}
              </span>
              <span className="text-sm font-semibold tabular-nums" style={{ color: priceColor }}>
                {isUp ? '+' : ''}{change.toFixed(2)}{' '}
                <span style={{ opacity: 0.8 }}>({isUp ? '+' : ''}{changePct.toFixed(2)}%)</span>
              </span>
            </div>
          ) : (
            <div
              className="rounded-lg px-3 py-2.5 mb-4 text-xs text-center"
              style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', color: 'var(--text-dim)' }}
            >
              Price unavailable
            </div>
          )}

          {/* Holdings header row */}
          <div className="flex items-center justify-between mb-2">
            <span
              className="text-xs font-semibold uppercase tracking-widest"
              style={{ color: 'var(--text-dim)' }}
            >
              Top Holdings
            </span>
            <div className="flex gap-3">
              <span className="text-xs font-semibold uppercase tracking-widest w-20 text-right"
                style={{ color: 'var(--text-dim)' }}>Last</span>
              <span className="text-xs font-semibold uppercase tracking-widest w-16 text-right"
                style={{ color: 'var(--text-dim)' }}>Chg%</span>
              <span className="text-xs font-semibold uppercase tracking-widest w-10 text-right"
                style={{ color: 'var(--text-dim)' }}>Wgt</span>
              <span className="text-xs font-semibold uppercase tracking-widest w-16 text-right"
                style={{ color: '#4ade80', opacity: 0.7 }}>Support</span>
              <span className="text-xs font-semibold uppercase tracking-widest w-16 text-right"
                style={{ color: '#f87171', opacity: 0.7 }}>Resist</span>
            </div>
          </div>
        </div>

        {/* ── Scrollable holdings list ────────────────────── */}
        <div
          style={{
            overflowY: 'auto',
            padding:   '0 20px 20px',
            flex:      1,
          }}
        >
          {loadingPrices && holdingPrices && Object.keys(holdingPrices).length === 0 && (
            <div className="py-4 text-center text-xs" style={{ color: 'var(--text-dim)' }}>
              Loading prices…
            </div>
          )}

          <div className="flex flex-col gap-3">
            {holdings.map((h, i) => {
              const p       = holdingPrices[h.ticker]
              const hIsUp   = p?.changePct != null ? p.changePct >= 0 : true
              const hColor  = p?.changePct != null
                ? (hIsUp ? '#4ade80' : '#f87171')
                : 'var(--text-dim)'
              // Use live quote price to pick nearest resistance above / support below
              const livePrice = p?.last ?? 0
              const srData    = holdingSR[h.ticker]
              const nearestResist = livePrice > 0 && srData
                ? (srData.resistance ?? []).find(r => r.price > livePrice) ?? null
                : null
              const nearestSupport = livePrice > 0 && srData
                ? (srData.support ?? []).find(s => s.price < livePrice) ?? null
                : null
              return (
                <div key={h.ticker}>
                  {/* Row: rank + ticker | last | chg% | weight */}
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2 min-w-0">
                      <span
                        className="text-xs tabular-nums w-4 shrink-0 text-right"
                        style={{ color: 'var(--text-dim)' }}
                      >
                        {i + 1}
                      </span>
                      <span
                        className="text-xs font-bold tracking-wide shrink-0"
                        style={{ color: 'var(--text-primary)', minWidth: '40px' }}
                      >
                        {h.ticker}
                      </span>
                      {h.name ? (
                        <span
                          className="text-xs truncate"
                          style={{ color: 'var(--text-muted)', fontSize: '10px', maxWidth: '90px', opacity: 0.55 }}
                          title={h.name}
                        >
                          {h.name.slice(0, 22)}
                        </span>
                      ) : null}
                    </div>
                    <div className="flex items-center gap-3">
                      {/* Last price */}
                      <span
                        className="text-xs tabular-nums w-20 text-right"
                        style={{ color: 'var(--text-primary)', fontWeight: 600 }}
                      >
                        {p?.last != null
                          ? (p.last >= 100 ? p.last.toFixed(2) : p.last.toFixed(3))
                          : '—'}
                      </span>
                      {/* % change */}
                      <span
                        className="text-xs tabular-nums w-16 text-right font-semibold"
                        style={{ color: hColor }}
                      >
                        {p?.changePct != null
                          ? `${hIsUp ? '+' : ''}${p.changePct.toFixed(2)}%`
                          : '—'}
                      </span>
                      {/* Weight */}
                      <span
                        className="text-xs tabular-nums w-10 text-right"
                        style={{ color: 'var(--text-muted)' }}
                      >
                        {h.weight.toFixed(1)}%
                      </span>
                      {/* Support — nearest Fib level strictly below live price */}
                      <span
                        className="text-xs tabular-nums w-16 text-right"
                        style={{ color: '#4ade80', opacity: nearestSupport ? 1 : 0.35 }}
                        title={nearestSupport
                          ? `Fib ${nearestSupport.zone_type} — ${nearestSupport.dist_pct?.toFixed(1)}% away`
                          : srData ? 'No Fib floor below price' : 'Loading…'}
                      >
                        {nearestSupport ? nearestSupport.price.toFixed(2) : '—'}
                      </span>
                      {/* Resistance — nearest Fib level strictly above live price */}
                      <span
                        className="text-xs tabular-nums w-16 text-right"
                        style={{ color: '#f87171', opacity: nearestResist ? 1 : 0.35 }}
                        title={nearestResist
                          ? `Fib ${nearestResist.zone_type} — ${nearestResist.dist_pct?.toFixed(1)}% away`
                          : srData ? 'No Fib resistance above price' : 'Loading…'}
                      >
                        {nearestResist ? nearestResist.price.toFixed(2) : '—'}
                      </span>
                    </div>
                  </div>
                  {/* Weight bar */}
                  <div
                    style={{
                      height:       '3px',
                      background:   'rgba(255,255,255,0.06)',
                      borderRadius: '2px',
                      overflow:     'hidden',
                    }}
                  >
                    <div
                      style={{
                        height:       '100%',
                        width:        `${(h.weight / maxWeight) * 100}%`,
                        background:   'var(--accent-blue)',
                        borderRadius: '2px',
                      }}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </>
  )
}
