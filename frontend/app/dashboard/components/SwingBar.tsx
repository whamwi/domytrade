'use client'

interface SwingBarProps {
  swingPct: number
  currentRange: number
  typicalRange: number
}

export default function SwingBar({ swingPct, currentRange, typicalRange }: SwingBarProps) {
  const pct = Math.min(Math.max(swingPct, 0), 100)

  // Color based on percentage of typical range used
  const ratio = typicalRange > 0 ? currentRange / typicalRange : 0
  let barColor = '#3b82f6' // blue default
  if (ratio >= 0.8) barColor = '#dc2626'       // red — nearly exhausted
  else if (ratio >= 0.5) barColor = '#d97706'  // amber — mid
  else barColor = '#16a34a'                     // green — early

  return (
    <div className="flex flex-col gap-1 min-w-[120px]">
      {/* Bar */}
      <div
        className="relative w-full rounded-full overflow-hidden"
        style={{ height: '4px', background: 'var(--border)' }}
      >
        <div
          className="absolute left-0 top-0 h-full rounded-full transition-all duration-300"
          style={{ width: `${pct}%`, background: barColor }}
        />
      </div>
      {/* Label */}
      <span
        className="text-xs tabular-nums"
        style={{ color: 'var(--text-muted)', fontVariantNumeric: 'tabular-nums' }}
      >
        {swingPct.toFixed(1)}% vs{' '}
        <span style={{ color: 'var(--text-dim)' }}>{typicalRange.toFixed(2)} typ</span>
      </span>
    </div>
  )
}
