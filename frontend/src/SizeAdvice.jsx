import { useState } from 'react'
import { api } from './api'

const COLS = ['chest', 'waist', 'hip', 'inseam']
const blankRow = () => ({ label: '', chest: '', waist: '', hip: '', inseam: '' })

// "86-91" -> [86, 91]; "88" -> 88 (server reads a point as ±2 cm); "" -> skipped
function parseCell(s) {
  const nums = s.split(/[-–]/).map((x) => Number(x.trim())).filter((n) => !Number.isNaN(n) && n > 0)
  if (!s.trim()) return undefined
  if (nums.length === 1) return nums[0]
  if (nums.length === 2) return nums
  throw new Error(`Can't read "${s}". Use a number like 88 or a range like 86-91.`)
}

function ChartForm({ garment, onSaved }) {
  const [unit, setUnit] = useState('cm')
  const [rows, setRows] = useState([blankRow(), blankRow(), blankRow()])
  const [error, setError] = useState('')

  async function save(e) {
    e.preventDefault()
    setError('')
    try {
      const sizes = rows.filter((r) => r.label.trim()).map((r) => ({
        label: r.label.trim(),
        ranges: Object.fromEntries(COLS.map((c) => [c, parseCell(r[c])]).filter(([, v]) => v !== undefined)),
      }))
      if (!sizes.length) throw new Error('Add at least one size.')
      onSaved(await api(`/garments/${garment.id}`, { method: 'PATCH', json: { size_chart: { unit, sizes } } }))
    } catch (err) {
      setError(err.message)
    }
  }

  const set = (i, k, v) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [k]: v } : r)))
  return (
    <form className="chart" onSubmit={save} aria-label="Size chart">
      <label>Unit
        <select value={unit} onChange={(e) => setUnit(e.target.value)}><option>cm</option><option>in</option></select>
      </label>
      <table>
        <thead><tr><th>Size</th>{COLS.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td><input aria-label={`Size ${i + 1} label`} value={r.label} onChange={(e) => set(i, 'label', e.target.value)} placeholder="M" /></td>
              {COLS.map((c) => (
                <td key={c}><input aria-label={`Size ${i + 1} ${c}`} value={r[c]} onChange={(e) => set(i, c, e.target.value)} placeholder="86-91" /></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        <button type="button" className="secondary" onClick={() => setRows((rs) => [...rs, blankRow()])}>Add size</button>
        <button>Save size chart</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  )
}

export default function SizeAdvice({ garment, onChange }) {
  const [advice, setAdvice] = useState(null)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(false)

  async function check() {
    setError('')
    try {
      setAdvice(await api(`/garments/${garment.id}/size-advice`))
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="advice" data-testid="size-advice">
      <button className="secondary" onClick={check}>Size advice</button>
      {error && <p className="error small">{error}</p>}
      {advice && (advice.recommended ? (
        <div>
          <p><strong data-testid="recommended-size">Size {advice.recommended}</strong> <span className="muted small">({advice.confidence} confidence)</span></p>
          <ul className="small">{advice.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      ) : (
        <p className="muted small">{advice.reason}</p>
      ))}
      {advice && !garment.size_chart && !editing && (
        <button className="link" onClick={() => setEditing(true)}>Enter the size chart</button>
      )}
      {editing && (
        <ChartForm garment={garment} onSaved={(g) => { onChange(g); setEditing(false); setAdvice(null) }} />
      )}
    </div>
  )
}
