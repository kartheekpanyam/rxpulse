import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'

const API = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

const STAGE_LABELS = {
  queued: 'Queued',
  starting: 'Starting...',
  parsing: 'Parsing PDF...',
  extracting: 'Extracting coverages...',
  storing: 'Saving to database...',
  chunking: 'Indexing for search...',
  diffing: 'Comparing versions...',
  finalizing: 'Wrapping up...',
  completed: 'Done',
  failed: 'Failed',
}

export default function DashboardView() {
  const navigate = useNavigate()

  const [stats, setStats] = useState(null)
  const [loadingStats, setLoadingStats] = useState(true)
  const [payerFilter, setPayerFilter] = useState('All')

  // Upload state
  const [uploadFile, setUploadFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploadStage, setUploadStage] = useState(null)
  const [uploadMessage, setUploadMessage] = useState('')
  const [uploadError, setUploadError] = useState('')
  const [uploadSuccess, setUploadSuccess] = useState('')
  const fileInputRef = useRef(null)
  const pollRef = useRef(null)

  const [dragging, setDragging] = useState(false)

  // Recent changes
  const [recentChanges, setRecentChanges] = useState([])

  const fetchStats = useCallback((payer) => {
    setLoadingStats(true)
    const params = payer && payer !== 'All' ? { payer } : {}
    axios.get(`${API}/stats`, { params })
      .then(res => setStats(res.data))
      .catch(() => setStats(null))
      .finally(() => setLoadingStats(false))
  }, [])

  const fetchChanges = useCallback(() => {
    axios.get(`${API}/policy-changes`, { params: { limit: 5 } })
      .then(res => setRecentChanges(res.data || []))
      .catch(() => setRecentChanges([]))
  }, [])

  useEffect(() => { fetchStats(payerFilter) }, [payerFilter, fetchStats])
  useEffect(() => { fetchChanges() }, [fetchChanges])

  // Cleanup polling on unmount
  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  const s = stats || {}
  const payerList = ['All', ...(s.payer_list || [])]

  const statCards = [
    { label: 'Payers Tracked', value: payerFilter !== 'All' ? 1 : (s.payers_tracked ?? 0), sub: payerFilter !== 'All' ? payerFilter : 'Active insurance plans', color: 'text-[var(--color-primary)]' },
    { label: 'Policies Ingested', value: s.policies_ingested ?? 0, sub: 'Documents processed', color: 'text-[var(--color-primary-soft)]' },
    { label: 'Drugs Covered', value: s.drugs_covered ?? 0, sub: 'Unique drugs tracked', color: 'text-[var(--color-accent)]' },
  ]

  // -- Upload handlers --

  const handleFileSelect = (e) => {
    const file = e.target.files?.[0]
    if (file) {
      setUploadFile(file)
      setUploadError('')
      setUploadSuccess('')
    }
  }

  const pollJob = (jobId) => {
    pollRef.current = setInterval(() => {
      axios.get(`${API}/upload/jobs/${jobId}`)
        .then(res => {
          const job = res.data
          setUploadStage(job.stage || job.status)
          setUploadMessage(job.message || '')

          if (job.status === 'completed') {
            clearInterval(pollRef.current)
            pollRef.current = null
            setUploading(false)
            setUploadStage(null)
            setUploadFile(null)
            const r = job.result
            setUploadSuccess(
              `${r?.payer || 'Policy'} — ${r?.drugs_extracted || 0} drug(s) extracted, ${r?.chunks_stored || 0} chunks indexed`
            )
            if (fileInputRef.current) fileInputRef.current.value = ''
            fetchStats(payerFilter)
            fetchChanges()
          } else if (job.status === 'failed') {
            clearInterval(pollRef.current)
            pollRef.current = null
            setUploading(false)
            setUploadStage(null)
            setUploadError(job.error || 'Upload failed')
          }
        })
        .catch(() => {
          // keep polling, transient error
        })
    }, 1500)
  }

  const handleUpload = async () => {
    if (!uploadFile) return
    setUploading(true)
    setUploadError('')
    setUploadSuccess('')
    setUploadStage('queued')
    setUploadMessage('Uploading file...')

    const formData = new FormData()
    formData.append('file', uploadFile)

    try {
      const res = await axios.post(`${API}/upload`, formData)
      const jobId = res.data.job_id
      setUploadStage('starting')
      setUploadMessage('Processing started...')
      pollJob(jobId)
    } catch (err) {
      setUploading(false)
      setUploadStage(null)
      setUploadError(err.response?.data?.detail || 'Upload failed — is the backend running?')
    }
  }

  const changeTypeStyles = {
    new_coverage: { bg: 'bg-[#EDF7ED]', text: 'text-[#2E7D32]', label: 'New' },
    coverage_expanded: { bg: 'bg-[#E3F2FD]', text: 'text-[#1565C0]', label: 'Expanded' },
    criteria_updated: { bg: 'bg-[#FFF8E1]', text: 'text-[#E65100]', label: 'Updated' },
    restriction_added: { bg: 'bg-[#FFF3E0]', text: 'text-[#BF360C]', label: 'Restricted' },
    coverage_removed: { bg: 'bg-[#FFEBEE]', text: 'text-[#C62828]', label: 'Removed' },
  }

  return (
    <div className="max-w-7xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="theme-page-title text-3xl font-bold mb-2">Medical Benefit Drug Policy Tracker</h1>
        <p className="theme-subtitle">AI-powered ingestion, comparison, and change tracking across payers</p>
      </div>

      {/* Upload Section */}
      <div className="theme-card rounded-xl p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <p className="theme-muted text-xs font-semibold uppercase tracking-wide">Upload Policy Document</p>
        </div>

        <div className="flex items-stretch gap-4">
          {/* Drag & drop zone — fills left */}
          <div
            onDragOver={e => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={e => {
              e.preventDefault()
              setDragging(false)
              const file = e.dataTransfer.files?.[0]
              if (file && file.name.toLowerCase().endsWith('.pdf')) {
                setUploadFile(file)
                setUploadError('')
                setUploadSuccess('')
              } else {
                setUploadError('Please drop a PDF file.')
              }
            }}
            onClick={() => !uploading && fileInputRef.current?.click()}
            className={`flex-1 flex flex-col items-center justify-center rounded-xl py-8 cursor-pointer transition-all ${
              dragging
                ? 'border-2 border-dashed border-[var(--color-primary)] bg-[var(--color-surface-soft)]'
                : 'border-2 border-dashed border-[var(--color-border)] hover:border-[var(--color-primary-soft)] hover:bg-[var(--color-surface-soft)]'
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf"
              onChange={handleFileSelect}
              className="hidden"
              disabled={uploading}
            />
            {/* Upload icon */}
            <div className={`w-10 h-10 rounded-full flex items-center justify-center mb-3 ${uploadFile ? 'bg-[var(--color-primary)]' : 'border border-[var(--color-border)]'}`}>
              <svg className={`w-5 h-5 ${uploadFile ? 'text-white' : 'text-[var(--color-text-muted)]'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
              </svg>
            </div>
            {uploadFile ? (
              <p className="text-sm text-[var(--color-primary-deep)] font-medium truncate max-w-xs">{uploadFile.name}</p>
            ) : (
              <>
                <p className="text-sm text-[var(--color-text)]">
                  Drag & drop PDF here, or <span className="text-[var(--color-primary)] underline">click to browse</span>
                </p>
                <p className="text-xs theme-muted mt-1">Medical policy documents (.pdf)</p>
              </>
            )}
          </div>

          {/* Upload button — right side */}
          <button
            onClick={handleUpload}
            disabled={!uploadFile || uploading}
            className={`px-6 rounded-xl text-sm font-medium transition-all flex flex-col items-center justify-center gap-2 shrink-0 min-w-[140px] ${
              uploading
                ? 'bg-[var(--color-primary-deep)] text-white cursor-wait'
                : uploadFile
                  ? 'bg-[var(--color-primary)] text-white hover:bg-[var(--color-primary-deep)]'
                  : 'bg-[var(--color-surface-soft)] text-[var(--color-text-muted)] cursor-not-allowed'
            }`}
          >
            {uploading && (
              <svg className="animate-spin h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            <span>{uploading ? (STAGE_LABELS[uploadStage] || 'Processing...') : 'Upload & Process'}</span>
          </button>
        </div>

        {/* Status messages */}
        {uploading && uploadMessage && (
          <p className="text-xs text-[var(--color-primary-soft)] mt-3">{uploadMessage}</p>
        )}
        {uploadError && (
          <p className="text-xs text-[var(--color-error)] mt-3">{uploadError}</p>
        )}
        {uploadSuccess && (
          <p className="text-xs text-[var(--color-success)] mt-3">{uploadSuccess}</p>
        )}
      </div>

      {/* Stat Cards + Payer Filter */}
      <div className="flex items-center gap-3 mb-3 flex-wrap">
        <p className="theme-muted text-xs font-semibold uppercase tracking-wide">Overview</p>
        <div className="ml-auto flex items-center gap-2">
          <label className="theme-muted text-xs">Filter</label>
          <select
            value={payerFilter}
            onChange={e => setPayerFilter(e.target.value)}
            className="theme-input text-xs rounded-lg px-2 py-1.5"
          >
            {payerList.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
        {statCards.map(sc => (
          <div key={sc.label} className="theme-card rounded-xl p-5">
            <p className={`text-3xl font-bold ${sc.color} ${loadingStats ? 'opacity-40' : ''}`}>{sc.value}</p>
            <p className="text-[var(--color-primary-deep)] font-medium text-sm mt-1">{sc.label}</p>
            <p className="theme-muted text-xs mt-0.5">{sc.sub}</p>
          </div>
        ))}
      </div>

      {/* Recent Policy Changes */}
      <div className="theme-card rounded-xl p-5 mb-8">
        <div className="flex items-center justify-between mb-4">
          <p className="theme-muted text-xs font-semibold uppercase tracking-wide">Recent Policy Changes</p>
          {recentChanges.length > 0 && (
            <button onClick={() => navigate('/changes')} className="text-[var(--color-primary)] text-xs hover:text-[var(--color-accent)] transition-colors">
              View all &rarr;
            </button>
          )}
        </div>
        {recentChanges.length > 0 ? (
          <div className="space-y-2">
            {recentChanges.map((c, i) => {
              const style = changeTypeStyles[c.change_type] || changeTypeStyles.criteria_updated
              return (
                <div key={c.id || i} className="flex items-center gap-3 p-3 rounded-lg theme-section-tint">
                  <span className={`text-[10px] font-semibold uppercase px-2 py-0.5 rounded ${style.bg} ${style.text}`}>
                    {style.label}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-[var(--color-primary-deep)] font-medium truncate">
                      {c.payer}{c.drug_name ? ` — ${c.drug_name}` : ''}
                    </p>
                    <p className="text-xs theme-muted truncate">{c.summary || c.field_changed || ''}</p>
                  </div>
                  {c.change_date && (
                    <span className="text-[10px] theme-muted shrink-0">{c.change_date}</span>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          <p className="theme-muted text-sm text-center py-8">No policy changes detected yet. Upload a new version of an existing policy to see diffs.</p>
        )}
      </div>

      {/* Top Tracked Drugs */}
      <div className="theme-card rounded-xl p-5 mb-8">
        <div className="flex items-center justify-between mb-4">
          <p className="theme-muted text-xs font-semibold uppercase tracking-wide">Top Tracked Drugs</p>
          <button onClick={() => navigate('/search')} className="text-[var(--color-primary)] text-xs hover:text-[var(--color-accent)] transition-colors">
            Search all &rarr;
          </button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {(s.top_drugs || []).slice(0, 6).map((d, i) => (
            <div
              key={d.drug + i}
              onClick={() => navigate('/search')}
              className="theme-section-tint flex items-center justify-between p-3 rounded-lg hover:bg-[var(--color-surface-soft)] cursor-pointer transition-colors"
            >
              <p className="text-[var(--color-primary-deep)] font-medium text-sm capitalize">{d.drug}</p>
              <div className="text-center">
                <p className="text-[var(--color-primary)] text-sm font-semibold">{d.payer_count}</p>
                <p className="theme-muted text-[10px]">Payers</p>
              </div>
            </div>
          ))}
          {(s.top_drugs || []).length === 0 && (
            <p className="theme-muted text-sm text-center py-4 col-span-3">Upload documents to see top drugs</p>
          )}
        </div>
      </div>

      {/* Tool Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[
          { title: 'Coverage Matrix', desc: 'Drug-vs-payer heatmap showing coverage status at a glance', path: '/matrix', color: 'from-[#2F3E4D] to-[#3F4A54]' },
          { title: 'Coverage Pathway', desc: 'Step-by-step approval flowchart for any drug and payer', path: '/pathway', color: 'from-[#3F4A54] to-[#5F8FBF]' },
          { title: 'Generate Report', desc: 'AI-powered policy intelligence reports for client deliverables', path: '/report', color: 'from-[#5F8FBF] to-[#7FAEDB]' },
        ].map(tool => (
          <button key={tool.path} onClick={() => navigate(tool.path)}
            className={`bg-gradient-to-br ${tool.color} rounded-xl p-5 text-left hover:brightness-110 transition-all`}>
            <p className="text-white font-semibold text-lg">{tool.title}</p>
            <p className="text-white/70 text-sm mt-1">{tool.desc}</p>
            <p className="text-white/50 text-xs mt-3">Open &rarr;</p>
          </button>
        ))}
      </div>

      {/* How It Works */}
      <div className="theme-card-soft mt-8 rounded-xl p-5">
        <p className="theme-muted text-xs font-semibold uppercase tracking-wide mb-3">How It Works</p>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {[
            { step: '1', title: 'Ingest', desc: 'Upload medical policy PDFs — payer auto-detected from document content' },
            { step: '2', title: 'Extract', desc: 'Gemini AI extracts drug names, indications, PA criteria, site-of-care rules' },
            { step: '3', title: 'Normalize', desc: 'Data standardized across payers using HCPCS codes and clinical terms' },
            { step: '4', title: 'Compare', desc: 'Side-by-side comparison, change tracking, and AI-powered Q&A' },
          ].map(st => (
            <div key={st.step} className="flex gap-3">
              <div className="w-8 h-8 rounded-full bg-[var(--color-primary)] text-white flex items-center justify-center text-sm font-bold shrink-0">
                {st.step}
              </div>
              <div>
                <p className="text-[var(--color-primary-deep)] font-medium text-sm">{st.title}</p>
                <p className="theme-muted text-xs mt-0.5">{st.desc}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
