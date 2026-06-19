'use client'

import { useEffect, useState, useCallback } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

interface MagnetInfo {
  direction: 'UP' | 'DN'
  pct: number
  target: number
}

interface RegimeRow {
  symbol: string
  spot: number
  day_pct: number | null
  regime: string
  gamma_regime: 'POSITIVE' | 'NEGATIVE'
  flow: string
  pc_ratio: number | null
  max_gex: number | null
  call_wall: number | null
  put_wall: number | null
  zero_gamma: number | null
  magnet: MagnetInfo | null
  net_gex_mm: number
  captured_at: string | null
  iv_environment: string | null
  top_vol_call_strike: number | null
  top_vol_put_strike: number | null
}

interface RegimeResponse {
  rows: RegimeRow[]
  count: number
}

// ── Color helpers ────────────────────────────────────────────────────────────

function regimeStyle(regime: string): { bg: string; color: string } {
  if (regime.startsWith('Trend Down'))
    return { bg: 'rgba(239,68,68,0.15)', color: '#ef4444' }
  if (regime.startsWith('Trend Up'))
    return { bg: 'rgba(74,222,128,0.15)', color: '#4ade80' }
  if (regime === 'Pinned')
    return { bg: 'rgba(96,165,250,0.15)', color: '#60a5fa' }
  // Chop
  return { bg: 'rgba(148,163,184,0.12)', color: '#94a3b8' }
}

function flowStyle(flow: string): { bg: string; color: string } {
  switch (flow) {
    case 'BULL':  return { bg: 'rgba(74,222,128,0.15)', color: '#4ade80' }
    case 'BEAR':  return { bg: 'rgba(239,68,68,0.15)',  color: '#ef4444' }
    case 'MIXED': return { bg: 'rgba(251,191,36,0.15)', color: '#fbbf24' }
    case 'QUIET': return { bg: 'rgba(148,163,184,0.12)', color: '#94a3b8' }
    default:      return { bg: 'transparent', color: '#64748b' }
  }
}

function magnetStyle(dir: 'UP' | 'DN'): { color: string } {
  return dir === 'UP' ? { color: '#4ade80' } : { color: '#ef4444' }
}

function fmt(n: number | null | undefined, decimals = 2): string {
  if (n == null) return '—'
  return n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

function fmtGex(n: number): string {
  const abs = Math.abs(n)
  const sign = n >= 0 ? '+' : '-'
  if (abs >= 1000) return `${sign}${(abs / 1000).toFixed(1)}B`
  return `${sign}${abs.toFixed(0)}M`
}

function formatAge(iso: string | null): { label: string; color: string } {
  if (!iso) return { label: '—', color: 'var(--text-muted)' }
  const d    = new Date(iso)
  const diff = (Date.now() - d.getTime()) / 1000
  let label: string
  if (diff < 60)        label = `${Math.round(diff)}s ago`
  else if (diff < 3600) label = `${Math.round(diff / 60)}m ago`
  else if (diff < 4 * 3600) label = `${Math.round(diff / 3600)}h ago`
  else {
    // Old enough to show actual timestamp — "Thu 4:15 PM"
    label = d.toLocaleString('en-US', {
      weekday: 'short', hour: 'numeric', minute: '2-digit',
      hour12: true, timeZone: 'America/New_York',
    })
  }
  const color =
    diff < 5   * 60  ? '#4ade80'   // <5m  green
  : diff < 30  * 60  ? '#a3e635'   // <30m lime
  : diff < 90  * 60  ? '#fbbf24'   // <90m amber
  : diff < 6   * 3600 ? '#fb923c'  // <6h  orange
  : '#f87171'                       // ≥6h  red — last-trading-day data
  return { label, color }
}

// ── Component ────────────────────────────────────────────────────────────────

export default function MarketRegime() {
  const [data, setData] = useState<RegimeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState<string | null>(null)
  const [lastFetch, setLastFetch] = useState<number>(0)

  const fetch = useCallback(async () => {
    try {
      const res = await window.fetch(`${API_URL}/api/market-regime`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json)
      setLastFetch(Date.now())
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch()
    const id = setInterval(fetch, 60_000)   // refresh every minute
    return () => clearInterval(id)
  }, [fetch])

  const CELL: React.CSSProperties = {
    padding: '10px 14px',
    fontSize: '12px',
    borderBottom: '1px solid var(--border)',
    whiteSpace: 'nowrap',
  }
  const HDR: React.CSSProperties = {
    ...CELL,
    color: 'var(--text-muted)',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.07em',
    fontSize: '10px',
    background: 'var(--bg-panel)',
    borderBottom: '1px solid var(--border)',
    position: 'sticky',
    top: 0,
    zIndex: 1,
  }

  return (
    <div
      style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg-base)',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 20px',
          background: 'var(--bg-panel)',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontWeight: 700, fontSize: 14, color: 'var(--text-primary)', letterSpacing: '0.06em' }}>
            MARKET REGIME
          </span>
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            GEX-based regime classification
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {lastFetch > 0 && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Fetched {formatAge(new Date(lastFetch).toISOString()).label}
            </span>
          )}
          <button
            onClick={fetch}
            style={{
              background: 'var(--bg-row)',
              border: '1px solid var(--border)',
              color: 'var(--text-muted)',
              borderRadius: 6,
              padding: '4px 10px',
              fontSize: 11,
              cursor: 'pointer',
            }}
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {loading && (
          <div style={{ padding: 40, color: 'var(--text-muted)', textAlign: 'center', fontSize: 13 }}>
            Loading regime data…
          </div>
        )}
        {error && (
          <div style={{ padding: 20, color: '#ef4444', fontSize: 13 }}>
            Error: {error}
          </div>
        )}
        {data && data.rows.length > 0 && (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {['SYMBOL', 'SPOT', 'DAY %', 'REGIME', 'FLOW', 'CALL WALL', 'PUT WALL', 'TV CALL', 'TV PUT', 'ZERO-γ', 'MAGNET', 'NET GEX', 'IV ENV', 'UPDATED'].map(h => (
                  <th key={h} style={{ ...HDR, textAlign: h === 'SYMBOL' ? 'left' : 'right' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, i) => {
                const rs = regimeStyle(row.regime)
                const fs = flowStyle(row.flow)
                const isIndex = ['SPX', 'NDX', 'RUT'].includes(row.symbol)
                const rowBg = i % 2 === 0 ? 'var(--bg-row)' : 'transparent'
                return (
                  <tr key={row.symbol} style={{ background: rowBg }}>
                    {/* SYMBOL */}
                    <td style={{ ...CELL, textAlign: 'left' }}>
                      <span style={{
                        fontWeight: 700,
                        fontSize: isIndex ? 13 : 12,
                        color: isIndex ? 'var(--text-primary)' : 'var(--accent-blue)',
                        letterSpacing: '0.04em',
                      }}>
                        {row.symbol}
                      </span>
                    </td>

                    {/* SPOT */}
                    <td style={{ ...CELL, textAlign: 'right', color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>
                      {fmt(row.spot, row.spot > 100 ? 2 : 4)}
                    </td>

                    {/* DAY % */}
                    <td style={{ ...CELL, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                      {row.day_pct == null ? (
                        <span style={{ color: 'var(--text-muted)' }}>—</span>
                      ) : (
                        <span style={{ color: row.day_pct >= 0 ? '#4ade80' : '#ef4444', fontWeight: 600 }}>
                          {row.day_pct >= 0 ? '+' : ''}{row.day_pct.toFixed(2)}%
                        </span>
                      )}
                    </td>

                    {/* REGIME */}
                    <td style={{ ...CELL, textAlign: 'right' }}>
                      <span style={{
                        display: 'inline-block',
                        padding: '2px 8px',
                        borderRadius: 4,
                        fontSize: 11,
                        fontWeight: 600,
                        background: rs.bg,
                        color: rs.color,
                      }}>
                        {row.regime}
                      </span>
                    </td>

                    {/* FLOW */}
                    <td style={{ ...CELL, textAlign: 'right' }}>
                      <span style={{
                        display: 'inline-block',
                        padding: '2px 8px',
                        borderRadius: 4,
                        fontSize: 11,
                        fontWeight: 600,
                        background: fs.bg,
                        color: fs.color,
                      }}>
                        {row.flow}
                      </span>
                    </td>

                    {/* CALL WALL */}
                    <td style={{ ...CELL, textAlign: 'right', color: '#4ade80', fontVariantNumeric: 'tabular-nums' }}>
                      {fmt(row.call_wall, 0)}
                    </td>

                    {/* PUT WALL */}
                    <td style={{ ...CELL, textAlign: 'right', color: '#f87171', fontVariantNumeric: 'tabular-nums' }}>
                      {fmt(row.put_wall, 0)}
                    </td>

                    {/* TV CALL — top-volume call strike (crowd focus) */}
                    <td style={{ ...CELL, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                      {row.top_vol_call_strike != null ? (
                        <span style={{
                          color: '#4ade80',
                          fontWeight: 600,
                          background: 'rgba(74,222,128,0.08)',
                          padding: '1px 6px',
                          borderRadius: 3,
                        }}>
                          {fmt(row.top_vol_call_strike, 0)}
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-muted)' }}>—</span>
                      )}
                    </td>

                    {/* TV PUT — top-volume put strike (crowd focus) */}
                    <td style={{ ...CELL, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                      {row.top_vol_put_strike != null ? (
                        <span style={{
                          color: '#f87171',
                          fontWeight: 600,
                          background: 'rgba(248,113,113,0.08)',
                          padding: '1px 6px',
                          borderRadius: 3,
                        }}>
                          {fmt(row.top_vol_put_strike, 0)}
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-muted)' }}>—</span>
                      )}
                    </td>

                    {/* ZERO-γ */}
                    <td style={{ ...CELL, textAlign: 'right', color: '#fbbf24', fontVariantNumeric: 'tabular-nums' }}>
                      {fmt(row.zero_gamma, 0)}
                    </td>

                    {/* MAGNET */}
                    <td style={{ ...CELL, textAlign: 'right' }}>
                      {row.magnet ? (
                        <span style={{ ...magnetStyle(row.magnet.direction), fontWeight: 600, fontSize: 11 }}>
                          {row.magnet.direction} {row.magnet.pct.toFixed(2)}%
                          <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
                            {' '}@ {fmt(row.magnet.target, 0)}
                          </span>
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-muted)' }}>—</span>
                      )}
                    </td>

                    {/* NET GEX */}
                    <td style={{ ...CELL, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                      <span style={{
                        fontWeight: 600,
                        color: row.net_gex_mm >= 0 ? '#4ade80' : '#ef4444',
                      }}>
                        {fmtGex(row.net_gex_mm)}
                      </span>
                    </td>

                    {/* IV ENV */}
                    <td style={{ ...CELL, textAlign: 'right' }}>
                      <span style={{
                        fontSize: 11,
                        color: row.iv_environment === 'ELEVATED' ? '#fbbf24'
                             : row.iv_environment === 'HIGH'     ? '#ef4444'
                             : row.iv_environment === 'LOW'      ? '#60a5fa'
                             : 'var(--text-muted)',
                      }}>
                        {row.iv_environment ?? '—'}
                      </span>
                    </td>

                    {/* UPDATED */}
                    <td style={{ ...CELL, textAlign: 'right', fontSize: 11 }}>
                      {(() => {
                        const { label, color } = formatAge(row.captured_at)
                        return <span style={{ color }}>{label}</span>
                      })()}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
        {data && data.rows.length === 0 && (
          <div style={{ padding: 40, color: 'var(--text-muted)', textAlign: 'center', fontSize: 13 }}>
            No GEX data available. Check that the GEX baseline has run.
          </div>
        )}
      </div>

      {/* Legend footer */}
      <div
        style={{
          padding: '8px 20px',
          background: 'var(--bg-panel)',
          borderTop: '1px solid var(--border)',
          display: 'flex',
          gap: 24,
          flexShrink: 0,
          flexWrap: 'wrap',
        }}
      >
        {[
          { label: 'Trend Down', color: '#ef4444' },
          { label: 'Trend Up',   color: '#4ade80' },
          { label: 'Pinned',     color: '#60a5fa' },
          { label: 'Chop',       color: '#94a3b8' },
        ].map(item => (
          <span key={item.label} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--text-muted)' }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: item.color, display: 'inline-block' }} />
            {item.label}
          </span>
        ))}
        <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>
          MAGNET → price gravitates toward Zero-γ flip level · TV = top-volume strike (where traders are most active today)
        </span>
      </div>
    </div>
  )
}
