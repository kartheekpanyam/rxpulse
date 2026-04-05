import { useState, useEffect, useRef } from 'react'
import axios from 'axios'

const API = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

const MAX_INDICATIONS = 5

function buildPayerData(results) {
  const payers = {}
  for (const r of results) {
    const payerName = r.payer || 'Unknown'
    const sites = (r.site_of_care || []).map(s =>
      s === 'hospital' ? 'Hospital OP' : s === 'office' ? 'Physician office' : s === 'home' ? 'Home infusion' : s
    )
    const paText = r.prior_authorization
      ? (r.prior_auth_criteria?.length ? r.prior_auth_criteria.join('; ') : 'Required')
      : 'Not required'
    const stepText = r.step_therapy
      ? (r.step_therapy_requirements?.length ? r.step_therapy_requirements.join('; ') : 'Required')
      : 'Not required'

    payers[payerName] = {
      policy: r.policy_number || '—',
      updated: r.effective_date || '—',
      covered_indications: r.covered_indications || [],
      pa: paText,
      step_therapy: stepText,
      sites,
      coverage_status: r.coverage_status || 'unknown',
      quantity_limit: r.quantity_limit ? (r.quantity_limit_detail || 'Yes') : 'No',
    }
  }
  return payers
}

function deriveKeyDifferences(payerData) {
  const diffs = []
  const names = Object.keys(payerData)
  if (names.length < 2) return diffs

  const indCounts = names.map(p => ({ p, n: (payerData[p].covered_indications || []).length })).sort((a, b) => b.n - a.n)
  if (indCounts[0].n !== indCounts[indCounts.length - 1].n) {
    diffs.push({ dimension: 'Indication Breadth', finding: `${indCounts[0].p} covers the most indications (${indCounts[0].n}); ${indCounts[indCounts.length - 1].p} covers the fewest (${indCounts[indCounts.length - 1].n})` })
  }

  const withStep = names.filter(p => payerData[p].step_therapy !== 'Not required')
  const noStep = names.filter(p => payerData[p].step_therapy === 'Not required')
  if (withStep.length > 0 && noStep.length > 0) {
    diffs.push({ dimension: 'Step Therapy', finding: `${withStep.join(', ')} require step therapy; ${noStep.join(', ')} do not` })
  }

  const withPA = names.filter(p => payerData[p].pa !== 'Not required')
  const noPA = names.filter(p => payerData[p].pa === 'Not required')
  if (noPA.length > 0) {
    diffs.push({ dimension: 'Prior Authorization', finding: `${withPA.join(', ')} require PA; ${noPA.join(', ')} do not` })
  }

  return diffs
}

const STATUS_STYLES = {
  covered: 'bg-[#EDF7ED] text-[#2E7D32]',
  restricted: 'bg-[#FFF8E1] text-[#E65100]',
  not_covered: 'bg-[#FFEBEE] text-[#C62828]',
  unknown: 'bg-[var(--color-surface-soft)] text-[var(--color-text-muted)]',
}

export default function ReportView() {
  const [drugInput, setDrugInput] = useState('')
  const [drugList, setDrugList] = useState([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState(null)
  const [reportDrug, setReportDrug] = useState('')
  const [payerData, setPayerData] = useState({})
  const [keyDifferences, setKeyDifferences] = useState([])
  const [hcpcsCode, setHcpcsCode] = useState('')
  const [summary, setSummary] = useState('')
  const wrapperRef = useRef(null)

  // Load drug list for dropdown
  useEffect(() => {
    axios.get(`${API}/drugs/list`)
      .then(res => setDrugList(res.data || []))
      .catch(() => setDrugList([]))
  }, [])

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) setShowDropdown(false)
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  const filtered = drugInput.trim()
    ? drugList.filter(d => d.drug_name.toLowerCase().includes(drugInput.toLowerCase()))
    : drugList

  function selectDrug(name) {
    setDrugInput(name)
    setShowDropdown(false)
  }

  async function generateReport() {
    const drug = drugInput.trim()
    if (!drug) return
    setGenerating(true)
    setError(null)
    setReportDrug('')

    try {
      const compareRes = await axios.get(`${API}/compare/plans`, { params: { drug } })
      const results = compareRes.data.results || []

      if (results.length === 0) {
        setError('No policy data found for this drug.')
        setGenerating(false)
        return
      }

      const payers = buildPayerData(results)
      const diffs = deriveKeyDifferences(payers)
      const hcpcs = results.find(r => r.hcpcs_code)?.hcpcs_code || ''

      // AI summary
      let aiSummary = ''
      const payerNames = Object.keys(payers)
      try {
        const qaRes = await axios.post(`${API}/qa/ask`, {
          question: `For ${drug}, provide a 2-3 sentence executive summary of coverage across ${payerNames.join(', ')}. Focus on key differences in access requirements.`,
        })
        aiSummary = (qaRes.data.answer || '').replace(/\*\*(.*?)\*\*/g, '$1').replace(/^#{1,6}\s+/gm, '').trim()
      } catch {
        aiSummary = `${drug} is covered by ${payerNames.length} payer(s): ${payerNames.join(', ')}.`
      }

      setPayerData(payers)
      setKeyDifferences(diffs)
      setReportDrug(drug)
      setHcpcsCode(hcpcs)
      setSummary(aiSummary)
    } catch {
      setError('Failed to generate report. Make sure the backend is running.')
    } finally {
      setGenerating(false)
    }
  }

  const payerNames = Object.keys(payerData)

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <h1 className="text-2xl font-bold text-[var(--color-primary-deep)] mb-1">Policy Intelligence Report</h1>
      <p className="theme-muted mb-6">Generate cross-payer comparison reports for medical benefit drugs</p>

      {/* Search */}
      <div className="theme-card rounded-xl p-5 mb-6 print:hidden">
        <div ref={wrapperRef} className="relative">
          <div className="flex gap-3">
            <div className="relative flex-1">
              <input
                value={drugInput}
                onChange={e => { setDrugInput(e.target.value); setShowDropdown(true) }}
                onFocus={() => setShowDropdown(true)}
                onKeyDown={e => e.key === 'Enter' && generateReport()}
                placeholder="Search by drug name..."
                className="theme-input w-full rounded-xl px-4 py-3"
              />
              {showDropdown && filtered.length > 0 && (
                <div className="absolute z-20 top-full left-0 right-0 mt-1 theme-card rounded-xl shadow-lg border border-[var(--color-border)] max-h-64 overflow-y-auto">
                  {filtered.map(d => (
                    <button key={d.drug_name} onClick={() => selectDrug(d.drug_name)}
                      className="w-full text-left px-4 py-2.5 hover:bg-[var(--color-surface-soft)] transition-colors flex items-center justify-between">
                      <span className="text-sm text-[var(--color-primary-deep)] font-medium capitalize">{d.drug_name}</span>
                      <span className="text-xs theme-muted">{d.payer_count} payer{d.payer_count !== 1 ? 's' : ''}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button onClick={generateReport} disabled={!drugInput.trim() || generating}
              className="theme-button-primary disabled:opacity-50 px-6 py-3 rounded-xl font-medium transition-colors shrink-0">
              {generating ? 'Generating...' : 'Generate Report'}
            </button>
          </div>
        </div>
        {error && <p className="text-[var(--color-error)] text-sm mt-3">{error}</p>}
      </div>

      {/* Loading */}
      {generating && (
        <div className="text-center py-16">
          <div className="inline-flex items-center gap-3">
            <div className="w-5 h-5 border-2 border-[var(--color-primary)] border-t-transparent rounded-full animate-spin" />
            <p className="theme-muted">Fetching coverage data and generating analysis...</p>
          </div>
        </div>
      )}

      {/* Report */}
      {reportDrug && !generating && payerNames.length > 0 && (
        <div>
          {/* Header */}
          <div className="theme-card rounded-xl p-6 mb-4">
            <div className="flex items-start justify-between flex-wrap gap-4">
              <div>
                <p className="theme-muted text-xs font-semibold uppercase tracking-wide mb-1">Policy Intelligence Report</p>
                <h2 className="text-2xl font-bold text-[var(--color-primary-deep)] capitalize">{reportDrug}</h2>
                <div className="flex gap-2 mt-2 flex-wrap">
                  {hcpcsCode && <span className="theme-pill text-xs px-2 py-0.5 rounded-full">{hcpcsCode}</span>}
                  <span className="theme-pill text-xs px-2 py-0.5 rounded-full">{payerNames.length} payer{payerNames.length !== 1 ? 's' : ''}</span>
                </div>
              </div>
              <button onClick={() => window.print()}
                className="theme-button-secondary px-4 py-2 rounded-lg text-sm print:hidden">
                Export PDF
              </button>
            </div>
            {summary && (
              <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
                <p className="theme-muted text-xs font-semibold uppercase tracking-wide mb-2">Executive Summary</p>
                <p className="text-[var(--color-text)] text-sm leading-relaxed">{summary}</p>
              </div>
            )}
          </div>

          {/* Comparison Table */}
          <div className="theme-card rounded-xl overflow-hidden mb-4">
            <div className="px-6 py-3 border-b border-[var(--color-border)]">
              <p className="theme-muted text-xs font-semibold uppercase tracking-wide">Coverage Comparison</p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--color-border)]">
                    <th className="text-left theme-muted font-semibold text-xs px-4 py-3 min-w-[140px]">Dimension</th>
                    {payerNames.map(p => (
                      <th key={p} className="text-left text-[var(--color-primary-deep)] font-semibold text-xs px-4 py-3 min-w-[180px]">
                        {p}
                        <span className="block theme-muted font-normal">{payerData[p].policy}</span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-[var(--color-border)]">
                  {/* Coverage Status */}
                  <Row label="Coverage Status" payers={payerNames} data={payerData}
                    render={p => {
                      const s = p.coverage_status || 'unknown'
                      return <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${STATUS_STYLES[s] || STATUS_STYLES.unknown}`}>
                        {s === 'covered' ? 'Covered' : s === 'not_covered' ? 'Not Covered' : 'Restricted'}
                      </span>
                    }} />
                  {/* Indications (truncated) */}
                  <Row label="Covered Indications" payers={payerNames} data={payerData}
                    render={p => {
                      const inds = p.covered_indications || []
                      if (inds.length === 0) return <span className="theme-muted text-xs">Not specified</span>
                      return (
                        <div>
                          <ul className="space-y-0.5">
                            {inds.slice(0, MAX_INDICATIONS).map((ind, i) => (
                              <li key={i} className="text-[var(--color-text)] text-xs line-clamp-1">{ind}</li>
                            ))}
                          </ul>
                          {inds.length > MAX_INDICATIONS && (
                            <p className="theme-muted text-[10px] mt-1">+ {inds.length - MAX_INDICATIONS} more</p>
                          )}
                        </div>
                      )
                    }} />
                  <Row label="Prior Authorization" payers={payerNames} data={payerData} field="pa" />
                  <Row label="Step Therapy" payers={payerNames} data={payerData} field="step_therapy" />
                  <Row label="Approved Sites" payers={payerNames} data={payerData}
                    render={p => p.sites?.length > 0
                      ? <div>{p.sites.map((s, i) => <p key={i} className="text-xs text-[var(--color-text)]">{s}</p>)}</div>
                      : <span className="theme-muted text-xs">Not specified</span>
                    } />
                  <Row label="Quantity Limit" payers={payerNames} data={payerData} field="quantity_limit" />
                  <Row label="Effective Date" payers={payerNames} data={payerData} field="updated" />
                </tbody>
              </table>
            </div>
          </div>

          {/* Key Differences */}
          {keyDifferences.length > 0 && (
            <div className="theme-card rounded-xl p-5">
              <p className="theme-muted text-xs font-semibold uppercase tracking-wide mb-3">Key Differences</p>
              <div className="space-y-3">
                {keyDifferences.map((diff, i) => (
                  <div key={i} className="flex gap-3">
                    <div className="w-6 h-6 rounded-full bg-[var(--color-warning)] flex items-center justify-center shrink-0 mt-0.5">
                      <span className="text-white text-xs font-bold">{i + 1}</span>
                    </div>
                    <div>
                      <p className="text-[var(--color-primary-deep)] font-medium text-sm">{diff.dimension}</p>
                      <p className="theme-muted text-sm">{diff.finding}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Row({ label, payers, data, field, render }) {
  return (
    <tr>
      <td className="px-4 py-3 theme-muted text-xs font-medium align-top">{label}</td>
      {payers.map(p => (
        <td key={p} className="px-4 py-3 align-top">
          {render ? render(data[p]) : <span className="text-[var(--color-text)] text-xs">{data[p]?.[field] ?? '—'}</span>}
        </td>
      ))}
    </tr>
  )
}
