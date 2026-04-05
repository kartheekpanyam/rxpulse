export default function CompareTable({ policies }) {
  if (!policies || policies.length < 2) return null

  const allIndications = [...new Set(policies.flatMap(p => p.covered_indications))]

  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--color-border)] bg-white/80 shadow-[0_18px_40px_rgba(63,85,111,0.08)]">
      <table className="w-full text-sm">
        <thead>
          <tr className="theme-table-header border-b border-[var(--color-border)]">
            <th className="text-left font-semibold px-4 py-3 sticky left-0 theme-table-header min-w-[180px]">Comparison</th>
            {policies.map(p => (
              <th key={p.policy_id} className="text-left text-[var(--color-primary-deep)] font-semibold px-4 py-3 min-w-[250px]">
                <div>{p.payer}</div>
                <div className="theme-muted text-xs font-normal">{p.policy_number}</div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-[rgba(214,228,243,0.75)]">
          {allIndications.map(ind => (
            <tr key={ind} className="hover:bg-[rgba(233,242,251,0.4)]">
              <td className="px-4 py-2 theme-muted text-xs sticky left-0 theme-sticky-cell">{ind}</td>
              {policies.map(p => {
                const covered = p.covered_indications.includes(ind)
                return (
                  <td key={p.policy_id} className="px-4 py-2">
                    <span className={covered ? 'text-[var(--color-primary)]' : 'text-red-500'}>
                      {covered ? 'Covered' : 'Not covered'}
                    </span>
                  </td>
                )
              })}
            </tr>
          ))}

          <tr><td colSpan={policies.length + 1} className="theme-table-header px-4 py-2 text-xs font-semibold uppercase tracking-wide">Policy Details</td></tr>

          <CompareRow label="Prior Authorization" policies={policies} render={p => (
            <span className={p.prior_auth_required ? 'text-[var(--color-primary)]' : 'text-[var(--color-primary-soft)]'}>
              {p.prior_auth_required ? 'Required' : 'Not required'}
            </span>
          )} />

          <CompareRow label="PA Criteria" policies={policies} render={p => (
            <ul className="space-y-1">
              {p.pa_criteria.map((c, i) => <li key={i} className="text-[var(--color-text)] text-xs">{c}</li>)}
            </ul>
          )} />

          <CompareRow label="Step Therapy" policies={policies} render={p => (
            <div>
              <span className={p.step_therapy?.required ? 'text-[var(--color-primary)]' : 'text-[var(--color-primary-soft)]'}>
                {p.step_therapy?.required ? 'Required' : 'Not required'}
              </span>
              {p.step_therapy?.required && (
                <p className="theme-muted text-xs mt-1">{p.step_therapy.details}</p>
              )}
            </div>
          )} />

          <CompareRow label="Site of Care" policies={policies} render={p => (
            <div className="space-y-0.5">
              {p.site_of_care.allowed.map((s, i) => (
                <p key={i} className="text-[var(--color-primary)] text-xs">&#10003; {s}</p>
              ))}
              {p.site_of_care.restricted.map((s, i) => (
                <p key={i} className="text-red-500 text-xs">&#10007; {s}</p>
              ))}
            </div>
          )} />

          <CompareRow label="Clinical Criteria" policies={policies} render={p => (
            <ul className="space-y-1">
              {p.clinical_criteria.map((c, i) => <li key={i} className="text-[var(--color-text)] text-xs">{c}</li>)}
            </ul>
          )} />

          <CompareRow label="Reauthorization" policies={policies} render={p => (
            <span className="text-[var(--color-text)]">{p.reauthorization_interval}</span>
          )} />

          <CompareRow label="Last Updated" policies={policies} render={p => (
            <span className="text-[var(--color-text)]">{p.last_updated}</span>
          )} />
        </tbody>
      </table>
    </div>
  )
}

function CompareRow({ label, policies, render }) {
  return (
    <tr className="hover:bg-[rgba(233,242,251,0.4)]">
      <td className="px-4 py-3 theme-muted text-xs font-medium sticky left-0 theme-sticky-cell align-top">{label}</td>
      {policies.map(p => (
        <td key={p.policy_id} className="px-4 py-3 text-sm align-top">{render(p)}</td>
      ))}
    </tr>
  )
}
