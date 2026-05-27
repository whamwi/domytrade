'use client'

import { useEffect } from 'react'

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    // Log to console so it shows in browser devtools
    console.error('[Dashboard error]', error)
  }, [error])

  return (
    <div
      className="flex flex-col items-center justify-center h-screen gap-6"
      style={{ background: 'var(--bg-base)', color: 'var(--text-muted)' }}
    >
      <div className="flex flex-col items-center gap-2 text-center max-w-lg px-6">
        <span
          className="text-sm font-bold uppercase tracking-widest"
          style={{ color: '#f87171' }}
        >
          Dashboard error
        </span>
        <pre
          className="mt-2 text-xs text-left rounded-lg p-4 overflow-auto max-w-full"
          style={{
            background:  'rgba(248,113,113,0.08)',
            border:      '1px solid rgba(248,113,113,0.25)',
            color:       '#fca5a5',
            maxHeight:   '200px',
            whiteSpace:  'pre-wrap',
            wordBreak:   'break-all',
          }}
        >
          {error.message || String(error)}
        </pre>
        {error.digest && (
          <p className="text-xs" style={{ color: 'var(--text-dim)' }}>
            Digest: {error.digest}
          </p>
        )}
      </div>

      <button
        onClick={reset}
        className="rounded-lg px-5 py-2 text-xs font-semibold uppercase tracking-wider"
        style={{ background: 'var(--accent-blue)', color: '#fff' }}
      >
        Try again
      </button>
    </div>
  )
}
