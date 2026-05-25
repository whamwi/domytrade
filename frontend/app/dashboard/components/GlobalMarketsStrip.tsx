'use client'

import { useEffect, useState, useCallback } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''
const REFRESH_MS = 15 * 60 * 1000   // 15 min — matches backend cache TTL

const REGION_FLAG: Record<string, string> = {
  JP: '🇯🇵',
  HK: '🇭🇰',
  CN: '🇨🇳',
  AU: '🇦🇺',
}

interface AsiaItem {
  name:       string
  region:     string
  close:      number
  change_pct: number
}

interface FXItem {
  name:       string
  risk:       'on' | 'off'
  rate:       number
  change_pct: number
}

interface GlobalMarketsData {
  asia: AsiaItem[]
  fx:   FXItem[]
}

function PctChip({ pct, decimals = 2 }: { pct: number; decimals?: number }) {
  const color = pct > 0 ? '#4ade80' : pct < 0 ? '#f87171' : '#64748b'
  const bg    = pct > 0 ? 'rgba(74,222,128,0.07)' : pct < 0 ? 'rgba(248,113,113,0.07)' : 'rgba(100,116,139,0.06)'
  return (
    <span className="text-xs font-bold tabular-nums" style={{ color, background: bg, borderRadius: '4px', padding: '0 4px' }}>
      {pct >= 0 ? '+' : ''}{pct.toFixed(decimals)}%
    </span>
  )
}

export default function GlobalMarketsStrip() {
  const [data, setData] = useState<GlobalMarketsData | null>(null)

  const fetchData = useCallback(async () => {
    try {
      const res  = await fetch(`${API_URL}/api/global-markets`, { cache: 'no-store' })
      if (res.ok) setData(await res.json())
    } catch { /* keep stale */ }
  }, [])

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, REFRESH_MS)
    return () => clearInterval(id)
  }, [fetchData])

  if (!data || (!data.asia.length && !data.fx.length)) return null

  return (
    <div
      className="flex items-center gap-3 px-5 py-2 shrink-0 overflow-x-auto"
      style={{ borderBottom: '1px solid var(--border)' }}
    >
      {/* ── Asian Markets ─────────────────────────── */}
      {data.asia.length > 0 && (
        <>
          <span
            className="text-xs font-semibold uppercase tracking-widest mr-0.5 shrink-0"
            style={{ color: 'var(--text-dim)' }}
          >
            Asia
          </span>
          {data.asia.map(item => (
            <div
              key={item.name}
              className="flex items-center gap-1.5 rounded-lg px-2.5 py-1 shrink-0"
              style={{
                background: item.change_pct > 0
                  ? 'rgba(74,222,128,0.05)'
                  : item.change_pct < 0
                  ? 'rgba(248,113,113,0.05)'
                  : 'rgba(100,116,139,0.05)',
              }}
            >
              <span style={{ fontSize: '12px' }}>{REGION_FLAG[item.region] ?? ''}</span>
              <span className="text-xs font-medium shrink-0" style={{ color: 'var(--text-muted)' }}>
                {item.name}
              </span>
              <PctChip pct={item.change_pct} />
            </div>
          ))}
        </>
      )}

      {/* Divider */}
      {data.asia.length > 0 && data.fx.length > 0 && (
        <span style={{ color: 'var(--border)', fontSize: '14px' }}>|</span>
      )}

      {/* ── FX Risk-on / Risk-off ─────────────────── */}
      {data.fx.length > 0 && (
        <>
          <span
            className="text-xs font-semibold uppercase tracking-widest mr-0.5 shrink-0"
            style={{ color: 'var(--text-dim)' }}
          >
            FX
          </span>
          {data.fx.map(item => {
            // For USD/JPY (risk-off): UP = dollar stronger = risk-on signal; label as risk-off pair
            // For EUR/USD, GBP/USD (risk-on): UP = dollar weaker = risk-on signal
            const riskLabel = item.risk === 'off' ? 'safe' : 'risk'
            const riskColor = item.risk === 'off' ? '#94a3b8' : '#60a5fa'

            return (
              <div
                key={item.name}
                className="flex items-center gap-1.5 rounded-lg px-2.5 py-1 shrink-0"
                style={{
                  background: item.change_pct > 0
                    ? 'rgba(74,222,128,0.05)'
                    : item.change_pct < 0
                    ? 'rgba(248,113,113,0.05)'
                    : 'rgba(100,116,139,0.05)',
                }}
              >
                <span
                  className="text-xs font-semibold shrink-0"
                  style={{ color: 'var(--text-muted)' }}
                >
                  {item.name}
                </span>
                <span className="text-xs tabular-nums shrink-0" style={{ color: 'var(--text-dim)' }}>
                  {item.rate.toFixed(item.name.includes('JPY') ? 2 : 4)}
                </span>
                <PctChip pct={item.change_pct} decimals={2} />
                <span
                  className="text-xs uppercase shrink-0"
                  style={{ color: riskColor, fontSize: '9px', letterSpacing: '0.05em' }}
                >
                  {riskLabel}
                </span>
              </div>
            )
          })}
        </>
      )}
    </div>
  )
}
