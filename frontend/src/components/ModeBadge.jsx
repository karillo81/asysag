export default function ModeBadge({ mode }) {
  const label = mode ? mode.toUpperCase() : '...'
  const styles =
    mode === 'live'
      ? 'border-amber-500 text-amber-400'
      : 'border-zinc-700 text-zinc-400'
  return (
    <span
      title={mode === 'live' ? 'Connected to real AutoSys' : 'Using mock data'}
      className={`mono text-xs px-2 py-0.5 rounded border ${styles}`}
    >
      {label}
    </span>
  )
}
