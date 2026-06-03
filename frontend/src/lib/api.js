/**
 * fetch wrapper that always sends cookies (so the HttpOnly session cookie
 * travels through the Vite proxy) and surfaces 401s as a window event so
 * the auth context can clear state without a circular import.
 */
export async function apiFetch(path, opts = {}) {
  const res = await fetch(path, { ...opts, credentials: 'include' })
  if (res.status === 401) {
    window.dispatchEvent(new CustomEvent('auth:unauthorized'))
  }
  return res
}
