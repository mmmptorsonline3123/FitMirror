import { lazy, Suspense, useEffect, useState } from 'react'
import { api } from './api'
import Auth from './Auth'
import Capture from './Capture'
import TryOn from './TryOn'
const Body = lazy(() => import('./Body')) // three.js is ~1 MB: keep it out of the first load

export default function App() {
  const [user, setUser] = useState(undefined) // undefined = loading, null = signed out
  const [features, setFeatures] = useState({})
  const [preview, setPreview] = useState(null) // flagged 3D garment preview shown on the avatar

  useEffect(() => {
    api('/me').then(setUser, () => setUser(null))
    api('/features').then(setFeatures, () => {})
  }, [])

  async function logout() {
    await api('/auth/logout', { method: 'POST' })
    setUser(null)
  }

  async function deleteAll() {
    if (!window.confirm('Delete ALL your data? This permanently removes your photos, body models, try-ons and account. It cannot be undone.')) return
    await api('/me', { method: 'DELETE' })
    setPreview(null)
    setUser(null)
    window.alert('All your data has been deleted.')
  }

  if (user === undefined) return <main className="center">Loading…</main>
  if (!user) return <Auth onAuthed={setUser} />

  return (
    <>
      <header className="bar">
        <strong>FitMirror</strong>
        <span className="muted">{user.email}</span>
        <button className="link" onClick={logout}>Sign out</button>
      </header>
      <main>
        <Capture user={user} onUser={setUser} />
        {user.height_cm && <Suspense fallback={<section className="card muted">Loading 3D…</section>}><Body preview={preview} onClearPreview={() => setPreview(null)} /></Suspense>}
        {user.height_cm && <TryOn preview3d={!!features.garment_3d} onPreview={setPreview} />}
        <section className="card danger">
          <h2>Your data</h2>
          <p>Your photos are private, encrypted at rest and only shared with inference providers when you run a try-on.</p>
          <button className="btn-danger" onClick={deleteAll}>Delete all my data</button>
        </section>
      </main>
    </>
  )
}
