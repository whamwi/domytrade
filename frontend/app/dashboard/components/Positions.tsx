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

interface BotPosition {
  symbol:      string
  side:        'LONG' | 'SHORT'
  entry_price: number
  stop_price:  number
  quantity:    number
  order_id:    string | null
  entered_at:  string
}

interface BotSignal {
  symbol:       string
  signal_state: string
  side:         string
  last:         number
  model:        string
}

interface BotLog {
  ts:    string
  level: 'INFO' | 'TRADE' | 'ERROR'
  msg:   string
}

interface BotCfg {
  asset:    string
  model:    string
  stop_pts: number
  quantity: number
}

interface BotArmed {
  side:        'LONG' | 'SHORT'
  entry_level: number
}

interface BotStatus {
  enabled:        boolean
  account_number: string | null
  cfg:            BotCfg
  armed:          BotArmed | null
  live_price:     number   | null
  position:       BotPosition | null
  signal:         BotSignal   | null
  log:            BotLog[]
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

function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1)  return 'just now'
  if (m < 60) return `${m}m ago`
  return `${Math.floor(m / 60)}h ${m % 60}m ago`
}

// ─── shared input style ───────────────────────────────────────────────────────

const INPUT_STYLE: React.CSSProperties = {
  background: 'rgba(255,255,255,0.06)', border: '1px solid var(--border)',
  borderRadius: 6, color: 'var(--text-primary)', fontSize: 11,
  padding: '5px 10px', outline: 'none', width: '100%',
}

const SEG_BTN = (active: boolean): React.CSSProperties => ({
  fontSize: 10, fontWeight: 700, letterSpacing: '0.07em',
  padding: '5px 12px', borderRadius: 5, cursor: 'pointer',
  background:  active ? 'var(--accent-blue)'         : 'rgba(255,255,255,0.04)',
  border:      active ? '1px solid var(--accent-blue)' : '1px solid var(--border)',
  color:       active ? '#fff'                        : 'var(--text-dim)',
  transition:  'all 0.15s',
})

// ─── bot panel ────────────────────────────────────────────────────────────────

const ASSETS  = ['/MES', '/MNQ', '/M2K', '/MYM', '/MGC']
const MODELS  = ['CON', 'AGG', 'WIDE']

function BotPanel() {
  const [bot,  setBot]  = useState<BotStatus | null>(null)
  const [busy, setBusy] = useState(false)

  // local config form state (mirrors what will be sent on enable)
  const [asset,    setAsset]    = useState('/MES')
  const [model,    setModel]    = useState('CON')
  const [stopPts,  setStopPts]  = useState(10)
  const [quantity, setQuantity] = useState(1)
  const [cfgErr,   setCfgErr]   = useState<string | null>(null)

  const fetchBot = useCallback(async () => {
    try {
      const r = await fetch(`${API_URL}/api/bot/status`)
      if (r.ok) {
        const data: BotStatus = await r.json()
        setBot(data)
        // keep local form in sync with running config when bot is enabled
        if (data.enabled && data.cfg) {
          setAsset(data.cfg.asset)
          setModel(data.cfg.model)
          setStopPts(data.cfg.stop_pts)
          setQuantity(data.cfg.quantity)
        }
      }
    } catch {}
  }, [])

  useEffect(() => {
    fetchBot()
    const t = setInterval(fetchBot, 5_000)
    return () => clearInterval(t)
  }, [fetchBot])

  async function handleEnable() {
    if (busy) return
    setCfgErr(null)
    setBusy(true)
    try {
      const r = await fetch(`${API_URL}/api/bot/enable`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset, model, stop_pts: stopPts, quantity }),
      })
      const data = await r.json()
      if (data.error) { setCfgErr(data.error); return }
      setBot(data)
    } finally { setBusy(false) }
  }

  async function handleDisable() {
    if (busy) return
    setBusy(true)
    try {
      const r = await fetch(`${API_URL}/api/bot/disable`, { method: 'POST' })
      if (r.ok) setBot(await r.json())
    } finally { setBusy(false) }
  }

  async function emergencyClose() {
    if (busy || !bot?.position) return
    if (!confirm(`Close ${bot.position.symbol} position at market NOW?`)) return
    setBusy(true)
    try {
      const r = await fetch(`${API_URL}/api/bot/close`, { method: 'POST' })
      if (r.ok) setBot(await r.json())
    } finally { setBusy(false) }
  }

  const enabled  = bot?.enabled ?? false
  const sig      = bot?.signal
  const pos      = bot?.position
  const sigColor = sig?.signal_state === 'ENTRY' ? '#4ade80' : sig?.signal_state === 'NEAR' ? '#fbbf24' : '#64748b'
  const logColor = (l: string) => l === 'TRADE' ? '#60a5fa' : l === 'ERROR' ? '#f87171' : '#94a3b8'

  return (
    <div style={{ borderBottom: '1px solid var(--border)', background: enabled ? 'rgba(74,222,128,0.025)' : 'var(--bg-panel)' }}>

      {/* ── top bar ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '10px 20px', borderBottom: '1px solid var(--border)' }}>

        {/* status dot + label */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          <div style={{ width: 8, height: 8, borderRadius: '50%',
            background: enabled ? '#4ade80' : '#475569',
            boxShadow: enabled ? '0 0 6px #4ade8088' : 'none', transition: 'all 0.3s' }} />
          <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.12em',
            textTransform: 'uppercase', color: enabled ? '#4ade80' : 'var(--text-dim)' }}>
            Auto-Bot
          </span>
        </div>

        {/* config summary when enabled */}
        {enabled && bot?.cfg && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6,
            padding: '3px 10px', borderRadius: 6, background: 'rgba(255,255,255,0.04)',
            border: '1px solid var(--border)', flexShrink: 0 }}>
            <span style={{ fontSize: 10, fontFamily: 'monospace', fontWeight: 700, color: 'var(--text-primary)' }}>{bot.cfg.asset}</span>
            <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>·</span>
            <span style={{ fontSize: 10, fontWeight: 700, color: '#60a5fa' }}>{bot.cfg.model}</span>
            <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>·</span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{bot.cfg.quantity}ct</span>
            <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>·</span>
            <span style={{ fontSize: 10, color: '#f87171' }}>{bot.cfg.stop_pts}pt stop</span>
          </div>
        )}

        {/* ARMED badge — price trigger waiting */}
        {bot?.armed && !pos && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6,
            padding: '3px 12px', borderRadius: 6, flexShrink: 0,
            background: 'rgba(251,191,36,0.1)', border: '1px solid rgba(251,191,36,0.3)' }}>
            <span style={{ fontSize: 9, fontWeight: 800, color: '#fbbf24', letterSpacing: '0.1em' }}>ARMED</span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{bot.armed.side}</span>
            <span style={{ fontSize: 11, fontFamily: 'monospace', fontWeight: 700, color: '#fbbf24' }}>
              {bot.armed.entry_level.toFixed(2)}
            </span>
            {bot.live_price && (
              <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>
                live {bot.live_price.toFixed(2)}
              </span>
            )}
          </div>
        )}

        {/* live signal badge */}
        {sig && !bot?.armed && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6,
            padding: '3px 10px', borderRadius: 6,
            background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', flexShrink: 0 }}>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{sig.symbol?.split(':')[0]}</span>
            <span style={{ fontSize: 11, fontWeight: 700, fontFamily: 'monospace', color: sigColor }}>{sig.signal_state}</span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{sig.side}</span>
            <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-primary)' }}>{sig.last?.toFixed(2)}</span>
          </div>
        )}

        {/* active position pill */}
        {pos && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8,
            padding: '4px 12px', borderRadius: 6,
            background: pos.side === 'LONG' ? 'rgba(74,222,128,0.1)' : 'rgba(248,113,113,0.1)',
            border: `1px solid ${pos.side === 'LONG' ? 'rgba(74,222,128,0.25)' : 'rgba(248,113,113,0.25)'}`,
            flexShrink: 0 }}>
            <span style={{ fontSize: 10, fontWeight: 700, color: pos.side === 'LONG' ? '#4ade80' : '#f87171' }}>{pos.side}</span>
            <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-primary)' }}>{pos.symbol}</span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>@{pos.entry_price.toFixed(2)}</span>
            <span style={{ fontSize: 10, color: '#f87171' }}>stop {pos.stop_price.toFixed(2)}</span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{relTime(pos.entered_at)}</span>
          </div>
        )}

        <div style={{ flex: 1 }} />

        {/* emergency close */}
        {pos && (
          <button onClick={emergencyClose} disabled={busy} style={{
            fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
            padding: '5px 12px', borderRadius: 6, cursor: 'pointer',
            background: 'rgba(248,113,113,0.12)', border: '1px solid rgba(248,113,113,0.35)',
            color: '#f87171',
          }}>CLOSE NOW</button>
        )}

        {/* enable / disable */}
        {enabled ? (
          <button onClick={handleDisable} disabled={busy} style={{
            fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
            padding: '5px 14px', borderRadius: 6, cursor: 'pointer',
            background: 'rgba(74,222,128,0.12)', border: '1px solid rgba(74,222,128,0.35)',
            color: '#4ade80',
          }}>DISABLE</button>
        ) : null}
      </div>

      {/* ── config form (shown when DISABLED) ── */}
      {!enabled && (
        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 24, flexWrap: 'wrap' }}>

            {/* Asset */}
            <div>
              <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
                color: 'var(--text-dim)', marginBottom: 6 }}>Asset</div>
              <div style={{ display: 'flex', gap: 4 }}>
                {ASSETS.map(a => (
                  <button key={a} onClick={() => setAsset(a)} style={SEG_BTN(asset === a)}>{a}</button>
                ))}
              </div>
            </div>

            {/* Model */}
            <div>
              <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
                color: 'var(--text-dim)', marginBottom: 6 }}>Model</div>
              <div style={{ display: 'flex', gap: 4 }}>
                {MODELS.map(m => (
                  <button key={m} onClick={() => setModel(m)} style={SEG_BTN(model === m)}>{m}</button>
                ))}
              </div>
            </div>

            {/* Stop pts */}
            <div style={{ width: 80 }}>
              <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
                color: 'var(--text-dim)', marginBottom: 6 }}>Stop (pts)</div>
              <input
                type="number" min={1} max={50} step={1}
                value={stopPts}
                onChange={e => setStopPts(Number(e.target.value))}
                style={INPUT_STYLE}
              />
            </div>

            {/* Quantity */}
            <div style={{ width: 70 }}>
              <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
                color: 'var(--text-dim)', marginBottom: 6 }}>Contracts</div>
              <input
                type="number" min={1} max={10} step={1}
                value={quantity}
                onChange={e => setQuantity(Number(e.target.value))}
                style={INPUT_STYLE}
              />
            </div>

            {/* Enable button */}
            <div>
              <button onClick={handleEnable} disabled={busy} style={{
                fontSize: 11, fontWeight: 800, letterSpacing: '0.1em', textTransform: 'uppercase',
                padding: '6px 20px', borderRadius: 6, cursor: busy ? 'not-allowed' : 'pointer',
                background: 'rgba(74,222,128,0.15)', border: '1px solid rgba(74,222,128,0.4)',
                color: '#4ade80', opacity: busy ? 0.6 : 1,
              }}>
                {busy ? 'Starting…' : 'Enable Bot'}
              </button>
            </div>

          </div>
          {cfgErr && (
            <div style={{ marginTop: 8, fontSize: 10, color: '#f87171' }}>{cfgErr}</div>
          )}
        </div>
      )}

      {/* ── event log strip ── */}
      {bot && bot.log.length > 0 && (
        <div style={{ display: 'flex', gap: 16, padding: '6px 20px', overflowX: 'auto', alignItems: 'center' }}>
          {[...bot.log].reverse().slice(0, 6).map((entry, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0, whiteSpace: 'nowrap' }}>
              <span style={{ fontSize: 9, color: logColor(entry.level), fontWeight: 700 }}>{entry.level}</span>
              <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{entry.msg}</span>
              {i < 5 && <span style={{ color: 'var(--border)', marginLeft: 8 }}>·</span>}
            </div>
          ))}
        </div>
      )}

    </div>
  )
}

// ─── manual order panel ───────────────────────────────────────────────────────

interface ManualQuote { asset: string; symbol: string; last: number }
interface ManualResult { ok?: boolean; symbol?: string; side?: string; quantity?: number; stop_price?: number; live_price_at_order?: number; order_id?: string | null; error?: string }

function ManualOrderPanel() {
  const [asset,    setAsset]    = useState('/MYM')
  const [side,     setSide]     = useState<'BUY'|'SELL'>('SELL')
  const [quantity, setQuantity] = useState(1)
  const [stopPts,  setStopPts]  = useState(100)
  const [quote,    setQuote]    = useState<ManualQuote | null>(null)
  const [busy,     setBusy]     = useState(false)
  const [result,   setResult]   = useState<ManualResult | null>(null)

  const fetchQuote = useCallback(async (a: string) => {
    try {
      const r = await fetch(`${API_URL}/api/futures/quote?asset=${encodeURIComponent(a)}`)
      if (r.ok) setQuote(await r.json())
    } catch {}
  }, [])

  useEffect(() => {
    fetchQuote(asset)
    const t = setInterval(() => fetchQuote(asset), 5_000)
    return () => clearInterval(t)
  }, [asset, fetchQuote])

  const estStop = quote?.last
    ? (side === 'BUY' ? quote.last - stopPts : quote.last + stopPts)
    : null

  async function handlePlace() {
    if (busy) return
    const sym    = quote?.symbol ?? `${asset.replace('/', '')} front-month`
    const priceStr = quote?.last ? `@ ~${quote.last.toLocaleString()}` : '(price unknown)'
    const stopStr  = estStop ? ` | stop ${estStop.toLocaleString()}` : ''
    const msg = `Place order:\n\n${side} ${quantity}ct ${sym} MARKET ${priceStr}${stopStr}\n\nProceed?`
    if (!confirm(msg)) return
    setBusy(true)
    setResult(null)
    try {
      const r = await fetch(`${API_URL}/api/trade/manual`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ asset, side, quantity, stop_pts: stopPts }),
      })
      setResult(await r.json())
    } catch (e: unknown) {
      setResult({ error: String(e) })
    } finally { setBusy(false) }
  }

  const sideBtn = (s: 'BUY'|'SELL'): React.CSSProperties => ({
    fontSize: 10, fontWeight: 800, letterSpacing: '0.08em',
    padding: '5px 16px', borderRadius: 5, cursor: 'pointer',
    background: side === s
      ? (s === 'BUY' ? 'rgba(74,222,128,0.18)' : 'rgba(248,113,113,0.18)')
      : 'rgba(255,255,255,0.04)',
    border: side === s
      ? `1px solid ${s === 'BUY' ? 'rgba(74,222,128,0.5)' : 'rgba(248,113,113,0.5)'}`
      : '1px solid var(--border)',
    color: side === s ? (s === 'BUY' ? '#4ade80' : '#f87171') : 'var(--text-dim)',
  })

  return (
    <div style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 20, padding: '12px 20px', flexWrap: 'wrap' }}>

        {/* label */}
        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase',
          color: 'var(--text-dim)', alignSelf: 'center', flexShrink: 0 }}>
          Manual Order
        </div>

        {/* Asset */}
        <div>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
            color: 'var(--text-dim)', marginBottom: 5 }}>Asset</div>
          <div style={{ display: 'flex', gap: 4 }}>
            {ASSETS.map(a => (
              <button key={a} onClick={() => { setAsset(a); setResult(null) }} style={SEG_BTN(asset === a)}>{a}</button>
            ))}
          </div>
        </div>

        {/* Live price */}
        {quote?.last ? (
          <div style={{ alignSelf: 'flex-end', paddingBottom: 2 }}>
            <div style={{ fontSize: 9, color: 'var(--text-dim)', marginBottom: 4 }}>{quote.symbol}</div>
            <span style={{ fontFamily: 'monospace', fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
              {quote.last.toLocaleString('en-US', { minimumFractionDigits: 2 })}
            </span>
          </div>
        ) : null}

        {/* Side */}
        <div>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
            color: 'var(--text-dim)', marginBottom: 5 }}>Side</div>
          <div style={{ display: 'flex', gap: 4 }}>
            <button onClick={() => setSide('BUY')}  style={sideBtn('BUY')}>BUY</button>
            <button onClick={() => setSide('SELL')} style={sideBtn('SELL')}>SELL</button>
          </div>
        </div>

        {/* Qty */}
        <div style={{ width: 60 }}>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
            color: 'var(--text-dim)', marginBottom: 5 }}>Contracts</div>
          <input type="number" min={1} max={10} step={1} value={quantity}
            onChange={e => setQuantity(Number(e.target.value))} style={INPUT_STYLE} />
        </div>

        {/* Stop pts */}
        <div style={{ width: 80 }}>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
            color: 'var(--text-dim)', marginBottom: 5 }}>Stop (pts)</div>
          <input type="number" min={1} max={1000} step={1} value={stopPts}
            onChange={e => setStopPts(Number(e.target.value))} style={INPUT_STYLE} />
        </div>

        {/* Est stop */}
        {estStop !== null && (
          <div style={{ alignSelf: 'flex-end', paddingBottom: 2 }}>
            <div style={{ fontSize: 9, color: 'var(--text-dim)', marginBottom: 4 }}>Est. stop</div>
            <span style={{ fontFamily: 'monospace', fontSize: 13, fontWeight: 600, color: '#f87171' }}>
              {estStop.toLocaleString('en-US', { minimumFractionDigits: 2 })}
            </span>
          </div>
        )}

        {/* Place button */}
        <div style={{ alignSelf: 'flex-end' }}>
          <button onClick={handlePlace} disabled={busy} style={{
            fontSize: 11, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase',
            padding: '6px 18px', borderRadius: 6, cursor: busy ? 'not-allowed' : 'pointer',
            background: side === 'BUY' ? 'rgba(74,222,128,0.15)' : 'rgba(248,113,113,0.15)',
            border: side === 'BUY' ? '1px solid rgba(74,222,128,0.4)' : '1px solid rgba(248,113,113,0.4)',
            color: side === 'BUY' ? '#4ade80' : '#f87171',
            opacity: busy ? 0.6 : 1,
          }}>
            {busy ? 'Placing…' : `${side} ${quantity}ct`}
          </button>
        </div>

      </div>

      {/* result strip */}
      {result && (
        <div style={{ padding: '6px 20px 10px', fontSize: 11 }}>
          {result.error ? (
            <span style={{ color: '#f87171' }}>✗ {result.error}</span>
          ) : (
            <span style={{ color: '#4ade80', fontFamily: 'monospace' }}>
              ✓ Order sent · {result.symbol} {result.side} {result.quantity}ct
              {result.stop_price != null ? ` · stop ${result.stop_price.toLocaleString()}` : ''}
              {result.order_id ? ` · #${result.order_id}` : ''}
            </span>
          )}
        </div>
      )}
    </div>
  )
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

      {/* ── Bot panel ── */}
      <BotPanel />

      {/* ── Manual order panel ── */}
      <ManualOrderPanel />

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
