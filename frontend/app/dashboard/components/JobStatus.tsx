'use client'

import { useEffect, useState } from 'react'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

interface JobRun {
  id: number
  run_date: string
  daily_ok: boolean
  weekly_ok: boolean
  monthly_ok: boolean
  bar_inject_ok: boolean
  scan_ok: boolean
  lag_ok: boolean
  daily_count: number
  weekly_count: number
  monthly_count: number
  scan_count: number
  lag_count: number
  universe_count: number
  notes: string
  created_at: string
}

function StatusRow({ label, ok, count, total }: {
  label: string
  ok: boolean
  count?: number
  total?: number
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
      <span style={{
        fontSize: 11, fontWeight: 800,
        color: ok ? '#4ade80' : '#f87171',
        width: 14, textAlign: 'center', flexShrink: 0,
      }}>
        {ok ? '✓' : '✗'}
      </span>
      <span style={{ fontSize: 11, color: ok ? 'var(--text-primary)' : '#f87171', flex: 1 }}>
        {label}
      </span>
      {count != null && total != null && (
        <span style={{
          fontSize: 10, fontFamily: 'monospace',
          color: ok ? 'var(--text-dim)' : '#fbbf24',
        }}>
          {count}/{total}
        </span>
      )}
    </div>
  )
}

export default function JobStatus() {
  const [runs, setRuns] = useState<JobRun[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API_URL}/api/job-status`)
      .then(r => r.json())
      .then(d => { setRuns(d.runs ?? []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  const latest = runs[0]

  if (loading) return (
    <div style={{ padding: 16, color: 'var(--text-dim)', fontSize: 11 }}>Loading…</div>
  )

  if (!latest) return (
    <div style={{ padding: 16, color: 'var(--text-dim)', fontSize: 11 }}>
      No job runs recorded yet. First run expected at 5:30 PM ET.
    </div>
  )

  const allOk = latest.daily_ok && latest.weekly_ok && latest.monthly_ok &&
                latest.bar_inject_ok && latest.scan_ok

  const runDate = new Date(latest.run_date + 'T00:00:00').toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  })

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 0,
      background: 'var(--bg-base)', color: 'var(--text-primary)',
      height: '100%',
    }}>

      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
        borderBottom: '1px solid var(--border)', background: 'var(--bg-panel)', flexShrink: 0,
      }}>
        <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: '0.05em' }}>JOB STATUS</span>
        <span style={{
          fontSize: 9, fontWeight: 700, letterSpacing: '0.07em',
          padding: '2px 8px', borderRadius: 5,
          background: allOk ? 'rgba(74,222,128,0.12)' : 'rgba(248,113,113,0.12)',
          color:      allOk ? '#4ade80' : '#f87171',
          border:     `1px solid ${allOk ? 'rgba(74,222,128,0.3)' : 'rgba(248,113,113,0.3)'}`,
        }}>
          {allOk ? '✓ ALL CLEAR' : '⚠ ISSUES'}
        </span>
        <span style={{ fontSize: 10, color: 'var(--text-dim)' }}>{runDate}</span>
      </div>

      {/* Latest run checklist */}
      <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', color: 'var(--text-dim)', marginBottom: 8 }}>
          LATEST RUN — {latest.run_date}
        </div>
        <StatusRow label="Daily candles"   ok={latest.daily_ok}      count={latest.daily_count}   total={latest.universe_count} />
        <StatusRow label="Weekly candles"  ok={latest.weekly_ok}     count={latest.weekly_count}  total={latest.universe_count} />
        <StatusRow label="Monthly candles" ok={latest.monthly_ok}    count={latest.monthly_count} total={latest.universe_count} />
        <StatusRow label="RTH bar inject"  ok={latest.bar_inject_ok} />
        <StatusRow label="Scan complete"   ok={latest.scan_ok}       count={latest.scan_count}    total={latest.universe_count} />
        <StatusRow label="Lag signals"     ok={latest.lag_ok}        count={latest.lag_count} />

        {latest.notes && (
          <div style={{
            marginTop: 10, fontSize: 10, color: allOk ? 'var(--text-dim)' : '#fbbf24',
            fontStyle: 'italic',
          }}>
            {latest.notes}
          </div>
        )}
      </div>

      {/* History */}
      {runs.length > 1 && (
        <div style={{ padding: '10px 14px', flex: 1, overflow: 'auto' }}>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', color: 'var(--text-dim)', marginBottom: 8 }}>
            HISTORY
          </div>
          {runs.map(r => {
            const ok = r.daily_ok && r.weekly_ok && r.monthly_ok && r.scan_ok
            return (
              <div key={r.id} style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '4px 0', borderBottom: '1px solid rgba(255,255,255,0.03)',
              }}>
                <span style={{ fontSize: 10, color: ok ? '#4ade80' : '#f87171', fontWeight: 700, width: 12 }}>
                  {ok ? '✓' : '✗'}
                </span>
                <span style={{ fontSize: 10, fontFamily: 'monospace', color: 'var(--text-muted)', width: 80 }}>
                  {r.run_date}
                </span>
                <span style={{ fontSize: 10, color: 'var(--text-dim)', flex: 1 }}>
                  {r.scan_count} tickers · {r.lag_count} signals
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
