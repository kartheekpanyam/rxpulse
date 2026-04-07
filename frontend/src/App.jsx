import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import DashboardView from './pages/DashboardView'
import SearchView from './pages/SearchView'
import AskView from './pages/AskView'
import HeatmapView from './pages/HeatmapView'
import PathwayView from './pages/PathwayView'
import ReportView from './pages/ReportView'
import PolicyTimeline from './components/Map/CoverageMap'

const navLink = ({ isActive }) =>
  `px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
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
          <NavLink to="/" end className="theme-brand font-bold text-xl mr-6 transition-colors shrink-0">
            RxPulse
          </NavLink>
          <NavLink to="/" end className={navLink}>Dashboard</NavLink>
          <NavLink to="/search" className={navLink}>Policy Search</NavLink>
          <NavLink to="/matrix" className={navLink}>Coverage Matrix</NavLink>
          <NavLink to="/pathway" className={navLink}>Coverage Pathway</NavLink>
          <NavLink to="/report" className={navLink}>Reports</NavLink>
          <NavLink to="/changes" className={navLink}>Policy Changes</NavLink>
          <NavLink to="/ask" className={navLink}>AI Assistant</NavLink>
        </nav>

        {/* Page Content */}
        <Routes>
          <Route path="/" element={<DashboardView />} />
          <Route path="/search" element={<SearchView />} />
          <Route path="/matrix" element={<HeatmapView />} />
          <Route path="/pathway" element={<PathwayView />} />
          <Route path="/report" element={<ReportView />} />
          <Route path="/changes" element={<PolicyTimeline />} />
          <Route path="/ask" element={<AskView />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}

export default App
