import { useState, useEffect, useMemo } from 'react'
import { getPolicyChanges } from '../../api'

const CHANGE_TYPES = {
  criteria_updated:  { label: 'Criteria Updated',   bg: 'bg-[#FFF8E1] text-[#E65100]',  border: 'border-[var(--color-warning)]' },
  restriction_added: { label: 'Restriction Added',  bg: 'bg-[#FFEBEE] text-[#C62828]',  border: 'border-[var(--color-error)]' },
  new_coverage:      { label: 'New Coverage',        bg: 'bg-[#EDF7ED] text-[#2E7D32]', border: 'border-[var(--color-success)]' },
  coverage_expanded: { label: 'Coverage Expanded',  bg: 'bg-[#E3F2FD] text-[#1565C0]', border: 'border-[var(--color-primary-soft)]' },
  coverage_removed:  { label: 'Coverage Removed',   bg: 'bg-[#F3E5F5] text-[#6A1B9A]', border: 'border-purple-400' },
}

function dbRowToCard(row, i) {
  return {
    change_id: row.id || `DB_${i}`,
    payer: row.payer || '—',
    drug: row.drug_name || '—',
    change_type: row.change_type || 'criteria_updated',
    change_date: row.change_date || '',
    summary: row.patient_impact_summary || row.summary || '—',
    details: {
      field_changed: row.field_changed || 'Policy Change',
      old_value: row.old_value || '—',
      new_value: row.new_value || '—',
    },
  }
}

export default function PolicyTimeline() {
  const [changes, setChanges] = useState([])
  const [payerFilter, setPayerFilter] = useState('All')
  const [drugFilter, setDrugFilter] = useState('All')
  const [typeFilter, setTypeFilter] = useState('All')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    getPolicyChanges({ limit: 50 })
      .then(data => {
        const rows = data || []
        setChanges(rows.map(dbRowToCard))
      })
      .catch(() => setChanges([]))
      .finally(() => setLoading(false))
  }, [])

  const payers = useMemo(() => ['All', ...new Set(changes.map(c => c.payer).filter(Boolean))], [changes])
  const drugs  = useMemo(() => ['All', ...new Set(changes.map(c => c.drug).filter(Boolean))], [changes])

  const filtered = useMemo(() => {
    return changes.filter(c => {
      if (payerFilter !== 'All' && c.payer !== payerFilter) return false
      if (drugFilter  !== 'All' && c.drug  !== drugFilter)  return false
      if (typeFilter  !== 'All' && c.change_type !== typeFilter) return false
      return true
    }).sort((a, b) => b.change_date.localeCompare(a.change_date))
  }, [changes, payerFilter, drugFilter, typeFilter])

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <h1 className="text-2xl font-bold text-[var(--color-primary-deep)] mb-1">Policy Changes</h1>
      <p className="theme-muted mb-6">Track medical benefit policy updates across payers</p>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap mb-6">
        <FilterSelect label="Payer"       value={payerFilter} onChange={setPayerFilter} options={payers} />
        <FilterSelect label="Drug"        value={drugFilter}  onChange={setDrugFilter}  options={drugs} />
        <FilterSelect label="Change Type" value={typeFilter}  onChange={setTypeFilter}
          options={['All', ...Object.keys(CHANGE_TYPES)]}
          displayFn={v => v === 'All' ? 'All' : (CHANGE_TYPES[v]?.label || v)}
        />
        <div className="ml-auto theme-muted text-sm self-end">
          {filtered.length} change{filtered.length !== 1 ? 's' : ''} found
        </div>
      </div>

      {/* Change Feed */}
      {loading ? (
        <p className="theme-muted text-center py-12">Loading policy changes...</p>
      ) : (
        <div className="space-y-3">
          {filtered.length === 0
            ? <p className="theme-muted text-center py-12">No policy changes found. Upload multiple versions of the same policy to generate change records.</p>
            : filtered.map(change => <ChangeCard key={change.change_id} change={change} />)
          }
        </div>
      )}
    </div>
  )
}

function ChangeCard({ change }) {
  const type = CHANGE_TYPES[change.change_type] || CHANGE_TYPES.criteria_updated
  return (
    <div className={`theme-card border rounded-xl p-4 ${type.border}`}>
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${type.bg}`}>{type.label}</span>
          <span className="text-[var(--color-primary-deep)] font-semibold">{change.drug}</span>
        </div>
        <div className="text-right shrink-0 ml-4">
          <p className="theme-muted text-xs">{change.payer}</p>
          <p className="theme-muted text-xs">{change.change_date}</p>
        </div>
      </div>
      <p className="text-[var(--color-text)] text-sm mb-3">{change.summary}</p>
      <div className="bg-[var(--color-surface-soft)] rounded-lg p-3">
        <p className="theme-muted text-xs mb-2">{change.details.field_changed}</p>
        <div className="flex gap-3 text-xs">
          <div className="flex-1">
            <p className="text-[var(--color-error)] mb-1">Before:</p>
            <p className="theme-muted">{change.details.old_value}</p>
          </div>
          <div className="theme-muted self-center text-lg">&rarr;</div>
          <div className="flex-1">
            <p className="text-[var(--color-success)] mb-1">After:</p>
            <p className="text-[var(--color-text)]">{change.details.new_value}</p>
          </div>
        </div>
      </div>
    </div>
  )
}

function FilterSelect({ label, value, onChange, options, displayFn }) {
  return (
    <div>
      <label className="theme-muted text-xs block mb-1">{label}</label>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="theme-card border border-[var(--color-border)] text-[var(--color-text)] text-sm rounded-lg px-3 py-2 focus:outline-none focus:border-[var(--color-accent)]"
      >
        {options.map(opt => (
          <option key={opt} value={opt}>{displayFn ? displayFn(opt) : opt}</option>
        ))}
      </select>
    </div>
  )
}

