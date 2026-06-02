'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import GlobalMarketsStrip from './dashboard/components/GlobalMarketsStrip'
import SignalTable, { Signal, SymbolInfo } from './dashboard/components/SignalTable'

const API = process.env.NEXT_PUBLIC_API_URL ?? ''

// ─── Navbar ──────────────────────────────────────────────────────────────────
function Navbar({ onSignIn, onCreate }: { onSignIn: () => void; onCreate: () => void }) {
  return (
    <nav style={{
      position: 'sticky', top: 0, zIndex: 50,
      background: 'rgba(13,15,20,0.92)', backdropFilter: 'blur(12px)',
      borderBottom: '1px solid var(--border)',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '0 24px', height: 52,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ color: 'var(--accent-blue)', fontSize: 20, fontWeight: 700 }}>◈</span>
        <span style={{ fontWeight: 700, fontSize: 13, letterSpacing: '0.18em', textTransform: 'uppercase' }}>
          DOMY<span style={{ color: 'var(--accent-blue)' }}>TRADE</span>
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button onClick={onSignIn} style={{
          background: 'none', border: 'none', cursor: 'pointer', padding: '6px 14px',
          color: 'var(--text-muted)', fontSize: 13, fontWeight: 500,
        }}>Sign in</button>
        <button onClick={onCreate} style={{
          background: 'var(--accent-blue)', border: 'none', cursor: 'pointer',
          padding: '7px 16px', borderRadius: 8, color: '#fff',
          fontSize: 13, fontWeight: 600,
        }}>Create account</button>
      </div>
    </nav>
  )
}

// ─── Hero ─────────────────────────────────────────────────────────────────────
function Hero({ signalCount, onCreate, onSignIn }: { signalCount: number; onCreate: () => void; onSignIn: () => void }) {
  return (
    <div style={{ padding: '64px 24px 40px', maxWidth: 640, margin: '0 auto', textAlign: 'center' }}>
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: 8,
        background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.25)',
        borderRadius: 999, padding: '4px 14px', marginBottom: 28,
      }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#4ade80', display: 'inline-block', animation: 'pulse 2s infinite' }} />
        <span style={{ fontSize: 11, color: 'var(--accent-blue)', letterSpacing: '0.12em', textTransform: 'uppercase' }}>
          AI Signal Engine · {signalCount} Active Setups
        </span>
      </div>

      <h1 style={{ fontSize: 'clamp(32px, 6vw, 52px)', fontWeight: 700, lineHeight: 1.15, marginBottom: 20 }}>
        Trade the{' '}
        <span style={{
          color: 'var(--accent-blue)',
          textDecoration: 'underline', textDecorationStyle: 'wavy',
          textUnderlineOffset: 6, textDecorationColor: 'rgba(59,130,246,0.5)',
        }}>Volatility</span>{' '}
        with AI.
      </h1>

      <p style={{ fontSize: 15, color: 'var(--text-muted)', lineHeight: 1.7, marginBottom: 32 }}>
        AI-scored buy & sell signals across <strong style={{ color: 'var(--text-primary)' }}>Market Profile</strong>,{' '}
        <strong style={{ color: 'var(--text-primary)' }}>asset volatility</strong>, and your{' '}
        <strong style={{ color: 'var(--text-primary)' }}>trading time frame</strong> — each setup ships with entry, stop and target.
      </p>

      <div style={{ display: 'flex', gap: 12, justifyContent: 'center', flexWrap: 'wrap', marginBottom: 16 }}>
        <button onClick={onCreate} style={{
          background: 'var(--accent-blue)', border: 'none', cursor: 'pointer',
          padding: '12px 28px', borderRadius: 10, color: '#fff', fontSize: 14, fontWeight: 600,
        }}>
          Create free account →
        </button>
        <button onClick={onSignIn} style={{
          background: 'var(--bg-panel)', border: '1px solid var(--border)', cursor: 'pointer',
          padding: '12px 28px', borderRadius: 10, color: 'var(--text-primary)', fontSize: 14, fontWeight: 600,
        }}>
          Sign in
        </button>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text-dim)' }}>
        No card required · Live since pre-market · Cancel anytime
      </p>
    </div>
  )
}

// ─── Live Preview wrapper ─────────────────────────────────────────────────────
function LivePreview({ signals, allSymbols, loading, onCreate }: {
  signals: Signal[]; allSymbols: SymbolInfo[]; loading: boolean; onCreate: () => void
}) {
  return (
    <div style={{ position: 'relative', maxWidth: 1400, margin: '0 auto 0', padding: '0 16px 0' }}>
      {/* Dashboard preview container — clipped height to create teaser */}
      <div style={{ position: 'relative', maxHeight: 520, overflow: 'hidden', borderRadius: 12, border: '1px solid var(--border)' }}>
        <SignalTable
          signals={signals}
          allSymbols={allSymbols}
          loading={loading}
          error={null}
          onRetry={() => {}}
          ytdMap={{}}
          personalityData={{}}
          personalityHour={-1}
        />
      </div>

      {/* Gradient fade + CTA overlay */}
      <div style={{
        position: 'absolute', bottom: 0, left: 16, right: 16,
        height: 280,
        background: 'linear-gradient(to bottom, transparent 0%, var(--bg-base) 70%)',
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-end',
        paddingBottom: 32, gap: 12,
        borderRadius: '0 0 12px 12px',
      }}>
        <p style={{ fontSize: 13, color: 'var(--text-muted)', margin: 0 }}>
          {signals.length} live signals across {allSymbols.length}+ instruments
        </p>
        <button onClick={onCreate} style={{
          background: 'var(--accent-blue)', border: 'none', cursor: 'pointer',
          padding: '13px 32px', borderRadius: 10, color: '#fff', fontSize: 14, fontWeight: 600,
          boxShadow: '0 0 32px rgba(59,130,246,0.3)',
        }}>
          Create free account to unlock all signals →
        </button>
        <p style={{ fontSize: 11, color: 'var(--text-dim)', margin: 0 }}>
          No card required · Cancel anytime
        </p>
      </div>
    </div>
  )
}

// ─── Feature pills ────────────────────────────────────────────────────────────
function Features() {
  const items = [
    { icon: '◎', label: 'Entry · Stop · Target', desc: 'Every signal ships a complete plan.' },
    { icon: '⚡', label: 'Real-time alerts', desc: 'ENTRY pings the instant a setup arms.' },
    { icon: '≋', label: 'Market Profile', desc: 'J. Dalton TPO scoring on every trade.' },
    { icon: '◈', label: 'AGG · CON · WIDE', desc: 'Three models, one instrument, your risk.' },
  ]
  return (
    <div style={{ maxWidth: 1200, margin: '48px auto 0', padding: '0 24px 64px', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
      {items.map(f => (
        <div key={f.label} style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', borderRadius: 10, padding: '16px 20px' }}>
          <div style={{ fontSize: 18, marginBottom: 8, color: 'var(--accent-blue)' }}>{f.icon}</div>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4, color: 'var(--text-primary)' }}>{f.label}</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.5 }}>{f.desc}</div>
        </div>
      ))}
    </div>
  )
}

// ─── Footer ───────────────────────────────────────────────────────────────────
function Footer({ onSignIn, onCreate }: { onSignIn: () => void; onCreate: () => void }) {
  return (
    <div style={{ borderTop: '1px solid var(--border)', padding: '24px', textAlign: 'center', color: 'var(--text-dim)', fontSize: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'center', gap: 16, marginBottom: 12 }}>
        <button onClick={onSignIn} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: 12 }}>Sign in</button>
        <button onClick={onCreate} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent-blue)', fontSize: 12, fontWeight: 600 }}>Create account →</button>
      </div>
      © 2026 DoMyTrade. Signals are informational and not investment advice.
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────
export default function HomePage() {
  const router = useRouter()
  const [signals,    setSignals]    = useState<Signal[]>([])
  const [allSymbols, setAllSymbols] = useState<SymbolInfo[]>([])
  const [loading,    setLoading]    = useState(true)

  // Redirect approved users to dashboard
  useEffect(() => {
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (!session) return
      const { data: profile } = await supabase
        .from('user_profiles').select('status').eq('id', session.user.id).single()
      if (profile?.status === 'approved') router.replace('/dashboard')
    })
  }, [router])

  // Fetch live data for preview
  const fetchData = useCallback(async () => {
    try {
      const [sigRes, symRes] = await Promise.all([
        fetch(`${API}/api/signals`),
        fetch(`${API}/api/symbols`),
      ])
      if (sigRes.ok) setSignals(await sigRes.json())
      if (symRes.ok) setAllSymbols(await symRes.json())
    } catch { /* non-fatal */ }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])

  const goSignIn = () => router.push('/login')
  const goCreate = () => router.push('/login?tab=signup')

  return (
    <div style={{ background: 'var(--bg-base)', minHeight: '100vh', color: 'var(--text-primary)' }}>
      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }`}</style>
      <GlobalMarketsStrip />
      <Navbar onSignIn={goSignIn} onCreate={goCreate} />
      <Hero signalCount={signals.length || 30} onCreate={goCreate} onSignIn={goSignIn} />
      <LivePreview signals={signals} allSymbols={allSymbols} loading={loading} onCreate={goCreate} />
      <Features />
      <Footer onSignIn={goSignIn} onCreate={goCreate} />
    </div>
  )
}
