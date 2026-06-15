import { createServerClient } from '@supabase/ssr'
import { cookies } from 'next/headers'
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url)
  const code = searchParams.get('code')

  if (!code) {
    return NextResponse.redirect(new URL('/login?error=no_code', origin))
  }

  const cookieStore = await cookies()

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() { return cookieStore.getAll() },
        setAll(cookiesToSet) {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options)
          )
        },
      },
    }
  )

  const { data: { session }, error } = await supabase.auth.exchangeCodeForSession(code)

  if (error || !session) {
    console.error('OAuth callback error:', error?.message)
    return NextResponse.redirect(new URL('/login?error=auth_failed', origin))
  }

  // Check user profile status
  const { data: profile } = await supabase
    .from('user_profiles')
    .select('status')
    .eq('id', session.user.id)
    .single()

  if (!profile) {
    // New Google user — profile may not exist yet, send to pending
    return NextResponse.redirect(new URL('/pending-approval', origin))
  }

  if (profile.status === 'approved') {
    return NextResponse.redirect(new URL('/dashboard', origin))
  }

  // Notify backend (fire and forget)
  try {
    await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/auth/notify-admin`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${session.access_token}`,
      },
    })
  } catch {
    // Non-fatal
  }

  return NextResponse.redirect(new URL('/pending-approval', origin))
}
