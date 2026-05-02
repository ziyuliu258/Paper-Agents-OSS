import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from '@/components/Layout'
import Dashboard from '@/pages/Dashboard'
import RunPage from '@/pages/RunPage'
import ReportsPage from '@/pages/ReportsPage'
import ReportViewer from '@/pages/ReportViewer'
import PapersPage from '@/pages/PapersPage'
import ProfilesPage from '@/pages/ProfilesPage'
import ProfileDetailPage from '@/pages/ProfileDetailPage'
import MemoryWorkspacePage from '@/pages/MemoryWorkspacePage'
import SettingsPage from '@/pages/SettingsPage'
import LivingSurveyPage from '@/pages/LivingSurveyPage'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Dashboard />} />
          <Route path="/run" element={<RunPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/reports/job/:jobId" element={<ReportViewer />} />
          <Route path="/jobs" element={<Navigate to="/reports" replace />} />
          <Route path="/papers" element={<PapersPage />} />
          <Route path="/profiles" element={<ProfilesPage />} />
          <Route path="/profiles/:profileId" element={<ProfileDetailPage />} />
          <Route path="/profiles/:profileId/survey" element={<LivingSurveyPage />} />
          <Route path="/profiles/:profileId/workspace" element={<MemoryWorkspacePage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
