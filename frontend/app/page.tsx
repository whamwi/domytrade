'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'
import './landing.css'

import TickerTape from './landing/components/TickerTape.jsx'
import Nav        from './landing/components/Nav.jsx'
import Hero       from './landing/components/Hero.jsx'
import Terminal   from './landing/components/Terminal.jsx'
import AssetStrip from './landing/components/AssetStrip.jsx'
import Strategies from './landing/components/Strategies.jsx'
import Models     from './landing/components/Models.jsx'
import Features   from './landing/components/Features.jsx'
import MarketProfile from './landing/components/MarketProfile.jsx'
import CtaBand    from './landing/components/CtaBand.jsx'
import Footer     from './landing/components/Footer.jsx'
import useReveal  from './landing/hooks/useReveal.js'

export default function HomePage() {
  const router = useRouter()
  useReveal()

  // Redirect approved users straight to dashboard
  useEffect(() => {
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (!session) return
      const { data: profile } = await supabase
        .from('user_profiles').select('status').eq('id', session.user.id).single()
      if (profile?.status === 'approved') router.replace('/dashboard')
    })
  }, [router])

  return (
    <>
      <TickerTape />
      <Nav />
      <Hero headline="Trade the |Volatility| with AI." />
      <section className="term-section" id="terminal">
        <div className="term-shell reveal">
          <Terminal />
        </div>
      </section>
      <AssetStrip />
      <Strategies />
      <Models />
      <Features />
      <MarketProfile />
      <CtaBand />
      <Footer />
    </>
  )
}
