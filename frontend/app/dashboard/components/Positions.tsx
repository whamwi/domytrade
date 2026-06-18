'use client'

import { useEffect, useState, useCallback } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

// ─── types ────────────────────────────────────────────────────────────────────

interface Instrument {
  assetType: string
  symbol:    string
  putCall?:  string
  underlyingSymbol?: string
  netChange?: number
}

interface Position {
  longQuantity:                   number
  shortQuantity:                  number
  averagePrice:                   number
  averageLongPrice?:              number
  averageShortPrice?:             number
  marketValue:                    number
  currentDayProfitLoss:           number
  currentDayProfitLossPercentage: number
  longOpenProfitLoss?:            number
  shortOpenProfitLoss?:           number
  instrument:                     Instrument
}

interface Balance {
  equity:           number
  buyingPower:      number
  cashBalance:      number
  longMarketValue:  number
  shortMarketValue: number
}

interface Account {
  securitiesAccount: {
    accountNumber:   string
    type:            string
    positions:       Position[]
    currentBalances: Balance
  }
}

// ─── helpers ──────────────────────────────────────────────────────────────────

const usd = (n: number, d = 2) =>
  Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })

const pnlStr = (n: number) => `${n >= 0 ? '+' : '−'}$${usd(n)}`
const pnlCol = (n: number) => n > 0 ? '#4ade80' : n < 0 ? '#f87171' : '#64748b'

function curPx(p: Position): number {
  const isOpt = p.instrument.assetType === 'OPTION'
  const qty   = p.longQuantity > 0 ? p.longQuantity : p.shortQuantity
  return Math.abs(p.marketValue) / (qty * (isOpt ? 100 : 1))
}

function pctChg(p: Position): number {
  const avg = p.longQuantity > 0 ? (p.averageLongPrice ?? p.averagePrice) : (p.averageShortPrice ?? p.averagePrice)
  const cur = curPx(p)
  if (!avg) return 0
  const sign = p.shortQuantity > 0 ? -1 : 1
  return sign * ((cur - avg) / avg) * 100
}

function optLabel(sym: string): string {
  const m = sym.match(/^(\S+)\s+(\d{2})(\d{2})(\d{2})([CP])(\d+)$/)
  if (!m) return sym.trim()
  const [, ticker, , mm, dd, cp, strike] = m
  return `${ticker.trim()} $${(parseInt(strike) / 1000).toFixed(0)} ${cp === 'C' ? 'Call' : 'Put'} ${mm}/${dd}`
}


// ─── account summary tile ─────────────────────────────────────────────────────

function Tile({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ flex: 1, minWidth: 0, padding: '14px 18px', background: 'var(--bg-panel)', borderRight: '1px solid var(--border)' }}>
      <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.08em', color: 'var(--text-dim)', textTransform: 'uppercase', marginBottom: 5 }}>
        {label}
      </div>
      <div style={{ fontSize: 18, fontWeight: 700, fontFamily: 'monospace', color: color ?? 'var(--text-primary)', letterSpacing: '-0.01em' }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 3 }}>{sub}</div>}
    </div>
  )
}

// ─── position row ─────────────────────────────────────────────────────────────

function PosRow({ p, isOpt }: { p: Position; isOpt: boolean }) {
  const isLong  = p.longQuantity > 0
  const qty     = isLong ? p.longQuantity : p.shortQuantity
  const avg     = isLong ? (p.averageLongPrice ?? p.averagePrice) : (p.averageShortPrice ?? p.averagePrice)
  const cur     = curPx(p)
  const pct     = pctChg(p)
  const openPnl = (p.longOpenProfitLoss ?? 0) + (p.shortOpenProfitLoss ?? 0)
  const dayPnl  = p.currentDayProfitLoss
  const chg     = p.instrument.netChange ?? 0
  const isFut   = ['FUTURE','FUTURES'].includes(p.instrument.assetType)
  const sym     = isOpt ? optLabel(p.instrument.symbol)
                : isFut ? (p.instrument.symbol ?? p.instrument.underlyingSymbol ?? '')
                : (p.instrument.underlyingSymbol ?? p.instrument.symbol)
  const putCall = p.instrument.putCall
  const symColor = putCall === 'CALL' ? '#4ade80' : putCall === 'PUT' ? '#f87171' : 'var(--text-primary)'

  return (
    <tr style={{ borderTop: '1px solid rgba(255,255,255,0.05)' }}
      onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.03)')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>

      <td style={{ padding: '10px 16px', whiteSpace: 'nowrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ display: 'inline-block', width: 4, height: 28, borderRadius: 2, flexShrink: 0,
            background: isLong ? '#4ade80' : '#f87171' }} />
          <span style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 13, color: symColor }}>{sym}</span>
        </div>
      </td>

      <td style={{ padding: '10px 12px', textAlign: 'center' }}>
        <span style={{
          display: 'inline-block', fontSize: 9, fontWeight: 800, letterSpacing: '0.1em',
          padding: '3px 8px', borderRadius: 4,
          background: isLong ? 'rgba(74,222,128,0.1)' : 'rgba(248,113,113,0.1)',
          color: isLong ? '#4ade80' : '#f87171',
          border: `1px solid ${isLong ? 'rgba(74,222,128,0.2)' : 'rgba(248,113,113,0.2)'}`,
        }}>{isLong ? 'LONG' : 'SHORT'}</span>
      </td>

      <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: 'monospace', fontSize: 13, color: 'var(--text-dim)' }}>
        {qty}
      </td>

      <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: 'monospace', fontSize: 13, color: 'var(--text-primary)' }}>
        ${usd(avg)}
      </td>

      <td style={{ padding: '10px 16px', textAlign: 'right' }}>
        <div style={{ fontFamily: 'monospace', fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
          ${usd(cur)}
        </div>
        {chg !== 0 && (
          <div style={{ fontSize: 10, color: pnlCol(chg), marginTop: 1 }}>
            {chg > 0 ? '+' : ''}{usd(chg)} today
          </div>
        )}
      </td>

      <td style={{ padding: '10px 12px', textAlign: 'right', fontFamily: 'monospace', fontSize: 13 }}>
        ${usd(Math.abs(p.marketValue))}
      </td>

      <td style={{ padding: '10px 12px', textAlign: 'right' }}>
        <div style={{ fontFamily: 'monospace', fontSize: 13, fontWeight: 600, color: pnlCol(dayPnl) }}>
          {pnlStr(dayPnl)}
        </div>
      </td>

      <td style={{ padding: '10px 16px', textAlign: 'right' }}>
        <div style={{ fontFamily: 'monospace', fontSize: 13, fontWeight: 700, color: pnlCol(openPnl) }}>
          {pnlStr(openPnl)}
        </div>
        <div style={{ fontSize: 10, color: pnlCol(pct), marginTop: 1 }}>
          {pct > 0 ? '+' : ''}{pct.toFixed(2)}%
        </div>
      </td>
    </tr>
  )
}

// ─── section table ────────────────────────────────────────────────────────────

const TH = (align: 'left' | 'right' | 'center' = 'left'): React.CSSProperties => ({
  padding: '8px 12px', textAlign: align,
  fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
  color: 'var(--text-dim)', whiteSpace: 'nowrap',
  borderBottom: '1px solid var(--border)',
  background: 'rgba(255,255,255,0.02)',
})

function Section({ title, rows, isOpt = false }: { title: string; rows: Position[]; isOpt?: boolean }) {
  if (!rows.length) return null
  return (
    <div style={{ borderBottom: '1px solid var(--border)' }}>
      <div style={{
        padding: '6px 16px', fontSize: 9, fontWeight: 800, letterSpacing: '0.12em',
        textTransform: 'uppercase', color: 'var(--text-dim)',
        background: 'rgba(255,255,255,0.015)', borderBottom: '1px solid var(--border)',
      }}>
        {title} <span style={{ opacity: 0.5, fontWeight: 400 }}>({rows.length})</span>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={{ ...TH('left'),   width: '26%', paddingLeft: 16 }}>Symbol</th>
            <th style={{ ...TH('center'), width: '8%'  }}>Side</th>
            <th style={{ ...TH('right'),  width: '5%'  }}>Qty</th>
            <th style={{ ...TH('right'),  width: '10%' }}>Avg Price</th>
            <th style={{ ...TH('right'),  width: '12%', paddingRight: 16 }}>Current</th>
            <th style={{ ...TH('right'),  width: '12%' }}>Mkt Value</th>
            <th style={{ ...TH('right'),  width: '12%' }}>Day P&L</th>
            <th style={{ ...TH('right'),  width: '15%', paddingRight: 16 }}>Open P&L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p, i) => <PosRow key={i} p={p} isOpt={isOpt} />)}
        </tbody>
      </table>
    </div>
  )
}

// ─── main ─────────────────────────────────────────────────────────────────────

export default function Positions() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [updated,  setUpdated]  = useState('')

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/account/summary`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setAccounts(data.accounts ?? [])
      setUpdated(new Date().toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', second: '2-digit',
        timeZone: 'America/New_York',
      }) + ' ET')
      setError(null)
    } catch (e: any) { setError(e.message) }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 30_000)
    return () => clearInterval(t)
  }, [load])

  if (loading) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '50vh', gap: 10, color: 'var(--text-dim)', fontSize: 13 }}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
        strokeLinecap="round" strokeLinejoin="round" style={{ animation: 'spin 1s linear infinite' }}>
        <path d="M21 12a9 9 0 1 1-6.219-8.56" />
      </svg>
      Loading…
      <style>{`@keyframes spin { to { transform: rotate(360deg) } }`}</style>
    </div>
  )

  if (error) return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '50vh', color: '#f87171', fontSize: 13 }}>
      Error: {error}
    </div>
  )

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: '100vh',
      background: 'var(--bg-base)' }}>

      {/* ── Page header ── */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '12px 20px', borderBottom: '1px solid var(--border)',
        background: 'var(--bg-panel)', flexShrink: 0,
      }}>
        <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: '0.12em',
          textTransform: 'uppercase', color: 'var(--text-primary)' }}>
          Positions
        </span>
        <span style={{ fontSize: 10, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#4ade80',
            boxShadow: '0 0 5px #4ade8066', display: 'inline-block' }} />
          {updated} · 30s refresh
        </span>
      </div>



      {accounts.map((acc, ai) => {
        const sa       = acc.securitiesAccount
        const bal      = sa.currentBalances
        const all      = sa.positions ?? []
        const equities = all.filter(p => ['EQUITY','COLLECTIVE_INVESTMENT'].includes(p.instrument.assetType))
        const options  = all.filter(p => p.instrument.assetType === 'OPTION')
        const futures  = all.filter(p => ['FUTURE','FUTURES'].includes(p.instrument.assetType))
        const totalDay  = all.reduce((s, p) => s + p.currentDayProfitLoss, 0)
        const totalOpen = all.reduce((s, p) => s + (p.longOpenProfitLoss ?? 0) + (p.shortOpenProfitLoss ?? 0), 0)
        const exposure  = Math.abs(bal.longMarketValue) + Math.abs(bal.shortMarketValue)

        return (
          <div key={ai} style={{ display: 'flex', flexDirection: 'column', flex: 1 }}>
            {/* Account summary bar */}
            <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
              <Tile label="Equity"       value={`$${usd(bal.equity)}`}      sub={`Cash $${usd(bal.cashBalance)}`} />
              <Tile label="Buying Power" value={`$${usd(bal.buyingPower)}`} />
              <Tile label="Exposure"     value={`$${usd(exposure)}`}        sub={`L $${usd(bal.longMarketValue)} · S $${usd(Math.abs(bal.shortMarketValue))}`} />
              <Tile label="Day P&L"      value={pnlStr(totalDay)}           color={pnlCol(totalDay)} />
              <div style={{ flex: 1, minWidth: 0, padding: '14px 18px', background: 'var(--bg-panel)' }}>
                <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.08em', color: 'var(--text-dim)', textTransform: 'uppercase', marginBottom: 5 }}>Open P&L</div>
                <div style={{ fontSize: 18, fontWeight: 700, fontFamily: 'monospace', color: pnlCol(totalOpen) }}>{pnlStr(totalOpen)}</div>
                <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 3 }}>{all.length} position{all.length !== 1 ? 's' : ''}</div>
              </div>
            </div>

            {/* Position tables */}
            <div style={{ flex: 1, overflowY: 'auto' }}>
              <Section title="Futures"       rows={futures}  />
              <Section title="Stocks & ETFs" rows={equities} />
              <Section title="Options"       rows={options}  isOpt />
            </div>
          </div>
        )
      })}
    </div>
  )
}
