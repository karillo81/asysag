import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../lib/api.js'

const STATUS_COLOR = {
  proposed: 'text-amber-400',
  executed: 'text-emerald-400',
  failed: 'text-red-400',
  rejected: 'text-zinc-500',
}

function pretty(key) {
  return key.replace(/_/g, ' ')
}

// Read-only audit log of every proposed/executed write action.
export default function ActionLog({ onClose }) {
  const [actions, setActions] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    apiFetch('/api/actions')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then((d) => setActions(d.actions ?? []))
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const btn =
    'mono text-xs px-2 py-0.5 border border-zinc-700 rounded text-zinc-300 hover:text-zinc-100 hover:border-zinc-500'

  return (
    <div
      className="fixed inset-0 z-30 bg-black/60 flex items-start justify-center p-6 overflow-auto"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-[760px] max-w-full bg-zinc-950 border border-zinc-800 rounded shadow-xl">
        <div className="px-4 py-2 border-b border-zinc-800 flex items-center justify-between">
          <span className="mono text-xs text-zinc-400 uppercase tracking-wide">
            action log
          </span>
          <div className="flex gap-2">
            <button onClick={load} className={btn}>refresh</button>
            <button onClick={onClose} className={btn}>close ✕</button>
          </div>
        </div>

        {error && <div className="mono text-xs text-red-400 px-4 py-2">{error}</div>}

        <div className="max-h-[65vh] overflow-auto">
          {actions == null && (
            <div className="text-xs text-zinc-500 px-4 py-3">loading…</div>
          )}
          {actions && actions.length === 0 && (
            <div className="text-xs text-zinc-500 px-4 py-3">
              no actions yet — proposals will appear here.
            </div>
          )}
          {actions && actions.length > 0 && (
            <table className="w-full text-sm">
              <thead className="text-left text-zinc-500 mono text-xs">
                <tr>
                  <th className="font-normal px-4 py-1">when (UTC)</th>
                  <th className="font-normal py-1">action</th>
                  <th className="font-normal py-1">target</th>
                  <th className="font-normal py-1">status</th>
                  <th className="font-normal py-1">by → approver</th>
                </tr>
              </thead>
              <tbody>
                {actions.map((a) => (
                  <tr key={a.id} className="border-t border-zinc-900">
                    <td className="px-4 py-1 mono text-xs text-zinc-500">
                      {(a.created_at || '').replace('T', ' ').replace('+00:00', '')}
                    </td>
                    <td className="py-1 text-zinc-200">
                      {pretty(a.action_key)}
                      {a.destructive && (
                        <span className="text-red-400 mono text-xs ml-1">!</span>
                      )}
                    </td>
                    <td className="py-1 mono text-zinc-300">{a.target}</td>
                    <td className={`py-1 mono text-xs ${STATUS_COLOR[a.status] || ''}`}>
                      {a.status}
                      {a.error && (
                        <span className="text-red-400" title={a.error}> ⚠</span>
                      )}
                    </td>
                    <td className="py-1 mono text-xs text-zinc-500">
                      {a.proposed_by || '?'}
                      {a.approved_by ? ` → ${a.approved_by}` : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
