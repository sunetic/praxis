import { BrowserRouter, Routes, Route, Navigate, useParams, useNavigate } from "react-router-dom"
import { lazy, useEffect, useState } from "react"
import { Toaster } from "@/components/ui/sonner"
import { AppLayout } from "@/layouts/AppLayout"
import { OnboardingPage } from "@/pages/OnboardingPage"
import { ShellI18nProvider } from "@/i18n/shellI18n"
import { EditionProvider } from "@/context/EditionContext"
import { onboardingApi } from "@/lib/api"

const ChatPage = lazy(() => import("@/pages/ChatPage").then((module) => ({ default: module.ChatPage })))
const DataSourcesPage = lazy(() => import("@/pages/DataSourcesPage").then((module) => ({ default: module.DataSourcesPage })))
const AgentsPage = lazy(() => import("@/pages/AgentsPage").then((module) => ({ default: module.AgentsPage })))
const SkillsPage = lazy(() => import("@/pages/SkillsPage").then((module) => ({ default: module.SkillsPage })))
const SkillBuilderPage = lazy(() => import("@/pages/SkillBuilderPage").then((module) => ({ default: module.SkillBuilderPage })))
const FunctionListPage = lazy(() => import("@/pages/FunctionListPage").then((module) => ({ default: module.FunctionListPage })))
const FunctionBuildPage = lazy(() => import("@/pages/FunctionBuildPage").then((module) => ({ default: module.FunctionBuildPage })))
const SchedulerConsolePage = lazy(() => import("@/pages/SchedulerConsolePage").then((module) => ({ default: module.SchedulerConsolePage })))
const ChannelConsolePage = lazy(() => import("@/pages/ChannelConsolePage").then((module) => ({ default: module.ChannelConsolePage })))
const CapabilitiesPage = lazy(() => import("@/pages/CapabilitiesPage").then((module) => ({ default: module.CapabilitiesPage })))
const KnowledgeListPage = lazy(() => import("@/pages/KnowledgeListPage").then((module) => ({ default: module.KnowledgeListPage })))
const KnowledgeDetailPage = lazy(() => import("@/pages/KnowledgeDetailPage").then((module) => ({ default: module.KnowledgeDetailPage })))
const SettingsPage = lazy(() => import("@/pages/SettingsPage").then((module) => ({ default: module.SettingsPage })))
const ServicesPage = lazy(() => import("@/pages/ServicesPage").then((module) => ({ default: module.ServicesPage })))
const PageListPage = lazy(() => import("@/pages/PageListPage").then((module) => ({ default: module.PageListPage })))
const PageConsolePage = lazy(() => import("@/pages/PageConsolePage").then((module) => ({ default: module.PageConsolePage })))
const PagePublishedPage = lazy(() => import("@/pages/PagePublishedPage").then((module) => ({ default: module.PagePublishedPage })))

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
      <EditionProvider>
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
            <Route path="scheduler" element={<SchedulerConsolePage />} />
            <Route path="scheduler/:schedulerId" element={<SchedulerConsolePage />} />
            <Route path="channel" element={<ChannelConsolePage />} />
            <Route path="channel/:provider" element={<ChannelConsolePage />} />
            <Route path="service" element={<ServicesPage />} />
            <Route path="page" element={<PageListPage />} />
            <Route path="page/workspace/:pageId" element={<PageConsolePage />} />
            <Route path="page/:pageId" element={<PagePublishedPage />} />
            <Route path="capabilities" element={<CapabilitiesPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/chat" replace />} />
          </Route>
        </Routes>
        <Toaster />
      </BrowserRouter>
      </EditionProvider>
    </ShellI18nProvider>
  )
}

export default App
