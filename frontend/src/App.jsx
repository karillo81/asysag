import { useCallback, useEffect, useRef, useState } from 'react'
import { AuthProvider, useAuth } from './auth/AuthContext.jsx'
import Chat from './components/Chat.jsx'
import JobsSidebar from './components/JobsSidebar.jsx'
import Login from './components/Login.jsx'
import ModeBadge from './components/ModeBadge.jsx'
import ScenariosMenu from './components/ScenariosMenu.jsx'
import { apiFetch } from './lib/api.js'

function AppShell() {
  const { user, checking, logout } = useAuth()
  const [health, setHealth] = useState(null)
  const [referenced, setReferenced] = useState([])
  const chatRef = useRef(null)
  const sidebarRef = useRef(null)

  useEffect(() => {
    if (!user) return
    apiFetch('/api/health')
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth({ status: 'error', mode: 'unknown' }))
  }, [user])

  const noteJobReferenced = useCallback((name) => {
    setReferenced((prev) => {
      const filtered = prev.filter((n) => n !== name)
      return [name, ...filtered].slice(0, 16)
    })
  }, [])

  const clearReferenced = useCallback(() => setReferenced([]), [])

  const sendFromSidebar = useCallback((message) => {
    chatRef.current?.send(message)
  }, [])

  const onScenarioReplayed = useCallback(() => {
    sidebarRef.current?.refresh()
  }, [])

  if (checking) {
    return (
      <div className="h-svh flex items-center justify-center">
        <span className="mono text-xs text-zinc-500">checking session…</span>
      </div>
    )
  }
  if (!user) return <Login />

  return (
    <div className="h-svh flex flex-col">
      <header className="border-b border-zinc-800 px-4 py-2 flex items-center justify-between shrink-0">
        <div className="flex items-baseline gap-3">
          <h1 className="text-base font-medium text-zinc-100">AutoSys Agent</h1>
          <span className="mono text-xs text-zinc-500">
            {health?.model ?? ''}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <ScenariosMenu onReplayed={onScenarioReplayed} />
          <ModeBadge mode={health?.mode} />
          <span className="mono text-xs text-zinc-500">{user}</span>
          <button
            onClick={logout}
            className="mono text-xs px-2 py-0.5 border border-zinc-700 rounded text-zinc-400 hover:text-zinc-200 hover:border-zinc-500"
            title="Sign out"
          >
            sign out
          </button>
        </div>
      </header>

      <main className="flex-1 grid grid-cols-1 md:grid-cols-[300px_1fr] min-h-0">
        <aside className="min-h-0 border-r border-zinc-800 hidden md:flex md:flex-col">
          <JobsSidebar
            ref={sidebarRef}
            referenced={referenced}
            onAction={sendFromSidebar}
          />
        </aside>
        <section className="min-h-0">
          <Chat
            ref={chatRef}
            onJobReferenced={noteJobReferenced}
            onClear={clearReferenced}
          />
        </section>
      </main>
    </div>
  )
}

function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  )
}

export default App
