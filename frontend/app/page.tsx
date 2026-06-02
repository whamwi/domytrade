'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'

const API = process.env.NEXT_PUBLIC_API_URL || ''

// ── Types ────────────────────────────────────────────────────────────────────
interface LiveStats {
  signals: number
  bullCount: number
  bearCount: number
  fearIndex: number | null
  assetsTracked: number
}

// ── FX Ticker Tape ───────────────────────────────────────────────────────────
function TickerTape({ pairs }: { pairs: { name: string; rate: number; change_pct: number }[] }) {
  if (!pairs.length) return null
  const items = [...pairs, ...pairs] // duplicate for seamless loop
  return (
    <div className="overflow-hidden border-b" style={{ background: 'var(--bg-panel)', borderColor: 'var(--border)', height: 28 }}>
      <div className="flex items-center h-full gap-8 animate-ticker whitespace-nowrap px-4" style={{ display: 'flex' }}>
        {items.map((p, i) => (
          <span key={i} className="text-xs flex items-center gap-2 shrink-0">
            <span style={{ color: 'var(--text-muted)' }}>{p.name}</span>
            <span style={{ color: 'var(--text-primary)' }}>{p.rate.toFixed(4)}</span>
            <span style={{ color: p.change_pct >= 0 ? '#22c55e' : '#ef4444' }}>
              {p.change_pct >= 0 ? '+' : ''}{p.change_pct.toFixed(2)}%
            </span>
          </span>
        ))}
      </div>
    </div>
  )
}

// ── Navbar ───────────────────────────────────────────────────────────────────
function Navbar({ onSignIn, onCreateAccount }: { onSignIn: () => void; onCreateAccount: () => void }) {
  return (
    <nav className="sticky top-0 z-50 flex items-center justify-between px-6 py-3 border-b"
      style={{ background: 'rgba(13,15,20,0.95)', borderColor: 'var(--border)', backdropFilter: 'blur(12px)' }}>
      <div className="flex items-center gap-2">
        <span className="text-xl font-bold" style={{ color: 'var(--accent-blue)' }}>◈</span>
        <span className="text-sm font-bold tracking-widest uppercase" style={{ color: 'var(--text-primary)' }}>
          DOMY<span style={{ color: 'var(--accent-blue)' }}>TRADE</span>
        </span>
      </div>
      <div className="flex items-center gap-3">
        <button onClick={onSignIn}
          className="px-4 py-1.5 text-sm font-medium transition-opacity hover:opacity-80"
          style={{ color: 'var(--text-muted)' }}>
          Sign in
        </button>
        <button onClick={onCreateAccount}
          className="px-4 py-1.5 text-sm font-semibold rounded-lg transition-opacity hover:opacity-90"
          style={{ background: 'var(--accent-blue)', color: '#fff' }}>
          Create account
        </button>
      </div>
    </nav>
  )
}

// ── Stats Card ───────────────────────────────────────────────────────────────
function StatCard({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="rounded-xl p-5 border" style={{ background: 'var(--bg-panel)', borderColor: 'var(--border)' }}>
      <p className="text-xs uppercase tracking-widest mb-2" style={{ color: 'var(--text-muted)' }}>{label}</p>
      <div className="text-3xl font-bold">{value}</div>
      {sub && <p className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>{sub}</p>}
    </div>
  )
}

// ── Feature Card ─────────────────────────────────────────────────────────────
function FeatureCard({ icon, title, desc }: { icon: React.ReactNode; title: string; desc: string }) {
  return (
    <div className="flex flex-col gap-3 p-5 rounded-xl border" style={{ background: 'var(--bg-panel)', borderColor: 'var(--border)' }}>
      <div className="w-10 h-10 rounded-lg flex items-center justify-center"
        style={{ background: 'rgba(59,130,246,0.15)' }}>
        {icon}
      </div>
      <h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>{title}</h3>
      <p className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>{desc}</p>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function HomePage() {
  const router = useRouter()
  const [fxPairs, setFxPairs] = useState<{ name: string; rate: number; change_pct: number }[]>([])
  const [stats, setStats] = useState<LiveStats>({ signals: 30, bullCount: 12, bearCount: 18, fearIndex: null, assetsTracked: 240 })

  // Redirect if already logged in and approved
  useEffect(() => {
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (!session) return
      const { data: profile } = await supabase
        .from('user_profiles').select('status').eq('id', session.user.id).single()
      if (profile?.status === 'approved') router.replace('/dashboard')
    })
  }, [router])

  // Fetch live stats
  useEffect(() => {
    fetch(`${API}/api/signals`)
      .then(r => r.json())
      .then(signals => {
        const longs = signals.filter((s: { direction: string }) => s.direction === 'LONG').length
        const shorts = signals.filter((s: { direction: string }) => s.direction === 'SHORT').length
        setStats(prev => ({ ...prev, signals: signals.length, bullCount: longs, bearCount: shorts }))
      }).catch(() => {})

    fetch(`${API}/api/global-markets`)
      .then(r => r.json())
      .then(d => { if (d.fx) setFxPairs(d.fx) })
      .catch(() => {})

    fetch(`${API}/api/market-bias`)
      .then(r => r.json())
      .then(d => {
        const vix = d.find((x: { ticker: string; value?: number }) => x.ticker === 'VIX')
        if (vix?.value) setStats(prev => ({ ...prev, fearIndex: vix.value }))
      }).catch(() => {})
  }, [])

  const goSignIn     = () => router.push('/login')
  const goCreate     = () => router.push('/login?tab=signup')

  return (
    <div style={{ background: 'var(--bg-base)', color: 'var(--text-primary)', minHeight: '100vh' }}>
      <style>{`
        @keyframes ticker { from { transform: translateX(0); } to { transform: translateX(-50%); } }
        .animate-ticker { animation: ticker 20s linear infinite; }
      `}</style>

      <TickerTape pairs={fxPairs} />
      <Navbar onSignIn={goSignIn} onCreateAccount={goCreate} />

      {/* ── Hero ── */}
      <section className="px-6 pt-16 pb-12 max-w-2xl mx-auto text-center">
        <div className="inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs mb-8 border"
          style={{ background: 'rgba(59,130,246,0.1)', borderColor: 'rgba(59,130,246,0.3)', color: 'var(--accent-blue)' }}>
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 inline-block animate-pulse" />
          AI SIGNAL ENGINE · {stats.signals} ACTIVE SETUPS
        </div>

        <h1 className="text-4xl font-bold leading-tight mb-4" style={{ color: 'var(--text-primary)' }}>
          Trade the{' '}
          <span style={{ color: 'var(--accent-blue)', textDecoration: 'underline', textDecorationStyle: 'wavy', textUnderlineOffset: 6 }}>
            Volatility
          </span>{' '}
          with AI.
        </h1>

        <p className="text-sm leading-relaxed mb-8" style={{ color: 'var(--text-muted)' }}>
          AI-scored buy & sell signals across <strong style={{ color: 'var(--text-primary)' }}>Market Profile</strong>,{' '}
          <strong style={{ color: 'var(--text-primary)' }}>asset volatility</strong>, and your{' '}
          <strong style={{ color: 'var(--text-primary)' }}>trading time frame</strong> — each setup ships with entry, stop and target.
          Across futures, equities and FX.
        </p>

        <div className="flex items-center justify-center gap-3 mb-4">
          <button onClick={goCreate}
            className="px-6 py-3 rounded-lg font-semibold text-sm transition-opacity hover:opacity-90"
            style={{ background: 'var(--accent-blue)', color: '#fff' }}>
            Create account →
          </button>
          <button onClick={goSignIn}
            className="px-6 py-3 rounded-lg font-semibold text-sm border transition-opacity hover:opacity-80"
            style={{ borderColor: 'var(--border)', color: 'var(--text-primary)', background: 'var(--bg-panel)' }}>
            View live signals
          </button>
        </div>
        <p className="text-xs" style={{ color: 'var(--text-dim)' }}>
          No card required · Streaming since pre-market · Cancel anytime
        </p>
      </section>

      {/* ── Live Stats ── */}
      <section className="px-6 pb-16 max-w-2xl mx-auto grid grid-cols-2 gap-3">
        <StatCard label="Active Signals" value={<span style={{ color: 'var(--text-primary)' }}>{stats.signals}</span>} />
        <StatCard label="Bull / Bear"
          value={
            <span>
              <span style={{ color: '#22c55e' }}>{stats.bullCount}</span>
              <span style={{ color: 'var(--text-dim)' }}> / </span>
              <span style={{ color: '#ef4444' }}>{stats.bearCount}</span>
            </span>
          }
        />
        <StatCard label="Fear Index"
          value={<span style={{ color: '#f59e0b' }}>{stats.fearIndex != null ? stats.fearIndex.toFixed(2) : '—'}</span>}
        />
        <StatCard label="Assets Tracked" value={<span style={{ color: 'var(--text-primary)' }}>{stats.assetsTracked}+</span>} />
      </section>

      {/* ── App Preview ── */}
      <section className="px-4 pb-16 max-w-2xl mx-auto">
        <div className="rounded-2xl border overflow-hidden" style={{ borderColor: 'var(--border)', background: 'var(--bg-panel)' }}>
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'var(--border)' }}>
            <div className="flex items-center gap-2">
              <span style={{ color: 'var(--accent-blue)' }}>◈</span>
              <span className="text-xs font-bold tracking-widest uppercase">DOMYTRADE</span>
              <span className="px-2 py-0.5 rounded text-xs font-semibold"
                style={{ background: 'rgba(59,130,246,0.2)', color: 'var(--accent-blue)' }}>PRE-MARKET</span>
            </div>
            <div className="flex items-center gap-1.5 text-xs" style={{ color: '#22c55e' }}>
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 inline-block animate-pulse" />
              LIVE
            </div>
          </div>
          {/* Filter pills */}
          <div className="px-4 py-2 flex flex-wrap gap-1.5 border-b" style={{ borderColor: 'var(--border)' }}>
            {['ALL', 'LONGS', 'SHORTS'].map(f => (
              <span key={f} className="px-2.5 py-0.5 rounded text-xs font-medium"
                style={{ background: f === 'ALL' ? 'var(--accent-blue)' : 'var(--bg-row)', color: f === 'ALL' ? '#fff' : 'var(--text-muted)' }}>
                {f}
              </span>
            ))}
            <span className="px-2.5 py-0.5 rounded text-xs font-medium ml-1"
              style={{ background: 'var(--bg-row)', color: 'var(--text-muted)' }}>ALL MODELS</span>
            {['AGGRO', 'CONSERV', 'WIDE'].map(f => (
              <span key={f} className="px-2.5 py-0.5 rounded text-xs font-medium"
                style={{ background: 'var(--bg-row)', color: 'var(--text-muted)' }}>{f}</span>
            ))}
          </div>
          <div className="px-4 py-2 flex gap-1.5 border-b" style={{ borderColor: 'var(--border)' }}>
            {['ALL ASSETS', 'EQUITIES', 'FUTURES'].map(f => (
              <span key={f} className="px-2.5 py-0.5 rounded text-xs font-medium"
                style={{ background: f === 'ALL ASSETS' ? 'var(--bg-row-hover)' : 'transparent', color: f === 'ALL ASSETS' ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                {f}
              </span>
            ))}
          </div>
          {/* Signal rows (static preview) */}
          {[
            { sym: '/NQ', price: '30,568.25', chg: '+2.000 (+0.01%)', up: true },
            { sym: '/ES', price: '7,601.50',  chg: '-11.75 (-0.15%)', up: false },
            { sym: '/YM', price: '50,919.00', chg: '-215.00 (-0.42%)', up: false },
            { sym: '/GC', price: '4,557.40',  chg: '+51.10 (+1.13%)', up: true },
            { sym: '/CL', price: '62.10',     chg: '-0.80 (-1.27%)', up: false },
          ].map((r, i) => (
            <div key={i} className="flex items-center justify-between px-4 py-2.5 border-b text-xs"
              style={{ borderColor: 'var(--border)', background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.01)' }}>
              <div className="flex items-center gap-3">
                <span style={{ color: 'var(--text-dim)' }}>{i + 1}</span>
                <span className="font-semibold" style={{ color: 'var(--text-primary)' }}>{r.sym}</span>
              </div>
              <span style={{ color: 'var(--text-muted)' }}>{r.price}</span>
              <span style={{ color: r.up ? '#22c55e' : '#ef4444' }}>{r.chg}</span>
            </div>
          ))}
          {/* CTA row */}
          <div className="p-4">
            <button onClick={goCreate}
              className="w-full py-2.5 rounded-lg text-sm font-semibold transition-opacity hover:opacity-90"
              style={{ background: 'var(--accent-blue)', color: '#fff' }}>
              Open the live terminal →
            </button>
          </div>
        </div>
      </section>

      {/* ── How it works ── */}
      <section className="px-6 pb-16 max-w-2xl mx-auto">
        <div className="grid gap-4">
          {[
            {
              num: '01', title: 'Market Profile',
              desc: 'Value area, point of control and acceptance zones tell us whether price is fair, stretched, or rejecting. Each setup is tagged Good, Neutral or Avoid.',
              icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>,
            },
            {
              num: '02', title: 'Asset Volatility',
              desc: 'Live realized vs. typical range sizes every stop and target. The daily swing meter shows exactly how much of the day\'s expected move is already spent.',
              icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>,
            },
            {
              num: '03', title: 'Trading Time Frame',
              desc: 'Scalp, intraday or swing — pick your horizon and signals recalibrate. Pre-market, regular and after-hours sessions are scored independently.',
              icon: <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>,
            },
          ].map((item) => (
            <div key={item.num} className="rounded-xl border p-5" style={{ background: 'var(--bg-panel)', borderColor: 'var(--border)' }}>
              <div className="flex items-start gap-4">
                <div>
                  <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{item.num}</span>
                  <div className="w-9 h-9 rounded-lg flex items-center justify-center mt-1"
                    style={{ background: 'rgba(59,130,246,0.15)' }}>
                    {item.icon}
                  </div>
                </div>
                <div>
                  <h3 className="text-sm font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>{item.title}</h3>
                  <p className="text-xs leading-relaxed" style={{ color: 'var(--text-muted)' }}>{item.desc}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Everything you need ── */}
      <section className="px-6 pb-16 max-w-2xl mx-auto">
        <p className="text-xs uppercase tracking-widest mb-3 text-center" style={{ color: 'var(--accent-blue)' }}>
          BUILT FOR THE OPEN
        </p>
        <h2 className="text-2xl font-bold text-center mb-8" style={{ color: 'var(--text-primary)' }}>
          Everything you need on the chart,<br />nothing you don&apos;t.
        </h2>
        <div className="grid grid-cols-1 gap-3">
          <FeatureCard
            icon={<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>}
            title="Real-time alerts"
            desc="ENTRY and NEAR pings the instant a setup arms — push, email or in-terminal."
          />
          <FeatureCard
            icon={<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>}
            title="Entry · Stop · Target"
            desc="Every signal ships a complete plan with a pre-computed risk/reward ratio."
          />
          <FeatureCard
            icon={<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>}
            title="Daily swing meter"
            desc="See how much of the expected range is spent before you ever take the trade."
          />
          <FeatureCard
            icon={<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" strokeWidth="2"><rect x="2" y="3" width="7" height="9"/><rect x="15" y="3" width="7" height="5"/><rect x="15" y="12" width="7" height="9"/><rect x="2" y="16" width="7" height="5"/></svg>}
            title="Futures, equities & FX"
            desc="240+ instruments and 11 sectors, scored on one consistent framework."
          />
        </div>
      </section>

      {/* ── Final CTA ── */}
      <section className="px-6 pb-16 max-w-2xl mx-auto text-center">
        <div className="rounded-2xl border p-10" style={{ background: 'var(--bg-panel)', borderColor: 'var(--border)' }}>
          <p className="text-xs uppercase tracking-widest mb-3" style={{ color: 'var(--accent-blue)' }}>
            BUILT FOR THE OPEN
          </p>
          <h2 className="text-2xl font-bold mb-3" style={{ color: 'var(--text-primary)' }}>
            Trade the volatility,<br />not the noise.
          </h2>
          <p className="text-sm mb-8 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
            Create a free account and watch the signal engine score the open in real time. No card, no commitment.
          </p>
          <div className="flex flex-col gap-3 items-center">
            <button onClick={goCreate}
              className="w-full max-w-xs py-3 rounded-lg font-semibold text-sm transition-opacity hover:opacity-90"
              style={{ background: 'var(--accent-blue)', color: '#fff' }}>
              Create account →
            </button>
            <button onClick={goSignIn}
              className="w-full max-w-xs py-3 rounded-lg font-semibold text-sm border transition-opacity hover:opacity-80"
              style={{ borderColor: 'var(--border)', color: 'var(--text-primary)', background: 'transparent' }}>
              Explore the terminal
            </button>
          </div>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="border-t px-6 py-10 max-w-2xl mx-auto" style={{ borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-2 mb-2">
          <span style={{ color: 'var(--accent-blue)' }}>◈</span>
          <span className="text-sm font-bold tracking-widest uppercase">
            DOMY<span style={{ color: 'var(--accent-blue)' }}>TRADE</span>
          </span>
        </div>
        <p className="text-xs mb-8" style={{ color: 'var(--text-muted)' }}>
          Market-profile, volatility and time-frame signals for traders who live at the open.
        </p>
        <div className="grid grid-cols-3 gap-6 mb-8">
          {[
            { heading: 'PRODUCT', links: ['Live signals', 'Strategies', 'Models', 'Market Profile', 'Changelog'] },
            { heading: 'MARKETS', links: ['Futures', 'Equities', 'FX', 'Sectors', 'Fear index'] },
            { heading: 'COMPANY', links: ['About', 'Methodology', 'Contact', 'Status'] },
          ].map(col => (
            <div key={col.heading}>
              <p className="text-xs font-semibold mb-3 uppercase tracking-widest" style={{ color: 'var(--text-dim)' }}>
                {col.heading}
              </p>
              {col.links.map(link => (
                <p key={link} className="text-xs mb-2 cursor-pointer hover:opacity-80 transition-opacity"
                  style={{ color: 'var(--text-muted)' }}>{link}</p>
              ))}
            </div>
          ))}
        </div>
        <div className="border-t pt-6" style={{ borderColor: 'var(--border)' }}>
          <p className="text-xs mb-3" style={{ color: 'var(--text-dim)' }}>
            © 2026 Domytrade. Signals are informational and not investment advice. Trading futures, equities and FX involves substantial risk of loss.
          </p>
          <div className="flex gap-4">
            {['Terms', 'Privacy', 'Disclosures'].map(l => (
              <span key={l} className="text-xs cursor-pointer hover:opacity-80" style={{ color: 'var(--text-muted)' }}>{l}</span>
            ))}
          </div>
        </div>
      </footer>
    </div>
  )
}
