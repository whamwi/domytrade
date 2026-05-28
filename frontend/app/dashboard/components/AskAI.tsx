'use client'

import { useState, useRef, useEffect } from 'react'

interface Message {
  role: 'user' | 'model'
  content: string
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export default function AskAI() {
  const [open,    setOpen]    = useState(false)
  const [input,   setInput]   = useState('')
  const [history, setHistory] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef  = useRef<HTMLTextAreaElement>(null)

  // Scroll to bottom whenever history changes
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, loading])

  // Focus input when panel opens
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 100)
  }, [open])

  const send = async () => {
    const msg = input.trim()
    if (!msg || loading) return

    const next: Message[] = [...history, { role: 'user', content: msg }]
    setHistory(next)
    setInput('')
    setLoading(true)

    try {
      const res  = await fetch(`${API_URL}/api/ai/ask`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ message: msg, history }),
        signal:  AbortSignal.timeout(45_000),   // 45s — Gemini can be slow under load
      })
      if (!res.ok) {
        setHistory([...next, { role: 'model', content: `Server error ${res.status} — try again.` }])
        return
      }
      const data = await res.json()
      setHistory([...next, { role: 'model', content: data.reply || '—' }])
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      const friendly = msg.includes('abort') || msg.includes('timeout')
        ? 'Request timed out — the AI is slow right now. Try again.'
        : `Connection error: ${msg}`
      setHistory([...next, { role: 'model', content: friendly }])
    } finally {
      setLoading(false)
    }
  }

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  return (
    <>
      {/* Floating button */}
      <button
        onClick={() => setOpen(o => !o)}
        className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-full px-4 py-2.5 text-sm font-bold shadow-lg transition-all"
        style={{
          background: open ? 'rgba(168,85,247,0.25)' : 'rgba(168,85,247,0.15)',
          border:     '1px solid rgba(168,85,247,0.5)',
          color:      '#c084fc',
          backdropFilter: 'blur(8px)',
        }}
      >
        <span style={{ fontSize: '14px' }}>✦</span>
        Ask AI
        {history.length > 0 && !open && (
          <span
            className="flex h-4 w-4 items-center justify-center rounded-full text-xs font-bold"
            style={{ background: 'rgba(168,85,247,0.6)', color: '#fff', fontSize: '10px' }}
          >
            {Math.floor(history.length / 2)}
          </span>
        )}
      </button>

      {/* Chat panel */}
      {open && (
        <div
          className="fixed bottom-20 right-6 flex flex-col rounded-xl"
          style={{
            width:           '340px',
            height:          '460px',
            zIndex:          1100,
            backgroundColor: '#16131f',   // explicit solid dark-purple bg
            backgroundImage: 'none',
            border:          '1px solid rgba(168,85,247,0.35)',
            boxShadow:       '0 0 0 1px rgba(0,0,0,0.8), 0 24px 60px rgba(0,0,0,0.85)',
          }}
        >
          {/* Header */}
          <div
            className="flex items-center justify-between px-4 py-3 rounded-t-xl"
            style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}
          >
            <div className="flex items-center gap-2">
              <span style={{ color: '#c084fc', fontSize: '14px' }}>✦</span>
              <span style={{ color: '#e2e8f0', fontSize: '13px', fontWeight: 700 }}>
                Ask AI
              </span>
              <span
                className="rounded px-1.5 py-0.5 text-xs font-semibold"
                style={{ background: 'rgba(168,85,247,0.12)', color: '#a855f7' }}
              >
                Gemini 2.0 Flash
              </span>
            </div>
            <div className="flex items-center gap-3">
              {history.length > 0 && (
                <button
                  onClick={() => setHistory([])}
                  style={{ color: '#64748b', fontSize: '11px', background: 'none', border: 'none', cursor: 'pointer' }}
                >
                  clear
                </button>
              )}
              <button
                onClick={() => setOpen(false)}
                style={{ color: '#64748b', fontSize: '16px', background: 'none', border: 'none', cursor: 'pointer', lineHeight: 1 }}
              >
                ×
              </button>
            </div>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
            {history.length === 0 && !loading && (
              <div style={{ color: '#64748b', fontSize: '12px', textAlign: 'center', marginTop: '60px' }}>
                <div style={{ fontSize: '24px', marginBottom: '8px' }}>✦</div>
                <div>Ask anything about the live signals,</div>
                <div>sectors, or market conditions.</div>
                <div style={{ marginTop: '16px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  {[
                    'Which signal looks cleanest right now?',
                    'Why is /NQ weak today?',
                    'Should I avoid shorts given internals?',
                  ].map(q => (
                    <button
                      key={q}
                      onClick={() => { setInput(q); inputRef.current?.focus() }}
                      className="rounded px-2 py-1 text-left text-xs"
                      style={{ background: 'rgba(168,85,247,0.08)', color: '#a855f7', border: '1px solid rgba(168,85,247,0.2)', cursor: 'pointer' }}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {history.map((msg, i) => (
              <div
                key={i}
                className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className="rounded-lg px-3 py-2 text-xs leading-relaxed"
                  style={{
                    maxWidth:   '85%',
                    background: msg.role === 'user'
                      ? 'rgba(168,85,247,0.15)'
                      : 'rgba(255,255,255,0.04)',
                    color:      msg.role === 'user' ? '#c084fc' : '#e2e8f0',
                    border:     msg.role === 'model' ? '1px solid rgba(255,255,255,0.08)' : 'none',
                    whiteSpace: 'pre-wrap',
                  }}
                >
                  {msg.content}
                </div>
              </div>
            ))}

            {loading && (
              <div className="flex justify-start">
                <div
                  className="rounded-lg px-3 py-2 text-xs"
                  style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', color: '#64748b' }}
                >
                  Thinking…
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <div
            className="px-3 py-3 rounded-b-xl"
            style={{ borderTop: '1px solid rgba(255,255,255,0.08)' }}
          >
            <div className="flex items-end gap-2">
              <textarea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKey}
                placeholder="Ask about signals, sectors, internals…"
                rows={1}
                className="flex-1 resize-none rounded-lg px-3 py-2 text-xs outline-none"
                style={{
                  background:  'rgba(255,255,255,0.05)',
                  border:      '1px solid rgba(255,255,255,0.08)',
                  color:       '#e2e8f0',
                  maxHeight:   '80px',
                  lineHeight:  '1.5',
                }}
              />
              <button
                onClick={send}
                disabled={!input.trim() || loading}
                className="flex h-8 w-8 items-center justify-center rounded-lg font-bold transition-opacity"
                style={{
                  background: (!input.trim() || loading) ? 'rgba(168,85,247,0.1)' : 'rgba(168,85,247,0.3)',
                  color:      (!input.trim() || loading) ? 'rgba(168,85,247,0.4)' : '#c084fc',
                  border:     '1px solid rgba(168,85,247,0.3)',
                  fontSize:   '14px',
                  flexShrink: 0,
                }}
              >
                ↑
              </button>
            </div>
            <div style={{ color: '#64748b', fontSize: '10px', marginTop: '4px', textAlign: 'right' }}>
              Enter to send · Shift+Enter for new line
            </div>
          </div>
        </div>
      )}
    </>
  )
}
