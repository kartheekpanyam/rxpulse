import { useState, useEffect, useRef } from 'react'
import axios from 'axios'
import PolicyCard from './PolicyCard'

const API = import.meta.env.VITE_API_BASE_URL

export default function DrugSearch() {
  const [drug, setDrug] = useState('')
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Dropdown state
  const [drugList, setDrugList] = useState([])
  const [showDropdown, setShowDropdown] = useState(false)
  const [filteredDrugs, setFilteredDrugs] = useState([])
  const wrapperRef = useRef(null)

  // Fetch drug list on mount
  useEffect(() => {
    axios.get(`${API}/drugs/list`)
      .then(res => setDrugList(res.data || []))
      .catch(() => setDrugList([]))
  }, [])

  // Filter dropdown as user types
  useEffect(() => {
    if (!drug.trim()) {
      setFilteredDrugs(drugList)
    } else {
      const q = drug.toLowerCase()
      setFilteredDrugs(drugList.filter(d => d.drug_name.toLowerCase().includes(q)))
    }
  }, [drug, drugList])

  // Close dropdown on outside click
  useEffect(() => {
    function handleClick(e) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setShowDropdown(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  async function search(drugName) {
    if (!drugName.trim()) return
    setLoading(true)
    setResults(null)
    setError(null)
    setShowDropdown(false)
    try {
      const { data } = await axios.get(`${API}/search/policy`, {
        params: { drug: drugName }
      })
      setResults(data)
    } catch (err) {
      setError(err.response?.data?.detail || 'Search failed. Make sure the backend is running.')
    } finally {
      setLoading(false)
    }
  }

  function selectDrug(name) {
    setDrug(name)
    setShowDropdown(false)
    search(name)
  }

  // Group policies by unique payer
  const groupedPolicies = []
  if (results?.policies) {
    const seen = new Map()
    for (const p of results.policies) {
      const key = (p.payer || 'Unknown').toLowerCase()
      if (!seen.has(key)) {
        seen.set(key, { ...p, _extra: [] })
      } else {
        seen.get(key)._extra.push(p)
      }
    }
    groupedPolicies.push(...seen.values())
  }

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <h1 className="theme-page-title text-2xl font-bold mb-1">Medical Policy Search</h1>
      <p className="theme-subtitle mb-6">Search payer medical benefit policies for provider-administered drugs</p>

      {/* Search bar with dropdown */}
      <div ref={wrapperRef} className="relative mb-8">
        <div className="flex gap-3">
          <div className="relative flex-1">
            <input
              value={drug}
              onChange={e => { setDrug(e.target.value); setShowDropdown(true) }}
              onFocus={() => setShowDropdown(true)}
              onKeyDown={e => e.key === 'Enter' && search(drug)}
              placeholder="Search by drug name..."
              className="theme-input w-full rounded-xl px-4 py-3"
            />
            {showDropdown && filteredDrugs.length > 0 && (
              <div className="absolute z-20 top-full left-0 right-0 mt-1 theme-card rounded-xl shadow-lg border border-[var(--color-border)] max-h-64 overflow-y-auto">
                {filteredDrugs.map(d => (
                  <button
                    key={d.drug_name}
                    onClick={() => selectDrug(d.drug_name)}
                    className="w-full text-left px-4 py-2.5 hover:bg-[var(--color-surface-soft)] transition-colors flex items-center justify-between"
                  >
                    <span className="text-sm text-[var(--color-primary-deep)] font-medium capitalize">{d.drug_name}</span>
                    <span className="text-xs theme-muted">{d.payer_count} payer{d.payer_count !== 1 ? 's' : ''}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button
            onClick={() => search(drug)}
            disabled={loading || !drug.trim()}
            className="theme-button-primary disabled:opacity-50 px-6 py-3 rounded-xl font-medium transition-colors shrink-0">
            {loading ? 'Searching...' : 'Search'}
          </button>
        </div>
      </div>

      {error && <p className="text-[var(--color-error)] text-sm mb-4">{error}</p>}

      {results && (
        <div>
          {/* Drug header */}
          <div className="theme-card rounded-xl p-4 mb-6">
            <div className="flex items-center gap-3 flex-wrap">
              <p className="text-[var(--color-primary-deep)] font-bold text-lg capitalize">{results.drug}</p>
              {results.hcpcs_code && <span className="theme-pill text-xs px-2 py-0.5 rounded-full">{results.hcpcs_code}</span>}
              <span className="theme-pill text-xs px-2 py-0.5 rounded-full">{results.payer_policies_found} payer{results.payer_policies_found !== 1 ? 's' : ''}</span>
            </div>
          </div>

          {/* Policy cards — one per unique payer */}
          <div className="space-y-4">
            {groupedPolicies.length === 0
              ? <p className="theme-muted text-center py-8">No payer policies found for this drug.</p>
              : groupedPolicies.map(policy => (
                  <PolicyCard key={policy.policy_id} policy={policy} extraPolicies={policy._extra} />
                ))
            }
          </div>
        </div>
      )}
    </div>
  )
}
