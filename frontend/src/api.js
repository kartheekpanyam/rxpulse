import axios from 'axios'

const API = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

const client = axios.create({ baseURL: API })

// ── Dashboard & Stats ──
export const getStats = (params) => client.get('/stats', { params }).then(r => r.data)
export const getPolicyChanges = (params) => client.get('/policy-changes', { params }).then(r => r.data)

// ── Upload ──
export const uploadPolicy = (formData) => client.post('/upload', formData).then(r => r.data)
export const uploadPolicySync = (formData) => client.post('/upload/sync', formData).then(r => r.data)
export const getUploadJob = (jobId) => client.get(`/upload/jobs/${jobId}`).then(r => r.data)

// ── Drugs & Coverages ──
export const getDrugsList = () => client.get('/drugs/list').then(r => r.data)
export const getDrugCoverages = (params) => client.get('/drug-coverages', { params }).then(r => r.data)
export const searchPolicy = (params) => client.get('/search/policy', { params }).then(r => r.data)
export const comparePlans = (params) => client.get('/compare/plans', { params }).then(r => r.data)

// ── Q&A ──
export const askQuestion = (question) => client.post('/qa/ask', { question }).then(r => r.data)

export default client
