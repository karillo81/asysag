import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../lib/api.js'

async function readError(res, fallback) {
  const body = await res.json().catch(() => ({}))
  return body.detail || fallback || `${res.status} ${res.statusText}`
}

export default function SettingsModal({ onClose }) {
  const [groups, setGroups] = useState(null)
  const [edits, setEdits] = useState({})
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)
  const [restartNeeded, setRestartNeeded] = useState(false)
  const [restarting, setRestarting] = useState(false)

  const flash = useCallback((msg) => {
    setNotice(msg)
    setError(null)
    setTimeout(() => setNotice(null), 3000)
  }, [])

  const load = useCallback(() => {
    apiFetch('/api/admin/settings')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then((data) => setGroups(data.groups ?? {}))
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && !restarting && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, restarting])

  const setEdit = (key, value) => setEdits((prev) => ({ ...prev, [key]: value }))

  const save = async () => {
    if (Object.keys(edits).length === 0) {
      flash('no changes to save')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const res = await apiFetch('/api/admin/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates: edits }),
      })
      if (!res.ok) throw new Error(await readError(res, 'could not save settings'))
      const data = await res.json()
      setGroups(data.groups ?? {})
      setEdits({})
      if (data.restart_required) setRestartNeeded(true)
      flash(
        data.changed?.length
          ? `saved: ${data.changed.join(', ')}`
          : 'no effective changes',
      )
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const pollHealthUntilUp = useCallback(async () => {
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 1000))
      try {
        const res = await fetch('/api/health', { credentials: 'include' })
        if (res.ok) return true
      } catch {
        // backend still down — keep polling
      }
    }
    return false
  }, [])

  const restart = async () => {
    if (!window.confirm('Restart the backend now? Active requests are interrupted for a few seconds.')) return
    setError(null)
    setRestarting(true)
    try {
      const res = await apiFetch('/api/admin/restart', { method: 'POST' })
      if (!res.ok && res.status !== 202)
        throw new Error(await readError(res, 'restart failed'))
      const up = await pollHealthUntilUp()
      if (up) {
        setRestartNeeded(false)
        load()
        flash('backend restarted with new settings')
      } else {
        setError('backend did not come back in time — check the server')
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setRestarting(false)
    }
  }

  const inputCls =
    'bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100 focus:border-zinc-500 outline-none w-full'
  const btnCls =
    'mono text-xs px-2 py-0.5 border border-zinc-700 rounded text-zinc-300 hover:text-zinc-100 hover:border-zinc-500 disabled:opacity-50'

  const renderField = (row) => {
    const edited = row.key in edits
    if (row.kind === 'bool') {
      const current = edited ? edits[row.key] : String(row.value).toLowerCase()
      return (
        <select
          className={inputCls}
          value={current === 'true' ? 'true' : 'false'}
          onChange={(e) => setEdit(row.key, e.target.value)}
        >
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      )
    }
    if (row.secret) {
      return (
        <input
          className={inputCls}
          type="password"
          placeholder={row.is_set ? `${row.value} — leave blank to keep` : 'not set'}
          value={edited ? edits[row.key] : ''}
          onChange={(e) => setEdit(row.key, e.target.value)}
        />
      )
    }
    return (
      <input
        className={inputCls}
        value={edited ? edits[row.key] : row.value}
        onChange={(e) => setEdit(row.key, e.target.value)}
      />
    )
  }

  return (
    <div
      className="fixed inset-0 z-30 bg-black/60 flex items-start justify-center p-6 overflow-auto"
      onMouseDown={(e) => e.target === e.currentTarget && !restarting && onClose()}
    >
      <div className="w-[720px] max-w-full bg-zinc-950 border border-zinc-800 rounded shadow-xl">
        <div className="px-4 py-2 border-b border-zinc-800 flex items-center justify-between">
          <span className="mono text-xs text-zinc-400 uppercase tracking-wide">
            settings
          </span>
          <button onClick={onClose} disabled={restarting} className={btnCls}>
            close ✕
          </button>
        </div>

        {(error || notice) && (
          <div
            className={`mono text-xs px-4 py-2 border-b border-zinc-900 ${
              error ? 'text-red-400' : 'text-emerald-400'
            }`}
          >
            {error || notice}
          </div>
        )}

        {restartNeeded && !restarting && (
          <div className="mono text-xs px-4 py-2 border-b border-zinc-900 text-amber-400 flex items-center justify-between">
            <span>changes saved — restart required to take effect</span>
            <button onClick={restart} className={btnCls}>restart now</button>
          </div>
        )}
        {restarting && (
          <div className="mono text-xs px-4 py-2 border-b border-zinc-900 text-amber-400">
            restarting backend…
          </div>
        )}

        <div className="px-4 py-3 max-h-[60vh] overflow-auto">
          {groups == null && (
            <div className="text-xs text-zinc-500">loading…</div>
          )}
          {groups &&
            Object.entries(groups).map(([group, rows]) => (
              <section key={group} className="mb-4">
                <h3 className="mono text-xs text-zinc-500 uppercase tracking-wide mb-2">
                  {group}
                </h3>
                <div className="space-y-2">
                  {rows.map((row) => (
                    <div key={row.key} className="grid grid-cols-[200px_1fr] gap-3 items-center">
                      <label className="text-sm text-zinc-300" title={row.help || row.key}>
                        {row.label}
                        {row.is_overridden && (
                          <span className="text-amber-500/70 ml-1" title="overridden">●</span>
                        )}
                      </label>
                      {renderField(row)}
                    </div>
                  ))}
                </div>
              </section>
            ))}
        </div>

        <div className="px-4 py-3 border-t border-zinc-800 flex items-center justify-end gap-2">
          <button onClick={save} disabled={busy || restarting} className={btnCls}>
            save changes
          </button>
        </div>
      </div>
    </div>
  )
}
