'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import Sidebar from './components/Sidebar'
import SignalTable, { Signal, SymbolInfo } from './components/SignalTable'
import MarketBias, { MarketBiasItem } from './components/MarketBias'
import SectorStrip, { SectorItem } from './components/SectorStrip'
import BriefingModal from './components/BriefingModal'
import AlertToast from './components/AlertToast'
import FuturesBrief from './components/FuturesBrief'
import GlobalMarketsStrip from './components/GlobalMarketsStrip'
import { useEconomicAlerts, EconAlert, playAlertSound } from './hooks/useEconomicAlerts'
import AskAI from './components/AskAI'
import EntryLog from './components/EntryLog'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''
const REFRESH_INTERVAL       = 30_000   // normal polling — matches entry log cadence
const WARMUP_RETRY_INTERVAL  = 10_000   // fast retry while backend is still computing
const ERROR_REFRESH_INTERVAL =  5_000   // retry on network error

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
  const [vix, setVix] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)

  const [briefingOpen, setBriefingOpen] = useState(false)
  const [activeAlert, setActiveAlert] = useState<EconAlert | null>(null)
  const [warmSeconds, setWarmSeconds] = useState(0)
  const [warmRetries, setWarmRetries] = useState(0)
  const warmTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Refresh countdown: counts down from 60→0 after each successful data fetch
  const [countdown, setCountdown] = useState<number>(60)
  const countdownIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

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

  // Reset countdown to 60 and tick down every second whenever the data timestamp changes
  useEffect(() => {
    if (!lastUpdated) return
    setCountdown(REFRESH_INTERVAL / 1000)
    if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current)
    countdownIntervalRef.current = setInterval(() => {
      setCountdown(c => {
        if (c <= 1) { clearInterval(countdownIntervalRef.current!); return 0 }
        return c - 1
      })
    }, 1000)
    return () => { if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current) }
  }, [lastUpdated])

  const [sideFilter, setSideFilter]   = useState<SideFilter>('all')
  const [modelFilter, setModelFilter] = useState<ModelFilter>('CON')
  const [assetFilter, setAssetFilter] = useState<AssetFilter>('all')
  const [showAsiaFX, setShowAsiaFX]   = useState(false)
  const [focusMode, setFocusMode]     = useState(true)   // pin key futures, hide neutral others
  const [showLog, setShowLog]         = useState(false)  // entry log panel

  // ── Watchlist (persistent symbol picker) ──────────────────────────────────
  const [watchlist, setWatchlist]     = useState<string[]>([])
  const [pickerOpen, setPickerOpen]   = useState(false)
  const pickerRef = useRef<HTMLDivElement>(null)

  // Load from localStorage once on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem('domytrade_watchlist')
      if (saved) setWatchlist(JSON.parse(saved))
    } catch {}
  }, [])

  // Persist whenever it changes
  useEffect(() => {
    localStorage.setItem('domytrade_watchlist', JSON.stringify(watchlist))
  }, [watchlist])

  // Close picker on outside click
  useEffect(() => {
    if (!pickerOpen) return
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

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Auth check
  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) {
        router.replace('/login')
      } else {
        setAuthed(true)
      }
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

  const fetchSignals = useCallback(async () => {
    try {
      const [sigRes, symRes, biasRes, indRes, ytdRes] = await Promise.all([
        fetch(`${API_URL}/api/signals?model=all&side=all`, { cache: 'no-store' }),
        fetch(`${API_URL}/api/symbols`,                    { cache: 'no-store' }),
        fetch(`${API_URL}/api/market-bias`,                { cache: 'no-store' }),
        fetch(`${API_URL}/api/industries`,                 { cache: 'no-store' }),
        fetch(`${API_URL}/api/sector-ytd`,                 { cache: 'no-store' }),
      ])
      if (!sigRes.ok) throw new Error(`HTTP ${sigRes.status}`)
      const json: ApiResponse = await sigRes.json()
      // Don't overwrite good data with an empty array — backend may be warming up
      // after a restart and returns [] for the first 30-60s.  Keep showing stale
      // data until the backend sends real signals again.
      const hasSignals = json.signals.length > 0
      setData(prev => (hasSignals || !prev?.signals.length) ? json : prev)
      setLastUpdated(formatLastUpdated(json.last_updated))
      setError(null)
      setLoading(false)

      if (hasSignals) {
        setWarmRetries(0)
      } else {
        // Backend still warming up — count retries so the UI can show progress
        setWarmRetries(r => r + 1)
      }

      if (symRes.ok) {
        const symJson = await symRes.json()
        setAllSymbols(symJson.symbols ?? [])
      }
      if (biasRes.ok) {
        const biasJson = await biasRes.json()
        setMarketBias(biasJson.markets ?? [])
        setVix(biasJson.volatility?.vix ?? null)
      }
      if (indRes.ok) {
        const indJson = await indRes.json()
        setIndustries(indJson.industries ?? [])
      }
      if (ytdRes.ok) {
        setYtdMap(await ytdRes.json())
      }

      // Retry faster while signals haven't arrived yet
      timerRef.current = setTimeout(fetchSignals, hasSignals ? REFRESH_INTERVAL : WARMUP_RETRY_INTERVAL)
    } catch {
      setError('Cannot reach server — retrying')
      setLoading(false)
      timerRef.current = setTimeout(fetchSignals, ERROR_REFRESH_INTERVAL)
    }
  }, [])

  useEffect(() => {
    if (!authed) return
    fetchSignals()
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [authed, fetchSignals])

  function handleRefresh() {
    if (timerRef.current) clearTimeout(timerRef.current)
    setLoading(true)
    fetchSignals()
  }

  // Filter signals — watchlist narrows first, then side/model/asset apply on top
  const PINNED_ORDER = ['/ES','/MES','/NQ','/MNQ','/YM','/MYM','/RTY','/M2K','/GC','/MGC']

  const filteredSignals = (data?.signals ?? []).filter((sig) => {
    const ticker    = sig.symbol.split(':')[0]
    const isFuture  = ticker.startsWith('/')
    const isSector  = SECTOR_TICKERS.has(ticker)
    const watchOk   = watchlist.length === 0 || watchlist.includes(ticker)
    const sideOk    =
      sideFilter === 'all' ||
      (sideFilter === 'longs'  && sig.side === 'LONG') ||
      (sideFilter === 'shorts' && sig.side === 'SHORT')
    const modelOk   = modelFilter === 'all' || sig.model === modelFilter
    const assetOk   =
      assetFilter === 'all' ||
      (assetFilter === 'futures'  && isFuture) ||
      (assetFilter === 'equities' && !isFuture && !isSector) ||
      (assetFilter === 'sectors'  && isSector)
    // Focus mode: pin key futures+/GC always; everything else only if at zone
    const focusOk   = !focusMode || PINNED_TICKERS.has(ticker) ||
                      sig.signal_state === 'NEAR' || sig.signal_state === 'ENTRY'
    return watchOk && sideOk && modelOk && assetOk && focusOk
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
      assetFilter === 'all'      ? true :
      assetFilter === 'futures'  ? s.ticker.startsWith('/') :
      assetFilter === 'sectors'  ? SECTOR_TICKERS.has(s.ticker) :
      !s.ticker.startsWith('/') && !SECTOR_TICKERS.has(s.ticker)
    )
    .filter(s => watchlist.length === 0 || watchlist.includes(s.ticker))

  const session = getSessionLabel()

  if (!authChecked) return null

  if (!authed) return null

  return (
    <div className="flex h-full min-h-screen" style={{ background: 'var(--bg-base)' }}>
      <Sidebar activeTab={activeTab} focusMode={focusMode} onFocusToggle={() => setFocusMode(f => !f)} showLog={showLog} onLogToggle={() => setShowLog(v => !v)} />

      {/* Agent tab — always mounted to preserve chat history and avoid refetch on toggle */}
      <div className="flex-1 min-w-0 overflow-auto" style={{ display: activeTab === 'agent' ? undefined : 'none' }}>
        <FuturesBrief />
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
            {lastUpdated && (
              <div className="flex items-center gap-1.5">
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  {lastUpdated}
                </span>
                {/* Countdown arc — fills from empty→full as next refresh approaches */}
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" style={{ flexShrink: 0 }}>
                  <circle cx="7" cy="7" r="5" stroke="var(--border)" strokeWidth="1.5" />
                  <circle
                    cx="7" cy="7" r="5"
                    stroke="var(--accent-blue)"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeDasharray={`${2 * Math.PI * 5}`}
                    strokeDashoffset={`${2 * Math.PI * 5 * (countdown / (REFRESH_INTERVAL / 1000))}`}
                    style={{ transform: 'rotate(-90deg)', transformOrigin: '7px 7px', transition: 'stroke-dashoffset 0.8s linear' }}
                  />
                </svg>
                <span className="text-xs tabular-nums" style={{ color: 'var(--text-dim)', minWidth: '2ch' }}>
                  {countdown}s
                </span>
              </div>
            )}
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


          {/* Watchlist picker button */}
          <div className="relative" ref={pickerRef}>
            <button
              onClick={() => setPickerOpen(v => !v)}
              title="Add symbols to your watchlist"
              className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors"
              style={
                pickerOpen || watchlist.length > 0
                  ? { background: 'rgba(251,191,36,0.15)', color: '#fbbf24', border: '1px solid rgba(251,191,36,0.3)' }
                  : { background: 'var(--bg-panel)', color: 'var(--text-muted)', border: '1px solid transparent' }
              }
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>
              Watch{watchlist.length > 0 ? ` (${watchlist.length})` : ''}
            </button>

            {pickerOpen && (
              <div
                className="absolute top-full left-0 z-50 mt-1 rounded-xl overflow-hidden shadow-2xl"
                style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', width: '200px', maxHeight: '320px', overflowY: 'auto' }}
              >
                {/* Futures group */}
                {allSymbols.filter(s => s.ticker.startsWith('/')).length > 0 && (
                  <>
                    <div className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest" style={{ color: 'var(--text-dim)', background: 'var(--bg-base)' }}>
                      Futures
                    </div>
                    {allSymbols.filter(s => s.ticker.startsWith('/')).map(s => (
                      <button
                        key={s.ticker}
                        onClick={() => toggleWatchlist(s.ticker)}
                        className="w-full flex items-center justify-between px-3 py-2 text-xs text-left transition-colors"
                        style={{
                          color: watchlist.includes(s.ticker) ? '#fbbf24' : 'var(--text-primary)',
                          background: watchlist.includes(s.ticker) ? 'rgba(251,191,36,0.08)' : 'transparent',
                        }}
                        onMouseEnter={e => { if (!watchlist.includes(s.ticker)) e.currentTarget.style.background = 'var(--bg-row)' }}
                        onMouseLeave={e => { if (!watchlist.includes(s.ticker)) e.currentTarget.style.background = 'transparent' }}
                      >
                        <span className="font-semibold">{s.ticker}</span>
                        {watchlist.includes(s.ticker) && <span style={{ color: '#fbbf24' }}>✓</span>}
                      </button>
                    ))}
                  </>
                )}
                {/* Sectors group */}
                {allSymbols.filter(s => SECTOR_TICKERS.has(s.ticker)).length > 0 && (
                  <>
                    <div className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest" style={{ color: 'var(--text-dim)', background: 'var(--bg-base)' }}>
                      Sectors / ETFs
                    </div>
                    {allSymbols.filter(s => SECTOR_TICKERS.has(s.ticker)).map(s => (
                      <button
                        key={s.ticker}
                        onClick={() => toggleWatchlist(s.ticker)}
                        className="w-full flex items-center justify-between px-3 py-2 text-xs text-left transition-colors"
                        style={{
                          color: watchlist.includes(s.ticker) ? '#fbbf24' : 'var(--text-primary)',
                          background: watchlist.includes(s.ticker) ? 'rgba(251,191,36,0.08)' : 'transparent',
                        }}
                        onMouseEnter={e => { if (!watchlist.includes(s.ticker)) e.currentTarget.style.background = 'var(--bg-row)' }}
                        onMouseLeave={e => { if (!watchlist.includes(s.ticker)) e.currentTarget.style.background = 'transparent' }}
                      >
                        <span className="font-semibold">{s.ticker}</span>
                        {watchlist.includes(s.ticker) && <span style={{ color: '#fbbf24' }}>✓</span>}
                      </button>
                    ))}
                  </>
                )}
                {/* Equities group */}
                {allSymbols.filter(s => !s.ticker.startsWith('/') && !SECTOR_TICKERS.has(s.ticker)).length > 0 && (
                  <>
                    <div className="px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest" style={{ color: 'var(--text-dim)', background: 'var(--bg-base)' }}>
                      Equities
                    </div>
                    {allSymbols.filter(s => !s.ticker.startsWith('/') && !SECTOR_TICKERS.has(s.ticker)).map(s => (
                      <button
                        key={s.ticker}
                        onClick={() => toggleWatchlist(s.ticker)}
                        className="w-full flex items-center justify-between px-3 py-2 text-xs text-left transition-colors"
                        style={{
                          color: watchlist.includes(s.ticker) ? '#fbbf24' : 'var(--text-primary)',
                          background: watchlist.includes(s.ticker) ? 'rgba(251,191,36,0.08)' : 'transparent',
                        }}
                        onMouseEnter={e => { if (!watchlist.includes(s.ticker)) e.currentTarget.style.background = 'var(--bg-row)' }}
                        onMouseLeave={e => { if (!watchlist.includes(s.ticker)) e.currentTarget.style.background = 'transparent' }}
                      >
                        <span className="font-semibold">{s.ticker}</span>
                        {watchlist.includes(s.ticker) && <span style={{ color: '#fbbf24' }}>✓</span>}
                      </button>
                    ))}
                  </>
                )}
              </div>
            )}
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
            />
          )}
        </div>

        {/* Entry Log — fixed overlay panel, shown when Log button is active */}
        <EntryLog visible={showLog} onClose={() => setShowLog(false)} />
      </div>

      {/* Economic Briefing modal */}
      {briefingOpen && <BriefingModal onClose={() => setBriefingOpen(false)} />}

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

