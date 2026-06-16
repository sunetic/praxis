import { BrowserRouter, Routes, Route, Navigate, useParams, useNavigate } from "react-router-dom"
import { useEffect, useState } from "react"
import { Toaster } from "@/components/ui/sonner"
import { AppLayout } from "@/layouts/AppLayout"
import { ChatPage } from "@/pages/ChatPage"
import { DataSourcesPage } from "@/pages/DataSourcesPage"
import { AgentsPage } from "@/pages/AgentsPage"
import { SkillsPage } from "@/pages/SkillsPage"
import { SkillBuilderPage } from "@/pages/SkillBuilderPage"
import { FunctionListPage } from "@/pages/FunctionListPage"
import { FunctionBuildPage } from "@/pages/FunctionBuildPage"
import { SchedulerConsolePage } from "@/pages/SchedulerConsolePage"
import { ChannelConsolePage } from "@/pages/ChannelConsolePage"
import { SqlAnalysisPage } from "@/pages/SqlAnalysisPage"
import { CapabilitiesPage } from "@/pages/CapabilitiesPage"
import { KnowledgeListPage } from "@/pages/KnowledgeListPage"
import { KnowledgeDetailPage } from "@/pages/KnowledgeDetailPage"
import { SettingsPage } from "@/pages/SettingsPage"
import { OnboardingPage } from "@/pages/OnboardingPage"
import { ShellI18nProvider } from "@/i18n/shellI18n"
import { onboardingApi } from "@/lib/api"

function FunctionLegacyRedirect() {
  const { functionId } = useParams()
  if (!functionId) return <Navigate to="/function" replace />
  return <Navigate to={`/function/${functionId}/build`} replace />
}

// Checks onboarding status and redirects to /onboarding if not completed.
function OnboardingGuard({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate()
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    onboardingApi.getStatus().then((status) => {
      if (!status.completed) {
        navigate("/onboarding", { replace: true })
      }
      setChecked(true)
    }).catch(() => {
      // If the check fails (e.g. first boot before DB is ready), let through
      setChecked(true)
    })
  }, [navigate])

  if (!checked) return null
  return <>{children}</>
}

function App() {
  return (
    <ShellI18nProvider>
      <BrowserRouter>
        <Routes>
          {/* Full-screen onboarding — outside AppLayout */}
          <Route path="/onboarding" element={<OnboardingPage />} />

          {/* Main app — guarded by onboarding check */}
          <Route
            path="/"
            element={
              <OnboardingGuard>
                <AppLayout />
              </OnboardingGuard>
            }
          >
            <Route index element={<Navigate to="/chat" replace />} />
            <Route path="chat" element={<ChatPage />} />
            <Route path="datasource" element={<DataSourcesPage />} />
            <Route path="datasources" element={<Navigate to="/datasource" replace />} />
            <Route path="knowledge" element={<KnowledgeListPage />} />
            <Route path="knowledge/:kbId" element={<KnowledgeDetailPage />} />
            <Route path="agent" element={<AgentsPage />} />
            <Route path="agents" element={<Navigate to="/agent" replace />} />
            <Route path="skills" element={<SkillsPage />} />
            <Route path="skills/builder" element={<SkillBuilderPage />} />
            <Route path="function" element={<FunctionListPage />} />
            <Route path="function/:functionId/build" element={<FunctionBuildPage />} />
            <Route path="function/:functionId" element={<FunctionLegacyRedirect />} />
            <Route path="sql-analysis" element={<SqlAnalysisPage />} />
            <Route path="scheduler" element={<SchedulerConsolePage />} />
            <Route path="scheduler/:schedulerId" element={<SchedulerConsolePage />} />
            <Route path="channel" element={<ChannelConsolePage />} />
            <Route path="channel/:provider" element={<ChannelConsolePage />} />
            <Route path="capabilities" element={<CapabilitiesPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Route>
        </Routes>
        <Toaster />
      </BrowserRouter>
    </ShellI18nProvider>
  )
}

export default App
