import { useState } from 'react'

const MAX_INDICATIONS = 3

export default function PolicyCard({ policy, extraPolicies = [] }) {
  const [expanded, setExpanded] = useState(false)

  const status = policy.coverage_status || 'unknown'
  const statusStyles = {
    covered: { bg: 'bg-[#EDF7ED]', text: 'text-[#2E7D32]', label: 'Covered' },
    restricted: { bg: 'bg-[#FFF8E1]', text: 'text-[#E65100]', label: 'Restricted' },
    not_covered: { bg: 'bg-[#FFEBEE]', text: 'text-[#C62828]', label: 'Not Covered' },
    unknown: { bg: 'bg-[var(--color-surface-soft)]', text: 'text-[var(--color-text-muted)]', label: 'Unknown' },
  }
  const st = statusStyles[status] || statusStyles.unknown

  const indications = policy.covered_indications || []
  const visibleIndications = expanded ? indications : indications.slice(0, MAX_INDICATIONS)
  const hasMore = indications.length > MAX_INDICATIONS

  // Merge brand names from extra policies (same payer, different product rows)
  const allBrands = [...new Set([
    ...(policy.brand_names || []),
    ...extraPolicies.flatMap(p => p.brand_names || []),
  ])]

  return (
    <div className="theme-card rounded-xl p-5">
      {/* Header row */}
      <div className="flex items-start justify-between gap-4 mb-3">
        <div>
          <div className="flex items-center gap-2.5 flex-wrap">
            <p className="text-[var(--color-primary-deep)] font-semibold text-lg">{policy.payer}</p>
            <span className={`text-xs font-semibold px-2.5 py-0.5 rounded-full ${st.bg} ${st.text}`}>
              {st.label}
            </span>
          </div>
          {policy.policy_number && (
            <p className="theme-muted text-xs mt-0.5">Policy #{policy.policy_number}</p>
          )}
        </div>
        {policy.effective_date && (
          <span className="theme-pill text-xs px-2.5 py-1 rounded-full shrink-0">
            Effective {policy.effective_date}
          </span>
        )}
      </div>

      {/* Brand names */}
      {allBrands.length > 0 && (
        <p className="text-xs theme-muted mb-3">
          Brands: {allBrands.join(', ')}
        </p>
      )}

      {/* Flags row */}
      <div className="flex gap-2 flex-wrap mb-4">
        <Badge active={policy.prior_auth_required} activeLabel="Prior Auth Required" inactiveLabel="No Prior Auth" />
        <Badge active={policy.step_therapy?.required} activeLabel="Step Therapy" inactiveLabel="No Step Therapy" />
        {policy.quantity_limit && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-[var(--color-accent-soft)] text-[var(--color-primary-deep)]">
            Quantity Limit
          </span>
        )}
      </div>

      {/* Indications — limited */}
      {indications.length > 0 && (
        <div className="mb-2">
          <p className="theme-muted text-xs font-semibold uppercase tracking-wide mb-1.5">Covered Indications</p>
          <ul className="space-y-1">
            {visibleIndications.map((ind, i) => (
              <li key={i} className="text-[var(--color-text)] text-sm flex items-start gap-2">
                <span className="text-[var(--color-primary)] mt-0.5 shrink-0">+</span>
                <span className="line-clamp-2">{ind}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Expand / collapse */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="text-[var(--color-primary)] text-sm mt-1 hover:text-[var(--color-accent)] transition-colors"
      >
        {expanded
          ? 'Show less'
          : hasMore
            ? `Show ${indications.length - MAX_INDICATIONS} more indications`
            : policy.pa_criteria?.length > 0
              ? 'Show details'
              : null
        }
      </button>

      {/* Expanded details */}
      {expanded && (
        <div className="mt-3 space-y-3 pt-3 border-t border-[var(--color-border)]">
          {policy.pa_criteria?.length > 0 && (
            <Section title="Prior Authorization Criteria">
              <ol className="space-y-1 list-decimal list-inside">
                {policy.pa_criteria.map((c, i) => (
                  <li key={i} className="text-[var(--color-text)] text-sm">{c}</li>
                ))}
              </ol>
            </Section>
          )}

          {policy.step_therapy?.required && policy.step_therapy.details && (
            <Section title="Step Therapy">
              <p className="text-[var(--color-text)] text-sm">{policy.step_therapy.details}</p>
            </Section>
          )}

          {policy.quantity_limit_detail && (
            <Section title="Quantity Limit">
              <p className="text-[var(--color-text)] text-sm">{policy.quantity_limit_detail}</p>
            </Section>
          )}

          {policy.site_of_care?.allowed?.length > 0 && (
            <Section title="Site of Care">
              <div className="flex gap-2 flex-wrap">
                {policy.site_of_care.allowed.map((site, i) => (
                  <span key={i} className="text-xs px-2 py-0.5 rounded-full bg-[var(--color-surface-soft)] text-[var(--color-primary-deep)]">
                    {site}{site === policy.site_of_care.preferred?.split(' (')[0] ? ' (preferred)' : ''}
                  </span>
                ))}
              </div>
            </Section>
          )}

          {policy.prescriber_requirements && (
            <Section title="Prescriber Requirements">
              <p className="text-[var(--color-text)] text-sm">{policy.prescriber_requirements}</p>
            </Section>
          )}

          {/* Extra product rows from same payer (e.g. brand variants) */}
          {extraPolicies.length > 0 && (
            <Section title={`Additional Product Entries (${extraPolicies.length})`}>
              <div className="space-y-2">
                {extraPolicies.map((ep, i) => (
                  <div key={i} className="theme-section-tint rounded-lg p-3">
                    <p className="text-sm font-medium text-[var(--color-primary-deep)] capitalize">{ep.drug_name}</p>
                    {ep.brand_names?.length > 0 && (
                      <p className="text-xs theme-muted">{ep.brand_names.join(', ')}</p>
                    )}
                    <div className="flex gap-2 mt-1 flex-wrap">
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${(statusStyles[ep.coverage_status] || statusStyles.unknown).bg} ${(statusStyles[ep.coverage_status] || statusStyles.unknown).text}`}>
                        {(statusStyles[ep.coverage_status] || statusStyles.unknown).label}
                      </span>
                      {ep.prior_auth_required && <span className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--color-primary)] text-white">PA</span>}
                      {ep.step_therapy?.required && <span className="text-[10px] px-2 py-0.5 rounded-full bg-[var(--color-primary)] text-white">ST</span>}
                    </div>
                  </div>
                ))}
              </div>
            </Section>
          )}
        </div>
      )}
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div className="mb-2">
      <p className="theme-muted text-xs font-semibold uppercase tracking-wide mb-1.5">{title}</p>
      {children}
    </div>
  )
}

function Badge({ active, activeLabel, inactiveLabel }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full ${
      active
        ? 'bg-[var(--color-primary)] text-white'
        : 'bg-[var(--color-accent-soft)] text-[var(--color-primary-deep)]'
    }`}>
      {active ? activeLabel : inactiveLabel}
    </span>
  )
}
