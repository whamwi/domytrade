'use client'

import { useEffect, useState, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import Sidebar from './components/Sidebar'
import SignalTable, { Signal } from './components/SignalTable'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''
const REFRESH_INTERVAL = 60_000
const ERROR_REFRESH_INTERVAL = 5_000

interface ApiResponse {
  signals: Signal[]
  count: number
  longs: number
  shorts: number
  last_updated: string
  status: string
}

type SideFilter = 'all' | 'longs' | 'shorts'
type ModelFilter = 'all' | 'AGG' | 'CON'

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

export default function DashboardPage() {
  const router = useRouter()
  const [authed, setAuthed] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)

  const [data, setData] = useState<ApiResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)

  const [sideFilter, setSideFilter] = useState<SideFilter>('all')
  const [modelFilter, setModelFilter] = useState<ModelFilter>('all')

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

  const fetchSignals = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/api/signals?model=all&side=all`, {
        cache: 'no-store',
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json: ApiResponse = await res.json()
      setData(json)
      setLastUpdated(formatLastUpdated(json.last_updated))
      setError(null)
      setLoading(false)

      timerRef.current = setTimeout(fetchSignals, REFRESH_INTERVAL)
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

  // Filter signals
  const filteredSignals = (data?.signals ?? []).filter((sig) => {
    const sideOk =
      sideFilter === 'all' ||
      (sideFilter === 'longs' && sig.side === 'LONG') ||
      (sideFilter === 'shorts' && sig.side === 'SHORT')
    const modelOk =
      modelFilter === 'all' || sig.model === modelFilter
    return sideOk && modelOk
  })

  const session = getSessionLabel()

  if (!authChecked) return null

  if (!authed) return null

  return (
    <div className="flex h-full min-h-screen" style={{ background: 'var(--bg-base)' }}>
      <Sidebar activeTab="dashboard" />

      {/* Main content */}
      <div className="flex flex-col flex-1 min-w-0">
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
          </div>

          {/* Center: stats chips */}
          <div className="flex items-center gap-3 flex-1 justify-center">
            <Chip label="Signals" value={data?.count ?? 0} />
            <span style={{ color: 'var(--border)' }}>·</span>
            <Chip label="Bull" value={data?.longs ?? 0} color="#4ade80" />
            <span style={{ color: 'var(--border)' }}>·</span>
            <Chip label="Bear" value={data?.shorts ?? 0} color="#f87171" />
          </div>

          {/* Right: last updated + refresh */}
          <div className="flex items-center gap-3">
            {lastUpdated && (
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {lastUpdated}
              </span>
            )}
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
            {(['all', 'AGG', 'CON'] as ModelFilter[]).map((f) => (
              <button
                key={f}
                onClick={() => setModelFilter(f)}
                className="rounded-md px-3 py-1.5 text-xs font-semibold uppercase tracking-wider transition-colors"
                style={
                  modelFilter === f
                    ? { background: 'var(--accent-blue)', color: '#fff' }
                    : { background: 'transparent', color: 'var(--text-muted)' }
                }
              >
                {f === 'all' ? 'All Models' : f === 'AGG' ? 'Aggressive' : 'Conservative'}
              </button>
            ))}
          </div>
        </div>

        {/* Table */}
        <div className="flex-1 overflow-auto">
          <SignalTable
            signals={filteredSignals}
            loading={loading}
            error={error}
            onRetry={handleRefresh}
          />
        </div>
      </div>
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
