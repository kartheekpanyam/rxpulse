import { useState, useEffect } from 'react'
import { getStats, getDrugCoverages, searchPolicy } from '../api'

const MAX_CRITERIA = 5

const STATUS_CONFIG = {
  required:     { color: 'bg-[var(--color-error)]',   ring: 'ring-[var(--color-error)]/20',   text: 'Required',     badge: 'bg-[#FFEBEE] text-[#C62828]' },
  restricted:   { color: 'bg-[var(--color-warning)]',  ring: 'ring-[var(--color-warning)]/20',  text: 'Restricted',   badge: 'bg-[#FFF8E1] text-[#E65100]' },
  not_required: { color: 'bg-[var(--color-success)]',  ring: 'ring-[var(--color-success)]/20',  text: 'Not Required', badge: 'bg-[#EDF7ED] text-[#2E7D32]' },
  open:         { color: 'bg-[var(--color-success)]',  ring: 'ring-[var(--color-success)]/20',  text: 'Open',         badge: 'bg-[#EDF7ED] text-[#2E7D32]' },
}

function buildPathway(policy) {
  const steps = []

  // Covered Indications
  if (policy.covered_indications?.length > 0) {
    steps.push({
      id: 'indications', title: 'Covered Indications',
      status: 'required',
      criteria: policy.covered_indications,
      tip: 'Patient must have one of these diagnoses documented',
    })
  }

  // Prior Authorization
  steps.push({
    id: 'pa', title: 'Prior Authorization',
    status: policy.prior_auth_required ? 'required' : 'not_required',
    criteria: policy.prior_auth_required
      ? (policy.pa_criteria?.length > 0 ? policy.pa_criteria : ['Prior authorization required — check payer portal for specific criteria'])
      : ['No prior authorization required for this drug'],
    tip: policy.prior_auth_required ? 'Submit all required documentation with the PA request' : null,
  })

  // Step Therapy
  if (policy.step_therapy?.required) {
    steps.push({
      id: 'step', title: 'Step Therapy',
      status: 'required',
      criteria: [policy.step_therapy.details || 'Step therapy required — must try preferred alternatives first'],
      tip: 'Document prior therapy attempts, dates, and reasons for discontinuation',
    })
  }

  // Site of Care
  if (policy.site_of_care?.allowed?.length > 0) {
    const siteCriteria = []
    if (policy.site_of_care.allowed.length > 0) siteCriteria.push('Approved: ' + policy.site_of_care.allowed.join(', '))
    if (policy.site_of_care.restricted?.length > 0) siteCriteria.push('Restricted: ' + policy.site_of_care.restricted.join(', '))
    if (policy.site_of_care.preferred) siteCriteria.push('Preferred: ' + policy.site_of_care.preferred)

    steps.push({
      id: 'site', title: 'Site of Care',
      status: policy.site_of_care.restricted?.length > 0 ? 'restricted' : 'open',
      criteria: siteCriteria,
      tip: null,
    })
  }

  // Quantity Limit
  if (policy.quantity_limit) {
    steps.push({
      id: 'quantity', title: 'Quantity Limit',
      status: 'required',
      criteria: [policy.quantity_limit_detail || 'Quantity limits apply — check policy for specifics'],
      tip: null,
    })
  }

  return steps
}

export default function PathwayView() {
  const [payers, setPayers] = useState([])
  const [selectedPayer, setSelectedPayer] = useState('')
  const [drugs, setDrugs] = useState([])
  const [selectedDrug, setSelectedDrug] = useState('')
  const [policies, setPolicies] = useState([])
  const [expandedStep, setExpandedStep] = useState(null)

  // Load payer list
  useEffect(() => {
    getStats()
      .then(data => {
        const list = data.payer_list || []
        setPayers(list)
        if (list.length > 0) setSelectedPayer(list[0])
      })
      .catch(() => {})
  }, [])

  // Load drugs for selected payer (deduped by generic name)
  useEffect(() => {
    if (!selectedPayer) return
    getDrugCoverages({ limit: 200 })
      .then(data => {
        const rows = data || []
        const payerDrugs = rows
          .filter(r => (r.payer || '').toLowerCase() === selectedPayer.toLowerCase())
          .map(r => r.drug_name)
        const unique = [...new Set(payerDrugs)].sort()
        setDrugs(unique)
        setSelectedDrug(prev => unique.includes(prev) ? prev : (unique[0] || ''))
      })
      .catch(() => setDrugs([]))
  }, [selectedPayer])

  // Load policy for selected drug
  useEffect(() => {
    if (!selectedDrug) { setPolicies([]); return }
    searchPolicy({ drug: selectedDrug })
      .then(data => setPolicies(data.policies || []))
      .catch(() => setPolicies([]))
  }, [selectedDrug])

  // Find the policy matching selected payer — pick highest confidence
  const matchingPolicies = policies.filter(p => (p.payer || '').toLowerCase() === selectedPayer.toLowerCase())
  const primaryPolicy = matchingPolicies.sort((a, b) => (b.confidence_score || 0) - (a.confidence_score || 0))[0] || null
  const primarySteps = primaryPolicy ? buildPathway(primaryPolicy) : []

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <h1 className="text-2xl font-bold text-[var(--color-primary-deep)] mb-1">Coverage Pathway</h1>
      <p className="theme-muted mb-6">Step-by-step approval requirements for medical benefit drugs by payer</p>

      {/* Selectors */}
      <div className="flex gap-3 items-end flex-wrap mb-8">
        <div>
          <label className="theme-muted text-xs block mb-1">Payer</label>
          <select value={selectedPayer} onChange={e => setSelectedPayer(e.target.value)}
            className="theme-input text-sm rounded-lg px-3 py-2">
            {payers.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div>
          <label className="theme-muted text-xs block mb-1">Drug</label>
          <select value={selectedDrug} onChange={e => setSelectedDrug(e.target.value)}
            className="theme-input text-sm rounded-lg px-3 py-2">
            {drugs.length === 0
              ? <option value="">No drugs for this payer</option>
              : drugs.map(d => <option key={d} value={d}>{d}</option>)
            }
          </select>
        </div>
      </div>

      {/* No data */}
      {!primaryPolicy && selectedDrug && (
        <div className="theme-card rounded-xl p-12 text-center">
          <p className="theme-muted">No policy data found for {selectedDrug} under {selectedPayer}.</p>
        </div>
      )}

      {/* Flowchart */}
      {primaryPolicy && (
        <div>
          <div className="theme-card rounded-xl p-5 mb-6">
            <div className="flex items-center gap-3 flex-wrap">
              <p className="text-[var(--color-primary-deep)] font-semibold capitalize">{selectedDrug}</p>
              <span className="theme-muted text-sm">—</span>
              <p className="text-[var(--color-primary-soft)] text-sm">{selectedPayer}</p>
              {primaryPolicy.coverage_status && (
                <span className={`text-xs font-semibold px-2.5 py-0.5 rounded-full ${
                  primaryPolicy.coverage_status === 'covered' ? 'bg-[#EDF7ED] text-[#2E7D32]' :
                  primaryPolicy.coverage_status === 'not_covered' ? 'bg-[#FFEBEE] text-[#C62828]' :
                  'bg-[#FFF8E1] text-[#E65100]'
                }`}>
                  {primaryPolicy.coverage_status === 'covered' ? 'Covered' :
                   primaryPolicy.coverage_status === 'not_covered' ? 'Not Covered' : 'Restricted'}
                </span>
              )}
            </div>
          </div>

          <div className="space-y-0 ml-2">
            {primarySteps.map((step, i) => (
              <FlowStep key={step.id} step={step} index={i} isLast={i === primarySteps.length - 1}
                expanded={expandedStep === step.id}
                onToggle={() => setExpandedStep(expandedStep === step.id ? null : step.id)}
              />
            ))}
          </div>

          {/* Approval */}
          <div className="flex items-center gap-3 mt-2 ml-2">
            <div className="flex flex-col items-center">
              <div className="w-10 h-10 rounded-full bg-[var(--color-success)] ring-4 ring-[var(--color-success)]/20 flex items-center justify-center">
                <span className="text-white font-bold text-lg">&#10003;</span>
              </div>
            </div>
            <div>
              <p className="text-[#2E7D32] font-semibold">Treatment Approved</p>
              <p className="theme-muted text-xs">All criteria met — drug administration can proceed</p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function FlowStep({ step, index, isLast, expanded, onToggle }) {
  const cfg = STATUS_CONFIG[step.status] || STATUS_CONFIG.required
  const hasMoreCriteria = step.criteria.length > 1

  return (
    <div className="flex gap-4">
      {/* Connector line + circle */}
      <div className="flex flex-col items-center">
        <div className={`w-10 h-10 rounded-full ${cfg.color} ring-4 ${cfg.ring} flex items-center justify-center shrink-0 z-10`}>
          <span className="text-white text-xs font-bold">{index + 1}</span>
        </div>
        {!isLast && <div className="w-0.5 flex-1 bg-[var(--color-border)] min-h-[20px]" />}
      </div>

      {/* Content */}
      <div className="flex-1 pb-5">
        <button onClick={onToggle} className="w-full text-left">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-[var(--color-primary-deep)] font-medium">{step.title}</p>
            <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${cfg.badge}`}>{cfg.text}</span>
            {hasMoreCriteria && (
              <span className={`theme-muted text-xs transition-transform inline-block ${expanded ? 'rotate-180' : ''}`}>&#9662;</span>
            )}
          </div>
          {/* Preview — first criterion truncated */}
          <p className="theme-muted text-xs mt-0.5 line-clamp-1">{step.criteria[0]}</p>
        </button>

        {expanded && (
          <div className="mt-3 theme-card border border-[var(--color-border)] rounded-lg p-4">
            <ul className="space-y-1.5">
              {step.criteria.slice(0, MAX_CRITERIA).map((c, i) => (
                <li key={i} className="text-[var(--color-text)] text-sm flex items-start gap-2">
                  <span className="text-[var(--color-text-muted)] mt-0.5 shrink-0">&#8226;</span>
                  <span className="line-clamp-2">{c}</span>
                </li>
              ))}
            </ul>
            {step.criteria.length > MAX_CRITERIA && (
              <p className="theme-muted text-xs mt-2">+ {step.criteria.length - MAX_CRITERIA} more criteria</p>
            )}
            {step.tip && (
              <div className="bg-[var(--color-surface-soft)] border border-[var(--color-border)] rounded-lg px-3 py-2 mt-3">
                <p className="text-[var(--color-primary-soft)] text-xs">
                  <span className="font-semibold">Tip:</span> {step.tip}
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
