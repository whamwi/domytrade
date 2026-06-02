'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import Sidebar from './components/Sidebar'
import SignalTable, { Signal, SymbolInfo, PersonalityMap } from './components/SignalTable'
import MarketBias, { MarketBiasItem } from './components/MarketBias'
import SectorStrip, { SectorItem } from './components/SectorStrip'
import BriefingModal from './components/BriefingModal'
import AlertToast from './components/AlertToast'
import MarketProfile from './components/MarketProfile'
import GlobalMarketsStrip from './components/GlobalMarketsStrip'
import { useEconomicAlerts, EconAlert, playAlertSound } from './hooks/useEconomicAlerts'
import AskAI from './components/AskAI'
import EntryLog from './components/EntryLog'
import WatchlistDropdown from './components/WatchlistDropdown'
import WatchlistEditor, { Watchlist } from './components/WatchlistEditor'

const API_URL  = process.env.NEXT_PUBLIC_API_URL ?? ''
// WebSocket URL — same host, ws(s):// scheme
const WS_URL   = API_URL.replace(/^https?:\/\//, m => m === 'https://' ? 'wss://' : 'ws://')
const SLOW_POLL_INTERVAL = 60_000   // market-bias, symbols, industries, etc.
const WS_RETRY_BASE_MS   =  3_000   // initial WS reconnect delay (doubles up to 30s)

interface ApiResponse {
  signals: Signal[]
  count: number
  longs: number
  shorts: number
  last_updated: string
  status: string
}

type SideFilter  = 'all' | 'longs' | 'shorts'
type ModelFilter = 'all' | 'AGG' | 'CON' | 'WIDE' | 'CR'
type AssetFilter = 'all' | 'equities' | 'futures' | 'sectors'

const SECTOR_TICKERS = new Set([
  'XLK','XLV','XLF','XLC','XLY','XLI','XLP','XLE','XLB','XLU','XLRE',
  'SMH','HACK','SKYY','TAN','JETS','OIH','IYT','EEM','SOCL','KCE','XLG','XRT','OEF',
])

// Focus mode: these are always shown regardless of signal state
const PINNED_TICKERS = new Set(['/ES','/MES','/NQ','/MNQ','/YM','/MYM','/RTY','/M2K','/GC','/MGC'])

function getSessionLabel(): { label: string; color: string; bg: string } {
  const now = new Date()
  // Convert to ET
  const etStr = now.toLocaleString('en-US', { timeZone: 'America/New_York' })
  const et = new Date(etStr)
  const day = et.getDay() // 0=Sun, 6=Sat
  const h = et.getHours()
  const m = et.getMinutes()
  const timeMin = h * 60 + m

  const isWeekday = day >= 1 && day <= 5
  const marketOpen = 9 * 60 + 30   // 9:30
  const marketClose = 16 * 60       // 16:00
  const preMarketStart = 4 * 60     // 04:00

  if (!isWeekday) {
    return { label: 'AFTER HOURS', color: '#94a3b8', bg: '#1e2330' }
  }

  if (timeMin >= marketOpen && timeMin < marketClose) {
    return { label: 'LIVE', color: '#4ade80', bg: '#052e16' }
  }

  if (timeMin >= preMarketStart && timeMin < marketOpen) {
    return { label: 'PRE-MARKET', color: '#fbbf24', bg: '#271d07' }
  }

  return { label: 'AFTER HOURS', color: '#94a3b8', bg: '#1e2330' }
}

function formatLastUpdated(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('en-US', {
      timeZone: 'America/New_York',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }) + ' ET'
  } catch {
    return iso
  }
}

function getTabFromUrl(): string {
  if (typeof window === 'undefined') return 'dashboard'
  return new URLSearchParams(window.location.search).get('tab') ?? 'dashboard'
}

export default function DashboardPage() {
  const router = useRouter()
  const [authed, setAuthed] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)
  const [activeTab, setActiveTab] = useState<string>('dashboard')

  const [data, setData] = useState<ApiResponse | null>(null)
  const [allSymbols, setAllSymbols] = useState<SymbolInfo[]>([])
  const [marketBias, setMarketBias] = useState<MarketBiasItem[]>([])
  const [industries, setIndustries] = useState<SectorItem[]>([])
  const [ytdMap, setYtdMap] = useState<Record<string, number>>({})
  const [personalityData, setPersonalityData] = useState<PersonalityMap>({})
  const [personalityHour, setPersonalityHour] = useState<number>(-1)
  const [vix, setVix] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)

  const [briefingOpen, setBriefingOpen] = useState(false)
  const [activeAlert, setActiveAlert] = useState<EconAlert | null>(null)
  const [warmSeconds, setWarmSeconds] = useState(0)
  const [warmRetries, setWarmRetries] = useState(0)
  const warmTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // WebSocket live indicator — pulses on every incoming message
  const [wsLive, setWsLive]   = useState(false)   // true = connected
  const [wsPulse, setWsPulse] = useState(false)   // brief flash on each message
  const pulseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEconomicAlerts({ onAlert: (ev) => setActiveAlert(ev) })

  // Play one beep when any signal transitions NEAR → ENTRY
  useEffect(() => {
    if (!data?.signals) return
    const hasEntryAlert = data.signals.some((s: Signal) => s.entry_alert)
    if (hasEntryAlert) playAlertSound()
  }, [data?.signals])

  // Warm-up timer: only show spinner while backend hasn't responded at all.
  // If status is 'live' or 'cached', backend is healthy — 0 signals just means
  // off-hours (all stocks blocked). Don't spin forever in that case.
  const backendLive = data?.status === 'live' || data?.status === 'cached'
  const isWarmingUp = !error && (data === null || (!backendLive && data.signals.length === 0))
  useEffect(() => {
    if (isWarmingUp) {
      if (!warmTimerRef.current) {
        warmTimerRef.current = setInterval(() => setWarmSeconds(s => s + 1), 1000)
      }
    } else {
      if (warmTimerRef.current) {
        clearInterval(warmTimerRef.current)
        warmTimerRef.current = null
      }
      setWarmSeconds(0)
    }
    return () => {
      if (warmTimerRef.current) clearInterval(warmTimerRef.current)
    }
  }, [isWarmingUp])

  // Pulse the live dot briefly on each incoming WS message
  const triggerPulse = useCallback(() => {
    setWsPulse(true)
    if (pulseTimerRef.current) clearTimeout(pulseTimerRef.current)
    pulseTimerRef.current = setTimeout(() => setWsPulse(false), 600)
  }, [])

  const [sideFilter, setSideFilter]   = useState<SideFilter>('all')
  const [modelFilter, setModelFilter] = useState<ModelFilter>('CON')
  const [assetFilter, setAssetFilter] = useState<AssetFilter>('all')
  const [showAsiaFX, setShowAsiaFX]   = useState(false)
  const [focusMode, setFocusMode]     = useState(true)   // pin key futures, hide neutral others
  const [showLog, setShowLog]         = useState(false)  // entry log panel

  // ── Watchlist (legacy symbol picker — kept for UI compat) ─────────────────
  const [watchlist, setWatchlist]     = useState<string[]>([])
  const [pickerOpen, setPickerOpen]   = useState(false)
  const [watchSearch, setWatchSearch] = useState('')
  const pickerRef    = useRef<HTMLDivElement>(null)
  const watchSearchRef = useRef<HTMLInputElement>(null)

  // ── Named Watchlists (Supabase-persisted) ─────────────────────────────────
  const [watchlists,        setWatchlists]        = useState<Watchlist[]>([])
  const [activeWatchlistId, setActiveWatchlistId] = useState<string | null>(null)
  const [editorOpen,        setEditorOpen]        = useState(false)
  const [editingWatchlist,  setEditingWatchlist]  = useState<Watchlist | null>(null)

  // Load watchlists from Supabase on mount + restore active selection
  useEffect(() => {
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (!session) return
      const { data } = await supabase
        .from('watchlists')
        .select('*')
        .eq('user_id', session.user.id)
        .order('created_at', { ascending: true })
      if (data) {
        setWatchlists(data as Watchlist[])
        // Restore last active watchlist (if it still exists)
        const saved = localStorage.getItem('domytrade_active_watchlist')
        if (saved && data.some((w: Watchlist) => w.id === saved)) {
          setActiveWatchlistId(saved)
        }
      }
    })
  }, [])

  // Persist active watchlist selection
  useEffect(() => {
    if (activeWatchlistId) {
      localStorage.setItem('domytrade_active_watchlist', activeWatchlistId)
    } else {
      localStorage.removeItem('domytrade_active_watchlist')
    }
  }, [activeWatchlistId])

  function handleWatchlistSaved(w: Watchlist) {
    setWatchlists(prev => {
      const idx = prev.findIndex(x => x.id === w.id)
      return idx >= 0 ? prev.map(x => x.id === w.id ? w : x) : [...prev, w]
    })
    setActiveWatchlistId(w.id)
    setEditorOpen(false)
    setEditingWatchlist(null)
  }

  function handleWatchlistDeleted(id: string) {
    setWatchlists(prev => prev.filter(w => w.id !== id))
    if (activeWatchlistId === id) setActiveWatchlistId(null)
    setEditorOpen(false)
    setEditingWatchlist(null)
  }

  // Close picker on outside click; auto-focus search on open
  useEffect(() => {
    if (!pickerOpen) { setWatchSearch(''); return }
    setTimeout(() => watchSearchRef.current?.focus(), 50)
    const handler = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node))
        setPickerOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [pickerOpen])

  function toggleWatchlist(ticker: string) {
    setWatchlist(prev =>
      prev.includes(ticker) ? prev.filter(t => t !== ticker) : [...prev, ticker]
    )
  }

  const wsRef       = useRef<WebSocket | null>(null)
  const wsRetryRef  = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wsRetryMs   = useRef(WS_RETRY_BASE_MS)
  const slowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Auth check — also verifies profile is approved
  useEffect(() => {
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (!session) {
        router.replace('/login')
        setAuthChecked(true)
        return
      }
      // Check approval status
      const { data: profile } = await supabase
        .from('user_profiles')
        .select('status')
        .eq('id', session.user.id)
        .single()

      if (!profile || profile.status !== 'approved') {
        await supabase.auth.signOut()
        router.replace('/login')
        setAuthChecked(true)
        return
      }

      setAuthed(true)
      setAuthChecked(true)
    })
  }, [router])

  // Tab sync from URL
  useEffect(() => {
    setActiveTab(getTabFromUrl())
    const onPop = () => setActiveTab(getTabFromUrl())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  // ── Apply lightweight price update (every 5s from _price_loop) ──────────────
  const applyPrices = useCallback((prices: Record<string, number>) => {
    // 1. Update signal rows (sig.last)
    setData(prev => {
      if (!prev?.signals?.length) return prev
      let changed = false
      const updated = prev.signals.map((s: Signal) => {
        const p = prices[s.symbol]
        if (p == null || p === s.last) return s
        changed = true
        return { ...s, last: p }
      })
      return changed ? { ...prev, signals: updated } : prev
    })
    // 2. Update symbol rows (sym.last_price) — these use allSymbols, not signals
    setAllSymbols(prev => {
      let changed = false
      const updated = prev.map(sym => {
        const p = prices[sym.ticker]
        if (p == null || p === sym.last_price) return sym
        changed = true
        return { ...sym, last_price: p, net_change: sym.prev_close ? p - sym.prev_close : sym.net_change }
      })
      return changed ? updated : prev
    })
  }, [])

  // ── Apply incoming signal payload (snapshot or update) ────────────────────
  const applySignals = useCallback((payload: { signals: Signal[]; last_updated?: string }) => {
    const hasSignals = (payload.signals?.length ?? 0) > 0
    setData(prev => {
      if (!hasSignals && (prev?.signals?.length ?? 0) > 0) return prev  // keep stale
      const base = prev ?? { signals: [], count: 0, longs: 0, shorts: 0,
                             last_updated: '', last_stats: '', status: '' } as ApiResponse
      return {
        ...base,
        signals:      payload.signals,
        count:        payload.signals.length,
        longs:        payload.signals.filter((s: Signal) => s.side === 'LONG').length,
        shorts:       payload.signals.filter((s: Signal) => s.side === 'SHORT').length,
        last_updated: payload.last_updated ?? base.last_updated,
        status:       'live',
      }
    })
    if (payload.last_updated) setLastUpdated(formatLastUpdated(payload.last_updated))
    if (hasSignals) { setWarmRetries(0); setError(null); setLoading(false) }
    else setWarmRetries(r => r + 1)
  }, [])

  // ── WebSocket — real-time signal push ─────────────────────────────────────
  const connectWS = useCallback(() => {
    if (!WS_URL) return
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(`${WS_URL}/ws/signals`)

    ws.onopen = () => {
      wsRetryMs.current = WS_RETRY_BASE_MS   // reset backoff on success
      setWsLive(true)
    }

    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data)
        if (msg.type === 'snapshot' || msg.type === 'update') {
          applySignals(msg)
          triggerPulse()
        } else if (msg.type === 'prices') {
          applyPrices(msg.prices)
          triggerPulse()
        }
        // pong handled implicitly — no action needed
      } catch { /* malformed message — ignore */ }
    }

    ws.onclose = () => {
      wsRef.current = null
      setWsLive(false)
      // Exponential backoff — doubles each retry up to 30s
      const delay = wsRetryMs.current
      wsRetryMs.current = Math.min(delay * 2, 30_000)
      wsRetryRef.current = setTimeout(connectWS, delay)
    }

    ws.onerror = () => ws.close()   // onclose handles reconnect

    wsRef.current = ws
  }, [applySignals, applyPrices, triggerPulse])

  // ── Slow data poll — market-bias, symbols, industries, etc. ──────────────
  // These change infrequently so 60s polling is fine; signals come via WS.
  const fetchSlowData = useCallback(async () => {
    try {
      const [symRes, biasRes, indRes, ytdRes, persRes] = await Promise.all([
        fetch(`${API_URL}/api/symbols`,      { cache: 'no-store' }),
        fetch(`${API_URL}/api/market-bias`,  { cache: 'no-store' }),
        fetch(`${API_URL}/api/industries`,   { cache: 'no-store' }),
        fetch(`${API_URL}/api/sector-ytd`,   { cache: 'no-store' }),
        fetch(`${API_URL}/api/personality`,  { cache: 'no-store' }),
      ])
      if (symRes.ok) {
        const j = await symRes.json()
        setAllSymbols(j.symbols ?? [])
      }
      if (biasRes.ok) {
        const j = await biasRes.json()
        setMarketBias(j.markets ?? [])
        setVix(j.volatility?.vix ?? null)
      }
      if (indRes.ok) {
        const j = await indRes.json()
        setIndustries(j.industries ?? [])
      }
      if (ytdRes.ok) setYtdMap(await ytdRes.json())
      if (persRes.ok) {
        const j = await persRes.json()
        setPersonalityData(j.data ?? {})
        setPersonalityHour(j.hour_et ?? -1)
      }
    } catch { /* network issue — will retry */ }
    slowTimerRef.current = setTimeout(fetchSlowData, SLOW_POLL_INTERVAL)
  }, [])

  // ── Boot: connect WS + start slow poll when authed ────────────────────────
  useEffect(() => {
    if (!authed) return
    connectWS()
    fetchSlowData()
    // Keepalive ping every 20s to prevent proxy/Railway idle timeout
    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send('ping')
    }, 20_000)
    return () => {
      clearInterval(pingInterval)
      if (wsRetryRef.current)   clearTimeout(wsRetryRef.current)
      if (slowTimerRef.current) clearTimeout(slowTimerRef.current)
      if (pulseTimerRef.current) clearTimeout(pulseTimerRef.current)
      wsRef.current?.close()
    }
  }, [authed, connectWS, fetchSlowData])

  function handleRefresh() {
    // Force a slow-data refresh immediately; WS delivers signals automatically
    if (slowTimerRef.current) clearTimeout(slowTimerRef.current)
    fetchSlowData()
  }

  // Filter signals — watchlist narrows first, then side/model/asset apply on top
  const PINNED_ORDER = ['/ES','/MES','/NQ','/MNQ','/YM','/MYM','/RTY','/M2K','/GC','/MGC']

  const activeWL = watchlists.find(w => w.id === activeWatchlistId) ?? null

  const filteredSignals = (data?.signals ?? []).filter((sig) => {
    const ticker    = sig.symbol.split(':')[0]
    const isFuture  = ticker.startsWith('/')
    const isSector  = SECTOR_TICKERS.has(ticker)
    // Named watchlist filter (overrides legacy watchlist + model filter when active)
    const namedWLOk = !activeWL || (activeWL.assets.includes(ticker) && activeWL.models.includes(sig.model))
    // Legacy simple watchlist (only used when no named WL active)
    const watchOk   = activeWL ? true : (watchlist.length === 0 || watchlist.includes(ticker))
    const sideOk    =
      sideFilter === 'all' ||
      (sideFilter === 'longs'  && sig.side === 'LONG') ||
      (sideFilter === 'shorts' && sig.side === 'SHORT')
    const modelOk   = activeWL ? true : (modelFilter === 'all' || sig.model === modelFilter)
    const assetOk   =
      activeWL ? true : (
        assetFilter === 'all' ||
        (assetFilter === 'futures'  && isFuture) ||
        (assetFilter === 'equities' && !isFuture && !isSector) ||
        (assetFilter === 'sectors'  && isSector)
      )
    // Focus mode: pin key futures+/GC always; everything else only if at zone
    const focusOk   = !focusMode || PINNED_TICKERS.has(ticker) ||
                      sig.signal_state === 'NEAR' || sig.signal_state === 'ENTRY'
    return namedWLOk && watchOk && sideOk && modelOk && assetOk && focusOk
  }).sort((a, b) => {
    const ta = a.symbol.split(':')[0]
    const tb = b.symbol.split(':')[0]
    const ia = PINNED_ORDER.indexOf(ta)
    const ib = PINNED_ORDER.indexOf(tb)
    // Futures pinned first (in defined order); SignalTable handles the rest
    if (ia !== -1 && ib !== -1) return ia - ib
    if (ia !== -1) return -1
    if (ib !== -1) return  1
    return 0
  })

  // Silent symbols list — filtered by asset type AND watchlist
  const filteredSymbols = allSymbols
    .filter(s =>
      activeWL ? activeWL.assets.includes(s.ticker) : (
        assetFilter === 'all'      ? true :
        assetFilter === 'futures'  ? s.ticker.startsWith('/') :
        assetFilter === 'sectors'  ? SECTOR_TICKERS.has(s.ticker) :
        !s.ticker.startsWith('/') && !SECTOR_TICKERS.has(s.ticker)
      )
    )
    .filter(s => activeWL ? true : (watchlist.length === 0 || watchlist.includes(s.ticker)))

  const session = getSessionLabel()

  if (!authChecked) return null

  if (!authed) return null

  return (
    <div className="flex h-full min-h-screen" style={{ background: 'var(--bg-base)' }}>
      <Sidebar activeTab={activeTab} focusMode={focusMode} onFocusToggle={() => setFocusMode(f => !f)} showLog={showLog} onLogToggle={() => setShowLog(v => !v)} />

      {/* Market Profile tab */}
      <div className="flex-1 min-w-0 overflow-auto" style={{ display: activeTab === 'agent' ? undefined : 'none' }}>
        <MarketProfile />
      </div>

      {/* Main content — dashboard tab */}
      <div className="flex flex-col flex-1 min-w-0" style={{ display: activeTab === 'agent' ? 'none' : undefined }}>
        {/* Header */}
        <header
          className="flex items-center gap-4 px-5 py-3 shrink-0"
          style={{
            background: 'var(--bg-panel)',
            borderBottom: '1px solid var(--border)',
          }}
        >
          {/* Left: logo + session badge */}
          <div className="flex items-center gap-3 min-w-0">
            <span
              className="text-sm font-bold uppercase tracking-widest"
              style={{ color: 'var(--text-primary)', letterSpacing: '0.18em' }}
            >
              DOMYTRADE
            </span>
            <span
              className="inline-block rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wider"
              style={{ background: session.bg, color: session.color }}
            >
              {session.label}
            </span>
            {data?.status === 'cached' && (
              <span
                className="inline-block rounded px-2 py-0.5 text-xs font-bold uppercase tracking-wider"
                style={{ background: 'rgba(251,191,36,0.12)', color: '#fbbf24' }}
                title="Showing last saved signals — live data computing in background"
              >
                CACHED
              </span>
            )}
          </div>

          {/* Center: stats chips */}
          <div className="flex items-center gap-3 flex-1 justify-center">
            <Chip label="Signals" value={data?.count ?? 0} />
            <span style={{ color: 'var(--border)' }}>·</span>
            <Chip label="Bull" value={data?.longs ?? 0} color="#4ade80" />
            <span style={{ color: 'var(--border)' }}>·</span>
            <Chip label="Bear" value={data?.shorts ?? 0} color="#f87171" />
            {vix != null && (
              <>
                <span style={{ color: 'var(--border)' }}>·</span>
                <FearIndexChip value={vix} />
              </>
            )}
          </div>

          {/* Right: last updated + countdown arc + briefing + refresh */}
          <div className="flex items-center gap-3">
            {/* Live indicator — replaces countdown (push model has no fixed interval) */}
            <div className="flex items-center gap-1.5">
              {lastUpdated && (
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  {lastUpdated}
                </span>
              )}
              <div style={{
                width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                background: wsLive ? (wsPulse ? '#ffffff' : '#4ade80') : '#475569',
                boxShadow: wsLive ? `0 0 ${wsPulse ? '8px #4ade80' : '4px #4ade8066'}` : 'none',
                transition: 'background 0.2s, box-shadow 0.2s',
              }} title={wsLive ? 'Live — receiving updates' : 'Reconnecting…'} />
              <span className="text-xs" style={{ color: wsLive ? '#4ade80' : '#475569', fontWeight: 600 }}>
                {wsLive ? 'LIVE' : 'OFF'}
              </span>
            </div>
            <WatchlistDropdown
              watchlists={watchlists}
              activeWatchlistId={activeWatchlistId}
              onActivate={setActiveWatchlistId}
              onNew={() => { setEditingWatchlist(null); setEditorOpen(true) }}
              onEdit={w => { setEditingWatchlist(w); setEditorOpen(true) }}
            />
            <button
              onClick={() => setBriefingOpen(true)}
              className="rounded-lg px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors"
              style={{ color: 'var(--text-muted)', background: 'var(--bg-row)', border: '1px solid var(--border)' }}
              onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.borderColor = 'var(--accent-blue)' }}
              onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.borderColor = 'var(--border)' }}
            >
              Calendar
            </button>
            <button
              onClick={handleRefresh}
              title="Refresh"
              className="rounded-lg p-1.5 transition-colors"
              style={{ color: 'var(--text-muted)', background: 'transparent' }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = 'var(--bg-row)'
                e.currentTarget.style.color = 'var(--text-primary)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent'
                e.currentTarget.style.color = 'var(--text-muted)'
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="23 4 23 10 17 10" />
                <polyline points="1 20 1 14 7 14" />
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
              </svg>
            </button>
          </div>
        </header>

        {/* Filter row */}
        <div
          className="flex items-center gap-4 px-5 py-3 shrink-0"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          {/* Side filter */}
          <div
            className="flex items-center rounded-lg p-0.5 gap-0.5"
            style={{ background: 'var(--bg-panel)' }}
          >
            {(['all', 'longs', 'shorts'] as SideFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setSideFilter(f)}
                className="rounded-md px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors"
                style={
                  sideFilter === f
                    ? { background: 'var(--accent-blue)', color: '#fff' }
                    : { background: 'transparent', color: 'var(--text-muted)' }
                }
              >
                {f === 'all' ? 'All' : f === 'longs' ? 'Longs' : 'Shorts'}
              </button>
            ))}
          </div>

          {/* Model filter */}
          <div
            className="flex items-center rounded-lg p-0.5 gap-0.5"
            style={{ background: 'var(--bg-panel)' }}
          >
            {(['all', 'AGG', 'CON', 'WIDE', 'CR'] as ModelFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setModelFilter(f)}
                className="rounded-md px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors"
                style={
                  modelFilter === f
                    ? f === 'WIDE' ? { background: 'rgba(20,184,166,0.25)', color: '#2dd4bf' }
                    : f === 'CR'   ? { background: 'rgba(168,85,247,0.25)',  color: '#c084fc' }
                    :                { background: 'var(--accent-blue)', color: '#fff' }
                    : { background: 'transparent', color: 'var(--text-muted)' }
                }
              >
                {f === 'all' ? 'All Models' : f === 'AGG' ? 'AGGRO' : f === 'CON' ? 'CONSERV' : f}
              </button>
            ))}
          </div>

          {/* Asset filter */}
          <div
            className="flex items-center rounded-lg p-0.5 gap-0.5"
            style={{ background: 'var(--bg-panel)' }}
          >
            {(['all', 'equities', 'futures', 'sectors'] as AssetFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setAssetFilter(f)}
                className="rounded-md px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors"
                style={
                  assetFilter === f
                    ? { background: 'var(--accent-blue)', color: '#fff' }
                    : { background: 'transparent', color: 'var(--text-muted)' }
                }
              >
                {f === 'all' ? 'All Assets' : f === 'equities' ? 'Equities' : f === 'futures' ? 'Futures' : 'Sectors'}
              </button>
            ))}
          </div>



          {/* Asia / FX toggle */}
          <button
            onClick={() => setShowAsiaFX(v => !v)}
            title={showAsiaFX ? 'Hide Asia & FX' : 'Show Asia & FX (Risk On/Off)'}
            className="ml-auto flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors"
            style={
              showAsiaFX
                ? { background: 'var(--accent-blue)', color: '#fff' }
                : { background: 'var(--bg-panel)', color: 'var(--text-muted)' }
            }
          >
            🌏 Asia &amp; FX
          </button>
        </div>

        {/* Watchlist chips row — visible only when symbols are selected */}
        {watchlist.length > 0 && (
          <div
            className="flex items-center gap-2 px-5 py-2 shrink-0 flex-wrap"
            style={{ borderBottom: '1px solid var(--border)', background: 'rgba(251,191,36,0.04)' }}
          >
            <span className="text-[10px] font-bold uppercase tracking-widest" style={{ color: 'var(--text-dim)' }}>
              Watching
            </span>
            {watchlist.map(ticker => (
              <span
                key={ticker}
                className="flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold"
                style={{ background: 'rgba(251,191,36,0.12)', color: '#fbbf24', border: '1px solid rgba(251,191,36,0.25)' }}
              >
                {ticker}
                <button
                  onClick={() => toggleWatchlist(ticker)}
                  className="ml-0.5 leading-none opacity-60 hover:opacity-100 transition-opacity"
                  style={{ fontSize: '10px' }}
                  title={`Remove ${ticker}`}
                >
                  ✕
                </button>
              </span>
            ))}
            <button
              onClick={() => setWatchlist([])}
              className="text-[10px] uppercase tracking-wider transition-opacity opacity-40 hover:opacity-80"
              style={{ color: 'var(--text-muted)', marginLeft: '4px' }}
            >
              Clear all
            </button>
          </div>
        )}

        {/* Market bias strip */}
        <MarketBias markets={marketBias} />

        {/* Asian markets + FX risk-on/off strip — always mounted so data pre-fetches;
            hidden with CSS so the component keeps its state across toggles */}
        <div style={{ display: showAsiaFX ? undefined : 'none' }}>
          <GlobalMarketsStrip />
        </div>

        {/* Industries performance strip (% from RTH open) */}
        <SectorStrip sectors={industries} label="INDUSTRIES" />

        {/* Table — or warm-up screen while backend is initialising */}
        <div className="flex-1 overflow-auto">
          {isWarmingUp && authed ? (
            <WarmUpScreen seconds={warmSeconds} retries={warmRetries} error={error} onRetry={handleRefresh} />
          ) : (
            <SignalTable
              signals={filteredSignals}
              allSymbols={filteredSymbols}
              loading={loading}
              error={error}
              onRetry={handleRefresh}
              ytdMap={ytdMap}
              personalityData={personalityData}
              personalityHour={personalityHour}
            />
          )}
        </div>

        {/* Entry Log — fixed overlay panel, shown when Log button is active */}
        <EntryLog visible={showLog} onClose={() => setShowLog(false)} />
      </div>

      {/* Economic Briefing modal */}
      {briefingOpen && <BriefingModal onClose={() => setBriefingOpen(false)} />}
      {editorOpen && (
        <WatchlistEditor
          allSymbols={allSymbols}
          watchlist={editingWatchlist}
          onSave={handleWatchlistSaved}
          onDelete={editingWatchlist ? handleWatchlistDeleted : undefined}
          onClose={() => { setEditorOpen(false); setEditingWatchlist(null) }}
        />
      )}

      {/* Economic event alert toast */}
      {activeAlert && (
        <AlertToast alert={activeAlert} onDismiss={() => setActiveAlert(null)} />
      )}

      {/* Gemini Ask AI — floating corner chat */}
      <AskAI />

    </div>
  )
}

// ── Warm-up screen ────────────────────────────────────────────────────────────

function WarmUpScreen({
  seconds,
  retries,
  error,
  onRetry,
}: {
  seconds: number
  retries: number
  error: string | null
  onRetry: () => void
}) {
  // Phase labels that match the real backend startup sequence
  const phase =
    seconds < 20  ? { label: 'Connecting to backend…',        detail: 'Establishing connection' } :
    seconds < 45  ? { label: 'Loading market data…',           detail: 'Fetching OHLC bars & symbols' } :
    seconds < 70  ? { label: 'Computing signals…',             detail: 'Running VBH model across all assets' } :
    seconds < 100 ? { label: 'Almost ready…',                  detail: 'Finalising signal rankings' } :
                    { label: 'Still loading…',                  detail: `Retrying every 10 s — attempt ${retries}` }

  const pct = Math.min(100, Math.round((seconds / 90) * 100))

  return (
    <div
      className="flex flex-col items-center justify-center h-full gap-6"
      style={{ color: 'var(--text-muted)' }}
    >
      {/* Spinner */}
      <div style={{ position: 'relative', width: 56, height: 56 }}>
        <svg width="56" height="56" viewBox="0 0 56 56" fill="none" style={{ position: 'absolute', inset: 0 }}>
          <circle cx="28" cy="28" r="24" stroke="var(--border)" strokeWidth="3" />
          <circle
            cx="28" cy="28" r="24"
            stroke="var(--accent-blue)"
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray={`${2 * Math.PI * 24}`}
            strokeDashoffset={`${2 * Math.PI * 24 * (1 - pct / 100)}`}
            style={{ transform: 'rotate(-90deg)', transformOrigin: '28px 28px', transition: 'stroke-dashoffset 0.8s ease' }}
          />
        </svg>
        <span
          className="absolute inset-0 flex items-center justify-center text-xs font-bold tabular-nums"
          style={{ color: 'var(--accent-blue)' }}
        >
          {seconds}s
        </span>
      </div>

      {/* Status */}
      <div className="flex flex-col items-center gap-1 text-center">
        <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
          {error ? 'Cannot reach server — retrying…' : phase.label}
        </span>
        <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
          {error ? 'Check that the backend is running' : phase.detail}
        </span>
      </div>

      {/* Progress bar */}
      {!error && (
        <div
          className="rounded-full overflow-hidden"
          style={{ width: 220, height: 3, background: 'var(--border)' }}
        >
          <div
            className="h-full rounded-full"
            style={{
              width: `${pct}%`,
              background: 'var(--accent-blue)',
              transition: 'width 0.8s ease',
            }}
          />
        </div>
      )}

      {/* Retry counter — visible once backend has been polled at least once */}
      {retries > 0 && !error && (
        <span className="text-xs tabular-nums" style={{ color: 'var(--text-dim)' }}>
          Attempt {retries} · auto-retrying every 10 s
        </span>
      )}

      {/* Manual retry on error or after a very long wait */}
      {(error || seconds > 120) && (
        <button
          onClick={onRetry}
          className="rounded-lg px-4 py-2 text-xs font-semibold uppercase tracking-wider transition-colors"
          style={{ background: 'var(--bg-panel)', color: 'var(--text-muted)', border: '1px solid var(--border)' }}
          onMouseEnter={e => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.borderColor = 'var(--accent-blue)' }}
          onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.borderColor = 'var(--border)' }}
        >
          Retry now
        </button>
      )}
    </div>
  )
}

function Chip({
  label,
  value,
  color,
}: {
  label: string
  value: number
  color?: string
}) {
  return (
    <span className="flex items-center gap-1.5 text-xs">
      <span style={{ color: 'var(--text-muted)' }}>{label}:</span>
      <span
        className="font-bold tabular-nums"
        style={{ color: color ?? 'var(--text-primary)' }}
      >
        {value}
      </span>
    </span>
  )
}

function FearIndexChip({ value }: { value: number }) {
  const color = value <= 20 ? '#4ade80' : value <= 30 ? '#fbbf24' : '#f87171'
  return (
    <span className="flex items-center gap-1.5 text-xs">
      <span style={{ color: 'var(--text-muted)' }}>Fear Index:</span>
      <span className="font-bold tabular-nums" style={{ color }}>
        {value.toFixed(2)}
      </span>
    </span>
  )
}

