'use client'

import { useEffect, useState, useCallback } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

interface Instrument {
  assetType: string
  symbol: string
  description?: string
  putCall?: string
  underlyingSymbol?: string
  netChange?: number
}

interface Position {
  longQuantity: number
  shortQuantity: number
  averagePrice: number
  averageLongPrice?: number
  averageShortPrice?: number
  marketValue: number
  currentDayProfitLoss: number
  currentDayProfitLossPercentage: number
  longOpenProfitLoss?: number
  shortOpenProfitLoss?: number
  maintenanceRequirement: number
  instrument: Instrument
}

interface Balance {
  equity: number
  liquidationValue: number
  buyingPower: number
  availableFunds: number
  maintenanceRequirement: number
  cashBalance: number
  longMarketValue: number
  shortMarketValue: number
}

interface Account {
  securitiesAccount: {
    accountNumber: string
    type: string
    isDayTrader: boolean
    positions: Position[]
    currentBalances: Balance
  }
}

function fmt(n: number, digits = 2) {
  return n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}
function fmtPnl(n: number) {
  const sign = n >= 0 ? '+' : ''
  return `${sign}$${fmt(Math.abs(n))}`
}
function pnlColor(n: number) {
  return n > 0 ? '#4ade80' : n < 0 ? '#f87171' : '#94a3b8'
}

export default function Positions() {
  const [accounts, setAccounts]   = useState<Account[]>([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState<string | null>(null)
  const [lastUpdate, setLastUpdate] = useState<string>('')

  const fetch_data = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/account/summary`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setAccounts(data.accounts ?? [])
      setLastUpdate(new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
      setError(null)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetch_data()
    const t = setInterval(fetch_data, 30_000)   // refresh every 30s
    return () => clearInterval(t)
  }, [fetch_data])

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '300px', color: 'var(--text-dim)' }}>
      Loading positions…
    </div>
  )

  if (error) return (
    <div style={{ padding: '24px', color: '#f87171' }}>Error: {error}</div>
  )

  return (
    <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '16px', maxWidth: '1100px', margin: '0 auto' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0, fontSize: '16px', fontWeight: 700, color: 'var(--text-primary)' }}>
          Positions
        </h2>
        <span style={{ fontSize: '11px', color: 'var(--text-dim)' }}>Updated {lastUpdate} · auto-refresh 30s</span>
      </div>

      {accounts.map((acc, ai) => {
        const sa  = acc.securitiesAccount
        const bal = sa.currentBalances
        const positions = sa.positions ?? []

        const equities = positions.filter(p => p.instrument.assetType === 'EQUITY' || p.instrument.assetType === 'COLLECTIVE_INVESTMENT')
        const options  = positions.filter(p => p.instrument.assetType === 'OPTION')

        const totalDayPnl  = positions.reduce((s, p) => s + (p.currentDayProfitLoss ?? 0), 0)
        const totalOpenPnl = positions.reduce((s, p) => s + (p.longOpenProfitLoss ?? 0) + (p.shortOpenProfitLoss ?? 0), 0)

        return (
          <div key={ai} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>

            {/* Account summary bar */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '8px' }}>
              {[
                { label: 'Equity',        value: `$${fmt(bal.equity)}` },
                { label: 'Buying Power',  value: `$${fmt(bal.buyingPower)}` },
                { label: 'Day P&L',       value: fmtPnl(totalDayPnl),  color: pnlColor(totalDayPnl) },
                { label: 'Open P&L',      value: fmtPnl(totalOpenPnl), color: pnlColor(totalOpenPnl) },
                { label: 'Cash',          value: `$${fmt(bal.cashBalance)}` },
              ].map((item, i) => (
                <div key={i} style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)',
                  borderRadius: '8px', padding: '10px 12px' }}>
                  <div style={{ fontSize: '10px', color: 'var(--text-dim)', textTransform: 'uppercase',
                    letterSpacing: '0.07em', marginBottom: '4px' }}>{item.label}</div>
                  <div style={{ fontSize: '15px', fontWeight: 700, fontFamily: 'monospace',
                    color: item.color ?? 'var(--text-primary)' }}>{item.value}</div>
                </div>
              ))}
            </div>

            {/* Equities & ETFs */}
            {equities.length > 0 && (
              <PositionTable title="Stocks & ETFs" positions={equities} />
            )}

            {/* Options */}
            {options.length > 0 && (
              <PositionTable title="Options" positions={options} isOptions />
            )}
          </div>
        )
      })}
    </div>
  )
}

function PositionTable({ title, positions, isOptions = false }: {
  title: string
  positions: Position[]
  isOptions?: boolean
}) {
  return (
    <div style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: '10px', overflow: 'hidden' }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)',
        fontSize: '11px', fontWeight: 700, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.07em' }}>
        {title}
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ fontSize: '10px', color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            <th style={thStyle('left')}>Symbol</th>
            {isOptions && <th style={thStyle('left')}>Description</th>}
            <th style={thStyle('center')}>Side</th>
            <th style={thStyle('right')}>Qty</th>
            <th style={thStyle('right')}>Avg Price</th>
            <th style={thStyle('right')}>Mkt Value</th>
            <th style={thStyle('right')}>Day P&L</th>
            <th style={thStyle('right')}>Open P&L</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p, i) => {
            const isLong     = p.longQuantity > 0
            const isShort    = p.shortQuantity > 0
            const qty        = isLong ? p.longQuantity : p.shortQuantity
            const avgPrice   = isLong ? (p.averageLongPrice ?? p.averagePrice) : (p.averageShortPrice ?? p.averagePrice)
            const openPnl    = (p.longOpenProfitLoss ?? 0) + (p.shortOpenProfitLoss ?? 0)
            const dayPnl     = p.currentDayProfitLoss
            const sym        = p.instrument.underlyingSymbol ?? p.instrument.symbol
            const desc       = p.instrument.description ?? ''
            const putCall    = p.instrument.putCall

            return (
              <tr key={i} style={{ borderTop: '1px solid rgba(255,255,255,0.04)',
                background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)' }}>
                <td style={tdStyle('left')}>
                  <span style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: '13px',
                    color: putCall === 'CALL' ? '#4ade80' : putCall === 'PUT' ? '#f87171' : 'var(--text-primary)' }}>
                    {sym}
                  </span>
                </td>
                {isOptions && (
                  <td style={{ ...tdStyle('left'), fontSize: '11px', color: 'var(--text-dim)', maxWidth: '200px' }}>
                    {desc}
                  </td>
                )}
                <td style={tdStyle('center')}>
                  <span style={{
                    fontSize: '10px', fontWeight: 700, padding: '2px 8px', borderRadius: '4px',
                    background: isLong ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)',
                    color: isLong ? '#4ade80' : '#f87171',
                  }}>
                    {isLong ? 'LONG' : 'SHORT'}
                  </span>
                </td>
                <td style={{ ...tdStyle('right'), fontFamily: 'monospace' }}>{qty}</td>
                <td style={{ ...tdStyle('right'), fontFamily: 'monospace' }}>${fmt(avgPrice)}</td>
                <td style={{ ...tdStyle('right'), fontFamily: 'monospace' }}>${fmt(Math.abs(p.marketValue))}</td>
                <td style={{ ...tdStyle('right'), fontFamily: 'monospace', color: pnlColor(dayPnl), fontWeight: 600 }}>
                  {fmtPnl(dayPnl)}
                </td>
                <td style={{ ...tdStyle('right'), fontFamily: 'monospace', color: pnlColor(openPnl), fontWeight: 600 }}>
                  {fmtPnl(openPnl)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function thStyle(align: 'left' | 'right' | 'center'): React.CSSProperties {
  return { padding: '8px 12px', textAlign: align, fontWeight: 600 }
}
function tdStyle(align: 'left' | 'right' | 'center'): React.CSSProperties {
  return { padding: '9px 12px', textAlign: align, fontSize: '13px', color: 'var(--text-primary)' }
}
