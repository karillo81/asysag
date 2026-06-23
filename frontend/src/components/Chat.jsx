import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react'
import { streamSse } from '../lib/stream.js'

const JOB_NAME_KEYS = ['job_name', 'name']

// crypto.randomUUID() only exists in secure contexts (HTTPS / localhost).
// We sometimes serve this app over plain HTTP from a public IP, so fall
// back to a cheap unique-enough id.
function newId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

function jobNamesFromToolCall(call) {
  const args = call?.args ?? {}
  const names = []
  for (const key of JOB_NAME_KEYS) {
    if (typeof args[key] === 'string' && args[key]) names.push(args[key])
  }
  return names
}

function Chat({ onJobReferenced, onClear, onTurnComplete }, ref) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const lastUserRef = useRef('')
  const scrollRef = useRef(null)

  useEffect(() => {
    scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight)
  }, [messages])

  const clear = useCallback(() => {
    setMessages([])
    setError(null)
    setInput('')
    lastUserRef.current = ''
    onClear?.()
  }, [onClear])

  const send = useCallback(
    async (text) => {
      const trimmed = text.trim()
      if (!trimmed || busy) return
      lastUserRef.current = trimmed
      setError(null)

      const history = messages
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({ role: m.role, content: m.content }))

      setMessages((prev) => [
        ...prev,
        { id: newId(), role: 'user', content: trimmed },
        { id: newId(), role: 'assistant', content: '', toolCalls: [] },
      ])
      setInput('')
      setBusy(true)

      try {
        const stream = streamSse('/api/chat/stream', { message: trimmed, history })
        for await (const evt of stream) {
          if (evt.event === 'token') {
            const text = evt.data?.text ?? ''
            setMessages((prev) => {
              const next = prev.slice()
              const last = next[next.length - 1]
              if (last && last.role === 'assistant') {
                next[next.length - 1] = { ...last, content: last.content + text }
              }
              return next
            })
          } else if (evt.event === 'tool_call') {
            const call = { name: evt.data?.name, args: evt.data?.args ?? {} }
            setMessages((prev) => {
              const next = prev.slice()
              const last = next[next.length - 1]
              if (last && last.role === 'assistant') {
                next[next.length - 1] = {
                  ...last,
                  toolCalls: [...(last.toolCalls ?? []), call],
                }
              }
              return next
            })
            for (const name of jobNamesFromToolCall(call)) {
              onJobReferenced?.(name)
            }
          } else if (evt.event === 'error') {
            setError(evt.data?.message ?? 'unknown error')
          }
        }
      } catch (e) {
        setError(e.message)
      } finally {
        setBusy(false)
        onTurnComplete?.()
      }
    },
    [busy, messages, onJobReferenced, onTurnComplete],
  )

  const onKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send(input)
    } else if (e.key === 'ArrowUp' && input === '' && lastUserRef.current) {
      e.preventDefault()
      setInput(lastUserRef.current)
    }
  }

  useEffect(() => {
    const onGlobal = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'l') {
        e.preventDefault()
        clear()
      }
    }
    window.addEventListener('keydown', onGlobal)
    return () => window.removeEventListener('keydown', onGlobal)
  }, [clear])

  useImperativeHandle(ref, () => ({ send, clear }), [send, clear])

  return (
    <div className="flex flex-col h-full min-h-0">
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-4 py-3 space-y-3"
      >
        {messages.length === 0 ? (
          <p className="text-sm text-zinc-500">
            Ask about a job — e.g. <span className="mono">is etl_load_facts healthy?</span>
          </p>
        ) : (
          messages.map((m) => <MessageRow key={m.id} message={m} />)
        )}
        {error && (
          <p className="mono text-xs text-red-400">stream error: {error}</p>
        )}
      </div>

      <div className="border-t border-zinc-800 p-3">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
          disabled={busy}
          placeholder={
            busy ? 'thinking…' : 'message agent — Enter to send, Shift+Enter for newline'
          }
          className="w-full bg-zinc-900 border border-zinc-800 rounded p-2 text-sm text-zinc-100 mono focus:outline-none focus:border-zinc-600 disabled:opacity-60 resize-none"
        />
        <div className="mt-1 text-xs text-zinc-500 flex justify-between">
          <span>↑ recall · Ctrl+L clear · Enter send</span>
          <span>{busy ? 'streaming…' : messages.length + ' msg'}</span>
        </div>
      </div>
    </div>
  )
}

function MessageRow({ message }) {
  if (message.role === 'user') {
    return (
      <div className="text-sm text-zinc-100 whitespace-pre-wrap">
        <span className="mono text-xs text-zinc-500 mr-2">you</span>
        {message.content}
      </div>
    )
  }
  return (
    <div className="text-sm text-zinc-100 whitespace-pre-wrap">
      <span className="mono text-xs text-emerald-400 mr-2">agent</span>
      {message.toolCalls?.map((call, i) => (
        <div
          key={i}
          className="mono text-xs text-zinc-500 my-1"
          title={JSON.stringify(call.args)}
        >
          → {call.name}({renderArgs(call.args)})
        </div>
      ))}
      <span>{message.content || (message.toolCalls?.length ? '' : '…')}</span>
    </div>
  )
}

function renderArgs(args) {
  if (!args || typeof args !== 'object') return ''
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(', ')
}

export default forwardRef(Chat)
