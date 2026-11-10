// All calls go to same-origin /api (Vite proxy in dev, Vercel rewrite in prod) so the httpOnly cookie just works.
export async function api(path, { method = 'GET', json, form } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: 'same-origin',
    headers: json ? { 'Content-Type': 'application/json' } : undefined,
    body: json ? JSON.stringify(json) : form,
  })
  const body = res.status === 204 ? null : await res.json().catch(() => null)
  if (!res.ok) {
    const detail = body?.reason || (typeof body?.detail === 'string' ? body.detail : body?.detail?.[0]?.msg) || `Request failed (${res.status})`
    throw Object.assign(new Error(detail), { status: res.status, code: body?.code })
  }
  return body
}
