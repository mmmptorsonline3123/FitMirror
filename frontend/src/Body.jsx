import { Suspense, useCallback, useEffect, useState } from 'react'
import { Canvas } from '@react-three/fiber'
import { Bounds, OrbitControls, useGLTF } from '@react-three/drei'
import { api } from './api'

const LABELS = { chest: 'Chest', waist: 'Waist', hip: 'Hip', inseam: 'Inseam', shoulder_width: 'Shoulder width' }

function Avatar({ url }) {
  const { scene } = useGLTF(url) // same-origin /api URL: cookie-authenticated, no bucket CORS needed
  useEffect(() => {
    scene.traverse((o) => o.isMesh && o.geometry.computeVertexNormals())
  }, [scene])
  return <primitive object={scene} />
}

function GarmentPreview({ url }) {
  const { scene } = useGLTF(url)
  useEffect(() => {
    scene.traverse((o) => {
      if (!o.isMesh) return
      o.geometry.computeVertexNormals()
      o.material.polygonOffset = true // keep the garment from z-fighting with the skin where ease is small
      o.material.polygonOffsetFactor = -1
    })
  }, [scene])
  return <primitive object={scene} />
}

function Viewer({ url, previewUrl }) {
  return (
    <div className="viewer" data-testid="body-viewer">
      {/* demand: render only on orbit/zoom/load, not 60 fps forever (battery; software-GL CPU hog in CI) */}
      <Canvas frameloop="demand" camera={{ position: [0, 1, 3], fov: 40 }}>
        <ambientLight intensity={0.6} />
        <directionalLight position={[2, 3, 2]} intensity={1.2} />
        <directionalLight position={[-2, 1, -2]} intensity={0.4} />
        <Suspense fallback={null}>
          <Bounds fit clip observe margin={1.1}><Avatar url={url} /></Bounds>
          {previewUrl && <GarmentPreview url={previewUrl} />}
        </Suspense>
        <OrbitControls makeDefault enablePan={false} minDistance={0.8} maxDistance={6} />
      </Canvas>
      <p className="muted small">Drag to rotate · scroll to zoom</p>
    </div>
  )
}

export default function Body({ onBody, preview, onClearPreview }) {
  const [body, setBody] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async (id) => {
    const b = await api(id ? `/body/${id}` : '/body/latest')
    setBody(b)
    if (b.status === 'done') onBody?.(b)
    return b
  }, [onBody])

  useEffect(() => { load().catch(() => {}) }, [load])

  useEffect(() => {
    if (body?.status !== 'running') return
    const id = setInterval(() => load(body.id).catch(() => {}), 1500)
    return () => clearInterval(id)
  }, [body, load])

  async function build() {
    setError('')
    try {
      setBody(await api('/body/fit', { method: 'POST' }))
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <section className="card">
      <h2>Your 3D body</h2>
      <p className="muted">Built from your front and side photos (back photo improves accuracy), scaled to your height.</p>
      <button onClick={build} disabled={body?.status === 'running'}>
        {body?.status === 'running' ? 'Building…' : body ? 'Rebuild from current photos' : 'Build my 3D body'}
      </button>
      {error && <p className="error" role="alert">{error}</p>}
      {body?.status === 'failed' && <p className="error" role="alert">{body.error}</p>}
      {body?.status === 'done' && (
        <div className="body-grid">
          <div>
            {preview && (
              <p className="preview-bar" data-testid="preview-label">
                <span className="badge">Preview</span> {preview.category} shown on your body · approximate, not a fit simulation
                <button className="link" onClick={onClearPreview}>Hide</button>
              </p>
            )}
            <Viewer url={`/api${body.mesh_url}`} previewUrl={preview && preview.body_id === body.id ? `/api${preview.mesh_url}` : null} />
          </div>
          <div>
            <table className="measures" data-testid="measurements">
              <tbody>
                {Object.entries(LABELS).map(([k, label]) => body.measurements[k] && (
                  <tr key={k}>
                    <th scope="row">{label}</th>
                    <td>{body.measurements[k].value_cm.toFixed(1)} cm</td>
                    <td className="muted">± {body.measurements[k].pm_cm} cm</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {body.notes.map((n) => <p key={n} className="muted small">{n}</p>)}
            <p className="muted small">Estimated from photos (model: {body.model}). Not a substitute for a tape measure.</p>
          </div>
        </div>
      )}
    </section>
  )
}
