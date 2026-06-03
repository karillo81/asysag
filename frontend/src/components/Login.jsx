import { useState } from 'react'
import { useAuth } from '../auth/AuthContext.jsx'

export default function Login() {
  const { login } = useAuth()
  const [username, setUsername] = useState('root')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const onSubmit = async (e) => {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await login(username, password)
    } catch (err) {
      setError(err.message || 'login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="h-svh flex items-center justify-center">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-xs bg-zinc-950 border border-zinc-800 rounded p-5"
      >
        <h1 className="text-sm font-medium text-zinc-100 mb-1">AutoSys Agent</h1>
        <p className="mono text-xs text-zinc-500 mb-4">sign in to continue</p>

        <label className="block mono text-xs text-zinc-500 mb-1">username</label>
        <input
          type="text"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
          disabled={busy}
          className="w-full bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-sm text-zinc-100 mono focus:outline-none focus:border-zinc-600 disabled:opacity-60 mb-3"
        />

        <label className="block mono text-xs text-zinc-500 mb-1">password</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={busy}
          className="w-full bg-zinc-900 border border-zinc-800 rounded px-2 py-1 text-sm text-zinc-100 mono focus:outline-none focus:border-zinc-600 disabled:opacity-60 mb-3"
        />

        {error && (
          <p className="mono text-xs text-red-400 mb-3">{error}</p>
        )}

        <button
          type="submit"
          disabled={busy || !password}
          className="w-full mono text-xs py-1.5 border border-zinc-700 rounded text-zinc-200 hover:text-zinc-100 hover:border-zinc-500 disabled:opacity-50"
        >
          {busy ? 'signing in…' : 'sign in'}
        </button>
      </form>
    </div>
  )
}
