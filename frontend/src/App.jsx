import { useCallback, useEffect, useRef, useState } from 'react'
import Chat from './components/Chat.jsx'
import JobsSidebar from './components/JobsSidebar.jsx'
import ModeBadge from './components/ModeBadge.jsx'
import ScenariosMenu from './components/ScenariosMenu.jsx'

function App() {
  const [health, setHealth] = useState(null)
  const [referenced, setReferenced] = useState([])
  const chatRef = useRef(null)
  const sidebarRef = useRef(null)

  useEffect(() => {
    fetch('/api/health')
      .then((r) => r.json())
      .then(setHealth)
      .catch(() => setHealth({ status: 'error', mode: 'unknown' }))
  }, [])

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

export default App
