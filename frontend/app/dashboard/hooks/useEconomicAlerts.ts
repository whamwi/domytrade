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
    // Close context shortly after tones finish to free resources
    setTimeout(() => ctx.close(), 1000)
  } catch {
    // AudioContext blocked (user hasn't interacted yet) — silent fail
  }
}

interface Options {
  onAlert: (ev: EconAlert) => void   // callback so page can show toast
}

export function useEconomicAlerts({ onAlert }: Options) {
  const timersRef    = useRef<ReturnType<typeof setTimeout>[]>([])
  const scheduledRef = useRef<Set<string>>(new Set())
  // Batch window: group events firing within the same minute into one alert
  const batchRef     = useRef<{ timer: ReturnType<typeof setTimeout> | null; events: EconAlert[] }>({
    timer: null, events: [],
  })

  const clearTimers = useCallback(() => {
    timersRef.current.forEach(clearTimeout)
    timersRef.current = []
    if (batchRef.current.timer) {
      clearTimeout(batchRef.current.timer)
      batchRef.current = { timer: null, events: [] }
    }
  }, [])

  /** Collect events that fire within 200ms of each other into one toast + one beep. */
  const queueAlert = useCallback((ev: EconAlert) => {
    batchRef.current.events.push(ev)
    if (batchRef.current.timer) return   // already waiting to flush

    batchRef.current.timer = setTimeout(() => {
      const batch = batchRef.current.events
      batchRef.current = { timer: null, events: [] }

      // One beep regardless of how many events fired at once
      playAlertSound()

      // If multiple events share the same time, combine titles into one alert
      if (batch.length === 1) {
        onAlert(batch[0])
      } else {
        const combined: EconAlert = {
          ...batch[0],
          title: batch.map(e => e.title).join('  ·  '),
        }
        onAlert(combined)
      }
    }, 200)
  }, [onAlert])

  const scheduleAlerts = useCallback(async () => {
    clearTimers()
    scheduledRef.current.clear()

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
        const alertAt = fireAt - 15 * 60 * 1000   // 15 min before event
        const delay   = alertAt - now

        // Only schedule future events (skip if already past or >7 days out)
        if (delay <= 0 || delay > 7 * 24 * 60 * 60 * 1000) continue
        if (scheduledRef.current.has(ev.date)) continue
        scheduledRef.current.add(ev.date)

        const t = setTimeout(() => queueAlert(ev), delay)
        timersRef.current.push(t)
        scheduled++
      }

      if (scheduled > 0) {
        console.info(`[EconAlerts] ${scheduled} USD events scheduled`)
      }
    } catch {
      // Network error — will retry on next refresh cycle
    }
  }, [clearTimers, queueAlert])

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
