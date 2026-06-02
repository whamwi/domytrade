'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'

type Tab = 'signin' | 'signup'

export default function LoginPage() {
  const router = useRouter()
  const [tab, setTab] = useState<Tab>('signin')

  // Sign-in state
  const [siEmail, setSiEmail]       = useState('')
  const [siPassword, setSiPassword] = useState('')

  // Sign-up state
  const [suName, setSuName]         = useState('')
  const [suEmail, setSuEmail]       = useState('')
  const [suPassword, setSuPassword] = useState('')
  const [suPhone, setSuPhone]       = useState('')

  const [error, setError]     = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // ── Sign In ────────────────────────────────────────────────────────────────
  async function handleSignIn(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const { error: authError } = await supabase.auth.signInWithPassword({
        email: siEmail,
        password: siPassword,
      })
      if (authError) { setError(authError.message); return }

      const { data: { session } } = await supabase.auth.getSession()
      if (!session) { setError('Session error — please try again.'); return }

      const { data: profile } = await supabase
        .from('user_profiles')
        .select('status')
        .eq('id', session.user.id)
        .single()

      if (!profile || profile.status === 'pending_verification') {
        setError('Please verify your email first.')
        await supabase.auth.signOut()
        return
      }
      if (profile.status === 'pending_approval') {
        await supabase.auth.signOut()
        router.push('/pending-approval')
        return
      }
      if (profile.status === 'rejected') {
        setError('Your access request was not approved. Contact support.')
        await supabase.auth.signOut()
        return
      }

      router.push('/dashboard')
    } catch {
      setError('Unexpected error — please try again.')
    } finally {
      setLoading(false)
    }
  }

  // ── Sign Up ────────────────────────────────────────────────────────────────
  async function handleSignUp(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSuccess(null)
    setLoading(true)
    try {
      const { data, error: authError } = await supabase.auth.signUp({
        email: suEmail,
        password: suPassword,
        options: {
          emailRedirectTo: `${window.location.origin}/auth/callback`,
          data: { full_name: suName, phone: suPhone || null },
        },
      })
      if (authError) { setError(authError.message); return }
      if (!data.user) { setError('Sign up failed — please try again.'); return }

      // Create profile row (pending_verification until email confirmed)
      const { error: profileError } = await supabase.from('user_profiles').insert({
        id: data.user.id,
        full_name: suName,
        email: suEmail,
        phone: suPhone || null,
        status: 'pending_verification',
      })
      if (profileError && profileError.code !== '23505') {
        console.error('Profile insert error:', profileError)
      }

      setSuccess(`Verification email sent to ${suEmail}. Click the link to confirm your email, then wait for admin approval.`)
    } catch {
      setError('Unexpected error — please try again.')
    } finally {
      setLoading(false)
    }
  }

  // ── Google OAuth ───────────────────────────────────────────────────────────
  async function handleGoogle() {
    setError(null)
    await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: `${window.location.origin}/auth/callback` },
    })
  }

  return (
    <div
      className="min-h-screen flex items-center justify-center"
      style={{ background: 'var(--bg-base)' }}
    >
      <div
        className="w-full max-w-sm rounded-xl border p-8"
        style={{ background: 'var(--bg-panel)', borderColor: 'var(--border)' }}
      >
        {/* Logo */}
        <div className="flex items-center justify-center gap-2 mb-6">
          <span className="text-2xl font-bold tracking-widest" style={{ color: 'var(--accent-blue)' }}>◈</span>
          <span className="text-lg font-bold tracking-widest uppercase" style={{ color: 'var(--text-primary)', letterSpacing: '0.2em' }}>
            DoMyTrade
          </span>
        </div>
        <p className="text-center text-xs uppercase tracking-widest mb-6" style={{ color: 'var(--text-muted)' }}>
          Trading Signals Dashboard
        </p>

        {/* Tabs */}
        <div className="flex rounded-lg overflow-hidden mb-6" style={{ background: 'var(--bg-row)', border: '1px solid var(--border)' }}>
          {(['signin', 'signup'] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => { setTab(t); setError(null); setSuccess(null) }}
              className="flex-1 py-2 text-xs uppercase tracking-wider font-semibold transition-colors"
              style={{
                background: tab === t ? 'var(--accent-blue)' : 'transparent',
                color: tab === t ? '#fff' : 'var(--text-muted)',
              }}
            >
              {t === 'signin' ? 'Sign In' : 'Request Access'}
            </button>
          ))}
        </div>

        {/* ── Sign In Form ── */}
        {tab === 'signin' && (
          <form onSubmit={handleSignIn} className="flex flex-col gap-4">
            <Field label="Email" id="si-email" type="email" autoComplete="email"
              value={siEmail} onChange={setSiEmail} />
            <Field label="Password" id="si-password" type="password" autoComplete="current-password"
              value={siPassword} onChange={setSiPassword} />
            {error && <ErrorBox msg={error} />}
            <SubmitBtn loading={loading} label="Sign In" />
          </form>
        )}

        {/* ── Sign Up Form ── */}
        {tab === 'signup' && (
          <form onSubmit={handleSignUp} className="flex flex-col gap-4">
            <Field label="Full Name" id="su-name" type="text" autoComplete="name"
              value={suName} onChange={setSuName} />
            <Field label="Email" id="su-email" type="email" autoComplete="email"
              value={suEmail} onChange={setSuEmail} />
            <Field label="Password" id="su-password" type="password" autoComplete="new-password"
              value={suPassword} onChange={setSuPassword} />
            <Field label="Phone (optional)" id="su-phone" type="tel" autoComplete="tel"
              value={suPhone} onChange={setSuPhone} required={false} />
            {error && <ErrorBox msg={error} />}
            {success
              ? <div className="text-xs rounded-lg px-3 py-2" style={{ background: 'rgba(34,197,94,0.1)', color: '#4ade80' }}>{success}</div>
              : <SubmitBtn loading={loading} label="Request Access" />
            }
          </form>
        )}

        {/* Google */}
        {!success && (
          <>
            <div className="flex items-center gap-3 my-5">
              <div className="flex-1 h-px" style={{ background: 'var(--border)' }} />
              <span className="text-xs" style={{ color: 'var(--text-dim)' }}>or</span>
              <div className="flex-1 h-px" style={{ background: 'var(--border)' }} />
            </div>
            <button
              type="button" onClick={handleGoogle}
              className="w-full flex items-center justify-center gap-2 rounded-lg border px-4 py-2.5 text-sm font-medium transition-opacity hover:opacity-80"
              style={{ borderColor: 'var(--border)', color: 'var(--text-primary)', background: 'var(--bg-row)' }}
            >
              <svg width="18" height="18" viewBox="0 0 24 24">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"/>
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
              </svg>
              Continue with Google
            </button>
          </>
        )}
      </div>
    </div>
  )
}

function Field({
  label, id, type, autoComplete, value, onChange, required = true,
}: {
  label: string; id: string; type: string; autoComplete: string;
  value: string; onChange: (v: string) => void; required?: boolean
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
        {label}
      </label>
      <input
        id={id} type={type} autoComplete={autoComplete}
        required={required} value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border px-3 py-2.5 text-sm outline-none transition-colors"
        style={{ background: 'var(--bg-row)', borderColor: 'var(--border)', color: 'var(--text-primary)' }}
        onFocus={(e) => { e.currentTarget.style.borderColor = 'var(--accent-blue)' }}
        onBlur={(e)  => { e.currentTarget.style.borderColor = 'var(--border)' }}
      />
    </div>
  )
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <p className="text-xs rounded-lg px-3 py-2" style={{ background: 'var(--red-bg)', color: '#f87171' }}>
      {msg}
    </p>
  )
}

function SubmitBtn({ loading, label }: { loading: boolean; label: string }) {
  return (
    <button
      type="submit" disabled={loading}
      className="mt-2 w-full rounded-lg px-4 py-2.5 text-sm font-semibold uppercase tracking-wider transition-opacity disabled:opacity-50"
      style={{ background: 'var(--accent-blue)', color: '#fff' }}
    >
      {loading ? 'Please wait…' : label}
    </button>
  )
}
