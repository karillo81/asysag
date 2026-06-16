import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../lib/api.js'

const ROLES = ['operator', 'admin']

async function readError(res, fallback) {
  const body = await res.json().catch(() => ({}))
  return body.detail || fallback || `${res.status} ${res.statusText}`
}

export default function AccountsModal({ onClose, currentUser, isAdmin }) {
  const [accounts, setAccounts] = useState([])
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  // add-account form
  const [newUser, setNewUser] = useState('')
  const [newPass, setNewPass] = useState('')
  const [newRole, setNewRole] = useState('operator')

  // change-own-password form
  const [curPass, setCurPass] = useState('')
  const [nextPass, setNextPass] = useState('')

  const flash = useCallback((msg) => {
    setNotice(msg)
    setError(null)
    setTimeout(() => setNotice(null), 2500)
  }, [])

  const refresh = useCallback(() => {
    if (!isAdmin) return
    apiFetch('/api/accounts')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then((data) => setAccounts(data.accounts ?? []))
      .catch((e) => setError(e.message))
  }, [isAdmin])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const createAccount = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await apiFetch('/api/accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: newUser, password: newPass, role: newRole }),
      })
      if (!res.ok) throw new Error(await readError(res, 'could not create account'))
      setNewUser('')
      setNewPass('')
      setNewRole('operator')
      flash(`created ${newUser}`)
      refresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const patchAccount = async (username, body, label) => {
    setError(null)
    try {
      const res = await apiFetch(`/api/accounts/${encodeURIComponent(username)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(await readError(res))
      flash(label)
      refresh()
    } catch (e) {
      setError(e.message)
    }
  }

  const resetPassword = (username) => {
    const pw = window.prompt(`New password for ${username} (min 8 chars):`)
    if (pw == null) return
    patchAccount(username, { password: pw }, `password reset for ${username}`)
  }

  const toggleActive = (a) =>
    patchAccount(
      a.username,
      { is_active: !a.is_active },
      `${a.username} ${a.is_active ? 'deactivated' : 'activated'}`,
    )

  const changeRole = (a) =>
    patchAccount(
      a.username,
      { role: a.role === 'admin' ? 'operator' : 'admin' },
      `${a.username} role changed`,
    )

  const deleteAccount = async (username) => {
    if (!window.confirm(`Delete account "${username}"? This cannot be undone.`)) return
    setError(null)
    try {
      const res = await apiFetch(`/api/accounts/${encodeURIComponent(username)}`, {
        method: 'DELETE',
      })
      if (!res.ok && res.status !== 204) throw new Error(await readError(res))
      flash(`deleted ${username}`)
      refresh()
    } catch (e) {
      setError(e.message)
    }
  }

  const changeOwnPassword = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const res = await apiFetch('/api/account/password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: curPass, new_password: nextPass }),
      })
      if (!res.ok && res.status !== 204)
        throw new Error(await readError(res, 'could not change password'))
      setCurPass('')
      setNextPass('')
      flash('your password was changed')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const inputCls =
    'bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100 focus:border-zinc-500 outline-none'
  const btnCls =
    'mono text-xs px-2 py-0.5 border border-zinc-700 rounded text-zinc-300 hover:text-zinc-100 hover:border-zinc-500 disabled:opacity-50'

  return (
    <div
      className="fixed inset-0 z-30 bg-black/60 flex items-start justify-center p-6 overflow-auto"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-[680px] max-w-full bg-zinc-950 border border-zinc-800 rounded shadow-xl">
        <div className="px-4 py-2 border-b border-zinc-800 flex items-center justify-between">
          <span className="mono text-xs text-zinc-400 uppercase tracking-wide">
            account management
          </span>
          <button onClick={onClose} className={btnCls}>close ✕</button>
        </div>

        {(error || notice) && (
          <div
            className={`mono text-xs px-4 py-2 border-b border-zinc-900 ${
              error ? 'text-red-400' : 'text-emerald-400'
            }`}
          >
            {error || notice}
          </div>
        )}

        {isAdmin && (
          <section className="px-4 py-3 border-b border-zinc-800">
            <h3 className="mono text-xs text-zinc-500 uppercase tracking-wide mb-2">
              accounts
            </h3>
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-zinc-500 mono text-xs">
                  <th className="font-normal pb-1">user</th>
                  <th className="font-normal pb-1">role</th>
                  <th className="font-normal pb-1">status</th>
                  <th className="font-normal pb-1 text-right">actions</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a) => {
                  const self = a.username === currentUser
                  return (
                    <tr key={a.username} className="border-t border-zinc-900">
                      <td className="py-1 text-zinc-100">
                        {a.username}
                        {self && <span className="text-zinc-600"> (you)</span>}
                      </td>
                      <td className="py-1 text-zinc-300">{a.role}</td>
                      <td className="py-1">
                        <span className={a.is_active ? 'text-emerald-400' : 'text-zinc-500'}>
                          {a.is_active ? 'active' : 'disabled'}
                        </span>
                      </td>
                      <td className="py-1">
                        <div className="flex gap-1 justify-end flex-wrap">
                          <button className={btnCls} onClick={() => resetPassword(a.username)}>
                            reset pw
                          </button>
                          <button
                            className={btnCls}
                            onClick={() => changeRole(a)}
                            disabled={self}
                            title={self ? 'use another admin to change your own role' : ''}
                          >
                            {a.role === 'admin' ? '→ operator' : '→ admin'}
                          </button>
                          <button
                            className={btnCls}
                            onClick={() => toggleActive(a)}
                            disabled={self}
                            title={self ? 'cannot change your own active state' : ''}
                          >
                            {a.is_active ? 'disable' : 'enable'}
                          </button>
                          <button
                            className={`${btnCls} hover:text-red-400 hover:border-red-700`}
                            onClick={() => deleteAccount(a.username)}
                            disabled={self}
                            title={self ? 'cannot delete your own account' : ''}
                          >
                            delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            <form onSubmit={createAccount} className="flex gap-2 items-center mt-3 flex-wrap">
              <input
                className={inputCls}
                placeholder="username"
                value={newUser}
                onChange={(e) => setNewUser(e.target.value)}
                required
              />
              <input
                className={inputCls}
                type="password"
                placeholder="password (min 8)"
                value={newPass}
                onChange={(e) => setNewPass(e.target.value)}
                required
              />
              <select
                className={inputCls}
                value={newRole}
                onChange={(e) => setNewRole(e.target.value)}
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
              <button type="submit" className={btnCls} disabled={busy}>
                add account
              </button>
            </form>
          </section>
        )}

        <section className="px-4 py-3">
          <h3 className="mono text-xs text-zinc-500 uppercase tracking-wide mb-2">
            change my password
          </h3>
          <form onSubmit={changeOwnPassword} className="flex gap-2 items-center flex-wrap">
            <input
              className={inputCls}
              type="password"
              placeholder="current password"
              value={curPass}
              onChange={(e) => setCurPass(e.target.value)}
              required
            />
            <input
              className={inputCls}
              type="password"
              placeholder="new password (min 8)"
              value={nextPass}
              onChange={(e) => setNextPass(e.target.value)}
              required
            />
            <button type="submit" className={btnCls} disabled={busy}>
              update
            </button>
          </form>
        </section>
      </div>
    </div>
  )
}
