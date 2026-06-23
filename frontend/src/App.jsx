import { useCallback, useEffect, useRef, useState } from 'react'
import { AuthProvider, useAuth } from './auth/AuthContext.jsx'
import AccountsModal from './components/AccountsModal.jsx'
import ActionLog from './components/ActionLog.jsx'
import Chat from './components/Chat.jsx'
import JobsSidebar from './components/JobsSidebar.jsx'
import Login from './components/Login.jsx'
import ModeBadge from './components/ModeBadge.jsx'
import PendingActions from './components/PendingActions.jsx'
import ScenariosMenu from './components/ScenariosMenu.jsx'
import SettingsModal from './components/SettingsModal.jsx'
import { apiFetch } from './lib/api.js'

function AppShell() {
  const { user, role, checking, logout } = useAuth()
  const [health, setHealth] = useState(null)
  const [referenced, setReferenced] = useState([])
  const [accountsOpen, setAccountsOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [actionLogOpen, setActionLogOpen] = useState(false)
  const [actionsBump, setActionsBump] = useState(0)
  const chatRef = useRef(null)
  const sidebarRef = useRef(null)

  const refreshActions = useCallback(() => setActionsBump((b) => b + 1), [])

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
          <span className="mono text-xs text-zinc-500">
            {user}
            {role === 'admin' && <span className="text-zinc-600"> · admin</span>}
          </span>
          <button
            onClick={() => setActionLogOpen(true)}
            className="mono text-xs px-2 py-0.5 border border-zinc-700 rounded text-zinc-400 hover:text-zinc-200 hover:border-zinc-500"
            title="Action log"
          >
            actions
          </button>
          {role === 'admin' && (
            <button
              onClick={() => setSettingsOpen(true)}
              className="mono text-xs px-2 py-0.5 border border-zinc-700 rounded text-zinc-400 hover:text-zinc-200 hover:border-zinc-500"
              title="Edit settings"
            >
              settings
            </button>
          )}
          <button
            onClick={() => setAccountsOpen(true)}
            className="mono text-xs px-2 py-0.5 border border-zinc-700 rounded text-zinc-400 hover:text-zinc-200 hover:border-zinc-500"
            title={role === 'admin' ? 'Manage accounts' : 'Change my password'}
          >
            {role === 'admin' ? 'accounts' : 'password'}
          </button>
          <button
            onClick={logout}
            className="mono text-xs px-2 py-0.5 border border-zinc-700 rounded text-zinc-400 hover:text-zinc-200 hover:border-zinc-500"
            title="Sign out"
          >
            sign out
          </button>
        </div>
      </header>

      {accountsOpen && (
        <AccountsModal
          onClose={() => setAccountsOpen(false)}
          currentUser={user}
          isAdmin={role === 'admin'}
        />
      )}
      {settingsOpen && role === 'admin' && (
        <SettingsModal onClose={() => setSettingsOpen(false)} />
      )}
      {actionLogOpen && <ActionLog onClose={() => setActionLogOpen(false)} />}

      <main className="flex-1 grid grid-cols-1 md:grid-cols-[300px_1fr] min-h-0">
        <aside className="min-h-0 border-r border-zinc-800 hidden md:flex md:flex-col">
          <JobsSidebar
            ref={sidebarRef}
            referenced={referenced}
            onAction={sendFromSidebar}
          />
        </aside>
        <section className="min-h-0 flex flex-col">
          <PendingActions
            reload={actionsBump}
            role={role}
            onResolved={refreshActions}
          />
          <div className="flex-1 min-h-0">
            <Chat
              ref={chatRef}
              onJobReferenced={noteJobReferenced}
              onClear={clearReferenced}
              onTurnComplete={refreshActions}
            />
          </div>
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
