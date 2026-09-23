import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { cleanup, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { renderWithShell as render } from "@/test/renderWithShell"
import { agentsApi, capabilitiesApi, datasourcesApi, skillsApi, type Agent, type DataSource } from "@/lib/api"
import { agentRunsApi } from "@/lib/agentRuns"
import { AgentsPage } from "./AgentsPage"

const agent: Agent = { id: 7, name: "资料助手", prompt: "只说明已有证据", tools: ["list_datasources"], skills: [],
  datasource_ids: [1], agent_type: "custom", status: "active", created_at: "", updated_at: "" }
const sources = [1, 2].map(id => ({ id, name: `数据源${id}`, status: "active", tenant_role: "user", cluster_key: "test", host: "localhost", port: 5432 } as DataSource))
function Location() { return <p data-testid="location">{useLocation().search}</p> }
function mount(path = "/agents") {
  render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/agents" element={<AgentsPage />} />
    <Route path="/chat" element={<Location />} />
  </Routes></MemoryRouter>)
}
beforeEach(() => {
  localStorage.clear()
  vi.spyOn(agentsApi, "list").mockResolvedValue([agent])
  vi.spyOn(datasourcesApi, "list").mockResolvedValue(sources)
  vi.spyOn(skillsApi, "list").mockResolvedValue([])
  vi.spyOn(capabilitiesApi, "list").mockResolvedValue({ tools: [{ name: "list_datasources", description: "列出数据源", parameters: {} }, { name: "query_database", description: "只读查询", parameters: {} }] })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

it("opens one native conversation with explicit scope and no fabricated run prompt", async () => {
  const create = vi.spyOn(agentRunsApi, "createConversation").mockResolvedValue({ id: "native-uuid", title: "资料助手", scene: {}, created_at: 1, active_run_id: null })
  const submit = vi.spyOn(agentRunsApi, "submit")
  mount()
  await userEvent.click(await screen.findByRole("button", { name: "资料助手 · 打开对话" }))
  const dialog = screen.getByRole("dialog")
  expect(within(dialog).getByText("数据源1")).toBeVisible()
  expect(within(dialog).queryByText("数据源2")).toBeNull()
  await userEvent.click(within(dialog).getByRole("checkbox", { name: "数据源1" }))
  await userEvent.click(within(dialog).getByRole("button", { name: "打开对话（1 个数据源）" }))
  await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("?conversationId=native-uuid"))
  expect(create).toHaveBeenCalledOnce()
  expect(create).toHaveBeenCalledWith(expect.any(String), { agent_id: 7, datasource_ids: [1] })
  expect(submit).not.toHaveBeenCalled()
})

it("keeps scope selection open when native conversation creation fails", async () => {
  vi.spyOn(agentRunsApi, "createConversation").mockRejectedValue(new Error("permission changed"))
  mount()
  await userEvent.click(await screen.findByRole("button", { name: "资料助手 · 打开对话" }))
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "打开对话", exact: true }))
  await waitFor(() => expect(within(screen.getByRole("dialog")).getByRole("button", { name: "打开对话", exact: true })).toBeEnabled())
  expect(screen.queryByTestId("location")).toBeNull()
})

it("edits actual capabilities and datasource grants without silently selecting all", async () => {
  const update = vi.spyOn(agentsApi, "update").mockResolvedValue(agent)
  mount("/agents?editAgentId=7")
  const dialog = await screen.findByRole("dialog")
  const tools = within(dialog).getByRole("group", { name: "可用工具" })
  expect(within(tools).getByRole("checkbox", { name: /list_datasources/ })).toBeChecked()
  expect(within(tools).getByRole("checkbox", { name: /query_database/ })).not.toBeChecked()
  await userEvent.click(within(tools).getByRole("checkbox", { name: /list_datasources/ }))
  await userEvent.click(within(dialog).getByRole("checkbox", { name: "数据源1" }))
  await userEvent.click(within(dialog).getByRole("button", { name: "保存", exact: true }))
  expect(update).toHaveBeenCalledWith(7, expect.objectContaining({ tools: [], datasource_ids: [] }))
})

it("reads native run history for a conversation-derived draft", async () => {
  const runs = vi.spyOn(agentRunsApi, "runs").mockResolvedValue([{ id: "r", conversation_id: "native-id", prompt: "分析锁等待", output: "已有等待证据", status: "finished", seq: 1, cancel_requested: false, error_code: null, event_seq: 3, created_at: 1 }])
  mount("/agents?handoff=chat-save-agent&conversationId=native-id")
  await waitFor(() => expect(runs).toHaveBeenCalledWith("native-id"))
  expect((await screen.findByRole("textbox", { name: "Prompt" }) as HTMLTextAreaElement).value).toContain("分析锁等待")
})
