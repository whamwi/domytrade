'use client'

import SwingBar from './SwingBar'

export interface Signal {
  symbol: string
  api_symbol: string
  side: 'LONG' | 'SHORT'
  model: 'AGG' | 'CON'
  entry: number
  stop: number
  target: number
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
}

function fmt(price: number): string {
  if (price >= 1000) return price.toFixed(2)
  if (price >= 100)  return price.toFixed(2)
  if (price >= 10)   return price.toFixed(3)
  return price.toFixed(4)
}

const COLS = [
  { label: '#',           align: 'left'  },
  { label: 'SYMBOL',      align: 'left'  },
  { label: 'LAST',        align: 'right' },
  { label: 'CHG',         align: 'right' },
  { label: 'SIDE',        align: 'left'  },
  { label: 'ENTRY',       align: 'right' },
  { label: 'STOP',        align: 'right' },
  { label: 'TARGET',      align: 'right' },
  { label: 'SENS',        align: 'left'  },
  { label: 'MODEL',       align: 'left'  },
  { label: 'DAILY SWING', align: 'left'  },
  { label: 'STAGE',       align: 'left'  },
  { label: 'DAYS',        align: 'right' },
  { label: 'SIGNAL',      align: 'left'  },
  { label: 'WIN%',        align: 'right' },
  { label: 'EV',          align: 'right' },
  { label: 'SCORE',       align: 'left'  },
]

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

interface ActiveRowProps {
  sig: Signal
  rank: number
}

function ActiveRow({ sig, rank }: ActiveRowProps) {
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
      style={{ borderBottom: '1px solid var(--border)' }}
      onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-row-hover)' }}
      onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
    >
      {/* # */}
      <td className="px-3 py-2.5 tabular-nums" style={{ color: 'var(--text-dim)', fontSize: '12px' }}>
        {rank}
      </td>

      {/* SYMBOL */}
      <td className="px-3 py-2.5">
        <span className="font-bold tracking-wider" style={{ color: 'var(--text-primary)', fontSize: '13px' }}>
          {sig.symbol}
        </span>
      </td>

      {/* LAST */}
      <td className="px-3 py-2.5 text-right tabular-nums" style={{ color: 'var(--text-primary)', fontSize: '13px', fontWeight: 600 }}>
        {fmt(sig.last)}
      </td>

      {/* CHANGE from day open */}
      <td className="px-3 py-2.5 text-right tabular-nums" style={{ fontSize: '12px' }}>
        {sig.prev_close > 0 ? (
          <span style={{ color: changeColor }}>
            {isUp ? '+' : ''}{change.toFixed(2)}{' '}
            <span style={{ opacity: 0.75 }}>({isUp ? '+' : ''}{changePct.toFixed(2)}%)</span>
          </span>
        ) : <Dash />}
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

      {/* ENTRY */}
      <td className="px-3 py-2.5 text-right tabular-nums" style={{ color: 'var(--text-primary)', fontSize: '13px' }}>
        {fmt(sig.entry)}
      </td>

      {/* STOP */}
      <td className="px-3 py-2.5 text-right tabular-nums" style={{ color: '#f87171', fontSize: '13px' }}>
        {fmt(sig.stop)}
      </td>

      {/* TARGET */}
      <td className="px-3 py-2.5 text-right tabular-nums" style={{ color: '#4ade80', fontSize: '13px' }}>
        {fmt(sig.target)}
      </td>

      {/* SENS (AGG / CON) */}
      <td className="px-3 py-2.5">
        <span
          className="inline-block rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wider"
          style={sig.model === 'AGG'
            ? { background: 'var(--amber-bg)', color: '#fbbf24' }
            : { background: 'var(--indigo-bg)', color: '#a5b4fc' }}
        >
          {sig.model}
        </span>
      </td>

      {/* MODEL (timeframe — always HOURLY for now) */}
      <td className="px-3 py-2.5">
        <span
          className="inline-block rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wider"
          style={{ background: '#1e293b', color: '#94a3b8' }}
        >
          HOURLY
        </span>
      </td>

      {/* DAILY SWING */}
      <td className="px-3 py-2.5">
        <SwingBar
          swingPct={sig.swing_pct}
          currentRange={sig.current_range}
          typicalRange={sig.typical_range}
        />
      </td>

      {/* STAGE — Phase 2 */}
      <td className="px-3 py-2.5 text-center"><Dash /></td>

      {/* DAYS — Phase 2 */}
      <td className="px-3 py-2.5 text-right"><Dash /></td>

      {/* SIGNAL type — Phase 2 */}
      <td className="px-3 py-2.5"><Dash /></td>

      {/* WIN% — Phase 2 */}
      <td className="px-3 py-2.5 text-right"><Dash /></td>

      {/* EV — Phase 2 */}
      <td className="px-3 py-2.5 text-right"><Dash /></td>

      {/* SCORE — Phase 2 */}
      <td className="px-3 py-2.5"><Dash /></td>
    </tr>
  )
}

interface NoSignalRowProps {
  sym: SymbolInfo
  rank: number
}

function NoSignalRow({ sym, rank }: NoSignalRowProps) {
  const last      = sym.last_price ?? null
  // Use Schwab net_change (matches TOS) — falls back to last-prev_close if unavailable
  const change    = sym.net_change != null ? sym.net_change
                  : (last && sym.prev_close && sym.prev_close > 0 ? last - sym.prev_close : null)
  const ref       = last && change != null ? last - change : null
  const changePct = change !== null && ref ? (change / ref) * 100 : null
  const isUp      = change !== null ? change >= 0 : true

  return (
    <tr style={{ borderBottom: '1px solid var(--border)', opacity: 0.38 }}>
      <td className="px-3 py-2" style={{ color: 'var(--text-dim)', fontSize: '12px' }}>{rank}</td>
      {/* SYMBOL */}
      <td className="px-3 py-2">
        <span className="font-bold tracking-wider" style={{ color: 'var(--text-muted)', fontSize: '13px' }}>
          {sym.ticker}
        </span>
      </td>
      {/* LAST */}
      <td className="px-3 py-2 text-right tabular-nums" style={{ color: 'var(--text-primary)', fontSize: '13px' }}>
        {last != null ? last.toFixed(2) : <Dash />}
      </td>
      {/* CHG */}
      <td className="px-3 py-2 text-right tabular-nums" style={{ fontSize: '12px', color: change !== null ? (isUp ? '#4ade80' : '#f87171') : 'var(--text-dim)' }}>
        {change !== null && changePct !== null ? (
          <>
            {isUp ? '+' : ''}{change.toFixed(2)}{' '}
            <span style={{ opacity: 0.75 }}>({isUp ? '+' : ''}{changePct.toFixed(2)}%)</span>
          </>
        ) : <Dash />}
      </td>
      {/* Fill remaining 13 columns with dashes */}
      {Array.from({ length: 13 }).map((_, i) => (
        <td key={i} className="px-3 py-2 text-center">
          <Dash />
        </td>
      ))}
    </tr>
  )
}

export default function SignalTable({ signals, allSymbols, loading, error, onRetry }: SignalTableProps) {
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

  // Build ordered rows: active signals first (sorted by rank), then silent symbols
  const activeSymbols = new Set(signals.map((s) => s.symbol))
  const silentSymbols = allSymbols.filter((s) => !activeSymbols.has(s.ticker))

  let rank = 1

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <HeaderRow />
        </thead>
        <tbody>
          {signals.map((sig) => (
            <ActiveRow key={`${sig.symbol}-${sig.model}`} sig={sig} rank={rank++} />
          ))}
          {silentSymbols.map((sym) => (
            <NoSignalRow key={sym.ticker} sym={sym} rank={rank++} />
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
  )
}
