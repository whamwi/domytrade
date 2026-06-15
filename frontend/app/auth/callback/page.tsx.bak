'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'

export default function AuthCallbackPage() {
  const router = useRouter()

  useEffect(() => {
    // Supabase redirects here after email verification with tokens in the URL hash
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (!session) {
        router.replace('/login')
        return
      }

      // Check profile status
      const { data: profile } = await supabase
        .from('user_profiles')
        .select('status')
        .eq('id', session.user.id)
        .single()

      if (!profile) {
        // Profile doesn't exist yet — shouldn't happen but redirect to login
        router.replace('/login')
        return
      }

      if (profile.status === 'approved') {
        router.replace('/dashboard')
      } else {
        // pending_verification or pending_approval
        // Notify backend to send admin approval email (idempotent)
        try {
          await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/auth/notify-admin`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${session.access_token}`,
            },
          })
        } catch {
          // Non-fatal — just redirect to pending page
        }
        router.replace('/pending-approval')
      }
    })
  }, [router])

  return (
    <div
      className="min-h-screen flex items-center justify-center"
      style={{ background: 'var(--bg-base)' }}
    >
      <div className="flex flex-col items-center gap-4">
        <span
          className="text-2xl font-bold tracking-widest"
          style={{ color: 'var(--accent-blue)' }}
        >
          ◈
        </span>
        <p
          className="text-sm uppercase tracking-widest"
          style={{ color: 'var(--text-muted)' }}
        >
          Verifying…
        </p>
      </div>
    </div>
  )
}
