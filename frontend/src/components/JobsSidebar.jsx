import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
} from 'react'

const STATUS_RANK = { FAILURE: 0, TERMINATED: 0, RUNNING: 1, SUCCESS: 2 }

const STATUS_STYLES = {
  SUCCESS: 'text-emerald-400',
  RUNNING: 'text-sky-400',
  FAILURE: 'text-red-400',
  TERMINATED: 'text-red-400',
}

const ACTIONS = [
  { id: 'status', label: 'status', template: (n) => `Is ${n} healthy?` },
  {
    id: 'history',
    label: 'history',
    template: (n) => `Show me the last 7 days of runs for ${n}.`,
  },
  {
    id: 'deps',
    label: 'deps',
    template: (n) =>
      `What does ${n} depend on, and what depends on it?`,
  },
]

function statusColour(s) {
  return STATUS_STYLES[s] ?? 'text-zinc-400'
}

function sortJobs(jobs) {
  return [...jobs].sort((a, b) => {
    const ra = STATUS_RANK[a.status] ?? 99
    const rb = STATUS_RANK[b.status] ?? 99
    if (ra !== rb) return ra - rb
    return a.name.localeCompare(b.name)
  })
}

function JobsSidebar({ referenced, onAction }, ref) {
  const [jobs, setJobs] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/jobs')
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const data = await res.json()
      setJobs(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useImperativeHandle(ref, () => ({ refresh: load }), [load])

  const visible = useMemo(() => {
    if (!jobs) return []
    const term = filter.trim().toLowerCase()
    const filtered = term
      ? jobs.filter((j) => j.name.toLowerCase().includes(term))
      : jobs
    return sortJobs(filtered)
  }, [jobs, filter])

  const referencedSet = useMemo(() => new Set(referenced), [referenced])

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="border-b border-zinc-800 px-3 py-2">
        <div className="flex items-center justify-between mb-2">
          <span className="mono text-xs text-zinc-500 uppercase tracking-wide">
            jobs {jobs ? `(${visible.length}/${jobs.length})` : ''}
          </span>
          <button
            onClick={load}
            disabled={loading}
            className="mono text-xs px-2 py-0.5 border border-zinc-700 rounded text-zinc-400 hover:text-zinc-200 hover:border-zinc-500 disabled:opacity-50"
            title="Refresh"
          >
            {loading ? '…' : '↻'}
          </button>
        </div>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="filter…"
          className="w-full bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-sm text-zinc-100 mono focus:outline-none focus:border-zinc-600"
        />
      </div>

      <div className="flex-1 overflow-y-auto">
        {error && (
          <div className="mono text-xs text-red-400 p-3">load error: {error}</div>
        )}
        {!error && jobs === null && (
          <div className="text-xs text-zinc-500 p-3">loading…</div>
        )}
        {!error && jobs && visible.length === 0 && (
          <div className="text-xs text-zinc-500 p-3">no matches</div>
        )}
        {visible.map((job) => (
          <JobRow
            key={job.name}
            job={job}
            referenced={referencedSet.has(job.name)}
            onAction={onAction}
          />
        ))}
      </div>
    </div>
  )
}

export default forwardRef(JobsSidebar)

function JobRow({ job, referenced, onAction }) {
  return (
    <div
      className={`group border-b border-zinc-900 px-3 py-2 mono text-xs ${
        referenced ? 'bg-zinc-900' : ''
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-zinc-200 truncate" title={job.name}>
          {referenced && <span className="text-emerald-500 mr-1">●</span>}
          {job.name}
        </span>
        <span className={`${statusColour(job.status)} shrink-0`}>
          {job.status}
        </span>
      </div>
      <div className="mt-1 flex gap-2 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
        {ACTIONS.map((a) => (
          <button
            key={a.id}
            onClick={() => onAction?.(a.template(job.name))}
            className="text-zinc-500 hover:text-zinc-100"
            title={a.template(job.name)}
          >
            {a.label}
          </button>
        ))}
      </div>
    </div>
  )
}
