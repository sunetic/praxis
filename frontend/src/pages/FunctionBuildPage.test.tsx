import { StrictMode } from "react"
import { act, fireEvent, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { renderWithShell as render } from "@/test/renderWithShell"
import { agentRunsApi, type RunConversation } from "@/lib/agentRuns"
import { datasourcesApi, functionsApi } from "@/lib/api"
import { canPublishDraft, functionArtifactsApi, type FunctionDraft } from "@/lib/functionArtifacts"
import { FunctionBuildPage } from "./FunctionBuildPage"

const conversation: RunConversation = { id: "conv", title: "Function conversation", scene: { function_ids: [1], datasource_ids: [] }, created_at: 1, active_run_id: null }
const initial: FunctionDraft = {
  function_id: 1, name: "订单统计", slug: "orders", code: "def main(payload, context):\n    return payload\n", dependencies: {},
  revision_id: "revision-1", revision_hash: "a".repeat(64), current_release_id: null, released_revision_id: null, changed_files: ["main.py"], validation: null,
}
let draft: FunctionDraft
function checks(status = "passed"): FunctionDraft["validation"] {
  return { id: "validation-1", revision_id: draft.revision_id!, revision_hash: draft.revision_hash, created_at: 1,
    checks: ["python_syntax", "entrypoint", "controlled_runtime"].map(name => ({ name, status: name === "controlled_runtime" ? status : "passed", executed: name !== "controlled_runtime" || status !== "unavailable" })) }
}
function open(path = "/function/1/build") {
  return render(<StrictMode><MemoryRouter initialEntries={[path]}><Routes><Route path="/function/:functionId/build" element={<FunctionBuildPage />} /></Routes></MemoryRouter></StrictMode>)
}
beforeEach(() => {
  draft = structuredClone(initial)
  vi.spyOn(functionsApi, "get").mockResolvedValue({ id: 1, name: "订单统计", description: "订单汇总", kind: "custom" })
  vi.spyOn(functionsApi, "update").mockResolvedValue({ id: 1, name: "新名称", description: "订单汇总", kind: "custom" })
  vi.spyOn(datasourcesApi, "list").mockResolvedValue([])
  vi.spyOn(functionArtifactsApi, "read").mockImplementation(async () => structuredClone(draft))
  vi.spyOn(functionArtifactsApi, "save").mockResolvedValue({})
  vi.spyOn(functionArtifactsApi, "validate").mockResolvedValue(checks()!)
  vi.spyOn(functionArtifactsApi, "publish").mockResolvedValue({})
  vi.spyOn(agentRunsApi, "conversations").mockResolvedValue([conversation])
  vi.spyOn(agentRunsApi, "createConversation").mockResolvedValue(conversation)
  vi.spyOn(agentRunsApi, "runs").mockResolvedValue([])
  vi.spyOn(agentRunsApi, "stream").mockImplementation(() => new Promise(() => {}))
})
afterEach(() => vi.restoreAllMocks())

describe("native Function workspace", () => {
  it("binds one native conversation, without a generated welcome or build history", async () => {
    vi.mocked(agentRunsApi.conversations).mockResolvedValue([])
    open()
    await screen.findByRole("heading", { name: "订单统计" })
    expect(agentRunsApi.createConversation).toHaveBeenCalledExactlyOnceWith("Function 工作区", { function_ids: [1], datasource_ids: [] })
    expect(screen.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
    expect(screen.getByText("当前版本尚无检查记录")).toBeVisible()
    expect(document.querySelectorAll("[data-message-id]")).toHaveLength(0)
  })

  it("rejects an explicit conversation URL for another Function without creating a replacement", async () => {
    open("/function/1/build?conversationId=another-object")
    expect(await screen.findByRole("alert")).toHaveTextContent("工作区加载失败")
    expect(agentRunsApi.createConversation).not.toHaveBeenCalled()
    expect(agentRunsApi.runs).not.toHaveBeenCalled()
  })

  it("shows native text and refreshes artifact facts without treating finished as publication", async () => {
    const run = { id: "run", conversation_id: "conv", prompt: "修改草稿", seq: 1, status: "finished" as const, cancel_requested: false, output: "已完成", error_code: null, event_seq: 3, created_at: 1 }
    vi.mocked(agentRunsApi.runs).mockResolvedValue([run])
    draft.validation = checks("unavailable")
    vi.mocked(agentRunsApi.stream).mockImplementation(async () => new Response([
      { type: "assistant_delta", message_id: "m", part_id: "0", text: "已完成" },
      { type: "tool_result", name: "function_validate", call_id: "validate", outcome: "success", content: draft.validation },
      { type: "run_finished", status: "finished" },
    ].map((event, i) => `data: ${JSON.stringify({ ...event, run_id: "run", seq: i + 1 })}\n\n`).join("")))
    open()
    expect(await screen.findByText("已完成")).toBeVisible()
    expect(await screen.findByText("环境不可用 · 未执行")).toBeVisible()
    expect(screen.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
    expect(screen.getByText("当前草稿未发布")).toBeVisible()
    await waitFor(() => expect(vi.mocked(functionArtifactsApi.read).mock.calls.length).toBeGreaterThan(1))
  })

  it("saves source with its original revision and drops eligibility after a new revision", async () => {
    draft.validation = checks()
    vi.mocked(functionArtifactsApi.save).mockImplementation(async (_id, data) => {
      draft = { ...draft, code: data.code, revision_id: "revision-2", revision_hash: "b".repeat(64), validation: null }
    })
    open()
    await userEvent.click(await screen.findByRole("button", { name: "编辑源码与依赖" }))
    expect(screen.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
    fireEvent.change(screen.getByRole("textbox", { name: "main.py" }), { target: { value: "def main(payload, context):\n    return 2" } })
    await userEvent.click(screen.getByRole("button", { name: "保存草稿" }))
    await screen.findByText("草稿已保存，旧检查不再适用")
    expect(functionArtifactsApi.save).toHaveBeenCalledWith(1, { expected_revision: initial.revision_hash, code: "def main(payload, context):\n    return 2", dependencies: {} })
    await waitFor(() => expect(screen.getByText("当前版本尚无检查记录")).toBeVisible())
    expect(screen.getByRole("button", { name: "发布当前版本" })).toBeDisabled()
  })

  it("refreshes on a result identified only by call ID before the model finishes", async () => {
    const run = { id: "running", conversation_id: "conv", prompt: "修改", seq: 1, status: "running" as const, cancel_requested: false, output: null, error_code: null, event_seq: 0, created_at: 1 }
    vi.mocked(agentRunsApi.runs).mockResolvedValue([run])
    let controller: ReadableStreamDefaultController<Uint8Array>
    vi.mocked(agentRunsApi.stream).mockImplementation(async () => new Response(new ReadableStream({ start(value) { controller = value } })))
    open(); await screen.findByRole("heading", { name: "订单统计" })
    await waitFor(() => expect(agentRunsApi.stream).toHaveBeenCalled())
    draft = { ...draft, revision_id: "saved-before-finish", validation: null }
    const push = (seq: number, event: Record<string, unknown>) => controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify({ ...event, run_id: run.id, seq })}\n\n`))
    await act(async () => {
      push(1, { type: "tool_start", call_id: "write", name: "function_write" })
      push(2, { type: "tool_result", call_id: "write", status: "succeeded", outcome: "success", content: { revision_id: draft.revision_id } })
    })
    await waitFor(() => expect(screen.getByTestId("artifact-state")).toHaveTextContent("saved-before-finish"))
    expect(screen.getByRole("button", { name: "停止", exact: true })).toBeVisible()
    await act(async () => { push(3, { type: "run_finished", status: "finished" }); controller.close() })
  })

  it("retains local edits if another editor changed the draft", async () => {
    vi.mocked(functionArtifactsApi.save).mockRejectedValue(new Error("Draft changed; read the current revision before editing"))
    open()
    await userEvent.click(await screen.findByRole("button", { name: "编辑源码与依赖" }))
    fireEvent.change(screen.getByRole("textbox", { name: "main.py" }), { target: { value: "local unsaved source" } })
    draft = { ...draft, code: "remote source", revision_id: "remote", revision_hash: "b".repeat(64) }
    await userEvent.click(screen.getByRole("button", { name: "刷新产物" }))
    await waitFor(() => expect(screen.getByTestId("artifact-state")).toHaveTextContent("remote"))
    await userEvent.click(screen.getByRole("button", { name: "保存草稿" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("Draft changed")
    expect(screen.getByRole("textbox", { name: "main.py" })).toHaveValue("local unsaved source")
    expect(functionArtifactsApi.save).toHaveBeenCalledWith(1, expect.objectContaining({ expected_revision: initial.revision_hash }))
  })

  it("sends the checked revision and report ID once on manual publication", async () => {
    draft.validation = checks()
    vi.mocked(functionArtifactsApi.publish).mockImplementation(async () => { draft = { ...draft, current_release_id: 9, released_revision_id: draft.revision_id } })
    open()
    await userEvent.dblClick(await screen.findByRole("button", { name: "发布当前版本" }))
    expect(await screen.findByText("当前草稿已发布")).toBeVisible()
    expect(functionArtifactsApi.publish).toHaveBeenCalledExactlyOnceWith(1, { expected_revision: initial.revision_hash, validation_id: "validation-1" })
  })

  it("keeps the previous release visible after editing and rejects invalid validation JSON", async () => {
    draft.current_release_id = 9; draft.released_revision_id = "older"
    open()
    await screen.findByText("当前草稿未发布")
    expect(screen.getByTestId("artifact-state")).toHaveTextContent("线上版本: #9")
    fireEvent.change(screen.getByRole("textbox", { name: "检查输入（JSON 对象）" }), { target: { value: "[]" } })
    await userEvent.click(screen.getByRole("button", { name: "检查当前草稿" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("请输入有效的 JSON 对象")
    expect(functionArtifactsApi.validate).not.toHaveBeenCalled()
  })

  it("does not silently broaden datasource scope when saving fails", async () => {
    vi.spyOn(agentRunsApi, "updateConversation").mockRejectedValue(new Error("offline"))
    open()
    await screen.findByRole("heading", { name: "订单统计" })
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "数据源范围" }), "authorized")
    expect(await screen.findByRole("alert")).toHaveTextContent("范围未保存")
    expect(screen.getByRole("combobox", { name: "数据源范围" })).toHaveValue("none")
  })

  it("disables stale artifact actions until a successful refresh", async () => {
    open(); await screen.findByRole("heading", { name: "订单统计" })
    vi.mocked(functionArtifactsApi.read).mockRejectedValueOnce(new Error("offline"))
    await userEvent.click(screen.getByRole("button", { name: "刷新产物" }))
    expect(await screen.findByRole("alert")).toHaveTextContent("产物刷新失败")
    expect(screen.getByRole("button", { name: "编辑源码与依赖" })).toBeDisabled()
    await userEvent.click(screen.getByRole("button", { name: "刷新产物" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "编辑源码与依赖" })).toBeEnabled())
  })

  it("updates metadata without submitting a run", async () => {
    open(); await screen.findByRole("heading", { name: "订单统计" })
    await userEvent.click(screen.getByText("名称与说明", { exact: true }))
    fireEvent.change(screen.getByRole("textbox", { name: "名称", exact: true }), { target: { value: "新名称" } })
    await userEvent.click(screen.getByRole("button", { name: "保存名称与说明" }))
    expect(await screen.findByRole("heading", { name: "新名称" })).toBeVisible()
    expect(functionsApi.update).toHaveBeenCalledWith(1, { name: "新名称", description: "订单汇总" })
  })

  it("never grants publication for a stale or incomplete report", () => {
    draft.validation = checks(); expect(canPublishDraft(draft)).toBe(true)
    draft.validation!.revision_id = "old"; expect(canPublishDraft(draft)).toBe(false)
    draft.validation = checks(); draft.validation!.checks.pop(); expect(canPublishDraft(draft)).toBe(false)
    draft.validation = checks("unavailable"); expect(canPublishDraft(draft)).toBe(false)
  })
})
