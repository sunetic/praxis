import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { Outlet } from "react-router-dom"

import App from "./App"

vi.mock("@/layouts/AppLayout", () => ({
  AppLayout: () => (
    <div>
      <Outlet />
    </div>
  ),
}))

vi.mock("@/pages/ChatPage", () => ({ ChatPage: () => <div>CHAT_PAGE</div> }))
vi.mock("@/pages/DataSourcesPage", () => ({ DataSourcesPage: () => <div>DATASOURCE_PAGE</div> }))
vi.mock("@/pages/AgentsPage", () => ({ AgentsPage: () => <div>AGENT_PAGE</div> }))
vi.mock("@/pages/SkillsPage", () => ({ SkillsPage: () => <div>SKILL_PAGE</div> }))
vi.mock("@/pages/PageConsolePage", () => ({ PageConsolePage: () => <div>PAGE_CONSOLE_PAGE</div> }))
vi.mock("@/pages/FunctionListPage", () => ({ FunctionListPage: () => <div>FUNCTION_LIST_PAGE</div> }))
vi.mock("@/pages/FunctionBuildPage", () => ({ FunctionBuildPage: () => <div>FUNCTION_BUILD_PAGE</div> }))
vi.mock("@/pages/SchedulerConsolePage", () => ({ SchedulerConsolePage: () => <div>SCHEDULER_CONSOLE_PAGE</div> }))
vi.mock("@/pages/ChannelConsolePage", () => ({ ChannelConsolePage: () => <div>CHANNEL_CONSOLE_PAGE</div> }))
vi.mock("@/pages/SqlAnalysisPage", () => ({ SqlAnalysisPage: () => <div>SQL_ANALYSIS_PAGE</div> }))

describe("App routes", () => {
  it("redirects root to /chat", async () => {
    window.history.pushState({}, "", "/")
    render(<App />)
    expect(await screen.findByText("CHAT_PAGE")).toBeInTheDocument()
  })

  it("keeps compatibility for /datasources and /agents", async () => {
    window.history.pushState({}, "", "/datasources")
    render(<App />)
    expect(await screen.findByText("DATASOURCE_PAGE")).toBeInTheDocument()

    window.history.pushState({}, "", "/agents")
    render(<App />)
    expect(await screen.findByText("AGENT_PAGE")).toBeInTheDocument()
  })

  it("keeps /skills route available", async () => {
    window.history.pushState({}, "", "/skills")
    render(<App />)
    expect(await screen.findByText("SKILL_PAGE")).toBeInTheDocument()
  })

  it("supports object console routes", async () => {
    window.history.pushState({}, "", "/function")
    render(<App />)
    expect(await screen.findByText("FUNCTION_LIST_PAGE")).toBeInTheDocument()

    window.history.pushState({}, "", "/function/2/build")
    render(<App />)
    expect(await screen.findByText("FUNCTION_BUILD_PAGE")).toBeInTheDocument()

    window.history.pushState({}, "", "/channel")
    render(<App />)
    expect(await screen.findByText("CHANNEL_CONSOLE_PAGE")).toBeInTheDocument()
  })

  it("keeps sql analysis route available", async () => {
    window.history.pushState({}, "", "/sql-analysis")
    render(<App />)
    expect(await screen.findByText("SQL_ANALYSIS_PAGE")).toBeInTheDocument()
  })
})
