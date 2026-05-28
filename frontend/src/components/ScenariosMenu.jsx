import { useEffect, useRef, useState } from 'react'

export default function ScenariosMenu({ onReplayed }) {
  const [open, setOpen] = useState(false)
  const [scenarios, setScenarios] = useState([])
  const [active, setActive] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [lastReplayed, setLastReplayed] = useState(null)
  const rootRef = useRef(null)

  const refresh = () =>
    fetch('/api/scenarios')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then((data) => {
        setScenarios(data.scenarios ?? [])
        setActive(data.active ?? null)
      })
      .catch((e) => setError(e.message))

  useEffect(() => {
    refresh()
  }, [])

  useEffect(() => {
    if (open && scenarios.length === 0 && !error) refresh()
  }, [open, scenarios.length, error])

  useEffect(() => {
    if (!open) return
    const onDown = (e) => {
      if (!rootRef.current?.contains(e.target)) setOpen(false)
    }
    window.addEventListener('mousedown', onDown)
    return () => window.removeEventListener('mousedown', onDown)
  }, [open])

  const replay = async (name) => {
    setBusy(name)
    setError(null)
    try {
      const res = await fetch(
        `/api/scenarios/${encodeURIComponent(name)}/reset`,
        { method: 'POST' },
      )
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const data = await res.json()
      setActive(data.active ?? name)
      setLastReplayed(name)
      onReplayed?.(name)
      setTimeout(() => setLastReplayed(null), 2500)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="mono text-xs px-2 py-0.5 border border-zinc-700 rounded text-zinc-300 hover:text-zinc-100 hover:border-zinc-500"
        title={active ? `active scenario: ${active}` : 'select a scenario'}
      >
        scenarios{active ? `: ${active}` : ''} ▾
      </button>
      {lastReplayed && (
        <span className="mono text-xs text-emerald-400 ml-2">
          ✓ {lastReplayed}
        </span>
      )}

      {open && (
        <div className="absolute right-0 top-full mt-1 w-[480px] max-w-[90vw] bg-zinc-950 border border-zinc-800 rounded shadow-lg z-10">
          <div className="px-3 py-2 border-b border-zinc-800 mono text-xs text-zinc-500 uppercase tracking-wide">
            replay scenario
          </div>
          {error && (
            <div className="mono text-xs text-red-400 px-3 py-2">{error}</div>
          )}
          {scenarios.length === 0 && !error && (
            <div className="text-xs text-zinc-500 px-3 py-2">loading…</div>
          )}
          {scenarios.map((s) => {
            const isActive = s.name === active
            return (
              <div
                key={s.name}
                className={`px-3 py-2 border-b border-zinc-900 last:border-b-0 ${
                  isActive ? 'bg-zinc-900' : ''
                }`}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-sm text-zinc-100 font-medium">
                    {isActive && <span className="text-emerald-500 mr-1">●</span>}
                    {s.title || s.name}
                  </span>
                  <button
                    onClick={() => replay(s.name)}
                    disabled={busy === s.name}
                    className="mono text-xs px-2 py-0.5 border border-zinc-700 rounded text-zinc-300 hover:text-zinc-100 hover:border-zinc-500 disabled:opacity-50 shrink-0"
                  >
                    {busy === s.name ? '…' : isActive ? '↻ reload' : '▶ load'}
                  </button>
                </div>
                {s.description && (
                  <p className="text-xs text-zinc-400 mt-1">{s.description}</p>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
