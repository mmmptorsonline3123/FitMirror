import { useEffect, useRef, useState } from 'react'
import { api } from './api'

const VIEWS = [
  { id: 'front', label: 'Front', tip: 'Face the camera, arms slightly away from your sides.' },
  { id: 'side', label: 'Side', tip: 'Turn 90° so your shoulder points at the camera.' },
  { id: 'back', label: 'Back', tip: 'Face away from the camera, arms slightly away from your sides.' },
]
const TIMER_S = 5

// Silhouette guide drawn over the camera feed (viewBox 0..100 x 0..200).
function Silhouette({ view }) {
  const d = view === 'side'
    ? 'M50 14a9 9 0 1 1 0.1 0M45 32h12l3 40-2 30 2 50 2 32h-9l-4-48-3 48h-9l1-32 1-50-2-30z'
    : 'M50 14a9 9 0 1 1 0.1 0M36 32h28l14 46-6 2-10-30 2 48 2 50 2 30h-10l-6-62-6 62h-10l2-30 2-50 2-48-10 30-6-2z'
  return (
    <svg className="silhouette" viewBox="0 0 100 200" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <path d={d} />
    </svg>
  )
}

function Camera({ view, onShot }) {
  const video = useRef(null)
  const [error, setError] = useState('')
  const [count, setCount] = useState(0)

  useEffect(() => {
    let stream
    navigator.mediaDevices?.getUserMedia({ video: { facingMode: 'user', height: { ideal: 1920 } }, audio: false })
      .then((s) => { stream = s; if (video.current) video.current.srcObject = s })
      .catch(() => setError('Camera unavailable — use “Upload a photo” instead.'))
    return () => stream?.getTracks().forEach((t) => t.stop())
  }, [])

  function startTimer() {
    let n = TIMER_S
    setCount(n)
    const id = setInterval(() => {
      n -= 1
      setCount(n)
      if (n > 0) return
      clearInterval(id)
      const v = video.current
      const c = document.createElement('canvas')
      c.width = v.videoWidth
      c.height = v.videoHeight
      c.getContext('2d').drawImage(v, 0, 0)
      c.toBlob((b) => b && onShot(b), 'image/jpeg', 0.92)
    }, 1000)
  }

  if (error) return <p className="muted">{error}</p>
  return (
    <div className="camera">
      <video ref={video} autoPlay playsInline muted />
      <Silhouette view={view} />
      {count > 0 && <div className="countdown">{count}</div>}
      <button className="shoot" onClick={startTimer} disabled={count > 0}>Take photo ({TIMER_S}s timer)</button>
    </div>
  )
}

export default function Capture({ user, onUser }) {
  const [photos, setPhotos] = useState({})
  const [step, setStep] = useState(0)
  const [status, setStatus] = useState({}) // view -> {busy?, error?}
  const [useCam, setUseCam] = useState(false)
  const [heightErr, setHeightErr] = useState('')

  useEffect(() => {
    api('/capture/photos').then((list) => setPhotos(Object.fromEntries(list.map((p) => [p.view, p]))), () => {})
  }, [])

  async function saveProfile(e) {
    e.preventDefault()
    const f = new FormData(e.currentTarget)
    const weight = f.get('weight')
    try {
      setHeightErr('')
      onUser(await api('/me', { method: 'PATCH', json: { height_cm: Number(f.get('height')), ...(weight ? { weight_kg: Number(weight) } : {}) } }))
    } catch (err) {
      setHeightErr(err.message)
    }
  }

  async function send(view, blob) {
    setStatus((s) => ({ ...s, [view]: { busy: true } }))
    const form = new FormData()
    form.append('view', view)
    form.append('file', blob, `${view}.jpg`)
    try {
      const p = await api('/capture/photos', { method: 'POST', form })
      setPhotos((ps) => ({ ...ps, [view]: p }))
      setStatus((s) => ({ ...s, [view]: {} }))
      setStep((i) => Math.min(i + 1, VIEWS.length - 1))
    } catch (err) {
      setStatus((s) => ({ ...s, [view]: { error: err.message } }))
    }
  }

  if (!user.height_cm) {
    return (
      <form className="card" onSubmit={saveProfile}>
        <h2>Your measurements</h2>
        <p className="muted">Height sets the real-world scale of your body model.</p>
        <label>Height (cm)<input name="height" type="number" min="100" max="250" step="0.5" required /></label>
        <label>Weight (kg, optional)<input name="weight" type="number" min="25" max="350" step="0.1" /></label>
        {heightErr && <p className="error" role="alert">{heightErr}</p>}
        <button>Continue</button>
      </form>
    )
  }

  const v = VIEWS[step]
  const st = status[v.id] || {}
  const done = VIEWS.every((x) => photos[x.id])
  return (
    <section className="card">
      <h2>Body capture <span className="muted">· height {user.height_cm} cm</span></h2>
      <ul className="rules">
        <li>Wear fitted clothing (leggings, fitted top)</li>
        <li>Plain, uncluttered background and good light</li>
        <li>Whole body in frame, head to feet; only you in the photo</li>
      </ul>
      <nav className="steps">
        {VIEWS.map((x, i) => (
          <button key={x.id} className={i === step ? 'active' : ''} onClick={() => setStep(i)}>
            {photos[x.id] ? '✓ ' : ''}{x.label}
          </button>
        ))}
      </nav>
      <div className="step">
        <h3>{v.label} photo</h3>
        <p>{v.tip}</p>
        {useCam ? <Camera key={v.id} view={v.id} onShot={(b) => send(v.id, b)} /> : (
          <button className="secondary" onClick={() => setUseCam(true)}>Use camera with guide</button>
        )}
        <label className="upload">
          Upload a photo
          <input data-testid={`upload-${v.id}`} type="file" accept="image/jpeg,image/png,image/webp"
            onChange={(e) => { const f = e.target.files[0]; e.target.value = ''; if (f) send(v.id, f) }} />
        </label>
        {st.busy && <p className="muted">Checking photo…</p>}
        {st.error && <p className="error" role="alert" data-testid="photo-error">Photo rejected: {st.error}</p>}
      </div>
      <div className="thumbs">
        {VIEWS.map((x) => photos[x.id] && (
          <figure key={x.id}><img src={photos[x.id].url} alt={`${x.label} photo`} /><figcaption>{x.label} ✓</figcaption></figure>
        ))}
      </div>
      {done && <p className="ok" data-testid="capture-done">All three photos accepted. You're ready for try-on.</p>}
    </section>
  )
}
