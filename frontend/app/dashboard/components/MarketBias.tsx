'use client'

export interface MarketBiasItem {
  symbol: string
  bias: 'BULL' | 'BEAR' | 'NEUTRAL'
  pts: number
  rth_open: number
  prev_close: number
}

interface MarketBiasProps {
  markets: MarketBiasItem[]
}

const BIAS_STYLE = {
  BULL:    { dot: '#4ade80', text: '#4ade80', bg: 'rgba(74,222,128,0.08)' },
  BEAR:    { dot: '#f87171', text: '#f87171', bg: 'rgba(248,113,113,0.08)' },
  NEUTRAL: { dot: '#64748b', text: '#94a3b8', bg: 'rgba(100,116,139,0.08)' },
}

export default function MarketBias({ markets }: MarketBiasProps) {
  if (!markets.length) return null

  return (
    <div
      className="flex items-center gap-3 px-5 py-2.5 shrink-0"
      style={{ borderBottom: '1px solid var(--border)' }}
    >
      <span
        className="text-xs font-semibold uppercase tracking-widest mr-1"
        style={{ color: 'var(--text-dim)' }}
      >
        Market
      </span>

      {markets.map((m) => {
        const s   = BIAS_STYLE[m.bias]
        const pos = m.pts >= 0
        return (
          <div
            key={m.symbol}
            className="flex items-center gap-2 rounded-lg px-3 py-1.5"
            style={{ background: s.bg }}
          >
            {/* Colored dot */}
            <span
              className="shrink-0 rounded-full"
              style={{ width: 8, height: 8, background: s.dot, boxShadow: `0 0 6px ${s.dot}` }}
            />

            {/* Symbol */}
            <span
              className="text-xs font-bold tracking-wider"
              style={{ color: 'var(--text-primary)' }}
            >
              {m.symbol}
            </span>

            {/* Points */}
            <span
              className="text-xs font-semibold tabular-nums"
              style={{ color: s.text }}
            >
              {m.bias === 'NEUTRAL'
                ? `${pos ? '+' : ''}${m.pts.toFixed(2)}`
                : `${pos ? '+' : ''}${m.pts.toFixed(2)}`}
            </span>
          </div>
        )
      })}
    </div>
  )
}
