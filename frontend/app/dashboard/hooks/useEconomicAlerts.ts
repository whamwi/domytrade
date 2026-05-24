'use client'

import { useEffect, useRef, useCallback } from 'react'

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
function playAlertSound() {
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
  } catch {
    // AudioContext blocked (user hasn't interacted yet) — silent fail
  }
}

/** Request browser notification permission. Returns true if granted. */
async function requestNotifPermission(): Promise<boolean> {
  if (!('Notification' in window)) return false
  if (Notification.permission === 'granted') return true
  if (Notification.permission === 'denied') return false
  const result = await Notification.requestPermission()
  return result === 'granted'
}

/** Fire a browser notification for an economic event. */
function fireNotification(ev: EconAlert) {
  if (Notification.permission !== 'granted') return
  const body = [
    ev.forecast ? `Forecast: ${ev.forecast}` : '',
    ev.previous ? `Previous: ${ev.previous}` : '',
  ].filter(Boolean).join('  ·  ')

  const n = new Notification(`⚡ In 15 min: ${ev.title}`, {
    body: body || 'USD Economic Release — in 15 minutes',
    icon: '/favicon.ico',
    tag : ev.date,           // dedupe: same event fires only once
    requireInteraction: false,
  })
  setTimeout(() => n.close(), 12_000)
}

interface Options {
  onAlert: (ev: EconAlert) => void   // callback so page can show toast
}

export function useEconomicAlerts({ onAlert }: Options) {
  const timersRef   = useRef<ReturnType<typeof setTimeout>[]>([])
  const scheduledRef = useRef<Set<string>>(new Set())

  const clearTimers = useCallback(() => {
    timersRef.current.forEach(clearTimeout)
    timersRef.current = []
  }, [])

  const scheduleAlerts = useCallback(async () => {
    clearTimers()
    scheduledRef.current.clear()

    // Request permission early (needs user gesture on some browsers;
    // this runs after the page loads so the browser should allow it)
    await requestNotifPermission()

    try {
      const res  = await fetch(`${API_URL}/api/briefing`, { cache: 'no-store' })
      if (!res.ok) return
      const json = await res.json()
      const events: EconAlert[] = (json.events ?? []).filter(
        (e: EconAlert) => e.country === 'USD'
      )

      const now = Date.now()
      let scheduled = 0

      for (const ev of events) {
        const fireAt = new Date(ev.date).getTime()
        const delay  = fireAt - now

        // Only schedule future events (skip if already past or >7 days out)
        if (delay <= 0 || delay > 7 * 24 * 60 * 60 * 1000) continue
        if (scheduledRef.current.has(ev.date)) continue
        scheduledRef.current.add(ev.date)

        const t = setTimeout(() => {
          playAlertSound()
          fireNotification(ev)
          onAlert(ev)
        }, delay - 15 * 60 * 1000)

        timersRef.current.push(t)
        scheduled++
      }

      if (scheduled > 0) {
        console.info(`[EconAlerts] ${scheduled} USD events scheduled`)
      }
    } catch {
      // Network error — will retry on next refresh cycle
    }
  }, [clearTimers, onAlert])

  useEffect(() => {
    scheduleAlerts()
    // Re-schedule every 6 hours to pick up any calendar updates
    const interval = setInterval(scheduleAlerts, 6 * 60 * 60 * 1000)
    return () => {
      clearTimers()
      clearInterval(interval)
    }
  }, [scheduleAlerts, clearTimers])
}
