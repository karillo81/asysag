import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../lib/api.js'

const TIER_NOTE = { A: 'reversible', B: 'state change', C: 'destructive' }

function pretty(key) {
  return key.replace(/_/g, ' ')
}

async function detail(res) {
  const b = await res.json().catch(() => ({}))
  return b.detail || `${res.status} ${res.statusText}`
}

// Approval cards for agent-proposed write actions. Renders nothing when there
// are no pending proposals, so it stays invisible in read-only mode.
export default function PendingActions({ reload, role, onResolved }) {
  const [actions, setActions] = useState([])
  const [error, setError] = useState(null)
  const [busyId, setBusyId] = useState(null)

  const load = useCallback(() => {
    apiFetch('/api/actions?status=proposed')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then((d) => setActions(d.actions ?? []))
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    load()
  }, [load, reload])

  useEffect(() => {
    const t = setInterval(load, 8000) // also surface actions proposed elsewhere
    return () => clearInterval(t)
  }, [load])

  const confirm = async (a) => {
    if (
      a.destructive &&
      !window.confirm(
        `DESTRUCTIVE action: ${pretty(a.action_key)} on "${a.target}". This may be irreversible. Proceed?`,
      )
    )
      return
    setBusyId(a.id)
    setError(null)
    try {
      const res = await apiFetch(`/api/actions/${a.id}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm_destructive: !!a.destructive }),
      })
      if (!res.ok) throw new Error(await detail(res))
      load()
      onResolved?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusyId(null)
    }
  }

  const reject = async (a) => {
    setBusyId(a.id)
    setError(null)
    try {
      const res = await apiFetch(`/api/actions/${a.id}/reject`, { method: 'POST' })
      if (!res.ok) throw new Error(await detail(res))
      load()
      onResolved?.()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusyId(null)
    }
  }

  if (!actions.length && !error) return null

  const btn =
    'mono text-xs px-2 py-0.5 border rounded disabled:opacity-40'

  return (
    <div className="border-b border-amber-900/50 bg-amber-950/20 px-4 py-2 space-y-2">
      {error && <div className="mono text-xs text-red-400">{error}</div>}
      {actions.map((a) => {
        const needsAdmin = a.destructive && role !== 'admin'
        return (
          <div
            key={a.id}
            className="flex items-center justify-between gap-3 text-sm"
          >
            <div className="min-w-0">
              <span className="text-amber-300 mono text-xs mr-2">proposed</span>
              <span className="text-zinc-100">{pretty(a.action_key)}</span>
              <span className="text-zinc-500"> → </span>
              <span className="mono text-zinc-200">{a.target}</span>
              <span
                className={`ml-2 mono text-xs ${
                  a.destructive ? 'text-red-400' : 'text-zinc-500'
                }`}
              >
                [{TIER_NOTE[a.tier] || a.tier}]
              </span>
              {a.proposed_by && (
                <span className="mono text-xs text-zinc-600 ml-2">
                  by {a.proposed_by}
                </span>
              )}
            </div>
            <div className="flex gap-1 shrink-0">
              <button
                className={`${btn} border-emerald-700 text-emerald-300 hover:text-emerald-100 hover:border-emerald-500`}
                onClick={() => confirm(a)}
                disabled={busyId === a.id || needsAdmin}
                title={needsAdmin ? 'destructive action — requires an admin' : 'Approve and execute'}
              >
                approve
              </button>
              <button
                className={`${btn} border-zinc-700 text-zinc-400 hover:text-zinc-200 hover:border-zinc-500`}
                onClick={() => reject(a)}
                disabled={busyId === a.id}
              >
                reject
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
