'use client'

import { useEffect, useRef, useState, useCallback, FormEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import FuturesPanel, { FuturesPanelInfo } from './FuturesPanel'

const API_URL    = process.env.NEXT_PUBLIC_API_URL ?? ''
const REFRESH_MS        = 60_000   // how often frontend polls the backend
const AI_TTL_DEFAULT_S  = 900      // default Claude refresh interval (15 min)
const AI_TTL_S          = AI_TTL_DEFAULT_S  // kept for secsUntilRefresh fallback
const TTL_OPTIONS        = [5, 10, 15, 30]  // minutes available in settings
const TTL_STORAGE_KEY    = 'dmt_ai_refresh_min'

function loadTTL(): number {
  try {
    const v = parseInt(localStorage.getItem(TTL_STORAGE_KEY) ?? '', 10)
    if (TTL_OPTIONS.includes(v)) return v * 60
  } catch { /* ignore */ }
  return AI_TTL_DEFAULT_S
}

/**
 * CME weekend pause: Friday 17:00 ET → Sunday 18:00 ET.
 * Returns true when futures markets are closed and auto-refresh should pause.
 */
function isCMEWeekend(): boolean {
  // Get current time in America/New_York
  const now  = new Date()
  const et   = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    weekday: 'short', hour: 'numeric', minute: 'numeric', hour12: false,
  }).formatToParts(now)
  const day  = et.find(p => p.type === 'weekday')?.value   // 'Mon'…'Sun'
  const hour = parseInt(et.find(p => p.type === 'hour')?.value   ?? '0', 10)
  const min  = parseInt(et.find(p => p.type === 'minute')?.value ?? '0', 10)
  const t    = hour * 60 + min

  if (day === 'Fri' && t >= 17 * 60) return true   // Fri 5:00 PM ET onward
  if (day === 'Sat')                  return true   // All day Saturday
  if (day === 'Sun' && t < 18 * 60)  return true   // Sun before 6:00 PM ET
  return false
}

/** Friendly label for when CME reopens (shown during weekend pause) */
function cmeReopenLabel(): string {
  const now = new Date()
  const et  = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', weekday: 'short',
  }).format(now)
  if (et === 'Fri' || et === 'Sat') return 'Sun 6 PM ET'
  return 'tonight 6 PM ET'   // Sunday before 6 PM
}

/** Human-readable age from a UTC-ms timestamp: "just now", "2m ago", "14m ago" */
function narrativeAge(tsMs: number | null): string {
  if (!tsMs) return ''
  const secs = Math.floor((Date.now() - tsMs) / 1000)
  if (secs < 60) return 'just now'
  return `${Math.floor(secs / 60)}m ago`
}

/** Seconds until next Claude call from a UTC-ms timestamp */
function secsUntilRefresh(tsMs: number | null, ttlS: number): number {
  if (!tsMs) return ttlS
  const elapsed = Math.floor((Date.now() - tsMs) / 1000)
  return Math.max(0, ttlS - elapsed)
}

interface Level  { name: string; price: number; type: string }
interface Targets { t1: Level | null; t2: Level | null; t3: Level | null }
interface Nearest { above: Level[]; below: Level[] }
interface Bias    { score: number; direction: 'BULL' | 'BEAR' | 'NEUTRAL'; reasons: string[] }
interface NakedVpoc { date: string; vpoc: number }

interface EntryStop {
  side:  'LONG' | 'SHORT'
  entry: number
  stop:  number
  risk:  number
}

interface SymbolData {
  symbol:      string
  price:       number
  change:      number
  change_pct:  number
  bias:        Bias
  nearest:     Nearest
  targets:     Targets
  entry_stop:  EntryStop | null
  naked_vpocs: NakedVpoc[]
  tick:        number
}

interface AgentResponse {
  generated_at:    string
  generated_at_ts: number   // UTC milliseconds — timezone-safe
  narrative:       string | null
  symbols:         SymbolData[]
}

const BIAS_COLOR = {
  BULL:    '#4ade80',
  BEAR:    '#f87171',
  NEUTRAL: '#94a3b8',
}

const SYMBOL_NAMES: Record<string, string> = {
  '/ES': 'S&P 500',
  '/NQ': 'Nasdaq',
  '/YM': 'Dow',
  '/RTY': 'Russell',
  '/GC': 'Gold',
}

function TargetBadge({ label, level, price }: { label: string; level: Level | null; price: number }) {
  if (!level) return null
  const dist  = level.price - price
  const color = level.type === 'naked' ? '#22d3ee' : '#a78bfa'
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs font-bold" style={{ color: '#94a3b8', minWidth: '16px' }}>{label}</span>
      <span className="text-xs font-bold tabular-nums" style={{ color }}>{level.price.toFixed(2)}</span>
      <span className="text-xs tabular-nums" style={{ color: '#60a5fa', opacity: 0.7, fontSize: '10px' }}>
        {dist >= 0 ? '+' : ''}{dist.toFixed(2)}
      </span>
    </div>
  )
}

function SymbolCard({ d, onOpenLevels }: { d: SymbolData; onOpenLevels: () => void }) {
  const biasColor   = BIAS_COLOR[d.bias.direction]
  const changeColor = d.change >= 0 ? '#4ade80' : '#f87171'
  const isUp        = d.change >= 0

  return (
    <div
      className="rounded-xl p-3 flex flex-col gap-2"
      style={{
        background  : 'var(--bg-row)',
        border      : `1px solid ${biasColor}28`,
        borderLeft  : `3px solid ${biasColor}`,
        minWidth    : '0',
      }}
    >
      {/* Symbol + price */}
      <div className="flex items-start justify-between gap-2">
        <div>
          <span className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--text-dim)' }}>
            {d.symbol}
          </span>
          <div className="text-sm font-bold tabular-nums" style={{ color: 'var(--text-primary)' }}>
            {d.price.toFixed(2)}
          </div>
          <div className="text-xs tabular-nums" style={{ color: changeColor }}>
            {isUp ? '+' : ''}{d.change.toFixed(2)} ({isUp ? '+' : ''}{d.change_pct.toFixed(2)}%)
          </div>
        </div>

        {/* Bias badge + Levels button */}
        <div className="flex flex-col items-end gap-1.5 shrink-0">
          <div
            className="rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wider"
            style={{ background: `${biasColor}18`, color: biasColor, border: `1px solid ${biasColor}40` }}
          >
            {d.bias.direction}
          </div>
          <button
            onClick={onOpenLevels}
            className="rounded px-2 py-0.5 text-xs font-semibold transition-colors"
            style={{
              background: 'rgba(96,165,250,0.08)',
              color     : '#60a5fa',
              border    : '1px solid rgba(96,165,250,0.2)',
              fontSize  : '9px',
              letterSpacing: '0.05em',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = 'rgba(96,165,250,0.18)' }}
            onMouseLeave={e => { e.currentTarget.style.background = 'rgba(96,165,250,0.08)' }}
          >
            LEVELS ↗
          </button>
        </div>
      </div>

      {/* Nearest levels */}
      <div className="flex flex-col gap-0.5">
        {d.nearest.above.map((l, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <span className="text-xs" style={{ color: '#94a3b8', fontSize: '9px' }}>▲</span>
            <span className="text-xs font-medium" style={{ color: 'var(--text-muted)', flex: 1 }}>{l.name}</span>
            <span className="text-xs tabular-nums font-bold" style={{ color: '#60a5fa' }}>{l.price.toFixed(2)}</span>
          </div>
        ))}

        {/* Current price line */}
        <div className="flex items-center gap-1 my-0.5">
          <div style={{ flex: 1, height: '1px', background: biasColor, opacity: 0.4 }} />
          <span className="text-xs font-bold tabular-nums" style={{ color: biasColor, fontSize: '10px' }}>
            {d.price.toFixed(2)}
          </span>
          <div style={{ flex: 1, height: '1px', background: biasColor, opacity: 0.4 }} />
        </div>

        {d.nearest.below.map((l, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <span className="text-xs" style={{ color: '#94a3b8', fontSize: '9px' }}>▼</span>
            <span className="text-xs font-medium" style={{ color: 'var(--text-muted)', flex: 1 }}>{l.name}</span>
            <span className="text-xs tabular-nums font-bold" style={{ color: '#60a5fa' }}>{l.price.toFixed(2)}</span>
          </div>
        ))}
      </div>

      {/* Targets */}
      <div
        className="rounded-lg px-2 py-1.5 flex flex-col gap-1"
        style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}
      >
        <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: 'var(--text-dim)', fontSize: '9px' }}>
          Targets
        </span>
        <TargetBadge label="T1" level={d.targets.t1} price={d.price} />
        <TargetBadge label="T2" level={d.targets.t2} price={d.price} />
        <TargetBadge label="T3" level={d.targets.t3} price={d.price} />
      </div>

      {/* Entry / Stop */}
      {d.entry_stop ? (
        <div
          className="rounded-lg px-2 py-1.5 flex flex-col gap-1"
          style={{
            background: d.entry_stop.side === 'SHORT'
              ? 'rgba(248,113,113,0.06)'
              : 'rgba(74,222,128,0.06)',
            border: `1px solid ${d.entry_stop.side === 'SHORT' ? 'rgba(248,113,113,0.2)' : 'rgba(74,222,128,0.2)'}`,
          }}
        >
          <span className="text-xs font-semibold uppercase tracking-widest" style={{ color: 'var(--text-dim)', fontSize: '9px' }}>
            Trade Setup
          </span>
          <div className="flex items-center justify-between">
            <span className="text-xs" style={{ color: 'var(--text-dim)' }}>Entry</span>
            <span
              className="text-xs font-bold tabular-nums"
              style={{ color: d.entry_stop.side === 'SHORT' ? '#f87171' : '#4ade80' }}
            >
              {d.entry_stop.side === 'SHORT' ? '▼' : '▲'} {d.entry_stop.entry.toFixed(2)}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs" style={{ color: 'var(--text-dim)' }}>Stop</span>
            <span className="text-xs font-bold tabular-nums" style={{ color: '#f87171' }}>
              {d.entry_stop.stop.toFixed(2)}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs" style={{ color: 'var(--text-dim)' }}>Risk</span>
            <span className="text-xs tabular-nums" style={{ color: '#94a3b8' }}>
              {d.entry_stop.risk.toFixed(2)} pts
            </span>
          </div>
        </div>
      ) : (
        <div className="text-xs rounded-lg px-2 py-1.5 text-center"
          style={{ background: 'rgba(255,255,255,0.03)', color: 'var(--text-dim)', fontSize: '10px' }}>
          No setup — Neutral bias
        </div>
      )}

      {/* Bias reasons */}
      <div className="flex flex-wrap gap-1">
        {d.bias.reasons.slice(0, 3).map((r, i) => (
          <span
            key={i}
            className="text-xs rounded px-1.5 py-0.5"
            style={{ background: 'rgba(255,255,255,0.04)', color: 'var(--text-dim)', fontSize: '9px' }}
          >
            {r}
          </span>
        ))}
      </div>
    </div>
  )
}

interface SRLevel {
  price:      number
  zone_type:  string
  touches:    number
  dist_pct:   number
  strength:   number
}

interface SRData {
  ticker:        string
  current_price: number
  resistance:    SRLevel[]
  support:       SRLevel[]
}

interface ChatMessage {
  role:    'user' | 'assistant'
  content: string
  sr_data?: Record<string, SRData>
}

const SUGGESTIONS = [
  'Best entry on /ES right now?',
  'Which symbol has the clearest setup?',
  'AAPL support & resistance last 3 days?',
  'NVDA supply and demand zones?',
  'What are the key levels on /NQ?',
  'Is /GC a short here?',
  'MSFT demand zones last 3 days?',
  'Which stock has the strongest demand zone?',
]

function SRCard({ ticker, data }: { ticker: string; data: SRData }) {
  return (
    <div
      className="rounded-xl p-2.5 flex flex-col gap-2 mt-1"
      style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-bold tracking-wider" style={{ color: 'var(--text-primary)' }}>
          {ticker}
        </span>
        <span className="text-xs tabular-nums" style={{ color: 'var(--text-dim)' }}>
          @ {data.current_price.toFixed(2)}  ·  3-day S/R
        </span>
      </div>

      <div className="grid gap-1" style={{ gridTemplateColumns: '1fr 1fr' }}>
        {/* Resistance / Supply */}
        <div className="flex flex-col gap-0.5">
          <span className="text-xs uppercase tracking-widest mb-0.5" style={{ color: '#f87171', fontSize: '9px' }}>
            Resistance
          </span>
          {data.resistance.slice(0, 4).map((r, i) => (
            <div key={i} className="flex items-center justify-between gap-1">
              <span
                className="text-xs tabular-nums font-semibold"
                style={{ color: r.zone_type === 'supply' ? '#f87171' : '#fca5a5' }}
              >
                {r.price.toFixed(2)}
              </span>
              <span className="text-xs" style={{ color: 'var(--text-dim)', fontSize: '9px' }}>
                {r.touches}x {r.dist_pct > 0 ? `+${r.dist_pct.toFixed(1)}%` : `${r.dist_pct.toFixed(1)}%`}
              </span>
            </div>
          ))}
        </div>

        {/* Support / Demand */}
        <div className="flex flex-col gap-0.5">
          <span className="text-xs uppercase tracking-widest mb-0.5" style={{ color: '#4ade80', fontSize: '9px' }}>
            Support
          </span>
          {data.support.slice(0, 4).map((s, i) => (
            <div key={i} className="flex items-center justify-between gap-1">
              <span
                className="text-xs tabular-nums font-semibold"
                style={{ color: s.zone_type === 'demand' ? '#4ade80' : '#86efac' }}
              >
                {s.price.toFixed(2)}
              </span>
              <span className="text-xs" style={{ color: 'var(--text-dim)', fontSize: '9px' }}>
                {s.touches}x {s.dist_pct > 0 ? `+${s.dist_pct.toFixed(1)}%` : `${s.dist_pct.toFixed(1)}%`}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function ChatPanel() {
  const [messages,  setMessages]  = useState<ChatMessage[]>([])
  const [input,     setInput]     = useState('')
  const [sending,   setSending]   = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef  = useRef<HTMLInputElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function send(text: string) {
    const msg = text.trim()
    if (!msg || sending) return

    const next: ChatMessage[] = [...messages, { role: 'user', content: msg }]
    setMessages(next)
    setInput('')
    setSending(true)

    try {
      const res  = await fetch(`${API_URL}/api/ai/chat`, {
        method : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body   : JSON.stringify({
          message: msg,
          history: messages.slice(-10), // send last 10 for context window
        }),
      })
      const json = await res.json()
      setMessages(prev => [...prev, {
        role   : 'assistant',
        content: json.reply ?? '…',
        sr_data: json.sr_data && Object.keys(json.sr_data).length ? json.sr_data : undefined,
      }])
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: 'Connection error — try again.' }])
    } finally {
      setSending(false)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    send(input)
  }

  return (
    <div
      className="flex flex-col rounded-xl overflow-hidden shrink-0"
      style={{
        border    : '1px solid rgba(96,165,250,0.2)',
        background: 'rgba(96,165,250,0.03)',
        height    : '480px',
      }}
    >
      {/* Header */}
      <div
        className="flex items-center gap-2 px-3 py-2 shrink-0"
        style={{ borderBottom: '1px solid rgba(96,165,250,0.12)' }}
      >
        <span className="text-xs font-bold" style={{ color: '#60a5fa' }}>💬 Ask the Agent</span>
        {sending && (
          <span className="text-xs" style={{ color: 'var(--text-dim)' }}>thinking…</span>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-auto px-3 py-2 flex flex-col gap-2">
        {messages.length === 0 && (
          <div className="flex flex-col gap-2 mt-2">
            <p className="text-xs" style={{ color: 'var(--text-dim)' }}>
              Ask anything about current futures setups, levels, or bias.
            </p>
            <div className="flex flex-wrap gap-1.5 mt-1">
              {SUGGESTIONS.map((s, i) => (
                <button
                  key={i}
                  onClick={() => send(s)}
                  className="text-xs rounded-lg px-2.5 py-1 transition-colors"
                  style={{
                    background: 'rgba(96,165,250,0.08)',
                    color     : '#60a5fa',
                    border    : '1px solid rgba(96,165,250,0.2)',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background = 'rgba(96,165,250,0.16)' }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'rgba(96,165,250,0.08)' }}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className="flex flex-col gap-1">
            <div
              className="flex"
              style={{ justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start' }}
            >
              <div
                className="rounded-xl px-3 py-2 text-xs leading-relaxed"
                style={
                  m.role === 'user'
                    ? {
                        background: 'rgba(96,165,250,0.18)',
                        color     : '#e2e8f0',
                        maxWidth  : '75%',
                        borderBottomRightRadius: '4px',
                      }
                    : {
                        background: 'rgba(255,255,255,0.05)',
                        color     : 'var(--text-muted)',
                        maxWidth  : '85%',
                        borderBottomLeftRadius : '4px',
                      }
                }
              >
                {m.role === 'assistant' ? (
                  <ReactMarkdown
                    components={{
                      p:      ({ children }) => <p className="text-xs leading-relaxed mb-1 last:mb-0" style={{ color: 'rgba(226,232,240,0.9)' }}>{children}</p>,
                      strong: ({ children }) => <strong className="font-bold" style={{ color: '#e2e8f0' }}>{children}</strong>,
                      ul:     ({ children }) => <ul className="text-xs flex flex-col gap-0.5 mb-1" style={{ paddingLeft: '1rem' }}>{children}</ul>,
                      li:     ({ children }) => <li className="text-xs leading-relaxed" style={{ color: 'rgba(226,232,240,0.85)', listStyleType: 'disc' }}>{children}</li>,
                    }}
                  >
                    {m.content}
                  </ReactMarkdown>
                ) : m.content}
              </div>
            </div>

            {/* S/R cards below assistant replies */}
            {m.role === 'assistant' && m.sr_data && Object.entries(m.sr_data).map(([ticker, data]) => (
              <SRCard key={ticker} ticker={ticker} data={data as SRData} />
            ))}
          </div>
        ))}

        {sending && (
          <div className="flex">
            <div
              className="rounded-xl px-3 py-2 text-xs"
              style={{ background: 'rgba(255,255,255,0.05)', color: 'var(--text-dim)' }}
            >
              <span className="animate-pulse">●●●</span>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form
        onSubmit={handleSubmit}
        className="flex items-center gap-2 px-3 py-2 shrink-0"
        style={{ borderTop: '1px solid rgba(96,165,250,0.12)' }}
      >
        <input
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="Ask about any futures symbol…"
          disabled={sending}
          className="flex-1 rounded-lg px-3 py-1.5 text-xs outline-none"
          style={{
            background: 'rgba(255,255,255,0.06)',
            border    : '1px solid rgba(255,255,255,0.1)',
            color     : 'var(--text-primary)',
          }}
          onFocus={e  => { e.currentTarget.style.borderColor = 'rgba(96,165,250,0.5)' }}
          onBlur={e   => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.1)' }}
        />
        <button
          type="submit"
          disabled={!input.trim() || sending}
          className="rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors"
          style={{
            background: input.trim() && !sending ? '#3b82f6' : 'rgba(255,255,255,0.06)',
            color     : input.trim() && !sending ? '#fff'    : 'var(--text-dim)',
          }}
        >
          Send
        </button>
      </form>
    </div>
  )
}

// ── Quiet hours helpers ────────────────────────────────────────────────────────
const DUBAI_TZ = 'Asia/Dubai'

function dubaiHourNow(): number {
  return parseInt(new Date().toLocaleString('en-US', { timeZone: DUBAI_TZ, hour: 'numeric', hour12: false }))
}

function dubaiTimeLabel(): string {
  return new Date().toLocaleTimeString('en-US', { timeZone: DUBAI_TZ, hour: '2-digit', minute: '2-digit', hour12: false })
}

function isQuiet(start: number, end: number): boolean {
  const h = dubaiHourNow()
  // overnight window e.g. 0–10: start < end
  // cross-midnight window e.g. 22–6: start > end
  return start < end ? h >= start && h < end : h >= start || h < end
}

function resumeLabel(end: number): string {
  return `${String(end).padStart(2, '0')}:00 Dubai`
}

const STORAGE_KEY = 'dmt_quiet_hours'
const DEFAULT_QUIET = { enabled: true, start: 0, end: 10 }

function loadQuiet() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return { ...DEFAULT_QUIET, ...JSON.parse(raw) }
  } catch { /* ignore */ }
  return DEFAULT_QUIET
}

// ── Settings popover ───────────────────────────────────────────────────────────
function QuietSettings({
  quiet, onChange, onClose, ttlS, onTTLChange,
}: {
  quiet: typeof DEFAULT_QUIET
  onChange: (q: typeof DEFAULT_QUIET) => void
  onClose: () => void
  ttlS: number
  onTTLChange: (s: number) => void
}) {
  const [local,    setLocal]    = useState(quiet)
  const [localTTL, setLocalTTL] = useState(ttlS / 60)  // stored in minutes

  function save() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(local))
    localStorage.setItem(TTL_STORAGE_KEY, String(localTTL))
    onChange(local)
    onTTLChange(localTTL * 60)
    onClose()
  }

  return (
    <div
      className="absolute right-0 top-7 rounded-xl p-3 flex flex-col gap-3 z-50"
      style={{
        background: 'var(--bg-panel)',
        border    : '1px solid var(--border)',
        width     : '220px',
        boxShadow : '0 8px 24px rgba(0,0,0,0.4)',
      }}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>Quiet Hours (Dubai)</span>
        <button onClick={onClose} style={{ color: 'var(--text-dim)', fontSize: '14px', lineHeight: 1 }}>✕</button>
      </div>

      {/* Enable toggle */}
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={local.enabled}
          onChange={e => setLocal(p => ({ ...p, enabled: e.target.checked }))}
          className="accent-blue-500"
        />
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>Pause refresh while sleeping</span>
      </label>

      {/* Time pickers */}
      <div className="flex items-center gap-2">
        <div className="flex flex-col gap-1 flex-1">
          <label className="text-xs" style={{ color: 'var(--text-dim)' }}>Sleep at</label>
          <input
            type="number" min={0} max={23}
            value={local.start}
            onChange={e => setLocal(p => ({ ...p, start: +e.target.value }))}
            className="rounded-lg px-2 py-1 text-xs text-center outline-none w-full"
            style={{ background: 'var(--bg-row)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
          />
        </div>
        <span className="text-xs mt-4" style={{ color: 'var(--text-dim)' }}>→</span>
        <div className="flex flex-col gap-1 flex-1">
          <label className="text-xs" style={{ color: 'var(--text-dim)' }}>Wake at</label>
          <input
            type="number" min={0} max={23}
            value={local.end}
            onChange={e => setLocal(p => ({ ...p, end: +e.target.value }))}
            className="rounded-lg px-2 py-1 text-xs text-center outline-none w-full"
            style={{ background: 'var(--bg-row)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
          />
        </div>
      </div>

      <p className="text-xs" style={{ color: 'var(--text-dim)', opacity: 0.7 }}>
        Hours in Dubai time (0–23). Currently {dubaiTimeLabel()} Dubai.
      </p>

      {/* AI Refresh interval */}
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: '10px' }}>
        <span className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>
          AI Refresh Interval
        </span>
        <div className="flex gap-1.5 mt-2">
          {TTL_OPTIONS.map(min => (
            <button
              key={min}
              onClick={() => setLocalTTL(min)}
              className="flex-1 rounded-lg py-1 text-xs font-semibold"
              style={{
                background: localTTL === min ? '#3b82f6'                   : 'var(--bg-row)',
                color     : localTTL === min ? '#fff'                       : 'var(--text-dim)',
                border    : `1px solid ${localTTL === min ? '#3b82f6' : 'var(--border)'}`,
              }}
            >
              {min}m
            </button>
          ))}
        </div>
        <p className="text-xs mt-1.5" style={{ color: 'var(--text-dim)', opacity: 0.6 }}>
          How often Claude generates a fresh analysis.
        </p>
      </div>

      <button
        onClick={save}
        className="rounded-lg py-1.5 text-xs font-semibold"
        style={{ background: '#3b82f6', color: '#fff' }}
      >
        Save
      </button>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function FuturesBrief() {
  const [data,         setData]         = useState<AgentResponse | null>(null)
  const [loading,      setLoading]      = useState(true)
  const [generatedAt,  setGeneratedAt]  = useState<number | null>(null)  // UTC ms
  const [ageLabel,     setAgeLabel]     = useState('')
  const [ttlS,         setTtlS]         = useState(loadTTL)   // user-chosen interval in seconds
  const [nextIn,       setNextIn]       = useState(() => loadTTL())
  const [quiet,        setQuiet]        = useState(DEFAULT_QUIET)
  const [showSettings, setShowSettings] = useState(false)
  const [levelsPanel,  setLevelsPanel]  = useState<FuturesPanelInfo | null>(null)
  const [stopped,      setStopped]      = useState(false)
  const [demanding,    setDemanding]    = useState(false)
  const [weekend,      setWeekend]      = useState(isCMEWeekend)
  const timerRef      = useRef<ReturnType<typeof setTimeout> | null>(null)
  const tickRef       = useRef<ReturnType<typeof setInterval> | null>(null)
  const quietRef      = useRef(quiet)
  const genAtRef      = useRef<number | null>(null)   // UTC ms
  const stoppedRef    = useRef(false)
  quietRef.current    = quiet

  // Load saved quiet hours on mount
  useEffect(() => { setQuiet(loadQuiet()) }, [])

  const sleeping = quiet.enabled && isQuiet(quiet.start, quiet.end)

  const fetchData = useCallback(async (force = false) => {
    // Re-evaluate weekend status on every poll tick
    const isWeekendNow = isCMEWeekend()
    setWeekend(isWeekendNow)

    // Respect stopped mode unless this is an on-demand call
    if (!force && stoppedRef.current) return
    // Auto-pause during CME weekend (on-demand still allowed)
    if (!force && isWeekendNow) {
      timerRef.current = setTimeout(() => fetchData(), REFRESH_MS)
      return
    }
    if (!force && quietRef.current.enabled && isQuiet(quietRef.current.start, quietRef.current.end)) {
      timerRef.current = setTimeout(() => fetchData(), REFRESH_MS)
      return
    }
    try {
      const res  = await fetch(`${API_URL}/api/ai/futures`, { cache: 'no-store' })
      const json: AgentResponse = await res.json()
      setData(json)
      setGeneratedAt(json.generated_at_ts)
      genAtRef.current = json.generated_at_ts
    } catch {
      // keep stale data on error
    } finally {
      setLoading(false)
      if (force) setDemanding(false)
    }
    // Only schedule next auto-poll if not stopped
    if (!stoppedRef.current) {
      timerRef.current = setTimeout(() => fetchData(), REFRESH_MS)
    }
  }, [ttlS])

  // Toggle auto-refresh on/off
  const toggleStop = useCallback(() => {
    const nowStopped = !stoppedRef.current
    stoppedRef.current = nowStopped
    setStopped(nowStopped)
    if (!nowStopped) {
      // Resume: kick off a poll immediately
      if (timerRef.current) clearTimeout(timerRef.current)
      fetchData()
    } else {
      // Stop: cancel any pending poll
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [fetchData])

  // On-demand: force a fresh fetch right now (bypasses backend cache via query param)
  const onDemand = useCallback(async () => {
    if (demanding) return
    setDemanding(true)
    setLoading(true)
    // Bust the backend bias-hash cache by appending a timestamp
    try {
      const res  = await fetch(`${API_URL}/api/ai/futures?bust=${Date.now()}`, { cache: 'no-store' })
      const json: AgentResponse = await res.json()
      setData(json)
      setGeneratedAt(json.generated_at_ts)
      genAtRef.current = json.generated_at_ts
    } catch {
      // keep stale
    } finally {
      setLoading(false)
      setDemanding(false)
    }
  }, [demanding])

  useEffect(() => {
    fetchData()
    // Tick every second to update "X min ago" and "next refresh in Xm Xs"
    tickRef.current = setInterval(() => {
      const ga = genAtRef.current
      setAgeLabel(narrativeAge(ga))
      setNextIn(secsUntilRefresh(ga, ttlS))
      // Auto-bust: if user's chosen interval has elapsed, force a fresh Claude call
      if (!stoppedRef.current && !isCMEWeekend() && secsUntilRefresh(ga, ttlS) === 0) {
        fetchData(true)
      }
    }, 1000)
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      if (tickRef.current)  clearInterval(tickRef.current)
    }
  }, [fetchData])

  return (
    <div className="flex flex-col gap-3 p-4 h-full overflow-auto">

      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold uppercase tracking-widest" style={{ color: 'var(--text-primary)' }}>
            Futures Agent
          </span>
          {loading && !sleeping && (
            <span className="text-xs" style={{ color: 'var(--text-dim)' }}>Loading…</span>
          )}
        </div>

        <div className="flex items-center gap-1.5 relative">

          {/* Stop / Resume auto-refresh */}
          <button
            onClick={toggleStop}
            title={stopped ? 'Resume automatic refresh' : 'Stop automatic refresh'}
            className="text-xs rounded px-2 py-0.5 font-medium"
            style={{
              background : stopped ? 'rgba(248,113,113,0.15)' : 'rgba(255,255,255,0.06)',
              color      : stopped ? '#f87171'                 : 'var(--text-dim)',
              border     : `1px solid ${stopped ? 'rgba(248,113,113,0.3)' : 'rgba(255,255,255,0.08)'}`,
              cursor     : 'pointer',
            }}
          >
            {stopped ? '▶ Resume' : '⏸ Stop'}
          </button>

          {/* On-demand refresh */}
          <button
            onClick={onDemand}
            disabled={demanding}
            title="Request a fresh AI analysis now"
            className="text-xs rounded px-2 py-0.5 font-medium"
            style={{
              background: demanding ? 'rgba(96,165,250,0.08)' : 'rgba(96,165,250,0.12)',
              color     : demanding ? 'rgba(96,165,250,0.4)'  : '#60a5fa',
              border    : '1px solid rgba(96,165,250,0.2)',
              cursor    : demanding ? 'default' : 'pointer',
            }}
          >
            {demanding ? '…' : '⚡ Now'}
          </button>

          {weekend ? (
            <span
              className="text-xs rounded px-2 py-0.5"
              title="CME futures closed — auto-refresh paused. You can still use ⚡ Now or chat."
              style={{ background: 'rgba(148,163,184,0.08)', color: '#64748b', border: '1px solid rgba(100,116,139,0.2)' }}
            >
              🌙 CME closed · reopens {cmeReopenLabel()}
            </span>
          ) : sleeping ? (
            <span
              className="text-xs rounded px-2 py-0.5"
              style={{ background: 'rgba(148,163,184,0.1)', color: '#94a3b8' }}
            >
              💤 Resumes {resumeLabel(quiet.end)}
            </span>
          ) : (
            <div className="flex items-center gap-1.5">
              {ageLabel && (
                <span className="text-xs tabular-nums" style={{ color: 'var(--text-dim)', opacity: 0.5 }}>
                  {ageLabel}
                </span>
              )}
              {!stopped && (
                <span
                  className="text-xs tabular-nums rounded px-1.5 py-0.5"
                  title="Time until next AI analysis refresh"
                  style={{ background: 'rgba(255,255,255,0.05)', color: '#60a5fa', minWidth: '42px', textAlign: 'center' }}
                >
                  {`${String(Math.floor(nextIn / 60)).padStart(2, '0')}:${String(nextIn % 60).padStart(2, '0')}`}
                </span>
              )}
            </div>
          )}

          {/* Settings gear */}
          <button
            title="Quiet hours settings"
            onClick={() => setShowSettings(s => !s)}
            className="rounded p-1 transition-colors"
            style={{ color: showSettings ? '#60a5fa' : 'var(--text-dim)', background: 'transparent' }}
            onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)' }}
            onMouseLeave={e => { e.currentTarget.style.color = showSettings ? '#60a5fa' : 'var(--text-dim)' }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>

          {showSettings && (
            <QuietSettings
              quiet={quiet}
              onChange={q => { setQuiet(q); quietRef.current = q }}
              onClose={() => setShowSettings(false)}
              ttlS={ttlS}
              onTTLChange={s => { setTtlS(s); setNextIn(secsUntilRefresh(genAtRef.current, s)) }}
            />
          )}
        </div>
      </div>

      {/* Claude narrative */}
      {data?.narrative && (
        <div
          className="rounded-xl p-3 shrink-0"
          style={{
            background: 'rgba(96,165,250,0.04)',
            border    : '1px solid rgba(96,165,250,0.15)',
          }}
        >
          <div className="flex items-center gap-1.5 mb-2">
            <span className="text-xs font-bold" style={{ color: '#60a5fa' }}>✦ AI Analysis</span>
          </div>
          <div
            style={{
              columns   : 2,
              columnGap : '1.5rem',
              columnRule: '1px solid rgba(96,165,250,0.1)',
            }}
          >
            <ReactMarkdown
              components={{
                h1: ({ children }) => (
                  <h1 className="text-xs font-bold uppercase tracking-wider mb-2" style={{ color: '#60a5fa' }}>{children}</h1>
                ),
                h2: ({ children }) => (
                  <h2 className="text-xs font-bold mt-3 mb-1" style={{ color: '#93c5fd' }}>{children}</h2>
                ),
                h3: ({ children }) => (
                  <h3 className="text-xs font-semibold mt-2 mb-0.5" style={{ color: '#bfdbfe' }}>{children}</h3>
                ),
                p: ({ children }) => (
                  <p className="text-xs leading-relaxed mb-1.5" style={{ color: 'rgba(226,232,240,0.9)' }}>{children}</p>
                ),
                strong: ({ children }) => (
                  <strong className="font-bold" style={{ color: '#e2e8f0' }}>{children}</strong>
                ),
                em: ({ children }) => (
                  <em style={{ color: '#94a3b8' }}>{children}</em>
                ),
                ul: ({ children }) => (
                  <ul className="text-xs mb-1.5 flex flex-col gap-0.5" style={{ paddingLeft: '1rem' }}>{children}</ul>
                ),
                li: ({ children }) => (
                  <li className="text-xs leading-relaxed" style={{ color: 'rgba(226,232,240,0.85)', listStyleType: 'disc' }}>{children}</li>
                ),
                hr: () => (
                  <hr style={{ border: 'none', borderTop: '1px solid rgba(96,165,250,0.15)', margin: '8px 0' }} />
                ),
              }}
            >
              {data.narrative}
            </ReactMarkdown>
          </div>
        </div>
      )}

      {/* Symbol cards grid */}
      {data?.symbols && (
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
          {data.symbols.map(s => (
            <SymbolCard
              key={s.symbol}
              d={s}
              onOpenLevels={() => setLevelsPanel({
                symbol   : s.symbol,
                last     : s.price,
                change   : s.change,
                changePct: s.change_pct,
              })}
            />
          ))}
        </div>
      )}

      {!loading && !data && (
        <div className="py-8 text-center text-xs" style={{ color: 'var(--text-dim)' }}>
          No data available
        </div>
      )}

      {/* Chat panel */}
      <ChatPanel />

      {/* Levels modal */}
      {levelsPanel && (
        <FuturesPanel
          info={levelsPanel}
          onClose={() => setLevelsPanel(null)}
        />
      )}
    </div>
  )
}
