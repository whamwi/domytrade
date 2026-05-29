'use client'

import { useState, useRef } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''
import SwingBar from './SwingBar'
import { ETF_META } from './etfMeta'
import ETFPanel, { EtfPanelInfo } from './ETFPanel'
import FuturesPanel, { FuturesPanelInfo } from './FuturesPanel'
import StockInfoPanel, { StockPanelInfo } from './StockInfoPanel'

// Futures that get a clickable levels panel
const FUTURES_PANEL_TICKERS = new Set(['/ES','/NQ','/YM','/RTY','/GC','/CL','/SI','/PL','/NG','/ZB','/ZN','/HG','/RB','/ZC','/ZS','/BTC'])

// The 4 major US equity index futures — each with a distinct accent color
const MAJOR_MARKET_COLORS: Record<string, { border: string; bg: string; hover: string }> = {
  '/ES' : { border: 'rgba(59,130,246,0.6)',  bg: 'rgba(59,130,246,0.05)',  hover: 'rgba(59,130,246,0.10)'  }, // blue
  '/NQ' : { border: 'rgba(168,85,247,0.6)',  bg: 'rgba(168,85,247,0.05)',  hover: 'rgba(168,85,247,0.10)'  }, // purple
  '/YM' : { border: 'rgba(251,146,60,0.6)',  bg: 'rgba(251,146,60,0.05)',  hover: 'rgba(251,146,60,0.10)'  }, // orange
  '/RTY': { border: 'rgba(20,184,166,0.6)',  bg: 'rgba(20,184,166,0.05)',  hover: 'rgba(20,184,166,0.10)'  }, // teal
}

// Mirrors the set in page.tsx — used to identify sector/ETF tickers
const SECTOR_TICKERS = new Set([
  'XLK','XLV','XLF','XLC','XLY','XLI','XLP','XLE','XLB','XLU','XLRE',
  'SMH','HACK','SKYY','TAN','JETS','OIH','IYT','EEM','SOCL','KCE','XLG','XRT','OEF',
])

export interface Signal {
  symbol: string
  api_symbol: string
  side: 'LONG' | 'SHORT'
  model: 'AGG' | 'CON' | 'WIDE' | 'CR'
  entry: number
  stop: number
  target: number
  lower_gray: number
  upper_gray: number
  near_gray?: boolean          // legacy — kept for compatibility
  signal_state?: 'NEAR' | 'ENTRY'
  entry_alert?: boolean        // true only on NEAR→ENTRY transition
  daily_bias?: 'LONG' | 'SHORT' | null
  last: number
  prev_close: number
  net_change?: number | null
  swing_pct: number
  current_range: number
  typical_range: number
  hour_high: number
  hour_low: number
  l1: number
  l2: number
  l3: number
  l4: number
  // 5-min SqueezePRO — only present for /ES /NQ /YM /RTY
  sq_state?:   string | null
  mo_state?:   string | null
  sq_confirm?: 'CONFIRMED' | 'CAUTION' | 'NEGATED' | 'NEUTRAL' | null
  sq_reason?:  string | null
}

export interface SymbolInfo {
  id: number
  ticker: string
  asset_type: string
  last_price?: number | null
  prev_close?: number | null
  net_change?: number | null
}

interface SignalTableProps {
  signals: Signal[]
  allSymbols: SymbolInfo[]
  loading: boolean
  error: string | null
  onRetry: () => void
  ytdMap?: Record<string, number>
}

function fmt(price: number): string {
  if (price >= 1000) return price.toFixed(2)
  if (price >= 100)  return price.toFixed(2)
  if (price >= 10)   return price.toFixed(3)
  return price.toFixed(4)
}

const COLS = [
  { label: '#',           align: 'left'   },
  { label: 'SYMBOL',      align: 'left'   },
  { label: 'LAST',        align: 'right'  },
  { label: 'CHG',         align: 'right'  },
  { label: 'MODEL',       align: 'left'   },  // AGG or CON
  { label: 'AI',           align: 'center' },  // on-demand AI advisory
  { label: 'SIDE',        align: 'left'   },
  { label: 'ALERT',       align: 'left'   },  // near gray trigger
  { label: 'ENTRY',       align: 'right'  },
  { label: 'STOP',        align: 'right'  },
  { label: 'TARGET',      align: 'right'  },
  { label: 'DAILY SWING', align: 'left'   },
]

// ── On-demand AI Advisory button ──────────────────────────────────────────────
type AiVerdict = 'ENTER' | 'WAIT' | 'SKIP'
interface AiState { verdict: AiVerdict | null; reason: string; loading: boolean; open: boolean }

const AI_VERDICT_STYLE: Record<AiVerdict, { bg: string; color: string; dot: string }> = {
  ENTER: { bg: 'rgba(74,222,128,0.15)',  color: '#4ade80', dot: '●' },
  WAIT:  { bg: 'rgba(251,191,36,0.15)',  color: '#fbbf24', dot: '◑' },
  SKIP:  { bg: 'rgba(248,113,113,0.15)', color: '#f87171', dot: '●' },
}

function AiAdvisoryButton({ symbol, model, side }: { symbol: string; model: string; side: string }) {
  const [ai, setAi]       = useState<AiState>({ verdict: null, reason: '', loading: false, open: false })
  const [popPos, setPopPos] = useState<{ top: number; left: number } | null>(null)
  const btnRef = useRef<HTMLButtonElement>(null)

  const fetchAdvisory = async () => {
    // Capture button position before opening so we can place the fixed popup
    if (btnRef.current) {
      const r = btnRef.current.getBoundingClientRect()
      const popW = 280
      let left = r.left + r.width / 2 - popW / 2
      // Keep within viewport horizontally
      left = Math.max(8, Math.min(left, window.innerWidth - popW - 8))
      // Open below by default; if too close to bottom, open above
      const spaceBelow = window.innerHeight - r.bottom
      const top = spaceBelow > 220 ? r.bottom + 6 : r.top - 6   // 6px gap
      setPopPos({ top, left })
    }
    setAi(prev => ({ ...prev, loading: true, open: true }))
    try {
      const res  = await fetch(`${API_URL}/api/ai/signal-advisory`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ symbol, model, side }),
      })
      const data = await res.json()
      setAi({ verdict: data.verdict as AiVerdict, reason: data.reason, loading: false, open: true })
    } catch {
      setAi({ verdict: 'WAIT', reason: 'Network error — try again.', loading: false, open: true })
    }
  }

  const handleClick = () => {
    if (ai.verdict && !ai.loading) {
      // Re-capture position and show again
      if (btnRef.current) {
        const r = btnRef.current.getBoundingClientRect()
        const popW = 280
        let left = r.left + r.width / 2 - popW / 2
        left = Math.max(8, Math.min(left, window.innerWidth - popW - 8))
        const spaceBelow = window.innerHeight - r.bottom
        const top = spaceBelow > 220 ? r.bottom + 6 : r.top - 6
        setPopPos({ top, left })
      }
      setAi(prev => ({ ...prev, open: true }))
    } else if (!ai.loading) {
      fetchAdvisory()
    }
  }

  const handleClose = () => setAi(prev => ({ ...prev, open: false }))

  const vStyle = ai.verdict ? AI_VERDICT_STYLE[ai.verdict] : null

  // Determine if popup opens upward (when near bottom of screen)
  const opensUp = popPos && btnRef.current
    ? (window.innerHeight - btnRef.current.getBoundingClientRect().bottom) <= 220
    : false

  return (
    <div className="inline-flex items-center justify-center">
      {/* The button */}
      <button
        ref={btnRef}
        onClick={handleClick}
        disabled={ai.loading}
        className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-bold transition-opacity"
        style={vStyle
          ? { background: vStyle.bg, color: vStyle.color, minWidth: '3.2rem', justifyContent: 'center' }
          : { background: 'rgba(168,85,247,0.12)', color: '#a855f7', minWidth: '3.2rem', justifyContent: 'center' }
        }
        title="Get AI advisory for this signal"
      >
        {ai.loading ? (
          <span style={{ fontSize: '11px' }}>…</span>
        ) : vStyle ? (
          <>
            <span style={{ fontSize: 7 }}>{vStyle.dot}</span>
            {ai.verdict}
          </>
        ) : (
          <>
            <span style={{ fontSize: '11px' }}>✦</span>
            AI
          </>
        )}
      </button>

      {/* Pop-up — fixed position to escape table overflow clipping */}
      {ai.open && popPos && (
        <>
          {/* Backdrop */}
          <div className="fixed inset-0 z-[1090]" onClick={handleClose} />
          {/* Card */}
          <div
            className="rounded-lg p-3 shadow-xl"
            style={{
              position:        'fixed',
              top:             opensUp ? undefined : popPos.top,
              bottom:          opensUp ? window.innerHeight - popPos.top : undefined,
              left:            popPos.left,
              width:           '280px',
              zIndex:          1100,
              background:      '#16131f',
              backgroundImage: 'none',
              border:          '1px solid rgba(168,85,247,0.35)',
              boxShadow:       '0 0 0 1px rgba(0,0,0,0.8), 0 16px 40px rgba(0,0,0,0.85)',
            }}
          >
            {ai.loading ? (
              <div style={{ color: '#64748b', fontSize: '12px', textAlign: 'center' }}>
                Analyzing…
              </div>
            ) : (
              <>
                {/* Verdict header */}
                {ai.verdict && (
                  <div className="flex items-center gap-2 mb-2">
                    <span
                      className="rounded px-2 py-0.5 text-xs font-bold"
                      style={{ background: AI_VERDICT_STYLE[ai.verdict].bg, color: AI_VERDICT_STYLE[ai.verdict].color }}
                    >
                      {AI_VERDICT_STYLE[ai.verdict].dot} {ai.verdict}
                    </span>
                    <span style={{ color: '#64748b', fontSize: '10px' }}>
                      {symbol} · {side}
                    </span>
                  </div>
                )}
                {/* Reason */}
                <div style={{ color: '#e2e8f0', fontSize: '12px', lineHeight: '1.5' }}>
                  {ai.reason}
                </div>
                {/* Actions */}
                <div className="flex items-center justify-between mt-2.5" style={{ borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '8px' }}>
                  <button
                    onClick={(e) => { e.stopPropagation(); fetchAdvisory() }}
                    style={{ color: '#64748b', fontSize: '11px', background: 'none', border: 'none', cursor: 'pointer' }}
                  >
                    ↺ refresh
                  </button>
                  <button
                    onClick={handleClose}
                    style={{ color: '#64748b', fontSize: '11px', background: 'none', border: 'none', cursor: 'pointer' }}
                  >
                    close
                  </button>
                </div>
              </>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function HeaderRow() {
  return (
    <tr style={{ borderBottom: '1px solid var(--border)' }}>
      {COLS.map((col) => (
        <th
          key={col.label}
          className={`px-3 py-3 text-xs font-semibold uppercase tracking-widest text-${col.align}`}
          style={{ color: 'var(--text-dim)', whiteSpace: 'nowrap' }}
        >
          {col.label}
        </th>
      ))}
    </tr>
  )
}

function Dash() {
  return <span style={{ color: 'var(--text-dim)' }}>—</span>
}

// ── Futures symbol cell: underline on hover, click opens levels panel ────────
function FuturesSymbolCell({ symbol, onFuturesClick }: { symbol: string; onFuturesClick: () => void }) {
  const [hovered, setHovered] = useState(false)
  return (
    <td className="px-3 py-2.5">
      <span
        className="font-bold tracking-wider"
        style={{
          color:         'var(--text-primary)',
          fontSize:      '13px',
          cursor:        'pointer',
          borderBottom:  hovered ? '1px solid #60a5fa' : '1px solid transparent',
          paddingBottom: '1px',
          transition:    'border-color 0.15s',
        }}
        onClick={onFuturesClick}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        {symbol}
      </span>
    </td>
  )
}

// ── ETF symbol cell: tooltip on hover, click opens detail panel ─────────────
function ETFSymbolCell({
  symbol,
  onEtfClick,
  ytd,
}: {
  symbol: string
  onEtfClick: () => void
  ytd?: number
}) {
  const [hovered, setHovered] = useState(false)
  const meta = ETF_META[symbol]
  const ytdColor = ytd == null ? 'var(--text-dim)' : ytd >= 0 ? '#4ade80' : '#f87171'

  return (
    <td className="px-3 py-2.5">
      <div className="relative inline-block">
        <div className="flex items-baseline gap-1.5">
          <span
            className="font-bold tracking-wider"
            style={{
              color:          'var(--text-primary)',
              fontSize:       '13px',
              cursor:         'pointer',
              borderBottom:   hovered ? '1px solid var(--accent-blue)' : '1px solid transparent',
              paddingBottom:  '1px',
              transition:     'border-color 0.15s',
            }}
            onClick={onEtfClick}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
          >
            {symbol}
          </span>
          {ytd != null && (
            <span
              className="tabular-nums"
              style={{ fontSize: '10px', fontWeight: 600, color: ytdColor, lineHeight: 1 }}
            >
              {ytd >= 0 ? '+' : ''}{ytd.toFixed(1)}%
            </span>
          )}
        </div>

        {/* Tooltip */}
        {hovered && meta && (
          <div
            style={{
              position:     'absolute',
              bottom:       'calc(100% + 6px)',
              left:         '0',
              padding:      '6px 10px',
              borderRadius: '6px',
              background:   'var(--bg-panel)',
              border:       '1px solid var(--border)',
              color:        'var(--text-muted)',
              fontSize:     '11px',
              whiteSpace:   'nowrap',
              pointerEvents:'none',
              zIndex:       50,
              boxShadow:    '0 4px 16px rgba(0,0,0,0.4)',
            }}
          >
            {meta.description}
          </div>
        )}
      </div>
    </td>
  )
}

// ── Stock symbol cell: clickable to open fundamentals/technicals panel ───────
function StockSymbolCell({ symbol, onStockClick }: { symbol: string; onStockClick: () => void }) {
  const [hovered, setHovered] = useState(false)
  return (
    <td className="px-3 py-2.5">
      <span
        className="font-bold tracking-wider"
        style={{
          color:         'var(--text-primary)',
          fontSize:      '13px',
          cursor:        'pointer',
          borderBottom:  hovered ? '1px solid #a855f7' : '1px solid transparent',
          paddingBottom: '1px',
          transition:    'border-color 0.15s',
        }}
        onClick={onStockClick}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        title="Click to view fundamentals & technicals"
      >
        {symbol}
      </span>
    </td>
  )
}

// ── Entry cell with h_high / h_low tooltip ──────────────────────────────────
function EntryCell({ sig }: { sig: Signal }) {
  const [hovered, setHovered] = useState(false)
  const isFutures = FUTURES_PANEL_TICKERS.has(sig.symbol)
  // Flat bar only meaningful for equities — futures trade 23h so flat bar = no 1-min data, not closed
  const flatBar   = sig.hour_high === sig.hour_low && !isFutures

  return (
    <div
      style={{ position: 'relative', display: 'inline-block', cursor: 'default' }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <span style={{ color: flatBar ? 'var(--text-dim)' : 'var(--text-primary)', fontSize: '13px' }}>
        {fmt(sig.entry)}
      </span>
      {flatBar && (
        <span
          title="No live candle — market closed or no data for this hour"
          style={{
            marginLeft:   4,
            fontSize:     9,
            fontWeight:   700,
            letterSpacing:'0.05em',
            color:        '#94a3b8',
            background:   'rgba(100,116,139,0.12)',
            borderRadius: 3,
            padding:      '1px 4px',
            verticalAlign:'middle',
          }}
        >
          CLOSED
        </span>
      )}

      {hovered && isFutures && (
        <div
          style={{
            position:    'absolute',
            bottom:      'calc(100% + 6px)',
            right:       0,
            padding:     '6px 10px',
            borderRadius:'6px',
            background:  'var(--bg-panel)',
            border:      '1px solid var(--border)',
            color:       'var(--text-muted)',
            fontSize:    '11px',
            whiteSpace:  'nowrap',
            pointerEvents:'none',
            zIndex:      50,
            boxShadow:   '0 4px 16px rgba(0,0,0,0.4)',
          }}
        >
          <div style={{ marginBottom: 2 }}>
            <span style={{ color: 'var(--text-dim)' }}>Hour H: </span>
            <span style={{ color: '#f87171', fontWeight: 600 }}>{fmt(sig.hour_high)}</span>
          </div>
          <div style={{ marginBottom: 2 }}>
            <span style={{ color: 'var(--text-dim)' }}>Hour L: </span>
            <span style={{ color: '#4ade80', fontWeight: 600 }}>{fmt(sig.hour_low)}</span>
          </div>
          <div style={{ marginTop: 4, paddingTop: 4, borderTop: '1px solid var(--border)' }}>
            <span style={{ color: 'var(--text-dim)' }}>L1: </span>
            <span>{sig.l1.toFixed(2)}</span>
            <span style={{ color: 'var(--text-dim)', marginLeft: 8 }}>σ: </span>
            <span>{(sig.l2 - sig.l1).toFixed(2)}</span>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Active signal row ────────────────────────────────────────────────────────
interface ActiveRowProps {
  sig: Signal
  rank: number
  onEtfClick:     (info: EtfPanelInfo) => void
  onFuturesClick: (info: FuturesPanelInfo) => void
  onStockClick:   (info: StockPanelInfo) => void
  ytdMap?: Record<string, number>
}

function ActiveRow({ sig, rank, onEtfClick, onFuturesClick, onStockClick, ytdMap }: ActiveRowProps) {
  const isSector  = SECTOR_TICKERS.has(sig.symbol)
  const isFutures = FUTURES_PANEL_TICKERS.has(sig.symbol)
  const isStock   = !isSector && !isFutures
  const majorColor = MAJOR_MARKET_COLORS[sig.symbol]
  // Futures trade ~23h — a flat bar just means no 1-min data, not that the market is closed.
  // Only show the CLOSED badge for equities/ETFs where flat bar = genuinely no session.
  const flatBar   = sig.hour_high === sig.hour_low && !isFutures

  // Use Schwab net_change (matches TOS) — falls back to last-prev_close if unavailable
  const change    = sig.net_change != null ? sig.net_change
                  : sig.prev_close > 0 ? sig.last - sig.prev_close : 0
  const ref       = sig.last - change   // reference price (settlement or prev close)
  const changePct = ref > 0 ? (change / ref) * 100 : 0
  const isUp      = change >= 0
  const changeColor = isUp ? '#4ade80' : '#f87171'

  return (
    <tr
      className="transition-colors"
      style={{
        borderBottom: '1px solid var(--border)',
        opacity: flatBar ? 0.45 : 1,
        ...(majorColor && {
          background: majorColor.bg,
          borderLeft: `3px solid ${majorColor.border}`,
        }),
      }}
      onMouseEnter={(e) => { e.currentTarget.style.background = majorColor ? majorColor.hover : 'var(--bg-row-hover)' }}
      onMouseLeave={(e) => { e.currentTarget.style.background = majorColor ? majorColor.bg : 'transparent' }}
    >
      {/* # */}
      <td className="px-3 py-2.5 tabular-nums" style={{ color: 'var(--text-dim)', fontSize: '12px' }}>
        {rank}
      </td>

      {/* SYMBOL */}
      {isSector ? (
        <ETFSymbolCell
          symbol={sig.symbol}
          onEtfClick={() => onEtfClick({ symbol: sig.symbol, last: sig.last, change, changePct })}
          ytd={ytdMap?.[sig.symbol]}
        />
      ) : isFutures ? (
        <FuturesSymbolCell
          symbol={sig.symbol}
          onFuturesClick={() => onFuturesClick({ symbol: sig.symbol, last: sig.last, change, changePct })}
        />
      ) : isStock ? (
        <StockSymbolCell
          symbol={sig.symbol}
          onStockClick={() => onStockClick({ symbol: sig.symbol, last: sig.last, change, changePct })}
        />
      ) : (
        <td className="px-3 py-2.5">
          <span className="font-bold tracking-wider" style={{ color: 'var(--text-primary)', fontSize: '13px' }}>
            {sig.symbol}
          </span>
        </td>
      )}

      {/* LAST */}
      <td className="px-3 py-2.5 text-right tabular-nums" style={{ color: 'var(--text-primary)', fontSize: '13px', fontWeight: 600 }}>
        {fmt(sig.last)}
      </td>

      {/* CHANGE from day open */}
      <td className="px-3 py-2.5 text-right tabular-nums" style={{ fontSize: '12px' }}>
        {ref > 0 ? (
          <span style={{ color: changeColor }}>
            {isUp ? '+' : ''}{change.toFixed(2)}{' '}
            <span style={{ opacity: 0.75 }}>({isUp ? '+' : ''}{changePct.toFixed(2)}%)</span>
          </span>
        ) : <Dash />}
      </td>

      {/* MODEL (AGG / CON / WIDE) */}
      <td className="px-3 py-2.5">
        <span
          className="inline-block rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wider"
          style={
            sig.model === 'AGG'  ? { background: 'var(--amber-bg)',  color: '#fbbf24' } :
            sig.model === 'CON'  ? { background: 'var(--indigo-bg)', color: '#a5b4fc' } :
            sig.model === 'WIDE' ? { background: 'rgba(20,184,166,0.12)', color: '#2dd4bf' } :
                                   { background: 'rgba(168,85,247,0.15)', color: '#c084fc' }  // CR = purple
          }
        >
          {sig.model}
        </span>
      </td>

      {/* AI — on-demand advisory (major market futures only) */}
      <td className="px-3 py-2.5 text-center">
        {majorColor ? (
          <AiAdvisoryButton
            symbol={sig.symbol.split(':')[0]}
            model={sig.model}
            side={sig.side}
          />
        ) : (
          <Dash />
        )}
      </td>

      {/* SIDE */}
      <td className="px-3 py-2.5">
        <span
          className="inline-block rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wider"
          style={sig.side === 'LONG'
            ? { background: 'var(--green-bg)', color: '#4ade80' }
            : { background: 'var(--red-bg)',   color: '#f87171' }}
        >
          {sig.side}
        </span>
      </td>

      {/* ALERT — NEAR or ENTRY state */}
      <td className="px-3 py-2.5">
        {sig.signal_state === 'ENTRY' ? (
          <span
            className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wider"
            style={{
              background: sig.side === 'LONG' ? 'rgba(74,222,128,0.18)' : 'rgba(248,113,113,0.18)',
              color: sig.side === 'LONG' ? '#4ade80' : '#f87171',
              animation: 'pulse 1.5s infinite',
            }}
          >
            <span style={{ fontSize: 8 }}>●</span> ENTRY
          </span>
        ) : sig.signal_state === 'NEAR' || sig.near_gray ? (
          <span
            className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wider"
            style={{ background: 'rgba(251,191,36,0.15)', color: '#fbbf24', animation: 'pulse 2s infinite' }}
          >
            <span style={{ fontSize: 8 }}>●</span> NEAR
          </span>
        ) : sig.signal_state === 'NEUTRAL' ? (
          <span
            className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-semibold uppercase tracking-wider"
            style={{ background: 'rgba(100,116,139,0.12)', color: '#64748b' }}
          >
            NEUTRAL
          </span>
        ) : (
          <Dash />
        )}
      </td>

      {/* ENTRY — tooltip shows h_high / h_low */}
      <td className="px-3 py-2.5 text-right tabular-nums" style={{ position: 'relative' }}>
        <EntryCell sig={sig} />
      </td>

      {/* STOP */}
      <td className="px-3 py-2.5 text-right tabular-nums" style={{ color: '#f87171', fontSize: '13px' }}>
        {fmt(sig.stop)}
      </td>

      {/* TARGET (gray T2) */}
      <td className="px-3 py-2.5 text-right tabular-nums" style={{ color: '#4ade80', fontSize: '13px' }}>
        {fmt(sig.target)}
      </td>

      {/* DAILY SWING */}
      <td className="px-3 py-2.5">
        <SwingBar
          swingPct={sig.swing_pct}
          currentRange={sig.current_range}
          typicalRange={sig.typical_range}
        />
      </td>

    </tr>
  )
}

// ── Silent (no-signal) row ───────────────────────────────────────────────────
interface NoSignalRowProps {
  sym: SymbolInfo
  rank: number
  onEtfClick:     (info: EtfPanelInfo) => void
  onFuturesClick: (info: FuturesPanelInfo) => void
  onStockClick:   (info: StockPanelInfo) => void
  ytdMap?: Record<string, number>
}

function NoSignalRow({ sym, rank, onEtfClick, onFuturesClick, onStockClick, ytdMap }: NoSignalRowProps) {
  const isSector  = SECTOR_TICKERS.has(sym.ticker)
  const isFutures = FUTURES_PANEL_TICKERS.has(sym.ticker)
  const isStock   = !isSector && !isFutures
  const last      = sym.last_price ?? null
  // Use Schwab net_change (matches TOS) — falls back to last-prev_close if unavailable
  const change    = sym.net_change != null ? sym.net_change
                  : (last && sym.prev_close && sym.prev_close > 0 ? last - sym.prev_close : null)
  const ref       = last && change != null ? last - change : null
  const changePct = change !== null && ref ? (change / ref) * 100 : null
  const isUp      = change !== null ? change >= 0 : true
  const changeColor = change !== null ? (isUp ? '#4ade80' : '#f87171') : 'var(--text-dim)'

  // Equities may end up in NoSignalRow for two reasons:
  //  1. Genuinely off-hours (pre/post market) → show CLOSED chip at full brightness
  //  2. Stats not yet computed (new symbol / first boot) → during RTH show dim row, no CLOSED chip
  // Detect RTH so we don't falsely label stats-less symbols as CLOSED during market hours.
  const _nowET     = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }))
  const _etMinutes = _nowET.getHours() * 60 + _nowET.getMinutes()
  const _isWeekday = _nowET.getDay() >= 1 && _nowET.getDay() <= 5
  const _isRTH     = _isWeekday && _etMinutes >= 570 && _etMinutes < 960  // 9:30–16:00 ET
  const offHoursEquity = isStock && !_isRTH

  return (
    <tr style={{ borderBottom: '1px solid var(--border)', opacity: offHoursEquity ? 1 : 0.38 }}>
      <td className="px-3 py-2" style={{ color: 'var(--text-dim)', fontSize: '12px' }}>{rank}</td>

      {/* SYMBOL */}
      {isSector ? (
        <ETFSymbolCell
          symbol={sym.ticker}
          onEtfClick={() =>
            onEtfClick({
              symbol:    sym.ticker,
              last:      last ?? 0,
              change:    change ?? 0,
              changePct: changePct ?? 0,
            })
          }
          ytd={ytdMap?.[sym.ticker]}
        />
      ) : isFutures ? (
        <FuturesSymbolCell
          symbol={sym.ticker}
          onFuturesClick={() => onFuturesClick({ symbol: sym.ticker, last: last ?? 0, change: change ?? 0, changePct: changePct ?? 0 })}
        />
      ) : isStock ? (
        <StockSymbolCell
          symbol={sym.ticker}
          onStockClick={() => onStockClick({ symbol: sym.ticker, last: last ?? 0, change: change ?? 0, changePct: changePct ?? 0 })}
        />
      ) : (
        <td className="px-3 py-2">
          <span className="font-bold tracking-wider" style={{ color: 'var(--text-muted)', fontSize: '13px' }}>
            {sym.ticker}
          </span>
        </td>
      )}

      {/* LAST — full brightness for off-hours equities */}
      <td className="px-3 py-2 text-right tabular-nums"
          style={{ color: 'var(--text-primary)', fontSize: '13px', fontWeight: offHoursEquity ? 600 : 400 }}>
        {last != null ? fmt(last) : <Dash />}
      </td>

      {/* CHG — full color for off-hours equities */}
      <td className="px-3 py-2 text-right tabular-nums"
          style={{ fontSize: '12px', color: change !== null ? changeColor : 'var(--text-dim)' }}>
        {change !== null && changePct !== null ? (
          <>
            {isUp ? '+' : ''}{change.toFixed(2)}{' '}
            <span style={{ opacity: 0.75 }}>({isUp ? '+' : ''}{changePct.toFixed(2)}%)</span>
          </>
        ) : <Dash />}
      </td>

      {/* MODEL */}
      <td className="px-3 py-2 text-center"><Dash /></td>

      {/* AI */}
      <td className="px-3 py-2 text-center"><Dash /></td>

      {/* SIDE — grayed out */}
      <td className="px-3 py-2 text-center"><Dash /></td>

      {/* ALERT — subtle CLOSED chip for off-hours equities, dash otherwise */}
      <td className="px-3 py-2">
        {offHoursEquity ? (
          <span
            className="inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold uppercase tracking-wider"
            style={{
              background: 'rgba(100,116,139,0.08)',
              color:      'rgba(100,116,139,0.4)',
              border:     '1px solid rgba(100,116,139,0.12)',
            }}
          >
            CLOSED
          </span>
        ) : (
          <Dash />
        )}
      </td>

      {/* ENTRY */}
      <td className="px-3 py-2 text-center"><Dash /></td>

      {/* STOP */}
      <td className="px-3 py-2 text-center"><Dash /></td>

      {/* TARGET */}
      <td className="px-3 py-2 text-center"><Dash /></td>

      {/* DAILY SWING */}
      <td className="px-3 py-2 text-center"><Dash /></td>
    </tr>
  )
}

// ── Main export ──────────────────────────────────────────────────────────────
export default function SignalTable({ signals, allSymbols, loading, error, onRetry, ytdMap }: SignalTableProps) {
  const [etfPanel,     setEtfPanel]     = useState<EtfPanelInfo | null>(null)
  const [futuresPanel, setFuturesPanel] = useState<FuturesPanelInfo | null>(null)
  const [stockPanel,   setStockPanel]   = useState<StockPanelInfo | null>(null)

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div
          className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin"
          style={{ borderColor: 'var(--accent-blue)', borderTopColor: 'transparent' }}
        />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-4">
        <p style={{ color: '#f87171' }} className="text-sm">{error}</p>
        <button
          onClick={onRetry}
          className="rounded-lg px-4 py-2 text-xs font-semibold uppercase tracking-wider transition-opacity hover:opacity-80"
          style={{ background: 'var(--accent-blue)', color: '#fff' }}
        >
          Retry
        </button>
      </div>
    )
  }

  // Build ordered rows:
  //   1st tier — ENTRY  (most urgent)
  //   2nd tier — NEAR
  //   3rd tier — everything else (NEUTRAL / no state)
  // Within each tier: sectors sorted by YTD% desc; non-sectors keep backend swing_pct order.
  const STATE_RANK: Record<string, number> = { ENTRY: 0, NEAR: 1 }
  const sortedSignals = [...signals].sort((a, b) => {
    const aRank = STATE_RANK[a.signal_state ?? ''] ?? 2
    const bRank = STATE_RANK[b.signal_state ?? ''] ?? 2
    if (aRank !== bRank) return aRank - bRank

    // Same state tier — sectors sorted by YTD desc
    const aIsSector = SECTOR_TICKERS.has(a.symbol)
    const bIsSector = SECTOR_TICKERS.has(b.symbol)
    if (aIsSector && bIsSector) {
      const aYtd = ytdMap?.[a.symbol] ?? -Infinity
      const bYtd = ytdMap?.[b.symbol] ?? -Infinity
      return bYtd - aYtd
    }
    return 0
  })

  const activeSymbols = new Set(signals.map((s) => s.symbol))
  const silentSymbols = allSymbols
    .filter((s) => !activeSymbols.has(s.ticker))
    .sort((a, b) => {
      const aIsSector = SECTOR_TICKERS.has(a.ticker)
      const bIsSector = SECTOR_TICKERS.has(b.ticker)
      if (aIsSector && bIsSector) {
        const aYtd = ytdMap?.[a.ticker] ?? -Infinity
        const bYtd = ytdMap?.[b.ticker] ?? -Infinity
        return bYtd - aYtd
      }
      if (aIsSector) return -1   // sectors before non-sectors
      if (bIsSector) return 1
      return 0                   // preserve original order for non-sectors
    })

  let rank = 1

  return (
    <>
      {/* ETF detail panel */}
      {etfPanel && (
        <ETFPanel info={etfPanel} onClose={() => setEtfPanel(null)} />
      )}

      {/* Futures levels panel */}
      {futuresPanel && (
        <FuturesPanel info={futuresPanel} onClose={() => setFuturesPanel(null)} />
      )}

      {/* Stock fundamentals + technicals panel */}
      {stockPanel && (
        <StockInfoPanel
          info={stockPanel}
          onClose={() => setStockPanel(null)}
          onSectorClick={(etf) => {
            setStockPanel(null)
            const sym = allSymbols.find(s => s.ticker === etf)
            setEtfPanel({
              symbol:    etf,
              last:      sym?.last_price  ?? 0,
              change:    sym?.net_change  ?? 0,
              changePct: (sym?.net_change && sym?.last_price)
                ? (sym.net_change / (sym.last_price - sym.net_change)) * 100
                : 0,
            })
          }}
        />
      )}

      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <HeaderRow />
          </thead>
          <tbody>
            {sortedSignals.map((sig) => (
              <ActiveRow
                key={`${sig.symbol}-${sig.model}`}
                sig={sig}
                rank={rank++}
                onEtfClick={setEtfPanel}
                onFuturesClick={setFuturesPanel}
                onStockClick={setStockPanel}
                ytdMap={ytdMap}
              />
            ))}
            {silentSymbols.map((sym) => (
              <NoSignalRow
                key={sym.ticker}
                sym={sym}
                rank={rank++}
                onEtfClick={setEtfPanel}
                onFuturesClick={setFuturesPanel}
                onStockClick={setStockPanel}
                ytdMap={ytdMap}
              />
            ))}
            {signals.length === 0 && silentSymbols.length === 0 && (
              <tr>
                <td colSpan={COLS.length} className="py-16 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
                  No symbols loaded
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  )
}
