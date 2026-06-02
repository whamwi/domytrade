'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'

export default function PendingApprovalPage() {
  const router = useRouter()
  const [email, setEmail] = useState('')

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (!session) {
        router.replace('/login')
        return
      }
      setEmail(session.user.email ?? '')

      // Poll every 10s — if approved, redirect to dashboard
      const interval = setInterval(async () => {
        const { data: profile } = await supabase
          .from('user_profiles')
          .select('status')
          .eq('id', session.user.id)
          .single()

        if (profile?.status === 'approved') {
          clearInterval(interval)
          router.replace('/dashboard')
        }
      }, 10000)

      return () => clearInterval(interval)
    })
  }, [router])

  return (
    <div
      className="min-h-screen flex items-center justify-center"
      style={{ background: 'var(--bg-base)' }}
    >
      <div
        className="w-full max-w-md rounded-xl border p-8 text-center"
        style={{ background: 'var(--bg-panel)', borderColor: 'var(--border)' }}
      >
        {/* Logo */}
        <div className="flex items-center justify-center gap-2 mb-8">
          <span className="text-2xl font-bold tracking-widest" style={{ color: 'var(--accent-blue)' }}>◈</span>
          <span className="text-lg font-bold tracking-widest uppercase" style={{ color: 'var(--text-primary)', letterSpacing: '0.2em' }}>
            DoMyTrade
          </span>
        </div>

        {/* Pending icon */}
        <div
          className="w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-6"
          style={{ background: 'rgba(251,191,36,0.1)', border: '1px solid rgba(251,191,36,0.3)' }}
        >
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#fbbf24" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <polyline points="12 6 12 12 16 14" />
          </svg>
        </div>

        <h2 className="text-lg font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>
          Access Request Submitted
        </h2>

        <p className="text-sm mb-6" style={{ color: 'var(--text-muted)' }}>
          Your email{email ? ` (${email})` : ''} has been verified. Your request is pending admin approval.
          You&apos;ll receive a confirmation email once access is granted.
        </p>

        <div
          className="rounded-lg px-4 py-3 text-xs"
          style={{ background: 'var(--bg-row)', color: 'var(--text-dim)' }}
        >
          This page checks automatically. You can close it and wait for the email.
        </div>

        <button
          onClick={() => supabase.auth.signOut().then(() => router.replace('/login'))}
          className="mt-6 text-xs uppercase tracking-wider transition-opacity hover:opacity-70"
          style={{ color: 'var(--text-dim)' }}
        >
          Sign out
        </button>
      </div>
    </div>
  )
}
