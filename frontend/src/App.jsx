import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import DashboardView from './pages/DashboardView'
import SearchView from './pages/SearchView'
import AskView from './pages/AskView'
import HeatmapView from './pages/HeatmapView'
import PathwayView from './pages/PathwayView'
import ReportView from './pages/ReportView'
import PolicyTimeline from './components/Map/CoverageMap'
import ApprovalView from './pages/ApprovalView'
import GraphView from './pages/GraphView'

const navLink = ({ isActive }) =>
  `px-3 py-2 rounded-lg text-sm font-medium transition-colors whitespace-nowrap ${
    isActive
      ? 'bg-white/15 text-white'
      : 'theme-nav-link'
  }`

function App() {
  return (
    <BrowserRouter>
      <div className="theme-shell">
        {/* Navigation */}
        <nav className="theme-nav flex items-center gap-1 px-6 py-3 overflow-x-auto">
          <NavLink to="/" end className="theme-brand font-bold text-xl mr-4 transition-colors shrink-0">
            RxPulse
          </NavLink>
          <NavLink to="/" end className={navLink}>Dashboard</NavLink>
          <NavLink to="/search" className={navLink}>Search</NavLink>
          <NavLink to="/matrix" className={navLink}>Matrix</NavLink>
          <NavLink to="/pathway" className={navLink}>Pathway</NavLink>
          <NavLink to="/approval" className={navLink}>Approval Score</NavLink>
          <NavLink to="/report" className={navLink}>Reports</NavLink>
          <NavLink to="/graph" className={navLink}>Graph</NavLink>
          <NavLink to="/changes" className={navLink}>Changes</NavLink>
          <NavLink to="/ask" className={navLink}>AI Assistant</NavLink>
        </nav>

        {/* Page Content */}
        <Routes>
          <Route path="/" element={<DashboardView />} />
          <Route path="/search" element={<SearchView />} />
          <Route path="/matrix" element={<HeatmapView />} />
          <Route path="/pathway" element={<PathwayView />} />
          <Route path="/approval" element={<ApprovalView />} />
          <Route path="/report" element={<ReportView />} />
          <Route path="/graph" element={<GraphView />} />
          <Route path="/changes" element={<PolicyTimeline />} />
          <Route path="/ask" element={<AskView />} />
          <Route path="*" element={
            <div className="max-w-3xl mx-auto px-6 py-16 text-center">
              <p className="text-6xl font-bold text-[var(--color-primary-deep)] mb-4">404</p>
              <p className="theme-muted mb-6">Page not found</p>
              <a href="/" className="theme-button-primary px-6 py-2.5 rounded-xl text-sm font-medium">Back to Dashboard</a>
            </div>
          } />
        </Routes>
      </div>
    </BrowserRouter>
  )
}

export default App
