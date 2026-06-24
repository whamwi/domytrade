'use client'

import { useRouter } from 'next/navigation'
import { supabase } from '@/lib/supabase'

interface SidebarProps {
  activeTab?: string
  focusMode?: boolean
  onFocusToggle?: () => void
  showLog?: boolean
  onLogToggle?: () => void
}

export default function Sidebar({ activeTab = 'dashboard', focusMode = false, onFocusToggle, showLog = false, onLogToggle }: SidebarProps) {
  const router = useRouter()

  async function handleLogout() {
    await supabase.auth.signOut()
    router.push('/login')
  }

  return (
    <aside
      className="flex flex-col items-center py-4 gap-1 shrink-0"
      style={{
        width: '56px',
        background: 'var(--bg-panel)',
        borderRight: '1px solid var(--border)',
        height: '100vh',
        position: 'sticky',
        top: 0,
      }}
    >
      {/* Logo icon */}
      <div className="mb-4 mt-1">
        <span className="text-xl font-bold select-none" style={{ color: 'var(--accent-blue)' }}>◈</span>
      </div>

      <div className="flex flex-col gap-1 flex-1 w-full px-2">
        {/* Dashboard nav item */}
        <button
          title="Dashboard"
          className="flex items-center justify-center w-full rounded-lg py-2.5 transition-colors"
          style={{
            background: activeTab === 'dashboard' ? 'var(--accent-blue-dim)' : 'transparent',
            color: activeTab === 'dashboard' ? 'var(--accent-blue)' : 'var(--text-muted)',
          }}
          onMouseEnter={(e) => {
            if (activeTab !== 'dashboard') {
              e.currentTarget.style.background = 'var(--bg-row)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }
          }}
          onMouseLeave={(e) => {
            if (activeTab !== 'dashboard') {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.color = 'var(--text-muted)'
            }
          }}
          onClick={() => {
            const url = new URL(window.location.href)
            url.searchParams.delete('tab')
            window.history.pushState({}, '', url)
            window.dispatchEvent(new Event('popstate'))
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" />
            <rect x="14" y="3" width="7" height="7" />
            <rect x="14" y="14" width="7" height="7" />
            <rect x="3" y="14" width="7" height="7" />
          </svg>
        </button>

        {/* Swing Scanner nav item */}
        <button
          title="Swing Scanner"
          className="flex items-center justify-center w-full rounded-lg py-2.5 transition-colors"
          style={{
            background: activeTab === 'swing' ? 'var(--accent-blue-dim)' : 'transparent',
            color: activeTab === 'swing' ? 'var(--accent-blue)' : 'var(--text-muted)',
          }}
          onMouseEnter={(e) => {
            if (activeTab !== 'swing') {
              e.currentTarget.style.background = 'var(--bg-row)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }
          }}
          onMouseLeave={(e) => {
            if (activeTab !== 'swing') {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.color = 'var(--text-muted)'
            }
          }}
          onClick={() => {
            const url = new URL(window.location.href)
            url.searchParams.set('tab', 'swing')
            window.history.pushState({}, '', url)
            window.dispatchEvent(new Event('popstate'))
          }}
        >
          {/* Crosshair / scanner icon */}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="8" />
            <line x1="12" y1="2" x2="12" y2="6" />
            <line x1="12" y1="18" x2="12" y2="22" />
            <line x1="2" y1="12" x2="6" y2="12" />
            <line x1="18" y1="12" x2="22" y2="12" />
            <circle cx="12" cy="12" r="2" fill="currentColor" />
          </svg>
        </button>

        {/* Market Profile nav item */}
        <button
          title="Market Profile"
          className="flex items-center justify-center w-full rounded-lg py-2.5 transition-colors"
          style={{
            background: activeTab === 'agent' ? 'var(--accent-blue-dim)' : 'transparent',
            color: activeTab === 'agent' ? 'var(--accent-blue)' : 'var(--text-muted)',
          }}
          onMouseEnter={(e) => {
            if (activeTab !== 'agent') {
              e.currentTarget.style.background = 'var(--bg-row)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }
          }}
          onMouseLeave={(e) => {
            if (activeTab !== 'agent') {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.color = 'var(--text-muted)'
            }
          }}
          onClick={() => {
            const url = new URL(window.location.href)
            url.searchParams.set('tab', 'agent')
            window.history.pushState({}, '', url)
            window.dispatchEvent(new Event('popstate'))
          }}
        >
          {/* Bell-curve / Market Profile icon */}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 18 Q4 18 5 14 Q6 10 7 8 Q8 5 9 6 Q10 7 11 4 Q12 2 13 4 Q14 7 15 6 Q16 5 17 8 Q18 10 19 14 Q20 18 21 18" />
            <line x1="3" y1="18" x2="21" y2="18" />
          </svg>
        </button>

        {/* GEX nav item */}
        <button
          title="GEX — Gamma Exposure"
          className="flex items-center justify-center w-full rounded-lg py-2.5 transition-colors"
          style={{
            background: activeTab === 'gex' ? 'var(--accent-blue-dim)' : 'transparent',
            color: activeTab === 'gex' ? 'var(--accent-blue)' : 'var(--text-muted)',
          }}
          onMouseEnter={(e) => {
            if (activeTab !== 'gex') {
              e.currentTarget.style.background = 'var(--bg-row)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }
          }}
          onMouseLeave={(e) => {
            if (activeTab !== 'gex') {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.color = 'var(--text-muted)'
            }
          }}
          onClick={() => {
            const url = new URL(window.location.href)
            url.searchParams.set('tab', 'gex')
            window.history.pushState({}, '', url)
            window.dispatchEvent(new Event('popstate'))
          }}
        >
          {/* Gamma / options wave icon */}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 12 Q4 4 6 12 Q8 20 10 12 Q12 4 14 12 Q16 20 18 12 Q20 4 22 12" />
            <line x1="2" y1="18" x2="22" y2="18" strokeDasharray="2 2" strokeWidth="1.2" />
          </svg>
        </button>

        {/* Market Regime nav item */}
        <button
          title="Market Regime"
          className="flex items-center justify-center w-full rounded-lg py-2.5 transition-colors"
          style={{
            background: activeTab === 'regime' ? 'var(--accent-blue-dim)' : 'transparent',
            color: activeTab === 'regime' ? 'var(--accent-blue)' : 'var(--text-muted)',
          }}
          onMouseEnter={(e) => {
            if (activeTab !== 'regime') {
              e.currentTarget.style.background = 'var(--bg-row)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }
          }}
          onMouseLeave={(e) => {
            if (activeTab !== 'regime') {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.color = 'var(--text-muted)'
            }
          }}
          onClick={() => {
            const url = new URL(window.location.href)
            url.searchParams.set('tab', 'regime')
            window.history.pushState({}, '', url)
            window.dispatchEvent(new Event('popstate'))
          }}
        >
          {/* Regime / gamma grid icon */}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <line x1="3" y1="9" x2="21" y2="9" />
            <line x1="3" y1="15" x2="21" y2="15" />
            <line x1="9" y1="3" x2="9" y2="21" />
          </svg>
        </button>

        {/* Positions nav item */}
        <button
          title="Positions"
          className="flex items-center justify-center w-full rounded-lg py-2.5 transition-colors"
          style={{
            background: activeTab === 'positions' ? 'var(--accent-blue-dim)' : 'transparent',
            color: activeTab === 'positions' ? 'var(--accent-blue)' : 'var(--text-muted)',
          }}
          onMouseEnter={(e) => {
            if (activeTab !== 'positions') {
              e.currentTarget.style.background = 'var(--bg-row)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }
          }}
          onMouseLeave={(e) => {
            if (activeTab !== 'positions') {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.color = 'var(--text-muted)'
            }
          }}
          onClick={() => {
            const url = new URL(window.location.href)
            url.searchParams.set('tab', 'positions')
            window.history.pushState({}, '', url)
            window.dispatchEvent(new Event('popstate'))
          }}
        >
          {/* Portfolio / positions icon */}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <rect x="2" y="7" width="20" height="14" rx="2" />
            <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
            <line x1="12" y1="12" x2="12" y2="16" />
            <line x1="10" y1="14" x2="14" y2="14" />
          </svg>
        </button>

        {/* Lag Signal Log nav item */}
        <button
          title="Laguerre Signal Log — track signal outcomes"
          className="flex items-center justify-center w-full rounded-lg py-2.5 transition-colors"
          style={{
            background: activeTab === 'laglog' ? 'var(--accent-blue-dim)' : 'transparent',
            color: activeTab === 'laglog' ? 'var(--accent-blue)' : 'var(--text-muted)',
          }}
          onMouseEnter={(e) => {
            if (activeTab !== 'laglog') {
              e.currentTarget.style.background = 'var(--bg-row)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }
          }}
          onMouseLeave={(e) => {
            if (activeTab !== 'laglog') {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.color = 'var(--text-muted)'
            }
          }}
          onClick={() => {
            const url = new URL(window.location.href)
            url.searchParams.set('tab', 'laglog')
            window.history.pushState({}, '', url)
            window.dispatchEvent(new Event('popstate'))
          }}
        >
          {/* Log / journal icon */}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
            <line x1="9" y1="13" x2="15" y2="13"/>
            <line x1="9" y1="17" x2="13" y2="17"/>
          </svg>
        </button>

        {/* Settings nav item */}
        <button
          title="Settings"
          className="flex items-center justify-center w-full rounded-lg py-2.5 transition-colors"
          style={{
            background: activeTab === 'settings' ? 'var(--accent-blue-dim)' : 'transparent',
            color: activeTab === 'settings' ? 'var(--accent-blue)' : 'var(--text-muted)',
          }}
          onMouseEnter={(e) => {
            if (activeTab !== 'settings') {
              e.currentTarget.style.background = 'var(--bg-row)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }
          }}
          onMouseLeave={(e) => {
            if (activeTab !== 'settings') {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.color = 'var(--text-muted)'
            }
          }}
        >
          {/* Gear icon */}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>

      {/* Entry log toggle */}
      <button
        title="Entry log — forward-testing history of all ENTRY signals"
        onClick={onLogToggle}
        className="flex items-center justify-center rounded-lg py-2.5 transition-colors"
        style={{
          width: 'calc(100% - 16px)',
          background: showLog ? 'rgba(168,85,247,0.12)' : 'transparent',
          color: showLog ? '#c084fc' : 'var(--text-muted)',
          border: showLog ? '1px solid rgba(168,85,247,0.25)' : '1px solid transparent',
          marginBottom: 4,
        }}
        onMouseEnter={(e) => {
          if (!showLog) {
            e.currentTarget.style.background = 'rgba(168,85,247,0.08)'
            e.currentTarget.style.color = '#c084fc'
          }
        }}
        onMouseLeave={(e) => {
          if (!showLog) {
            e.currentTarget.style.background = 'transparent'
            e.currentTarget.style.color = 'var(--text-muted)'
          }
        }}
      >
        {/* List / log icon */}
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/>
          <rect x="9" y="3" width="6" height="4" rx="1"/>
          <line x1="9" y1="12" x2="15" y2="12"/>
          <line x1="9" y1="16" x2="13" y2="16"/>
        </svg>
      </button>

      {/* Focus mode toggle */}
      <button
        title={focusMode ? 'Focus ON — pinned markets + NEAR/ENTRY only' : 'Focus OFF — showing all signals'}
        onClick={onFocusToggle}
        className="flex items-center justify-center rounded-lg py-2.5 transition-colors"
        style={{
          width: 'calc(100% - 16px)',
          background: focusMode ? 'rgba(74,222,128,0.12)' : 'transparent',
          color: focusMode ? '#4ade80' : 'var(--text-muted)',
          border: focusMode ? '1px solid rgba(74,222,128,0.25)' : '1px solid transparent',
          marginBottom: 4,
        }}
        onMouseEnter={(e) => {
          if (!focusMode) {
            e.currentTarget.style.background = 'rgba(74,222,128,0.08)'
            e.currentTarget.style.color = '#4ade80'
          }
        }}
        onMouseLeave={(e) => {
          if (!focusMode) {
            e.currentTarget.style.background = 'transparent'
            e.currentTarget.style.color = 'var(--text-muted)'
          }
        }}
      >
        {/* Target / focus icon */}
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          <circle cx="12" cy="12" r="6" />
          <circle cx="12" cy="12" r="2" />
        </svg>
      </button>

      {/* Logout at bottom */}
      <button
        title="Sign out"
        onClick={handleLogout}
        className="flex items-center justify-center w-full rounded-lg py-2.5 transition-colors mx-2"
        style={{
          width: 'calc(100% - 16px)',
          color: 'var(--text-muted)',
          background: 'transparent',
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = 'var(--red-bg)'
          e.currentTarget.style.color = '#f87171'
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = 'transparent'
          e.currentTarget.style.color = 'var(--text-muted)'
        }}
      >
        {/* Log out icon */}
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
          <polyline points="16 17 21 12 16 7" />
          <line x1="21" y1="12" x2="9" y2="12" />
        </svg>
      </button>
    </aside>
  )
}
