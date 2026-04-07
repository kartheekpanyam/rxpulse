import { useState, useEffect, useMemo } from 'react'
import { getDrugCoverages } from '../api'

const STATUS = {
  covered:     { label: 'Covered',     short: 'C', bg: 'bg-[#4CAF50]', textColor: 'text-white' },
  restricted:  { label: 'Restricted',  short: 'R', bg: 'bg-[#F59E0B]', textColor: 'text-white' },
  not_covered: { label: 'Not Covered', short: '—', bg: 'bg-[#9CA3AF]', textColor: 'text-white' },
}

/**
 * Aggregate brand-level rows into one cell per (generic drug × payer).
 * Priority: if ANY row for that pair says "covered", the cell is "covered".
 * If any says "restricted", it's "restricted". Otherwise "not_covered".
 */
function buildMatrix(rows) {
  const cellMap = {}   // "drugName|payer" → { status, pa, step, brands[] }
  const drugMap = {}   // drugName → { code }
  const payerSet = new Set()

  for (const row of rows) {
    const drug  = (row.drug_name || '').toLowerCase()
    const payer = row.payer || 'Unknown'
    if (!drug) continue

    payerSet.add(payer)

    // Track drug-level info (pick first HCPCS code we find)
    if (!drugMap[drug]) {
      drugMap[drug] = { name: row.drug_name, code: row.hcpcs_code || '' }
    } else if (!drugMap[drug].code && row.hcpcs_code) {
      drugMap[drug].code = row.hcpcs_code
    }

    const key = `${drug}|${payer}`
    const rowStatus = row.coverage_status || 'unknown'

    if (!cellMap[key]) {
      cellMap[key] = {
        status: normalizeStatus(rowStatus),
        pa: !!row.prior_authorization,
        step: !!row.step_therapy,
        brands: new Set(row.brand_names || []),
      }
    } else {
      const cell = cellMap[key]
      // Upgrade: covered > restricted > not_covered
      const incoming = normalizeStatus(rowStatus)
      if (incoming === 'covered' && cell.status !== 'covered') {
        cell.status = 'covered'
      } else if (incoming === 'restricted' && cell.status === 'not_covered') {
        cell.status = 'restricted'
      }
      if (row.prior_authorization) cell.pa = true
      if (row.step_therapy) cell.step = true
      for (const b of (row.brand_names || [])) cell.brands.add(b)
    }
  }

  // Convert brand Sets to arrays
  for (const cell of Object.values(cellMap)) {
    cell.brands = [...cell.brands]
  }

  const drugs = Object.entries(drugMap)
    .map(([key, val]) => ({ generic: key, name: val.name, code: val.code }))
    .sort((a, b) => a.generic.localeCompare(b.generic))

  const payers = [...payerSet].sort()

  return { drugs, payers, matrix: cellMap }
}

function normalizeStatus(raw) {
  if (raw === 'covered') return 'covered'
  if (raw === 'not_covered') return 'not_covered'
  // "restricted", "unknown", anything else → restricted (drug exists in policy)
  return 'restricted'
}

function exportMatrixCSV(drugs, payers, matrix) {
  const header = ['Drug', 'HCPCS', ...payers]
  const rows = drugs.map(drug => {
    const cells = payers.map(payer => {
      const cell = matrix[`${drug.generic}|${payer}`]
      if (!cell) return ''
      let val = cell.status
      if (cell.pa) val += ' (PA)'
      if (cell.step) val += ' (ST)'
      return val
    })
    return [drug.name, drug.code || '', ...cells]
  })
  const csv = [header, ...rows].map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'coverage_matrix.csv'
  a.click()
  URL.revokeObjectURL(url)
}

export default function HeatmapView() {
  const [loading, setLoading]     = useState(true)
  const [drugs, setDrugs]         = useState([])
  const [payers, setPayers]       = useState([])
  const [matrix, setMatrix]       = useState({})
  const [hoveredCell, setHovered]  = useState(null)
  const [hasData, setHasData]     = useState(false)

  useEffect(() => {
    getDrugCoverages()
      .then(data => {
        const rows = data || []
        if (rows.length > 0) {
          const built = buildMatrix(rows)
          setDrugs(built.drugs)
          setPayers(built.payers)
          setMatrix(built.matrix)
          setHasData(true)
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const stats = useMemo(() => {
    const vals = Object.values(matrix)
    const total = vals.length || 1
    const covered = vals.filter(v => v.status === 'covered').length
    const restricted = vals.filter(v => v.status === 'restricted').length
    const notCovered = vals.filter(v => v.status === 'not_covered').length
    return { covered, restricted, notCovered, total }
  }, [matrix])

  return (
    <div className="max-w-7xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-start justify-between mb-6 flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold text-[var(--color-primary-deep)] mb-1">Coverage Matrix</h1>
          <p className="theme-muted">
            Drug-vs-payer coverage status at a glance
            {hasData && (
              <span className="ml-2 text-[var(--color-primary)] text-xs">
                {drugs.length} drugs x {payers.length} payers
              </span>
            )}
          </p>
        </div>
        {hasData && stats.total > 0 && (
          <div className="flex items-center gap-5">
            <div className="flex gap-5 text-center">
              <div>
                <p className="text-xl font-bold text-[var(--color-success)]">{Math.round((stats.covered / stats.total) * 100)}%</p>
                <p className="theme-muted text-xs">Covered</p>
              </div>
              <div>
                <p className="text-xl font-bold text-[var(--color-warning)]">{Math.round((stats.restricted / stats.total) * 100)}%</p>
                <p className="theme-muted text-xs">Restricted</p>
              </div>
            </div>
            <button onClick={() => exportMatrixCSV(drugs, payers, matrix)}
              className="theme-button-secondary px-4 py-2 rounded-lg text-sm">
              Export CSV
            </button>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="flex gap-4 mb-4 flex-wrap">
        {Object.entries(STATUS).map(([key, s]) => (
          <div key={key} className="flex items-center gap-1.5 text-xs">
            <div className={`w-5 h-5 rounded flex items-center justify-center ${s.bg}`}>
              <span className={`text-[10px] font-bold ${s.textColor}`}>{s.short}</span>
            </div>
            <span className="theme-muted">{s.label}</span>
          </div>
        ))}
      </div>

      {/* Loading */}
      {loading && (
        <div className="text-center py-16">
          <div className="inline-flex items-center gap-3">
            <div className="w-5 h-5 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin" />
            <p className="theme-muted">Loading coverage matrix...</p>
          </div>
        </div>
      )}

      {/* No data */}
      {!loading && !hasData && (
        <div className="theme-card rounded-xl p-12 text-center">
          <p className="theme-muted">No coverage data found. Upload policy documents to populate the matrix.</p>
        </div>
      )}

      {/* Matrix Table */}
      {!loading && hasData && (
        <>
          <div className="overflow-x-auto rounded-xl border border-[var(--color-border)]">
            <table className="w-full">
              <thead>
                <tr className="bg-[var(--color-panel)]">
                  <th className="text-left theme-muted font-semibold text-xs px-4 py-3 sticky left-0 bg-[var(--color-panel)] z-10 min-w-[200px]">Drug</th>
                  {payers.map(p => (
                    <th key={p} className="text-center theme-muted font-semibold text-xs px-3 py-3 min-w-[130px]">{p}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {drugs.map((drug, rowIdx) => (
                  <tr key={drug.generic} className="hover:bg-[var(--color-surface-soft)]">
                    <td className="px-4 py-3 sticky left-0 bg-[var(--color-surface)] z-10">
                      <p className="text-[var(--color-primary-deep)] text-sm font-medium capitalize">{drug.name}</p>
                      <p className="theme-muted text-xs">{drug.code || '—'}</p>
                    </td>
                    {payers.map(payer => {
                      const key = `${drug.generic}|${payer}`
                      const cell = matrix[key]
                      const st = cell ? (STATUS[cell.status] || STATUS.not_covered) : null
                      const isHovered = hoveredCell === key
                      // Tooltip goes below for top rows to avoid clipping
                      const tooltipBelow = rowIdx < 2

                      return (
                        <td key={payer} className="px-3 py-3 text-center relative"
                          onMouseEnter={() => setHovered(key)}
                          onMouseLeave={() => setHovered(null)}>
                          {cell ? (
                            <div className={`inline-flex items-center justify-center w-10 h-10 rounded-lg ${st.bg} transition-transform ${isHovered ? 'scale-110 ring-2 ring-[var(--color-primary-soft)]' : ''}`}>
                              <span className={`text-xs font-bold ${st.textColor}`}>{st.short}</span>
                            </div>
                          ) : (
                            <span className="text-xs theme-muted">—</span>
                          )}

                          {/* Tooltip */}
                          {isHovered && cell && (
                            <div className={`absolute z-30 left-1/2 -translate-x-1/2 bg-white border border-[var(--color-border)] rounded-lg p-3 w-56 text-left shadow-xl ${
                              tooltipBelow ? 'top-full mt-2' : 'bottom-full mb-2'
                            }`}>
                              <p className="text-[var(--color-primary-deep)] text-xs font-semibold mb-1.5 capitalize">{drug.name} — {payer}</p>
                              <div className="space-y-1 text-xs">
                                <p className="theme-muted">Status: <span className="text-[var(--color-primary-deep)] font-medium">{st.label}</span></p>
                                {cell.pa && <p className="theme-muted">Prior Auth: <span className="text-[var(--color-warning)]">Required</span></p>}
                                {cell.step && <p className="theme-muted">Step Therapy: <span className="text-[var(--color-warning)]">Required</span></p>}
                                {cell.brands.length > 0 && (
                                  <p className="theme-muted text-[10px] mt-1">Brands: {cell.brands.slice(0, 5).join(', ')}{cell.brands.length > 5 ? '...' : ''}</p>
                                )}
                              </div>
                            </div>
                          )}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Summary */}
          <div className="mt-4 grid grid-cols-3 gap-3">
            {[
              { label: 'Covered',     count: stats.covered,    color: 'bg-[#4CAF50]' },
              { label: 'Restricted',  count: stats.restricted, color: 'bg-[#F59E0B]' },
              { label: 'Not Covered', count: stats.notCovered, color: 'bg-[#9CA3AF]' },
            ].map(s => (
              <div key={s.label} className="theme-card border border-[var(--color-border)] rounded-lg p-3 text-center">
                <div className="flex items-center justify-center gap-2 mb-1">
                  <div className={`w-3 h-3 rounded ${s.color}`} />
                  <span className="text-[var(--color-primary-deep)] font-bold">{s.count}</span>
                </div>
                <p className="theme-muted text-[10px]">{s.label}</p>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
