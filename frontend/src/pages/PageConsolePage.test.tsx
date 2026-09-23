import { StrictMode } from "react"
import { fireEvent, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { renderWithShell as render } from "@/test/renderWithShell"
import { agentRunsApi, type RunConversation } from "@/lib/agentRuns"
import { datasourcesApi, functionsApi, pagesApi } from "@/lib/api"
import { canPublishPage, pageArtifactsApi, type PageDraft } from "@/lib/pageArtifacts"
import { PageConsolePage } from "./PageConsolePage"

const conversation: RunConversation = { id: "conv", title: "Page conversation", scene: { page_ids: [1], datasource_ids: [] }, created_at: 1, active_run_id: null }
const initial: PageDraft = { page_id: 1, name: "订单页面", files: { "main.tsx": "export default () => <main>Hello</main>" }, bindings: {},
  revision_id: "revision-1", revision_hash: "a".repeat(64), current_release_id: null, released_revision_id: null,
  changed_files: ["main.tsx"], bindings_changed: false, artifact_hash: "compiled-1", validation: null, owned_functions: [] }
let draft: PageDraft
function checks(status = "passed"): PageDraft["validation"] {
  return { id: "check-1", revision_id: draft.revision_id!, revision_hash: draft.revision_hash, checks: [
    { name: "source_compile", status: "passed", executed: true }, { name: "function_bindings", status: "passed", executed: true },
    { name: "browser_runtime", status, executed: status !== "unavailable" },
    { name: "binding_runtime", status: "not_run", executed: false, applicable: false },
  ] }
}
function open(path = "/page/workspace/1") {
  return render(<StrictMode><MemoryRouter initialEntries={[path]}><Routes><Route path="/page/workspace/:pageId?" element={<PageConsolePage />} /><Route path="/page" element={<p>Page list</p>} /></Routes></MemoryRouter></StrictMode>)
}
beforeEach(() => {
  draft = structuredClone(initial)
  vi.spyOn(pagesApi, "get").mockResolvedValue({ id: 1, name: "订单页面", description: "订单汇总" })
  vi.spyOn(pagesApi, "update").mockResolvedValue({ id: 1, name: "新名称" })
  vi.spyOn(datasourcesApi, "list").mockResolvedValue([])
  vi.spyOn(functionsApi, "list").mockResolvedValue([{ id: 2, name: "Existing" }])
  vi.spyOn(pageArtifactsApi, "read").mockImplementation(async () => structuredClone(draft))
  vi.spyOn(pageArtifactsApi, "preview").mockImplementation(async () => ({ page_id: 1, revision_id: draft.revision_id!, artifact_hash: draft.artifact_hash!, validation_id: "check-1", html: "<main>Compiled fixture</main>" }))
  vi.spyOn(pageArtifactsApi, "save").mockResolvedValue({})
  vi.spyOn(pageArtifactsApi, "validate").mockResolvedValue(checks()!)
  vi.spyOn(pageArtifactsApi, "publish").mockResolvedValue({})
  vi.spyOn(agentRunsApi, "conversations").mockResolvedValue([conversation])
  vi.spyOn(agentRunsApi, "createConversation").mockResolvedValue(conversation)
  vi.spyOn(agentRunsApi, "updateConversation").mockImplementation(async (_id, scene) => ({ ...conversation, scene }))
  vi.spyOn(agentRunsApi, "runs").mockResolvedValue([])
  vi.spyOn(agentRunsApi, "stream").mockImplementation(() => new Promise(() => {}))
})
afterEach(() => vi.restoreAllMocks())

describe("native Page workspace", () => {
  it("creates a Page-bound native conversation once without a generated welcome", async () => {
    vi.mocked(agentRunsApi.conversations).mockResolvedValue([])
    open()
    await screen.findByRole("heading", { name: "订单页面" })
    expect(agentRunsApi.createConversation).toHaveBeenCalledExactlyOnceWith("Page 工作区", { page_ids: [1], datasource_ids: [] })
    expect(document.querySelectorAll("[data-message-id]")).toHaveLength(0)
    expect(screen.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
  })

  it("redirects an unbound workspace route to the list", async () => {
    open("/page/workspace")
    expect(await screen.findByText("Page list")).toBeVisible()
    expect(agentRunsApi.createConversation).not.toHaveBeenCalled()
  })

  it("saves explicit Function scope before allowing the next message", async () => {
    let resolve!: (value: RunConversation) => void
    vi.mocked(agentRunsApi.updateConversation).mockImplementation(() => new Promise(done => { resolve = done }))
    open()
    await screen.findByRole("heading", { name: "订单页面" })
    await userEvent.click(screen.getByText("可使用的现有 Function", { selector: "summary" }))
    const scope = screen.getByRole("listbox", { name: "可使用的现有 Function" })
    await userEvent.selectOptions(scope, "2")
    expect(agentRunsApi.updateConversation).toHaveBeenCalledExactlyOnceWith("conv", { page_ids: [1], datasource_ids: [], function_ids: [2] })
    expect(scope).toBeDisabled()
    expect(screen.getByRole("textbox", { name: "消息", exact: true })).toBeDisabled()
    resolve({ ...conversation, scene: { ...conversation.scene, function_ids: [2] } })
    await waitFor(() => expect(scope).toBeEnabled())
    expect(scope).toHaveValue(["2"])
    expect(screen.getByRole("textbox", { name: "消息", exact: true })).toBeEnabled()
    expect(pageArtifactsApi.publish).not.toHaveBeenCalled()
  })

  it("keeps the prior Function scope after a rejected scope update", async () => {
    vi.mocked(agentRunsApi.conversations).mockResolvedValue([{ ...conversation, scene: { ...conversation.scene, function_ids: [2] } }])
    vi.mocked(agentRunsApi.updateConversation).mockRejectedValue(new Error("scope denied"))
    open()
    await screen.findByRole("heading", { name: "订单页面" })
    await userEvent.click(screen.getByText("可使用的现有 Function", { selector: "summary" }))
    const scope = screen.getByRole("listbox", { name: "可使用的现有 Function" })
    await userEvent.deselectOptions(scope, "2")
    expect(await screen.findByRole("alert")).toHaveTextContent("范围未保存")
    expect(scope).toHaveValue(["2"])
    expect(scope).toBeEnabled()
    expect(agentRunsApi.createConversation).not.toHaveBeenCalled()
  })

  it("leaves saved scope intact when the Function catalogue cannot be loaded", async () => {
    vi.mocked(agentRunsApi.conversations).mockResolvedValue([{ ...conversation, scene: { ...conversation.scene, function_ids: [2] } }])
    vi.mocked(functionsApi.list).mockRejectedValue(new Error("network"))
    open()
    await screen.findByRole("heading", { name: "订单页面" })
    await userEvent.click(screen.getByText("可使用的现有 Function", { selector: "summary" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("已有授权不变")
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument()
    expect(agentRunsApi.updateConversation).not.toHaveBeenCalled()
    expect(screen.getByRole("textbox", { name: "消息", exact: true })).toBeEnabled()
  })

  it("does not replace an explicit conversation belonging to another Page", async () => {
    open("/page/workspace/1?conversationId=other")
    expect(await screen.findByRole("alert")).toHaveTextContent("工作区加载失败")
    expect(agentRunsApi.createConversation).not.toHaveBeenCalled()
  })

  it("shows only the exact compiled HTML in an opaque script-only sandbox", async () => {
    open()
    const frame = await screen.findByTitle("编译产物预览")
    expect(frame).toHaveAttribute("srcdoc", "<main>Compiled fixture</main>")
    expect(frame).toHaveAttribute("sandbox", "allow-scripts")
    expect(frame).toHaveAttribute("referrerpolicy", "no-referrer")
    expect(pageArtifactsApi.preview).toHaveBeenCalledWith(1, "revision-1")
  })

  it("rejects a stale compiled response instead of presenting it as the current draft", async () => {
    vi.mocked(pageArtifactsApi.preview).mockResolvedValue({ page_id: 1, revision_id: "old", artifact_hash: "old", validation_id: "old", html: "stale" })
    open()
    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取当前版本的预览")
    expect(screen.queryByTitle("编译产物预览")).not.toBeInTheDocument()
  })

  it("allows preview retry without resetting unchanged artifact state", async () => {
    vi.mocked(pageArtifactsApi.preview).mockRejectedValueOnce(new Error("network"))
    open()
    await screen.findByRole("alert")
    await userEvent.click(screen.getByRole("button", { name: "刷新产物" }))
    expect(await screen.findByTitle("编译产物预览")).toBeVisible()
  })

  it("refreshes real artifact facts on call-ID tool results, not model claims", async () => {
    const run = { id: "run", conversation_id: "conv", prompt: "检查", seq: 1, status: "finished" as const, cancel_requested: false, output: "已完成", error_code: null, event_seq: 4, created_at: 1 }
    vi.mocked(agentRunsApi.runs).mockResolvedValue([run])
    vi.mocked(agentRunsApi.stream).mockImplementation(async () => {
      draft.validation = checks("unavailable")
      return new Response([
        { type: "tool_start", call_id: "check", name: "page_validate" },
        { type: "tool_result", call_id: "check", outcome: "success", status: "succeeded", content: draft.validation },
        { type: "assistant_delta", message_id: "message", part_id: "0", text: "已完成" },
        { type: "run_finished", status: "finished" },
      ].map((event, i) => `data: ${JSON.stringify({ ...event, run_id: "run", seq: i + 1 })}\n\n`).join(""))
    })
    open()
    expect(await screen.findByText("已完成")).toBeVisible()
    expect(await screen.findByText("环境不可用 · 未执行")).toBeVisible()
    expect(screen.getByText("不适用 · 未执行")).toBeVisible()
    expect(screen.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
    expect(pageArtifactsApi.publish).not.toHaveBeenCalled()
  })

  it("retains local edits and the original CAS revision after external updates", async () => {
    vi.mocked(pageArtifactsApi.save).mockRejectedValue(new Error("Version conflict"))
    open()
    await userEvent.click(await screen.findByRole("button", { name: "编辑工作区" }))
    const files = screen.getByRole("textbox", { name: "源文件（JSON）" })
    fireEvent.change(files, { target: { value: JSON.stringify({ "main.tsx": "local unsaved" }) } })
    draft = { ...draft, revision_hash: "b".repeat(64), revision_id: "revision-2", artifact_hash: null, files: { "main.tsx": "remote" } }
    await userEvent.click(screen.getByRole("button", { name: "刷新产物" }))
    await waitFor(() => expect(screen.getByTestId("artifact-state")).toHaveTextContent("revision-2"))
    expect(screen.queryByTitle("编译产物预览")).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "保存草稿" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Version conflict")
    expect(files).toHaveValue(JSON.stringify({ "main.tsx": "local unsaved" }))
    expect(pageArtifactsApi.save).toHaveBeenCalledWith(1, { expected_revision: "a".repeat(64), source: { files: { "main.tsx": "local unsaved" }, bindings: {} } })
  })

  it("publishes only the exact report once despite repeated clicks", async () => {
    draft.validation = checks()
    vi.mocked(pageArtifactsApi.publish).mockImplementation(() => new Promise(() => {}))
    open()
    const button = await screen.findByRole("button", { name: "发布当前版本" })
    fireEvent.click(button); fireEvent.click(button)
    expect(pageArtifactsApi.publish).toHaveBeenCalledExactlyOnceWith(1, { expected_revision: draft.revision_hash, validation_id: "check-1" })
  })

  it("does not trust not-applicable markers for actual bound Functions", () => {
    draft.validation = checks()
    expect(canPublishPage(draft)).toBe(true)
    draft.bindings = { calculate: { function_id: 2, release_id: 1, revision_id: null } }
    expect(canPublishPage(draft)).toBe(false)
    draft.validation!.checks[3] = { name: "binding_runtime", status: "passed", executed: true }
    expect(canPublishPage(draft)).toBe(true)
    draft.validation!.revision_id = "another"
    expect(canPublishPage(draft)).toBe(false)
  })
})
