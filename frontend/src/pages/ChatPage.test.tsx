import { StrictMode } from "react"
import { MemoryRouter } from "react-router-dom"
import { cleanup, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"
import { renderWithShell as render } from "@/test/renderWithShell"
import { agentRunsApi } from "@/lib/agentRuns"
import { agentsApi, datasourcesApi, type Agent, type DataSource } from "@/lib/api"
import { ChatPage } from "./ChatPage"

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it("creates only one native conversation on first load in StrictMode", async () => {
  vi.spyOn(agentRunsApi, "conversations").mockResolvedValue([])
  const create = vi.spyOn(agentRunsApi, "createConversation").mockResolvedValue({ id: "native-uuid", title: "新对话", scene: { datasource_ids: [] }, created_at: 1, active_run_id: null })
  vi.spyOn(agentRunsApi, "runs").mockResolvedValue([])
  vi.spyOn(datasourcesApi, "list").mockResolvedValue([])
  render(<StrictMode><MemoryRouter><ChatPage /></MemoryRouter></StrictMode>)
  await waitFor(() => expect(screen.getByRole("textbox", { name: "消息" })).toBeEnabled())
  expect(screen.queryByRole("option", { name: "全部可用数据源" })).toBeNull()
  expect(create).toHaveBeenCalledTimes(1)
  expect(create).toHaveBeenCalledWith("新对话", { datasource_ids: [] })
  expect(screen.getByRole("combobox", { name: "会话" })).toHaveValue("native-uuid")
})

it("submits explicit empty datasource scope without a legacy stream request", async () => {
  vi.spyOn(agentRunsApi, "conversations").mockResolvedValue([{ id: "native-uuid", title: "现有对话", scene: { datasource_ids: [] }, created_at: 1, active_run_id: null }])
  vi.spyOn(agentRunsApi, "runs").mockResolvedValue([])
  vi.spyOn(datasourcesApi, "list").mockResolvedValue([])
  vi.spyOn(agentRunsApi, "updateConversation").mockImplementation(async (id, scene) => ({ id, title: "现有对话", scene, created_at: 1, active_run_id: null }))
  const submit = vi.spyOn(agentRunsApi, "submit").mockImplementation(() => new Promise(() => {}))
  render(<MemoryRouter><ChatPage /></MemoryRouter>)
  await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled())
  await userEvent.type(screen.getByRole("textbox"), "只解释概念")
  await userEvent.click(screen.getByRole("button", { name: "发送" }))
  expect(submit).toHaveBeenCalledWith("native-uuid", expect.any(String), "只解释概念", { datasource_ids: [], locale: "zh-CN" }, "append")
})

it("keeps the persisted scope if saving a datasource change fails", async () => {
  vi.spyOn(agentRunsApi, "conversations").mockResolvedValue([{ id: "native-uuid", title: "现有对话", scene: { datasource_ids: [] }, created_at: 1, active_run_id: null }])
  vi.spyOn(agentRunsApi, "runs").mockResolvedValue([])
  vi.spyOn(datasourcesApi, "list").mockResolvedValue([{ id: 1, name: "业务库", status: "active" } as DataSource])
  const update = vi.spyOn(agentRunsApi, "updateConversation").mockRejectedValue(new Error("offline"))
  render(<MemoryRouter><ChatPage /></MemoryRouter>)
  await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled())
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "数据源范围" }), "1")
  expect(update).toHaveBeenCalledWith("native-uuid", { datasource_ids: [1], auto_approval: null })
  await screen.findByText(/范围未保存/)
  expect(screen.getByRole("combobox", { name: "数据源范围" })).toHaveValue("none")
})

it("enables a confirmed, datasource-scoped conversation auto-approval", async () => {
  vi.spyOn(agentRunsApi, "conversations").mockResolvedValue([{ id: "native-uuid", title: "现有对话", scene: { datasource_ids: [1] }, created_at: 1, active_run_id: null }])
  vi.spyOn(agentRunsApi, "runs").mockResolvedValue([])
  vi.spyOn(datasourcesApi, "list").mockResolvedValue([{ id: 1, name: "业务库", status: "active" } as DataSource])
  const update = vi.spyOn(agentRunsApi, "updateConversation").mockImplementation(async (id, scene) => ({ id, title: "现有对话", scene, created_at: 1, active_run_id: null }))
  vi.spyOn(window, "confirm").mockReturnValue(true)
  render(<MemoryRouter><ChatPage /></MemoryRouter>)
  const button = await screen.findByRole("button", { name: "自动批准：关" })
  await userEvent.click(button)
  await waitFor(() => expect(update).toHaveBeenCalledWith("native-uuid", {
    datasource_ids: [1],
    auto_approval: {
      tool_name: "request_database_change",
      agent_id: null,
      datasource_id: 1,
      expires_at: expect.any(Number),
    },
  }))
  expect(window.confirm).toHaveBeenCalledOnce()
  expect(await screen.findByRole("button", { name: "自动批准：30 分钟" })).toBeEnabled()
})

it("keeps ordinary chat available and reports a datasource-list failure in StrictMode", async () => {
  vi.spyOn(agentRunsApi, "conversations").mockResolvedValue([{ id: "native-uuid", title: "现有对话", scene: {}, created_at: 1, active_run_id: null }])
  vi.spyOn(agentRunsApi, "runs").mockResolvedValue([])
  vi.spyOn(datasourcesApi, "list").mockRejectedValue(new Error("datasource service unavailable"))
  render(<StrictMode><MemoryRouter><ChatPage /></MemoryRouter></StrictMode>)
  await screen.findByText(/数据源列表暂时无法加载/)
  await waitFor(() => expect(screen.getByRole("textbox")).toBeEnabled())
})

it("does not silently open another conversation when a requested ID is unavailable", async () => {
  vi.spyOn(agentRunsApi, "conversations").mockResolvedValue([{ id: "other", title: "另一会话", scene: {}, created_at: 1, active_run_id: null }])
  const runs = vi.spyOn(agentRunsApi, "runs").mockResolvedValue([])
  vi.spyOn(datasourcesApi, "list").mockResolvedValue([])
  render(<MemoryRouter initialEntries={["/chat?conversationId=missing"]}><ChatPage /></MemoryRouter>)
  await screen.findByRole("alert")
  expect(runs).not.toHaveBeenCalled()
  expect(screen.getByRole("textbox", { name: "消息" })).toBeDisabled()
})

it("shows the custom Agent's authorized scope and retains a multi-source selection", async () => {
  vi.spyOn(agentRunsApi, "conversations").mockResolvedValue([{ id: "custom", title: "Agent 对话", scene: { agent_id: 7, datasource_ids: [1, 2] }, created_at: 1, active_run_id: null }])
  vi.spyOn(agentRunsApi, "runs").mockResolvedValue([])
  vi.spyOn(datasourcesApi, "list").mockResolvedValue([1, 2, 3].map(id => ({ id, name: `数据源${id}`, status: "active" } as DataSource)))
  vi.spyOn(agentsApi, "get").mockResolvedValue({ id: 7, datasource_ids: [1, 2] } as Agent)
  render(<MemoryRouter><ChatPage /></MemoryRouter>)
  const scope = screen.getByRole("combobox", { name: "数据源范围" })
  await waitFor(() => expect(scope).toBeEnabled())
  expect(scope).toHaveValue("selection")
  expect(screen.queryByRole("option", { name: "数据源3" })).toBeNull()
  expect(screen.getByRole("option", { name: "数据源1, 数据源2" })).toBeInTheDocument()
})
