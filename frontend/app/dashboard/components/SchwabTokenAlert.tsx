'use client'

import { useEffect, useState } from 'react'

interface TokenStatus {
  status: 'ok' | 'warning' | 'critical' | 'expired' | 'unknown'
  message: string
  days_remaining: number | null
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''
const POLL_INTERVAL = 60 * 60 * 1000  // re-check every hour

export default function SchwabTokenAlert() {
  const [status, setStatus] = useState<TokenStatus | null>(null)
  const [dismissed, setDismissed] = useState(false)

  const check = async () => {
    try {
      const r = await fetch(`${API_URL}/api/token-status`)
      if (!r.ok) return
      const data = await r.json()
      setStatus(data)
      // Reset dismiss if status worsens
      if (data.status === 'critical' || data.status === 'expired') {
        setDismissed(false)
      }
    } catch {
      // silent — don't surface network errors as token alerts
    }
  }

  useEffect(() => {
    check()
    const id = setInterval(check, POLL_INTERVAL)
    return () => clearInterval(id)
  }, [])

  if (!status || status.status === 'ok' || status.status === 'unknown' || dismissed) {
    return null
  }

  const isExpired  = status.status === 'expired'
  const isCritical = status.status === 'critical'

  const bg    = isExpired  ? 'bg-red-600'
              : isCritical ? 'bg-orange-500'
              :               'bg-yellow-500'

  const icon  = isExpired  ? '🔴'
              : isCritical ? '🟠'
              :               '🟡'

  return (
    <div className={`${bg} text-white text-sm font-medium px-4 py-2 flex items-center justify-between gap-4 z-50`}>
      <span>
        {icon} <strong>Schwab Token:</strong> {status.message}
        {status.days_remaining !== null && !isExpired && (
          <span className="ml-2 opacity-80">— Renew in Railway env vars.</span>
        )}
      </span>
      {!isExpired && (
        <button
          onClick={() => setDismissed(true)}
          className="opacity-70 hover:opacity-100 text-white font-bold text-lg leading-none"
          aria-label="Dismiss"
        >
          ×
        </button>
      )}
    </div>
  )
}
