'use client'

import { useState, useEffect } from 'react'
import { supabase } from '@/lib/supabase'

export interface Watchlist {
  id: string
  user_id: string
  name: string
  assets: string[]
  models: string[]
  created_at: string
}

export interface SymbolItem {
  ticker: string
  asset_type: string
}

const SECTOR_TICKERS = new Set([
  'XLK','XLV','XLF','XLC','XLY','XLI','XLP','XLE','XLB','XLU','XLRE',
  'SMH','HACK','SKYY','TAN','JETS','OIH','IYT','EEM','SOCL','KCE','XLG','XRT','OEF',
])

const MODELS = ['AGG', 'CON', 'WIDE'] as const
type ModelKey = typeof MODELS[number]

const MODEL_LABEL: Record<ModelKey, string> = {
  AGG:  'Aggressive',
  CON:  'Conservative',
  WIDE: 'Wide / Position',
}

const MODEL_COLOR: Record<ModelKey, { bg: string; color: string; border: string }> = {
  AGG:  { bg: 'rgba(251,146,60,0.15)',  color: '#fb923c', border: 'rgba(251,146,60,0.4)'  },
  CON:  { bg: 'rgba(99,102,241,0.15)',  color: '#818cf8', border: 'rgba(99,102,241,0.4)'  },
  WIDE: { bg: 'rgba(168,85,247,0.15)',  color: '#c084fc', border: 'rgba(168,85,247,0.4)'  },
}

type Category = 'futures' | 'sectors' | 'equities'
const CAT_LABEL: Record<Category, string> = { futures: 'Futures', sectors: 'Sectors', equities: 'Equities' }

interface Props {
  allSymbols: SymbolItem[]
  watchlist?: Watchlist | null   // null = create new
  onSave:  (w: Watchlist) => void
  onDelete?: (id: string) => void
  onClose: () => void
}

export default function WatchlistEditor({ allSymbols, watchlist, onSave, onDelete, onClose }: Props) {
  const [name,    setName]    = useState(watchlist?.name ?? '')
  const [assets,  setAssets]  = useState<string[]>(watchlist?.assets ?? [])
  const [models,  setModels]  = useState<string[]>(watchlist?.models ?? ['AGG', 'CON', 'WIDE'])
  const [cat,     setCat]     = useState<Category>('futures')
  const [search,  setSearch]  = useState('')
  const [saving,  setSaving]  = useState(false)
  const [error,   setError]   = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  // Categorise symbols
  const byCategory = (c: Category) => allSymbols.filter(s => {
    const isFuture = s.ticker.startsWith('/')
    const isSector = SECTOR_TICKERS.has(s.ticker)
    if (c === 'futures')  return isFuture
    if (c === 'sectors')  return isSector
    return !isFuture && !isSector
  })

  const visibleSymbols = byCategory(cat).filter(s =>
    !search || s.ticker.toLowerCase().includes(search.toLowerCase())
  )

  function toggleAsset(ticker: string) {
    setAssets(prev => prev.includes(ticker) ? prev.filter(t => t !== ticker) : [...prev, ticker])
  }

  function toggleModel(m: string) {
    setModels(prev =>
      prev.includes(m)
        ? prev.length > 1 ? prev.filter(x => x !== m) : prev  // keep at least 1
        : [...prev, m]
    )
  }

  function selectAllInCat() {
    const tickers = byCategory(cat).map(s => s.ticker)
    const allSelected = tickers.every(t => assets.includes(t))
    if (allSelected) {
      setAssets(prev => prev.filter(t => !tickers.includes(t)))
    } else {
      setAssets(prev => [...new Set([...prev, ...tickers])])
    }
  }

  async function handleSave() {
    setError(null)
    if (!name.trim())       return setError('Name is required.')
    if (assets.length === 0) return setError('Select at least one symbol.')
    if (models.length === 0) return setError('Select at least one model.')

    setSaving(true)
    try {
      const { data: { session } } = await supabase.auth.getSession()
      if (!session) return setError('Not authenticated.')

      const payload = {
        user_id:    session.user.id,
        name:       name.trim(),
        assets,
        models,
        updated_at: new Date().toISOString(),
      }

      if (watchlist?.id) {
        const { data, error: err } = await supabase
          .from('watchlists')
          .update(payload)
          .eq('id', watchlist.id)
          .select()
          .single()
        if (err) throw err
        onSave(data as Watchlist)
      } else {
        const { data, error: err } = await supabase
          .from('watchlists')
          .insert(payload)
          .select()
          .single()
        if (err) throw err
        onSave(data as Watchlist)
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed.')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    if (!watchlist?.id || !onDelete) return
    setSaving(true)
    const { error: err } = await supabase.from('watchlists').delete().eq('id', watchlist.id)
    if (err) { setError(err.message); setSaving(false); return }
    onDelete(watchlist.id)
  }

  const catTickers = byCategory(cat).map(s => s.ticker)
  const allCatSelected = catTickers.length > 0 && catTickers.every(t => assets.includes(t))

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 100,
        background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
      }}
      onClick={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div style={{
        background: 'var(--bg-panel)', border: '1px solid var(--border)',
        borderRadius: 14, width: '100%', maxWidth: 520,
        maxHeight: '90vh', display: 'flex', flexDirection: 'column',
        boxShadow: '0 24px 64px rgba(0,0,0,0.5)',
      }}>

        {/* Header */}
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
            {watchlist ? 'Edit Watchlist' : 'New Watchlist'}
          </span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: 18, lineHeight: 1 }}>×</button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 20 }}>

          {/* Name */}
          <div>
            <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', display: 'block', marginBottom: 6 }}>
              Watchlist Name
            </label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Futures Focus, Tech + Sectors…"
              style={{
                width: '100%', padding: '8px 12px', borderRadius: 8,
                background: 'var(--bg-row)', border: '1px solid var(--border)',
                color: 'var(--text-primary)', fontSize: 13, outline: 'none',
              }}
              onFocus={e => e.currentTarget.style.borderColor = 'var(--accent-blue)'}
              onBlur={e  => e.currentTarget.style.borderColor = 'var(--border)'}
            />
          </div>

          {/* Models */}
          <div>
            <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em', display: 'block', marginBottom: 8 }}>
              Models — {models.length === 3 ? 'All' : models.join(', ')}
            </label>
            <div style={{ display: 'flex', gap: 8 }}>
              {MODELS.map(m => {
                const active = models.includes(m)
                const c = MODEL_COLOR[m]
                return (
                  <button
                    key={m}
                    onClick={() => toggleModel(m)}
                    style={{
                      flex: 1, padding: '8px 4px', borderRadius: 8, cursor: 'pointer',
                      border: `1px solid ${active ? c.border : 'var(--border)'}`,
                      background: active ? c.bg : 'var(--bg-row)',
                      transition: 'all 0.15s',
                    }}
                  >
                    <div style={{ fontSize: 12, fontWeight: 700, color: active ? c.color : 'var(--text-muted)' }}>{m}</div>
                    <div style={{ fontSize: 10, color: active ? c.color : 'var(--text-dim)', marginTop: 2 }}>{MODEL_LABEL[m]}</div>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Asset Picker */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
              <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                Assets — {assets.length} selected
              </label>
              {assets.length > 0 && (
                <button onClick={() => setAssets([])} style={{ fontSize: 10, color: 'var(--text-dim)', background: 'none', border: 'none', cursor: 'pointer' }}>
                  Clear all
                </button>
              )}
            </div>

            {/* Category tabs */}
            <div style={{ display: 'flex', gap: 4, marginBottom: 10 }}>
              {(['futures', 'sectors', 'equities'] as Category[]).map(c => {
                const count = byCategory(c).filter(s => assets.includes(s.ticker)).length
                const total = byCategory(c).length
                return (
                  <button
                    key={c}
                    onClick={() => { setCat(c); setSearch('') }}
                    style={{
                      flex: 1, padding: '6px 4px', borderRadius: 7, cursor: 'pointer', transition: 'all 0.15s',
                      background: cat === c ? 'var(--accent-blue)' : 'var(--bg-row)',
                      border: `1px solid ${cat === c ? 'var(--accent-blue)' : 'var(--border)'}`,
                      color: cat === c ? '#fff' : 'var(--text-muted)',
                    }}
                  >
                    <div style={{ fontSize: 11, fontWeight: 600 }}>{CAT_LABEL[c]}</div>
                    <div style={{ fontSize: 10, opacity: 0.8, marginTop: 1 }}>{count}/{total}</div>
                  </button>
                )
              })}
            </div>

            {/* Search + Select All */}
            <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder={`Search ${CAT_LABEL[cat].toLowerCase()}…`}
                style={{
                  flex: 1, padding: '6px 10px', borderRadius: 7,
                  background: 'var(--bg-row)', border: '1px solid var(--border)',
                  color: 'var(--text-primary)', fontSize: 12, outline: 'none',
                }}
                onFocus={e => e.currentTarget.style.borderColor = 'var(--accent-blue)'}
                onBlur={e  => e.currentTarget.style.borderColor = 'var(--border)'}
              />
              <button
                onClick={selectAllInCat}
                style={{
                  padding: '6px 12px', borderRadius: 7, cursor: 'pointer', fontSize: 11, fontWeight: 600,
                  background: allCatSelected ? 'rgba(99,102,241,0.15)' : 'var(--bg-row)',
                  border: `1px solid ${allCatSelected ? 'rgba(99,102,241,0.4)' : 'var(--border)'}`,
                  color: allCatSelected ? '#818cf8' : 'var(--text-muted)',
                  transition: 'all 0.15s', whiteSpace: 'nowrap',
                }}
              >
                {allCatSelected ? '✓ All' : 'All'}
              </button>
            </div>

            {/* Symbol grid */}
            <div style={{
              display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 5,
              maxHeight: 180, overflowY: 'auto',
              padding: '4px 2px',
            }}>
              {visibleSymbols.map(s => {
                const active = assets.includes(s.ticker)
                return (
                  <button
                    key={s.ticker}
                    onClick={() => toggleAsset(s.ticker)}
                    style={{
                      padding: '6px 4px', borderRadius: 6, cursor: 'pointer',
                      fontSize: 11, fontWeight: 600, textAlign: 'center',
                      background: active ? 'rgba(59,130,246,0.15)' : 'var(--bg-row)',
                      border: `1px solid ${active ? 'rgba(59,130,246,0.5)' : 'var(--border)'}`,
                      color: active ? 'var(--accent-blue)' : 'var(--text-muted)',
                      transition: 'all 0.12s',
                    }}
                  >
                    {s.ticker}
                  </button>
                )
              })}
              {visibleSymbols.length === 0 && (
                <div style={{ gridColumn: '1/-1', textAlign: 'center', color: 'var(--text-dim)', fontSize: 12, padding: '12px 0' }}>
                  No symbols found
                </div>
              )}
            </div>

            {/* Selected chips */}
            {assets.length > 0 && (
              <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {assets.map(t => (
                  <span
                    key={t}
                    onClick={() => toggleAsset(t)}
                    style={{
                      padding: '2px 8px', borderRadius: 5, fontSize: 10, fontWeight: 700,
                      background: 'rgba(59,130,246,0.12)', color: 'var(--accent-blue)',
                      border: '1px solid rgba(59,130,246,0.3)', cursor: 'pointer',
                    }}
                    title="Click to remove"
                  >
                    {t} ×
                  </span>
                ))}
              </div>
            )}
          </div>

          {error && (
            <div style={{ padding: '8px 12px', borderRadius: 7, background: 'rgba(239,68,68,0.1)', color: '#f87171', fontSize: 12, border: '1px solid rgba(239,68,68,0.2)' }}>
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: '12px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, alignItems: 'center' }}>
          {watchlist && onDelete && (
            confirmDelete ? (
              <>
                <span style={{ fontSize: 12, color: '#f87171', flex: 1 }}>Delete this watchlist?</span>
                <button onClick={() => setConfirmDelete(false)} style={{ padding: '7px 14px', borderRadius: 7, border: '1px solid var(--border)', background: 'var(--bg-row)', color: 'var(--text-muted)', fontSize: 12, cursor: 'pointer' }}>Cancel</button>
                <button onClick={handleDelete} disabled={saving} style={{ padding: '7px 14px', borderRadius: 7, border: 'none', background: '#ef4444', color: '#fff', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>Delete</button>
              </>
            ) : (
              <button onClick={() => setConfirmDelete(true)} style={{ padding: '7px 14px', borderRadius: 7, border: '1px solid rgba(239,68,68,0.3)', background: 'transparent', color: '#f87171', fontSize: 12, cursor: 'pointer', marginRight: 'auto' }}>
                Delete
              </button>
            )
          )}
          {!confirmDelete && (
            <>
              <button onClick={onClose} style={{ padding: '8px 18px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-row)', color: 'var(--text-muted)', fontSize: 13, cursor: 'pointer', marginLeft: watchlist ? 0 : 'auto' }}>
                Cancel
              </button>
              <button
                onClick={handleSave}
                disabled={saving || !name.trim() || assets.length === 0}
                style={{
                  padding: '8px 20px', borderRadius: 8, border: 'none', cursor: 'pointer',
                  background: 'var(--accent-blue)', color: '#fff', fontSize: 13, fontWeight: 600,
                  opacity: (saving || !name.trim() || assets.length === 0) ? 0.5 : 1,
                }}
              >
                {saving ? 'Saving…' : watchlist ? 'Save Changes' : 'Create Watchlist'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
