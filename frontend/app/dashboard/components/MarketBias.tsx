'use client'

export interface MarketBiasItem {
  symbol: string
  bias: 'BULL' | 'BEAR' | 'NEUTRAL'
  pts: number
  rth_open: number
  prev_close: number
  vwap?: number | null
  poc?: number | null
  gap?: number | null
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

            {/* Points change vs prev settlement */}
            <span
              className="text-xs font-semibold tabular-nums"
              style={{ color: s.text }}
            >
              {`${pos ? '+' : ''}${m.pts.toFixed(2)}`}
            </span>

            {/* Gap — open vs prev close, shown when nonzero */}
            {m.gap != null && Math.abs(m.gap) >= 0.25 && (
              <span
                className="text-xs tabular-nums"
                style={{
                  color      : m.gap >= 0 ? 'rgba(74,222,128,0.75)' : 'rgba(248,113,113,0.75)',
                  borderLeft : '1px solid rgba(255,255,255,0.1)',
                  paddingLeft: '6px',
                  marginLeft : '2px',
                  fontSize   : '10px',
                }}
                title="Overnight gap: today's open vs previous RTH close"
              >
                G{m.gap >= 0 ? '+' : ''}{m.gap.toFixed(2)}
              </span>
            )}

            {/* VWAP / POC — shown when available */}
            {(m.vwap || m.poc) && (
              <span
                className="text-xs tabular-nums"
                style={{ color: 'var(--text-dim)', borderLeft: '1px solid var(--border)', paddingLeft: '8px', marginLeft: '2px' }}
              >
                {m.vwap && <span>V <span style={{ color: 'var(--text-muted)' }}>{m.vwap.toFixed(2)}</span></span>}
                {m.vwap && m.poc && <span style={{ margin: '0 4px', opacity: 0.4 }}>·</span>}
                {m.poc  && <span>P <span style={{ color: 'var(--text-muted)' }}>{m.poc.toFixed(2)}</span></span>}
              </span>
            )}
          </div>
        )
      })}

    </div>
  )
}
