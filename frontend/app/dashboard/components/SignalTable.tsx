'use client'

import SwingBar from './SwingBar'

export interface Signal {
  symbol: string
  side: 'LONG' | 'SHORT'
  model: 'AGG' | 'CON'
  entry: number
  stop: number
  target: number
  last: number
  swing_pct: number
  current_range: number
  typical_range: number
  hour_high: number
  hour_low: number
}

interface SignalTableProps {
  signals: Signal[]
  loading: boolean
  error: string | null
  onRetry: () => void
}

function formatPrice(price: number, symbol: string): string {
  // Futures symbols start with /
  return price.toFixed(2)
}

export default function SignalTable({ signals, loading, error, onRetry }: SignalTableProps) {
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
        <p style={{ color: '#f87171' }} className="text-sm">
          {error}
        </p>
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

  if (signals.length === 0) {
    return (
      <div className="flex items-center justify-center py-24">
        <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
          No signals match current filters
        </p>
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            {[
              { label: '#', align: 'left' },
              { label: 'SYMBOL', align: 'left' },
              { label: 'SIDE', align: 'left' },
              { label: 'ENTRY', align: 'right' },
              { label: 'STOP', align: 'right' },
              { label: 'TARGET', align: 'right' },
              { label: 'MODEL', align: 'left' },
              { label: 'DAILY SWING', align: 'left' },
              { label: 'LAST', align: 'right' },
            ].map((col) => (
              <th
                key={col.label}
                className={`px-4 py-3 text-xs font-semibold uppercase tracking-widest text-${col.align}`}
                style={{ color: 'var(--text-dim)', whiteSpace: 'nowrap' }}
              >
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {signals.map((sig, idx) => {
            const isFutures = sig.symbol.startsWith('/')
            return (
              <tr
                key={`${sig.symbol}-${idx}`}
                className="transition-colors"
                style={{ borderBottom: '1px solid var(--border)' }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = 'var(--bg-row-hover)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = 'transparent'
                }}
              >
                {/* # */}
                <td
                  className="px-4 py-3 tabular-nums"
                  style={{ color: 'var(--text-dim)', fontSize: '12px' }}
                >
                  {idx + 1}
                </td>

                {/* SYMBOL */}
                <td className="px-4 py-3">
                  <span
                    className="font-bold tracking-wider"
                    style={{ color: 'var(--text-primary)', fontSize: '13px' }}
                  >
                    {sig.symbol}
                  </span>
                </td>

                {/* SIDE */}
                <td className="px-4 py-3">
                  <span
                    className="inline-block rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wider"
                    style={
                      sig.side === 'LONG'
                        ? { background: 'var(--green-bg)', color: '#4ade80' }
                        : { background: 'var(--red-bg)', color: '#f87171' }
                    }
                  >
                    {sig.side}
                  </span>
                </td>

                {/* ENTRY */}
                <td
                  className="px-4 py-3 text-right tabular-nums"
                  style={{ color: 'var(--text-primary)', fontSize: '13px' }}
                >
                  {!isFutures && ''}
                  {formatPrice(sig.entry, sig.symbol)}
                </td>

                {/* STOP */}
                <td
                  className="px-4 py-3 text-right tabular-nums"
                  style={{ color: '#f87171', fontSize: '13px' }}
                >
                  {formatPrice(sig.stop, sig.symbol)}
                </td>

                {/* TARGET */}
                <td
                  className="px-4 py-3 text-right tabular-nums"
                  style={{ color: 'var(--accent-blue)', fontSize: '13px' }}
                >
                  {formatPrice(sig.target, sig.symbol)}
                </td>

                {/* MODEL */}
                <td className="px-4 py-3">
                  <span
                    className="inline-block rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wider"
                    style={
                      sig.model === 'AGG'
                        ? { background: 'var(--amber-bg)', color: '#fbbf24' }
                        : { background: 'var(--indigo-bg)', color: '#a5b4fc' }
                    }
                  >
                    {sig.model}
                  </span>
                </td>

                {/* DAILY SWING */}
                <td className="px-4 py-3">
                  <SwingBar
                    swingPct={sig.swing_pct}
                    currentRange={sig.current_range}
                    typicalRange={sig.typical_range}
                  />
                </td>

                {/* LAST */}
                <td
                  className="px-4 py-3 text-right tabular-nums"
                  style={{ color: 'var(--text-muted)', fontSize: '13px' }}
                >
                  {formatPrice(sig.last, sig.symbol)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
