'use client'

export interface SectorItem {
  symbol: string
  name: string
  weight?: number
  pct: number
}

interface SectorStripProps {
  sectors: SectorItem[]
  label?: string
}

export default function SectorStrip({ sectors, label = 'Sectors' }: SectorStripProps) {
  if (!sectors.length) return null

  return (
    <div
      className="flex items-center gap-2 px-5 py-2 shrink-0 overflow-x-auto"
      style={{ borderBottom: '1px solid var(--border)' }}
    >
      <span
        className="text-xs font-semibold uppercase tracking-widest mr-1 shrink-0"
        style={{ color: 'var(--text-dim)' }}
      >
        {label}
      </span>

      {sectors.map((s) => {
        const pos   = s.pct >= 0
        const color = s.pct > 0 ? '#4ade80' : s.pct < 0 ? '#f87171' : '#64748b'
        const bg    = s.pct > 0
          ? 'rgba(74,222,128,0.07)'
          : s.pct < 0
          ? 'rgba(248,113,113,0.07)'
          : 'rgba(100,116,139,0.06)'

        return (
          <div
            key={s.symbol}
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1 shrink-0"
            style={{ background: bg }}
          >
            <span
              className="text-xs font-semibold tracking-wide"
              style={{ color: 'var(--text-muted)' }}
            >
              {s.name}
            </span>
            <span
              className="text-xs font-bold tabular-nums"
              style={{ color }}
            >
              {`${pos ? '+' : ''}${s.pct.toFixed(2)}%`}
            </span>
          </div>
        )
      })}
    </div>
  )
}
