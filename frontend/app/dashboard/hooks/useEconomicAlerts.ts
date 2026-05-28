'use client'

import { useEffect, useRef } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export interface EconAlert {
  title: string
  impact: string
  forecast: string
  previous: string
  date: string
  country?: string
}

/** Play a short two-tone alert using the Web Audio API. */
export function playAlertSound() {
  try {
    const ctx = new AudioContext()
    const tones = [880, 1100]
    tones.forEach((freq, i) => {
      const osc  = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.type = 'sine'
      osc.frequency.value = freq
      const start = ctx.currentTime + i * 0.18
      gain.gain.setValueAtTime(0, start)
      gain.gain.linearRampToValueAtTime(0.25, start + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.35)
      osc.start(start)
      osc.stop(start + 0.35)
    })
    setTimeout(() => ctx.close(), 1000)
  } catch {
    // AudioContext blocked — silent fail
  }
}

interface Options {
  onAlert: (ev: EconAlert) => void
}

/**
 * Polls the economic calendar every 30 s and fires a beep + toast
 * for every USD event that is 13–17 minutes away.
 *
 * Using a 30-second polling interval (instead of one-shot setTimeout) makes
 * this robust against:
 *   - page loads after the 15-min mark
 *   - browser tab throttling of long timers
 *   - calendar refreshes mid-session
 */
export function useEconomicAlerts({ onAlert }: Options) {
  const eventsRef  = useRef<EconAlert[]>([])
  const alertedRef = useRef<Set<string>>(new Set())  // keyed by event.date — prevents duplicate alerts
  const onAlertRef = useRef(onAlert)
  onAlertRef.current = onAlert   // always call the latest version

  // Fetch calendar every 30 min to pick up updates
  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API_URL}/api/briefing`, { cache: 'no-store' })
        if (!res.ok) return
        const json = await res.json()
        // All USD events — alert system handles filtering by impact at fire time
        eventsRef.current = (json.events ?? []).filter((e: EconAlert) => e.country === 'USD')
      } catch { /* network error — keep existing events */ }
    }
    load()
    const iv = setInterval(load, 30 * 60_000)
    return () => clearInterval(iv)
  }, [])

  // Check every 30 s: fire alert for any USD event that is 13–17 min away
  useEffect(() => {
    const check = () => {
      const now    = Date.now()
      const toFire: EconAlert[] = []

      for (const ev of eventsRef.current) {
        // Only alert on High and Medium impact events
        if (ev.impact !== 'High' && ev.impact !== 'Medium') continue
        if (alertedRef.current.has(ev.date)) continue

        const minUntil = (new Date(ev.date).getTime() - now) / 60_000

        // Alert window: 13 → 17 minutes before the event (wide window catches a 30-s check interval)
        if (minUntil >= 13 && minUntil <= 17) {
          alertedRef.current.add(ev.date)
          toFire.push(ev)
        }
      }

      if (toFire.length === 0) return

      // Group same-time events into one beep + one toast
      playAlertSound()
      if (toFire.length === 1) {
        onAlertRef.current(toFire[0])
      } else {
        onAlertRef.current({
          ...toFire[0],
          title: toFire.map(e => e.title).join('  ·  '),
        })
      }
    }

    check()                                    // run immediately on mount
    const iv = setInterval(check, 30_000)      // then every 30 s
    return () => clearInterval(iv)
  }, [])
}
