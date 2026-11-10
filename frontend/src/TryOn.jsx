import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import SizeAdvice from './SizeAdvice'
import Wardrobe from './Wardrobe'

const CATEGORIES = ['dress', 'top', 'bottom', 'outerwear']
const POLL_MS = 2500

function Garment({ g, onChange, onTryOn, onPreview3d, onRemove }) {
  async function setCategory(category) {
    onChange(await api(`/garments/${g.id}`, { method: 'PATCH', json: { category } }))
  }
  return (
    <figure className="garment" data-testid="garment">
      <img src={g.image_url} alt={`Garment (${g.category || 'uncategorized'})`} />
      <figcaption>
        {g.title && <strong className="small">{g.title}</strong>}
        {g.source_url && (
          <a className="small" href={g.source_url} target="_blank" rel="noopener noreferrer nofollow">
            from {new URL(g.source_url).hostname}
          </a>
        )}
        {g.source === 'url' && <small className="muted">{g.size_chart ? 'Size chart found on the page' : 'No size chart found on the page'}</small>}
        {g.status === 'processing' ? <span className="muted">Processing…</span> : (
          <label>Category
            <select value={g.category || ''} onChange={(e) => setCategory(e.target.value)}>
              {!g.category && <option value="" disabled>Pick one</option>}
              {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
        )}
        {g.note && <small className="muted">{g.note}</small>}
        {g.status === 'ready' && <button onClick={() => onTryOn(g)}>Try it on</button>}
        {g.status === 'ready' && onPreview3d && (
          <button className="secondary" onClick={() => onPreview3d(g)}>3D preview <span className="badge">beta</span></button>
        )}
        {g.status === 'ready' && <SizeAdvice garment={g} onChange={onChange} />}
        {g.status !== 'processing' && <button className="link danger-link" onClick={() => onRemove(g)}>Remove garment</button>}
      </figcaption>
    </figure>
  )
}

export default function TryOn({ preview3d = false, onPreview }) {
  const [garments, setGarments] = useState([])
  const [tryons, setTryons] = useState([])
  const [category, setCategory] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [url, setUrl] = useState('')
  const [urlError, setUrlError] = useState('')

  const refresh = useCallback(async () => {
    const w = await api('/wardrobe')
    setGarments(w.garments)
    setTryons(w.tryons)
  }, [])

  useEffect(() => { refresh().catch(() => {}) }, [refresh])

  const pending = garments.some((g) => g.status === 'processing') || tryons.some((t) => t.status === 'running')
  useEffect(() => {
    if (!pending) return
    const id = setInterval(() => refresh().catch(() => {}), POLL_MS)
    return () => clearInterval(id)
  }, [pending, refresh])

  async function addGarment(file) {
    const form = new FormData()
    form.append('file', file)
    if (category) form.append('category', category)
    setBusy(true)
    setError('')
    try {
      const g = await api('/garments/from-image', { method: 'POST', form })
      setGarments((gs) => [g, ...gs])
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function addFromUrl(e) {
    e.preventDefault()
    setBusy(true)
    setUrlError('')
    try {
      const g = await api('/garments/from-url', { method: 'POST', json: { url: url.trim(), ...(category ? { category } : {}) } })
      setGarments((gs) => [g, ...gs])
      setUrl('')
    } catch (err) {
      setUrlError(err.message)
      if (err.code) document.querySelector('[data-testid="upload-garment"]')?.focus()
    } finally {
      setBusy(false)
    }
  }

  async function preview(g) {
    setError('')
    try {
      onPreview(await api('/tryon3d', { method: 'POST', json: { garment_id: g.id } }))
      document.querySelector('[data-testid="body-viewer"]')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    } catch (err) {
      setError(err.message)
    }
  }

  async function removeGarment(g) {
    if (!window.confirm('Remove this garment and all its try-ons? This deletes their images.')) return
    try {
      await api(`/garments/${g.id}`, { method: 'DELETE' })
      setGarments((gs) => gs.filter((x) => x.id !== g.id))
      setTryons((ts) => ts.filter((t) => t.garment_id !== g.id))
    } catch (err) {
      setError(err.message)
    }
  }

  async function removeTryon(t) {
    if (!window.confirm('Remove this try-on? This deletes its images.')) return
    try {
      await api(`/tryon/${t.id}`, { method: 'DELETE' })
      setTryons((ts) => ts.filter((x) => x.id !== t.id))
    } catch (err) {
      setError(err.message)
    }
  }

  async function tryOn(g) {
    setError('')
    try {
      const t = await api('/tryon', { method: 'POST', json: { garment_id: g.id } })
      setTryons((ts) => [t, ...ts])
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section className="card">
      <h2>Try on a garment</h2>
      <p className="muted">Paste a product link or upload a screenshot. A product-only image (no model) gives the best result.</p>
      <form className="row url-row" onSubmit={addFromUrl}>
        <label className="grow">Product link
          <input type="url" inputMode="url" placeholder="https://shop.example/product/…" value={url}
            onChange={(e) => setUrl(e.target.value)} required />
        </label>
        <button disabled={busy || !url.trim()}>{busy ? 'Fetching…' : 'Fetch product'}</button>
      </form>
      {urlError && <p className="error" role="alert" data-testid="url-error">{urlError}</p>}
      <div className="row">
        <label>Category (optional)
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Detect automatically</option>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="upload">
          {busy ? 'Uploading…' : 'Upload screenshot'}
          <input data-testid="upload-garment" type="file" accept="image/jpeg,image/png,image/webp"
            onChange={(e) => { const f = e.target.files[0]; e.target.value = ''; if (f) addGarment(f) }} />
        </label>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {garments.length > 0 && (
        <div className="garments">
          {garments.map((g) => (
            <Garment key={g.id} g={g} onTryOn={tryOn} onPreview3d={preview3d ? preview : undefined} onRemove={removeGarment} onChange={(ng) => setGarments((gs) => gs.map((x) => (x.id === ng.id ? ng : x)))} />
          ))}
        </div>
      )}
      <Wardrobe tryons={tryons} garments={garments} onRemoveTryon={removeTryon} />
    </section>
  )
}
