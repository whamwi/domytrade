'use client'

import { useEffect, useState, useCallback } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

interface BotSignal {
  symbol:       string
  signal_state: string
  side:         string
  entry:        number
  model:        string
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

interface BotArmed {
  side:        'LONG' | 'SHORT'
  entry_level: number
}

interface BotCfg {
  symbol:   string
  model:    string
  stop_pts: number
  quantity: number
}

interface BotStatus {
  enabled:    boolean
  cfg:        BotCfg
  armed:      BotArmed | null
  position:   BotPosition | null
  signal:     BotSignal | null
  live_price: number | null
  log:        { ts: string; level: string; msg: string }[]
}

const MODELS = ['CON', 'AGG', 'WIDE']

const SEG = (active: boolean): React.CSSProperties => ({
  fontSize: 10, fontWeight: 700, letterSpacing: '0.07em',
  padding: '4px 10px', borderRadius: 5, cursor: 'pointer',
  background: active ? 'var(--accent-blue)' : 'rgba(255,255,255,0.04)',
  border:     active ? '1px solid var(--accent-blue)' : '1px solid var(--border)',
  color:      active ? '#fff' : 'var(--text-dim)',
  transition: 'all 0.15s',
})

const NUM_INPUT: React.CSSProperties = {
  background: 'rgba(255,255,255,0.06)', border: '1px solid var(--border)',
  borderRadius: 6, color: 'var(--text-primary)', fontSize: 11,
  padding: '4px 8px', outline: 'none', width: '100%',
}

function relTime(iso: string): string {
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (m < 1)  return 'just now'
  if (m < 60) return `${m}m ago`
  return `${Math.floor(m / 60)}h ${m % 60}m ago`
}

const pnlCol = (n: number) => n > 0 ? '#4ade80' : n < 0 ? '#f87171' : '#64748b'

export default function EquityBotPanel() {
  const [bot,      setBot]      = useState<BotStatus | null>(null)
  const [symbols,  setSymbols]  = useState<string[]>([])
  const [busy,     setBusy]     = useState(false)
  const [cfgErr,   setCfgErr]   = useState<string | null>(null)

  // form state
  const [symbol,   setSymbol]   = useState('BA')
  const [model,    setModel]    = useState('CON')
  const [stopPts,  setStopPts]  = useState(100)
  const [quantity, setQuantity] = useState(1)

  const fetchBot = useCallback(async () => {
    try {
      const r = await fetch(`${API_URL}/api/equity-bot/status`)
      if (r.ok) {
        const data: BotStatus = await r.json()
        setBot(data)
        if (data.enabled && data.cfg) {
          setSymbol(data.cfg.symbol)
          setModel(data.cfg.model)
          setStopPts(data.cfg.stop_pts)
          setQuantity(data.cfg.quantity)
        }
      }
    } catch {}
  }, [])

  useEffect(() => {
    fetch(`${API_URL}/api/equity-bot/symbols`)
      .then(r => r.json())
      .then(d => setSymbols(d.symbols ?? []))
      .catch(() => {})
    fetchBot()
    const t = setInterval(fetchBot, 5_000)
    return () => clearInterval(t)
  }, [fetchBot])

  async function handleEnable() {
    if (busy) return
    setCfgErr(null)
    setBusy(true)
    try {
      const r = await fetch(`${API_URL}/api/equity-bot/enable`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, model, stop_pts: stopPts, quantity }),
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
      const r = await fetch(`${API_URL}/api/equity-bot/disable`, { method: 'POST' })
      if (r.ok) setBot(await r.json())
    } finally { setBusy(false) }
  }

  async function handleClose() {
    if (busy || !bot?.position) return
    const pos = bot.position
    if (!confirm(`Close ${pos.symbol} ${pos.side} × ${pos.quantity} at market NOW?`)) return
    setBusy(true)
    try {
      const r = await fetch(`${API_URL}/api/equity-bot/close`, { method: 'POST' })
      if (r.ok) setBot(await r.json())
    } finally { setBusy(false) }
  }

  const enabled   = bot?.enabled ?? false
  const sig       = bot?.signal
  const pos       = bot?.position
  const armed     = bot?.armed
  const sigColor  = sig?.signal_state === 'ENTRY' ? '#4ade80' : sig?.signal_state === 'NEAR' ? '#fbbf24' : '#64748b'
  const logColor  = (l: string) => l === 'TRADE' ? '#60a5fa' : l === 'ERROR' ? '#f87171' : '#94a3b8'
  const stopDollar = (stopPts / 100).toFixed(2)

  return (
    <div style={{
      borderBottom: '1px solid var(--border)',
      background: enabled ? 'rgba(74,222,128,0.02)' : 'var(--bg-panel)',
    }}>
      {/* ── status bar ── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 12, padding: '8px 16px',
        borderBottom: '1px solid var(--border)', flexWrap: 'wrap',
      }}>
        {/* dot + label */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 7, flexShrink: 0 }}>
          <div style={{
            width: 7, height: 7, borderRadius: '50%',
            background: enabled ? '#4ade80' : '#475569',
            boxShadow: enabled ? '0 0 5px #4ade8088' : 'none',
          }} />
          <span style={{
            fontSize: 10, fontWeight: 700, letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: enabled ? '#4ade80' : 'var(--text-dim)',
          }}>Equity Bot</span>
        </div>

        {/* running config pill */}
        {enabled && bot?.cfg && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '2px 9px', borderRadius: 6,
            background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)',
            flexShrink: 0,
          }}>
            <span style={{ fontSize: 11, fontFamily: 'monospace', fontWeight: 700, color: 'var(--text-primary)' }}>{bot.cfg.symbol}</span>
            <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>·</span>
            <span style={{ fontSize: 10, fontWeight: 700, color: '#60a5fa' }}>{bot.cfg.model}</span>
            <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>·</span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{bot.cfg.quantity}sh</span>
            <span style={{ fontSize: 9, color: 'var(--text-dim)' }}>·</span>
            <span style={{ fontSize: 10, color: '#f87171' }}>${(bot.cfg.stop_pts / 100).toFixed(2)} stop</span>
          </div>
        )}

        {/* ARMED badge */}
        {armed && !pos && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '2px 10px', borderRadius: 6, flexShrink: 0,
            background: 'rgba(251,191,36,0.1)', border: '1px solid rgba(251,191,36,0.3)',
          }}>
            <span style={{ fontSize: 9, fontWeight: 800, color: '#fbbf24', letterSpacing: '0.1em' }}>ARMED</span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{armed.side}</span>
            <span style={{ fontSize: 11, fontFamily: 'monospace', fontWeight: 700, color: '#fbbf24' }}>
              {armed.entry_level.toFixed(2)}
            </span>
            {bot?.live_price && (
              <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>
                live {bot.live_price.toFixed(2)}
              </span>
            )}
          </div>
        )}

        {/* signal badge */}
        {sig && !armed && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 5,
            padding: '2px 9px', borderRadius: 6, flexShrink: 0,
            background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)',
          }}>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{sig.symbol?.split(':')[0]}</span>
            <span style={{ fontSize: 11, fontWeight: 700, fontFamily: 'monospace', color: sigColor }}>{sig.signal_state}</span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{sig.side}</span>
            <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-primary)' }}>{sig.entry?.toFixed(2)}</span>
          </div>
        )}

        {/* position pill */}
        {pos && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 7,
            padding: '3px 10px', borderRadius: 6, flexShrink: 0,
            background: pos.side === 'LONG' ? 'rgba(74,222,128,0.1)' : 'rgba(248,113,113,0.1)',
            border: `1px solid ${pos.side === 'LONG' ? 'rgba(74,222,128,0.25)' : 'rgba(248,113,113,0.25)'}`,
          }}>
            <span style={{ fontSize: 10, fontWeight: 700, color: pos.side === 'LONG' ? '#4ade80' : '#f87171' }}>{pos.side}</span>
            <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-primary)' }}>{pos.symbol}</span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>@{pos.entry_price.toFixed(2)}</span>
            <span style={{ fontSize: 10, color: '#f87171' }}>stop {pos.stop_price.toFixed(2)}</span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>× {pos.quantity}</span>
            <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{relTime(pos.entered_at)}</span>
          </div>
        )}

        <div style={{ flex: 1 }} />

        {pos && (
          <button onClick={handleClose} disabled={busy} style={{
            fontSize: 10, fontWeight: 700, letterSpacing: '0.07em',
            padding: '4px 10px', borderRadius: 6, cursor: 'pointer',
            background: 'rgba(248,113,113,0.12)', border: '1px solid rgba(248,113,113,0.35)',
            color: '#f87171',
          }}>CLOSE</button>
        )}

        {enabled && (
          <button onClick={handleDisable} disabled={busy} style={{
            fontSize: 10, fontWeight: 700, letterSpacing: '0.07em',
            padding: '4px 12px', borderRadius: 6, cursor: 'pointer',
            background: 'rgba(74,222,128,0.1)', border: '1px solid rgba(74,222,128,0.3)',
            color: '#4ade80',
          }}>DISABLE</button>
        )}
      </div>

      {/* ── config form (disabled state) ── */}
      {!enabled && (
        <div style={{ padding: '10px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'flex-end', gap: 18, flexWrap: 'wrap' }}>

            {/* Symbol */}
            <div>
              <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
                color: 'var(--text-dim)', marginBottom: 5 }}>Symbol</div>
              <select
                value={symbol}
                onChange={e => setSymbol(e.target.value)}
                style={{
                  background: 'rgba(255,255,255,0.06)', border: '1px solid var(--border)',
                  borderRadius: 6, color: 'var(--text-primary)', fontSize: 11,
                  padding: '4px 8px', outline: 'none', minWidth: 90,
                }}
              >
                {symbols.length === 0 && <option value={symbol}>{symbol}</option>}
                {symbols.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </div>

            {/* Model */}
            <div>
              <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
                color: 'var(--text-dim)', marginBottom: 5 }}>Model</div>
              <div style={{ display: 'flex', gap: 4 }}>
                {MODELS.map(m => (
                  <button key={m} onClick={() => setModel(m)} style={SEG(model === m)}>{m}</button>
                ))}
              </div>
            </div>

            {/* Stop pts */}
            <div style={{ width: 90 }}>
              <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
                color: 'var(--text-dim)', marginBottom: 5 }}>Stop (pts)</div>
              <input type="number" min={1} max={10000} step={1} value={stopPts}
                onChange={e => setStopPts(Number(e.target.value))} style={NUM_INPUT} />
              <div style={{ fontSize: 9, color: 'var(--text-dim)', marginTop: 3 }}>= ${stopDollar}</div>
            </div>

            {/* Quantity */}
            <div style={{ width: 70 }}>
              <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.09em', textTransform: 'uppercase',
                color: 'var(--text-dim)', marginBottom: 5 }}>Shares</div>
              <input type="number" min={1} max={500} step={1} value={quantity}
                onChange={e => setQuantity(Number(e.target.value))} style={NUM_INPUT} />
            </div>

            {/* Enable */}
            <div>
              <button onClick={handleEnable} disabled={busy} style={{
                fontSize: 11, fontWeight: 800, letterSpacing: '0.08em', textTransform: 'uppercase',
                padding: '5px 18px', borderRadius: 6, cursor: busy ? 'not-allowed' : 'pointer',
                background: 'rgba(74,222,128,0.15)', border: '1px solid rgba(74,222,128,0.4)',
                color: '#4ade80', opacity: busy ? 0.6 : 1,
              }}>
                {busy ? 'Starting…' : 'Enable Bot'}
              </button>
            </div>

          </div>

          {cfgErr && (
            <div style={{ marginTop: 7, fontSize: 10, color: '#f87171' }}>{cfgErr}</div>
          )}
        </div>
      )}

      {/* ── log strip ── */}
      {bot && bot.log.length > 0 && (
        <div style={{
          display: 'flex', gap: 14, padding: '5px 16px 8px', overflowX: 'auto', alignItems: 'center',
        }}>
          {[...bot.log].reverse().slice(0, 6).map((e, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0, whiteSpace: 'nowrap' }}>
              <span style={{ fontSize: 9, color: logColor(e.level), fontWeight: 700 }}>{e.level}</span>
              <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{e.msg}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
