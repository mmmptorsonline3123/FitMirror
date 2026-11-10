import { useEffect, useRef, useState } from 'react'
import { api } from './api'

const MAX_COMPARE = 4
const label = (g) => g?.title || g?.category || 'Garment'

function Result({ t, garment, selected, canSelect, onToggle, onRemove }) {
  const status = { running: 'Generating…', done: 'Done', failed: 'Failed' }[t.status]
  return (
    <article className="result" data-testid="tryon-result">
      <header>
        {garment && <img className="mini" src={garment.image_url} alt="" />}
        <strong>{label(garment)}</strong>
        <span className={t.status === 'failed' ? 'error' : 'muted'} data-testid="tryon-status">{status}</span>
      </header>
      {Object.entries(t.views).map(([view, v]) => (
        <div key={view} className="pair">
          <figure>
            {v.original_url ? <img src={v.original_url} alt={`Original ${view}`} /> : <div className="ph">Photo retaken</div>}
            <figcaption>Original · {view}</figcaption>
          </figure>
          <figure>
            {v.result_url ? <img src={v.result_url} alt={`Try-on ${view}`} />
              : <div className="ph">{v.status === 'failed' ? v.error : 'Generating…'}</div>}
            <figcaption>Try-on · {view}</figcaption>
          </figure>
        </div>
      ))}
      {t.unsupported_views.length > 0 && (
        <p className="muted small">{t.unsupported_views.join(' & ')} view: not supported by the current try-on model.</p>
      )}
      <div className="row result-actions">
        {t.status === 'done' && (
          <label className="check">
            <input type="checkbox" checked={selected} disabled={!selected && !canSelect} onChange={onToggle}
              aria-label={`Compare ${label(garment)}`} />
            Compare
          </label>
        )}
        <button className="link danger-link" onClick={onRemove}>Remove</button>
      </div>
    </article>
  )
}

function Compare({ items, onClose }) {
  const [sizes, setSizes] = useState({})
  const requested = useRef(new Set()) // items is a fresh array each render: fetch each garment's advice once
  useEffect(() => {
    for (const { garment } of items) {
      if (!garment || requested.current.has(garment.id)) continue
      requested.current.add(garment.id)
      api(`/garments/${garment.id}/size-advice`)
        .then((a) => setSizes((s) => ({ ...s, [garment.id]: a.recommended ? `Size ${a.recommended}` : 'No size chart' })))
        .catch(() => setSizes((s) => ({ ...s, [garment.id]: '' })))
    }
  }, [items])

  const original = items[0].t.views.front?.original_url
  return (
    <section className="compare" data-testid="compare" aria-label="Compare try-ons">
      <header className="row">
        <h3>Comparing {items.length} try-ons</h3>
        <button className="link" onClick={onClose}>Clear</button>
      </header>
      <div className="compare-grid" style={{ '--cols': items.length + 1 }}>
        <figure>
          {original ? <img src={original} alt="You (original)" /> : <div className="ph">No photo</div>}
          <figcaption><strong>You</strong></figcaption>
        </figure>
        {items.map(({ t, garment }) => (
          <figure key={t.id} data-testid="compare-item">
            <img src={t.views.front?.result_url} alt={`Try-on ${label(garment)}`} />
            <figcaption>
              <strong>{label(garment)}</strong>
              {garment && (garment.title || sizes[garment.id]) && (
                <span className="muted">{[garment.title && garment.category, sizes[garment.id]].filter(Boolean).join(' · ')}</span>
              )}
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  )
}

export default function Wardrobe({ tryons, garments, onRemoveTryon }) {
  const [selected, setSelected] = useState([])
  const byId = Object.fromEntries(garments.map((g) => [g.id, g]))
  const live = selected.filter((id) => tryons.some((t) => t.id === id && t.status === 'done')) // drop removed ones
  const items = live.map((id) => {
    const t = tryons.find((x) => x.id === id)
    return { t, garment: byId[t.garment_id] }
  })

  const toggle = (id) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id].slice(-MAX_COMPARE)))

  if (!tryons.length) return null
  return (
    <div className="wardrobe">
      <h3>Your wardrobe <span className="muted">· {tryons.length} try-on{tryons.length > 1 ? 's' : ''}</span></h3>
      {items.length >= 2 ? <Compare items={items} onClose={() => setSelected([])} />
        : <p className="muted small" data-testid="compare-hint">Tick 2–{MAX_COMPARE} finished try-ons to compare them side by side.</p>}
      {tryons.map((t) => (
        <Result key={t.id} t={t} garment={byId[t.garment_id]} selected={live.includes(t.id)} canSelect={live.length < MAX_COMPARE}
          onToggle={() => toggle(t.id)} onRemove={() => onRemoveTryon(t)} />
      ))}
    </div>
  )
}
